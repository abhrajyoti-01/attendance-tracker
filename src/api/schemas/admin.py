from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

API_KEY_SCOPES = (
    "recognition:read",
    "recognition:write",
    "attendance:write",
    "attendance:read",
    "*",
)

VALID_ROLES = ("superadmin", "org_admin", "member")


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=2, max_length=100, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    max_users: int = Field(default=5000, ge=1)
    settings: dict = Field(default_factory=dict)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    max_users: int | None = Field(None, ge=1)
    is_active: bool | None = None
    settings: dict | None = None


class OrganizationResponse(BaseModel):
    id: UUID
    name: str
    slug: str
    max_users: int
    is_active: bool
    user_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class OrganizationListResponse(BaseModel):
    organizations: list[OrganizationResponse]
    total: int


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    parent_id: UUID | None = None


class DepartmentUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    parent_id: UUID | None = None


class DepartmentResponse(BaseModel):
    id: UUID
    name: str
    parent_id: UUID | None
    user_count: int
    created_at: datetime

    model_config = {"from_attributes": True}


class DepartmentListResponse(BaseModel):
    departments: list[DepartmentResponse]
    total: int


class APIKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    scopes: list[str] = Field(
        default_factory=lambda: ["recognition:write", "attendance:write"],
        min_length=1,
    )
    expires_in_days: int | None = Field(None, ge=1, le=3650)

    @field_validator("scopes")
    def validate_scopes(cls, v):
        unknown = [s for s in v if s not in API_KEY_SCOPES]
        if unknown:
            raise ValueError(f"Unknown scopes: {', '.join(unknown)}")
        return v


class APIKeyResponse(BaseModel):
    id: UUID
    name: str
    prefix: str
    scopes: list[str]
    is_active: bool
    last_used_at: datetime | None
    expires_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class APIKeyCreatedResponse(APIKeyResponse):
    """Returned only on creation - includes the plaintext key exactly once."""

    key: str


class APIKeyListResponse(BaseModel):
    api_keys: list[APIKeyResponse]
    total: int
