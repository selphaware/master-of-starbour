"""Where the config, saves and logs live - BR-20, BR-21, D-61.

The only module that decides this: next to the executable when frozen
(PyInstaller, sys.frozen), else the repository root (the parent of the package).
"""
from __future__ import annotations

import sys
from pathlib import Path


def root() -> Path:
    """Folder holding controls_config.ini, saves/ and logs/ (D-61)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def config_path() -> Path:
    """controls_config.ini - BR-20."""
    return root() / "controls_config.ini"


def saves_dir() -> Path:
    """saves/ - BR-21, OV-12."""
    return root() / "saves"


def save_path() -> Path:
    """The single save slot saves/game.json - OV-12, C6."""
    return saves_dir() / "game.json"


def logs_dir() -> Path:
    """logs/ - engine.log, launcher.log, <role>.log."""
    return root() / "logs"
