"""Liveness detector tests.

Regression coverage for two bugs that made every recognition request look like
a spoof: the config dict lacked ``liveness_threshold`` (KeyError swallowed by a
bare ``except``), and the score divided by temporal checks that a single frame
can never satisfy.
"""

import cv2
import numpy as np
import pytest

from src.anti_spoofing.liveness import LivenessDetector

pytestmark = pytest.mark.ml


@pytest.fixture(scope="module")
def detector() -> LivenessDetector:
    return LivenessDetector()


@pytest.fixture(scope="module")
def face_crop() -> np.ndarray:
    rng = np.random.default_rng(1)
    img = np.full((160, 160, 3), 140, np.uint8)
    cv2.ellipse(img, (80, 84), (52, 66), 0, 0, 360, (196, 168, 148), -1)
    cv2.circle(img, (60, 68), 9, (40, 40, 45), -1)
    cv2.circle(img, (100, 68), 9, (40, 40, 45), -1)
    noise = rng.integers(-10, 11, img.shape)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


def test_config_includes_liveness_threshold(detector):
    """Missing key caused a swallowed KeyError on every request."""
    assert "liveness_threshold" in detector.config


def test_uint8_input_does_not_error(detector, face_crop):
    _, reason, _ = detector.check([face_crop])
    assert reason != "error", "uint8 crops must be accepted by MediaPipe"


def test_single_frame_scores_on_available_checks_only(detector, face_crop):
    """A lone frame cannot blink or move; it must not be divided by 3."""
    _, reason, score = detector.check([face_crop])
    assert reason.startswith("static_only"), reason
    assert 0.0 <= score <= 1.0
    assert "no_movement" not in reason


def test_empty_input_fails_closed(detector):
    is_live, reason, score = detector.check([])
    assert is_live is False
    assert reason == "no_frames"
    assert score == 0.0


def test_require_sequence_rejects_single_frame(detector, face_crop, monkeypatch):
    from src.config import settings

    monkeypatch.setattr(settings.anti_spoof, "require_sequence", True)
    is_live, reason, _ = detector.check([face_crop])
    assert is_live is False
    assert reason == "sequence_required"


def test_float_standardized_input_is_coerced(detector, face_crop):
    """Defensive: float [-1,1] input must not raise a casting TypeError."""
    standardized = (face_crop.astype(np.float32) / 255.0 - 0.5) / 0.5
    _, reason, _ = detector.check([standardized])
    assert reason != "error"


def test_output_is_triple(detector, face_crop):
    result = detector.check([face_crop])
    assert len(result) == 3
    is_live, reason, score = result
    assert isinstance(is_live, bool)
    assert isinstance(reason, str)
    assert isinstance(score, float)
