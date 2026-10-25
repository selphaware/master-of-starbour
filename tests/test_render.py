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


# --------------------------------------------------------------------------- M2: hazard overlay, PNL-06, PNL-07, banners (GRD-03, OUT-03, OUT-16)

def styled(renderable, width: int = 120) -> str:
    con = Console(file=io.StringIO(), width=width, record=True, force_terminal=True, color_system="truecolor", legacy_windows=False, highlight=False)
    con.print(renderable)
    return con.export_text(styles=True)


def hazard_variants(snap: dict) -> dict:
    """A hole in the sector with player.hazard set; the same sector without; a star under the ship; game over."""
    base = copy.deepcopy(snap)
    cap = base["captains"][base["player_id"]]
    sid = cap["active_ship"]
    ship = base["ships"][sid]
    (gx, gy), (sx, sy), _ = ship["position"]
    ship["position"] = [[gx, gy], [sx, sy], [6, 5]]
    ship["hazard"] = None
    ship["move_remaining"] = None
    ship["orders"] = []
    base["game_over"] = False
    key = f"{gx},{gy}/{sx},{sy}"
    sector = base.setdefault("sectors", {}).setdefault(key, {"terrain": [], "items": []})
    sector["terrain"] = [t for t in sector.get("terrain") or [] if t.get("kind") not in ("black_hole", "neutron_star", "wormhole")
                         and list(t.get("cell") or []) not in ([6, 5], [1, 1])]
    for other in base["ships"].values():
        if other is not ship and list(other.get("position", [[], [], []])[2]) in ([6, 5], [1, 1]):
            other["position"][2] = [7, 6]
    hole = copy.deepcopy(base)
    hole["sectors"][key]["terrain"].append({"kind": "black_hole", "cell": [1, 1], "mass": 20})
    hole["ships"][sid]["hazard"] = {"kind": "black_hole", "cell": [1, 1], "distance": 5, "pull": 1.361, "turns_left": 4, "drain": 0}
    star = copy.deepcopy(base)
    star["sectors"][key]["terrain"].append({"kind": "star", "cell": [6, 5], "colour": "yellow", "bucket": "medium", "mass": 1.0})
    star["ships"][sid]["hazard"] = {"kind": "star", "cell": [6, 5], "distance": 0, "pull": 1.0, "turns_left": 4, "drain": 10}
    star["ships"][sid]["shield_current"] = 30.0
    over = copy.deepcopy(star)
    over["game_over"] = True
    over["game_over_turn"] = over["turn_no"]
    orders = copy.deepcopy(base)
    orders["ships"][sid]["move_remaining"] = [0, 6]
    orders["ships"][sid]["orders"] = [{"command": "move", "args": ["+0", "+6"], "persisting": True}, {"command": "warp", "args": ["1", "1", "2", "2", "3"]}]
    orders["ships"][sid]["orders_queued"] = 2
    return {"without": base, "hole": hole, "star": star, "over": over, "orders": orders, "sid": sid}


def test_grid_hazard_overlay_and_countdown(snap: dict, gcfg: GameConfig) -> None:
    """GRD-03: cells in pull range are tinted; turns_left sits beside the ship glyph; no label without a hazard."""
    v = hazard_variants(snap)
    glyph = P.glyphs(gcfg)["ship_active"]
    with_, without = plain(render_grid.render(v["hole"], None, gcfg, 120)), plain(render_grid.render(v["without"], None, gcfg, 120))
    rows_w, rows_wo = with_.splitlines(), without.splitlines()
    ship_row = next(i for i, ln in enumerate(rows_w) if ln.startswith(" 5 ") and glyph in ln)
    col = rows_w[ship_row].index(glyph)
    assert rows_w[ship_row][col + 1] == "4", "the countdown follows the glyph (GRD-03)"
    assert rows_wo[ship_row][col + 1] == " "
    assert "4 turn(s) until pulled in" in with_ and "pull 1.36" in with_
    sw, swo = styled(render_grid.render(v["hole"], None, gcfg, 120)).splitlines(), styled(render_grid.render(v["without"], None, gcfg, 120)).splitlines()
    identical_text_rows = [i for i in range(min(len(rows_w), len(rows_wo))) if rows_w[i] == rows_wo[i] and rows_w[i][:3].strip().isdigit()]
    assert identical_text_rows and any(sw[i] != swo[i] for i in identical_text_rows), "a tinted row has the same text but other styles"
    star_t = plain(render_grid.render(v["star"], None, gcfg, 120))
    assert "shields drain 10 per turn" in star_t
    assert "GAME OVER" in plain(render_grid.render(v["over"], None, gcfg, 120)) and "GAME OVER" not in with_


def test_status_hazard_orders_and_banner(snap: dict, gcfg: GameConfig) -> None:
    """PNL-06 shows pull, countdown and drain only when relevant; PNL-07 lists orders; OUT-16 banner on game over."""
    v = hazard_variants(snap)
    star_t, wo_t, over_t = (plain(render_status.render(v[k], None, gcfg, 120)) for k in ("star", "without", "over"))
    assert "Hazard" in star_t and "Hazard" not in wo_t
    new_lines = "\n".join(ln for ln in star_t.splitlines() if ln not in set(wo_t.splitlines()))
    assert "Pull" in new_lines and " 1 " in new_lines + " " and "4 turn(s) until pulled in" in new_lines and "10 per turn" in new_lines
    assert "GAME OVER" in over_t and "GAME OVER" not in wo_t and "GAME OVER" not in star_t
    orders_t = plain(render_status.render(v["orders"], None, gcfg, 120))
    i_move, i_warp = orders_t.find("move +0 +6"), orders_t.find("warp 1 1 2 2 3")
    assert 0 <= i_move < i_warp, "PNL-07 lists the queued orders in order"
    step = int(v["orders"]["ships"][v["sid"]]["engine"])
    assert f"{step} cells/turn" in orders_t and "persisting" in orders_t and "Orders (2)" in orders_t
    assert "no orders queued" in wo_t


def test_output_game_over_block(snap: dict, gcfg: GameConfig) -> None:
    """OUT-16: a turn whose snapshot says game_over ends with the block; game_over_lines renders the MSG-09 payload."""
    v = hazard_variants(snap)
    ev = {"phase": "TP-03", "type": "destroyed", "severity": "alert", "text": "Lantern destroyed: pulled into the black hole at 1,1 - game over", "ids": ["OUT-16"]}
    text = plain(render_output.render({"turn_no": 9, "snapshot": v["over"], "events": [ev]}, None, gcfg, 120))
    assert "black hole" in text and "GAME OVER" in text and "'new'" in text
    plain_turn = plain(render_output.render({"turn_no": 9, "snapshot": v["without"], "events": [ev]}, None, gcfg, 120))
    assert "GAME OVER" not in plain_turn
    block = "\n".join(plain(t) for t in render_output.game_over_lines({"turn_no": 57, "cause": "pulled into the star at 5,5", "summary": {"turns": 57, "fear": 10}}))
    assert "57" in block and "pulled into the star" in block and "fear 10" in block


def test_command_shields_lines(snap: dict, gcfg: GameConfig) -> None:
    """CMD-09: shields current / max from the snapshot, with the drain when in a star's cell."""
    v = hazard_variants(snap)
    text = plain(render_command.shields_lines(v["star"])[0])
    assert "shields 30 / " in text and "draining 10 per turn" in text
    assert "shields" in plain(render_command.shields_lines(v["without"])[0])
