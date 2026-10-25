"""Headless sim additions - --teleport, --player-script, --world-index, the C9 report fields, game over stops the
in-process run and deletes the save (C2, C9, D-104, D-106, OV-09c)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from starbour.config import GameConfig
from starbour.engine import hazards as H
from starbour.engine import orders as O
from starbour.engine.server import Engine, parse_teleport
from starbour.engine.sim import run_sim, world_index
from starbour.engine.state import new_game


def test_parse_teleport() -> None:
    assert parse_teleport("3 4 7 9 5 5") == ((3, 4), (7, 9), (5, 5))
    with pytest.raises(ValueError, match="D-104"):
        parse_teleport("3 4 7 9")
    with pytest.raises(ValueError):
        parse_teleport("a b c d e f")


def test_world_index_shape_and_order(small_cfg: GameConfig) -> None:
    state = new_game(99, small_cfg)
    idx = world_index(state, small_cfg)
    ship = state.active_ship()
    assert idx["player_start"] == [list(ship.galaxy), list(ship.sector), list(ship.cell)]
    for name in ("stars", "black_holes", "neutron_stars", "planets"):
        assert isinstance(idx[name], list) and len(idx[name]) <= 200
        for e in idx[name][:5]:
            assert len(e["galaxy"]) == 2 and len(e["sector"]) == 2 and len(e["cell"]) == 2
    assert idx["stars"] and all({"bucket", "mass", "colour"} <= set(s) for s in idx["stars"])
    assert all("wormhole_to" in h for h in idx["black_holes"])
    from starbour.world.generate import flatten_cell
    from starbour.world.grid import chebyshev
    origin = flatten_cell(ship.position, small_cfg)
    ds = [chebyshev(flatten_cell((tuple(s["galaxy"]), tuple(s["sector"]), tuple(s["cell"])), small_cfg), origin) for s in idx["stars"]]
    assert ds == sorted(ds), "nearest to the player's start first (C9)"


def test_sim_script_teleport_and_game_over(small_cfg: GameConfig, tmp_path: Path) -> None:
    """A scripted run teleported into a star ends by game_over at N with the C9 fields filled and the save gone."""
    state = new_game(99, small_cfg)
    idx = world_index(state, small_cfg)
    star = min(idx["stars"], key=lambda s: abs(float(s["mass"]) - 1.0))
    n = H.countdown_turns(H.pull(float(star["mass"]), 0, small_cfg.k_g, small_cfg.falloff), small_cfg.pull_turns_scale)
    script = tmp_path / "script.txt"
    script.write_text("\n" * (n + 3), encoding="utf-8")
    report = tmp_path / "report.json"
    rc = run_sim(small_cfg, seed=99, turns=n + 3, ai_player=True, report_path=report, save_dir=tmp_path / "saves",
                 teleport=(tuple(star["galaxy"]), tuple(star["sector"]), tuple(star["cell"])), script_path=script, with_world_index=True)
    assert rc == 0
    doc = json.loads(report.read_text(encoding="utf-8"))
    assert doc["outcome"] == "game_over" and doc["turns_completed"] == n == doc["final"]["game_over_turn"] == len(doc["per_turn"])
    assert doc["final"]["hazard_deaths"] >= 1 and isinstance(doc["final"]["warps"], int) and doc["final"]["game_over_cause"]
    assert "world_index" in doc and doc["world_index"]["player_start"] == [star["galaxy"], star["sector"], star["cell"]]
    first, last = doc["per_turn"][0]["player"], doc["per_turn"][-1]
    assert first["hazard"]["kind"] == "star" and first["hazard"]["turns_left"] == n - 1 and first["move_remaining"] is None
    assert first["shield_current"] == max(0.0, first["shield_max"] - small_cfg.drain_medium) or first["hazard"]["drain"] != small_cfg.drain_medium
    assert last["destroyed"][0]["cause"] == "star" and last["destroyed"][0]["cell"] == star["cell"]
    assert "warps" in last and "destroyed" in last
    assert not (tmp_path / "saves" / "game.json").exists(), "OV-09c: the save is deleted on game over"


def test_sim_script_orders_are_applied_and_noted(small_cfg: GameConfig, tmp_path: Path) -> None:
    state = new_game(99, small_cfg)
    ship = state.active_ship()
    sector = state.sector_of(ship)
    w, h = small_cfg.sector_size
    # a free straight path of 2 cells from the start, if one exists
    direction = next((d for d in ((0, 1), (0, -1), (1, 0), (-1, 0))
                      if all(0 <= ship.cell[0] + d[0] * k < w and 0 <= ship.cell[1] + d[1] * k < h
                             and sector.terrain_at((ship.cell[0] + d[0] * k, ship.cell[1] + d[1] * k)) is None for k in (1, 2))), None)
    if direction is None:
        pytest.skip("no clear 2-cell path from the start in this world")
    script = tmp_path / "script.txt"
    script.write_text(f"move {direction[0] * 2:+d} {direction[1] * 2:+d} 1\nstop\nbogus 1\n", encoding="utf-8")
    report = tmp_path / "report.json"
    rc = run_sim(small_cfg, seed=99, turns=3, ai_player=False, report_path=report, save_dir=tmp_path / "saves", script_path=script)
    assert rc == 0
    doc = json.loads(report.read_text(encoding="utf-8"))
    t1, t2, t3 = doc["per_turn"]
    assert t1["player"]["move_remaining"] == [direction[0], direction[1]], "speed 1: one cell per turn, one remaining (D-98)"
    assert any("script order 'move" in e and "accepted" in e for e in t1["events"])
    assert t2["player"]["move_remaining"] is None and any("all stop" in e for e in t2["events"])
    assert any("not parsed" in e for e in t3["events"])
    assert (tmp_path / "saves" / "game.json").exists()


def test_engine_game_over_state_machine_in_process(small_cfg: GameConfig, tmp_path: Path) -> None:
    """D-105 / CMD-19: after game over every order but new/quit is rejected; new starts at turn 0 with a save."""
    state = new_game(99, small_cfg)
    idx = world_index(state, small_cfg)
    star = min(idx["stars"], key=lambda s: abs(float(s["mass"]) - 1.0))
    n = H.countdown_turns(H.pull(float(star["mass"]), 0, small_cfg.k_g, small_cfg.falloff), small_cfg.pull_turns_scale)
    save = tmp_path / "game.json"
    eng = Engine(small_cfg, seed=99, save_path=save, bus=False, resume=False,
                 teleport=(tuple(star["galaxy"]), tuple(star["sector"]), tuple(star["cell"])))
    eng.start()
    assert save.exists()
    for _ in range(n):
        eng.resolve_and_publish()
    assert eng.state.game_over and eng.stopping and not save.exists()
    over = eng.game_over_payload()
    assert over["turn_no"] == n and over["cause"].startswith("pulled into the star") and set(over["summary"]) >= {"turns", "sectors_visited", "mutinies", "fear", "respect", "gold", "silver"}
    from starbour.commands.parse import parse
    rej = eng.apply_order(parse("move 1 0", eng.state.player.active_ship or "s-000"))
    assert isinstance(rej, O.Rejected) and "CMD-19" in rej.reason
    eng.autosave()
    assert not save.exists(), "OV-09c: nothing resurrects the save after game over"
    eng.handle_new(parse("new 5"))
    assert eng.state.seed == 5 and eng.state.turn_no == 0 and not eng.state.game_over and save.exists()
    eng.close()
