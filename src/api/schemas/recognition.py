from pydantic import BaseModel, Field


class RecognizeRequest(BaseModel):
    image_base64: str = Field(min_length=1)
    threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    check_liveness: bool = Field(default=True)
    auto_mark_attendance: bool = Field(default=False)


class RecognizeResponse(BaseModel):
    matched: bool
    user_id: str | None
    name: str | None
    external_id: str | None
    confidence: float
    is_live: bool
    liveness_score: float
    liveness_details: str
    processing_time_ms: float


class BatchRecognizeRequest(BaseModel):
    images_base64: list[str] = Field(min_length=1, max_length=10)
    threshold: float = Field(default=0.55, ge=0.0, le=1.0)
    check_liveness: bool = Field(default=True)


class BatchRecognizeResponse(BaseModel):
    results: list[RecognizeResponse]
    total_images: int
    matched: int
    unmatched: int


class RegistrationUploadRequest(BaseModel):
    images_base64: list[str] = Field(min_length=3, max_length=5)
    quality_threshold: float = Field(default=0.6, ge=0.0, le=1.0)


class RegistrationStatusResponse(BaseModel):
    is_registered: bool
    num_samples: int
    quality_score: float | None
    version: int
    registered_at: str | None


class RegistrationResponse(BaseModel):
    success: bool
    user_id: str
    num_samples: int
    quality_score: float
    message: str
