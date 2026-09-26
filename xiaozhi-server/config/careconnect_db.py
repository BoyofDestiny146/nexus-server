"""careconnect direct-DB persistence layer for xiaozhi-server.

Replaces the inherited Java manager-api round-trip with a direct MariaDB
write into the same ``ai_agent_chat_history`` table the careconnect bridge
writes to. Both ingest paths converge on one DB row shape so the dashboard,
daily 02:00 triage, and Chroma RAG don't need to know which protocol
delivered a given turn.

Mirrors the pattern in ``careconnect/bridge/bridge_skeleton.py`` (lines
~120-280): pymysql connect against ``xiaozhi_esp32_server`` using the
shared credential at ``~/.config/careconnect/mariadb-app``; INSERT one row
per turn keyed by mac_address; fire-and-forget POST to the careconnect-api
internal-notify endpoint so the dashboard's Redis pub/sub WebSocket
subscribers see the row in real time.

Exported function ``report(...)`` matches the signature of the original
``config.manage_api_client.manage_report`` so ``core/handle/reportHandle.py``
can swap in a one-line import change.

Created 2026-05-04.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import httpx
import pymysql

TAG = __name__
log = logging.getLogger(TAG)

HOME = Path.home()

# Resolved at read time so a single image works on both:
#   - bare-metal dev Jetson: ``~/.config/careconnect/<name>``
#   - fallback: ``~/.config/careconnect/secrets/<name>`` (mount)
_SECRET_DIRS = [
    HOME / ".config" / "careconnect" / "secrets",
    HOME / ".config" / "careconnect",
]


# careconnect: explicit per-secret file overrides. Compose sets these to the
# mounted docker-secret paths (e.g. /run/secrets/mariadb-app). Honoring them is
# the clean container-native fix: it removes the dependency on HOME resolving to
# a path under which cc-secrets happens to be mounted (the container runs as
# root with HOME=/root, but the upstream mount targeted a different HOME, which
# caused "No such file: /root/.config/careconnect/secrets/mariadb-app" and broke
# persona lookup + chat-history writes). Env override > HOME-relative search.
_SECRET_ENV = {
    "mariadb-app": "CC_DB_PASSWORD_FILE",
    "api-internal-token": "CC_INTERNAL_TOKEN_FILE",
}


def _find_secret(name: str) -> Path:
    # 1) explicit env override pointing at the exact file (container-native).
    env_var = _SECRET_ENV.get(name)
    if env_var:
        env_path = os.environ.get(env_var)
        if env_path:
            p = Path(env_path)
            if p.exists():
                return p
    # 2) HOME-relative search (bare-metal dev + legacy mount layouts).
    for d in _SECRET_DIRS:
        p = d / name
        if p.exists():
            return p
    # Return the env-override path if set (names the configured path in errors),
    # else the secrets/ candidate so the error message names a sane path.
    if env_var and os.environ.get(env_var):
        return Path(os.environ[env_var])
    return _SECRET_DIRS[0] / name


# Bridge / api hosts. Override via env when running in containers.
NOTIFY_URL = os.environ.get(
    "CC_NOTIFY_URL", "http://127.0.0.1:8080/api/internal/notify/chat-turn"
)
REVEL_COMMAND_URL = os.environ.get(
    "CC_REVEL_COMMAND_URL", "http://127.0.0.1:8080/api/internal/revel/command"
)
KNOWLEDGE_SEARCH_URL = os.environ.get(
    "CC_KNOWLEDGE_SEARCH_URL",
    "http://127.0.0.1:8080/api/internal/device/{mac}/knowledge-search",
)
DB_HOST = os.environ.get("CC_DB_HOST", "127.0.0.1")
DB_PORT = int(os.environ.get("CC_DB_PORT", "3306"))
DB_USER = os.environ.get("CC_DB_USER", "xiaozhi")
DB_NAME = os.environ.get("CC_DB_NAME", "xiaozhi_esp32_server")

# Singletons resolved on first call so import-time failures don't kill
# xiaozhi-server boot. The credential files are written at careconnect-api
# first-run and persist; if missing we surface a clear error per call.
_db_password: Optional[str] = None
_internal_token: Optional[str] = None


def _password() -> str:
    global _db_password
    if _db_password is None:
        _db_password = _find_secret("mariadb-app").read_text().strip()
    return _db_password


def _token() -> Optional[str]:
    global _internal_token
    if _internal_token is None:
        p = _find_secret("api-internal-token")
        if p.exists():
            _internal_token = p.read_text().strip()
    return _internal_token


def _connect():
    return pymysql.connect(
        host=DB_HOST, port=DB_PORT,
        user=DB_USER, password=_password(),
        database=DB_NAME,
        charset="utf8mb4",
        autocommit=True,
    )


def _normalize_mac(mac_address: str) -> str:
    """Strip colons/dashes/spaces, uppercase. Physical-device key.

    Lockstep with ``api/careconnect_api/watcher_device.py:stripped_mac``.
    """
    return (mac_address or "").strip().upper().replace(":", "").replace("-", "").replace(" ", "")


def _colon_mac(mac_address: str):
    stripped = _normalize_mac(mac_address)
    if len(stripped) == 12 and all(c in "0123456789ABCDEF" for c in stripped):
        return ":".join(stripped[i : i + 2] for i in range(0, 12, 2))
    return None


def _canonical_store_mac(mac_address: str) -> str:
    """Format written on INSERT. 12-hex MACs use colon-separated uppercase."""
    return _colon_mac(mac_address) or _normalize_mac(mac_address)


def _display_mac(mac_address: str) -> str:
    return _colon_mac(mac_address) or (mac_address or "").strip().upper()


def _mac_lookup_candidates(mac_address: str) -> list:
    """Formats that may already exist in ai_device (heartbeat vs onboard)."""
    upper = (mac_address or "").strip().upper()
    stripped = _normalize_mac(upper)
    out = []
    for cand in (upper, stripped, _colon_mac(upper)):
        if cand and cand not in out:
            out.append(cand)
    return out


def _now_utc_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _device_id_for_mac(mac: str) -> str:
    """Deterministic PK from stripped hex — same as API device_id_for_mac."""
    key = _normalize_mac(mac).lower()
    return f"watcher-{key[:24]}"


def ensure_watcher_device(mac_address: str) -> bool:
    """Upsert a W1-A SenseCAP Watcher into ``ai_device`` on WebSocket connect.

    Mirrors ``api/careconnect_api/watcher_device.py:ensure_watcher_device``:
    create a minimal unbound row if the MAC is new; otherwise only refresh
    ``last_connected_at`` and ``last_seen``. Never binds ``agent_id``, never
    overwrites alias / client_device_id / telemetry.

    ``ai_device.id`` is always ``watcher-<stripped-hex>`` so colon and
    stripped spellings collide on the primary key if two connects race.
    No unique index on mac_address is required.

    Returns True if a row was ensured, False if mac was empty or DB failed
    (failures are logged, never raised to the WS path).
    """
    if not _normalize_mac(mac_address):
        return False
    now = _now_utc_naive()
    candidates = _mac_lookup_candidates(mac_address)
    device_id = _device_id_for_mac(mac_address)
    store_mac = _canonical_store_mac(mac_address)
    shown = _display_mac(mac_address)
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                row = None
                for cand in candidates:
                    cur.execute(
                        "SELECT id FROM ai_device WHERE mac_address = %s LIMIT 1",
                        (cand,),
                    )
                    row = cur.fetchone()
                    if row:
                        break
                if row is None:
                    cur.execute(
                        "SELECT id FROM ai_device WHERE id = %s LIMIT 1",
                        (device_id,),
                    )
                    row = cur.fetchone()
                if row:
                    cur.execute(
                        """
                        UPDATE ai_device
                           SET last_connected_at = %s,
                               last_seen = %s
                         WHERE id = %s
                        """,
                        (now, now, row[0]),
                    )
                    log.info("watcher updated mac=%s", shown)
                else:
                    try:
                        cur.execute(
                            """
                            INSERT INTO ai_device
                              (id, mac_address, device_type, firmware_type, board,
                               sort, last_connected_at, last_seen)
                            VALUES (%s, %s, %s, %s, %s, 0, %s, %s)
                            """,
                            (
                                device_id,
                                store_mac,
                                "W1-A",
                                "xiaozhi",
                                "sensecap_watcher",
                                now,
                                now,
                            ),
                        )
                        log.info("watcher registered mac=%s", shown)
                    except pymysql.err.IntegrityError:
                        # Concurrent insert of the same PK — treat as update.
                        cur.execute(
                            """
                            UPDATE ai_device
                               SET last_connected_at = %s,
                                   last_seen = %s
                             WHERE id = %s
                            """,
                            (now, now, device_id),
                        )
                        log.info("watcher updated mac=%s", shown)
            return True
        finally:
            conn.close()
    except Exception as e:
        log.error("careconnect_db.ensure_watcher_device mac=%s failed: %s", shown, e)
        return False


def lookup_agent_id(mac_address: str) -> Optional[str]:
    """Return the ai_agent.id bound to this device's MAC, or None.

    Tries the exact MAC first (in case the row was inserted with the same
    format the firmware sends), then a normalized lookup (uppercase, no
    separators) which matches what the dashboard's onboarding flow stores.
    """
    if not mac_address:
        return None
    candidates = _mac_lookup_candidates(mac_address)
    if not candidates:
        return None
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                for cand in candidates:
                    cur.execute(
                        "SELECT agent_id FROM ai_device WHERE mac_address = %s LIMIT 1",
                        (cand,),
                    )
                    row = cur.fetchone()
                    if row and row[0]:
                        return row[0]
                return None
        finally:
            conn.close()
    except Exception as e:
        log.error("careconnect_db.lookup_agent_id mac=%s failed: %s", mac_address, e)
        return None


def lookup_agent_persona(mac_address: str) -> Optional[dict]:
    """Return agent persona including optional ``bot_name`` command prefix."""
    if not mac_address:
        return None
    candidates = _mac_lookup_candidates(mac_address)
    if not candidates:
        return None
    sql_with = """
        SELECT a.id, a.agent_name, a.system_prompt, a.bot_name
          FROM ai_device d
          JOIN ai_agent a ON a.id = d.agent_id
         WHERE d.mac_address = %s
         LIMIT 1
    """
    sql_without = """
        SELECT a.id, a.agent_name, a.system_prompt
          FROM ai_device d
          JOIN ai_agent a ON a.id = d.agent_id
         WHERE d.mac_address = %s
         LIMIT 1
    """
    try:
        conn = _connect()
        try:
            with conn.cursor() as cur:
                for cand in candidates:
                    row = None
                    try:
                        cur.execute(sql_with, (cand,))
                        row = cur.fetchone()
                        if row:
                            agent_id, agent_name, system_prompt, bot_name = row
                            first_name = (
                                (agent_name or "").strip().split()[0] if agent_name else None
                            )
                            return {
                                "agent_id": agent_id,
                                "agent_name": agent_name,
                                "system_prompt": system_prompt,
                                "first_name": first_name,
                                "bot_name": (bot_name or "").strip() or None,
                            }
                    except Exception as col_err:
                        if "bot_name" not in str(col_err):
                            raise
                        cur.execute(sql_without, (cand,))
                        row = cur.fetchone()
                        if row:
                            agent_id, agent_name, system_prompt = row
                            first_name = (
                                (agent_name or "").strip().split()[0] if agent_name else None
                            )
                            return {
                                "agent_id": agent_id,
                                "agent_name": agent_name,
                                "system_prompt": system_prompt,
                                "first_name": first_name,
                                "bot_name": None,
                            }
                return None
        finally:
            conn.close()
    except Exception as e:
        log.error("careconnect_db.lookup_agent_persona mac=%s failed: %s", mac_address, e)
        return None


def insert_chat_turn(
    mac_address: str,
    agent_id: str,
    session_id: str,
    chat_type: int,
    content: str,
) -> None:
    """One row in ``ai_agent_chat_history``, same shape as the bridge writes.
    Truncates content to 1024 chars (DB column limit; bridge does the same).
    """
    if not (mac_address and agent_id and content):
        return None
    content = content[:1024]
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO ai_agent_chat_history
                  (mac_address, agent_id, session_id, chat_type, content,
                   created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, NOW(3), NOW(3))
                """,
                (mac_address, agent_id, session_id, chat_type, content),
            )
            return cur.lastrowid  # so the live WS push can carry the real DB id
    finally:
        conn.close()


def notify_chat_turn(
    agent_id: str,
    session_id: str,
    chat_type: int,
    content: str,
    mac_address: str,
    row_id: int | None = None,
) -> None:
    """Fire-and-forget POST to the careconnect-api internal-notify endpoint
    so dashboard WebSocket subscribers (``web/lib/useLiveChat.ts``) see the
    new turn in near-real-time. Best-effort — failure is logged but never
    raised; the DB row already landed via insert_chat_turn().
    """
    token = _token()
    if not token:
        return
    payload = {
        "agentId": agent_id,
        "sessionId": session_id,
        "chatType": chat_type,
        "content": content,
        "macAddress": mac_address,
        "id": row_id,
    }
    try:
        httpx.post(
            NOTIFY_URL,
            json=payload,
            headers={"X-Internal-Token": token},
            timeout=0.5,
        )
    except Exception as e:
        log.debug("careconnect_db.notify_chat_turn failed (non-fatal): %s", e)


def post_revel_command(
    agent_id: str,
    utterance: str,
    remainder: str,
) -> dict | None:
    """Synchronous POST to CareConnect allowlisted Revel matcher. No secrets."""
    token = _token()
    if not token or not agent_id or not remainder:
        return None
    try:
        resp = httpx.post(
            REVEL_COMMAND_URL,
            json={
                "agentId": agent_id,
                "utterance": (utterance or "")[:1024],
                "remainder": remainder[:1024],
            },
            headers={"X-Internal-Token": token},
            timeout=2.5,
        )
        if resp.status_code >= 400:
            log.warning("revel command http=%s", resp.status_code)
            return None
        data = resp.json()
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if isinstance(data, dict):
            return data
    except Exception as e:
        log.debug("careconnect_db.post_revel_command failed (non-fatal): %s", e)
    return None


def _knowledge_search_url(mac_address: str) -> str:
    template = os.environ.get("CC_KNOWLEDGE_SEARCH_URL") or KNOWLEDGE_SEARCH_URL
    encoded = quote((mac_address or "").strip(), safe=":")
    if "{mac}" in template:
        return template.replace("{mac}", encoded)
    return f"{template.rstrip('/')}/{encoded}/knowledge-search"


def _knowledge_search_timeout() -> float:
    try:
        return max(0.2, min(float(os.environ.get("CC_KNOWLEDGE_SEARCH_TIMEOUT") or "2.0"), 5.0))
    except (TypeError, ValueError):
        return 2.0


def search_device_knowledge(
    mac_address: str,
    query: str,
    limit: int = 3,
) -> dict | None:
    """POST device-authorized knowledge search. Fail-open: errors return None."""
    token = _token()
    q = (query or "").strip()
    mac = (mac_address or "").strip()
    if not token or not mac or not q:
        return None
    try:
        resp = httpx.post(
            _knowledge_search_url(mac),
            json={"query": q[:2000], "limit": max(1, min(int(limit or 3), 50))},
            headers={"X-Internal-Token": token},
            timeout=_knowledge_search_timeout(),
        )
        if resp.status_code >= 400:
            log.warning(
                "knowledge search http=%s mac=%s",
                resp.status_code,
                _display_mac(mac),
            )
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0):
            log.warning(
                "knowledge search code=%s mac=%s",
                data.get("code"),
                _display_mac(mac),
            )
            return None
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if isinstance(data, dict):
            return data
    except Exception as e:
        log.warning(
            "careconnect_db.search_device_knowledge failed (non-fatal): %s", e
        )
    return None


def post_knowledge_revel(
    mac_address: str,
    query: str,
    already_executed_tag: str | None = None,
    limit: int = 3,
) -> dict | None:
    """POST device Knowledge Revel decision. Fail-open: errors return None."""
    token = _token()
    q = (query or "").strip()
    mac = (mac_address or "").strip()
    if not token or not mac or not q:
        return None
    template = os.environ.get("CC_KNOWLEDGE_REVEL_URL") or (
        (os.environ.get("CC_KNOWLEDGE_SEARCH_URL") or KNOWLEDGE_SEARCH_URL).replace(
            "knowledge-search", "knowledge-revel"
        )
    )
    encoded = quote(mac, safe=":")
    if "{mac}" in template:
        url = template.replace("{mac}", encoded)
    else:
        url = f"{template.rstrip('/')}/{encoded}/knowledge-revel"
    body: dict = {"query": q[:2000], "limit": max(1, min(int(limit or 3), 50))}
    if already_executed_tag:
        body["alreadyExecutedTag"] = str(already_executed_tag)[:64]
    try:
        resp = httpx.post(
            url,
            json=body,
            headers={"X-Internal-Token": token},
            timeout=_knowledge_search_timeout(),
        )
        if resp.status_code >= 400:
            log.warning(
                "knowledge revel http=%s mac=%s",
                resp.status_code,
                _display_mac(mac),
            )
            return None
        data = resp.json()
        if isinstance(data, dict) and data.get("code") not in (None, 0):
            log.warning(
                "knowledge revel code=%s mac=%s",
                data.get("code"),
                _display_mac(mac),
            )
            return None
        if isinstance(data, dict) and isinstance(data.get("data"), dict):
            data = data["data"]
        if isinstance(data, dict):
            return data
    except Exception as e:
        log.warning(
            "careconnect_db.post_knowledge_revel failed (non-fatal): %s", e
        )
    return None


def report(
    mac_address: str,
    session_id: str,
    chat_type: int,
    content: str,
    audio=None,
    report_time: Optional[int] = None,
) -> None:
    """Drop-in replacement for ``config.manage_api_client.manage_report``.

    Signature mirrors the original so ``core/handle/reportHandle.py:37`` can
    swap the import line and keep working. ``audio`` and ``report_time`` are
    accepted for compatibility but ignored — careconnect's chat-history
    schema doesn't store audio (per careconnect/bridge convention) and
    timestamps are populated by MariaDB ``NOW(3)``.
    """
    agent_id = lookup_agent_id(mac_address)
    if not agent_id:
        log.debug("careconnect_db.report: device %s not bound to any agent", mac_address)
        return
    try:
        rid = insert_chat_turn(mac_address, agent_id, session_id, chat_type, content)
        notify_chat_turn(agent_id, session_id, chat_type, content, mac_address, row_id=rid)
    except Exception as e:
        log.error("careconnect_db.report failed mac=%s agent=%s: %s",
                  mac_address, agent_id, e)
