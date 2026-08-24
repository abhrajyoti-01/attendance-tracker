"""initial baseline schema

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-23

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    is_postgres = bind.dialect.name == "postgresql"

    uuid_kwargs = {"as_uuid": True} if is_postgres else {}

    op.create_table(
        "organizations",
        *_base_columns(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("logo_url", sa.Text(), nullable=True),
        sa.Column("settings", pg.JSON() if is_postgres else sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::json" if is_postgres else "'{}'")),
        sa.Column("max_users", sa.Integer(), nullable=False, server_default="5000"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"], unique=True)
    op.create_index("ix_organizations_is_active", "organizations", ["is_active"])

    org_id = lambda: sa.Column(  # noqa: E731
        "organization_id",
        pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
        sa.ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
    )

    op.create_table(
        "departments",
        *_base_columns(),
        org_id(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "parent_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_departments_organization_id", "departments", ["organization_id"])
    op.create_index("ix_department_org_name", "departments", ["organization_id", "name"], unique=True)

    op.create_table(
        "users",
        *_base_columns(),
        org_id(),
        sa.Column("external_id", sa.String(100), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column(
            "department_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("departments.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("role", sa.String(50), nullable=False, server_default="member"),
        sa.Column("password_hash", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("metadata", pg.JSON() if is_postgres else sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::json" if is_postgres else "'{}'")),
    )
    op.create_index("ix_users_organization_id", "users", ["organization_id"])
    op.create_index("ix_users_is_active", "users", ["is_active"])
    op.create_index("ix_user_org_external", "users", ["organization_id", "external_id"], unique=True)
    op.create_index("ix_user_org_name", "users", ["organization_id", "name"])
    op.create_index(
        "ix_user_org_email",
        "users",
        ["organization_id", "email"],
        unique=True,
        postgresql_where=sa.text("email IS NOT NULL"),
        sqlite_where=sa.text("email IS NOT NULL"),
    )

    op.create_table(
        "embeddings",
        *_base_columns(),
        sa.Column(
            "user_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("num_samples", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("model_version", sa.String(100), nullable=False, server_default=""),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
    )
    op.create_index("ix_embeddings_user_id", "embeddings", ["user_id"], unique=True)

    op.create_table(
        "face_images",
        *_base_columns(),
        sa.Column(
            "user_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("quality_score", sa.Float(), nullable=True),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.Column(
            "captured_at", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_face_images_user_id", "face_images", ["user_id"])

    op.create_table(
        "attendance",
        *_base_columns(),
        org_id(),
        sa.Column(
            "user_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("method", sa.String(20), nullable=False, server_default="auto"),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("is_spoof", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("device_id", sa.String(100), nullable=True),
        sa.Column("metadata", pg.JSON() if is_postgres else sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::json" if is_postgres else "'{}'")),
    )
    op.create_index("ix_attendance_organization_id", "attendance", ["organization_id"])
    op.create_index("ix_attendance_user_id", "attendance", ["user_id"])
    op.create_index("ix_attendance_timestamp", "attendance", ["timestamp"])
    op.create_index("ix_attendance_org_ts", "attendance", ["organization_id", "timestamp"])
    op.create_index("ix_attendance_user_ts", "attendance", ["user_id", "timestamp"])

    op.create_table(
        "spoof_attempts",
        *_base_columns(),
        org_id(),
        sa.Column(
            "timestamp", sa.DateTime(timezone=True), nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("reason", sa.String(50), nullable=True),
        sa.Column("liveness_score", sa.Float(), nullable=True),
        sa.Column("device_id", sa.String(100), nullable=True),
        sa.Column("snapshot_key", sa.Text(), nullable=True),
    )
    op.create_index("ix_spoof_attempts_organization_id", "spoof_attempts", ["organization_id"])
    op.create_index("ix_spoof_attempts_timestamp", "spoof_attempts", ["timestamp"])

    op.create_table(
        "audit_log",
        *_base_columns(),
        sa.Column(
            "organization_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "actor_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("target_type", sa.String(50), nullable=True),
        sa.Column("target_id", pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36), nullable=True),
        sa.Column("details", pg.JSON() if is_postgres else sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::json" if is_postgres else "'{}'")),
        sa.Column("ip_address", pg.INET() if is_postgres else sa.String(45), nullable=True),
    )
    op.create_index("ix_audit_log_organization_id", "audit_log", ["organization_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])

    op.create_table(
        "api_keys",
        *_base_columns(),
        org_id(),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("key_hash", sa.String(128), nullable=False),
        sa.Column("prefix", sa.String(12), nullable=False),
        sa.Column("scopes", pg.JSON() if is_postgres else sa.JSON(), nullable=False,
                  server_default=sa.text("'[]'::json" if is_postgres else "'[]'")),
        sa.Column(
            "created_by",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_api_keys_organization_id", "api_keys", ["organization_id"])
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
    op.create_index("ix_api_keys_is_active", "api_keys", ["is_active"])

    op.create_table(
        "notification_preferences",
        *_base_columns(),
        sa.Column(
            "user_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email_digest", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("slack_webhook", sa.Text(), nullable=True),
    )
    op.create_index("ix_notification_preferences_user_id", "notification_preferences", ["user_id"], unique=True)

    op.create_table(
        "refresh_tokens",
        *_base_columns(),
        sa.Column("jti", sa.String(64), nullable=False),
        sa.Column(
            "user_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "organization_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_jti", sa.String(64), nullable=True),
        sa.Column("created_ip", pg.INET() if is_postgres else sa.String(45), nullable=True),
    )
    op.create_index("ix_refresh_tokens_jti", "refresh_tokens", ["jti"], unique=True)
    op.create_index("ix_refresh_tokens_user_id", "refresh_tokens", ["user_id"])
    op.create_index("ix_refresh_token_user_active", "refresh_tokens", ["user_id", "revoked_at"])

    op.create_table(
        "password_reset_tokens",
        *_base_columns(),
        sa.Column("token_hash", sa.String(128), nullable=False),
        sa.Column(
            "user_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("requested_ip", pg.INET() if is_postgres else sa.String(45), nullable=True),
    )
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True)
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])

    op.create_table(
        "training_jobs",
        *_base_columns(),
        sa.Column(
            "organization_id",
            pg.UUID(**uuid_kwargs) if is_postgres else sa.String(36),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("model_type", sa.String(50), nullable=False, server_default="inception_resnet_v1"),
        sa.Column("config", pg.JSON() if is_postgres else sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::json" if is_postgres else "'{}'")),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metrics", pg.JSON() if is_postgres else sa.JSON(), nullable=False,
                  server_default=sa.text("'{}'::json" if is_postgres else "'{}'")),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("checkpoint_path", sa.Text(), nullable=True),
        sa.Column("onnx_path", sa.Text(), nullable=True),
        sa.Column("celery_task_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_training_jobs_status", "training_jobs", ["status"])
    op.create_index("ix_training_jobs_celery_task_id", "training_jobs", ["celery_task_id"])
    op.create_index("ix_training_job_org_status", "training_jobs", ["organization_id", "status"])


def downgrade() -> None:
    for table in (
        "training_jobs",
        "password_reset_tokens",
        "refresh_tokens",
        "notification_preferences",
        "api_keys",
        "audit_log",
        "spoof_attempts",
        "attendance",
        "face_images",
        "embeddings",
        "users",
        "departments",
        "organizations",
    ):
        op.drop_table(table)
