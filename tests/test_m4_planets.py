"""Items in space and planets in combat - SP-03, IT-04, IT-05, CMD-14, CMD-15, FR-08, CM-11, EF-05, PR-02, CB-03, D-141, D-145, D-148."""
from __future__ import annotations

import dataclasses

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import combat as C
from starbour.engine import orders as O
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState, from_snapshot
from starbour.entities.item import Item
from starbour.world.generate import game_rng
from tests.test_combat import arena
from tests.test_economy import dock


def run(state: GameState, cfg: GameConfig, line: str | None = None, n: int = 1, passive: bool = True):
    rng = game_rng(state.seed)
    if line:
        for part in line.split(";"):
            res = O.submit(state, parse(part.strip(), state.player.active_ship), cfg)
            assert isinstance(res, O.Accepted), getattr(res, "reason", res)
    events: list[dict] = []
    for _ in range(n):
        state, events = resolve_turn(state, cfg, rng, ai_passive=passive)
    return state, events


# --------------------------------------------------------------------------- items (D-141)

def test_item_hidden_until_scanned_then_picked_up_once(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    sector = small_state.sector_of(ship)
    sector.items = [Item("i-9001", "orichalcum", "special", ship.cell, units=2)]
    assert sector.to_dict()["items"][0] == {"id": "i-9001", "cell": list(ship.cell), "rarity": "special", "revealed": False}, "element and units stay hidden (C11)"
    res = O.submit(small_state, parse("pickup orichalcum", ship.id), small_cfg)
    assert isinstance(res, O.Rejected) and "IT-04" in res.reason
    acc = O.apply_immediate(small_state, parse("scan", ship.id), small_cfg)
    assert isinstance(acc, O.Accepted) and "orichalcum" in acc.text and "2 units" in acc.text
    assert sector.to_dict()["items"][0]["element"] == "orichalcum" and sector.to_dict()["items"][0]["units"] == 2
    respect0 = small_state.player.respect
    state, events = run(small_state, small_cfg, "pickup orichalcum")
    row = state.last_stats["pickups"][0]
    assert row == {"captain": state.player_id, "element": "orichalcum", "units": 2, "rarity": "special", "cell": list(ship.cell)}
    assert state.player.items == {"orichalcum": 2} and sector.items == []
    assert state.player.respect == respect0 + small_cfg.respect_special_item_delta, "FR-08 once for a special stack"
    assert any("Picked up 2 orichalcum" in e["text"] for e in events)
    res = O.submit(state, parse("pickup orichalcum", ship.id), small_cfg)
    assert isinstance(res, O.Rejected), "no respawn (IT-05)"
    snap = state.snapshot()
    back = from_snapshot(snap, small_cfg).snapshot()
    assert {k: v for k, v in back.items() if k != "sectors"} == {k: v for k, v in snap.items() if k != "sectors"}   # the arena cleared terrain the seed regenerates (D-87)
    assert back["sectors"][sector.key]["items"] == snap["sectors"][sector.key]["items"] == []


# --------------------------------------------------------------------------- planets in combat (CM-11, D-145)

def test_fire_on_a_planet_opens_a_battle_and_the_planet_fires_back(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, 50)
    big = dataclasses.replace(small_cfg, ships=dict(small_cfg.ships, kestrel=dataclasses.replace(small_cfg.ships["kestrel"], shield_max=400.0)))
    ship.shield_max = ship.shield_current = 400.0
    respect0 = small_state.player.respect
    state, events = run(small_state, big, f"fire phaser {planet.id}")
    b = state.last_stats["battles"][0]
    assert set(b["sides"]) == {state.player_id, planet.id} and b["opened_by"] == state.player_id and b["ended"] is None
    weapons = sorted(r["weapon"] for r in b["exchanges"] if r["attacker"] == planet.id)
    assert weapons == ["phaser", "torpedo"] and all(r["distance"] == 0 for r in b["exchanges"])
    assert any(e == {"planet": planet.id, "event": "attacked", "by": state.player_id} for e in state.last_stats["planet_events"])
    assert state.player.respect == respect0 - small_cfg.respect_start_fight_delta, "FR-06 for the opener"
    assert planet.shield_current == planet.shield_max - C.phaser_damage(ship.phasers, big.phaser_damage_per_unit, 0)
    assert state.snapshot()["planets"][planet.id]["in_battle"] == b["id"]
    state, _ = run(state, big)
    assert any(r["attacker"] == planet.id for r in state.last_stats["battles"][0]["exchanges"]), "the planet keeps firing every turn"


def test_planet_destroyed_is_looted_and_removed(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, 50)
    cfg = dataclasses.replace(small_cfg, torpedo_hit_base=1.0, torpedo_evade_per_engine=0.0)
    ship.torpedoes = 60
    ship.shield_max = ship.shield_current = 500.0
    food0, water0 = small_state.player.food, small_state.player.water
    stock_food, stock_els = planet.stock("food", 0, cfg), dict(planet.elements)
    torps0 = ship.torpedoes
    need = int(planet.shield_max / cfg.torpedo_damage) + 2
    state, events = run(small_state, cfg, f"fire torpedo {planet.id} {need}; fire phaser {planet.id}")
    assert planet.destroyed and state.planet_at(planet.galaxy, planet.sector, planet.cell) is None
    b = state.last_stats["battles"][0]
    assert b["ended"] == "destroyed" and b["victor"] == state.player_id
    assert any(e["planet"] == planet.id and e["event"] == "destroyed" for e in state.last_stats["planet_events"]) and state.last_stats["planets_destroyed"] == 1
    cap = state.player
    assert cap.food == food0 + stock_food - 1 and cap.water == water0 + planet.stock("water", 0, cfg) + stock_food - stock_food - 1 or cap.water > water0
    for el, n in stock_els.items():
        assert cap.items.get(el, 0) >= n
    fired = sum(1 for r in b["exchanges"] if r["attacker"] == ship.id and r["weapon"] == "torpedo")
    p_fired = sum(1 for r in b["exchanges"] if r["attacker"] == planet.id and r["weapon"] == "torpedo")
    assert ship.torpedoes == torps0 - fired + (small_cfg.planet_torpedoes - p_fired), "the planet's remaining torpedoes are looted (CB-01)"
    snap = state.snapshot()
    assert snap["planets"][planet.id]["destroyed"] is True
    assert not any(t["kind"] == "planet" and t["cell"] == list(planet.cell) for t in snap["sectors"][planet.sector_key()]["terrain"])
    back = from_snapshot(snap, cfg)
    assert back.snapshot() == snap and back.planet_at(planet.galaxy, planet.sector, planet.cell) is None, "the destroyed planet stays gone (C6)"


def test_planet_accepts_peace_when_weaker_and_takes_coins(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, 50)
    planet.shield_max = planet.shield_current = 100.0
    planet.torpedoes = planet.torpedoes_gen = 0
    planet.phasers = 1.0
    cap = small_state.player
    cap.gold, cap.silver = 5, 50
    state, _ = run(small_state, small_cfg, f"fire phaser {planet.id}")
    state, _ = run(state, small_cfg, f"peace offer {planet.id}")
    state, events = run(state, small_cfg)
    b = next(b for t in [state.last_stats] for b in t["battles"])
    assert b["ended"] == "peace" and b["victor"] == planet.id and b["transfer"]["pct"] >= 0
    pct = b["transfer"]["pct"]
    assert cap.gold == 5 - 5 * pct // 100 and cap.silver == 50 - 50 * pct // 100
    assert any(e["planet"] == planet.id and e["event"] == "peace" for e in state.last_stats["planet_events"])


def test_ef05_planet_opens_on_a_low_respect_weaker_ship_only(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, 50)
    free = next(c for c in small_state.sector_of(ship).grid.neighbours(*planet.cell) if small_state.sector_of(ship).grid.is_void(*c))
    small_state.teleport((ship.galaxy, ship.sector, free))
    ratio = C.ship_strength(planet, small_cfg) / C.strength([ship], small_cfg)
    assert ratio >= small_cfg.attack_ratio_min, "the default planet outguns a Kestrel (M4 section 5)"
    small_state.player.respect = small_cfg.planet_attack_respect_max - 10
    state, _ = run(small_state, small_cfg, passive=False)
    b = state.last_stats["battles"][0]
    assert b["opened_by"] == planet.id and {"planet": planet.id, "event": "opened_fire", "by": planet.id} in state.last_stats["planet_events"]
    assert any(r["attacker"] == planet.id for r in b["exchanges"])
    quiet = from_snapshot(small_state.snapshot(), small_cfg)
    quiet.battles.clear()
    quiet.player.respect = small_cfg.planet_attack_respect_max + 40
    quiet, _ = run(quiet, small_cfg, passive=False)
    assert not quiet.last_stats["battles"], "respect at or above the threshold: the planet holds"
    strong = from_snapshot(small_state.snapshot(), small_cfg)
    strong.battles.clear()
    strong.player.respect = small_cfg.planet_attack_respect_max - 10
    strong.active_ship().shield_max = strong.active_ship().shield_current = 2000.0
    strong, _ = run(strong, small_cfg, passive=False)
    assert not strong.last_stats["battles"], "a planet never opens on a ship it cannot beat"
    passive = from_snapshot(small_state.snapshot(), small_cfg)
    passive.battles.clear()
    passive, _ = run(passive, small_cfg, passive=True)
    assert not passive.last_stats["battles"], "--ai passive removes the planets' initiative too (D-132)"
