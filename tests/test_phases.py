"""Turn resolution - TP-01...08, MV-01, SUP-01...04c, D-60, D-67, D-73, D-76."""
from __future__ import annotations

import random

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import orders as O
from starbour.engine.phases import impulse_cells, resolve_turn, step_towards
from starbour.engine.state import GameState, new_game
from starbour.entities.ship import Ship
from starbour.world.generate import game_rng


def run_turns(state: GameState, cfg: GameConfig, n: int, rng: random.Random | None = None, ai_player: bool = False, passive: bool = True):
    """Supplies and movement scenarios run with a passive AI (--ai passive, D-125) so no M3 battle cuts them short."""
    rng = rng or game_rng(state.seed)
    events = []
    for _ in range(n):
        state, events = resolve_turn(state, cfg, rng, ai_player=ai_player, ai_passive=passive)
    return state, events


def texts(events: list[dict], phase: str | None = None) -> list[str]:
    return [e["text"] for e in events if phase is None or e["phase"] == phase]


def test_turn_no_increments_and_phases_named(small_state: GameState, small_cfg: GameConfig) -> None:
    state, events = run_turns(small_state, small_cfg, 1)
    assert state.turn_no == 1
    phases = [e["phase"] for e in events]
    assert phases == sorted(phases), "events arrive in TP order (OB-04)"
    for code in ("TP-01", "TP-02", "TP-03", "TP-04", "TP-05", "TP-06", "TP-07", "TP-08"):
        assert code in phases, f"{code} must appear, even as 'skipped' (never silently absent)"
    assert any(t.startswith("Hazards:") for t in texts(events, "TP-03")), "TP-03 resolves hazards from M2"
    assert any(t.startswith("Combat:") for t in texts(events, "TP-04")), "TP-04 resolves combat from M3"
    assert any(t.startswith("Reputation:") for t in texts(events, "TP-07")), "TP-07 applies reputation from M3"
    assert any("skipped (M4)" in t for t in texts(events, "TP-05"))
    state, _ = run_turns(state, small_cfg, 2)
    assert state.turn_no == 3


def test_movement_helpers() -> None:
    assert impulse_cells(4.0) == 4 and impulse_cells(2.5) == 2 and impulse_cells(0.4) == 0 and impulse_cells(3, 1.5) == 1
    assert step_towards((0, 0), (5, 2), 3) == (3, 2)
    assert step_towards((5, 5), (5, 5), 3) == (5, 5)
    assert step_towards((4, 4), (0, 0), 10) == (0, 0)


def test_player_move_persists_beyond_engine_reach(small_state: GameState, small_cfg: GameConfig) -> None:
    """MV-01 / D-98: a far target moves floor(engine) cells along the Chebyshev path and the order persists."""
    ship = small_state.active_ship()
    ship.position = (ship.galaxy, ship.sector, (0, 0))
    w, h = small_cfg.sector_size
    O.submit(small_state, parse(f"move {w - 1} {h - 1}", ship.id), small_cfg)
    state, events = run_turns(small_state, small_cfg, 1)
    cells = int(ship.engine)
    assert state.ships[ship.id].cell == (cells, cells)
    assert any("continues" in t for t in texts(events, "TP-02"))
    assert state.last_stats["orders_executed"]["player"] == 1
    assert state.move_remaining(ship.id) == [w - 1 - cells, h - 1 - cells]
    assert O.pending(state, ship.id)[0].persisting


def test_relative_move(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    ship.position = (ship.galaxy, ship.sector, (5, 5))
    O.submit(small_state, parse("move +2 -1", ship.id), small_cfg)
    state, _ = run_turns(small_state, small_cfg, 1)
    assert state.ships[ship.id].cell == (7, 4)


def test_ai_moves_every_ship_every_turn(small_state: GameState, small_cfg: GameConfig) -> None:
    """OV-10 / SP-06: every AI ship gets an order and (in a 12x12 sector) moves."""
    ai_ships = [s for s in small_state.ships.values() if s.captain_id != small_state.player_id]
    before = {s.id: s.cell for s in ai_ships}
    state, _ = run_turns(small_state, small_cfg, 1, passive=False)
    st = state.last_stats
    assert st["orders_executed"]["ai"] >= len(ai_ships)
    assert st["ai_ships_moved"] >= len(ai_ships) * 0.5
    moved = sum(1 for s in ai_ships if state.ships[s.id].cell != before[s.id])
    assert moved == st["ai_ships_moved"]
    for _ in range(5):
        state, _ = run_turns(state, small_cfg, 1, passive=False)
        assert state.last_stats["orders_executed"]["ai"] >= 1
    # --ai passive (D-125, D-132): no wandering at all - a spawned opponent stays where the gate put it
    quiet = new_game(small_state.seed, small_cfg)
    cells = {s.id: s.cell for s in quiet.ships.values() if s.captain_id != quiet.player_id}
    quiet, _ = run_turns(quiet, small_cfg, 3)
    assert all(quiet.ships[s].cell == c for s, c in cells.items() if s in quiet.ships)


def test_upkeep_minus_one_player_only(small_state: GameState, small_cfg: GameConfig) -> None:
    ai = next(c for c in small_state.captains.values() if not c.is_player)
    state, events = run_turns(small_state, small_cfg, 3)
    assert state.player.food == small_cfg.food_start - 3 and state.player.water == small_cfg.water_start - 3
    assert ai.food == small_cfg.food_start and ai.water == small_cfg.water_start, "D-60: AI upkeep is off until M4"
    assert any(t.startswith("Upkeep: food") for t in texts(events, "TP-06"))


def test_warning_at_warn_turns(small_state: GameState, small_cfg: GameConfig) -> None:
    n = small_cfg.food_start - small_cfg.warn_turns
    state, events = run_turns(small_state, small_cfg, n - 1)
    assert not any("Supplies warning" in t for t in texts(events))
    state, events = run_turns(state, small_cfg, 1)
    assert state.player.food == small_cfg.warn_turns
    warn = [t for t in texts(events, "TP-06") if "Supplies warning" in t]
    assert warn and "suppl" in warn[0].lower() and "warn" in warn[0].lower()


def test_mutiny_turn_and_demotion(small_state: GameState, small_cfg: GameConfig) -> None:
    """SUP-03, SUP-04b, SUP-04c, D-76: fires at food_start + mutiny_turns_at_zero (33) with no fleet."""
    T = small_cfg.food_start + small_cfg.mutiny_turns_at_zero
    cap = small_state.player
    old_ship_id = cap.active_ship
    captains_before = len(small_state.captains)
    fear_before = cap.fear
    state, events = run_turns(small_state, small_cfg, T - 1)
    assert state.player.food == 0 and state.player.zero_turns == small_cfg.mutiny_turns_at_zero
    assert state.player.active_ship == old_ship_id and len(state.captains) == captains_before
    assert any("mutiny in 1 turn" in t for t in texts(events, "TP-06"))
    state, events = run_turns(state, small_cfg, 1)
    assert state.turn_no == T
    mut = [t for t in texts(events, "TP-06") if "mutin" in t.lower()]
    assert mut, texts(events)
    assert state.last_stats["mutinies"] == [{"turn_no": T, "captain": "player", "result": "demoted"}]
    cap = state.player
    assert cap.active_ship != old_ship_id and len(cap.ships) == 1
    new_ship = state.ships[cap.active_ship]
    assert new_ship.cls == small_cfg.mutiny_ship_class
    assert new_ship.position == state.ships[old_ship_id].position, "the new ship appears in the same cell (SUP-04b)"
    assert cap.food == small_cfg.mutiny_supplies and cap.water == small_cfg.mutiny_supplies and cap.zero_turns == 0
    assert cap.fear == max(0, fear_before - small_cfg.mutiny_fear_delta)
    assert cap.gold == small_cfg.gold_start and cap.silver == small_cfg.silver_start, "coins kept"
    assert len(state.captains) == captains_before + 1
    mutineer = state.captains[state.ships[old_ship_id].captain_id]
    assert not mutineer.is_player and mutineer.ships == [old_ship_id] and mutineer.active_ship == old_ship_id
    # the mutineers act as an AI captain from the next turn (a non-passive turn: every AI ship gets at least one order)
    state, _ = run_turns(state, small_cfg, 1, passive=False)
    assert state.last_stats["orders_executed"]["ai"] >= sum(len(c.ships) for c in state.captains.values() if not c.is_player)


def test_mutiny_with_a_fleet_moves_to_the_nearest_ship(small_state: GameState, small_cfg: GameConfig) -> None:
    """SUP-04 / D-51 / D-72: with other ships the captain moves to the nearest and keeps them."""
    T = small_cfg.food_start + small_cfg.mutiny_turns_at_zero
    cap = small_state.player
    active = small_state.ships[cap.active_ship]
    # free cells only: from M2 a ship parked on a star is destroyed by it (HZ-02), and the start sector has no hole (SP-05)
    from starbour.world.grid import chebyshev
    free = [c for c in small_state.sector_of(active).grid.free_cells() if c != active.cell]
    near_cell = min(free, key=lambda c: (chebyshev(c, active.cell), c))
    far_cell = max(free, key=lambda c: (chebyshev(c, active.cell), c))
    far = Ship.of_class(small_state.new_ship_id(), "Far Gannet", small_cfg.ships["corsair"], cap.id, (active.galaxy, active.sector, far_cell))
    near = Ship.of_class(small_state.new_ship_id(), "Near Plover", small_cfg.ships["corsair"], cap.id, (active.galaxy, active.sector, near_cell))
    small_state.ships[far.id] = far
    small_state.ships[near.id] = near
    cap.ships += [far.id, near.id]
    captains_before = len(small_state.captains)
    state, events = run_turns(small_state, small_cfg, T)
    assert state.last_stats["mutinies"] == [{"turn_no": T, "captain": "player", "result": "moved"}]
    cap = state.player
    assert cap.active_ship == near.id, "nearest by Chebyshev distance on the flattened grid"
    assert sorted(cap.ships) == sorted([far.id, near.id])
    assert cap.food == small_cfg.mutiny_supplies and cap.water == small_cfg.mutiny_supplies
    assert len(state.captains) == captains_before + 1
    assert state.ships[active.id].captain_id != cap.id


def test_two_runs_same_seed_identical(small_cfg: GameConfig) -> None:
    """D-67: same seed + same (no) orders = identical snapshots."""
    a, _ = run_turns(new_game(5, small_cfg), small_cfg, 12, game_rng(5), ai_player=True)
    b, _ = run_turns(new_game(5, small_cfg), small_cfg, 12, game_rng(5), ai_player=True)
    assert a.snapshot_hash() == b.snapshot_hash()
    c, _ = run_turns(new_game(6, small_cfg), small_cfg, 12, game_rng(6), ai_player=True)
    assert a.snapshot_hash() != c.snapshot_hash()


def test_ai_player_drives_the_player(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    before = ship.cell
    state, _ = run_turns(small_state, small_cfg, 1, ai_player=True, passive=False)
    assert state.last_stats["orders_executed"]["player"] >= 1
    assert state.ships[ship.id].cell != before
