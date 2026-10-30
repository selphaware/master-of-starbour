"""starbour.config - every key loads typed; MxN parses; a missing key fails loudly (CFG-*, D-53, C7)."""
from __future__ import annotations

import configparser
import json
import subprocess
import sys
from pathlib import Path

import pytest

from starbour.config import ConfigError, GameConfig, load_config

REPO = Path(__file__).resolve().parents[1]
INI = REPO / "controls_config.ini"
MANIFEST = REPO / "docs" / "config_keys.json"


def ini_copy(tmp_path: Path, mutate) -> Path:
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cp.read(INI, encoding="utf-8")
    mutate(cp)
    out = tmp_path / "cfg.ini"
    with open(out, "w", encoding="utf-8") as f:
        cp.write(f)
    return out


def test_every_blueprint_key_has_a_typed_field(cfg: GameConfig) -> None:
    """Each [section] key on tab 15 is read into GameConfig (data sections as dicts)."""
    rows = json.loads(MANIFEST.read_text(encoding="utf-8"))["keys"]
    # CFG-70: [ships] class rows are data (D-53) - names are placeholders, so they key cfg.ships, not fields
    data_sections = {"upgrades": cfg.upgrades, "prices": cfg.prices,
                     "ships": {**cfg.ships, "starting_class": cfg.starting_class}}
    prefixed = {"fear": "fear_", "respect": "respect_", "planets": "planet_"}
    for row in rows:
        sec = row["section"]
        if sec == "elements":
            assert len(cfg.elements) >= 12
            continue
        for key in row["keys"]:
            if sec in data_sections:
                assert key in data_sections[sec], f"{row['id']}: [{sec}] {key} not loaded"
                continue
            name = key if hasattr(cfg, key) else prefixed.get(sec, "") + key
            assert hasattr(cfg, name), f"{row['id']}: [{sec}] {key} has no GameConfig field"


def test_values_match_the_ini(cfg: GameConfig) -> None:
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cp.read(INI, encoding="utf-8")
    assert cfg.food_start == cp.getint("supplies", "food_start")
    assert cfg.warn_turns == cp.getint("supplies", "warn_turns")
    assert cfg.mutiny_ship_class == cp.get("supplies", "mutiny_ship_class").strip().lower()
    assert cfg.pub_port == cp.getint("bus", "pub_port") and cfg.pull_port == cp.getint("bus", "pull_port")
    assert cfg.fear_start == cp.getint("fear", "start") and cfg.respect_start == cp.getint("respect", "start")
    assert cfg.temp_scale == cp.getfloat("stellar", "temp_scale")
    assert cfg.colour_names[-1] == "blue" and len(cfg.colour_names) == len(cfg.colour_thresholds) + 1
    assert cfg.ships["kestrel"].engine == 4.0 and cfg.ships["kestrel"].torpedoes == 6
    assert cfg.starting_class == "kestrel"
    assert cfg.upgrades["engine"]["star_iron"] == 1.5
    assert cfg.elements["orichalcum"].rarity == "special" and cfg.elements["iridium"].value == 30
    assert cfg.prices["food_buy"] == 2
    assert cfg.gold_start == 5 and cfg.silver_start == 50
    assert cfg.path == INI.resolve()


def test_sizes_parse_MxN(cfg: GameConfig) -> None:
    assert cfg.universe_size == (8, 8)
    assert cfg.galaxy_size == (16, 16)
    assert cfg.sector_size == (24, 24)


def test_seed_random_is_none(cfg: GameConfig) -> None:
    assert cfg.seed is None   # [world] seed = random (CFG-53)


def test_bad_size_is_a_config_error(tmp_path: Path) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.set("world", "sector_size", "24by24"))
    with pytest.raises(ConfigError, match=r"\[world\] sector_size"):
        load_config(path)


def test_missing_key_names_section_and_key(tmp_path: Path) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.remove_option("supplies", "food_start"))
    with pytest.raises(ConfigError, match=r"\[supplies\] food_start"):
        load_config(path)


def test_unparsable_number_names_the_key(tmp_path: Path) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.set("supplies", "warn_turns", "five"))
    with pytest.raises(ConfigError, match=r"\[supplies\] warn_turns"):
        load_config(path)


def test_unknown_mutiny_class_rejected(tmp_path: Path) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.set("supplies", "mutiny_ship_class", "Dreadnought"))
    with pytest.raises(ConfigError, match="mutiny_ship_class"):
        load_config(path)


def test_colour_names_must_match_thresholds(tmp_path: Path) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.set("stellar", "colour_names", "red, blue"))
    with pytest.raises(ConfigError, match="colour_names"):
        load_config(path)


def test_cli_exits_4_naming_the_key(tmp_path: Path) -> None:
    """C7: python -m starbour exits 4 with the [section] key when the ini fails validation."""
    path = ini_copy(tmp_path, lambda cp: cp.remove_option("supplies", "water_start"))
    r = subprocess.run([sys.executable, "-m", "starbour", "sim", "--config", str(path), "--turns", "1", "--seed", "1"],
                       cwd=str(REPO), capture_output=True, text=True, timeout=60)
    assert r.returncode == 4, r.stderr
    assert "[supplies] water_start" in r.stderr


def test_cfg_61_to_73_fields_typed(cfg: GameConfig) -> None:
    """Workbook v2.4 rows CFG-61...73 load with the types tab 15 gives (M3 step 0)."""
    assert (cfg.planet_distance_min, cfg.planet_distance_max) == (1, 3)                    # CFG-61
    assert cfg.wormhole_exit_distance == 2                                                  # CFG-62
    for name in ("galaxy_fill_pct", "sector_fill_pct", "multi_star_pct", "hole_pct", "neutron_pct", "wormhole_pct"):
        assert type(getattr(cfg, name)) is int, name                                        # CFG-63, 64, 66
    assert cfg.planets_per_system_max == 3                                                  # CFG-65
    assert type(cfg.radius_min) is float and type(cfg.base_energy_max) is float             # CFG-67
    assert (cfg.peace_shield_pct_at_fear100, cfg.surrender_shield_pct) == (60, 20)          # CFG-72
    assert cfg.roam_every_turns == 10                                                       # CFG-73


@pytest.mark.parametrize("section, key, value", [
    ("world", "planet_distance_min", "0"),
    ("world", "planet_distance_min", "4"),          # above planet_distance_max = 3
    ("hazards", "wormhole_exit_distance", "0"),
    ("ai", "surrender_shield_pct", "120"),
    ("ai", "peace_shield_pct_at_fear100", "sixty"),
    ("ai", "roam_every_turns", "-1"),
    ("world", "hole_pct", "12.5"),
])
def test_cfg_61_to_73_invalid_values_rejected(tmp_path: Path, section: str, key: str, value: str) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.set(section, key, value))
    with pytest.raises(ConfigError, match=key):
        load_config(path)


@pytest.mark.parametrize("section, key", [
    ("world", "planet_distance_max"), ("hazards", "wormhole_exit_distance"),
    ("ai", "peace_shield_pct_at_fear100"), ("ai", "surrender_shield_pct"), ("ai", "roam_every_turns"),
])
def test_cfg_61_to_73_missing_key_names_it(tmp_path: Path, section: str, key: str) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.remove_option(section, key))
    with pytest.raises(ConfigError, match=rf"\[{section}\] {key}"):
        load_config(path)


def test_cfg_74_to_82_fields_typed(cfg: GameConfig) -> None:
    """Workbook v2.5 rows CFG-74...82 load with the types tab 15 gives (M4 step 0)."""
    assert cfg.ai_trace == "sector"                                                         # CFG-74
    assert cfg.ai_class_mix == (("corsair", 60), ("kestrel", 25), ("bastion", 15))          # CFG-75
    assert cfg.phaser_max == 10.0 and cfg.shield_cap == 100.0                               # CFG-76, 82
    assert (cfg.planet_shield_max, cfg.planet_torpedoes, cfg.planet_phasers) == (80.0, 10, 4.0)   # CFG-77
    assert (cfg.food_stock_start, cfg.water_stock_start, cfg.stock_regen, cfg.stock_cap) == (100, 100, 5, 200)   # CFG-78
    assert (cfg.element_stacks_min, cfg.element_stacks_max, cfg.stack_units_min, cfg.stack_units_max) == (2, 4, 1, 5)   # CFG-79
    assert (cfg.item_units_min, cfg.item_units_max) == (1, 3)                               # CFG-80
    assert (cfg.buy_supplies_below, cfg.docking_income_silver) == (10, 10)                  # CFG-81
    assert cfg.torpedo_damage == 15.0                                                       # CFG-25 retuned in v2.5
    for name in ("planet_torpedoes", "stock_cap", "item_units_max", "buy_supplies_below", "docking_income_silver"):
        assert type(getattr(cfg, name)) is int, name
    for name in ("planet_shield_max", "planet_phasers", "shield_cap"):
        assert type(getattr(cfg, name)) is float, name


@pytest.mark.parametrize("section, key, value", [
    ("logging", "ai_trace", "everything"),
    ("world", "ai_class_mix", "corsair:60, dreadnought:40"),
    ("world", "ai_class_mix", "corsair=60"),
    ("world", "ai_class_mix", "corsair:0, kestrel:0"),
    ("world", "item_units_min", "0"),
    ("world", "item_units_min", "4"),               # above item_units_max = 3
    ("battle", "shield_cap", "-1"),
    ("ai", "buy_supplies_below", "-1"),
    ("ai", "docking_income_silver", "lots"),
    ("planets", "shield_max", "-5"),
    ("planets", "torpedoes", "ten"),
    ("planets", "stock_regen", "-1"),
    ("planets", "element_stacks_min", "5"),         # above element_stacks_max = 4
    ("planets", "stack_units_min", "0"),
    ("planets", "stack_units_min", "6"),            # above stack_units_max = 5
])
def test_cfg_74_to_82_invalid_values_rejected(tmp_path: Path, section: str, key: str, value: str) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.set(section, key, value))
    with pytest.raises(ConfigError, match=key):
        load_config(path)


@pytest.mark.parametrize("section, key", [
    ("logging", "ai_trace"), ("world", "ai_class_mix"), ("world", "item_units_max"), ("battle", "shield_cap"),
    ("ai", "buy_supplies_below"), ("ai", "docking_income_silver"), ("planets", "shield_max"),
    ("planets", "stock_cap"), ("planets", "stack_units_max"),
])
def test_cfg_74_to_82_missing_key_names_it(tmp_path: Path, section: str, key: str) -> None:
    path = ini_copy(tmp_path, lambda cp: cp.remove_option(section, key))
    with pytest.raises(ConfigError, match=rf"\[{section}\] {key}"):
        load_config(path)
