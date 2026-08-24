from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator

VALID_ROLES = ("superadmin", "org_admin", "member")


class UserCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    external_id: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    department_id: UUID | None = None
    role: str = Field(default="member")
    password: str | None = Field(
        None,
        min_length=8,
        max_length=72,
        description="Initial password. Omit to create a user that cannot sign in until a password is set.",
    )
    send_invite_email: bool = Field(
        default=False, description="Send an invite email when SMTP is configured."
    )
    metadata: dict = Field(default_factory=dict)

    @field_validator("role")
    def validate_role(cls, v):
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(VALID_ROLES)}")
        return v


class UserUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    external_id: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)
    department_id: UUID | None = None
    role: str | None = None
    is_active: bool | None = None
    metadata: dict | None = None

    @field_validator("role")
    def validate_role(cls, v):
        if v is not None and v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(VALID_ROLES)}")
        return v


class UserResponse(BaseModel):
    id: UUID
    name: str
    external_id: str | None
    email: str | None
    phone: str | None
    department_id: UUID | None
    department_name: str | None
    role: str
    is_active: bool
    is_registered: bool
    registration_quality: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class UserListResponse(BaseModel):
    users: list[UserResponse]
    total: int
    page: int
    page_size: int


class BulkImportItem(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    external_id: str | None = Field(None, max_length=100)
    email: EmailStr | None = None
    phone: str | None = Field(None, max_length=50)


class BulkImportRequest(BaseModel):
    department_id: UUID | None = None
    role: str = Field(default="member")
    users: list[BulkImportItem] = Field(min_length=1, max_length=1000)

    @field_validator("role")
    def validate_role(cls, v):
        if v not in VALID_ROLES:
            raise ValueError(f"role must be one of: {', '.join(VALID_ROLES)}")
        return v


class BulkImportResponse(BaseModel):
    imported: int
    failed: int
    errors: list[dict]


class SetPasswordRequest(BaseModel):
    new_password: str = Field(min_length=8, max_length=72)
