"""add users.token_version for access-token revocation

Revision ID: 0002_token_version
Revises: 0001_baseline
Create Date: 2026-09-15

Access tokens are JWTs and cannot be deleted server-side, so logging out or
changing a password left them valid until expiry. Tokens now embed the user's
``token_version``; bumping the column invalidates every outstanding token.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_token_version"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("token_version", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
