import os
import secrets
from functools import lru_cache
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings

load_dotenv()

# Captured after load_dotenv so values from a real .env file count as explicit.
_JWT_SECRET_EXPLICITLY_SET = bool(os.environ.get("JWT_SECRET"))

BASE_DIR = Path(__file__).resolve().parent.parent
MODELS_DIR = BASE_DIR / "models"

INSECURE_JWT_SECRETS = {
    "change-this-in-production",
    "your-super-secret-jwt-key-change-this-in-production",
    "secret",
    "changeme",
}


def _generate_jwt_secret() -> str:
    """Auto-generate a per-process secret for non-production use only."""
    return secrets.token_urlsafe(48)


class DatabaseSettings(BaseSettings):
    url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/attendance",
        alias="DATABASE_URL",
    )
    pool_size: int = Field(default=20, alias="DB_POOL_SIZE")
    max_overflow: int = Field(default=10, alias="DB_MAX_OVERFLOW")
    echo: bool = Field(default=False, alias="DB_ECHO")
    command_timeout: int = Field(default=30, alias="DB_COMMAND_TIMEOUT")


class RedisSettings(BaseSettings):
    url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    max_connections: int = Field(default=50, alias="REDIS_MAX_CONNECTIONS")
    socket_timeout: int = Field(default=5, alias="REDIS_SOCKET_TIMEOUT")
    socket_connect_timeout: int = Field(default=5, alias="REDIS_SOCKET_CONNECT_TIMEOUT")


class JWTSettings(BaseSettings):
    secret: str = Field(default_factory=_generate_jwt_secret, alias="JWT_SECRET")
    algorithm: str = Field(default="HS256", alias="JWT_ALGORITHM")
    access_token_expire_minutes: int = Field(default=30, alias="JWT_ACCESS_TOKEN_EXPIRE_MINUTES")
    refresh_token_expire_days: int = Field(default=7, alias="JWT_REFRESH_TOKEN_EXPIRE_DAYS")
    reset_token_expire_minutes: int = Field(default=60, alias="JWT_RESET_TOKEN_EXPIRE_MINUTES")


class SecuritySettings(BaseSettings):
    require_redis: bool = Field(default=False, alias="SECURITY_REQUIRE_REDIS")
    docs_url_in_production: bool = Field(default=False, alias="SECURITY_DOCS_IN_PRODUCTION")
    force_password_change_first_login: bool = Field(
        default=False, alias="SECURITY_FORCE_PASSWORD_CHANGE_FIRST_LOGIN"
    )
    max_failed_logins_before_delay: int = Field(
        default=5, alias="SECURITY_MAX_FAILED_LOGINS_BEFORE_DELAY"
    )


class StorageSettings(BaseSettings):
    endpoint: str = Field(default="localhost:9000", alias="MINIO_ENDPOINT")
    access_key: str = Field(default="", alias="MINIO_ACCESS_KEY")
    secret_key: str = Field(default="", alias="MINIO_SECRET_KEY")
    bucket_name: str = Field(default="attendance-images", alias="MINIO_BUCKET_NAME")
    export_bucket_name: str = Field(default="attendance-exports", alias="MINIO_EXPORT_BUCKET")
    use_ssl: bool = Field(default=False, alias="MINIO_USE_SSL")
    region: str = Field(default="us-east-1", alias="MINIO_REGION")
    presigned_url_expiry_seconds: int = Field(default=3600, alias="MINIO_PRESIGN_EXPIRY_SECONDS")
    enabled: bool = Field(default=False, alias="STORAGE_ENABLED")


class ModelSettings(BaseSettings):
    path: str = Field(
        default=str(MODELS_DIR / "exported" / "embedding_net.onnx"), alias="MODEL_PATH"
    )
    device: str = Field(default="cpu", alias="DEVICE")
    embedding_dim: int = Field(default=512, alias="EMBEDDING_DIM")
    face_image_size: int = Field(default=160, alias="FACE_IMAGE_SIZE")
    match_threshold_default: float = Field(default=0.55, alias="MATCH_THRESHOLD_DEFAULT")
    intra_org_threshold_floor: float = Field(default=0.30, alias="MATCH_THRESHOLD_FLOOR")
    batch_size: int = Field(default=32, alias="MODEL_BATCH_SIZE")
    num_workers: int = Field(default=4, alias="MODEL_NUM_WORKERS")
    onnx_input_name: str = Field(default="input.1", alias="ONNX_INPUT_NAME")

    @field_validator("device")
    def validate_device(cls, v):
        if v not in ("cpu", "cuda", "mps"):
            raise ValueError(f"Invalid device: {v}. Must be 'cpu', 'cuda', or 'mps'")
        return v


class AntiSpoofSettings(BaseSettings):
    liveness_threshold: float = Field(default=0.67, alias="SPOOF_LIVENESS_THRESHOLD")
    blink_required_window: float = Field(default=3.0, alias="SPOOF_BLINK_REQUIRED_WINDOW")
    nose_movement_px: int = Field(default=12, alias="SPOOF_NOSE_MOVEMENT_PX")
    texture_variance_min: float = Field(default=15.0, alias="SPOOF_TEXTURE_VARIANCE_MIN")
    ear_closed: float = Field(default=0.18, alias="SPOOF_EAR_CLOSED")
    ear_open: float = Field(default=0.25, alias="SPOOF_EAR_OPEN")
    blink_consec_frames: int = Field(default=2, alias="SPOOF_BLINK_CONSEC_FRAMES")


class APISettings(BaseSettings):
    host: str = Field(default="0.0.0.0", alias="API_HOST")
    port: int = Field(default=8000, alias="API_PORT")
    workers: int = Field(default=4, alias="API_WORKERS")
    max_upload_size: int = Field(default=10485760, alias="MAX_UPLOAD_SIZE")
    default_page_size: int = Field(default=50, alias="API_DEFAULT_PAGE_SIZE")
    max_page_size: int = Field(default=200, alias="API_MAX_PAGE_SIZE")


class RateLimitSettings(BaseSettings):
    enabled: bool = Field(default=True, alias="RATE_LIMIT_ENABLED")
    requests_per_minute: int = Field(default=100, alias="RATE_LIMIT_PER_MINUTE")
    auth_requests_per_minute: int = Field(default=10, alias="RATE_LIMIT_AUTH_PER_MINUTE")
    recognition_requests_per_minute: int = Field(
        default=60, alias="RATE_LIMIT_RECOGNITION_PER_MINUTE"
    )
    enabled_in_production_only: bool = Field(default=False, alias="RATE_LIMIT_PROD_ONLY")


class CORSSettings(BaseSettings):
    origins: list[str] = Field(
        default=["http://localhost:3000", "http://localhost:8080"],
        alias="CORS_ORIGINS",
    )
    allow_credentials: bool = Field(default=True, alias="CORS_ALLOW_CREDENTIALS")
    allow_methods: list[str] = ["GET", "POST", "PATCH", "DELETE", "OPTIONS"]
    allow_headers: list[str] = [
        "Authorization",
        "Content-Type",
        "X-API-Key",
        "X-Correlation-ID",
    ]

    @field_validator("origins", mode="before")
    def parse_origins(cls, v):
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v


class LogSettings(BaseSettings):
    level: str = Field(default="INFO", alias="LOG_LEVEL")
    format: str = Field(default="json", alias="LOG_FORMAT")

    @field_validator("level")
    def validate_level(cls, v):
        if v.upper() not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            raise ValueError(f"Invalid log level: {v}")
        return v.upper()


class CelerySettings(BaseSettings):
    broker_url: str = Field(default="redis://localhost:6379/1", alias="CELERY_BROKER_URL")
    result_backend: str = Field(default="redis://localhost:6379/2", alias="CELERY_RESULT_BACKEND")
    task_routes: dict = {
        "src.workers.tasks.embedding_tasks.*": {"queue": "embedding"},
        "src.workers.tasks.training_tasks.*": {"queue": "training"},
        "src.workers.tasks.export_tasks.*": {"queue": "export"},
        "src.workers.tasks.maintenance_tasks.*": {"queue": "default"},
    }
    beat_schedule: dict = {}


class FeatureFlags(BaseSettings):
    enable_faiss_matching: bool = Field(default=False, alias="ENABLE_FAISS_MATCHING")
    enable_training: bool = Field(default=True, alias="ENABLE_TRAINING")
    enable_async_export: bool = Field(default=True, alias="ENABLE_ASYNC_EXPORT")
    enable_email: bool = Field(default=True, alias="ENABLE_EMAIL")
    enable_live_feed: bool = Field(default=True, alias="ENABLE_LIVE_FEED")


class OrganizationSettings(BaseSettings):
    default_max_users: int = Field(default=5000, alias="DEFAULT_ORG_MAX_USERS")
    attendance_dedupe_window_minutes: int = Field(
        default=10, alias="ATTENDANCE_DEDUPE_WINDOW_MINUTES"
    )

    @property
    def default_settings(self) -> dict[str, Any]:
        settings_str = os.getenv("DEFAULT_ORG_SETTINGS", "{}")
        try:
            import json

            return json.loads(settings_str)
        except (json.JSONDecodeError, TypeError):
            return {
                "working_hours_start": "09:00",
                "working_hours_end": "17:00",
                "allow_late_checkin": True,
            }


class EmailSettings(BaseSettings):
    host: str = Field(default="", alias="SMTP_HOST")
    port: int = Field(default=587, alias="SMTP_PORT")
    user: str = Field(default="", alias="SMTP_USER")
    password: str = Field(default="", alias="SMTP_PASSWORD")
    from_address: str = Field(default="noreply@attendance.local", alias="SMTP_FROM")
    from_name: str = Field(default="Attendance Tracker", alias="SMTP_FROM_NAME")
    use_tls: bool = Field(default=True, alias="SMTP_USE_TLS")
    timeout_seconds: int = Field(default=15, alias="SMTP_TIMEOUT_SECONDS")
    base_url: str = Field(default="http://localhost:3000", alias="EMAIL_BASE_URL")

    @property
    def enabled(self) -> bool:
        return bool(self.host and self.from_address)


class MonitoringSettings(BaseSettings):
    prometheus_enabled: bool = Field(default=True, alias="PROMETHEUS_ENABLED")
    prometheus_port: int = Field(default=9090, alias="PROMETHEUS_PORT")


class Settings(BaseSettings):
    env: str = Field(default="development", alias="ENV")
    debug: bool = Field(default=False, alias="DEBUG")

    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    jwt: JWTSettings = Field(default_factory=JWTSettings)
    security: SecuritySettings = Field(default_factory=SecuritySettings)
    storage: StorageSettings = Field(default_factory=StorageSettings)
    model: ModelSettings = Field(default_factory=ModelSettings)
    anti_spoof: AntiSpoofSettings = Field(default_factory=AntiSpoofSettings)
    api: APISettings = Field(default_factory=APISettings)
    rate_limit: RateLimitSettings = Field(default_factory=RateLimitSettings)
    cors: CORSSettings = Field(default_factory=CORSSettings)
    logging: LogSettings = Field(default_factory=LogSettings)
    celery: CelerySettings = Field(default_factory=CelerySettings)
    features: FeatureFlags = Field(default_factory=FeatureFlags)
    org: OrganizationSettings = Field(default_factory=OrganizationSettings)
    email: EmailSettings = Field(default_factory=EmailSettings)
    monitoring: MonitoringSettings = Field(default_factory=MonitoringSettings)

    @field_validator("env", mode="before")
    def validate_env(cls, v):
        v = str(v).lower()
        if v not in ("development", "staging", "production"):
            raise ValueError(f"Invalid environment: {v}")
        return v

    @model_validator(mode="after")
    def validate_production_safety(self):
        if self.is_production:
            errors = []
            if (
                not _JWT_SECRET_EXPLICITLY_SET
                or self.jwt.secret in INSECURE_JWT_SECRETS
                or len(self.jwt.secret) < 32
            ):
                errors.append(
                    "JWT_SECRET must be explicitly set (env or .env) to a strong unique value "
                    "(>= 32 chars) in production"
                )
            if self.debug:
                errors.append("DEBUG must be false in production")
            wildcard_cors = any(o == "*" for o in self.cors.origins)
            if wildcard_cors:
                errors.append("CORS_ORIGINS must not contain '*' in production")
            if errors:
                raise ValueError("Refusing to start in production: " + "; ".join(errors))
        elif self.jwt.secret in INSECURE_JWT_SECRETS:
            import warnings

            warnings.warn(
                "Using an insecure JWT secret; acceptable only outside production",
                stacklevel=2,
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.env == "production"

    @property
    def is_development(self) -> bool:
        return self.env == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
