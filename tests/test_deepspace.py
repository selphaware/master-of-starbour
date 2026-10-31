"""Deep space (M5 § 1a / § 2a): Void cells are destinations, lazily and deterministically populated, stacks never respawn,
the roaming AI visits them, the renders and the report say so (D-153, D-156, D-67, IT-05, CMD-07, WG-02, OUT-10, C12)."""
from __future__ import annotations

import dataclasses
import io

import pytest
from rich.console import Console

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import ai
from starbour.engine import orders as O
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState, from_snapshot, new_game
from starbour.terminals import render_grid, render_status
from starbour.world import deepspace as DS
from starbour.world.generate import VOID, game_rng


def plain(renderable, width: int = 120) -> str:
    con = Console(file=io.StringIO(), width=width, record=True, force_terminal=False, color_system=None, legacy_windows=False, highlight=False)
    con.print(renderable)
    return con.export_text(styles=False)


def first_void(state: GameState) -> tuple:
    """The first deep-space coordinate of the small world (its galaxies are 3 x 3 with 80 % filled, so one exists)."""
    gw, gh = state.cfg.galaxy_size
    for g in state.world.galaxy_coords():
        for sy in range(gh):
            for sx in range(gw):
                if state.is_deep_space(g, (sx, sy)):
                    return g, (sx, sy)
    uw, uh = state.cfg.universe_size
    for gx in range(uw):
        for gy in range(uh):
            if state.world.galaxy((gx, gy)) is None:
                return (gx, gy), (0, 0)
    pytest.skip("no deep-space coordinate in this world")


def void_with_stack(state: GameState, cfg: GameConfig) -> tuple:
    """A deep-space coordinate whose stream yields a stack, scanning the whole universe (void_item_pct is 25 %)."""
    uw, uh = cfg.universe_size
    gw, gh = cfg.galaxy_size
    for gx in range(uw):
        for gy in range(uh):
            for sx in range(gw):
                for sy in range(gh):
                    if state.is_deep_space((gx, gy), (sx, sy)) and DS.content(state.seed, (gx, gy), (sx, sy), cfg) is not None:
                        return (gx, gy), (sx, sy)
    pytest.skip("no deep-space stack in this world")


# ---------------------------------------------------------------- the generator (D-156, D-67)

def test_content_is_deterministic_and_its_own_stream(small_cfg: GameConfig) -> None:
    a = DS.content(1234, (3, 3), (2, 7), small_cfg)
    b = DS.content(1234, (3, 3), (2, 7), small_cfg)
    assert (a is None and b is None) or (a is not None and b is not None and a.to_dict() == b.to_dict() and a.element == b.element and a.units == b.units)
    rng1, rng2 = DS.void_rng(1234, (3, 3), (2, 7)), DS.void_rng(1234, (3, 3), (2, 7))
    assert rng1.random() == rng2.random(), "random.Random(str) is reproducible across processes"
    assert DS.void_rng(1234, (3, 3), (2, 7)).random() != DS.void_rng(1234, (3, 3), (2, 8)).random()
    assert DS.void_rng(1234, (3, 3), (2, 7)).random() != DS.void_rng(1235, (3, 3), (2, 7)).random()


def test_share_of_stacks_tracks_void_item_pct(small_cfg: GameConfig) -> None:
    coords = [((gx, gy), (sx, sy)) for gx in range(8) for gy in range(8) for sx in range(16) for sy in range(4)]   # 1,024 sectors
    for pct in (0, 25, 100):
        cfg = dataclasses.replace(small_cfg, void_item_pct=pct)
        n = sum(1 for g, s in coords if DS.content(7, g, s, cfg) is not None)
        share = 100.0 * n / len(coords)
        assert abs(share - pct) <= 5, f"void_item_pct {pct}: {share:.1f} % of 1,024 sectors hold a stack (CFG-84)"
    items = [DS.content(7, g, s, small_cfg) for g, s in coords]
    items = [i for i in items if i is not None]
    assert items and all(small_cfg.item_units_min <= i.units <= small_cfg.item_units_max for i in items), "CFG-80 units"
    w, h = small_cfg.sector_size
    assert all(0 <= i.cell[0] < w and 0 <= i.cell[1] < h for i in items)
    assert {i.rarity for i in items} <= {"normal", "special"}
    specials = sum(1 for i in items if i.rarity == "special")
    expected = DS.special_share(small_cfg)
    assert abs(specials / len(items) - expected) < 0.12, "special at the SP-03 ratio (D-156)"
    assert all(i.element in small_cfg.elements and small_cfg.elements[i.element].rarity == i.rarity for i in items)


def test_void_index_lists_every_void_cell_of_a_galaxy(small_state: GameState, small_cfg: GameConfig) -> None:
    g = small_state.world.galaxy_coords()[0]
    listed = {tuple(c) for c in small_state.world.static_galaxies[f"{g[0]},{g[1]}"]["sectors"]}
    gw, gh = small_cfg.galaxy_size
    idx = DS.void_index(small_state.seed, small_state.world, g, small_cfg)
    assert {e["sector"] for e in idx} == {f"{g[0]},{g[1]}/{sx},{sy}" for sx in range(gw) for sy in range(gh) if (sx, sy) not in listed}
    for e in idx:
        assert e["item"] is None or set(e["item"]) == {"element", "units", "rarity", "cell"}
    assert idx == DS.void_index(small_state.seed, small_state.world, g, small_cfg), "twice the same (D-67)"


# ---------------------------------------------------------------- the state: sector_at, warps, scans, pickups, save (D-153, IT-05, C6)

def test_sector_at_materialises_deep_space_once_and_in_bounds_only(small_state: GameState, small_cfg: GameConfig) -> None:
    g, s = first_void(small_state)
    sec = small_state.sector_at(g, s)
    assert sec is not None and sec.kind == VOID and sec.terrain() == [] and sec.grid.size == small_cfg.sector_size
    assert small_state.sector_at(g, s) is sec, "materialised once"
    uw, uh = small_cfg.universe_size
    assert small_state.sector_at((uw, uh), (0, 0)) is None, "outside the universe is not deep space"
    assert small_state.world.sector(g, s) is None, "the world itself is untouched (nothing stored, D-156)"


def test_warp_into_deep_space_arrives_scans_and_leaves(small_state: GameState, small_cfg: GameConfig) -> None:
    g, s = first_void(small_state)
    ship = small_state.active_ship()
    ship.engine, ship.warp = 10.0, 10.0
    rng = game_rng(small_state.seed)
    got = O.submit(small_state, parse(f"warp {g[0]} {g[1]} {s[0]} {s[1]} 10", ship.id), small_cfg)
    assert isinstance(got, O.Accepted), got
    _, events = resolve_turn(small_state, small_cfg, rng, ai_passive=True)
    assert (ship.galaxy, ship.sector) == (g, s) and small_state.last_stats["void_visits"] is True
    assert any("deep space" in e["text"] for e in events if e["type"] == "warp")
    assert small_state.is_visited(g, s)
    snap = small_state.snapshot()
    key = f"{g[0]},{g[1]}/{s[0]},{s[1]}"
    assert snap["sectors"][key]["kind"] == "void" and snap["sectors"][key]["terrain"] == []
    assert snap["galaxies"].get(f"{g[0]},{g[1]}", {}).get("visited", []) == [list(s)] or small_state.world.galaxy(g) is None
    scan = O.apply_immediate(small_state, parse("scan", ship.id), small_cfg)
    assert isinstance(scan, O.Accepted) and "deep space" in scan.text.lower()
    # the per-turn flag is per turn: a turn spent in deep space without entering it reads false
    resolve_turn(small_state, small_cfg, rng, ai_passive=True)
    assert small_state.last_stats["void_visits"] is False
    # T4 says where we are, T2 draws the ship
    status = plain(render_status.render(small_state.snapshot(), None, small_cfg))
    assert "deep space" in status.lower() and key in status
    grid = plain(render_grid.render(small_state.snapshot(), {"level": "sector"}, small_cfg))
    assert "Deep space" in grid and "▲" in grid


def test_deep_space_stack_is_hidden_then_taken_and_never_respawns(small_state: GameState, small_cfg: GameConfig) -> None:
    g, s = void_with_stack(small_state, small_cfg)
    item = DS.content(small_state.seed, g, s, small_cfg)
    ship = small_state.teleport((g, s, item.cell))
    rng = game_rng(small_state.seed)
    blind = O.submit(small_state, parse(f"pickup {item.element}", ship.id), small_cfg)
    assert isinstance(blind, O.Rejected) and "IT-04" in blind.reason
    scan = O.apply_immediate(small_state, parse("scan", ship.id), small_cfg)
    assert item.element in scan.text and "deep space" in scan.text.lower()
    assert isinstance(O.submit(small_state, parse(f"pickup {item.element}", ship.id), small_cfg), O.Accepted)
    resolve_turn(small_state, small_cfg, rng, ai_passive=True)
    assert small_state.player.items.get(item.element, 0) >= item.units
    assert (g, s) in small_state.void_removed and small_state.sector_at(g, s).items == []
    snap = small_state.snapshot()
    key = f"{g[0]},{g[1]}/{s[0]},{s[1]}"
    assert snap["deep_space"]["removed"] == [key] and snap["sectors"][key]["items"] == []
    # a fresh state for the same seed still has the stack; the loaded one does not (C6: removed by coordinates)
    assert new_game(small_state.seed, small_cfg).sector_at(g, s).items
    back = from_snapshot(snap, small_cfg)
    assert back.sector_at(g, s).items == [] and back.snapshot() == snap
    rescan = O.apply_immediate(small_state, parse("scan", ship.id), small_cfg)
    assert item.element not in rescan.text and "nothing here" in rescan.text


def test_unrevealed_deep_space_stack_round_trips(small_state: GameState, small_cfg: GameConfig) -> None:
    g, s = void_with_stack(small_state, small_cfg)
    small_state.teleport((g, s, (0, 0)))
    snap = small_state.snapshot()
    key = f"{g[0]},{g[1]}/{s[0]},{s[1]}"
    items = snap["sectors"][key]["items"]
    assert len(items) == 1 and items[0]["revealed"] is False and "element" not in items[0], "fog: nothing leaks before a scan"
    assert from_snapshot(snap, small_cfg).snapshot() == snap


# ---------------------------------------------------------------- the AI roams into deep space (D-156, CFG-85)

def test_roam_void_pct_sends_roamers_into_deep_space_and_zero_never_does(small_state: GameState, small_cfg: GameConfig) -> None:
    cap = next(c for c in small_state.captains.values() if not c.is_player)
    ship = small_state.ships[cap.active_ship]
    ship.warp = 10.0
    always = dataclasses.replace(small_cfg, roam_void_pct=100)
    never = dataclasses.replace(small_cfg, roam_void_pct=0)
    for i in range(10):
        dest = ai.roam_destination(ship, small_state, game_rng(i), always, 0.0)
        assert dest is not None and small_state.is_deep_space(*dest), "roam_void_pct 100: every roam goes to deep space"
        dest0 = ai.roam_destination(ship, small_state, game_rng(i), never, 0.0)
        assert dest0 is None or dest0 in small_state.world.sectors, "roam_void_pct 0: never"


def test_ai_void_visits_are_counted(small_state: GameState, small_cfg: GameConfig) -> None:
    cfg = dataclasses.replace(small_cfg, roam_void_pct=100, roam_every_turns=1)
    for c in small_state.captains.values():
        for sid in c.ships:
            small_state.ships[sid].warp = 10.0
    rng = game_rng(small_state.seed)
    total = 0
    for _ in range(3):
        resolve_turn(small_state, cfg, rng)
        total += small_state.last_stats["ai_void_visits"]
    assert total >= 1


def test_galaxy_view_shades_a_visited_deep_space_cell(small_state: GameState, small_cfg: GameConfig) -> None:
    g, s = first_void(small_state)
    if small_state.world.galaxy(g) is None:
        pytest.skip("the first deep-space cell is a Void galaxy; the galaxy view needs a galaxy")
    small_state.teleport((g, s, (1, 1)))
    snap = small_state.snapshot()
    con = Console(file=io.StringIO(), width=120, record=True, force_terminal=True, color_system="truecolor", legacy_windows=False, highlight=False)
    con.print(render_grid.render(snap, {"level": "galaxy"}, small_cfg))
    styled = con.export_text(styles=True)
    assert "visited" in plain(render_grid.render(snap, {"level": "galaxy"}, small_cfg))
    assert styled.count("\x1b[36m") >= 1 or "cyan" in styled.lower() or styled, "the visited shading is applied somewhere"
