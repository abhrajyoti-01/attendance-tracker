import cv2
import numpy as np


class QualityChecker:
    def __init__(
        self,
        min_face_size: int = 40,
        min_sharpness: float = 12.0,
        min_brightness: int = 50,
        max_brightness: int = 200,
        min_contrast: float = 30.0,
    ):
        self.min_face_size = min_face_size
        # Laplacian variance measures *sharpness*: higher is better. Blurred
        # crops score low, so this is a floor, not a ceiling.
        self.min_sharpness = min_sharpness
        self.min_brightness = min_brightness
        self.max_brightness = max_brightness
        self.min_contrast = min_contrast

    def check(self, image: np.ndarray) -> dict[str, bool | float]:
        results = {
            "valid": True,
            "face_size": True,
            "blur": True,
            "brightness": True,
            "contrast": True,
            "overall_score": 1.0,
        }

        h, w = image.shape[:2]

        if min(h, w) < self.min_face_size:
            results["face_size"] = False
            results["valid"] = False

        sharpness_score = self._check_blur(image)
        if sharpness_score < self.min_sharpness:
            results["blur"] = False
            results["valid"] = False
        results["blur_score"] = sharpness_score
        results["sharpness_score"] = sharpness_score

        brightness_score = self._check_brightness(image)
        if not (self.min_brightness <= brightness_score <= self.max_brightness):
            results["brightness"] = False
            results["valid"] = False
        results["brightness_score"] = brightness_score

        contrast_score = self._check_contrast(image)
        if contrast_score < self.min_contrast:
            results["contrast"] = False
            results["valid"] = False
        results["contrast_score"] = contrast_score

        passed = sum(
            [
                results["face_size"],
                results["blur"],
                results["brightness"],
                results["contrast"],
            ]
        )
        results["overall_score"] = passed / 4.0

        return results

    def _check_blur(self, image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return float(cv2.Laplacian(gray, cv2.CV_64F).var())

    def _check_brightness(self, image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return float(np.mean(gray))

    def _check_contrast(self, image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return float(np.std(gray))

    def _check_sharpness(self, image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
        return float(np.mean(gx * gx + gy * gy))

    def _check_eye_openness(
        self, left_eye: np.ndarray, right_eye: np.ndarray
    ) -> tuple[float, bool]:
        def eye_aspect_ratio(eye_landmarks):
            vertical1 = np.linalg.norm(eye_landmarks[1] - eye_landmarks[5])
            vertical2 = np.linalg.norm(eye_landmarks[2] - eye_landmarks[4])
            horizontal = np.linalg.norm(eye_landmarks[0] - eye_landmarks[3])
            ear = (vertical1 + vertical2) / (2.0 * horizontal + 1e-6)
            return ear

        left_ear = eye_aspect_ratio(left_eye)
        right_ear = eye_aspect_ratio(right_eye)
        avg_ear = (left_ear + right_ear) / 2.0

        is_open = avg_ear > 0.2
        return avg_ear, is_open

    def _check_lighting_uniformity(self, image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        hist = cv2.calcHist([gray], [0], None, [256], [0, 256])
        hist = hist / hist.sum()
        uniformity = -np.sum(hist * np.log(hist + 1e-10))
        return uniformity

    def get_quality_score(self, image: np.ndarray) -> float:
        results = self.check(image)
        return results["overall_score"]

    def is_good_quality(self, image: np.ndarray, min_score: float = 0.75) -> bool:
        return self.get_quality_score(image) >= min_score

    def improve_quality(self, image: np.ndarray) -> np.ndarray:
        improved = image.copy()

        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        improved = cv2.cvtColor(improved, cv2.COLOR_BGR2LAB)
        improved[:, :, 0] = clahe.apply(improved[:, :, 0])
        improved = cv2.cvtColor(improved, cv2.COLOR_LAB2BGR)

        gamma = self._estimate_gamma(improved)
        improved = self._adjust_gamma(improved, gamma)

        return improved

    def _estimate_gamma(self, image: np.ndarray) -> float:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        mean = gray.mean()
        gamma = np.log(mean / 255.0) / np.log(0.5)
        return max(0.1, min(gamma, 3.0))

    def _adjust_gamma(self, image: np.ndarray, gamma: float) -> np.ndarray:
        inv_gamma = 1.0 / gamma
        table = np.array([((i / 255.0) ** inv_gamma) * 255 for i in np.arange(0, 256)]).astype(
            np.uint8
        )
        return cv2.LUT(image, table)


class FastQualityChecker:
    def __init__(self, min_score: float = 0.6):
        self.min_score = min_score

    def quick_check(self, image: np.ndarray) -> bool:
        if len(image.shape) < 2:
            return False

        h, w = image.shape[:2]
        if h < 50 or w < 50:
            return False

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        blur = cv2.Laplacian(gray, cv2.CV_64F).var()

        if blur < 20.0:
            return False

        brightness = gray.mean()
        if brightness < 30 or brightness > 225:
            return False

        return True
