"""Unit tests for CareConnect board display mapping."""
from __future__ import annotations

from careconnect_api.board_display import (
    BOARD_DISPLAY_NAMES,
    friendly_board_name,
    normalize_board_slug,
)


def test_m5stack_core_s3_maps_to_cube_v1():
    assert friendly_board_name("m5stack-core-s3") == "Cube V1.0"


def test_sensecap_watcher_slug_maps_to_watcher():
    assert friendly_board_name("sensecap_watcher") == "Watcher"
    assert friendly_board_name("sensecap-watcher") == "Watcher"


def test_unknown_slug_passes_through():
    assert friendly_board_name("custom-board-x") == "custom-board-x"


def test_none_and_blank():
    assert friendly_board_name(None) is None
    assert friendly_board_name("") is None
    assert friendly_board_name("  ") is None
    assert normalize_board_slug("  m5stack-core-s3  ") == "m5stack-core-s3"


def test_catalog_contains_required_cube_entry():
    assert BOARD_DISPLAY_NAMES["m5stack-core-s3"] == "Cube V1.0"
