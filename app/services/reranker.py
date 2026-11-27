from __future__ import annotations

import logging
import math
from abc import ABC, abstractmethod
from typing import List, Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class RerankProvider(ABC):
    name: str = "reranker"

    @abstractmethod
    def score(self, query: str, documents: List[str]) -> Optional[List[float]]:
        ...


class CrossEncoderProvider(RerankProvider):
    def __init__(self, model_name: Optional[str] = None) -> None:
        self.name = model_name or settings.RERANKER_MODEL
        self._model = None
        self._failed = False

    def _load(self) -> None:
        if self._model is not None or self._failed:
            return
        try:
            from sentence_transformers import CrossEncoder

            logger.info("Loading reranker '%s'...", self.name)
            self._model = CrossEncoder(self.name)
        except Exception:
            self._failed = True
            logger.warning("Reranker '%s' unavailable; using first-stage scores only.", self.name, exc_info=True)

    def score(self, query: str, documents: List[str]) -> Optional[List[float]]:
        self._load()
        if self._model is None:
            return None
        try:
            logits = self._model.predict([(query, doc) for doc in documents], show_progress_bar=False)
        except Exception:
            logger.warning("Reranking failed; using first-stage scores only.", exc_info=True)
            return None
        return [1.0 / (1.0 + math.exp(-float(value))) for value in logits]


class Reranker:
    def __init__(self, provider: Optional[RerankProvider] = None) -> None:
        self._provider = provider

    def set_provider(self, provider: Optional[RerankProvider]) -> None:
        self._provider = provider

    @property
    def name(self) -> Optional[str]:
        if not settings.RERANK_ENABLED:
            return None
        return self._get().name

    def _get(self) -> RerankProvider:
        if self._provider is None:
            self._provider = CrossEncoderProvider()
        return self._provider

    def score(self, query: str, documents: List[str]) -> Optional[List[float]]:
        if not settings.RERANK_ENABLED or not documents or not query.strip():
            return None
        scores = self._get().score(query, documents)
        if scores is None or len(scores) != len(documents):
            return None
        return [max(0.0, min(1.0, float(value))) for value in scores]


reranker = Reranker()
