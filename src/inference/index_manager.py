import asyncio
import uuid as uuid_module

import numpy as np
import structlog

from src.api.metrics import matcher_index_size
from src.config import settings
from src.inference.matcher import Matcher

logger = structlog.get_logger(__name__)


def decode_vector(raw: bytes, expected_dim: int) -> np.ndarray | None:
    """Decode a float32 little-endian vector; None when malformed or wrong dimension."""
    if not raw:
        return None
    if len(raw) % 4 != 0:
        return None
    vector = np.frombuffer(raw, dtype="<f4").astype(np.float32)
    if vector.shape[0] != expected_dim:
        return None
    return vector


class MatcherRegistry:
    """Per-organization in-memory matching indexes backed by the embeddings table.

    Indexes are loaded lazily on first recognition request for an organization,
    refreshed eagerly at startup, and kept consistent on registration updates.
    Tenant isolation is structural: an index only ever contains vectors for its
    own organization.
    """

    def __init__(self) -> None:
        self._matchers: dict[str, Matcher] = {}
        self._loading: dict[str, asyncio.Lock] = {}
        self._global_lock = asyncio.Lock()
        self._model_error: str | None = None

    @property
    def model_available(self) -> bool:
        try:
            from src.inference.embedding_engine import EmbeddingEngineLazy

            EmbeddingEngineLazy.get_instance()
            return True
        except Exception as exc:
            self._model_error = str(exc)
            return False

    @property
    def total_indexed_users(self) -> int:
        return sum(m.get_user_count() for m in self._matchers.values())

    def _lock_for(self, org_id: str) -> asyncio.Lock:
        # Dict get-or-create is atomic under the single-threaded event loop.
        if org_id not in self._loading:
            self._loading[org_id] = asyncio.Lock()
        return self._loading[org_id]

    async def initialize(self) -> None:
        try:
            await self.refresh_all()
        except Exception as exc:
            logger.warning("Initial matcher warm-up failed", error=str(exc))

    async def refresh_all(self) -> None:
        from sqlalchemy import select

        from src.database.models import Embedding, Organization, User
        from src.database.session import get_db_context

        async with get_db_context() as db:
            result = await db.execute(
                select(
                    Organization.id,
                    Embedding.user_id,
                    Embedding.vector,
                )
                .join(User, User.id == Embedding.user_id)
                .join(Organization, Organization.id == User.organization_id)
                .where(Organization.is_active.is_(True))
                .where(User.is_active.is_(True))
            )
            rows = result.all()

        grouped: dict[str, list[tuple[str, np.ndarray]]] = {}
        skipped = 0
        for org_id, user_id, raw_vector in rows:
            vector = decode_vector(raw_vector, settings.model.embedding_dim)
            if vector is None:
                skipped += 1
                continue
            grouped.setdefault(str(org_id), []).append((str(user_id), vector))

        if skipped:
            logger.warning(
                "Skipped incompatible embeddings during index build",
                skipped=skipped,
                expected_dim=settings.model.embedding_dim,
            )

        async with self._global_lock:
            new_matchers: dict[str, Matcher] = {}
            for org_id, pairs in grouped.items():
                ids = [p[0] for p in pairs]
                matrix = (
                    np.stack([p[1] for p in pairs])
                    if len(pairs) > 1
                    else pairs[0][1][np.newaxis, :]
                )
                old = self._matchers.get(org_id)
                matcher = Matcher(use_faiss=settings.features.enable_faiss_matching)
                matcher.build(matrix, ids, user_data=dict(old.user_data) if old else {})
                new_matchers[org_id] = matcher
            self._matchers = new_matchers

        for org_id, matcher in new_matchers.items():
            matcher_index_size.labels(organization_id=org_id).set(matcher.get_user_count())

        logger.info(
            "Matcher registry built",
            organizations=len(new_matchers),
            users=self.total_indexed_users,
        )

    async def get_matcher(self, org_id: str) -> Matcher | None:
        lock = self._lock_for(org_id)
        async with lock:
            if org_id in self._matchers:
                return self._matchers[org_id]
            matcher = await self._load_org(org_id)
            if matcher is not None:
                self._matchers[org_id] = matcher
                matcher_index_size.labels(organization_id=org_id).set(matcher.get_user_count())
            return matcher

    async def _load_org(self, org_id: str) -> Matcher | None:
        from sqlalchemy import select

        from src.database.models import Embedding, User
        from src.database.session import get_db_context

        async with get_db_context() as db:
            try:
                org_uuid = uuid_module.UUID(org_id)
            except ValueError:
                return None

            result = await db.execute(
                select(Embedding.user_id, Embedding.vector)
                .join(User, User.id == Embedding.user_id)
                .where(User.organization_id == org_uuid)
                .where(User.is_active.is_(True))
            )
            rows = result.all()

        pairs = []
        for user_id, raw_vector in rows:
            vector = decode_vector(raw_vector, settings.model.embedding_dim)
            if vector is not None:
                pairs.append((str(user_id), vector))

        if not pairs:
            return None

        ids = [p[0] for p in pairs]
        matrix = np.stack([p[1] for p in pairs]) if len(pairs) > 1 else pairs[0][1][np.newaxis, :]
        matcher = Matcher(use_faiss=settings.features.enable_faiss_matching)
        matcher.build(matrix, ids)
        return matcher

    async def upsert_user(self, org_id: str, user_id: str, vector: np.ndarray) -> None:
        matcher = await self.get_matcher(org_id)
        if matcher is None:
            matcher = Matcher(use_faiss=settings.features.enable_faiss_matching)
            async with self._lock_for(org_id):
                self._matchers[org_id] = matcher
        matcher.update_embedding(user_id, vector.astype(np.float32))
        matcher_index_size.labels(organization_id=org_id).set(matcher.get_user_count())

    async def remove_user(self, org_id: str, user_id: str) -> None:
        matcher = self._matchers.get(org_id)
        if matcher is not None:
            matcher.remove_user(user_id)
            matcher_index_size.labels(organization_id=org_id).set(matcher.get_user_count())

    async def invalidate_org(self, org_id: str) -> None:
        async with self._lock_for(org_id):
            self._matchers.pop(org_id, None)

    async def shutdown(self) -> None:
        async with self._global_lock:
            for matcher in self._matchers.values():
                matcher.clear()
            self._matchers.clear()


matcher_registry = MatcherRegistry()
