import uuid
from datetime import UTC, datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    SmallInteger,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Portable column aliases: PostgreSQL renders these as native UUID/JSON;
# other engines (e.g. SQLite in tests) fall back to CHAR/VARCHAR storage.
PgUUID = Uuid
IpAddress = String(45)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    id: Mapped[uuid.UUID] = mapped_column(PgUUID(), primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class Organization(Base):
    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    logo_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    settings: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    max_users: Mapped[int] = mapped_column(Integer, default=5000, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    users: Mapped[list["User"]] = relationship(
        "User", back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )
    departments: Mapped[list["Department"]] = relationship(
        "Department",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    attendance_records: Mapped[list["Attendance"]] = relationship(
        "Attendance",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    api_keys: Mapped[list["APIKey"]] = relationship(
        "APIKey", back_populates="organization", cascade="all, delete-orphan", passive_deletes=True
    )
    spoof_attempts: Mapped[list["SpoofAttempt"]] = relationship(
        "SpoofAttempt",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    audit_logs: Mapped[list["AuditLog"]] = relationship(
        "AuditLog",
        back_populates="organization",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    def __repr__(self) -> str:
        return f"<Organization {self.slug}>"


class Department(Base):
    __tablename__ = "departments"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="departments"
    )
    parent: Mapped[Optional["Department"]] = relationship(
        "Department", remote_side="Department.id", back_populates="children"
    )
    children: Mapped[list["Department"]] = relationship("Department", back_populates="parent")
    users: Mapped[list["User"]] = relationship("User", back_populates="department")

    __table_args__ = (Index("ix_department_org_name", "organization_id", "name", unique=True),)


class User(Base):
    __tablename__ = "users"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    external_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(50), nullable=True)
    department_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(), ForeignKey("departments.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(String(50), default="member", nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    # Bumped on logout / password change / admin reset; access tokens carry the
    # value they were minted with so a stale token is rejected immediately
    # rather than remaining valid until expiry.
    token_version: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    # NOTE: attribute must not be named "metadata" - SQLAlchemy reserves it.
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    organization: Mapped["Organization"] = relationship("Organization", back_populates="users")
    department: Mapped[Optional["Department"]] = relationship("Department", back_populates="users")
    embedding: Mapped[Optional["Embedding"]] = relationship(
        "Embedding",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    face_images: Mapped[list["FaceImage"]] = relationship(
        "FaceImage", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    attendance_records: Mapped[list["Attendance"]] = relationship(
        "Attendance", back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    notification_prefs: Mapped[Optional["NotificationPreference"]] = relationship(
        "NotificationPreference",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    refresh_tokens: Mapped[list["RefreshTokenRecord"]] = relationship(
        "RefreshTokenRecord",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    password_reset_tokens: Mapped[list["PasswordResetToken"]] = relationship(
        "PasswordResetToken",
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        Index("ix_user_org_external", "organization_id", "external_id", unique=True),
        Index("ix_user_org_name", "organization_id", "name"),
        Index(
            "ix_user_org_email",
            "organization_id",
            "email",
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
            sqlite_where=text("email IS NOT NULL"),
        ),
    )

    @property
    def is_registered(self) -> bool:
        return self.embedding is not None

    @property
    def is_superadmin(self) -> bool:
        return self.role == "superadmin"

    @property
    def is_org_admin(self) -> bool:
        return self.role in ("superadmin", "org_admin")

    def __repr__(self) -> str:
        return f"<User {self.name} org={self.organization_id}>"


class Embedding(Base):
    __tablename__ = "embeddings"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    num_samples: Mapped[int] = mapped_column(SmallInteger, default=0, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    model_version: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    user: Mapped["User"] = relationship("User", back_populates="embedding")


class FaceImage(Base):
    __tablename__ = "face_images"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    storage_key: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="face_images")


class Attendance(Base):
    __tablename__ = "attendance"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    method: Mapped[str] = mapped_column(String(20), default="auto", nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_spoof: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    device_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # NOTE: attribute must not be named "metadata" - SQLAlchemy reserves it.
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict, nullable=False)

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="attendance_records"
    )
    user: Mapped["User"] = relationship("User", back_populates="attendance_records")

    __table_args__ = (
        Index("ix_attendance_org_ts", "organization_id", "timestamp"),
        Index("ix_attendance_user_ts", "user_id", "timestamp"),
    )


class SpoofAttempt(Base):
    __tablename__ = "spoof_attempts"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    reason: Mapped[str | None] = mapped_column(String(50), nullable=True)
    liveness_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    snapshot_key: Mapped[str | None] = mapped_column(Text, nullable=True)

    organization: Mapped["Organization"] = relationship(
        "Organization", back_populates="spoof_attempts"
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(), nullable=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(IpAddress, nullable=True)

    organization: Mapped[Optional["Organization"]] = relationship(
        "Organization", back_populates="audit_logs"
    )
    actor: Mapped[Optional["User"]] = relationship(
        "User", foreign_keys=[actor_id], overlaps="actor"
    )


class APIKey(Base):
    __tablename__ = "api_keys"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    key_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    prefix: Mapped[str] = mapped_column(String(12), nullable=False, index=True)
    scopes: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)

    organization: Mapped["Organization"] = relationship("Organization", back_populates="api_keys")

    def has_scope(self, scope: str) -> bool:
        return "*" in self.scopes or scope in self.scopes


class NotificationPreference(Base):
    __tablename__ = "notification_preferences"

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    email_digest: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    slack_webhook: Mapped[str | None] = mapped_column(Text, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="notification_prefs")


class RefreshTokenRecord(Base):
    __tablename__ = "refresh_tokens"

    jti: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    replaced_by_jti: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_ip: Mapped[str | None] = mapped_column(IpAddress, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="refresh_tokens")

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        expires = (
            self.expires_at.replace(tzinfo=UTC)
            if self.expires_at.tzinfo is None
            else self.expires_at
        )
        return expires <= utcnow()

    __table_args__ = (Index("ix_refresh_token_user_active", "user_id", "revoked_at"),)


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"

    token_hash: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    requested_ip: Mapped[str | None] = mapped_column(IpAddress, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="password_reset_tokens")

    @property
    def is_usable(self) -> bool:
        expires = (
            self.expires_at.replace(tzinfo=UTC)
            if self.expires_at.tzinfo is None
            else self.expires_at
        )
        return self.used_at is None and expires > utcnow()


class TrainingJob(Base):
    __tablename__ = "training_jobs"

    organization_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(), ForeignKey("organizations.id", ondelete="CASCADE"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False, index=True)
    model_type: Mapped[str] = mapped_column(
        String(50), default="inception_resnet_v1", nullable=False
    )
    config: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    checkpoint_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    onnx_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)

    __table_args__ = (Index("ix_training_job_org_status", "organization_id", "status"),)
