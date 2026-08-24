from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("LOG_LEVEL", "CRITICAL")
os.environ.setdefault("BILLING_ENABLED", "true")

_TMP = tempfile.mkdtemp(prefix="nexus-tests-")
os.environ.setdefault("STORAGE_LOCAL_ROOT", str(Path(_TMP) / "storage"))
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{Path(_TMP) / 'test.db'}")


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
def tmp_root() -> Path:
    return Path(_TMP)


@pytest.fixture(autouse=True)
def reset_health():
    from nexus.routing.health import health

    health.reset()
    yield
    health.reset()


@pytest.fixture
async def db():
    from nexus.db.session import create_all, get_sessionmaker

    await create_all()
    async with get_sessionmaker()() as session:
        yield session


@pytest.fixture
async def client():
    from httpx import ASGITransport, AsyncClient

    from nexus.api.app import create_app
    from nexus.db.session import create_all

    await create_all()
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture
async def authed_client(client):
    import uuid

    email = f"user-{uuid.uuid4().hex[:8]}@example.com"
    resp = await client.post("/api/v1/auth/signup", json={
        "email": email, "password": "supersecret123", "organization_name": "Test Studio",
    })
    assert resp.status_code == 201, resp.text
    token = resp.json()["access_token"]
    client.headers["Authorization"] = f"Bearer {token}"
    client.email = email  # type: ignore[attr-defined]
    return client
