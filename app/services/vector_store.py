from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

from app.core.config import settings
from app.services.embedder import embedder
from app.services.text import sparse_vector

logger = logging.getLogger(__name__)

MEMORIES = "memories"
SCHEMA_TABLES = "schema_tables"
QUERY_EXAMPLES = "query_examples"
COLLECTIONS = (MEMORIES, SCHEMA_TABLES, QUERY_EXAMPLES)

DENSE = "dense"
SPARSE = "sparse"


@dataclass
class Hit:
    id: str
    score: float
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Document:
    id: str
    text: str
    payload: Dict[str, Any]
    vector: Optional[List[float]] = None


def match(key: str, value: Any):
    from qdrant_client import models

    return models.FieldCondition(key=key, match=models.MatchValue(value=value))


def match_any(key: str, values: Iterable[Any]):
    from qdrant_client import models

    return models.FieldCondition(key=key, match=models.MatchAny(any=list(values)))


def all_of(*conditions, none_of: Iterable[Any] = ()):
    from qdrant_client import models

    return models.Filter(must=[c for c in conditions if c is not None], must_not=list(none_of) or None)


def any_of(*conditions):
    from qdrant_client import models

    return models.Filter(should=[c for c in conditions if c is not None])


class VectorStore:
    def __init__(self) -> None:
        self._client = None
        self._location: Optional[str] = None
        self._lock = threading.RLock()
        self._ready: Dict[str, int] = {}
        self._failed = False

    def configure(self, location: Optional[str] = None) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
            self._client = None
            self._location = location
            self._ready = {}
            self._failed = False

    @property
    def mode(self) -> str:
        if self._failed:
            return "unavailable"
        if self._location == ":memory:":
            return "memory"
        return "server" if settings.VECTOR_STORE_URL and not self._location else "embedded"

    def _connect(self):
        if self._client is not None or self._failed:
            return self._client
        try:
            from qdrant_client import QdrantClient

            if self._location:
                self._client = QdrantClient(location=self._location) if self._location == ":memory:" else QdrantClient(path=self._location)
            elif settings.VECTOR_STORE_URL:
                self._client = QdrantClient(url=settings.VECTOR_STORE_URL, api_key=settings.VECTOR_STORE_API_KEY or None)
            else:
                self._client = QdrantClient(path=settings.VECTOR_STORE_PATH)
        except Exception:
            self._failed = True
            logger.warning("Vector store unavailable; retrieval falls back to database scans.", exc_info=True)
        return self._client

    @property
    def available(self) -> bool:
        with self._lock:
            return self._connect() is not None

    def ensure(self, name: str) -> bool:
        from qdrant_client import models

        client = self._connect()
        if client is None:
            return False
        dimension = embedder.dimension
        if self._ready.get(name) == dimension:
            return True
        try:
            if client.collection_exists(name):
                info = client.get_collection(name)
                vectors = info.config.params.vectors
                current = vectors.get(DENSE).size if isinstance(vectors, dict) and vectors.get(DENSE) else None
                if current != dimension:
                    logger.info("Rebuilding collection %s for embedding size %s", name, dimension)
                    client.delete_collection(name)
            if not client.collection_exists(name):
                client.create_collection(
                    name,
                    vectors_config={DENSE: models.VectorParams(size=dimension, distance=models.Distance.COSINE)},
                    sparse_vectors_config={SPARSE: models.SparseVectorParams(modifier=models.Modifier.IDF)},
                )
            self._ready[name] = dimension
            return True
        except Exception:
            logger.warning("Could not prepare collection %s", name, exc_info=True)
            return False

    def count(self, name: str) -> Optional[int]:
        with self._lock:
            if not self.ensure(name):
                return None
            try:
                return self._client.count(name, exact=True).count
            except Exception:
                return None

    def upsert(self, name: str, documents: List[Document]) -> bool:
        from qdrant_client import models

        if not documents:
            return True
        with self._lock:
            if not self.ensure(name):
                return False
            missing = [doc for doc in documents if doc.vector is None]
            if missing:
                for doc, vector in zip(missing, embedder.embed_many([doc.text for doc in missing])):
                    doc.vector = vector
            points = []
            for doc in documents:
                indices, values = sparse_vector(doc.text)
                vectors: Dict[str, Any] = {SPARSE: models.SparseVector(indices=indices, values=values)}
                if doc.vector and len(doc.vector) == embedder.dimension:
                    vectors[DENSE] = doc.vector
                points.append(models.PointStruct(id=doc.id, vector=vectors, payload={**doc.payload, "text": doc.text}))
            try:
                self._client.upsert(name, points=points)
                return True
            except Exception:
                logger.warning("Vector upsert into %s failed", name, exc_info=True)
                return False

    def delete(self, name: str, ids: List[str]) -> None:
        from qdrant_client import models

        if not ids:
            return
        with self._lock:
            if not self.ensure(name):
                return
            try:
                self._client.delete(name, points_selector=models.PointIdsList(points=list(ids)))
            except Exception:
                logger.warning("Vector delete from %s failed", name, exc_info=True)

    def delete_where(self, name: str, condition) -> None:
        from qdrant_client import models

        with self._lock:
            if not self.ensure(name):
                return
            try:
                self._client.delete(name, points_selector=models.FilterSelector(filter=condition))
            except Exception:
                logger.warning("Vector delete from %s failed", name, exc_info=True)

    def reset(self, name: str) -> None:
        with self._lock:
            client = self._connect()
            if client is None:
                return
            try:
                if client.collection_exists(name):
                    client.delete_collection(name)
            except Exception:
                logger.warning("Could not reset collection %s", name, exc_info=True)
            self._ready.pop(name, None)

    def search(self, name: str, query: str, condition=None, limit: int = 20) -> Optional[List[Hit]]:
        from qdrant_client import models

        if not query or not query.strip():
            return []
        with self._lock:
            if not self.ensure(name):
                return None
            indices, values = sparse_vector(query, query=True)
            dense = embedder.embed(query)
            prefetch = []
            if dense and len(dense) == embedder.dimension:
                prefetch.append(models.Prefetch(query=dense, using=DENSE, filter=condition, limit=limit))
            if indices:
                prefetch.append(
                    models.Prefetch(
                        query=models.SparseVector(indices=indices, values=values),
                        using=SPARSE,
                        filter=condition,
                        limit=limit,
                    )
                )
            if not prefetch:
                return []
            try:
                if self._client.count(name, exact=False).count == 0:
                    return []
                result = self._client.query_points(
                    name,
                    prefetch=prefetch,
                    query=models.FusionQuery(fusion=models.Fusion.RRF),
                    limit=limit,
                    with_payload=True,
                )
            except Exception:
                logger.warning("Vector search in %s failed", name, exc_info=True)
                return None
            return [Hit(id=str(point.id), score=float(point.score), payload=dict(point.payload or {})) for point in result.points]


vector_store = VectorStore()
