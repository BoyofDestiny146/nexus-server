"""Revel integration metadata on ``cc_client_integration.metadata_json``."""
from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from .envelope import APIException

DEFAULT_API_BASE = "https://api.reveldigital.com"

ALLOWED_INTENTS = (
    "display_calendar",
    "display_photos",
    "display_home",
    "display_reminders",
)

_INTENT_LABELS = {
    "display_calendar": "Display Calendar",
    "display_photos": "Display Photos",
    "display_home": "Display Home",
    "display_reminders": "Display Reminders",
}

_DEFAULT_PHRASES: dict[str, list[str]] = {
    "display_calendar": [
        "show my calendar",
        "display my calendar",
        "show my appointments",
    ],
    "display_photos": [
        "show my pictures",
        "show my photos",
        "display my pictures",
    ],
    "display_home": [
        "go home",
        "show the home screen",
    ],
    "display_reminders": [],
}

# Live Revel tag/command execute is intentionally off until discovery + approval.
EXECUTE_ENABLED = False

_META_SECRET_KEYS = (
    "apiKey",
    "api_key",
    "secret",
    "secret_enc",
    "registrationKey",
    "registration_key",
    "registrationKeyEnc",
    "registration_key_enc",
)


def default_actions() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for intent in ALLOWED_INTENTS:
        out.append(
            {
                "intent": intent,
                "label": _INTENT_LABELS[intent],
                "revelTag": None,
                "enabled": intent != "display_reminders",
                "phrases": list(_DEFAULT_PHRASES.get(intent) or []),
            }
        )
    return out


def empty_meta() -> dict[str, Any]:
    return {
        "apiBaseUrl": DEFAULT_API_BASE,
        "deviceId": None,
        "deviceName": None,
        "discoveredDevices": [],
        "discoveredTags": [],
        "lastDiscoverAt": None,
        "registrationKeyEnc": None,
        "registrationKeyHint": None,
        "actions": default_actions(),
    }


def load_meta(row: Any) -> dict[str, Any]:
    meta = empty_meta()
    raw = getattr(row, "metadata_json", None) if row is not None else None
    if not raw:
        return meta
    try:
        data = json.loads(raw)
    except Exception:
        return meta
    if not isinstance(data, dict):
        return meta
    for key in (
        "apiBaseUrl",
        "deviceId",
        "deviceName",
        "lastDiscoverAt",
        "registrationKeyEnc",
        "registrationKeyHint",
    ):
        val = data.get(key)
        if val is None or isinstance(val, str):
            meta[key] = val
    devices = data.get("discoveredDevices")
    if isinstance(devices, list):
        meta["discoveredDevices"] = [
            d for d in (_public_device(x) for x in devices) if d is not None
        ]
    tags = data.get("discoveredTags")
    if isinstance(tags, list):
        meta["discoveredTags"] = _clean_tags(tags)
    actions = data.get("actions")
    if isinstance(actions, list) and actions:
        meta["actions"] = _merge_actions(actions)
    return meta


def dump_meta(meta: dict[str, Any]) -> str:
    return json.dumps(meta)


def _clean_tags(tags: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tag in tags:
        if not isinstance(tag, str):
            continue
        t = tag.strip()
        if not t or t.casefold() in seen:
            continue
        seen.add(t.casefold())
        out.append(t)
    return out


def _public_device(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    device_id = str(raw.get("id") or raw.get("deviceId") or "").strip()
    if not device_id:
        return None
    name = raw.get("name") or raw.get("deviceName") or device_id
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    if isinstance(raw.get("tags"), str):
        tags = [p.strip() for p in raw["tags"].replace(",", "\n").split("\n") if p.strip()]
    online = raw.get("isOnline")
    if not isinstance(online, bool):
        online = raw.get("is_online") if isinstance(raw.get("is_online"), bool) else None
    return {
        "id": device_id[:128],
        "name": str(name)[:128],
        "isOnline": online,
        "tags": _clean_tags([str(t) for t in tags]),
    }


def _merge_actions(incoming: list[Any]) -> list[dict[str, Any]]:
    by_intent = {a["intent"]: dict(a) for a in default_actions()}
    for item in incoming:
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent") or "").strip()
        if intent not in by_intent:
            continue
        cur = by_intent[intent]
        if "label" in item and isinstance(item["label"], str) and item["label"].strip():
            cur["label"] = item["label"].strip()[:80]
        if "enabled" in item:
            cur["enabled"] = bool(item["enabled"])
        if "revelTag" in item:
            tag = item["revelTag"]
            if tag is None or tag == "":
                cur["revelTag"] = None
            elif isinstance(tag, str):
                cur["revelTag"] = tag.strip()[:64] or None
        if isinstance(item.get("phrases"), list):
            cur["phrases"] = _clean_phrases(item["phrases"])
        by_intent[intent] = cur
    return [by_intent[i] for i in ALLOWED_INTENTS]


def _clean_phrases(phrases: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for p in phrases:
        if not isinstance(p, str):
            continue
        text = " ".join(p.strip().split())
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text[:120])
        if len(out) >= 20:
            break
    return out


def validate_api_base_url(url: str | None) -> str:
    raw = (url or "").strip() or DEFAULT_API_BASE
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise APIException(400, "apiBaseUrl must be an https URL without credentials")
    path = (parsed.path or "").rstrip("/")
    if path not in ("", "/"):
        # Allow a path prefix; strip trailing slash.
        pass
    return f"{parsed.scheme}://{parsed.netloc}{path}".rstrip("/")


def apply_public_config(
    meta: dict[str, Any],
    *,
    api_base_url: str | None = None,
    device_id: str | None = None,
    device_name: str | None = None,
    actions: list[Any] | None = None,
) -> dict[str, Any]:
    if api_base_url is not None:
        meta["apiBaseUrl"] = validate_api_base_url(api_base_url)
    devices = meta.get("discoveredDevices") if isinstance(meta.get("discoveredDevices"), list) else []
    device_ids = {str(d.get("id")) for d in devices if isinstance(d, dict) and d.get("id")}
    if device_id is not None:
        did = device_id.strip()
        if did == "":
            meta["deviceId"] = None
            meta["deviceName"] = None
        else:
            if device_ids and did not in device_ids:
                raise APIException(400, "deviceId must be one of the discovered Revel devices")
            meta["deviceId"] = did[:128]
            if device_name and device_name.strip():
                meta["deviceName"] = device_name.strip()[:128]
            else:
                match = next((d for d in devices if d.get("id") == did), None)
                meta["deviceName"] = (match or {}).get("name") or did
    if actions is not None:
        merged = _merge_actions(actions)
        tags = set(t.casefold() for t in (meta.get("discoveredTags") or []) if isinstance(t, str))
        for action in merged:
            tag = action.get("revelTag")
            if tag and tags and str(tag).casefold() not in tags:
                raise APIException(
                    400,
                    f"revelTag for {action['intent']} must be a discovered tag",
                )
        meta["actions"] = merged
    return meta


def public_meta(meta: dict[str, Any]) -> dict[str, Any]:
    actions = []
    for action in meta.get("actions") or default_actions():
        if not isinstance(action, dict):
            continue
        actions.append(
            {
                "intent": action.get("intent"),
                "label": action.get("label"),
                "revelTag": action.get("revelTag"),
                "enabled": bool(action.get("enabled", True)),
                "phrases": list(action.get("phrases") or []),
            }
        )
    devices = []
    for d in meta.get("discoveredDevices") or []:
        pub = _public_device(d)
        if pub:
            devices.append(pub)
    hint = meta.get("registrationKeyHint")
    if not isinstance(hint, str):
        hint = None
    out = {
        "apiBaseUrl": meta.get("apiBaseUrl") or DEFAULT_API_BASE,
        "deviceId": meta.get("deviceId"),
        "deviceName": meta.get("deviceName"),
        "discoveredDevices": devices,
        "discoveredTags": list(meta.get("discoveredTags") or []),
        "lastDiscoverAt": meta.get("lastDiscoverAt"),
        "registrationKeySet": bool(meta.get("registrationKeyEnc")),
        "registrationKeyHint": hint,
        "actions": actions,
        "executeEnabled": EXECUTE_ENABLED,
        "voiceRequiresBotName": True,
    }
    for key in _META_SECRET_KEYS:
        out.pop(key, None)
    return out


def tags_from_devices(devices: list[dict[str, Any]]) -> list[str]:
    tags: list[str] = []
    for d in devices:
        raw = d.get("tags") if isinstance(d, dict) else None
        if isinstance(raw, list):
            tags.extend(str(t) for t in raw)
        elif isinstance(raw, str):
            tags.extend(part.strip() for part in raw.replace(",", "\n").split("\n"))
    return _clean_tags(tags)
