import base64
import time
from uuid import UUID

import cv2
import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    RequesterContext,
    authenticate_requester,
    get_current_user,
    get_org_admin_user,
)
from src.api.metrics import liveness_checks_total, matches_total, model_inferences_total
from src.api.schemas.recognition import (
    BatchRecognizeRequest,
    BatchRecognizeResponse,
    RecognizeRequest,
    RecognizeResponse,
    RegistrationResponse,
    RegistrationStatusResponse,
    RegistrationUploadRequest,
)
from src.config import settings
from src.database.models import Embedding, SpoofAttempt, User
from src.database.session import get_db
from src.inference.embedding_engine import EmbeddingEngineLazy
from src.inference.index_manager import matcher_registry
from src.preprocessing.face_detector import FaceDetectorLazy
from src.preprocessing.quality_checker import QualityChecker
from src.services.attendance_service import mark_attendance
from src.services.audit import record_audit
from src.services.org_settings import load_org_settings
from src.utils.logger import get_logger

router = APIRouter()
logger = get_logger(__name__)

MAX_IMAGE_PIXELS = 4096 * 4096


def decode_base64_image(base64_str: str) -> np.ndarray:
    max_b64_len = ((settings.api.max_upload_size + 2) // 3) * 4 + 64
    if len(base64_str) > max_b64_len:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image payload exceeds {settings.api.max_upload_size} bytes",
        )

    if "," in base64_str:
        base64_str = base64_str.split(",", 1)[1]

    try:
        image_data = base64.b64decode(base64_str, validate=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid base64 image data"
        ) from exc

    if not image_data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Empty image payload")

    if len(image_data) > settings.api.max_upload_size:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image payload exceeds {settings.api.max_upload_size} bytes",
        )

    buffer = np.frombuffer(image_data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported image format"
        )

    if image.shape[0] * image.shape[1] > MAX_IMAGE_PIXELS:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Image too large"
        )

    return cv2.cvtColor(image, cv2.COLOR_BGR2RGB)


def _run_liveness(
    detector, frames: list[np.ndarray], threshold: float | None = None
) -> tuple[bool, str, float]:
    detector.reset()
    return detector.check(frames, threshold=threshold)


def _no_match_response(
    start_time: float, *, liveness_details: str, is_live: bool = True, liveness_score: float = 1.0
) -> RecognizeResponse:
    return RecognizeResponse(
        matched=False,
        user_id=None,
        name=None,
        external_id=None,
        confidence=0.0,
        is_live=is_live,
        liveness_score=liveness_score,
        liveness_details=liveness_details,
        processing_time_ms=round((time.perf_counter() - start_time) * 1000, 2),
    )


async def _record_spoof_attempt(
    db: AsyncSession, organization_id: UUID, reason: str, score: float
) -> None:
    try:
        db.add(
            SpoofAttempt(
                organization_id=organization_id,
                reason=reason[:50],
                liveness_score=score,
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Failed to persist spoof attempt")


@router.post("", response_model=RecognizeResponse)
async def recognize(
    request: RecognizeRequest,
    requester: RequesterContext = Depends(authenticate_requester),
    db: AsyncSession = Depends(get_db),
):
    start_time = time.perf_counter()
    org_id = str(requester.organization_id)

    image = decode_base64_image(request.image_base64)

    org_settings = await load_org_settings(db, requester.organization_id)
    check_liveness = request.check_liveness and bool(org_settings["require_liveness_check"])

    face_detector = FaceDetectorLazy.get_instance()
    detected_face = face_detector.detect(image)
    if detected_face is None:
        model_inferences_total.labels(status="no_face").inc()
        return _no_match_response(start_time, liveness_details="no_face_detected")

    is_live = True
    liveness_score = 1.0
    liveness_details = "live"

    if check_liveness:
        from src.anti_spoofing.liveness import LivenessDetectorLazy

        liveness_frames = [detected_face]
        for extra in request.liveness_frames_base64:
            try:
                extra_image = decode_base64_image(extra)
            except HTTPException:
                continue
            extra_face = face_detector.detect(extra_image)
            if extra_face is not None:
                liveness_frames.append(extra_face)

        detector = LivenessDetectorLazy.get_instance()
        is_live, liveness_details, liveness_score = _run_liveness(
            detector, liveness_frames, float(org_settings["liveness_threshold"])
        )
        liveness_checks_total.labels(result="pass" if is_live else "fail").inc()

        if not is_live:
            model_inferences_total.labels(status="spoof_blocked").inc()
            matches_total.labels(result="spoof").inc()
            await _record_spoof_attempt(
                db, requester.organization_id, liveness_details, liveness_score
            )
            return _no_match_response(
                start_time,
                is_live=False,
                liveness_score=liveness_score,
                liveness_details=liveness_details or "liveness_failed",
            )

    embedding_engine = EmbeddingEngineLazy.get_instance()
    embedding = embedding_engine.compute_single(detected_face)
    model_inferences_total.labels(status="ok").inc()

    requested_threshold = (
        request.threshold
        if request.threshold is not None
        else float(org_settings["recognition_threshold"])
    )
    threshold = max(requested_threshold, settings.model.intra_org_threshold_floor)

    matcher = await matcher_registry.get_matcher(org_id)
    result = matcher.match(embedding, threshold=threshold) if matcher else None

    if result is not None:
        matched_user_id, confidence = result
        matched_uuid: UUID | None
        try:
            matched_uuid = UUID(matched_user_id)
        except (ValueError, TypeError):
            matched_uuid = None

        user = None
        if matched_uuid is not None:
            user_result = await db.execute(
                select(User).where((User.id == matched_uuid) & (User.is_active.is_(True)))
            )
            user = user_result.scalar_one_or_none()

        if (
            matched_uuid is not None
            and user is not None
            and user.organization_id == requester.organization_id
        ):
            matches_total.labels(result="match").inc()

            if request.auto_mark_attendance:
                await mark_attendance(
                    db,
                    organization_id=requester.organization_id,
                    user_id=user.id,
                    method="auto",
                    confidence=confidence,
                )

            return RecognizeResponse(
                matched=True,
                user_id=str(user.id),
                name=user.name,
                external_id=user.external_id,
                confidence=round(float(confidence), 4),
                is_live=is_live,
                liveness_score=liveness_score,
                liveness_details=liveness_details,
                processing_time_ms=round((time.perf_counter() - start_time) * 1000, 2),
            )

    matches_total.labels(result="no_match").inc()
    return _no_match_response(
        start_time, liveness_score=liveness_score, liveness_details=liveness_details
    )


@router.post("/batch", response_model=BatchRecognizeResponse)
async def batch_recognize(
    request: BatchRecognizeRequest,
    requester: RequesterContext = Depends(authenticate_requester),
    db: AsyncSession = Depends(get_db),
):
    org_id = str(requester.organization_id)

    face_detector = FaceDetectorLazy.get_instance()
    embedding_engine = EmbeddingEngineLazy.get_instance()
    matcher = await matcher_registry.get_matcher(org_id)

    org_settings = await load_org_settings(db, requester.organization_id)
    check_liveness = request.check_liveness and bool(org_settings["require_liveness_check"])

    requested_threshold = (
        request.threshold
        if request.threshold is not None
        else float(org_settings["recognition_threshold"])
    )
    threshold = max(requested_threshold, settings.model.intra_org_threshold_floor)

    results: list[RecognizeResponse] = []
    matched_count = 0

    for img_base64 in request.images_base64:
        item_start = time.perf_counter()
        try:
            image = decode_base64_image(img_base64)
        except HTTPException:
            results.append(_no_match_response(item_start, liveness_details="invalid_image"))
            continue

        detected_face = face_detector.detect(image)
        if detected_face is None:
            results.append(_no_match_response(item_start, liveness_details="no_face_detected"))
            continue

        if check_liveness:
            from src.anti_spoofing.liveness import LivenessDetectorLazy

            detector = LivenessDetectorLazy.get_instance()
            is_live, liveness_details, liveness_score = _run_liveness(
                detector, [detected_face], float(org_settings["liveness_threshold"])
            )
            liveness_checks_total.labels(result="pass" if is_live else "fail").inc()
            if not is_live:
                await _record_spoof_attempt(
                    db, requester.organization_id, liveness_details, liveness_score
                )
                results.append(
                    _no_match_response(
                        item_start,
                        is_live=False,
                        liveness_score=liveness_score,
                        liveness_details=liveness_details or "liveness_failed",
                    )
                )
                continue
        else:
            is_live, liveness_details, liveness_score = True, "live", 1.0

        embedding = embedding_engine.compute_single(detected_face)
        result = matcher.match(embedding, threshold=threshold) if matcher else None

        if result is not None:
            matched_user_id, confidence = result
            try:
                matched_uuid = UUID(matched_user_id)
            except (ValueError, TypeError):
                matched_uuid = None

            user = None
            if matched_uuid is not None:
                user_result = await db.execute(
                    select(User).where((User.id == matched_uuid) & (User.is_active.is_(True)))
                )
                user = user_result.scalar_one_or_none()

            if (
                matched_uuid is not None
                and user is not None
                and user.organization_id == requester.organization_id
            ):
                matched_count += 1
                results.append(
                    RecognizeResponse(
                        matched=True,
                        user_id=str(user.id),
                        name=user.name,
                        external_id=user.external_id,
                        confidence=round(float(confidence), 4),
                        is_live=is_live,
                        liveness_score=liveness_score,
                        liveness_details=liveness_details,
                        processing_time_ms=round((time.perf_counter() - item_start) * 1000, 2),
                    )
                )
                continue

        results.append(
            _no_match_response(
                item_start, liveness_score=liveness_score, liveness_details="no_match"
            )
        )

    return BatchRecognizeResponse(
        results=results,
        total_images=len(request.images_base64),
        matched=matched_count,
        unmatched=len(request.images_base64) - matched_count,
    )


@router.post("/users/{user_id}/register", response_model=RegistrationResponse)
async def register_user_face(
    user_id: UUID,
    request: RegistrationUploadRequest,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    """Register/replace a user's face template. Admin-only; scoped to own organization."""
    result = await db.execute(
        select(User).where(
            (User.id == user_id) & (User.organization_id == current_user.organization_id)
        )
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    quality_checker = QualityChecker(min_face_size=settings.model.face_image_size // 2)
    face_detector = FaceDetectorLazy.get_instance()
    embedding_engine = EmbeddingEngineLazy.get_instance()

    embeddings: list[np.ndarray] = []
    quality_scores: list[float] = []
    rejected: dict[str, int] = {"no_face": 0, "low_quality": 0}

    for img_base64 in request.images_base64:
        try:
            image = decode_base64_image(img_base64)
        except HTTPException:
            rejected["no_face"] += 1
            continue

        detected_face = face_detector.detect(image)
        if detected_face is None:
            rejected["no_face"] += 1
            continue

        face_uint8 = detected_face
        quality = quality_checker.check(face_uint8)
        if not quality["valid"]:
            rejected["low_quality"] += 1
            continue
        quality_scores.append(float(quality["overall_score"]))
        embeddings.append(embedding_engine.compute_single(detected_face))

    if len(embeddings) < 3:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"At least 3 valid face images required. Accepted {len(embeddings)} "
                f"(no_face={rejected['no_face']}, low_quality={rejected['low_quality']})"
            ),
        )

    avg_quality = sum(quality_scores) / len(quality_scores)
    if avg_quality < request.quality_threshold:
        return RegistrationResponse(
            success=False,
            user_id=str(user.id),
            num_samples=len(embeddings),
            quality_score=round(avg_quality, 4),
            message=(
                f"Average image quality {avg_quality:.2f} below required "
                f"{request.quality_threshold:.2f}"
            ),
        )

    stacked = np.stack(embeddings)
    mean_embedding = stacked.mean(axis=0)
    norm = float(np.linalg.norm(mean_embedding))
    mean_embedding = mean_embedding / (norm + 1e-10)

    vector_bytes = mean_embedding.astype("<f4").tobytes()
    model_version = settings.model.path.rsplit("/", 1)[-1]

    existing = await db.execute(select(Embedding).where(Embedding.user_id == user.id))
    record = existing.scalar_one_or_none()
    if record is not None:
        record.vector = vector_bytes
        record.num_samples = len(embeddings)
        record.quality_score = avg_quality
        record.model_version = model_version
        record.version += 1
    else:
        record = Embedding(
            user_id=user.id,
            vector=vector_bytes,
            num_samples=len(embeddings),
            quality_score=avg_quality,
            model_version=model_version,
            version=1,
        )
        db.add(record)

    await db.commit()

    await matcher_registry.upsert_user(
        str(current_user.organization_id), str(user.id), mean_embedding.astype(np.float32)
    )

    await record_audit(
        db,
        "face.registered",
        organization_id=current_user.organization_id,
        actor_id=current_user.id,
        target_type="user",
        target_id=user.id,
        details={
            "samples": len(embeddings),
            "quality": round(avg_quality, 3),
            "version": record.version,
        },
        commit=False,
    )

    logger.info(
        "Face registered",
        user_id=str(user.id),
        samples=len(embeddings),
        quality=round(avg_quality, 3),
        by=str(current_user.id),
    )

    return RegistrationResponse(
        success=True,
        user_id=str(user.id),
        num_samples=len(embeddings),
        quality_score=round(avg_quality, 4),
        message="Face registration successful",
    )


@router.delete("/users/{user_id}/register", status_code=status.HTTP_204_NO_CONTENT)
async def deregister_user_face(
    user_id: UUID,
    current_user: User = Depends(get_org_admin_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User).where(
            (User.id == user_id) & (User.organization_id == current_user.organization_id)
        )
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    deleted = await db.execute(Embedding.__table__.delete().where(Embedding.user_id == user.id))
    await db.commit()

    await matcher_registry.remove_user(str(current_user.organization_id), str(user.id))

    if deleted.rowcount:
        logger.info("Face deregistered", user_id=str(user.id), by=str(current_user.id))
    return None


@router.get("/users/{user_id}/register/status", response_model=RegistrationStatusResponse)
async def get_registration_status(
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(User).where(User.id == user_id)
    if not current_user.is_superadmin:
        query = query.where(User.organization_id == current_user.organization_id)

    result = await db.execute(query)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    embedding_result = await db.execute(select(Embedding).where(Embedding.user_id == user.id))
    embedding = embedding_result.scalar_one_or_none()

    if embedding is not None:
        return RegistrationStatusResponse(
            is_registered=True,
            num_samples=embedding.num_samples,
            quality_score=embedding.quality_score,
            version=embedding.version,
            registered_at=(embedding.updated_at or embedding.created_at).isoformat(),
        )

    return RegistrationStatusResponse(
        is_registered=False,
        num_samples=0,
        quality_score=None,
        version=0,
        registered_at=None,
    )
