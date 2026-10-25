"""Atomic save and load of saves/game.json - OV-12, OV-09c, C6, D-61, D-87.

The document is ``{"schema": 1, "config_path": ..., "snapshot": {...}, "rng_state": [...]}``.
It is written to ``game.json.tmp`` and moved into place with ``os.replace`` so a crash
mid-write leaves the previous save intact (tests/test_save.py).
"""
from __future__ import annotations

import json
import logging
import os
import random
from pathlib import Path

from starbour import paths
from starbour.config import GameConfig, load_config
from starbour.engine.state import GameState, from_snapshot

log = logging.getLogger(__name__)
SCHEMA = 1


def rng_state_to_json(state: tuple) -> list:
    version, internal, gauss = state
    return [version, list(internal), gauss]


def rng_state_from_json(raw: list) -> tuple:
    return int(raw[0]), tuple(int(x) for x in raw[1]), raw[2]


def write_atomic(path: Path, text: str) -> None:
    """Temp file + os.replace (C6).  Any failure before the replace leaves ``path`` untouched."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def save(state: GameState, rng: random.Random, path: Path | str, snapshot: dict | None = None) -> dict:
    """Autosave (TP-08).  Pass the snapshot already built this turn so the hash in the report matches byte for byte."""
    snap = snapshot if snapshot is not None else state.snapshot()
    doc = {"schema": SCHEMA, "config_path": str(state.cfg.path), "snapshot": snap, "rng_state": rng_state_to_json(rng.getstate())}
    write_atomic(Path(path), json.dumps(doc, ensure_ascii=False))
    return doc


def load_document(path: Path | str) -> dict:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"no save at {p} (C6)")
    doc = json.loads(p.read_text(encoding="utf-8"))
    if not isinstance(doc, dict) or "snapshot" not in doc or "rng_state" not in doc:
        raise ValueError(f"{p} is not a Master of Starbour save (needs snapshot and rng_state; C6)")
    return doc


def _config_for(doc: dict, cfg: GameConfig | None) -> GameConfig:
    if cfg is not None:
        return cfg
    candidate = doc.get("config_path")
    if candidate and Path(candidate).is_file():
        return load_config(candidate)
    if candidate:
        log.warning("config %s from the save no longer exists; using %s", candidate, paths.config_path())
    return load_config(paths.config_path())


def load_full(path: Path | str, cfg: GameConfig | None = None) -> tuple[GameState, random.Random]:
    """The saved state and the game RNG positioned where it was (D-67)."""
    doc = load_document(path)
    cfg = _config_for(doc, cfg)
    state = from_snapshot(doc["snapshot"], cfg)
    rng = random.Random()
    rng.setstate(rng_state_from_json(doc["rng_state"]))
    return state, rng


def load(path: Path | str, cfg: GameConfig | None = None) -> GameState:
    """C6 / D-79: a state whose ``snapshot_hash()`` equals the saved snapshot's hash."""
    return load_full(path, cfg)[0]


def delete(path: Path | str) -> bool:
    """OV-09c: the save is deleted on game over (and by ``new``)."""
    p = Path(path)
    try:
        p.unlink()
        return True
    except FileNotFoundError:
        return False
