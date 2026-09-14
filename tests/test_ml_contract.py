"""Face-crop range contract tests.

These lock in the fixes for the bugs that made the system non-functional:

* ``FaceDetector.detect`` used to return MTCNN's standardized floats (~[-1, 1])
  because facenet-pytorch defaults to ``post_process=True``. That array was fed
  to MediaPipe (uint8-only) and to the quality checker, so liveness raised a
  swallowed TypeError and ~50% of registration pixels clamped to black.
* ``EmbeddingEngine._preprocess`` then standardized a second time, putting the
  network far out of distribution (cosine 0.66 vs the correct 1.00).
* ``_preprocess`` returned a non-contiguous array, which segfaults ONNX Runtime
  on Windows.

Requires the exported ONNX model; skipped when it is absent.
"""

import cv2
import numpy as np
import pytest

from src.inference.embedding_engine import EmbeddingEngine
from src.preprocessing.face_detector import FaceDetector

pytestmark = pytest.mark.ml


@pytest.fixture(scope="module")
def synthetic_face() -> np.ndarray:
    """A deterministic face-shaped RGB uint8 crop in the expected 0..255 range."""
    rng = np.random.default_rng(0)
    img = np.full((160, 160, 3), 140, np.uint8)
    cv2.ellipse(img, (80, 84), (52, 66), 0, 0, 360, (196, 168, 148), -1)
    cv2.circle(img, (60, 68), 9, (40, 40, 45), -1)
    cv2.circle(img, (100, 68), 9, (40, 40, 45), -1)
    cv2.ellipse(img, (80, 116), (24, 8), 0, 0, 180, (96, 70, 70), -1)
    noise = rng.integers(-6, 7, img.shape)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def engine(has_onnx_model) -> EmbeddingEngine:
    if not has_onnx_model:
        pytest.skip("ONNX model not exported; run scripts/export_pretrained_onnx.py")
    return EmbeddingEngine()


def test_preprocess_output_is_contiguous(engine, synthetic_face):
    batch = np.stack([synthetic_face])
    processed = engine._preprocess(batch)
    assert processed.flags["C_CONTIGUOUS"], "ONNX Runtime segfaults on strided input"


def test_preprocess_range_matches_export_contract(engine, synthetic_face):
    """Model expects (x/255 - 0.5)/0.5 applied exactly once -> [-1, 1]."""
    processed = engine._preprocess(np.stack([synthetic_face]))
    assert processed.min() >= -1.0001
    assert processed.max() <= 1.0001
    assert processed.shape == (1, 3, 160, 160)


def test_uint8_and_float_inputs_agree(engine, synthetic_face):
    from_uint8 = engine.compute_single(synthetic_face)
    from_float = engine.compute_single(synthetic_face.astype(np.float32) / 255.0)
    assert np.dot(from_uint8, from_float) > 0.9999


def test_embedding_is_normalized_and_deterministic(engine, synthetic_face):
    first = engine.compute_single(synthetic_face)
    second = engine.compute_single(synthetic_face.copy())
    assert first.shape == (512,)
    assert np.linalg.norm(first) == pytest.approx(1.0, abs=1e-4)
    assert np.allclose(first, second, atol=1e-6)


def test_double_standardization_would_be_detectable(engine, synthetic_face):
    """Guards against reintroducing the second standardization pass."""
    correct = engine.compute_single(synthetic_face)
    x = synthetic_face.astype(np.float32) / 255.0
    x = (x - 0.5) / 0.5
    x = (x - 0.5) / 0.5  # the old bug: standardize an already-standardized crop
    wrong = engine._run(np.ascontiguousarray(x.transpose(2, 0, 1)[None]))
    cosine = float(np.dot(correct, wrong[0]))
    assert cosine < 0.95, "double-standardized embedding is too close to correct"


def test_real_face_crop_is_uint8_in_range(has_onnx_model):
    """MTCNN must yield uint8 RGB, not standardized floats."""
    if not has_onnx_model:
        pytest.skip("model not available")

    from skimage import data

    frame = cv2.resize(
        data.astronaut()[:, :, ::-1].copy(), (1024, 1024), interpolation=cv2.INTER_CUBIC
    )
    detector = FaceDetector(image_size=160, margin=0, device="cpu")
    face = detector.detect(frame)

    if face is None:
        pytest.skip("no face detected in fixture image")

    assert face.dtype == np.uint8
    assert face.shape == (160, 160, 3)
    assert face.max() > 1.5, "values must be 0..255, not standardized"
    assert face.flags["C_CONTIGUOUS"]
    # A real photographic face must not be mostly black.
    assert face.mean() > 40


def test_quality_checker_rewards_sharpness(has_onnx_model, synthetic_face):
    """Laplacian variance is a sharpness floor; the old code used it as a cap.

    The synthetic crop is deliberately low-contrast, so only the blur criterion
    is asserted here; `valid` is covered by the registration tests.
    """
    from src.preprocessing.quality_checker import QualityChecker

    checker = QualityChecker(min_face_size=80)
    sharp = checker.check(synthetic_face)
    blurred = checker.check(cv2.GaussianBlur(synthetic_face, (0, 0), 12))

    assert sharp["blur_score"] > blurred["blur_score"]
    assert sharp["blur"] is True, "a crisp crop must pass the sharpness criterion"
    assert blurred["blur"] is False, "a heavily blurred crop must fail"
    assert blurred["valid"] is False
