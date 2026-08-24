import threading
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)


class EmbeddingEngine:
    """ONNX face embedding inference.

    Expects pre-cropped 160x160 RGB faces (as produced by FaceDetector.detect).
    Input pixels may be uint8 [0..255] or float [0..1]; standardization
    ((x/255 or x) - mean) / std is applied here exactly once.
    """

    def __init__(
        self,
        onnx_path: str | None = None,
        device: str | None = None,
        embedding_dim: int | None = None,
    ):
        self.onnx_path = str(onnx_path or settings.model.path)
        self.device = device or settings.model.device
        self._dim_hint = embedding_dim or settings.model.embedding_dim

        self._lock = threading.Lock()
        self._load_model()

    def _resolve_providers(self) -> list[str]:
        available = ort.get_available_providers()
        if self.device == "cuda" and "CUDAExecutionProvider" in available:
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    def _load_model(self) -> None:
        if not Path(self.onnx_path).exists():
            raise FileNotFoundError(
                f"ONNX model not found at {self.onnx_path}. "
                "Run scripts/export_pretrained_onnx.py to generate it."
            )

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL

        self.session = ort.InferenceSession(
            self.onnx_path,
            sess_options=options,
            providers=self._resolve_providers(),
        )
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.input_shape = self.session.get_inputs()[0].shape

        inferred_dim = self.session.get_outputs()[0].shape[-1]
        if isinstance(inferred_dim, int):
            self.embedding_dim = inferred_dim
            if self.embedding_dim != self._dim_hint:
                logger.warning(
                    "Configured EMBEDDING_DIM differs from ONNX output",
                    configured=self._dim_hint,
                    actual=self.embedding_dim,
                )
        else:
            self.embedding_dim = self._dim_hint

        logger.info(
            "Embedding engine ready",
            path=self.onnx_path,
            device=self.device,
            dim=self.embedding_dim,
        )

    def _preprocess(self, batch_hwcr: np.ndarray) -> np.ndarray:
        """batch of HWC RGB faces -> standardized NCHW float32."""
        x = batch_hwcr.astype(np.float32, copy=True)
        if x.max(initial=0.0) > 1.5:  # uint8-style input
            x /= 255.0
        x = (x - 0.5) / 0.5
        return x.transpose(0, 3, 1, 2).astype(np.float32)

    def _run(self, standardized_nchw: np.ndarray) -> np.ndarray:
        outputs = self.session.run([self.output_name], {self.input_name: standardized_nchw})[0]
        norms = np.linalg.norm(outputs, axis=1, keepdims=True)
        return (outputs / (norms + 1e-10)).astype(np.float32)

    def compute(self, faces: np.ndarray) -> np.ndarray:
        """Compute embeddings for a single face (HWC) or a batch (NHWC), RGB."""
        if faces.ndim == 3:
            faces = faces[np.newaxis, ...]

        target = settings.model.face_image_size
        resized = []
        for face in faces:
            if face.shape[0] != target or face.shape[1] != target:
                face = cv2.resize(face, (target, target))
            resized.append(face)
        batch = np.stack(resized)

        with self._lock:
            result = self._run(self._preprocess(batch))

        if result.shape[0] == 1:
            return result[0]
        return result

    def compute_single(self, face: np.ndarray) -> np.ndarray:
        """Compute one L2-normalized embedding from an HWC RGB face crop."""
        embedding = self.compute(face)
        return embedding if embedding.ndim > 1 else embedding

    def compute_batch_from_images(
        self, images: list[np.ndarray], face_detector=None
    ) -> tuple[np.ndarray, int]:
        """Detect + embed a list of full frames; returns (embeddings, valid_count)."""
        faces = []
        for img in images:
            if face_detector is not None:
                face = face_detector.detect(img)
            else:
                face = img if img.shape[0] == settings.model.face_image_size else None
            if face is not None:
                faces.append(face)
        if not faces:
            return np.empty((0, self.embedding_dim), dtype=np.float32), 0
        return self.compute(np.stack(faces)), len(faces)

    def get_model_info(self) -> dict:
        return {
            "path": self.onnx_path,
            "device": self.device,
            "embedding_dim": self.embedding_dim,
            "input_name": self.input_name,
            "output_name": self.output_name,
            "providers": self.session.get_providers(),
        }


class EmbeddingEngineLazy:
    _instance: EmbeddingEngine | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> EmbeddingEngine:
        with cls._lock:
            if cls._instance is None:
                cls._instance = EmbeddingEngine()
            return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        with cls._lock:
            cls._instance = None
