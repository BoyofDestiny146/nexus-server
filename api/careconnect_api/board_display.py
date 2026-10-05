"""CareConnect board display labels.

Firmware reports a machine board identity (e.g. ``m5stack-core-s3``,
``sensecap-watcher``) in OTA ``board.type`` / ``board.name``. CareConnect
stores that slug on ``ai_device.board`` and maps it to a staff-facing label
for the Devices table.

Do not use this map to rename product copy elsewhere (onboarding wizard
text, etc.) — it is only the Board column / API ``board`` display value.
"""
from __future__ import annotations

# Firmware / stored slug → CareConnect Board column label.
BOARD_DISPLAY_NAMES: dict[str, str] = {
    "m5stack-core-s3": "Cube V1.0",
    "sensecap-watcher": "Watcher",
    "sensecap_watcher": "Watcher",
}


def normalize_board_slug(raw: str | None) -> str | None:
    """Trim and collapse empty strings to None. Preserves firmware slug form."""
    if raw is None:
        return None
    key = str(raw).strip()
    return key or None


def friendly_board_name(raw: str | None) -> str | None:
    """Map a stored/firmware board slug to the CareConnect display label.

    Unknown values pass through unchanged so a one-time admin write of a
    friendly name (or a future slug) still renders.
    """
    key = normalize_board_slug(raw)
    if key is None:
        return None
    if key in BOARD_DISPLAY_NAMES:
        return BOARD_DISPLAY_NAMES[key]
    alt = key.replace("_", "-")
    if alt in BOARD_DISPLAY_NAMES:
        return BOARD_DISPLAY_NAMES[alt]
    alt = key.replace("-", "_")
    if alt in BOARD_DISPLAY_NAMES:
        return BOARD_DISPLAY_NAMES[alt]
    return key
