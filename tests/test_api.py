"""End-to-end API tests against an in-memory SQLite database.

Covers the security and correctness fixes that had no regression protection:
audit persistence, tenant isolation, privilege escalation, login enumeration,
enum validation, org-settings enforcement, and the token_version revocation.
"""

import os

os.environ.setdefault("JWT_SECRET", "test-secret-value-that-is-at-least-32-characters")

import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine  # noqa: E402

from src.database.models import AuditLog, Base, Department, Organization, User  # noqa: E402
from src.utils.security import get_password_hash  # noqa: E402

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

PASSWORD = "Str0ng-Passw0rd!"


@pytest_asyncio.fixture
async def db_engine():
    engine = create_async_engine(TEST_DB_URL, connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def session_factory(db_engine):
    return async_sessionmaker(db_engine, expire_on_commit=False, autoflush=False)


@pytest_asyncio.fixture
async def seeded(session_factory):
    """Two orgs; org A has superadmin + org_admin + member, org B is separate."""
    ids = {}
    async with session_factory() as db:
        org_a = Organization(name="Acme", slug="acme", settings={}, max_users=100)
        org_b = Organization(name="Globex", slug="globex", settings={}, max_users=100)
        db.add_all([org_a, org_b])
        await db.flush()

        dept = Department(organization_id=org_a.id, name="Engineering")
        db.add(dept)
        await db.flush()

        users = {
            "super_a": User(
                organization_id=org_a.id,
                name="Super A",
                email="super@acme.example.com",
                role="superadmin",
                password_hash=get_password_hash(PASSWORD),
            ),
            "admin_a": User(
                organization_id=org_a.id,
                name="Admin A",
                email="admin@acme.example.com",
                role="org_admin",
                password_hash=get_password_hash(PASSWORD),
                department_id=dept.id,
            ),
            "member_a": User(
                organization_id=org_a.id,
                name="Member A",
                email="member@acme.example.com",
                role="member",
                password_hash=get_password_hash(PASSWORD),
                department_id=dept.id,
            ),
            "admin_b": User(
                organization_id=org_b.id,
                name="Admin B",
                email="admin@globex.example.com",
                role="org_admin",
                password_hash=get_password_hash(PASSWORD),
            ),
        }
        db.add_all(users.values())
        await db.commit()

        ids = {
            "org_a": str(org_a.id),
            "org_b": str(org_b.id),
            "dept_a": str(dept.id),
            **{key: str(user.id) for key, user in users.items()},
        }
    return ids


@pytest_asyncio.fixture
async def client(session_factory, seeded, monkeypatch):
    """App wired to the test database, with lifespan disabled."""
    from src.api import dependencies
    from src.api.main import app
    from src.database import session as session_module

    async def _override_get_db():
        async with session_factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[dependencies.get_db] = _override_get_db
    monkeypatch.setattr(session_module, "async_session_maker", session_factory)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.seeded = seeded
        yield ac

    app.dependency_overrides.clear()


async def _login(client, email: str, slug: str = "acme", password: str = PASSWORD) -> dict:
    response = await client.post(
        "/api/auth/login",
        json={"email": email, "password": password, "organization_slug": slug},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------
# Audit persistence (rows used to be added but never committed)
# --------------------------------------------------------------------------


async def test_login_success_is_audited(client, session_factory):
    await _login(client, "admin@acme.example.com")
    async with session_factory() as db:
        rows = (
            await db.execute(select(AuditLog).where(AuditLog.action == "auth.login_success"))
        ).all()
    assert len(rows) >= 1


async def test_failed_login_is_audited(client, session_factory):
    await client.post(
        "/api/auth/login",
        json={"email": "admin@acme.example.com", "password": "wrong", "organization_slug": "acme"},
    )
    async with session_factory() as db:
        rows = (
            await db.execute(select(AuditLog).where(AuditLog.action == "auth.login_failed"))
        ).all()
    assert len(rows) >= 1


# --------------------------------------------------------------------------
# Login enumeration
# --------------------------------------------------------------------------


async def test_unknown_org_and_unknown_user_are_indistinguishable(client):
    unknown_org = await client.post(
        "/api/auth/login",
        json={
            "email": "nobody@acme.example.com",
            "password": "x",
            "organization_slug": "does-not-exist",
        },
    )
    unknown_user = await client.post(
        "/api/auth/login",
        json={"email": "nobody@acme.example.com", "password": "x", "organization_slug": "acme"},
    )
    assert unknown_org.status_code == unknown_user.status_code == 401
    assert unknown_org.json()["detail"] == unknown_user.json()["detail"]


async def test_wrong_password_on_inactive_user_looks_like_bad_credentials(client, session_factory):
    """Inactive accounts must not be distinguishable before auth succeeds."""
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.email == "member@acme.example.com"))
        ).scalar_one()
        user.is_active = False
        await db.commit()

    response = await client.post(
        "/api/auth/login",
        json={"email": "member@acme.example.com", "password": "nope", "organization_slug": "acme"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid credentials"


# --------------------------------------------------------------------------
# Privilege escalation
# --------------------------------------------------------------------------


async def test_org_admin_cannot_bulk_import_superadmins(client):
    tokens = await _login(client, "admin@acme.example.com")
    response = await client.post(
        "/api/users/bulk-import",
        headers=_auth(tokens["access_token"]),
        json={
            "role": "superadmin",
            "users": [{"name": "Backdoor", "email": "backdoor@acme.example.com"}],
        },
    )
    assert response.status_code == 403


async def test_org_admin_cannot_bulk_import_admins(client):
    tokens = await _login(client, "admin@acme.example.com")
    response = await client.post(
        "/api/users/bulk-import",
        headers=_auth(tokens["access_token"]),
        json={"role": "org_admin", "users": [{"name": "Peer", "email": "peer@acme.example.com"}]},
    )
    assert response.status_code == 403


async def test_org_admin_cannot_reset_superadmin_password(client):
    tokens = await _login(client, "admin@acme.example.com")
    response = await client.post(
        f"/api/users/{client.seeded['super_a']}/password",
        headers=_auth(tokens["access_token"]),
        json={"new_password": "Hijacked-Pass1!"},
    )
    assert response.status_code == 403


async def test_org_admin_cannot_deactivate_superadmin(client):
    tokens = await _login(client, "admin@acme.example.com")
    response = await client.delete(
        f"/api/users/{client.seeded['super_a']}",
        headers=_auth(tokens["access_token"]),
    )
    assert response.status_code == 403


async def test_superadmin_may_manage_superadmin(client):
    tokens = await _login(client, "super@acme.example.com")
    response = await client.post(
        f"/api/users/{client.seeded['super_a']}/password",
        headers=_auth(tokens["access_token"]),
        json={"new_password": "Rotated-Passw0rd!"},
    )
    assert response.status_code == 200


# --------------------------------------------------------------------------
# Tenant isolation
# --------------------------------------------------------------------------


async def test_cross_org_user_read_returns_404(client):
    tokens = await _login(client, "admin@globex.example.com", slug="globex")
    response = await client.get(
        f"/api/users/{client.seeded['member_a']}", headers=_auth(tokens["access_token"])
    )
    assert response.status_code == 404


async def test_cross_org_user_delete_returns_404(client):
    tokens = await _login(client, "admin@globex.example.com", slug="globex")
    response = await client.delete(
        f"/api/users/{client.seeded['member_a']}", headers=_auth(tokens["access_token"])
    )
    assert response.status_code == 404


async def test_member_cannot_list_users(client):
    tokens = await _login(client, "member@acme.example.com")
    response = await client.get("/api/users", headers=_auth(tokens["access_token"]))
    assert response.status_code == 403


# --------------------------------------------------------------------------
# Token revocation via token_version
# --------------------------------------------------------------------------


async def test_logout_invalidates_access_token(client):
    tokens = await _login(client, "member@acme.example.com")
    access = tokens["access_token"]

    assert (await client.get("/api/users/me", headers=_auth(access))).status_code == 200

    logout = await client.post(
        "/api/auth/logout",
        headers=_auth(access),
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert logout.status_code == 204

    replay = await client.get("/api/users/me", headers=_auth(access))
    assert replay.status_code == 401


async def test_refresh_after_password_change_is_rejected(client):
    tokens = await _login(client, "member@acme.example.com")
    change = await client.post(
        "/api/auth/change-password",
        headers=_auth(tokens["access_token"]),
        json={"current_password": PASSWORD, "new_password": "Brand-New-Pass9!"},
    )
    assert change.status_code == 200

    refreshed = await client.post(
        "/api/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refreshed.status_code == 401


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


async def test_invalid_attendance_method_is_422_not_500(client):
    tokens = await _login(client, "admin@acme.example.com")
    response = await client.post(
        "/api/attendance/manual",
        headers=_auth(tokens["access_token"]),
        json={"user_ids": [client.seeded["member_a"]], "method": "not-a-real-method"},
    )
    assert response.status_code == 422


async def test_valid_attendance_method_succeeds(client):
    tokens = await _login(client, "admin@acme.example.com")
    response = await client.post(
        "/api/attendance/manual",
        headers=_auth(tokens["access_token"]),
        json={"user_ids": [client.seeded["member_a"]], "method": "manual"},
    )
    assert response.status_code == 201, response.text


async def test_forgot_password_never_reveals_org_existence(client):
    known = await client.post(
        "/api/auth/forgot-password",
        json={"email": "admin@acme.example.com", "organization_slug": "acme"},
    )
    unknown_org = await client.post(
        "/api/auth/forgot-password",
        json={"email": "admin@acme.example.com", "organization_slug": "nope"},
    )
    unknown_user = await client.post(
        "/api/auth/forgot-password",
        json={"email": "ghost@acme.example.com", "organization_slug": "acme"},
    )
    assert known.status_code == unknown_org.status_code == unknown_user.status_code == 202
    assert known.json() == unknown_org.json() == unknown_user.json()


# --------------------------------------------------------------------------
# Dashboard feed (used to raise IndexError on the first row)
# --------------------------------------------------------------------------


async def test_dashboard_feed_with_records(client):
    tokens = await _login(client, "admin@acme.example.com")
    headers = _auth(tokens["access_token"])

    marked = await client.post(
        "/api/attendance/manual",
        headers=headers,
        json={"user_ids": [client.seeded["member_a"]], "method": "manual"},
    )
    assert marked.status_code == 201, marked.text

    response = await client.get("/api/dashboard/feed", headers=headers)
    assert response.status_code == 200, response.text
    events = response.json()["events"]
    assert len(events) >= 1
    assert events[0]["user_name"] == "Member A"
    assert events[0]["department_name"] == "Engineering"


# --------------------------------------------------------------------------
# Per-organization settings actually apply
# --------------------------------------------------------------------------


async def test_org_settings_are_returned_with_defaults(client):
    tokens = await _login(client, "admin@acme.example.com")
    response = await client.get("/api/dashboard/settings", headers=_auth(tokens["access_token"]))
    assert response.status_code == 200
    body = response.json()
    assert "recognition_threshold" in body
    assert "liveness_threshold" in body


async def test_org_settings_update_persists(client):
    tokens = await _login(client, "admin@acme.example.com")
    headers = _auth(tokens["access_token"])

    updated = await client.patch(
        "/api/dashboard/settings",
        headers=headers,
        json={"recognition_threshold": 0.72, "working_hours_start": "08:30"},
    )
    assert updated.status_code == 200

    reread = await client.get("/api/dashboard/settings", headers=headers)
    assert reread.json()["recognition_threshold"] == 0.72
    assert reread.json()["working_hours_start"] == "08:30"


async def test_org_settings_reject_malformed_time(client):
    tokens = await _login(client, "admin@acme.example.com")
    response = await client.patch(
        "/api/dashboard/settings",
        headers=_auth(tokens["access_token"]),
        json={"working_hours_start": "25:99"},
    )
    assert response.status_code == 400


async def test_load_org_settings_clamps_below_floor(session_factory, seeded):
    """A configured threshold may never drop under the cross-org safety floor."""
    from src.config import settings as app_settings
    from src.services.org_settings import load_org_settings

    async with session_factory() as db:
        org = (
            await db.execute(select(Organization).where(Organization.slug == "acme"))
        ).scalar_one()
        org.settings = {"recognition_threshold": 0.05}
        await db.commit()

        loaded = await load_org_settings(db, org.id)
        assert loaded["recognition_threshold"] >= app_settings.model.intra_org_threshold_floor


# --------------------------------------------------------------------------
# Health
# --------------------------------------------------------------------------


async def test_health_endpoint_reports_ok(client):
    response = await client.get("/health")
    assert response.status_code in (200, 503)


# --------------------------------------------------------------------------
# SSE stream auth (must not pin a pooled DB connection for its lifetime)
# --------------------------------------------------------------------------


async def test_stream_requires_authentication(client):
    response = await client.get("/api/dashboard/stream")
    assert response.status_code == 401


async def test_stream_rejects_non_admin(client):
    tokens = await _login(client, "member@acme.example.com")
    response = await client.get("/api/dashboard/stream", headers=_auth(tokens["access_token"]))
    assert response.status_code == 403


async def test_stream_rejects_revoked_token(client):
    """The short-lived auth path must still enforce token_version."""
    tokens = await _login(client, "admin@acme.example.com")
    access = tokens["access_token"]

    await client.post(
        "/api/auth/logout", headers=_auth(access), json={"refresh_token": tokens["refresh_token"]}
    )

    response = await client.get("/api/dashboard/stream", headers=_auth(access))
    assert response.status_code == 401
