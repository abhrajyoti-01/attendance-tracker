import threading
import time
from collections import deque

import cv2
import mediapipe as mp
import numpy as np
import structlog
from skimage.feature import local_binary_pattern

from src.config import settings

logger = structlog.get_logger(__name__)

LEFT_EYE_IDXS = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_IDXS = [362, 385, 387, 263, 373, 380]
NOSE_IDX = 1


class LivenessDetector:
    def __init__(self, config: dict | None = None):
        self.config = config or {
            "ear_closed": settings.anti_spoof.ear_closed,
            "ear_open": settings.anti_spoof.ear_open,
            "blink_consec_frames": settings.anti_spoof.blink_consec_frames,
            "blink_required_window": settings.anti_spoof.blink_required_window,
            "nose_movement_px": settings.anti_spoof.nose_movement_px,
            "texture_variance_min": settings.anti_spoof.texture_variance_min,
            "liveness_threshold": settings.anti_spoof.liveness_threshold,
        }

        self.face_mesh = mp.solutions.face_mesh.FaceMesh(
            static_image_mode=False,
            max_num_faces=1,
            refine_landmarks=True,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        self.ear_history: deque[float] = deque(maxlen=90)
        self.nose_history: deque[tuple[int, int]] = deque(maxlen=30)
        self.texture_history: deque[float] = deque(maxlen=10)

        self.frame_counter = 0
        self.last_blink_time = 0.0
        self.blink_detected = False
        self.movement_detected = False
        self.texture_detected = False

        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self.ear_history.clear()
            self.nose_history.clear()
            self.texture_history.clear()
            self.frame_counter = 0
            self.last_blink_time = 0.0
            self.blink_detected = False
            self.movement_detected = False
            self.texture_detected = False

    @staticmethod
    def _as_uint8_rgb(frame: np.ndarray) -> np.ndarray:
        """Coerce a crop to contiguous uint8 RGB, the only format MediaPipe accepts."""
        arr = frame
        if arr.dtype != np.uint8:
            if arr.dtype.kind == "f" and arr.max(initial=0.0) <= 1.5:
                arr = arr * 255.0
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        if not arr.flags["C_CONTIGUOUS"]:
            arr = np.ascontiguousarray(arr)
        return arr

    def check(
        self, face_image: np.ndarray | list[np.ndarray], threshold: float | None = None
    ) -> tuple[bool, str, float]:
        """Assess liveness over one frame or a short burst of frames.

        Blink and head-movement are temporal signals: they are only measurable
        when several frames are supplied, and each is scored independently of
        the others. A single frame can therefore only be judged on its static
        texture, and ``anti_spoof.require_sequence`` can be set to reject
        single-frame requests outright for high-security deployments.

        Frames must be RGB uint8 crops (as produced by ``FaceDetector.detect``).
        ``threshold`` overrides ``anti_spoof.liveness_threshold`` (per-org setting).
        Returns ``(is_live, reason, score)`` where score is the fraction of
        *available* checks that passed.
        """
        frames = [face_image] if isinstance(face_image, np.ndarray) else list(face_image)

        if not frames:
            return False, "no_frames", 0.0
        if settings.anti_spoof.require_sequence and len(frames) < 2:
            return False, "sequence_required", 0.0

        with self._lock:
            try:
                return self._evaluate(frames, threshold)
            except Exception as e:
                # Fail closed, but log loudly: silently swallowing a KeyError or
                # dtype error here means every request looks like a spoof attempt.
                logger.error(
                    "Liveness check error - failing closed",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                return False, "error", 0.0

    def _evaluate(
        self, frames: list[np.ndarray], threshold: float | None = None
    ) -> tuple[bool, str, float]:
        ear_series: list[float] = []
        nose_points: list[tuple[int, int]] = []
        texture_scores: list[float] = []

        for frame in frames:
            rgb = self._as_uint8_rgb(frame)
            results = self.face_mesh.process(rgb)
            if not results.multi_face_landmarks:
                continue

            lm = results.multi_face_landmarks[0]
            h, w = rgb.shape[:2]

            left_ear = self._compute_ear(lm, LEFT_EYE_IDXS, w, h)
            right_ear = self._compute_ear(lm, RIGHT_EYE_IDXS, w, h)
            ear_series.append((left_ear + right_ear) / 2.0)
            nose_points.append((int(lm.landmark[NOSE_IDX].x * w), int(lm.landmark[NOSE_IDX].y * h)))

            gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
            lbp = local_binary_pattern(gray, P=8, R=1, method="uniform")
            texture_scores.append(float(np.var(lbp.astype(np.float32))))

        if not ear_series:
            return False, "no_face", 0.0

        self.ear_history.extend(ear_series)
        self.nose_history.extend(nose_points)
        self.texture_history.extend(texture_scores[-10:])

        reasons: list[str] = []
        passed = 0
        available = 0

        # Texture is measurable from a single frame.
        available += 1
        texture_score = float(np.mean(texture_scores))
        self.texture_detected = texture_score >= self.config["texture_variance_min"]
        if self.texture_detected:
            passed += 1
        else:
            reasons.append("low_texture")

        # Blink: ear must dip below ear_closed and later rise above ear_open.
        if len(ear_series) >= 3:
            available += 1
            closed_at = next(
                (i for i, ear in enumerate(ear_series) if ear < self.config["ear_closed"]), None
            )
            reopened = closed_at is not None and any(
                ear > self.config["ear_open"] for ear in ear_series[closed_at + 1 :]
            )
            self.blink_detected = reopened
            if reopened:
                passed += 1
                self.last_blink_time = time.time()
            else:
                reasons.append("no_blink")

        # Head movement: cumulative nose displacement across the burst.
        if len(nose_points) >= 5:
            available += 1
            movement = sum(
                float(
                    np.hypot(
                        nose_points[i][0] - nose_points[i - 1][0],
                        nose_points[i][1] - nose_points[i - 1][1],
                    )
                )
                for i in range(1, len(nose_points))
            )
            self.movement_detected = movement >= self.config["nose_movement_px"]
            if self.movement_detected:
                passed += 1
            else:
                reasons.append("no_movement")

        liveness_score = passed / float(available)
        effective_threshold = (
            threshold if threshold is not None else self.config["liveness_threshold"]
        )
        is_live = liveness_score >= effective_threshold

        if is_live:
            logger.debug(
                "Liveness check passed",
                score=liveness_score,
                checks_passed=passed,
                frames=len(frames),
            )
        else:
            logger.debug(
                "Liveness check failed",
                score=liveness_score,
                reasons=reasons,
                frames=len(frames),
            )

        detail = ",".join(reasons) if reasons else "live"
        if available == 1:
            detail = f"static_only,{detail}" if reasons else "static_only"
        return is_live, detail, liveness_score

    def _compute_ear(self, landmarks, eye_indices, w: int, h: int) -> float:
        points = []
        for idx in eye_indices:
            points.append(
                [
                    int(landmarks.landmark[idx].x * w),
                    int(landmarks.landmark[idx].y * h),
                ]
            )

        vertical1 = np.linalg.norm(np.array(points[1]) - np.array(points[5]))
        vertical2 = np.linalg.norm(np.array(points[2]) - np.array(points[4]))
        horizontal = np.linalg.norm(np.array(points[0]) - np.array(points[3]))

        ear = (vertical1 + vertical2) / (2.0 * horizontal + 1e-6)
        return float(ear)

    def _compute_movement(self) -> float:
        if len(self.nose_history) < 5:
            return 0.0

        positions = list(self.nose_history)
        total_distance = 0.0

        for i in range(1, len(positions)):
            dx = positions[i][0] - positions[i - 1][0]
            dy = positions[i][1] - positions[i - 1][1]
            total_distance += np.sqrt(dx * dx + dy * dy)

        return total_distance

    def get_status(self) -> dict:
        with self._lock:
            return {
                "blink_detected": (time.time() - self.last_blink_time)
                < self.config["blink_required_window"],
                "movement_detected": self.movement_detected,
                "texture_detected": self.texture_detected,
                "current_ear": float(self.ear_history[-1]) if self.ear_history else 0.0,
                "frames_since_blink": int((time.time() - self.last_blink_time) * 30),
            }

    def check_challenge_response(
        self,
        challenge_type: str,
        face_image: np.ndarray,
        expected_value: any,
    ) -> tuple[bool, str]:
        rgb = cv2.cvtColor(face_image, cv2.COLOR_BGR2RGB)
        results = self.face_mesh.process(rgb)

        if not results.multi_face_landmarks:
            return False, "no_face_detected"

        lm = results.multi_face_landmarks[0]
        h, w = face_image.shape[:2]

        if challenge_type == "blink":
            left_ear = self._compute_ear(lm, LEFT_EYE_IDXS, w, h)
            right_ear = self._compute_ear(lm, RIGHT_EYE_IDXS, w, h)
            avg_ear = (left_ear + right_ear) / 2.0
            blinked = avg_ear < self.config["ear_closed"]
            return blinked, f"ear={avg_ear:.3f}"

        elif challenge_type == "head_left":
            nose_x = lm.landmark[NOSE_IDX].x
            return nose_x < 0.4, f"nose_x={nose_x:.3f}"

        elif challenge_type == "head_right":
            nose_x = lm.landmark[NOSE_IDX].x
            return nose_x > 0.6, f"nose_x={nose_x:.3f}"

        elif challenge_type == "head_up":
            nose_y = lm.landmark[NOSE_IDX].y
            return nose_y < 0.3, f"nose_y={nose_y:.3f}"

        elif challenge_type == "head_down":
            nose_y = lm.landmark[NOSE_IDX].y
            return nose_y > 0.7, f"nose_y={nose_y:.3f}"

        return False, "unknown_challenge"


class LivenessDetectorLazy:
    _instance: LivenessDetector | None = None
    _lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> LivenessDetector:
        with cls._lock:
            if cls._instance is None:
                cls._instance = LivenessDetector()
            return cls._instance

    @classmethod
    def reset_instance(cls):
        with cls._lock:
            cls._instance = None


def check_liveness(face_image: np.ndarray | list[np.ndarray]) -> tuple[bool, str, float]:
    detector = LivenessDetectorLazy.get_instance()
    return detector.check(face_image)


def reset_liveness() -> None:
    detector = LivenessDetectorLazy.get_instance()
    detector.reset()


class TextureAnalyzer:
    def __init__(self):
        self.min_variance = settings.anti_spoof.texture_variance_min

    def analyze(self, image: np.ndarray) -> dict:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        lbp = local_binary_pattern(gray, P=8, R=1, method="uniform")
        lbp_var = np.var(lbp.astype(np.float32))

        gradient_x = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gradient_y = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        gradient_mag = np.sqrt(gradient_x**2 + gradient_y**2)
        gradient_var = np.var(gradient_mag)

        is_textured = lbp_var >= self.min_variance

        return {
            "lbp_variance": float(lbp_var),
            "gradient_variance": float(gradient_var),
            "is_textured": is_textured,
            "score": float(min(1.0, lbp_var / (self.min_variance * 2))),
        }


class BlinkDetector:
    def __init__(self, ear_closed: float = 0.18, ear_open: float = 0.25):
        self.ear_closed = ear_closed
        self.ear_open = ear_open
        self.ear_history: deque[float] = deque(maxlen=5)
        self.blink_count = 0

    def detect(self, left_ear: float, right_ear: float) -> bool:
        avg_ear = (left_ear + right_ear) / 2.0
        self.ear_history.append(avg_ear)

        if len(self.ear_history) < 3:
            return False

        if avg_ear < self.ear_closed and self.ear_history[-2] < self.ear_closed:
            if self.ear_history[-3] >= self.ear_open:
                self.blink_count += 1
                return True

        return False

    def reset(self) -> None:
        self.ear_history.clear()
        self.blink_count = 0

    def get_blink_count(self) -> int:
        return self.blink_count
