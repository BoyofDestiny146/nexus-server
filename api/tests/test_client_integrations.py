"""Per-client CareConnect / Revel integrations (Directed Logic is UI-only)."""
from __future__ import annotations

import json
import logging
import re

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from careconnect_api.auth import ROLE_ROOT, hash_password, issue_token, verify_password
from careconnect_api.models import AiAgent, AiDevice, ClientIntegration, SysUser
from careconnect_api.watcher_device import device_id_for_mac, get_watcher_device


_API_KEY = "test-api-key-abc123"
_HEADERS_KEY = {"X-API-Key": _API_KEY}
_COLON_MAC = "E0:72:A1:DB:36:40"
_NX_RE = re.compile(r"^Nx-[A-Z0-9]{9}$")
_SECRET_KEYS = (
    "secret_hash",
    "secret_enc",
    "secretHash",
    "secretEnc",
    "apiKey",
    "api_key",
)


@pytest_asyncio.fixture(scope="function")
async def admin_token(client: AsyncClient, db_session: AsyncSession) -> str:
    _ = client
    user = SysUser(
        id=1,
        username="admin1",
        password=hash_password("unused-in-these-tests"),
        super_admin=ROLE_ROOT,
        status=1,
    )
    db_session.add(user)
    await db_session.commit()
    token, _expire = issue_token(user.id, user.username, ROLE_ROOT)
    return token


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _blob(payload) -> str:
    return json.dumps(payload)


def _assert_no_plaintext_secret_field(payload) -> None:
    blob = _blob(payload)
    assert '"secret":' not in blob
    for key in _SECRET_KEYS:
        assert f'"{key}"' not in blob


async def _onboard(client: AsyncClient, token: str, name: str = "B Dalton") -> str:
    resp = await client.post(
        "/api/agent/onboard", json={"name": name}, headers=_auth(token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    return body["data"]["agentId"]


@pytest.mark.asyncio
async def test_list_empty_includes_coming_soon_directed_logic(
    client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token)
    resp = await client.get(
        f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
    )
    body = resp.json()
    assert body["code"] == 0, body
    items = {row["provider"]: row for row in body["data"]["list"]}
    assert items["careconnect"]["connected"] is False
    assert items["revel"]["connected"] is False
    assert items["directed_logic"]["status"] == "coming_soon"
    assert items["directed_logic"]["comingSoon"] is True
    _assert_no_plaintext_secret_field(body)
    assert '"secret":' not in _blob(body["data"])


@pytest.mark.asyncio
async def test_careconnect_create_returns_secret_once_and_stores_hash(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, caplog
):
    agent_id = await _onboard(client, admin_token)
    caplog.set_level(logging.INFO)
    created = await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(admin_token),
    )
    body = created.json()
    assert body["code"] == 0, body
    data = body["data"]
    assert _NX_RE.match(data["publicId"])
    assert data["secretOnce"] is True
    secret = data["secret"]
    assert isinstance(secret, str) and len(secret) >= 32

    listed = await client.get(
        f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
    )
    listed_body = listed.json()
    assert listed_body["code"] == 0, listed_body
    cc = next(
        r for r in listed_body["data"]["list"] if r["provider"] == "careconnect"
    )
    assert cc["connected"] is True
    assert cc["publicId"] == data["publicId"]
    assert cc.get("secret") is None
    assert secret not in _blob(listed_body)
    _assert_no_plaintext_secret_field(listed_body)

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "careconnect",
            )
        )
    ).scalar_one()
    assert row.public_id == data["publicId"]
    assert row.secret_enc is None
    assert row.secret_hash
    assert verify_password(secret, row.secret_hash)
    assert secret not in (row.secret_hash or "")
    # logs must not contain the plaintext secret
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_careconnect_second_create_is_409(
    client: AsyncClient, admin_token: str
):
    agent_id = await _onboard(client, admin_token)
    first = await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(admin_token),
    )
    assert first.json()["code"] == 0
    second = await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(admin_token),
    )
    assert second.json()["code"] == 409


@pytest.mark.asyncio
async def test_careconnect_rotate_keeps_public_id(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    created = (
        await client.post(
            f"/api/agent/{agent_id}/integrations/careconnect",
            headers=_auth(admin_token),
        )
    ).json()["data"]
    old_secret = created["secret"]
    public_id = created["publicId"]

    rotated = await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect/rotate",
        headers=_auth(admin_token),
    )
    body = rotated.json()
    assert body["code"] == 0, body
    assert body["data"]["publicId"] == public_id
    new_secret = body["data"]["secret"]
    assert new_secret != old_secret
    assert body["data"]["secretOnce"] is True

    listed = (
        await client.get(
            f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
        )
    ).json()
    assert new_secret not in _blob(listed)
    assert old_secret not in _blob(listed)

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "careconnect",
            )
        )
    ).scalar_one()
    assert verify_password(new_secret, row.secret_hash)
    assert not verify_password(old_secret, row.secret_hash)


@pytest.mark.asyncio
async def test_careconnect_disconnect_keeps_person_and_devices(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    await client.post(
        "/api/v1/watcher/heartbeat",
        json={"mac": _COLON_MAC, "battery": 70, "fw": "1.9.0"},
        headers=_HEADERS_KEY,
    )
    attached = await client.post(
        "/api/device/attach",
        json={
            "agentId": agent_id,
            "eui": _COLON_MAC,
            "alias": "dalton-watch",
            "deviceType": "W1-A",
            "firmwareType": "xiaozhi",
        },
        headers=_auth(admin_token),
    )
    assert attached.json()["code"] == 0, attached.json()

    await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(admin_token),
    )
    deleted = await client.delete(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(admin_token),
    )
    body = deleted.json()
    assert body["code"] == 0, body
    assert body["data"]["disconnected"] is True
    assert body["data"]["clientPreserved"] is True
    assert body["data"]["devicesPreserved"] is True

    db_session.expire_all()
    assert await db_session.get(AiAgent, agent_id) is not None
    row = (
        await db_session.execute(
            select(func.count())
            .select_from(ClientIntegration)
            .where(ClientIntegration.agent_id == agent_id)
        )
    ).scalar_one()
    assert int(row or 0) == 0
    dev = await get_watcher_device(db_session, _COLON_MAC)
    assert dev is not None
    assert dev.agent_id == agent_id
    assert dev.id == device_id_for_mac(_COLON_MAC)
    leftover = (
        await db_session.execute(
            select(AiDevice).where(AiDevice.id == device_id_for_mac(_COLON_MAC))
        )
    ).scalar_one()
    assert leftover.alias == "dalton-watch"


@pytest.mark.asyncio
async def test_public_id_collision_retries(
    client: AsyncClient, admin_token: str, db_session: AsyncSession, monkeypatch
):
    agent_id = await _onboard(client, admin_token)
    other = await _onboard(client, admin_token, name="Other")
    taken = "Nx-TAKEN0001"
    db_session.add(
        ClientIntegration(
            agent_id=other,
            provider="careconnect",
            public_id=taken,
            secret_hash=hash_password("occupied"),
            status="connected",
        )
    )
    await db_session.commit()

    seq = [taken, "Nx-FRESH0001"]

    def _fake() -> str:
        return seq.pop(0) if seq else "Nx-FRESH0001"

    monkeypatch.setattr("careconnect_api.routers.integrations.new_public_id", _fake)
    created = await client.post(
        f"/api/agent/{agent_id}/integrations/careconnect",
        headers=_auth(admin_token),
    )
    body = created.json()
    assert body["code"] == 0, body
    assert body["data"]["publicId"] == "Nx-FRESH0001"


@pytest.mark.asyncio
async def test_revel_put_never_returns_full_key(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    full_key = "revel-live-key-XXXX7F2A"
    saved = await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": full_key},
        headers=_auth(admin_token),
    )
    body = saved.json()
    assert body["code"] == 0, body
    assert body["data"]["connected"] is True
    assert body["data"]["secretHint"] == "7F2A"
    assert full_key not in _blob(body)
    assert body["data"].get("apiKey") is None
    _assert_no_plaintext_secret_field(body)

    listed = (
        await client.get(
            f"/api/agent/{agent_id}/integrations", headers=_auth(admin_token)
        )
    ).json()
    assert full_key not in _blob(listed)
    revel = next(r for r in listed["data"]["list"] if r["provider"] == "revel")
    assert revel["connected"] is True
    assert revel["secretHint"] == "7F2A"
    assert full_key not in (revel.get("maskedKey") or "")

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "revel",
            )
        )
    ).scalar_one()
    assert row.secret_hash is None
    assert row.secret_enc
    assert full_key not in row.secret_enc
    from careconnect_api.integration_crypto import decrypt_secret

    assert decrypt_secret(row.secret_enc) == full_key


@pytest.mark.asyncio
async def test_revel_replace_key_overwrites_ciphertext(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    first = "first-revel-key-AAAA"
    second = "second-revel-key-7F2A"
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": first},
        headers=_auth(admin_token),
    )
    replaced = await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": second},
        headers=_auth(admin_token),
    )
    body = replaced.json()
    assert body["code"] == 0, body
    assert body["data"]["replaced"] is True
    assert body["data"]["secretHint"] == "7F2A"
    assert first not in _blob(body)
    assert second not in _blob(body)

    db_session.expire_all()
    row = (
        await db_session.execute(
            select(ClientIntegration).where(
                ClientIntegration.agent_id == agent_id,
                ClientIntegration.provider == "revel",
            )
        )
    ).scalar_one()
    from careconnect_api.integration_crypto import decrypt_secret

    assert decrypt_secret(row.secret_enc) == second
    assert first not in (row.secret_enc or "")


@pytest.mark.asyncio
async def test_revel_disconnect_preserves_client(
    client: AsyncClient, admin_token: str, db_session: AsyncSession
):
    agent_id = await _onboard(client, admin_token)
    await client.put(
        f"/api/agent/{agent_id}/integrations/revel",
        json={"apiKey": "revel-key-to-drop-ZZZZ"},
        headers=_auth(admin_token),
    )
    deleted = await client.delete(
        f"/api/agent/{agent_id}/integrations/revel",
        headers=_auth(admin_token),
    )
    assert deleted.json()["code"] == 0
    db_session.expire_all()
    assert await db_session.get(AiAgent, agent_id) is not None
    n = (
        await db_session.execute(
            select(func.count())
            .select_from(ClientIntegration)
            .where(ClientIntegration.agent_id == agent_id)
        )
    ).scalar_one()
    assert int(n or 0) == 0


@pytest.mark.asyncio
async def test_no_directed_logic_routes(client: AsyncClient, admin_token: str):
    agent_id = await _onboard(client, admin_token)
    for method, path in (
        ("POST", f"/api/agent/{agent_id}/integrations/directed-logic"),
        ("PUT", f"/api/agent/{agent_id}/integrations/directed_logic"),
        ("DELETE", f"/api/agent/{agent_id}/integrations/directed_logic"),
    ):
        resp = await client.request(method, path, headers=_auth(admin_token))
        # Unimplemented routes are 404, never 2xx.
        assert resp.status_code in (404, 405) or resp.json().get("code") in (404, 405)
