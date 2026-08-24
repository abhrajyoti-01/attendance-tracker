import random
import time
from collections import deque
from dataclasses import dataclass

import numpy as np

from src.anti_spoofing.liveness import LivenessDetectorLazy


@dataclass
class Challenge:
    type: str
    description: str
    expected_value: any
    timeout: float = 10.0
    created_at: float = 0.0


class ChallengeGenerator:
    def __init__(self):
        self.challenges = [
            "blink",
            "head_left",
            "head_right",
            "head_up",
            "head_down",
            "look_at_dot",
            "smile",
        ]

    def generate_random(self) -> Challenge:
        challenge_type = random.choice(self.challenges)
        return self._generate(challenge_type)

    def _generate(self, challenge_type: str) -> Challenge:
        challenge = Challenge(
            type=challenge_type,
            description="",
            expected_value=None,
            timeout=10.0,
            created_at=time.time(),
        )

        if challenge_type == "blink":
            challenge.description = "Please blink your eyes now"
            challenge.expected_value = True

        elif challenge_type == "head_left":
            challenge.description = "Please turn your head to the left"
            challenge.expected_value = "left"

        elif challenge_type == "head_right":
            challenge.description = "Please turn your head to the right"
            challenge.expected_value = "right"

        elif challenge_type == "head_up":
            challenge.description = "Please look up"
            challenge.expected_value = "up"

        elif challenge_type == "head_down":
            challenge.description = "Please look down"
            challenge.expected_value = "down"

        elif challenge_type == "look_at_dot":
            dot_pos = (random.randint(10, 40), random.randint(10, 40))
            challenge.description = "Please look at the dot on screen"
            challenge.expected_value = dot_pos

        elif challenge_type == "smile":
            challenge.description = "Please smile for the camera"
            challenge.expected_value = "smile"

        return challenge

    def generate_sequence(self, count: int = 3) -> list[Challenge]:
        challenges = []
        available = self.challenges.copy()
        random.shuffle(available)

        for i in range(min(count, len(available))):
            challenges.append(self._generate(available[i]))

        return challenges


class ChallengeResponseVerifier:
    def __init__(self):
        self.detector = LivenessDetectorLazy.get_instance()
        self.current_challenge: Challenge | None = None
        self.response_frames: deque[np.ndarray] = deque(maxlen=30)
        self.responses_received = 0

    def set_challenge(self, challenge: Challenge) -> None:
        self.current_challenge = challenge
        self.response_frames.clear()
        self.responses_received = 0

    def add_frame(self, frame: np.ndarray) -> None:
        if self.current_challenge is None:
            return
        self.response_frames.append(frame)

    def verify(self) -> tuple[bool, str, dict]:
        if self.current_challenge is None:
            return False, "no_challenge_active", {}

        if len(self.response_frames) == 0:
            return False, "no_frames_received", {}

        elapsed = time.time() - self.current_challenge.created_at
        if elapsed > self.current_challenge.timeout:
            return False, "timeout_expired", {"elapsed": elapsed}

        latest_frame = self.response_frames[-1]
        success, detail = self._verify_single(latest_frame)

        if success:
            return True, "challenge_passed", {"detail": detail}
        else:
            return False, f"challenge_failed: {detail}", {}

    def _verify_single(self, frame: np.ndarray) -> tuple[bool, str]:
        success, reason = self.detector.check_challenge_response(
            self.current_challenge.type,
            frame,
            self.current_challenge.expected_value,
        )
        return success, reason

    def get_progress(self) -> dict:
        if self.current_challenge is None:
            return {"active": False}

        elapsed = time.time() - self.current_challenge.created_at
        progress = min(1.0, elapsed / self.current_challenge.timeout)

        return {
            "active": True,
            "challenge_type": self.current_challenge.type,
            "description": self.current_challenge.description,
            "elapsed": elapsed,
            "timeout": self.current_challenge.timeout,
            "progress": progress,
            "frames_collected": len(self.response_frames),
        }


class RandomDigitChallenge:
    def __init__(self):
        self.current_digit: int | None = None
        self.display_start_time = 0.0
        self.display_duration = 3.0

    def generate(self) -> int:
        self.current_digit = random.randint(0, 9)
        self.display_start_time = time.time()
        return self.current_digit

    def verify_spoken(
        self,
        spoken_digit: int,
        max_response_time: float = 5.0,
    ) -> tuple[bool, str]:
        if self.current_digit is None:
            return False, "no_challenge_active"

        elapsed = time.time() - self.display_start_time
        if elapsed > self.display_duration + max_response_time:
            return False, "response_timeout"

        if spoken_digit == self.current_digit:
            return True, "correct"
        else:
            return False, f"wrong_digit: expected {self.current_digit}, got {spoken_digit}"

    def is_display_active(self) -> bool:
        if self.current_digit is None:
            return False
        return (time.time() - self.display_start_time) < self.display_duration


class MultiFactorLivenessChecker:
    def __init__(self, min_checks_passed: int = 2):
        self.min_checks_passed = min_checks_passed
        self.passive_detector = LivenessDetectorLazy.get_instance()
        self.challenge_generator = ChallengeGenerator()
        self.challenge_verifier = ChallengeResponseVerifier()

        self.checks_performed = 0
        self.checks_passed = 0
        self.challenges_passed = 0
        self.challenges_required = 1

    def reset(self) -> None:
        self.checks_performed = 0
        self.checks_passed = 0
        self.challenges_passed = 0
        self.challenge_verifier = ChallengeResponseVerifier()

    def check_passive(self, face_image: np.ndarray) -> tuple[bool, str, float]:
        is_live, reason, score = self.passive_detector.check(face_image)

        self.checks_performed += 1
        if is_live:
            self.checks_passed += 1

        return is_live, reason, score

    def generate_challenge(self) -> Challenge:
        return self.challenge_generator.generate_random()

    def verify_challenge(self, challenge: Challenge) -> tuple[bool, str]:
        return self.challenge_verifier.verify()

    def final_assessment(self) -> tuple[bool, dict]:
        passive_ratio = (
            self.checks_passed / self.checks_performed if self.checks_performed > 0 else 0
        )

        challenge_ratio = (
            self.challenges_passed / self.challenges_required if self.challenges_required > 0 else 0
        )

        overall_score = (passive_ratio * 0.5) + (challenge_ratio * 0.5)
        is_verified = overall_score >= 0.67

        return is_verified, {
            "passive_checks": {
                "performed": self.checks_performed,
                "passed": self.checks_passed,
                "ratio": passive_ratio,
            },
            "active_challenges": {
                "required": self.challenges_required,
                "passed": self.challenges_passed,
                "ratio": challenge_ratio,
            },
            "overall_score": overall_score,
            "verified": is_verified,
        }
