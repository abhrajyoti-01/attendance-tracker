from pydantic import BaseModel, EmailStr, Field, field_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)
    organization_slug: str = Field(min_length=1, max_length=100)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user_id: str
    user_name: str
    organization_id: str
    role: str


class RefreshTokenRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr
    organization_slug: str = Field(min_length=1, max_length=100)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    def validate_password_strength(cls, v):
        from src.utils.security import PasswordValidator

        validator = PasswordValidator()
        is_valid, errors = validator.validate(v)
        if not is_valid:
            raise ValueError("; ".join(errors))
        return v


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    def validate_password_strength(cls, v):
        from src.utils.security import PasswordValidator

        validator = PasswordValidator()
        is_valid, errors = validator.validate(v)
        if not is_valid:
            raise ValueError("; ".join(errors))
        return v


class UserContext(BaseModel):
    user_id: str
    user_name: str
    email: str | None = None
    organization_id: str
    organization_name: str
    role: str
    permissions: list[str] = []


class LogoutResponse(BaseModel):
    message: str = "Successfully logged out"
