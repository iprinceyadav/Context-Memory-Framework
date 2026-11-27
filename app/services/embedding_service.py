"""
Embedding Service for the Context Memory Framework.

Provides vector embeddings for semantic search and memory retrieval.
Uses sentence-transformers with a provider abstraction so the underlying
model can be swapped without changing call-sites.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import List

import numpy as np
from app.core.config import settings

logger = logging.getLogger(__name__)


class EmbeddingProvider(ABC):
    """Abstract base for any embedding backend."""

    @abstractmethod
    def encode(self, texts: List[str]) -> List[List[float]]:
        """Return one embedding vector per input text."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Dimensionality of the vectors this provider produces."""


class SentenceTransformerProvider(EmbeddingProvider):
    """Provider backed by the *sentence-transformers* library."""

    _DIMENSION = 384  # all-MiniLM-L6-v2 output size

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or settings.EMBEDDING_MODEL or "all-MiniLM-L6-v2"
        self._model = None  # lazy – loaded on first call
        self._load_failed = False

    def _load_model(self) -> None:
        """Load the model on first use, logging any errors gracefully."""
        if self._model is not None or self._load_failed:
            return
        try:
            from sentence_transformers import SentenceTransformer

            logger.info("Loading sentence-transformer model '%s'...", self._model_name)
            self._model = SentenceTransformer(self._model_name)
            logger.info("Model '%s' loaded successfully.", self._model_name)
        except Exception:
            self._load_failed = True
            logger.warning(
                "Failed to load sentence-transformer model '%s'. "
                "Embeddings will be empty; keyword/hybrid fallback remains active.",
                self._model_name,
                exc_info=True,
            )

    @property
    def dimension(self) -> int:
        return self._DIMENSION

    def encode(self, texts: List[str]) -> List[List[float]]:
        """Encode *texts* into normalized embedding vectors."""
        if not texts:
            return []

        self._load_model()

        if self._model is None:
            return [[] for _ in texts]

        try:
            vectors = self._model.encode(
                texts,
                show_progress_bar=False,
                convert_to_numpy=True,
                normalize_embeddings=True,
            )
            return [vec.tolist() for vec in vectors]
        except Exception:
            logger.warning(
                "Error generating embeddings – returning empty vectors.",
                exc_info=True,
            )
            return [[] for _ in texts]


class EmbeddingService:
    """High-level embedding service used throughout the application."""

    def __init__(self, provider: EmbeddingProvider | None = None) -> None:
        self._provider: EmbeddingProvider = provider or SentenceTransformerProvider()

    def set_provider(self, provider: EmbeddingProvider) -> None:
        """Replace the current embedding provider at runtime."""
        self._provider = provider
        logger.info("Embedding provider switched to %s", type(provider).__name__)

    @property
    def dimension(self) -> int:
        return self._provider.dimension

    def generate_embedding(self, text: str) -> List[float]:
        """Generate an embedding vector for a single piece of text."""
        if not text or not text.strip():
            return []

        results = self._provider.encode([text])
        return results[0] if results else []

    def generate_embeddings(self, texts: List[str]) -> List[List[float]]:
        """Generate embedding vectors for a batch of texts."""
        if not texts:
            return []

        non_empty_indices: List[int] = []
        non_empty_texts: List[str] = []
        for idx, t in enumerate(texts):
            if t and t.strip():
                non_empty_indices.append(idx)
                non_empty_texts.append(t)

        encoded = self._provider.encode(non_empty_texts) if non_empty_texts else []

        result: List[List[float]] = [[] for _ in texts]
        for pos, idx in enumerate(non_empty_indices):
            result[idx] = encoded[pos] if pos < len(encoded) else []

        return result

    @staticmethod
    def cosine_similarity(vec1: List[float], vec2: List[float]) -> float:
        """Compute cosine similarity between two vectors."""
        if not vec1 or not vec2:
            return 0.0

        a = np.asarray(vec1, dtype=np.float64)
        b = np.asarray(vec2, dtype=np.float64)

        if a.shape != b.shape:
            return 0.0

        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)

        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0

        return float(np.dot(a, b) / (norm_a * norm_b))


embedding_service: EmbeddingService = EmbeddingService()
