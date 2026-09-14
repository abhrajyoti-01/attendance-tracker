import threading
from pathlib import Path

import cv2
import numpy as np
import torch
from facenet_pytorch import MTCNN
from PIL import Image

from src.config import settings


class FaceDetector:
    def __init__(
        self,
        image_size: int = 160,
        margin: int = 0,
        min_face_size: int = 20,
        thresholds: list[float] | None = None,
        factor: float = 0.709,
        keep_all: bool = False,
        device: str = "cpu",
    ):
        self.image_size = image_size
        self.margin = margin
        self.device = device

        if thresholds is None:
            thresholds = [0.6, 0.7, 0.7]

        self.mtcnn = MTCNN(
            image_size=image_size,
            margin=margin,
            min_face_size=min_face_size,
            thresholds=thresholds,
            factor=factor,
            keep_all=keep_all,
            post_process=False,
            device=device,
        )

        self._lock = threading.Lock()

    @staticmethod
    def _to_uint8_hwc(face: "torch.Tensor") -> np.ndarray:
        """MTCNN CHW tensor (float [0..255] with post_process=False) -> uint8 HWC RGB."""
        arr = face.detach().to("cpu").permute(1, 2, 0).clamp(0, 255).round().numpy()
        return np.ascontiguousarray(arr, dtype=np.uint8)

    def detect(self, image: np.ndarray) -> np.ndarray | None:
        """Detect the largest face and return a 160x160 RGB **uint8** crop.

        Pixels are in [0, 255]. Every downstream consumer (liveness, quality
        checks, exports) and the EmbeddingEngine depend on this contract; the
        engine applies ``(x/255 - 0.5) / 0.5`` exactly once.
        """
        with self._lock:
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            image_pil = Image.fromarray(image_rgb)

            face = self.mtcnn(image_pil, return_prob=False)

            if face is not None:
                return self._to_uint8_hwc(face)
            return None

    def detect_multiple(
        self, image: np.ndarray
    ) -> tuple[list[np.ndarray], list[float], list[tuple[int, int, int, int]]]:
        """Detect all faces, returning RGB uint8 crops in [0, 255]."""
        with self._lock:
            image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            image_pil = Image.fromarray(image_rgb)

            faces, probs, boxes = self.mtcnn.detect(image_pil, landmarks=True)

            if faces is None or boxes is None:
                return [], [], []

            crops: list[np.ndarray] = []
            for box in boxes:
                try:
                    crop = self.mtcnn.extract(image_pil, [box], save_path=None)
                except Exception:
                    continue
                if crop is not None and len(crop) > 0:
                    crops.append(self._to_uint8_hwc(crop[0]))

            probs_list = [float(p) for p in probs] if probs is not None else [1.0] * len(crops)
            boxes_list = [tuple(map(int, b)) for b in boxes]
            return crops, probs_list, boxes_list

    def align_and_crop(self, image: np.ndarray, landmarks: np.ndarray) -> np.ndarray:
        left_eye = landmarks[0]
        right_eye = landmarks[1]

        dy = right_eye[1] - left_eye[1]
        dx = right_eye[0] - left_eye[0]
        angle = np.degrees(np.arctan2(dy, dx)) - 180

        center = (int((left_eye[0] + right_eye[0]) / 2), int((left_eye[1] + right_eye[1]) / 2))
        rotation_matrix = cv2.getRotationMatrix2D(center, angle, 1.0)

        height, width = image.shape[:2]
        rotated = cv2.warpAffine(image, rotation_matrix, (width, height), flags=cv2.INTER_CUBIC)

        face_size = self.image_size
        x1 = max(0, center[0] - face_size // 2)
        y1 = max(0, center[1] - face_size // 2)
        x2 = min(width, center[0] + face_size // 2)
        y2 = min(height, center[1] + face_size // 2)

        cropped = rotated[y1:y2, x1:x2]

        if cropped.shape[0] != face_size or cropped.shape[1] != face_size:
            cropped = cv2.resize(cropped, (face_size, face_size))

        return cropped

    def preprocess(self, image: np.ndarray) -> np.ndarray:
        image_rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
        image_resized = cv2.resize(image_rgb, (self.image_size, self.image_size))
        image_float = image_resized.astype(np.float32) / 255.0
        image_normalized = (image_float - 0.5) / 0.5
        return image_normalized.transpose(2, 0, 1)

    def extract_face_from_file(self, image_path: str | Path) -> np.ndarray | None:
        image = cv2.imread(str(image_path))
        if image is None:
            return None
        return self.detect(image)

    def extract_faces_from_directory(
        self, directory: str | Path, extensions: tuple[str, ...] = (".jpg", ".jpeg", ".png")
    ) -> dict[str, list[np.ndarray]]:
        directory = Path(directory)
        faces_dict = {}

        for ext in extensions:
            for image_path in directory.glob(f"*{ext}"):
                faces, probs, _ = self.detect_multiple(cv2.imread(str(image_path)))
                if faces:
                    image_name = image_path.stem
                    if image_name not in faces_dict:
                        faces_dict[image_name] = []
                    for face in faces:
                        faces_dict[image_name].append(face)

        return faces_dict

    def save_detected_face(self, image: np.ndarray, output_path: str | Path) -> bool:
        face = self.detect(image)
        if face is None:
            return False

        cv2.imwrite(str(output_path), cv2.cvtColor(face, cv2.COLOR_RGB2BGR))
        return True

    def is_face_present(self, image: np.ndarray, min_prob: float = 0.9) -> tuple[bool, float]:
        faces, probs, _ = self.detect_multiple(image)
        if not faces:
            return False, 0.0

        max_prob = max(probs) if probs else 0.0
        return max_prob >= min_prob, max_prob


class FaceDetectorLazy:
    _instance: FaceDetector | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> FaceDetector:
        with cls._lock:
            if cls._instance is None:
                cls._instance = FaceDetector(
                    image_size=settings.model.face_image_size,
                    device=settings.model.device,
                )
            return cls._instance


def detect_face(image: np.ndarray) -> np.ndarray | None:
    detector = FaceDetectorLazy.get_instance()
    return detector.detect(image)


def preprocess_for_inference(image: np.ndarray) -> np.ndarray:
    detector = FaceDetectorLazy.get_instance()
    return detector.preprocess(image)
