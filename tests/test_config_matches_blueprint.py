"""controls_config.ini must carry every key on tab 15 of the blueprint (CFG-01…60).

docs/config_keys.json is generated from the workbook by tools/blueprint_to_design.py;
this test checks the ini against it. It fails when a key is missing or unparsable,
not when a value differs from the workbook default — values are placeholders that
Usman tunes outside the game (D-49, D-53). Run `pytest tests/test_config_matches_blueprint.py -v`
to see every key checked.
"""

from __future__ import annotations

import configparser
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
INI = REPO / "controls_config.ini"
MANIFEST = REPO / "docs" / "config_keys.json"


def load_ini() -> configparser.ConfigParser:
    cp = configparser.ConfigParser(inline_comment_prefixes=None, interpolation=None)
    read = cp.read(INI, encoding="utf-8")
    assert read, f"controls_config.ini not found or unreadable at {INI}"
    return cp


def manifest_rows() -> list[dict]:
    assert MANIFEST.exists(), (
        f"{MANIFEST} is missing — run tools/blueprint_to_design.py (HARNESS ERROR, not a code problem)"
    )
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["keys"]


ROWS = manifest_rows() if MANIFEST.exists() else []


@pytest.mark.parametrize("row", ROWS, ids=[r["id"] for r in ROWS])
def test_every_blueprint_key_is_in_the_ini(row: dict) -> None:
    cp = load_ini()
    section = row["section"]
    assert cp.has_section(section), f"{row['id']}: section [{section}] missing from controls_config.ini"
    if section == "elements":
        # CFG-42: one line per element, 'name = category, value, rarity'.
        items = cp.items(section)
        assert len(items) >= 12, f"{row['id']}: [elements] has {len(items)} entries; EL-01…12 expect at least 12"
        for name, value in items:
            parts = [p.strip() for p in value.split(",")]
            assert len(parts) == 3, f"{row['id']}: [elements] {name} must be 'category, value, rarity', got {value!r}"
            assert parts[1].isdigit(), f"{row['id']}: [elements] {name} value must be an integer (silver), got {parts[1]!r}"
            assert parts[2] in {"normal", "special"}, f"{row['id']}: [elements] {name} rarity must be normal|special"
        return
    for key in row["keys"]:
        assert cp.has_option(section, key), (
            f"{row['id']}: [{section}] {key} missing from controls_config.ini "
            f"(meaning: {row['meaning']}; used by {row['used_by']})"
        )
        assert cp.get(section, key).strip() != "", f"{row['id']}: [{section}] {key} is empty"


def test_numeric_keys_parse() -> None:
    cp = load_ini()
    numeric_types = {"int", "float"}
    for row in ROWS:
        if row["type"] not in numeric_types or row["section"] == "elements":
            continue
        for key in row["keys"]:
            raw = cp.get(row["section"], key).strip()
            if row["id"] == "CFG-53" and raw == "random":  # seed may be 'random'
                continue
            try:
                float(raw)
            except ValueError:
                pytest.fail(f"{row['id']}: [{row['section']}] {key} = {raw!r} is not numeric ({row['type']})")


def test_upgrade_map_only_names_known_elements() -> None:
    """CFG-33…37 / D-53: every element named in [upgrades] exists in [elements]."""
    cp = load_ini()
    known = set(cp.options("elements"))
    for component, value in cp.items("upgrades"):
        for pair in value.split(","):
            name, _, amount = pair.strip().partition(":")
            assert name in known, f"[upgrades] {component} names unknown element {name!r}"
            float(amount)


def test_grid_sizes_are_MxN() -> None:
    """CFG-54: sizes are written as MxN."""
    cp = load_ini()
    for key in ("universe_size", "galaxy_size", "sector_size"):
        m, x, n = cp.get("world", key).lower().partition("x")
        assert x == "x" and m.strip().isdigit() and n.strip().isdigit(), f"[world] {key} must be MxN"
