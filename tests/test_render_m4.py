"""The M4 renders - cargo, the planet panel, the battle numbers, item glyphs, help fire, the T4 heartbeat fields (PNL-04, SYM-08, SYM-09, UX-09, C4, C5, C11)."""
from __future__ import annotations

import copy

from starbour.config import GameConfig
from starbour.engine import combat as C
from starbour.engine.state import GameState
from starbour.terminals import render_command, render_grid, render_status
from tests.test_render import plain


def _snap(small_state: GameState) -> dict:
    return small_state.snapshot()


def test_cargo_panel_lists_units_and_values(small_state: GameState, small_cfg: GameConfig) -> None:
    small_state.player.items = {"iridium": 2, "orichalcum": 1}
    text = plain(render_status.render(_snap(small_state), None, small_cfg, 120))
    assert "iridium" in text and "2 units" in text and str(small_cfg.elements["iridium"].value * 2) in text
    assert "orichalcum" in text and "special" in text
    assert render_status.cargo_stacks(_snap(small_state)) == 2


def test_planet_panel_and_heartbeat_docked(small_state: GameState, small_cfg: GameConfig) -> None:
    from tests.test_economy import dock
    ship, planet = dock(small_state, 50)
    snap = _snap(small_state)
    text = plain(render_status.render(snap, None, small_cfg, 120))
    assert planet.name in text and planet.president in text and "trades" in text.lower() and "Food" in text
    assert render_status.docked_planet_id(snap) == planet.id
    small_state.player.respect = small_cfg.planet_refuse_respect - 8
    text = plain(render_status.render(_snap(small_state), None, small_cfg, 120))
    assert "refuses" in text and str(2 * small_state.player.respect - 100) in text


def test_battle_panel_shows_shield_percent_and_peace_threshold(small_state: GameState, small_cfg: GameConfig) -> None:
    from tests.test_combat import arena, spawn, submit
    ship = arena(small_state)
    cap, foe = spawn(small_state, "corsair", (6, 5))
    foe.shield_current = 37.0
    foe.shield_max = 80.0
    cap.rep_seen_turn = 1
    submit(small_state, small_cfg, f"fire phaser {foe.id}")
    snap = _snap(small_state)
    text = plain(render_status.render(snap, None, small_cfg, 120))
    assert "46 %" in text, "shields as a percentage (UX-09)"
    thr = C.ai_peace_threshold(small_state.player.fear, small_cfg)
    assert f"{thr:g}" in text, "the scanned enemy's peace threshold (D-138)"


def test_grid_draws_items_only_when_revealed_and_colours_refusing_planets(small_state: GameState, small_cfg: GameConfig) -> None:
    from starbour.entities.item import Item
    ship = small_state.active_ship()
    small_state.player.respect = small_cfg.planet_refuse_respect + 30     # trades: the default start of 10 already refuses (EF-04)
    sector = small_state.sector_of(ship)
    sector.items = [Item("i-9003", "iridium", "normal", (ship.cell[0], (ship.cell[1] + 2) % small_cfg.sector_size[1]), units=1)]
    plain_snap = _snap(small_state)
    hidden = copy.deepcopy(plain_snap)
    sector.items[0].revealed = True
    revealed = _snap(small_state)
    g = render_grid.render
    assert plain(g(hidden, None, small_cfg, 120)) != plain(g(revealed, None, small_cfg, 120))
    small_state.player.respect = small_cfg.planet_refuse_respect - 1
    refusing = _snap(small_state)
    assert plain(g(revealed, None, small_cfg, 120)) == plain(g(refusing, None, small_cfg, 120)), "the glyph is the same; the colour differs"
    from tests.test_render import styled
    assert styled(g(revealed, None, small_cfg, 120)) != styled(g(refusing, None, small_cfg, 120))


def test_help_fire_prints_the_ini_numbers(small_cfg: GameConfig) -> None:
    text = "\n".join(t.plain for t in render_command.help_text("fire", small_cfg))
    start = small_cfg.ships[small_cfg.starting_class]
    pct = int(round(C.torpedo_hit_p(start.engine, small_cfg) * 100))
    assert f"{small_cfg.torpedo_damage:g}" in text and f"{pct} %" in text
    for d in range(0, small_cfg.phaser_range + 1):
        assert f"{C.phaser_damage(start.phasers, small_cfg.phaser_damage_per_unit, d):g}" in text
