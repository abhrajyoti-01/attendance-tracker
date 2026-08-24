import asyncio
from email.message import EmailMessage
from urllib.parse import quote

import aiosmtplib
import structlog

from src.config import settings

logger = structlog.get_logger(__name__)

PASSWORD_RESET_SUBJECT = "Reset your password"
PASSWORD_RESET_BODY = """Hello {name},

We received a request to reset the password for your account ({email}) in {organization}.

Open the link below within {expiry_minutes} minutes to choose a new password:

{reset_url}

If you did not request this, you can safely ignore this email - your password will not change.

Regards,
{from_name}
"""

INVITE_SUBJECT = "You have been added to {organization}"
INVITE_BODY = """Hello {name},

An administrator added you to {organization} on {from_name}.

{setup_line}

Sign in here: {login_url}

Regards,
{from_name}
"""


def _build_message(*, to: str, subject: str, body: str) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = (
        f"{settings.email.from_name} <{settings.email.from_address}>"
        if settings.email.from_name
        else settings.email.from_address
    )
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    return msg


async def _send(msg: EmailMessage) -> None:
    await aiosmtplib.send(
        msg,
        hostname=settings.email.host,
        port=settings.email.port,
        username=settings.email.user or None,
        password=settings.email.password or None,
        start_tls=settings.email.use_tls,
        timeout=settings.email.timeout_seconds,
    )


def build_password_reset_url(raw_token: str, org_slug: str) -> str:
    return (
        f"{settings.email.base_url.rstrip('/')}/reset-password"
        f"?token={quote(raw_token)}&org={quote(org_slug)}"
    )


def build_invite_url(org_slug: str) -> str:
    return f"{settings.email.base_url.rstrip('/')}/login?org={quote(org_slug)}"


async def send_password_reset_email(
    *,
    to: str,
    name: str,
    raw_token: str,
    organization_name: str,
    org_slug: str,
    expiry_minutes: int,
) -> None:
    body = PASSWORD_RESET_BODY.format(
        name=name,
        email=to,
        organization=organization_name,
        expiry_minutes=expiry_minutes,
        reset_url=build_password_reset_url(raw_token, org_slug),
        from_name=settings.email.from_name,
    )
    msg = _build_message(to=to, subject=PASSWORD_RESET_SUBJECT, body=body)

    try:
        await _send(msg)
        logger.info("Password reset email sent", recipient_domain=to.split("@")[-1])
    except Exception:
        logger.exception("Failed to send password reset email")
        raise


async def send_invite_email(
    *,
    to: str,
    name: str,
    organization_name: str,
    org_slug: str,
    has_temporary_password: bool,
) -> None:
    setup_line = (
        "Use the temporary password provided by your administrator, then change it after "
        "your first sign-in."
        if has_temporary_password
        else "Sign in with your existing credentials."
    )
    body = INVITE_BODY.format(
        name=name,
        organization=organization_name,
        from_name=settings.email.from_name,
        setup_line=setup_line,
        login_url=build_invite_url(org_slug),
    )
    msg = _build_message(
        to=to, subject=INVITE_SUBJECT.format(organization=organization_name), body=body
    )
    try:
        await _send(msg)
        logger.info("Invite email sent", recipient_domain=to.split("@")[-1])
    except Exception:
        logger.exception("Failed to send invite email")


def schedule_email(coro) -> None:
    """Fire-and-forget dispatch for use outside FastAPI BackgroundTasks."""
    try:
        loop = asyncio.get_running_loop()
        task = loop.create_task(coro)
        task.add_done_callback(_log_task_exception)
    except RuntimeError:
        logger.warning("No running event loop; dropping email send")


def _log_task_exception(task: "asyncio.Task") -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.error("Background email task failed", error=str(task.exception()))
