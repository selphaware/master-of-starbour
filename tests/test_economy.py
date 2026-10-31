"""Trade, prices, disposition, barter and upgrades - the pure rules and the specs/M4.md section 5 vectors.

PL-06, PR-05, TR-01...06, PRC-01...05, EF-03, EF-04, UP-01...05, CMD-12, CMD-13, CMD-20, FR-07, D-142, D-143, D-148.
Every expected number is computed from the ini the test loads (never a literal that could drift from it), and the
section 5 vectors are asserted once against the default values they were written for."""
from __future__ import annotations

import dataclasses

import pytest

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import economy as E
from starbour.engine import orders as O
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState, from_snapshot
from starbour.world.generate import game_rng
from tests.test_combat import arena, spawn


def dock(state: GameState, respect: int = 50):
    """Park the player on a planet of its start sector (PR-04) with a clear respect (EF-04, EF-05)."""
    ship = state.active_ship()
    planets = state.planets_in(ship.galaxy, ship.sector)
    assert planets, "the safe start sector holds a planet (SP-05)"
    planet = planets[0]
    state.teleport((ship.galaxy, ship.sector, planet.cell))
    state.player.respect = respect
    for sid in list(state.ships):
        s = state.ships[sid]
        if s.captain_id != state.player_id and (s.galaxy, s.sector) == (ship.galaxy, ship.sector):
            state.remove_ship(sid)
    return ship, planet


def run(state: GameState, cfg: GameConfig, line: str | None = None, n: int = 1):
    rng = game_rng(state.seed)
    if line:
        for part in line.split(";"):
            res = O.submit(state, parse(part.strip(), state.player.active_ship), cfg)
            assert isinstance(res, O.Accepted), getattr(res, "reason", res)
    events: list[dict] = []
    for _ in range(n):
        state, events = resolve_turn(state, cfg, rng, ai_passive=True)
    return state, events


# --------------------------------------------------------------------------- formulas (M4 section 5)

def test_section_5_vectors(cfg: GameConfig) -> None:
    """The numbers specs/M4.md section 5 names, valid for the default ini."""
    if cfg.planet_price_band != 20 or cfg.prices["food_buy"] != 2 or cfg.elements["iridium"].value != 30 or cfg.prices["element_sell_pct"] != 60:
        pytest.skip("the ini no longer carries the section 5 defaults")
    assert (E.disposition(10), E.disposition(50), E.disposition(100)) == (-80, 0, 100)
    assert E.price_factor(50, 20, selling=False) == pytest.approx(0.90) and E.price_factor(100, 20, selling=False) == pytest.approx(0.80)
    assert E.price_factor(50, 20, selling=True) == pytest.approx(1.10) and E.price_factor(100, 20, selling=True) == pytest.approx(1.20)
    assert E.total_price(E.planet_unit_price(cfg, "food", 50, selling=False), 10) == 18
    assert E.total_price(E.planet_unit_price(cfg, "iridium", 50, selling=True), 1) == 19      # 30 x 0.6 x 1.1 = 19.8 -> 19
    assert E.barter_factor(E.disposition(10), 20) == pytest.approx(1.16) and E.barter_factor(E.disposition(90), 20) == pytest.approx(0.84)
    assert cfg.prices["ship_kestrel"] == 40 and E.wealth(dataclasses.replace(E.Captain.new("c-x", "x", True, cfg)), cfg) == 150


def test_prices_follow_the_ini_not_literals(cfg: GameConfig) -> None:
    """D-49: a changed [prices] value changes the cost."""
    alt = dataclasses.replace(cfg, prices=dict(cfg.prices, food_buy=7))
    assert E.total_price(E.planet_unit_price(alt, "food", 50, selling=False), 10) == E.floor_silver(10 * 7 * E.price_factor(50, cfg.planet_price_band, selling=False))


def test_pay_converts_gold_only_when_silver_is_short(cfg: GameConfig) -> None:
    cap = E.Captain.new("c-x", "x", True, cfg)
    cap.gold, cap.silver = 5, 50
    assert E.pay(cap, 63, cfg) and (cap.gold, cap.silver) == (4, 7), "one gold converts for the 13 silver short (TR-01)"
    assert E.pay(cap, 7, cfg) and (cap.gold, cap.silver) == (4, 0)
    assert not E.pay(cap, 100, cfg) and (cap.gold, cap.silver) == (4, 0), "cannot pay: nothing moves"


# --------------------------------------------------------------------------- buy, sell, refusals (CMD-20)

def test_buy_food_changes_coins_supplies_and_stock(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, 50)
    cap = small_state.player
    cap.gold, cap.silver = 5, 50
    food0, stock0 = cap.food, planet.stock("food", 0, small_cfg)
    cost = E.total_price(E.planet_unit_price(small_cfg, "food", 50, selling=False), 10)
    state, events = run(small_state, small_cfg, "buy food 10")
    rows = state.last_stats["trades"]
    assert rows and rows[0]["accepted"] and rows[0]["kind"] == "buy" and rows[0]["with"] == planet.id and rows[0]["gave"] == {"silver": cost}
    assert E.wealth(cap, small_cfg) == 150 - cost and cap.food == food0 + 10 - 1, "bought at TP-05, upkeep at TP-06"
    assert planet.stock("food", 1, small_cfg) == stock0 - 10 + small_cfg.stock_regen, "the stock falls by the purchase and regenerates at TP-06 (PR-03)"
    assert any("food" in e["text"] and str(cost) in e["text"] for e in events if e["phase"] == "TP-05"), "OUT-07 names the item and the silver"
    assert any(r["rule"] == "FR-07" and r["captain"] == cap.id for r in state.last_stats["reputation"]) and cap.respect == 50 + small_cfg.respect_trade_accepted_delta
    assert planet.id in state.snapshot()["planets"]


def test_sell_element_pays_the_sell_price(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, 50)
    cap = small_state.player
    cap.items = {"iridium": 1}
    want = E.total_price(E.planet_unit_price(small_cfg, "iridium", 50, selling=True), 1)
    state, _ = run(small_state, small_cfg, "sell iridium 1")
    assert cap.silver == small_cfg.silver_start + want and cap.items == {} and planet.elements.get("iridium", 0) >= 1


def test_buy_beyond_the_coins_or_the_stock_is_refused(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, 50)
    res = O.submit(small_state, parse("buy food 200", ship.id), small_cfg)
    assert isinstance(res, O.Rejected) and ("silver" in res.reason or "stock" in res.reason) and "200" in res.reason or "needed" in res.reason


def test_planet_refuses_below_the_threshold(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, small_cfg.planet_refuse_respect - 8)
    res = O.submit(small_state, parse("buy food 1", ship.id), small_cfg)
    assert isinstance(res, O.Rejected) and "EF-04" in res.reason and "refuses" in res.reason
    snap = small_state.snapshot()
    rec = snap["planets"][planet.id]
    assert rec["trades_with_player"] is False and rec["disposition_to_player"] == E.disposition(small_state.player.respect)


def test_buy_ship_in_gold_adds_a_ship_to_the_fleet(small_state: GameState, small_cfg: GameConfig) -> None:
    ship, planet = dock(small_state, 50)
    cls, price = min(((k[5:], v) for k, v in small_cfg.prices.items() if k.startswith("ship_")), key=lambda kv: kv[1])
    cap = small_state.player
    cap.gold, cap.silver = price, 0
    state, _ = run(small_state, small_cfg, f"buy ship {cls}")
    assert cap.gold == 0 and len(cap.ships) == 2
    new = state.ships[cap.ships[-1]]
    assert new.cls == cls and new.position == ship.position and state.last_stats["trades"][0]["kind"] == "ship"
    cap.gold = price // 4
    res = O.submit(state, parse(f"buy ship {cls}", ship.id), small_cfg)
    assert isinstance(res, O.Rejected) and "gold" in res.reason


# --------------------------------------------------------------------------- barter (CMD-12, TR-06)

def test_barter_threshold_with_a_planet(small_state: GameState, small_cfg: GameConfig) -> None:
    r = small_cfg.planet_refuse_respect
    ship, planet = dock(small_state, r)
    cap = small_state.player
    cap.items = {"iridium": 1}
    f = E.barter_factor(E.disposition(r), small_cfg.planet_price_band)
    value, unit = small_cfg.elements["iridium"].value, small_cfg.prices["food_buy"]
    n_ok = int(value / (unit * f) + 1e-9)
    n_bad = n_ok + 1
    while n_bad * unit * f <= value + 1e-9:
        n_bad += 1
    state, _ = run(small_state, small_cfg, f"trade {planet.id} give iridium:1 for food:{n_bad}")
    row = state.last_stats["trades"][0]
    assert row["accepted"] is False and "TR-06" in row["reason"] and cap.items == {"iridium": 1}
    food0 = cap.food
    state, _ = run(state, small_cfg, f"trade {planet.id} give iridium:1 for food:{n_ok}")
    row = state.last_stats["trades"][0]
    assert row["accepted"] is True and row["kind"] == "barter" and cap.items == {} and cap.food == food0 + n_ok - 1
    assert cap.respect == r + small_cfg.respect_trade_accepted_delta


def test_barter_with_an_ai_captain_moves_goods_both_ways(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    small_state.player.respect = 90
    small_state.player.items = {"iridium": 1}
    other_cap, other = spawn(small_state, "kestrel", ship.cell)
    f = E.barter_factor(E.disposition(90), small_cfg.respect_price_band)
    value = small_cfg.elements["iridium"].value
    n_ok = int(value / f + 1e-9)
    silver0 = other_cap.silver
    state, _ = run(small_state, small_cfg, f"trade {other_cap.id} give iridium:1 for silver:{n_ok}")
    row = state.last_stats["trades"][0]
    assert row["accepted"] and other_cap.items == {"iridium": 1} and other_cap.silver == silver0 - n_ok and state.player.silver == small_cfg.silver_start + n_ok


# --------------------------------------------------------------------------- upgrades (CMD-13, D-143)

def test_upgrades_follow_the_map_and_the_caps(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap = small_state.player
    cap.items = {el: 1 for el in small_cfg.elements}
    comp, (el, amount) = "shields", sorted(small_cfg.upgrades["shields"].items())[0]
    before = ship.shield_max
    state, events = run(small_state, small_cfg, f"upgrade {comp} with {el}")
    row = state.last_stats["upgrades"][0]
    assert row["component"] == comp and row["element"] == el and row["before"] == before and row["after"] == pytest.approx(min(small_cfg.shield_cap, before + amount))
    assert cap.items.get(el, 0) == 0 and any("->" in e["text"] and comp in e["text"].lower() for e in events if e["phase"] == "TP-05")
    wrong = next(e for e in sorted(small_cfg.elements) if e not in small_cfg.upgrades["shields"])
    res = O.submit(state, parse(f"upgrade shields with {wrong}", ship.id), small_cfg)
    assert isinstance(res, O.Rejected) and "CMD-13" in res.reason
    big = dataclasses.replace(small_cfg, upgrades=dict(small_cfg.upgrades, phasers={"osmium": 2 * small_cfg.phaser_max}))
    cap.items = {"osmium": 1}
    state, _ = run(state, big, "upgrade phasers with osmium")
    row = state.last_stats["upgrades"][0]
    assert row["capped"] is True and ship.phasers == small_cfg.phaser_max


def test_economy_state_round_trips(small_state: GameState, small_cfg: GameConfig) -> None:
    """C6: stock, items, a touched planet and revealed items survive snapshot -> from_snapshot."""
    ship, planet = dock(small_state, 50)
    small_state.player.items = {"iridium": 2}
    state, _ = run(small_state, small_cfg, "buy food 3; sell iridium 1")
    snap = state.snapshot()
    back = from_snapshot(snap, small_cfg)
    assert back.snapshot() == snap and back.player.items == {"iridium": 1} and planet.id in back.touched_planets
