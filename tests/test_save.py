"""Save / load - round-trip hash; atomic write (OV-12, C6, D-61, D-87)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from starbour.config import GameConfig
from starbour.engine import save as S
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState, from_snapshot, hash_snapshot
from starbour.world.generate import game_rng


def test_round_trip_hash(small_state: GameState, small_cfg: GameConfig, tmp_path: Path) -> None:
    rng = game_rng(small_state.seed)
    for _ in range(4):
        resolve_turn(small_state, small_cfg, rng, ai_player=True)
    snap = small_state.snapshot()
    path = tmp_path / "game.json"
    doc = S.save(small_state, rng, path, snap)
    assert "rng_state" in doc and doc["snapshot"] is snap and path.is_file() and not path.with_name("game.json.tmp").exists()
    loaded = S.load(path, small_cfg)
    assert loaded.snapshot_hash() == hash_snapshot(snap) == small_state.snapshot_hash()
    assert loaded.turn_no == 4 and loaded.player.food == small_cfg.food_start - 4
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert hash_snapshot(on_disk["snapshot"]) == small_state.snapshot_hash()


def test_rng_state_restored(small_state: GameState, small_cfg: GameConfig, tmp_path: Path) -> None:
    """The restored game RNG continues exactly where it was (D-67)."""
    rng = game_rng(small_state.seed)
    resolve_turn(small_state, small_cfg, rng, ai_player=True)
    path = tmp_path / "game.json"
    S.save(small_state, rng, path)
    state2, rng2 = S.load_full(path, small_cfg)
    resolve_turn(small_state, small_cfg, rng, ai_player=True)
    resolve_turn(state2, small_cfg, rng2, ai_player=True)
    assert state2.snapshot_hash() == small_state.snapshot_hash()


def test_from_snapshot_is_exact(small_state: GameState, small_cfg: GameConfig) -> None:
    snap = small_state.snapshot()
    assert from_snapshot(snap, small_cfg).snapshot() == snap


def test_queued_orders_survive_the_round_trip(small_state: GameState, small_cfg: GameConfig) -> None:
    from starbour.commands.parse import parse
    from starbour.engine import orders as O
    sid = small_state.player.active_ship
    O.submit(small_state, parse("move 1 1", sid), small_cfg)
    snap = small_state.snapshot()
    assert snap["ships"][sid]["orders_queued"] == 1
    back = from_snapshot(snap, small_cfg)
    assert O.pending_count(back, sid) == 1 and back.snapshot() == snap


def test_atomic_write_keeps_the_previous_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure before os.replace leaves the old file intact and no partial file at the final path."""
    path = tmp_path / "game.json"
    S.write_atomic(path, '{"old": true}')

    def boom(fd):   # noqa: ARG001
        raise OSError("disk full")
    monkeypatch.setattr(S.os, "fsync", boom)
    with pytest.raises(OSError):
        S.write_atomic(path, '{"new": true}')
    assert json.loads(path.read_text(encoding="utf-8")) == {"old": True}


def test_load_errors_are_explicit(tmp_path: Path, small_cfg: GameConfig) -> None:
    with pytest.raises(FileNotFoundError):
        S.load(tmp_path / "missing.json", small_cfg)
    bad = tmp_path / "bad.json"
    bad.write_text('{"not": "a save"}', encoding="utf-8")
    with pytest.raises(ValueError, match="C6"):
        S.load(bad, small_cfg)
    assert S.delete(bad) and not S.delete(bad)
