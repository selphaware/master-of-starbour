"""The AI economy - restock trips, docking, supplies, automatic pickups, the respect start (D-60 lifted, D-144, D-149, CFG-81)."""
from __future__ import annotations

import dataclasses

from starbour.config import GameConfig
from starbour.engine import ai
from starbour.engine import economy as E
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState
from starbour.entities.item import Item
from starbour.world.generate import game_rng


def turns(state: GameState, cfg: GameConfig, n: int, *, passive: bool = False, ai_player: bool = False):
    rng = game_rng(state.seed)
    stats = []
    for _ in range(n):
        state, _ = resolve_turn(state, cfg, rng, ai_passive=passive, ai_player=ai_player)
        stats.append(state.last_stats)
    return state, stats


def test_ai_captains_start_at_the_ai_respect(small_state: GameState, small_cfg: GameConfig) -> None:
    ai_caps = [c for c in small_state.captains.values() if not c.is_player]
    assert ai_caps and all(c.respect == small_cfg.ai_respect_start for c in ai_caps), "D-149: [ai] respect_start for generated AI captains"
    assert small_state.player.respect == small_cfg.respect_start, "the player keeps [respect] start (LV-02)"


def test_ai_consumes_supplies_and_restocks_before_a_mutiny(small_state: GameState, small_cfg: GameConfig) -> None:
    cfg = dataclasses.replace(small_cfg, roam_every_turns=0)
    ai_caps = [c for c in small_state.captains.values() if not c.is_player]
    state, stats = turns(small_state, cfg, cfg.food_start + cfg.mutiny_turns_at_zero + 2)
    assert all(m["captain"] == "player" for s in stats for m in s["mutinies"]), "no AI captain starves: restock trips start below buy_supplies_below"
    trips = sum(s["ai_economy"]["restock_trips"] for s in stats)
    purchases = sum(s["ai_economy"]["purchases"] for s in stats)
    assert trips >= 1 and purchases >= 1
    assert any(r["captain"] != state.player_id and r["kind"] == "buy" for s in stats for r in s["trades"])
    restocked = [c for c in state.captains.values() if not c.is_player and c.id in {x.id for x in ai_caps}]
    assert any(c.food > cfg.buy_supplies_below for c in restocked)


def test_restock_warps_are_not_roams(small_state: GameState, small_cfg: GameConfig) -> None:
    cfg = dataclasses.replace(small_cfg, roam_every_turns=0)
    state, stats = turns(small_state, cfg, cfg.food_start)
    assert sum(s["roams"] for s in stats) == 0, "CFG-73: 0 disables roaming; a restock warp is not a roam (D-144)"


def test_passive_ai_keeps_d60(small_state: GameState, small_cfg: GameConfig) -> None:
    before = {c.id: c.food for c in small_state.captains.values() if not c.is_player}
    state, _ = turns(small_state, small_cfg, 3, passive=True)
    assert all(state.captains[cid].food == f for cid, f in before.items() if cid in state.captains), "--ai passive: no AI consumption (D-150)"


def test_knobs_at_zero_starve_the_ai(small_state: GameState, small_cfg: GameConfig) -> None:
    cfg = dataclasses.replace(small_cfg, buy_supplies_below=0, docking_income_silver=0, roam_every_turns=0)
    state, stats = turns(small_state, cfg, cfg.food_start + cfg.mutiny_turns_at_zero + 1)
    ai_mutinies = sum(1 for s in stats for m in s["mutinies"] if m["captain"] != "player")
    assert ai_mutinies >= 1, "with buy_supplies_below 0 nobody restocks (CFG-81 live)"


def test_ai_picks_up_what_it_lands_on(small_state: GameState, small_cfg: GameConfig) -> None:
    cap = next(c for c in small_state.captains.values() if not c.is_player)
    ship = small_state.ships[cap.active_ship]
    sector = small_state.sector_of(ship)
    sector.items.append(Item("i-9002", "iridium", "normal", ship.cell, units=3))
    ship.engine = 0.0     # it stays put, so the stack lies where it ends the turn (pickups follow movement, TP-05)
    state, stats = turns(small_state, small_cfg, 1)
    rows = [r for r in stats[0]["pickups"] if r["captain"] == cap.id]
    assert rows and rows[0]["element"] == "iridium" and rows[0]["units"] == 3 and cap.items.get("iridium") == 3


def test_docked_ai_sells_elements_and_earns_income(small_state: GameState, small_cfg: GameConfig) -> None:
    cap = next(c for c in small_state.captains.values() if not c.is_player)
    ship = small_state.ships[cap.active_ship]
    planet = next((p for (g, s), sec in small_state.world.sectors.items() for p in sec.planets() if not sec.has_hole()), None)
    assert planet is not None
    ship.position = (planet.galaxy, planet.sector, planet.cell)
    cap.items = {"iridium": 2}
    cap.food = small_cfg.buy_supplies_below - 1
    silver0 = cap.silver
    state, stats = turns(small_state, small_cfg, 1)
    assert stats[0]["ai_economy"]["sales"] >= 1 and stats[0]["ai_economy"]["docked"] >= 1 and cap.items == {}
    assert cap.silver > silver0 - 200 and cap.food > small_cfg.buy_supplies_below - 1, "sold, earned the placeholder income, bought food"


def test_ai_does_not_open_when_the_fight_would_cost_its_trade(small_state: GameState, small_cfg: GameConfig) -> None:
    from tests.test_combat import arena, spawn
    ship = arena(small_state)
    cap, foe = spawn(small_state, "corsair", (6, 5))
    assert ai.can_afford_a_fight(cap, small_cfg)
    cap.respect = small_cfg.planet_refuse_respect + small_cfg.respect_start_fight_delta - 1
    assert not ai.can_afford_a_fight(cap, small_cfg)
    assert all(o.command != "fire" for o in ai.decide(cap, small_state, game_rng(1), small_cfg)), "D-150: it keeps enough respect to eat"


def test_hostile_planet_cells_cover_its_range(small_state: GameState, small_cfg: GameConfig) -> None:
    from tests.test_economy import dock
    ship, planet = dock(small_state, small_cfg.planet_attack_respect_max - 5)
    cells = ai.hostile_planet_cells(small_state.player, ship, [ship], small_state, small_cfg)
    assert planet.cell in cells and (planet.cell[0] + small_cfg.torpedo_range + 1, planet.cell[1]) not in cells
    small_state.player.respect = small_cfg.planet_attack_respect_max
    assert ai.hostile_planet_cells(small_state.player, ship, [ship], small_state, small_cfg) == set()
