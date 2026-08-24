import threading
import time
from typing import Any

import numpy as np
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)


class Matcher:
    def __init__(self, use_faiss: bool = False):
        self.use_faiss = use_faiss
        self.embeddings: np.ndarray | None = None
        self.user_ids: list[str] = []
        self.user_data: dict[str, Any] = {}
        self._faiss_index = None

    def build(
        self,
        embeddings: np.ndarray,
        user_ids: list[str],
        user_data: dict[str, Any] | None = None,
    ) -> None:
        if len(embeddings) != len(user_ids):
            raise ValueError("Number of embeddings must match number of user IDs")

        self.embeddings = embeddings.astype(np.float32)
        self.user_ids = user_ids
        self.user_data = user_data or {}

        if self.use_faiss:
            self._build_faiss_index()
        else:
            logger.info("Built NumPy matcher", n_users=len(user_ids))

    def _build_faiss_index(self) -> None:
        try:
            import faiss

            n = len(self.embeddings)
            dimension = self.embeddings.shape[1]

            if n < 1000:
                self._faiss_index = faiss.IndexFlatIP(dimension)
                logger.info("Built FAISS Flat IP index", n_users=n)
            elif n < 100000:
                quantizer = faiss.IndexFlatIP(dimension)
                self._faiss_index = faiss.IndexIVFPQ(
                    quantizer,
                    dimension,
                    min(int(np.sqrt(n)), 256),
                    32,
                    8,
                )
                self._faiss_index.train(self.embeddings)
                logger.info("Built FAISS IVF-PQ index", n_users=n)
            else:
                self._faiss_index = faiss.IndexHNSWFlat(dimension, 32)
                logger.info("Built FAISS HNSW index", n_users=n)

            self._faiss_index.add(self.embeddings)
        except ImportError:
            logger.warning("FAISS not available, falling back to NumPy")
            self.use_faiss = False

    def match(
        self,
        query_embedding: np.ndarray,
        threshold: float = 0.55,
        top_k: int = 1,
    ) -> tuple[str, float] | None:
        if self.embeddings is None or len(self.embeddings) == 0:
            return None

        if self.use_faiss and self._faiss_index is not None:
            return self._match_faiss(query_embedding, threshold, top_k)
        else:
            return self._match_numpy(query_embedding, threshold, top_k)

    def _match_numpy(
        self,
        query_embedding: np.ndarray,
        threshold: float,
        top_k: int,
    ) -> tuple[str, float] | None:
        start_time = time.time()

        if query_embedding.ndim == 1:
            query_embedding = query_embedding[np.newaxis, :]

        similarities = np.dot(self.embeddings, query_embedding.T).flatten()

        top_k = min(top_k, len(similarities))
        top_indices = np.argsort(similarities)[-top_k:][::-1]

        for idx in top_indices:
            score = float(similarities[idx])
            if score >= threshold:
                match_time = (time.time() - start_time) * 1000
                logger.debug(
                    "Match found",
                    user_id=self.user_ids[idx],
                    score=score,
                    match_time_ms=match_time,
                )
                return self.user_ids[idx], score

        match_time = (time.time() - start_time) * 1000
        logger.debug("No match found", match_time_ms=match_time)
        return None

    def _match_faiss(
        self,
        query_embedding: np.ndarray,
        threshold: float,
        top_k: int,
    ) -> tuple[str, float] | None:
        start_time = time.time()

        if query_embedding.ndim == 1:
            query_embedding = query_embedding[np.newaxis, :]

        top_k = min(top_k, len(self.user_ids))
        distances, indices = self._faiss_index.search(query_embedding, top_k)

        for dist, idx in zip(distances[0], indices[0], strict=False):
            score = float(dist)
            if idx >= 0 and score >= threshold:
                match_time = (time.time() - start_time) * 1000
                logger.debug(
                    "Match found (FAISS)",
                    user_id=self.user_ids[idx],
                    score=score,
                    match_time_ms=match_time,
                )
                return self.user_ids[idx], score

        match_time = (time.time() - start_time) * 1000
        logger.debug("No match found (FAISS)", match_time_ms=match_time)
        return None

    def match_batch(
        self,
        query_embeddings: np.ndarray,
        threshold: float = 0.55,
        top_k: int = 1,
    ) -> list[tuple[str, float] | None]:
        results = []
        for emb in query_embeddings:
            results.append(self.match(emb, threshold, top_k))
        return results

    def get_all_scores(
        self,
        query_embedding: np.ndarray,
    ) -> list[tuple[str, float]]:
        if self.embeddings is None:
            return []

        if query_embedding.ndim == 1:
            query_embedding = query_embedding[np.newaxis, :]

        similarities = np.dot(self.embeddings, query_embedding.T).flatten()
        return [(self.user_ids[i], float(similarities[i])) for i in range(len(self.user_ids))]

    def find_similar_users(
        self,
        query_embedding: np.ndarray,
        n: int = 5,
    ) -> list[tuple[str, float]]:
        all_scores = self.get_all_scores(query_embedding)
        return sorted(all_scores, key=lambda x: x[1], reverse=True)[:n]

    def update_embedding(
        self,
        user_id: str,
        embedding: np.ndarray,
        user_data: dict[str, Any] | None = None,
    ) -> None:
        if user_id in self.user_ids:
            idx = self.user_ids.index(user_id)
            self.embeddings[idx] = embedding.astype(np.float32)
            if user_data is not None:
                self.user_data[user_id] = user_data
            self._rebuild_index_if_needed()
        else:
            self.add_user(user_id, embedding, user_data)

    def add_user(
        self,
        user_id: str,
        embedding: np.ndarray,
        user_data: dict[str, Any] | None = None,
    ) -> None:
        if self.embeddings is None:
            self.embeddings = embedding.astype(np.float32)[np.newaxis, :]
        else:
            self.embeddings = np.vstack([self.embeddings, embedding.astype(np.float32)])

        self.user_ids.append(user_id)
        if user_data is not None:
            self.user_data[user_id] = user_data

        if self.use_faiss and self._faiss_index is not None:
            self._faiss_index.add(embedding.astype(np.float32)[np.newaxis, :])

    def remove_user(self, user_id: str) -> bool:
        if user_id not in self.user_ids:
            return False

        idx = self.user_ids.index(user_id)
        self.embeddings = np.delete(self.embeddings, idx, axis=0)
        self.user_ids.pop(idx)
        if user_id in self.user_data:
            del self.user_data[user_id]

        self._rebuild_index_if_needed()
        return True

    def _rebuild_index_if_needed(self) -> None:
        if self.use_faiss and len(self.embeddings) > 0:
            self._build_faiss_index()

    def get_user_count(self) -> int:
        return len(self.user_ids)

    def get_user_ids(self) -> list[str]:
        return self.user_ids.copy()

    def get_user_data(self, user_id: str) -> Any | None:
        return self.user_data.get(user_id)

    def clear(self) -> None:
        self.embeddings = None
        self.user_ids = []
        self.user_data = {}
        if self._faiss_index is not None:
            self._faiss_index.reset()
        logger.info("Matcher cleared")


class MatcherLazy:
    _instance: Matcher | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> Matcher:
        with cls._lock:
            if cls._instance is None:
                cls._instance = Matcher(use_faiss=settings.features.enable_faiss_matching)
            return cls._instance

    @classmethod
    def reset_instance(cls):
        with cls._lock:
            cls._instance = None


def build_matcher(
    embeddings: np.ndarray,
    user_ids: list[str],
    use_faiss: bool | None = None,
) -> Matcher:
    matcher = Matcher(
        use_faiss=use_faiss if use_faiss is not None else settings.features.enable_faiss_matching
    )
    matcher.build(embeddings, user_ids)
    return matcher
