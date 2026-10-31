"""M2 turn resolution - persistent moves, stop, warp, countdowns, drain, recharge, wormholes, destruction,
game over, AI hazard avoidance (CMD-06/07/21, MV-01...05, HZ-02...10, OV-09b, D-98, D-100...105, D-108...111).

Scenarios place terrain by hand in the small world and teleport the ship there (the D-104 debug path)."""
from __future__ import annotations

import random

import pytest

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import ai
from starbour.engine import hazards as H
from starbour.engine import orders as O
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState, from_snapshot, new_game
from starbour.entities.ship import Ship
from starbour.world.generate import game_rng
from starbour.world.grid import chebyshev
from starbour.world.stellar import BLACK_HOLE, NEUTRON_STAR, STAR, WORMHOLE, Hole, StarBody


def run_turns(state: GameState, cfg: GameConfig, n: int, rng: random.Random | None = None, ai_player: bool = False):
    """Hazard scenarios run with a passive AI (--ai passive, D-125): from M3 an AI captain would otherwise open fire on a
    ship parked at 0 shields in a star's cell and end the scenario by combat instead of by the hazard."""
    rng = rng or game_rng(state.seed)
    events: list[dict] = []
    for _ in range(n):
        state, events = resolve_turn(state, cfg, rng, ai_player=ai_player, ai_passive=True)
    return state, events


def texts(events: list[dict], phase: str | None = None) -> list[str]:
    return [e["text"] for e in events if phase is None or e["phase"] == phase]


def clear_sector(state: GameState, ship: Ship) -> None:
    """Strip the terrain of the ship's sector so a scenario controls every hazard."""
    sector = state.sector_of(ship)
    for cell in list(sector.grid.coords()):
        sector.grid.remove(*cell)


def place_star(state: GameState, ship: Ship, cell: tuple[int, int], mass: float = 1.0, bucket: str = "medium") -> None:
    state.sector_of(ship).grid.set(cell[0], cell[1], StarBody(cell, mass ** (1 / 3), 5.0, mass, "yellow", bucket))


def place_hole(state: GameState, ship: Ship, cell: tuple[int, int], kind: str, mass: float, partner=None) -> None:
    state.sector_of(ship).grid.set(cell[0], cell[1], Hole(kind, cell, mass, partner))


def put(state: GameState, ship: Ship, cell: tuple[int, int]) -> None:
    state.teleport((ship.galaxy, ship.sector, cell))


def submit(state: GameState, cfg: GameConfig, text: str) -> O.Accepted:
    ship = state.active_ship()
    res = O.submit(state, parse(text, ship.id), cfg)
    assert isinstance(res, O.Accepted), getattr(res, "reason", res)
    return res


def immediate(state: GameState, cfg: GameConfig, text: str) -> O.Accepted:
    ship = state.active_ship()
    res = O.apply_immediate(state, parse(text, ship.id), cfg)
    assert isinstance(res, O.Accepted), getattr(res, "reason", res)
    return res


# --------------------------------------------------------------------------- persistent moves (CMD-06, CMD-21, D-98)

def test_persistent_move_arrives_over_turns(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    put(small_state, ship, (1, 0))
    submit(small_state, small_cfg, "move +0 +10")
    step = int(ship.engine)
    cells, remaining = [], []
    for _ in range(5):
        small_state, _ = run_turns(small_state, small_cfg, 1)
        cells.append(ship.cell)
        remaining.append(small_state.move_remaining(ship.id))
    assert cells == [(1, min(10, step)), (1, min(10, 2 * step)), (1, 10), (1, 10), (1, 10)]
    assert remaining[:3] == [[0, 10 - step], [0, 10 - 2 * step], None] and remaining[3:] == [None, None]
    snap = small_state.snapshot()
    assert snap["ships"][ship.id]["move_remaining"] is None and snap["ships"][ship.id]["orders"] == []


def test_persisting_move_in_snapshot_orders_and_text(small_state: GameState, small_cfg: GameConfig) -> None:
    """C9 / PNL-07 / D-99: the persisting move is listed as 'move +0 +6' with its cells per turn."""
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    put(small_state, ship, (1, 0))
    submit(small_state, small_cfg, "move +0 +10")
    small_state, _ = run_turns(small_state, small_cfg, 1)
    step = int(ship.engine)
    snap = small_state.snapshot()
    s = snap["ships"][ship.id]
    assert s["move_remaining"] == [0, 10 - step] and s["orders_queued"] == 1
    assert s["orders"][0]["command"] == "move" and s["orders"][0]["args"] == ["+0", f"+{10 - step}"] and s["orders"][0]["persisting"]
    listing = immediate(small_state, small_cfg, "orders")
    assert f"move +0 +{10 - step}" in listing.text and f"{step} cells/turn" in listing.text
    # D-98: survives save/load (terrain is regenerated from the seed, D-87, so only the dynamic part is compared here)
    back = from_snapshot(snap, small_cfg)
    assert back.move_remaining(ship.id) == [0, 10 - step] and back.snapshot()["ships"] == snap["ships"]


def test_stop_cancels_the_persisting_move(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    put(small_state, ship, (1, 0))
    submit(small_state, small_cfg, "move +0 +10")
    small_state, _ = run_turns(small_state, small_cfg, 1)
    after_one = ship.cell
    res = immediate(small_state, small_cfg, "stop")
    assert "cancelled" in res.text and res.events and res.events[0]["type"] == "stopped"
    assert small_state.move_remaining(ship.id) is None and O.pending_count(small_state, ship.id) == 0
    small_state, _ = run_turns(small_state, small_cfg, 2)
    assert ship.cell == after_one
    again = immediate(small_state, small_cfg, "stop")
    assert "nothing to stop" in again.text and not again.events, "CMD-21: info message when there is nothing to stop"


def test_stop_all_and_a_new_move_replaces(small_state: GameState, small_cfg: GameConfig) -> None:
    cap = small_state.player
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    put(small_state, ship, (1, 0))
    second = Ship.of_class(small_state.new_ship_id(), "Second Tern", small_cfg.ships["corsair"], cap.id, (ship.galaxy, ship.sector, (0, 0)))
    small_state.ships[second.id] = second
    cap.ships.append(second.id)
    submit(small_state, small_cfg, "move +0 +10")
    O.submit(small_state, parse("move +0 +11", second.id), small_cfg)
    small_state, _ = run_turns(small_state, small_cfg, 1)
    assert small_state.move_remaining(ship.id) and small_state.move_remaining(second.id)
    res = immediate(small_state, small_cfg, "stop all")
    assert "Second Tern" in res.text and small_state.move_remaining(second.id) is None and small_state.move_remaining(ship.id) is None
    # OV-02b: a second move replaces the persisting one
    put(small_state, ship, (1, 0))
    submit(small_state, small_cfg, "move +0 +10")
    small_state, _ = run_turns(small_state, small_cfg, 1)
    y = ship.cell[1]
    res = submit(small_state, small_cfg, "move +2 +0")
    assert "replaced the persisting move" in res.text and O.pending_count(small_state, ship.id) == 1
    small_state, _ = run_turns(small_state, small_cfg, 1)
    assert ship.cell == (3, y) and small_state.move_remaining(ship.id) is None


# --------------------------------------------------------------------------- stars: countdown, drain, recharge, reset (HZ-02, HZ-03, HZ-08, HZ-09, CM-05)

def test_sun_like_star_kills_on_the_seventh_turn(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    place_star(small_state, ship, (6, 6), mass=1.0)
    put(small_state, ship, (6, 6))
    n = H.countdown_turns(1.0, small_cfg.pull_turns_scale)
    assert n == 7
    lefts, shields = [], []
    for t in range(1, n):
        small_state, events = run_turns(small_state, small_cfg, 1)
        hz = small_state.hazard_dict(ship)
        lefts.append(hz["turns_left"])
        shields.append(ship.shield_current)
        assert hz["kind"] == "star" and hz["distance"] == 0 and hz["pull"] == pytest.approx(1.0) and hz["drain"] == small_cfg.drain_medium
        assert any("until pulled in" in s for s in texts(events, "TP-03")), "OUT-03 every turn in range"
        assert not small_state.game_over
    assert lefts == [n - t for t in range(1, n)], "D-100: N - t at turn t"
    assert shields == [max(0.0, ship.shield_max - t * small_cfg.drain_medium) for t in range(1, n)], "HZ-03 drain, HZ-09 floor at 0"
    cap = small_state.player
    small_state, events = run_turns(small_state, small_cfg, 1)
    assert small_state.turn_no == n and small_state.game_over and small_state.game_over_turn == n
    assert ship.id not in small_state.ships and cap.ships == [] and cap.active_ship is None and cap.id in small_state.captains
    destroyed = small_state.last_stats["destroyed"]
    assert destroyed == [{"ship": ship.id, "captain": cap.id, "cause": "star", "cell": [6, 6]}]
    assert small_state.last_stats["game_over"]["cause"].startswith("pulled into the star at 6,6")
    assert any("game over" in s.lower() and "new" in s for s in texts(events, "TP-03")), "OUT-16"
    assert small_state.snapshot()["game_over"] is True
    # after game over the engine side accepts nothing but new/quit - the AI player stays quiet (no ship)
    small_state, events = run_turns(small_state, small_cfg, 1, ai_player=True)
    assert small_state.last_stats["orders_executed"]["player"] == 0


def test_countdown_resets_and_shields_recharge(small_state: GameState, small_cfg: GameConfig) -> None:
    """HZ-08 / CM-05 / smoke/16: 3 turns in, 3 out, 1 in (counter reset -> N - 1), out again (recharge to the cap)."""
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    place_star(small_state, ship, (6, 6), mass=1.0)
    put(small_state, ship, (6, 6))
    drain, charge, cap_ = small_cfg.drain_medium, small_cfg.shield_recharge, ship.shield_max
    small_state, _ = run_turns(small_state, small_cfg, 3)
    assert ship.shield_current == cap_ - 3 * drain and small_state.hazard_dict(ship)["turns_left"] == 7 - 3
    submit(small_state, small_cfg, "move +1 +0")
    small_state, events = run_turns(small_state, small_cfg, 1)
    assert ship.cell == (7, 6) and small_state.hazard_dict(ship) is None and ship.hazard_turns == 0
    assert ship.shield_current == cap_ - 3 * drain + charge and any("recharge" in s.lower() for s in texts(events, "TP-06"))
    small_state, _ = run_turns(small_state, small_cfg, 2)
    assert ship.shield_current == cap_ - 3 * drain + 3 * charge
    submit(small_state, small_cfg, "move -1 +0")
    small_state, _ = run_turns(small_state, small_cfg, 1)
    assert ship.cell == (6, 6) and small_state.hazard_dict(ship)["turns_left"] == 7 - 1, "HZ-08: the counter restarted"
    assert ship.shield_current == cap_ - 3 * drain + 3 * charge - drain
    submit(small_state, small_cfg, "move +1 +0")
    small_state, _ = run_turns(small_state, small_cfg, 7)
    assert ship.shield_current == cap_, "CM-05: recharge caps at shield_max"
    assert not small_state.game_over and small_state.turn_no == 14


def test_drain_floors_at_zero_without_destroying(small_state: GameState, small_cfg: GameConfig) -> None:
    """HZ-09: shields at 0 do not destroy by themselves; the countdown does (pull_turns_scale is config, CFG-20)."""
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    place_star(small_state, ship, (6, 6), mass=1.0, bucket="high")
    put(small_state, ship, (6, 6))
    small_state, _ = run_turns(small_state, small_cfg, 4)
    assert ship.shield_current == 0.0 and not small_state.game_over
    import dataclasses
    slow = dataclasses.replace(small_cfg, pull_turns_scale=10)
    s2 = new_game(small_state.seed, slow)
    ship2 = s2.active_ship()
    clear_sector(s2, ship2)
    place_star(s2, ship2, (6, 6), mass=1.0)
    put(s2, ship2, (6, 6))
    s2, _ = run_turns(s2, slow, 9)
    assert not s2.game_over and s2.hazard_dict(ship2)["turns_left"] == 1
    s2, _ = run_turns(s2, slow, 1)
    assert s2.game_over_turn == 10


# --------------------------------------------------------------------------- holes (HZ-04, HZ-05, HZ-06, D-100, D-102)

def test_held_by_a_black_hole_then_destroyed(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    place_hole(small_state, ship, (0, 0), BLACK_HOLE, small_cfg.blackhole_mass)
    put(small_state, ship, (2, 2))
    pull = H.pull(small_cfg.blackhole_mass, 2, small_cfg.k_g, small_cfg.falloff)
    assert pull == pytest.approx(3.849, abs=0.005) and int(ship.engine - pull) <= 0
    submit(small_state, small_cfg, "move +1 +1")
    small_state, events = run_turns(small_state, small_cfg, 1)
    assert ship.cell == (2, 2) and any("held" in s for s in texts(events, "TP-02"))
    hz = small_state.hazard_dict(ship)
    assert hz["kind"] == "black_hole" and hz["distance"] == 2 and hz["turns_left"] == 1 and hz["drain"] == 0
    assert ship.shield_current == ship.shield_max, "HZ-06 / HZ-04: holes do not drain"
    small_state, _ = run_turns(small_state, small_cfg, 1)
    assert small_state.game_over_turn == 2 and small_state.last_stats["destroyed"][0]["cause"] == "black_hole"


def test_moving_away_from_a_hole_buys_time(small_state: GameState, small_cfg: GameConfig) -> None:
    """D-100 / smoke/17 (b): from distance 4, directly away at full power: 2, 2, 3 cells; turns_left 6, 8, 12."""
    import dataclasses
    wide = dataclasses.replace(small_cfg, sector_size=(16, 16))
    state = new_game(small_state.seed, wide)
    ship = state.active_ship()
    clear_sector(state, ship)
    place_hole(state, ship, (0, 0), BLACK_HOLE, wide.blackhole_mass)
    put(state, ship, (4, 4))
    lefts, cells = [], []
    for _ in range(3):
        d = chebyshev(ship.cell, (0, 0))
        step = max(1, int(ship.engine - H.pull(wide.blackhole_mass, d, wide.k_g, wide.falloff)))
        submit(state, wide, f"move +{step} +{step}")
        state, _ = run_turns(state, wide, 1)
        cells.append(step)
        lefts.append(state.hazard_dict(ship)["turns_left"])
    assert cells == [2, 2, 3] and lefts == [6, 8, 12] and ship.cell == (11, 11)
    state, _ = run_turns(state, wide, 7)
    assert not state.game_over and state.hazard_dict(ship)["turns_left"] == 15 - 10


def test_neutron_star_destroys_without_drain(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    place_hole(small_state, ship, (5, 5), NEUTRON_STAR, small_cfg.neutron_mass)
    put(small_state, ship, (6, 5))
    small_state, _ = run_turns(small_state, small_cfg, 2)
    hz = small_state.hazard_dict(ship)
    assert hz["kind"] == "neutron_star" and hz["pull"] == pytest.approx(2.828, abs=0.005) and hz["turns_left"] == 1
    assert ship.shield_current == ship.shield_max
    small_state, _ = run_turns(small_state, small_cfg, 1)
    assert small_state.game_over_turn == 3 and small_state.last_stats["destroyed"][0]["cause"] == "neutron_star"


def test_wormhole_crossing_keeps_the_ship_alive(small_state: GameState, small_cfg: GameConfig) -> None:
    """HZ-05 / D-102: emerge at the free cell nearest the partner, counter reset, inside its range."""
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    here = (ship.galaxy, ship.sector)
    other = next(k for k in sorted(small_state.world.sectors) if k != here)
    psector = small_state.world.sectors[other]
    for cell in list(psector.grid.coords()):
        psector.grid.remove(*cell)
    partner_cell = (4, 4)
    psector.grid.set(4, 4, Hole(WORMHOLE, partner_cell, small_cfg.blackhole_mass, (here[0], here[1], (5, 5))))
    place_hole(small_state, ship, (5, 5), WORMHOLE, small_cfg.blackhole_mass, (other[0], other[1], partner_cell))
    put(small_state, ship, (6, 5))
    small_state, events = run_turns(small_state, small_cfg, 1)
    assert ship.id in small_state.ships and not small_state.game_over
    exit_d = small_cfg.wormhole_exit_distance   # CFG-62 / D-114: 2 with the defaults (was 'nearest free cell', D-102)
    assert (ship.galaxy, ship.sector) == other and chebyshev(ship.cell, partner_cell) == exit_d
    assert ship.cell == (2, 4), "the axis-aligned cell at the exit distance with the smallest x wins (D-113 ordering)"
    assert any("wormhole" in s and "emerged" in s for s in texts(events, "TP-03"))
    hz = small_state.hazard_dict(ship)
    assert hz is not None and hz["kind"] == "wormhole" and hz["distance"] == exit_d
    assert small_state.last_stats["wormhole_crossings"] == 1 and small_state.last_stats["destroyed"] == []
    assert small_state.is_visited(*other)
    # a plain black hole at the same distance destroys (D-102)
    s2 = new_game(small_state.seed, small_cfg)
    ship2 = s2.active_ship()
    clear_sector(s2, ship2)
    place_hole(s2, ship2, (5, 5), BLACK_HOLE, small_cfg.blackhole_mass)
    put(s2, ship2, (6, 5))
    s2, _ = run_turns(s2, small_cfg, 1)
    assert s2.game_over_turn == 1


# --------------------------------------------------------------------------- destruction of other ships (FL-04, OUT-11, D-105)

def test_ai_ship_destroyed_removes_an_empty_captain(small_state: GameState, small_cfg: GameConfig) -> None:
    ai_cap = next(c for c in small_state.captains.values() if not c.is_player)
    ai_ship = small_state.ships[ai_cap.active_ship]
    clear_sector(small_state, ai_ship)
    place_hole(small_state, ai_ship, (0, 0), BLACK_HOLE, small_cfg.blackhole_mass)
    ai_ship.position = (ai_ship.galaxy, ai_ship.sector, (1, 1))
    captains_before = len(small_state.captains)
    small_state, _ = run_turns(small_state, small_cfg, 1)
    assert ai_ship.id not in small_state.ships and ai_cap.id not in small_state.captains, "D-105: no orphaned captain"
    assert len(small_state.captains) == captains_before - 1 and not small_state.game_over
    snap = small_state.snapshot()
    owners = {s["captain"] for s in snap["ships"].values()}
    assert all(cid in owners for cid in snap["captains"] if cid != snap["player_id"])
    assert small_state.last_stats["destroyed"][0]["captain"] == ai_cap.id


def test_losing_a_non_active_ship_is_not_game_over(small_state: GameState, small_cfg: GameConfig) -> None:
    cap = small_state.player
    ship = small_state.active_ship()
    other_key = next(k for k in sorted(small_state.world.sectors) if k != (ship.galaxy, ship.sector))
    sector = small_state.world.sectors[other_key]
    for cell in list(sector.grid.coords()):
        sector.grid.remove(*cell)
    sector.grid.set(0, 0, Hole(BLACK_HOLE, (0, 0), small_cfg.blackhole_mass))
    second = Ship.of_class(small_state.new_ship_id(), "Second Tern", small_cfg.ships["corsair"], cap.id, (other_key[0], other_key[1], (1, 1)))
    small_state.ships[second.id] = second
    cap.ships.append(second.id)
    small_state, events = run_turns(small_state, small_cfg, 1)
    assert second.id not in small_state.ships and cap.ships == [ship.id] and not small_state.game_over
    assert any("lost from your fleet" in s for s in texts(events, "TP-03")), "OUT-11"


# --------------------------------------------------------------------------- warp (CMD-07, MV-02, MV-03, MV-05, HZ-10, D-101, D-109)

def test_warp_arrives_at_the_free_cell_nearest_the_centre(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    here = (ship.galaxy, ship.sector)
    dest = next(k for k in sorted(small_state.world.sectors) if k[0] == here[0] and k != here)
    (gx, gy), (sx, sy) = dest
    res = submit(small_state, small_cfg, f"warp {gx} {gy} {sx} {sy}")
    assert "warp queued" in res.text
    small_state, events = run_turns(small_state, small_cfg, 1)
    assert (ship.galaxy, ship.sector) == dest
    blocked = {t.cell for t in small_state.world.sectors[dest].terrain()}
    cx, cy = (small_cfg.sector_size[0] - 1) / 2, (small_cfg.sector_size[1] - 1) / 2
    assert ship.cell not in blocked and max(abs(ship.cell[0] - cx), abs(ship.cell[1] - cy)) <= 1.5
    w = small_state.last_stats["warps"]
    assert w and w[0]["from"] == [list(here[0]), list(here[1])] and w[0]["to"] == [list(dest[0]), list(dest[1])]
    assert w[0]["factor"] == ship.warp and w[0]["effective"] == ship.warp
    assert any("warped" in s for s in texts(events, "TP-02")) and small_state.is_visited(*dest)
    assert O.pending_count(small_state, ship.id) == 0, "D-101: a warp is a single-turn order"


def test_warp_rejections_name_cmd_07(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    here = (ship.galaxy, ship.sector)
    dest = next(k for k in sorted(small_state.world.sectors) if k != here)
    (gx, gy), (sx, sy) = dest
    uw, uh = small_cfg.universe_size
    gw, gh = small_cfg.galaxy_size
    fast = O.submit(small_state, parse(f"warp {gx} {gy} {sx} {sy} {ship.warp + 1}", ship.id), small_cfg)
    assert isinstance(fast, O.Rejected) and "warpcore_power" in fast.reason and "CMD-07" in fast.reason
    outside = O.submit(small_state, parse(f"warp {uw} {uh} 0 0", ship.id), small_cfg)
    assert isinstance(outside, O.Rejected) and "outside the universe" in outside.reason
    void = None
    for g in small_state.world.galaxy_coords():
        for sx_ in range(gw):
            for sy_ in range(gh):
                if small_state.world.sector(g, (sx_, sy_)) is None:
                    void = (g, (sx_, sy_))
                    break
            if void:
                break
        if void:
            break
    if void is not None:
        rej = O.submit(small_state, parse(f"warp {void[0][0]} {void[0][1]} {void[1][0]} {void[1][1]}", ship.id), small_cfg)
        assert isinstance(rej, O.Rejected) and "Void" in rej.reason
    same = O.submit(small_state, parse(f"warp {here[0][0]} {here[0][1]} {here[1][0]} {here[1][1]}", ship.id), small_cfg)
    assert isinstance(same, O.Rejected)
    # beyond reach: the farthest sector in a 2 x 2 universe of 3 x 3 galaxies is > 3.07 away for a Kestrel (warp 3)
    from starbour.engine import movement as MV
    far = max(sorted(small_state.world.sectors), key=lambda k: MV.warp_distance(here[0], here[1], k[0], k[1], small_cfg.galaxy_size))
    d = MV.warp_distance(here[0], here[1], far[0], far[1], small_cfg.galaxy_size)
    reach = MV.warp_reach(ship.warp, small_cfg.warp_curve_base, small_cfg.warp_curve_exponent)
    if d > reach:
        rej = O.submit(small_state, parse(f"warp {far[0][0]} {far[0][1]} {far[1][0]} {far[1][1]}", ship.id), small_cfg)
        assert isinstance(rej, O.Rejected) and f"{reach:.2f}" in rej.reason and "CMD-07" in rej.reason, "the message states the reach"


def test_warp_under_pull_fails_at_resolution(small_state: GameState, small_cfg: GameConfig) -> None:
    """HZ-10 / MV-03 / D-107 (6): effective <= 0 is accepted and fails with a hazard event mentioning 'warp'."""
    ship = small_state.active_ship()
    clear_sector(small_state, ship)
    place_hole(small_state, ship, (0, 0), BLACK_HOLE, small_cfg.blackhole_mass)
    put(small_state, ship, (2, 2))
    here = (ship.galaxy, ship.sector)
    dest = next(k for k in sorted(small_state.world.sectors) if k != here and k[0] == here[0])
    (gx, gy), (sx, sy) = dest
    res = submit(small_state, small_cfg, f"warp {gx} {gy} {sx} {sy}")
    assert "will fail" in res.text
    submit(small_state, small_cfg, "move +1 +1")     # D-109: the warp wins over the move
    small_state, events = run_turns(small_state, small_cfg, 1)
    assert (ship.galaxy, ship.sector) == here and ship.cell == (2, 2)
    assert any("warp" in s and "failed" in s for s in texts(events, "TP-02"))
    assert small_state.last_stats["warps"] == [] and small_state.move_remaining(ship.id) is None
    # 0 < effective < 1 from distance 3: rejected at submission (reach 0.50)
    put(small_state, ship, (3, 3))
    rej = O.submit(small_state, parse(f"warp {gx} {gy} {sx} {sy}", ship.id), small_cfg)
    assert isinstance(rej, O.Rejected) and "reach 0.50" in rej.reason and "HZ-10" in rej.reason


# --------------------------------------------------------------------------- AI (D-103, D-105, OV-10)

def test_ai_moves_directly_away_from_a_hole(small_state: GameState, small_cfg: GameConfig) -> None:
    ai_cap = next(c for c in small_state.captains.values() if not c.is_player)
    ai_ship = small_state.ships[ai_cap.active_ship]
    clear_sector(small_state, ai_ship)
    place_hole(small_state, ai_ship, (0, 0), BLACK_HOLE, small_cfg.blackhole_mass)
    ai_ship.position = (ai_ship.galaxy, ai_ship.sector, (4, 4))
    orders = ai.decide(ai_cap, small_state, game_rng(1), small_cfg)
    assert len(orders) == 1
    tx, ty = int(orders[0].args[0]), int(orders[0].args[1])
    pull = H.pull(small_cfg.blackhole_mass, 4, small_cfg.k_g, small_cfg.falloff)
    step = int(ai_ship.engine - pull)
    assert (tx, ty) == (4 + step, 4 + step), "directly away at full engine power (D-103)"
    ai_ship.position = (ai_ship.galaxy, ai_ship.sector, (1, 1))     # pull 7.07 holds even a Corsair (engine 6)
    assert ai.decide(ai_cap, small_state, game_rng(1), small_cfg) == [], "held ships get no order (HZ-04)"


def test_ai_never_ends_on_terrain_and_never_dies_by_a_star(small_cfg: GameConfig) -> None:
    import dataclasses
    busy = dataclasses.replace(small_cfg, hole_pct=0)   # stars and planets everywhere a hole is not
    state = new_game(21, busy)
    rng = game_rng(21)
    for _ in range(30):
        state, _ = resolve_turn(state, busy, rng, ai_player=True)
        for sid, ship in state.ships.items():
            sector = state.sector_of(ship)
            here = sector.terrain_at(ship.cell)
            assert here is None or here.kind == "planet", f"turn {state.turn_no}: {sid} ended on terrain at {ship.cell}"   # M4: docking at a planet is allowed (PR-04, D-144)
        assert all(d["cause"] != "star" for d in state.last_stats["destroyed"])
        assert state.last_stats["orders_executed"]["ai"] >= 1
    snap = state.snapshot()
    owners = {s["captain"] for s in snap["ships"].values()}
    assert all(cid in owners for cid in snap["captains"] if cid != snap["player_id"])
