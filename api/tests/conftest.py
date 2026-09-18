"""Shared pytest fixtures for careconnect-api unit/integration tests.

Uses an in-process SQLite (aiosqlite) DB so no running MariaDB is needed.
The app is mounted with overridden settings + DB/secret dependencies.
"""
from __future__ import annotations

import os
import secrets
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Point settings env vars BEFORE importing app modules so Settings() picks them up.
_TMP_CFG = tempfile.mkdtemp(prefix="cc_test_cfg_")
_TEST_API_KEY = "test-api-key-abc123"
_TEST_ADMIN1_PW = "AdminPassword1!"
_TEST_ADMIN2_PW = "AdminPassword2!"

os.environ.setdefault("CC_DB_HOST", "unused-for-test")
os.environ.setdefault("CC_DB_PASSWORD_FILE", str(Path(_TMP_CFG) / "mariadb-app"))
os.environ.setdefault("CC_JWT_SECRET_FILE", str(Path(_TMP_CFG) / "jwt-secret"))
os.environ.setdefault("CC_INTERNAL_TOKEN_FILE", str(Path(_TMP_CFG) / "internal-token"))
os.environ.setdefault("CC_CLIENT_API_KEY_FILE", str(Path(_TMP_CFG) / "client-api-key"))
os.environ.setdefault("CC_ADMIN1_PASSWORD", _TEST_ADMIN1_PW)
os.environ.setdefault("CC_ADMIN2_PASSWORD", _TEST_ADMIN2_PW)

# Write the API key into the secret file so the settings can read it.
_api_key_path = Path(_TMP_CFG) / "client-api-key"
_api_key_path.write_text(_TEST_API_KEY)
_api_key_path.chmod(0o600)

# Write a dummy DB password (not used by SQLite, but settings reads it eagerly).
_db_pw_path = Path(_TMP_CFG) / "mariadb-app"
_db_pw_path.write_text("test-db-password")

from careconnect_api.models import Base  # noqa: E402
from careconnect_api.db import get_db  # noqa: E402


# ---------------------------------------------------------------------------
# SQLite in-memory engine (shared per test session for speed, each test gets
# its own tables via create_all / drop_all in the fixture below)
# ---------------------------------------------------------------------------

SQLITE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def db_engine():
    """Create a fresh SQLite engine + schema for each test."""
    engine = create_async_engine(SQLITE_URL, connect_args={"check_same_thread": False})

    # SQLite doesn't enforce FK by default — enable it so tests are realistic.
    @event.listens_for(engine.sync_engine, "connect")
    def _set_fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def client(db_engine) -> AsyncIterator[AsyncClient]:
    """HTTPX async client wired to the FastAPI app with an in-memory DB."""
    from careconnect_api.main import app

    factory = async_sessionmaker(db_engine, expire_on_commit=False, class_=AsyncSession)

    async def _override_get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
