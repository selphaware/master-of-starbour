"""The four renderers, headless - C4 properties for unicode and ascii glyph sets (D-65, D-78, D-81, GRD-01, GRD-06)."""
from __future__ import annotations

import copy
import dataclasses
import io

import pytest
from rich.console import Console

from starbour.config import GameConfig
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState
from starbour.terminals import palette as P
from starbour.terminals import render_command, render_grid, render_output, render_status
from starbour.terminals.render_grid import CELL_W
from starbour.world.generate import game_rng


def plain(renderable, width: int = 120) -> str:
    con = Console(file=io.StringIO(), width=width, record=True, force_terminal=False, color_system=None, legacy_windows=False, highlight=False)
    con.print(renderable)
    return con.export_text(styles=False)


def successor(snapshot: dict, dx: int, dy: int) -> dict:
    s = copy.deepcopy(snapshot)
    s["turn_no"] += 1
    cap = s["captains"][s["player_id"]]
    cap["food"] -= 1
    cap["water"] -= 1
    ship = s["ships"][cap["active_ship"]]
    (gx, gy), (sx, sy), (cx, cy) = ship["position"]
    ship["position"] = [[gx, gy], [sx, sy], [cx + dx, cy + dy]]
    return s


@pytest.fixture(params=["unicode", "ascii"])
def gcfg(request, small_cfg: GameConfig) -> GameConfig:
    return dataclasses.replace(small_cfg, glyph_set=request.param)


@pytest.fixture
def snap(small_state: GameState, small_cfg: GameConfig) -> dict:
    rng = game_rng(small_state.seed)
    resolve_turn(small_state, small_cfg, rng, ai_player=True)
    ship = small_state.active_ship()
    w, h = small_cfg.sector_size
    ship.position = (ship.galaxy, ship.sector, (min(5, w - 2), min(5, h - 2)))
    return small_state.snapshot()


def glyph_cells(text: str, glyph: str) -> set[tuple[int, int]]:
    out = set()
    for li, line in enumerate(text.splitlines()):
        i = line.find(glyph)
        while i >= 0:
            out.add((li, i))
            i = line.find(glyph, i + 1)
    return out


def test_grid_sector_glyph_tracks_position(snap: dict, gcfg: GameConfig) -> None:
    glyph = P.glyphs(gcfg)["ship_active"]
    view = {"level": "follow"}
    a = plain(render_grid.render(snap, view, gcfg, 120))
    bx = plain(render_grid.render(successor(snap, 1, 0), view, gcfg, 120))
    by = plain(render_grid.render(successor(snap, 0, 1), view, gcfg, 120))
    assert a != bx != by
    pa, pbx, pby = glyph_cells(a, glyph), glyph_cells(bx, glyph), glyph_cells(by, glyph)
    (old,), (new,) = (pa - pbx), (pbx - pa)
    assert old[0] == new[0] and new[1] - old[1] == CELL_W, "dx=1 moves the glyph one cell right on the same line (GRD-01)"
    (old,), (new,) = (pa - pby), (pby - pa)
    assert old[1] == new[1] and new[0] - old[0] == 1, "dy=1 moves the glyph one text line down"
    cap = snap["captains"][snap["player_id"]]
    cx, cy = snap["ships"][cap["active_ship"]]["position"][2]
    lines = a.splitlines()
    row = next(ln for ln in lines if ln.startswith(f"{cy:>2} "))
    assert row[3 + cx * CELL_W] == glyph, "the glyph sits at the labelled row and column"
    assert f"turn {snap['turn_no']}" in a and "Sector" in a


def test_grid_levels_render(snap: dict, gcfg: GameConfig) -> None:
    g = P.glyphs(gcfg)
    gal = plain(render_grid.render(snap, {"level": "galaxy"}, gcfg, 120))
    uni = plain(render_grid.render(snap, {"level": "universe"}, gcfg, 120))
    assert "Galaxy" in gal and g["here"] in gal
    assert "Universe" in uni and g["here"] in uni and g["galaxy"] in uni or len(snap["universe"]["galaxies"]) == 1
    assert gal != uni != plain(render_grid.render(snap, None, gcfg, 120))


def test_grid_viewport_clips_to_width(snap: dict, small_cfg: GameConfig) -> None:
    narrow = plain(render_grid.render(snap, None, small_cfg, 14), width=14)
    assert "viewport" in narrow and all(len(ln) <= 14 for ln in narrow.splitlines())


def test_status_shows_the_numbers(snap: dict, gcfg: GameConfig) -> None:
    a = plain(render_status.render(snap, None, gcfg, 120))
    b = plain(render_status.render(successor(snap, 1, 0), None, gcfg, 120))
    assert a != b
    cap = snap["captains"][snap["player_id"]]
    for value in (snap["turn_no"], cap["food"], cap["water"], cap["fear"], cap["respect"], snap["seed"], cap["gold"], cap["silver"]):
        assert str(value) in a
    assert cap["name"] in a and "Fleet" in a and "Cargo" in a and "Supplies" in a
    assert str(snap["turn_no"] + 1) in b and str(cap["food"] - 1) in b


def test_status_mutiny_countdown(snap: dict, small_cfg: GameConfig) -> None:
    s = copy.deepcopy(snap)
    cap = s["captains"][s["player_id"]]
    cap["food"] = 0
    cap["mutiny_countdown"] = 2
    text = plain(render_status.render(s, None, small_cfg, 120))
    assert "Mutiny" in text and "2 turn" in text


def test_output_lists_every_event_in_order(snap: dict, gcfg: GameConfig) -> None:
    events_a = [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "alpha moved to 3,3", "ids": ["MV-01"]},
                {"phase": "TP-06", "type": "upkeep", "severity": "info", "text": "beta upkeep applied", "ids": ["SUP-01"]}]
    events_b = [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "gamma another move", "ids": ["MV-01"]},
                {"phase": "TP-06", "type": "supplies", "severity": "warning", "text": "delta supplies low", "ids": ["SUP-02"]}]
    a = plain(render_output.render({"turn_no": 1, "snapshot": snap, "events": events_a}, None, gcfg, 120))
    b = plain(render_output.render({"turn_no": 2, "snapshot": snap, "events": events_b}, None, gcfg, 120))
    assert a != b
    for text, evs in ((a, events_a), (b, events_b)):
        last = -1
        for ev in evs:
            i = text.find(ev["text"])
            assert i > last, f"{ev['text']!r} missing or out of order"
            last = i
    assert "Movement" in a and "Upkeep" in a and "Turn 1" in a


def test_command_prompt_format(snap: dict, gcfg: GameConfig) -> None:
    a = plain(render_command.render(snap, None, gcfg, 120))
    b = plain(render_command.render(successor(snap, 1, 0), None, gcfg, 120))
    assert a != b
    cap = snap["captains"][snap["player_id"]]
    name = snap["ships"][cap["active_ship"]]["name"]
    assert f"T{snap['turn_no']}" in a and name in a and "0 orders" in a and a.rstrip().endswith(">")
    assert render_command.prompt_plain(snap, 2) == f"[T{snap['turn_no']} · {name} · 2 orders] > "
    status = "\n".join(plain(t) for t in render_command.status_lines(snap, gcfg))
    assert str(cap["food"]) in status and name in status
    assert name in plain(render_command.fleet_table(snap))
    assert any("move" in plain(t) for t in render_command.help_text())


def test_render_modules_are_pure() -> None:
    """C4: no zmq, time or input in the render modules."""
    import inspect
    for mod in (render_grid, render_output, render_status, render_command):
        src = inspect.getsource(mod)
        assert "import zmq" not in src and "import time" not in src and "input(" not in src
