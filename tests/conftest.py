"""Shared fixtures: the real config and a small, fast world for engine tests."""
from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from starbour.config import GameConfig, load_config  # noqa: E402
from starbour.engine.state import GameState, new_game  # noqa: E402

INI = REPO / "controls_config.ini"
PY = REPO / "MOS" / "Scripts" / "python.exe"


@pytest.fixture(scope="session")
def cfg() -> GameConfig:
    return load_config(INI)


@pytest.fixture(scope="session")
def small_cfg(cfg: GameConfig) -> GameConfig:
    """A 2x2 universe of 3x3 galaxies with 12x12 sectors: generates in milliseconds, same rules."""
    return dataclasses.replace(cfg, universe_size=(2, 2), galaxy_size=(3, 3), sector_size=(12, 12),
                               special_items_per_galaxy=2, normal_items_per_galaxy=6, ai_captains_per_galaxy=2)


@pytest.fixture
def small_state(small_cfg: GameConfig) -> GameState:
    return new_game(99, small_cfg)


@pytest.fixture(scope="session")
def default_state(cfg: GameConfig) -> GameState:
    """The seed-1234 world at the real sizes (about 1.7 s to build); tests must not mutate it."""
    return new_game(1234, cfg)
