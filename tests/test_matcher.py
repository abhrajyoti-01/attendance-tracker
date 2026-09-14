"""Matcher math tests: cosine ranking, thresholding, and index mutation."""

import numpy as np
import pytest

from src.inference.index_manager import decode_vector
from src.inference.matcher import Matcher


def _unit(vec: np.ndarray) -> np.ndarray:
    return (vec / np.linalg.norm(vec)).astype(np.float32)


@pytest.fixture
def populated() -> Matcher:
    rng = np.random.default_rng(11)
    vectors = np.stack([_unit(rng.normal(size=512)) for _ in range(4)])
    matcher = Matcher(use_faiss=False)
    matcher.build(vectors, ["u0", "u1", "u2", "u3"])
    return matcher


def test_exact_match_returns_owner(populated):
    rng = np.random.default_rng(11)
    first = _unit(rng.normal(size=512))
    assert populated.match(first, threshold=0.5)[0] == "u0"


def test_below_threshold_returns_none(populated):
    rng = np.random.default_rng(99)
    unrelated = _unit(rng.normal(size=512))
    assert populated.match(unrelated, threshold=0.99) is None


def test_user_count_tracks_updates_and_removal(populated):
    rng = np.random.default_rng(5)
    assert populated.get_user_count() == 4
    populated.update_embedding("u4", _unit(rng.normal(size=512)))
    assert populated.get_user_count() == 5
    populated.remove_user("u4")
    assert populated.get_user_count() == 4


def test_update_existing_user_does_not_grow_index(populated):
    rng = np.random.default_rng(6)
    populated.update_embedding("u0", _unit(rng.normal(size=512)))
    assert populated.get_user_count() == 4


def test_decode_vector_rejects_wrong_dimension():
    payload = np.ones(128, dtype="<f4").tobytes()
    assert decode_vector(payload, expected_dim=512) is None


def test_decode_vector_rejects_malformed_bytes():
    assert decode_vector(b"\x01\x02\x03", expected_dim=512) is None


def test_decode_vector_roundtrip():
    original = np.arange(512, dtype="<f4")
    decoded = decode_vector(original.tobytes(), expected_dim=512)
    assert decoded is not None
    assert np.allclose(decoded, original)
