"""Canonical system Agent Personalities.

Built-in rows are seeded from Ted's source file
``AI_agent_Personalities.txt``. UI cannot edit them; duplicate is allowed.
``{{assistant_name}}`` is substituted at prompt-construction time, never in storage.
The Nexus safety layer is composed separately and is not stored in these templates.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

WITTY_TECH_SIDEKICK_ID = "sys_witty_tech_sidekick"
DEFAULT_PERSONALITY_ID = WITTY_TECH_SIDEKICK_ID

ASSISTANT_NAME_TOKEN = "{{assistant_name}}"

SOURCE_PATH = Path(__file__).with_name("AI_agent_Personalities.txt")

# Stable ids/categories. Names, descriptions, and prompt_template come from source.
_SYSTEM_META: tuple[tuple[str, str], ...] = (
    (WITTY_TECH_SIDEKICK_ID, "system"),
    ("sys_calming_zen_guide", "system"),
    ("sys_pragmatic_strategist", "system"),
    ("sys_sales_charismatic_relationship_builder", "sales"),
    ("sys_sales_high_energy_deal_maker", "sales"),
    ("sys_sales_trusted_authority", "sales"),
    ("sys_care_empathetic_guardian", "care"),
    ("sys_care_resilient_mentor", "care"),
    ("sys_care_mindful_specialist", "care"),
)

_OPTION_SPLIT = re.compile(r"(?=^Option \d+:)", re.M)
_OPTION_HEAD = re.compile(r"^Option \d+:\s*(.+)$", re.M)


def _split_title(title: str) -> tuple[str, str]:
    name = title.strip()
    if name.count("(") > name.count(")"):
        name = name + ")"
    description = ""
    match = re.search(r"\(([^)]+)\)\s*$", name)
    if match:
        description = match.group(1).strip()
    return name, description


def parse_source_personalities(text: str) -> list[tuple[str, str, str]]:
    """Return [(name, description, prompt_template), ...] from Ted's source file."""
    blocks: list[tuple[str, str, str]] = []
    for chunk in _OPTION_SPLIT.split(text):
        chunk = chunk.strip()
        match = _OPTION_HEAD.match(chunk)
        if not match:
            continue
        title = match.group(1).strip()
        body = chunk[match.end() :].strip()
        if ASSISTANT_NAME_TOKEN not in body:
            continue
        name, description = _split_title(title)
        blocks.append((name, description, body))
    return blocks


def load_system_personalities(source_text: str | None = None) -> tuple[dict[str, Any], ...]:
    text = source_text if source_text is not None else SOURCE_PATH.read_text(encoding="utf-8")
    blocks = parse_source_personalities(text)
    if len(blocks) != len(_SYSTEM_META):
        raise RuntimeError(
            f"expected {len(_SYSTEM_META)} source personalities, found {len(blocks)}"
        )
    rows: list[dict[str, Any]] = []
    for (personality_id, category), (name, description, prompt_template) in zip(
        _SYSTEM_META, blocks, strict=True
    ):
        rows.append(
            {
                "id": personality_id,
                "name": name,
                "category": category,
                "description": description,
                "prompt_template": prompt_template,
            }
        )
    return tuple(rows)


SYSTEM_PERSONALITIES: tuple[dict[str, Any], ...] = load_system_personalities()
