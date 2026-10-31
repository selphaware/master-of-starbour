"""M4 fixtures for --selftest (specs/M4.md § 4, CONTRACTS C11), called by make_fixtures.py.

sim_m4_scenarios.json holds one good synthetic C2+C9+C10+C11 report (plus the save snapshot / probe
facts a row reads) per gate scenario, each with the exact checker call the gate makes (`check`,
`args`, `kwargs`) against verify/_economy.py, so the selftest proves every checker passes its good
fixture and fails its broken one for the right reason.  The broken fixtures the spec names —
sim_item_auto_pickup, sim_price_ignored, sim_trade_wrong_threshold, sim_upgrade_uncapped,
sim_barter_ai_wrong, sim_planet_immortal, sim_planet_too_wild, sim_ai_starves, sim_ai_no_trade,
sim_fire_all_one_row, sim_save_planet_resurrects — are minimal edits of the good ones.  All
numbers follow the default controls_config.ini (FIXTURE_M4) and the conventions of D-148.  No
franchise names (D-03).
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
import _economy as E  # noqa: E402
from make_fixtures_m3 import CELL, HOME, PID, PSHIP, SPAWN_C, SPAWN_S, battle, case, ex, rep, report, snapshot  # noqa: E402

FIXTURE_M4 = {
    "food_buy": 2.0, "food_sell": 1.0, "water_buy": 2.0, "water_sell": 1.0, "torpedo_buy": 10.0, "torpedo_sell": 5.0, "element_sell_pct": 60.0,
    "silver_per_gold": 20, "planet_refuse_respect": 20, "planet_price_band": 20.0, "respect_price_band": 10.0, "trade_delta": 3, "special_delta": 6,
    "planet_attack_respect_max": 20, "planet": {"shield_max": 80.0, "torpedoes": 10.0, "phasers": 4.0}, "food_stock_start": 100, "stock_regen": 5, "stock_cap": 200,
    "phaser_max": 10.0, "shield_cap": 100.0, "impulse_max": 10.0, "warp_max": 10.0, "torpedo_damage": 15.0, "phaser_per_unit": 5.0, "peace_loot_max": 50.0,
    "attack_ratio_min": 1.3, "peace_shield_pct": 30.0, "peace_pct_at_fear100": 60.0,
    "elements": {"iridium": 30.0, "osmium": 40.0, "rhodium": 40.0, "sapphire": 60.0, "moonstone": 60.0, "sunstone": 80.0, "antihydrogen": 100.0, "positronium": 100.0,
                 "orichalcum": 400.0, "adamantine": 400.0, "aetherite": 500.0, "star_iron": 500.0},
    "upgrades": {"torpedoes": {"iridium": 4.0, "positronium": 10.0}, "phasers": {"osmium": 0.5, "sunstone": 1.0, "adamantine": 2.0},
                 "shields": {"rhodium": 5.0, "sapphire": 10.0, "orichalcum": 25.0}, "engine": {"moonstone": 0.5, "star_iron": 1.5}, "warp": {"antihydrogen": 0.5, "aetherite": 1.5}},
}
RENDER_K = {"planet_shield_max": 80.0, "planet_torpedoes": 10, "planet_phasers": 4.0, "food_buy": 2.0, "planet_price_band": 20.0, "elements": FIXTURE_M4["elements"],
            "peace_shield_pct": 30.0, "peace_pct_at_fear100": 60.0}
PLANET, PLANET_NAME, PLANET_CELL, KEY = "p-012", "Oreth", [6, 5], "3,4/7,9"
NEIGHBOUR = [7, 5]
NORMAL_ITEM = {"galaxy": HOME[0], "sector": HOME[1], "cell": [9, 2], "element": "iridium", "units": 2, "rarity": "normal"}
SPECIAL_ITEM = {"galaxy": HOME[0], "sector": HOME[1], "cell": [3, 20], "element": "orichalcum", "units": 1, "rarity": "special"}
COINS = [5, 50]
CAPS = {"torpedoes": None, "phasers": 10.0, "shields": 100.0, "engine": 10.0, "warp": 10.0}
BASE = {"torpedoes": 6.0, "phasers": 3.0, "shield_max": 60.0, "engine": 4.0, "warp": 3.0}


# --------------------------------------------------------------------------- builders

def planet_record(**over) -> dict:
    rec = {"name": PLANET_NAME, "president": "Maren Ostrel", "cell": list(PLANET_CELL), "sector": KEY, "shields": 80.0, "shield_max": 80.0, "torpedoes": 10, "phasers": 4.0,
           "stock": {"food": 100, "water": 100, "elements": {"iridium": 3, "rhodium": 2}}, "disposition_to_player": 0, "trades_with_player": True, "in_battle": None,
           "destroyed": False}
    rec.update(over)
    return rec


def m4_snapshot(turn_no: int, *, player_cell=CELL, respect: int = 50, items: dict | None = None, planets: dict | None = None, sector_items: list | None = None,
                ship: dict | None = None, spawns: list[dict] | None = None, ships_owned: list[str] | None = None, extra_ships: dict | None = None,
                captain: dict | None = None, remove_planet_terrain: bool = False) -> dict:
    cap = {"respect": respect, "items": dict(items or {})}
    cap.update(captain or {})
    snap = snapshot(turn_no, player_cell=player_cell, captain=cap, player_ship=ship, spawns=spawns, player_ships=ships_owned, extra_ships=extra_ships)
    snap["planets"] = copy.deepcopy(planets) if planets is not None else {}
    sec = snap["sectors"][KEY]
    sec["items"] = list(sector_items or [])
    if remove_planet_terrain:
        sec["terrain"] = [t for t in sec["terrain"] if t.get("kind") != "planet"]
    return snap


def m4_report(turns: list[dict], **kw) -> dict:
    """report() plus the C11 per-turn lists (trades, pickups, upgrades, planet_events, ai_economy, player.items) and final counts."""
    doc = report(turns, **kw)
    for t, spec in zip(doc["per_turn"], turns):
        for key in ("trades", "pickups", "upgrades", "planet_events"):
            t[key] = copy.deepcopy(spec.get(key) or [])
        t["ai_economy"] = dict(spec.get("ai_economy") or {"purchases": 0, "sales": 0, "restock_trips": 0, "docked": 0})
        t["player"].setdefault("items", {})
    fin = doc["final"]
    fin.setdefault("trades", sum(1 for t in doc["per_turn"] for r in t["trades"] if r.get("accepted")))
    fin.setdefault("pickups", sum(len(t["pickups"]) for t in doc["per_turn"]))
    fin.setdefault("upgrades", sum(len(t["upgrades"]) for t in doc["per_turn"]))
    fin.setdefault("planets_destroyed", sum(1 for t in doc["per_turn"] for e in t["planet_events"] if e.get("event") == "destroyed"))
    fin.setdefault("ai_purchases", sum(int(t["ai_economy"].get("purchases", 0)) for t in doc["per_turn"]))
    fin.setdefault("ai_mutinies", 0)
    counts = [t["captains"] for t in doc["per_turn"]]
    fin.setdefault("captains_start", counts[0] if counts else 193)
    fin.setdefault("population_min", min(counts) if counts else 193)
    fin.setdefault("population_max", max(counts) if counts else 193)
    return doc


def trade(with_: str, kind: str, gave: dict, received: dict, *, accepted: bool = True, reason: str | None = None, factor: float | None = None) -> dict:
    return {"with": with_, "kind": kind, "gave": gave, "received": received, "accepted": accepted, "reason": reason, "price_factor": factor}


def sector_item(item: dict, revealed: bool) -> dict:
    d = {"id": "i-017", "cell": list(item["cell"]), "rarity": item["rarity"], "revealed": revealed}
    if revealed:
        d.update(element=item["element"], units=item["units"])
    return d


def at(cell: list, **player) -> dict:
    return {"position": [HOME[0], HOME[1], list(cell)], **player}


# --------------------------------------------------------------------------- smoke/37 — items

def item_hidden() -> dict:
    t1 = {"events": ["script order 'pickup iridium' rejected: nothing has been scanned in this cell (IT-04, CMD-15)"], "player": at(NORMAL_ITEM["cell"], respect=50)}
    return case("item_hidden", ["report", "snapshot"], {"item": NORMAL_ITEM, "key": KEY, "what": "fixture item hidden"}, report=m4_report([t1]),
                snapshot=m4_snapshot(1, player_cell=NORMAL_ITEM["cell"], sector_items=[sector_item(NORMAL_ITEM, False)]))


def item_scan() -> dict:
    t1 = {"events": ["script order 'scan' accepted: items here: iridium (2 units, normal) at 9,2; captains in this sector: none"], "player": at(NORMAL_ITEM["cell"], respect=50)}
    return case("item_scan", ["report", "snapshot"], {"item": NORMAL_ITEM, "key": KEY, "what": "fixture item scan"}, report=m4_report([t1]),
                snapshot=m4_snapshot(1, player_cell=NORMAL_ITEM["cell"], sector_items=[sector_item(NORMAL_ITEM, True)]))


def item_pickup(item: dict, special: bool) -> dict:
    el, u = item["element"], item["units"]
    respect = 50 + (FIXTURE_M4["special_delta"] if special else 0)
    turns = [{"events": [f"script order 'scan' accepted: items here: {el} ({u} unit{'s' if u != 1 else ''}, {item['rarity']}) at {item['cell'][0]},{item['cell'][1]}"],
              "player": at(item["cell"], respect=50)},
             {"events": [f"Picked up {u} {el} (OUT-10)"], "pickups": [{"captain": PID, "element": el, "units": u, "rarity": item["rarity"]}],
              "player": at(item["cell"], respect=respect, items={el: u}), "reputation": [rep(PID, "FR-08", 0, FIXTURE_M4["special_delta"])] if special else []}]
    for _ in range(3 if not special else 1):
        turns.append({"events": ["script order 'scan' accepted: items here: none; captains in this sector: none"], "player": at(item["cell"], respect=respect, items={el: u})})
    return case("item_pickup", ["report", "snapshot"], {"item": item, "key": KEY, "pickup_turn": 2, "respect_start": 50, "special_delta": FIXTURE_M4["special_delta"],
                                                        "what": f"fixture pickup {el}"},
                report=m4_report(turns), snapshot=m4_snapshot(len(turns), player_cell=item["cell"], respect=respect, items={el: u}))


def no_respawn() -> dict:
    from make_fixtures import M2_INDEX  # noqa: E402
    idx = copy.deepcopy(M2_INDEX)
    idx["items"] = [dict(NORMAL_ITEM), dict(SPECIAL_ITEM)] + [{"galaxy": HOME[0], "sector": [8, 9], "cell": [i, i + 1], "element": "osmium", "units": 1, "rarity": "normal"} for i in range(10)]
    turns = []
    for t in range(1, 61):
        pk = [{"captain": f"c-0{t:02d}", "element": "osmium", "units": 1, "rarity": "normal"}] if t % 15 == 0 else []
        turns.append({"pickups": pk, "tag": "ai60"})
    return case("no_respawn", ["report", "index"], {"what": "fixture no respawn"}, report=m4_report(turns), index=idx)


# --------------------------------------------------------------------------- smoke/38 — buy / sell

def market_turns() -> list[dict]:
    cost1 = 18                                         # 10 × 2 × 0.9 at respect 50
    sell = int(math.floor(30 * 0.6 * (1 + 0.2 * 0.53) + 1e-9))   # 19 at respect 53
    cost3 = int(math.floor(200 * 2 * (1 - 0.2 * 0.56) + 1e-9))   # 355 at respect 56
    return [
        {"trades": [trade(PLANET, "buy", {"silver": cost1}, {"food": 10}, factor=0.9)], "reputation": [rep(PID, "FR-07", 0, 3)],
         "events": [f"Bought 10 food from {PLANET_NAME} for {cost1} silver (OUT-07)"], "player": at(PLANET_CELL, respect=53, silver=50 - cost1, food=39, items={"iridium": 1})},
        {"trades": [trade(PLANET, "sell", {"iridium": 1}, {"silver": sell}, factor=1.106)], "reputation": [rep(PID, "FR-07", 0, 3)],
         "events": [f"Sold 1 iridium to {PLANET_NAME} for {sell} silver (OUT-07)"], "player": at(PLANET_CELL, respect=56, silver=50 - cost1 + sell, food=38, items={})},
        {"trades": [trade(PLANET, "buy", {"silver": cost3}, {"food": 200}, accepted=False, reason=f"not enough silver: {cost3} needed, {100 + 50 - cost1 + sell} held (TR-04)", factor=0.888)],
         "events": [f"script order 'buy food 200' rejected: not enough silver: {cost3} needed, {100 + 50 - cost1 + sell} held (TR-04)"],
         "player": at(PLANET_CELL, respect=56, silver=50 - cost1 + sell, food=37, items={})},
    ]


def market_snapshot() -> dict:
    return m4_snapshot(3, player_cell=PLANET_CELL, respect=56, items={}, captain={"silver": 51, "food": 37},
                       planets={PLANET: planet_record(stock={"food": 105, "water": 115, "elements": {"iridium": 4, "rhodium": 2}})})


def buy() -> dict:
    probe = m4_report([{"player": at(PLANET_CELL, respect=50, items={"iridium": 1})}])
    return case("buy", ["report", "snapshot"], {"planet_id": PLANET, "turn": 1, "item": "food", "n": 10, "unit_price": 2.0, "band": 20.0, "spg": 20, "respect_before": 50,
                                               "baseline_turn": probe["per_turn"][0], "stock_before": 100, "regen": 5, "cap": 200, "turns_total": 3, "coins_before": COINS,
                                               "what": "fixture buy food 10"},
                report=m4_report(market_turns()), snapshot=market_snapshot())


def sell() -> dict:
    return case("sell", ["report", "snapshot"], {"planet_id": PLANET, "turn": 2, "element": "iridium", "n": 1, "value": 30.0, "pct": 60.0, "band": 20.0, "spg": 20,
                                                "respect_before": 53, "what": "fixture sell iridium 1"},
                report=m4_report(market_turns()), snapshot=market_snapshot())


def buy_rejected() -> dict:
    cost3 = int(math.floor(200 * 2 * (1 - 0.2 * 0.56) + 1e-9))
    return case("buy_rejected", ["report"], {"planet_id": PLANET, "turn": 3, "item": "food", "n": 200, "cost_options": [cost3, 200], "stock_numbers": [100, 105, 200],
                                            "what": "fixture buy food 200"}, report=m4_report(market_turns()))


def price_key() -> dict:
    cost = 63                                          # 10 × 7 × 0.9: 13 silver short, one gold converts
    t1 = {"trades": [trade(PLANET, "buy", {"silver": cost}, {"food": 10}, factor=0.9)], "reputation": [rep(PID, "FR-07", 0, 3)],
          "events": [f"Bought 10 food from {PLANET_NAME} for {cost} silver (OUT-07)"], "player": at(PLANET_CELL, respect=53, gold=4, silver=7, food=39)}
    return case("buy", ["report", "snapshot"], {"planet_id": PLANET, "turn": 1, "item": "food", "n": 10, "unit_price": 7.0, "band": 20.0, "spg": 20, "respect_before": 50,
                                               "baseline_turn": None, "stock_before": None, "regen": 5, "cap": 200, "turns_total": 1, "coins_before": COINS,
                                               "what": "fixture food_buy 7 (D-49)"}, report=m4_report([t1]), snapshot=None)


def buy_ship() -> dict:
    new = "s-195"
    t1 = {"trades": [trade(PLANET, "ship", {"gold": 40}, {"ship": "kestrel"})], "reputation": [rep(PID, "FR-07", 0, 3)],
          "events": [f"Bought a Kestrel from {PLANET_NAME} for 40 gold (OUT-07)"], "player": at(PLANET_CELL, respect=53, gold=0, silver=0, ships=2)}
    other = {"name": "Pale Cormorant", "class": "kestrel", "captain": PID, "torpedoes": 6, "phasers": 3.0, "shield_max": 60.0, "shield_current": 60.0, "engine": 4.0, "warp": 3.0,
             "position": [HOME[0], HOME[1], list(PLANET_CELL)], "orders_queued": 0, "orders": [], "hazard": None, "move_remaining": None, "in_battle": None, "target": None}
    snap = m4_snapshot(1, player_cell=PLANET_CELL, respect=53, captain={"gold": 0, "silver": 0}, planets={PLANET: planet_record()}, ships_owned=[PSHIP, new], extra_ships={new: other})
    return case("buy_ship", ["report", "snapshot"], {"planet_id": PLANET, "turn": 1, "cls": "kestrel", "price_gold": 40, "gold_before": 40, "what": "fixture buy ship"},
                report=m4_report([t1]), snapshot=snap)


def buy_ship_poor() -> dict:
    t1 = {"trades": [trade(PLANET, "ship", {"gold": 40}, {"ship": "kestrel"}, accepted=False, reason="not enough gold: 40 needed, 10 held (PRC-05)")],
          "events": ["script order 'buy ship kestrel' rejected: not enough gold: 40 needed, 10 held (PRC-05)"], "player": at(PLANET_CELL, respect=50, gold=10, silver=0, ships=1)}
    return case("buy_ship_rejected", ["report"], {"planet_id": PLANET, "turn": 1, "cls": "kestrel", "price_gold": 40, "gold_held": 10, "what": "fixture buy ship poor"},
                report=m4_report([t1]))


# --------------------------------------------------------------------------- smoke/39 — disposition and barter

def refused() -> dict:
    t1 = {"trades": [trade(PLANET, "buy", {"silver": 20}, {"food": 10}, accepted=False, reason=f"{PLANET_NAME} refuses to trade: respect 12 < 20 (EF-04)")],
          "events": [f"script order 'buy food 10' rejected: {PLANET_NAME} refuses to trade: respect 12 < 20 (EF-04)"], "player": at(PLANET_CELL, respect=12)}
    snap = m4_snapshot(1, player_cell=PLANET_CELL, respect=12, planets={PLANET: planet_record(disposition_to_player=-76, trades_with_player=False)})
    return case("refused", ["report", "snapshot"], {"planet_id": PLANET, "turn": 1, "respect": 12, "refuse_respect": 20, "what": "fixture refused"}, report=m4_report([t1]), snapshot=snap)


def factor_100() -> dict:
    t1 = {"trades": [trade(PLANET, "buy", {"silver": 16}, {"food": 10}, factor=0.8)], "reputation": [rep(PID, "FR-07", 0, 3)],
          "events": [f"Bought 10 food from {PLANET_NAME} for 16 silver (OUT-07)"], "player": at(PLANET_CELL, respect=100, silver=34, food=39)}
    return case("factor_at", ["report"], {"planet_id": PLANET, "turn": 1, "respect": 100, "band": 20.0, "unit_price": 2.0, "n": 10, "spg": 20, "coins_before": COINS,
                                         "what": "fixture factor at 100"}, report=m4_report([t1]))


def barter_planet(respect: int) -> dict:
    f = E.barter_factor(E.disposition(respect), 20.0)
    n_acc, n_rej = E.barter_bounds(30.0, 2.0, f, cap=100)
    worth = int(math.floor(n_rej * 2.0 * f + 1e-9))
    turns = [{"trades": [trade(PLANET, "barter", {"iridium": 1}, {"food": n_rej}, accepted=False, reason=f"{PLANET_NAME} declines: {n_rej} food worth {worth} > iridium worth 30 (TR-06)")],
              "events": [f"script order 'trade {PLANET} give iridium:1 for food:{n_rej}' rejected: {PLANET_NAME} declines (TR-06)"], "player": at(PLANET_CELL, respect=respect, items={"iridium": 1})},
             {"trades": [trade(PLANET, "barter", {"iridium": 1}, {"food": n_acc})], "reputation": [rep(PID, "FR-07", 0, 3)],
              "events": [f"Traded 1 iridium to {PLANET_NAME} for {n_acc} food (OUT-07)"], "player": at(PLANET_CELL, respect=min(100, respect + 3), items={}, food=28 + n_acc)}]
    snap = m4_snapshot(2, player_cell=PLANET_CELL, respect=min(100, respect + 3), items={}, planets={PLANET: planet_record(disposition_to_player=E.disposition(respect + 3))})
    return case("barter", ["report", "snapshot"], {"counterparty": PLANET, "give": ["iridium", 1], "want_item": "food", "n_accept": n_acc, "n_reject": n_rej, "accept_turn": 2,
                                                  "reject_turn": 1, "factor": f, "trade_delta": 3, "respect_before": respect, "what": f"fixture barter at respect {respect}"},
                report=m4_report(turns), snapshot=snap)


# --------------------------------------------------------------------------- smoke/40 — upgrades

def upgrade_pairs(upgrades: dict, caps: dict, big: bool = False) -> list[dict]:
    pairs, turn = [], 1
    for comp in E.STAT_OF:
        if big:
            el = sorted(upgrades[comp])[0]
            amt = 400.0 if caps[comp] is None else 2.0 * caps[comp]
            pairs.append({"turn": turn, "component": comp, "element": el, "amount": amt})
            turn += 1
            continue
        for el, amt in sorted(upgrades[comp].items()):
            pairs.append({"turn": turn, "component": comp, "element": el, "amount": amt})
            turn += 1
    return pairs


def upgrades_case(big: bool) -> dict:
    pairs = upgrade_pairs(FIXTURE_M4["upgrades"], CAPS, big=big)
    stats = dict(BASE)
    items = {el: 1 for el in FIXTURE_M4["elements"]}
    turns = []
    for p in pairs:
        stat = E.STAT_OF[p["component"]]
        before = stats[stat]
        after = before + p["amount"]
        cap = CAPS[p["component"]]
        capped = cap is not None and after > cap + 1e-9
        if capped:
            after = cap
        stats[stat] = after
        items[p["element"]] -= 1
        items = {k: v for k, v in items.items() if v}
        turns.append({"upgrades": [{"component": p["component"], "element": p["element"], "before": before, "after": after, "capped": capped}],
                      "events": [f"{p['component'].capitalize()} upgraded with {p['element']}: {before:g} → {after:g}" + (" (capped)" if capped else "") + " (OUT-08)"],
                      "player": {"items": dict(items)}})
    wrong = None
    if not big:
        wrong = {"turn": len(pairs) + 1, "component": "shields", "element": "adamantine"}
        turns.append({"events": ["script order 'upgrade shields with adamantine' rejected: adamantine does not improve shields (CMD-13)"], "player": {"items": dict(items)}})
    ship = {"torpedoes": stats["torpedoes"], "phasers": stats["phasers"], "shield_max": stats["shield_max"], "shield_current": stats["shield_max"], "engine": stats["engine"],
            "warp": stats["warp"]}
    return case("upgrades", ["report", "snapshot"], {"pairs": pairs, "caps": CAPS, "base": BASE, "wrong": wrong, "what": "fixture upgrades" + (" caps" if big else "")},
                report=m4_report(turns), snapshot=m4_snapshot(len(turns), items=items, ship=ship))


# --------------------------------------------------------------------------- smoke/41 — barter with an AI captain

def barter_ai(respect: int) -> dict:
    f = E.barter_factor(E.disposition(respect), 10.0)
    n_acc, n_rej = E.barter_bounds(30.0, 1.0, f)
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "kestrel", "cell": list(CELL), "silver": 50}
    turns = [{"trades": [trade(SPAWN_C, "barter", {"iridium": 1}, {"silver": n_rej}, accepted=False, reason=f"Cinder Lark declines: {n_rej} silver × {f:.2f} > iridium worth 30 (TR-06, EF-03)")],
              "events": [f"script order 'trade {SPAWN_C} give iridium:1 for silver:{n_rej}' rejected: Cinder Lark declines (TR-06)"], "player": {"respect": respect, "items": {"iridium": 1}}},
             {"trades": [trade(SPAWN_C, "barter", {"iridium": 1}, {"silver": n_acc})], "reputation": [rep(PID, "FR-07", 0, 3)],
              "events": [f"Traded 1 iridium to Cinder Lark for {n_acc} silver (OUT-07)"], "player": {"respect": respect + 3, "items": {}, "silver": 50 + n_acc}}]
    snap = m4_snapshot(2, respect=respect + 3, items={}, captain={"silver": 50 + n_acc}, spawns=[sp])
    snap["captains"][SPAWN_C]["items"] = {"iridium": 1}
    snap["captains"][SPAWN_C]["silver"] = 50 - n_acc
    return case("barter_ai", ["report", "snapshot"], {"spawn": {"captain": SPAWN_C, "ship": SPAWN_S, "silver": 50}, "give": ["iridium", 1], "n_accept": n_acc, "n_reject": n_rej,
                                                     "accept_turn": 2, "reject_turn": 1, "factor": f, "trade_delta": 3, "respect_before": respect, "player_silver_before": 50,
                                                     "what": f"fixture AI barter at respect {respect}"},
                report=m4_report(turns, spawned=[sp]), snapshot=snap)


# --------------------------------------------------------------------------- smoke/42 — planets in combat

BIG_SHIP = {"torpedoes": 60, "shield_max": 200.0, "shield_current": 200.0}


def planet_record_case() -> dict:
    return case("planet_record", ["snapshot"], {"planet_id": PLANET, "stats": {"shield_max": 80.0, "torpedoes": 10.0, "phasers": 4.0}, "what": "fixture planet record"},
                snapshot=m4_snapshot(1, player_cell=PLANET_CELL, planets={PLANET: planet_record()}))


def planet_fight() -> dict:
    sides = [PID, PLANET]
    strength = {PID: 200 + 60 * 15 + 45.0, PLANET: 290.0}
    turns, shields_p, shields_me = [], 80.0, 200.0
    for i in range(1, 4):
        rows = []
        if i == 1:
            shields_p -= 15
            rows.append(ex(PSHIP, PLANET, "phaser", 0, True, 15, shields_p))
        hit = i != 2
        if hit:
            shields_me -= 15
        rows.append(ex(PLANET, PSHIP, "torpedo", 0, hit, 15 if hit else 0, shields_me))
        shields_me -= 20
        rows.append(ex(PLANET, PSHIP, "phaser", 0, True, 20, shields_me))
        t = {"battles": [battle(sides, 1, PID, strength, rows)], "player": at(PLANET_CELL, respect=45 if i >= 1 else 50, shield_current=shields_me, shield_max=200.0, in_battle=True)}
        if i == 1:
            t["reputation"] = [rep(PID, "FR-06", 0, -5)]
            t["planet_events"] = [{"planet": PLANET, "event": "attacked", "by": PID}]
        turns.append(t)
    snap = m4_snapshot(3, player_cell=PLANET_CELL, respect=45, ship=dict(BIG_SHIP, shield_current=shields_me, in_battle="b-001", target=PLANET),
                       planets={PLANET: planet_record(shields=shields_p, in_battle="b-001")})
    return case("planet_fight", ["report", "snapshot"], {"planet_id": PLANET, "planet_cell": PLANET_CELL, "distance": 0, "phasers": 4.0, "per_unit": 5.0, "torpedo_damage": 15.0,
                                                        "start_fight_delta": 5, "respect_start": 50, "until_turn": 3, "what": "fixture planet fight"},
                report=m4_report(turns), snapshot=snap)


def planet_destroyed() -> dict:
    sides = [PID, PLANET]
    strength = {PID: 200 + 60 * 15 + 45.0, PLANET: 290.0}
    n = 30
    rows, shields = [], 80.0
    for i in range(n):
        hit = i % 3 != 2
        if hit:
            shields = max(0.0, shields - 15)
        rows.append(ex(PSHIP, PLANET, "torpedo", 0, hit, 15 if hit else 0, shields))
    rows.append(ex(PSHIP, PLANET, "phaser", 0, True, 15, 0.0))
    rows.append(ex(PLANET, PSHIP, "torpedo", 0, True, 15, 185.0))
    rows.append(ex(PLANET, PSHIP, "phaser", 0, True, 20, 165.0))
    b = battle(sides, 2, PID, strength, rows, ended="destroyed", victor=PID, transfer={"gold": 0, "silver": 0, "items": {"iridium": 3, "rhodium": 2}, "pct": 100})
    turns = [{"player": at(PLANET_CELL, respect=50, shield_current=200.0, shield_max=200.0)},
             {"battles": [b], "reputation": [rep(PID, "FR-06", 0, -5)], "planet_events": [{"planet": PLANET, "event": "attacked", "by": PID}, {"planet": PLANET, "event": "destroyed", "by": PID}],
              "events": [f"{PLANET_NAME} destroyed — looted 100 food, 100 water, 3 iridium, 2 rhodium, 9 torpedoes (OUT-06)"],
              "player": at(PLANET_CELL, respect=45, shield_current=165.0, shield_max=200.0, food=128, water=128, items={"iridium": 3, "rhodium": 2})},
             {"player": at(PLANET_CELL, respect=45, shield_current=170.0, shield_max=200.0, food=127, water=127, items={"iridium": 3, "rhodium": 2})},
             {"player": at(PLANET_CELL, respect=45, shield_current=175.0, shield_max=200.0, food=126, water=126, items={"iridium": 3, "rhodium": 2})}]
    ship = dict(BIG_SHIP, torpedoes=60 - n + 9, shield_current=175.0)
    snap = m4_snapshot(4, player_cell=PLANET_CELL, respect=45, items={"iridium": 3, "rhodium": 2}, ship=ship, planets={}, remove_planet_terrain=True,
                       captain={"food": 126, "water": 126})
    deltas = sorted({min(200, 100 + 5 * r) - u for r in (0, 1, 2) for u in (0, 1)})
    return case("planet_destroyed", ["report", "snapshot"], {"planet_id": PLANET, "key": KEY, "planet_cell": PLANET_CELL, "player_before": {"ship": PSHIP, "torpedoes": 60},
                                                            "stock": {"food": deltas, "water": deltas, "elements": {"iridium": 3, "rhodium": 2}}, "planet_torpedoes": 10,
                                                            "what": "fixture planet destroyed"}, report=m4_report(turns), snapshot=snap)


def planet_peace() -> dict:
    sides = [PID, PLANET]
    strength = {PID: 195.0, PLANET: 115.0}
    pct = E.peace_pct(85.0, 50.0, 50.0)                 # 20
    t1 = {"battles": [battle(sides, 1, PID, strength, [ex(PSHIP, PLANET, "phaser", 0, True, 15, 85.0), ex(PLANET, PSHIP, "phaser", 0, True, 5, 55.0)])],
          "reputation": [rep(PID, "FR-06", 0, -5)], "planet_events": [{"planet": PLANET, "event": "attacked", "by": PID}],
          "player": at(PLANET_CELL, respect=45, shield_current=55.0)}
    t2 = {"battles": [battle(sides, 1, PID, strength, [ex(PLANET, PSHIP, "phaser", 0, True, 5, 50.0)])], "events": [f"peace offer to {PLANET_NAME} stands (OUT-05)"],
          "player": at(PLANET_CELL, respect=45, shield_current=50.0)}
    t3 = {"battles": [battle(sides, 1, PID, strength, [], ended="peace", victor=PLANET, transfer={"gold": 1, "silver": 10, "items": {}, "pct": pct})],
          "events": [f"{PLANET_NAME} accepts peace: {pct} % of your coins (OUT-05)"], "planet_events": [{"planet": PLANET, "event": "peace", "by": PLANET}],
          "player": at(PLANET_CELL, respect=45, shield_current=50.0, gold=4, silver=40)}
    t4 = {"player": at(PLANET_CELL, respect=45, shield_current=55.0, gold=4, silver=40)}
    snap = m4_snapshot(4, player_cell=PLANET_CELL, respect=45, captain={"gold": 4, "silver": 40}, planets={PLANET: planet_record(shields=85.0, shield_max=100.0, torpedoes=0, phasers=1.0)})
    return case("planet_peace", ["report", "snapshot"], {"planet_id": PLANET, "loot_max": 50.0, "offer_turn": 2, "coins_before": COINS, "planet_shield_max": 100.0,
                                                        "what": "fixture planet peace"}, report=m4_report([t1, t2, t3, t4]), snapshot=snap)


# --------------------------------------------------------------------------- smoke/43 — EF-05

def planet_opens() -> dict:
    sides = [PLANET, PID]
    strength = {PID: 195.0, PLANET: 290.0}
    t1 = {"player": at(NEIGHBOUR, respect=10)}
    t2 = {"battles": [battle(sides, 2, PLANET, strength, [ex(PLANET, PSHIP, "torpedo", 1, True, 15, 45.0), ex(PLANET, PSHIP, "phaser", 1, True, 13.33, 31.67)])],
          "planet_events": [{"planet": PLANET, "event": "opened_fire", "by": PLANET}], "events": [f"{PLANET_NAME} opens fire on Lantern: respect 10 < 20 (EF-05)"],
          "player": at(NEIGHBOUR, respect=10, shield_current=31.67, in_battle=True)}
    t3 = {"battles": [battle(sides, 2, PLANET, strength, [ex(PLANET, PSHIP, "torpedo", 1, False, 0, 31.67), ex(PLANET, PSHIP, "phaser", 1, True, 13.33, 18.34)])],
          "player": at(NEIGHBOUR, respect=10, shield_current=18.34, in_battle=True)}
    snap = m4_snapshot(3, player_cell=NEIGHBOUR, respect=10, planets={PLANET: planet_record(disposition_to_player=-80, trades_with_player=False, in_battle="b-001")})
    return case("planet_opens", ["report", "snapshot"], {"planet_id": PLANET, "within": 2, "respect": 10, "respect_max": 20, "ratio": 290 / 195, "ratio_min": 1.3,
                                                        "what": "fixture planet opens"}, report=m4_report([t1, t2, t3]), snapshot=snap)


def planet_holds(respect: int, why: str) -> dict:
    turns = [{"player": at(NEIGHBOUR, respect=respect)} for _ in range(10)]
    snap = m4_snapshot(10, player_cell=NEIGHBOUR, respect=respect, planets={PLANET: planet_record(disposition_to_player=E.disposition(respect), trades_with_player=respect >= 20)})
    return case("planet_holds", ["report", "snapshot"], {"planet_id": PLANET, "turns": 10, "why": why, "what": f"fixture planet holds at respect {respect}"},
                report=m4_report(turns), snapshot=snap)


# --------------------------------------------------------------------------- smoke/44, 45, 12 — the AI economy

def ai_turns(n: int, *, mutinies_at: tuple[int, ...] = ()) -> list[dict]:
    turns = []
    for t in range(1, n + 1):
        captains = 193 + ((t * 7) % 11) - 5                              # 188…198, within 0.7…1.3 × 193
        econ = {"purchases": 1 if t % 5 == 0 else 0, "sales": 1 if t % 9 == 0 else 0, "restock_trips": 1 if t % 11 == 0 else 0, "docked": t % 4}
        pk = [{"captain": f"c-{(t * 13) % 160 + 1:03d}", "element": "osmium", "units": 1, "rarity": "normal"}] if t % 25 == 0 else []
        turns.append({"captains": captains, "ai_economy": econ, "pickups": pk, "tag": "ai200", "player": {"fear": 10, "respect": 10}})
    return turns


def ai200_report(**final) -> dict:
    fin = {"ai_mutinies": 5}
    fin.update(final)
    return m4_report(ai_turns(200), final=fin)


def ai_supplies() -> dict:
    return case("ai_supplies", ["report"], {"turns": 200, "what": "fixture AI supplies"}, report=ai200_report())


def knobs_live() -> dict:
    off = m4_report(ai_turns(43), final={"ai_mutinies": 150})
    return case("knobs_live", ["report", "report2"], {"what": "fixture knobs live"}, report=ai200_report(), report2=off)


def ai_economy() -> dict:
    return case("ai_economy", ["report"], {"turns": 200, "what": "fixture AI economy"}, report=ai200_report())


def long_run() -> dict:
    turns = []
    for t in range(1, 41):
        b = [battle(["c-010", "c-011"], t, "c-010", {"c-010": 255.0, "c-011": 455.0}, [ex("s-010", "s-011", "phaser", 1, True, 10, 70)], bid=f"b-{t:03d}", ended="escape")] if t % 10 == 0 else []
        tr = [trade("p-020", "buy", {"silver": 18}, {"food": 10})] if t % 3 == 0 else []
        turns.append({"battles": b, "trades": tr, "captains": 193 + (t % 3), "ai_economy": {"purchases": 1 if t % 3 == 0 else 0, "sales": 0, "restock_trips": 0, "docked": 1}, "tag": "long"})
    rpt = m4_report(turns, final={"ai_mutinies": 2})
    snap = m4_snapshot(40, spawns=[{"ship": "s-010", "captain": "c-010"}, {"ship": "s-011", "captain": "c-011"}])
    return {"report": rpt, "snapshot": snap}


# --------------------------------------------------------------------------- smoke/46 — M3 fix-ups

def fire_all() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "kestrel", "cell": [13, 11]}
    sides = [PID, SPAWN_C]
    rows = [ex(PSHIP, SPAWN_S, "torpedo", 1, True, 15, 45.0), ex(PSHIP, SPAWN_S, "phaser", 1, True, 10, 35.0), ex(SPAWN_S, PSHIP, "torpedo", 1, False, 0, 60.0),
            ex(SPAWN_S, PSHIP, "phaser", 1, True, 10, 50.0)]
    t1 = {"battles": [battle(sides, 1, PID, {PID: 195.0, SPAWN_C: 195.0}, rows)], "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5, "shield_current": 50.0, "in_battle": True}}
    return case("fire_all", ["report", "snapshot"], {"what": "fixture fire all"}, report=m4_report([t1], spawned=[sp]), snapshot=m4_snapshot(1, respect=5, spawns=[sp]))


def help_fire() -> dict:
    text = ("fire <target> | fire torpedo <target> [n] | fire phaser <target>\n  Attacks a ship or planet in range; fire <target> fires one torpedo and the phasers.\n"
            "  torpedo: 31 damage, hits 58 % vs engine 4; phasers: 21 / 14 / 7 damage at distance 0 / 1 / 2\n  queued - CMD-08")
    return case("help_fire", ["text"], {"torpedo_damage": 31.0, "hit_pct": 58, "phaser_damages": [21.0, 14.0, 7.0], "what": "fixture help fire"}, text=text)


def class_mix() -> dict:
    spawns = [{"ship": f"s-9{i:02d}", "captain": f"c-9{i:02d}", "class": "kestrel", "cell": [2 + i, 3], "name": n} for i, n in enumerate(("Oriel Vance", "Tamsin Harlow", "Ragnar Penhallow"))]
    return case("class_mix", ["snapshot"], {"cls": "kestrel", "what": "fixture class mix"}, snapshot=m4_snapshot(1, spawns=spawns))


def ai_trace() -> dict:
    battle_log = ("T1 c-163 Elin Kestle: in battle b-001, weaker (strength 220 v 245) -> no peace; fires torpedo+phaser at s-000 (CM-10, D-125)\n"
                  "T1 c-020 Ragnar Penhallow: roam due -> warp to 3,4/8,9 (CFG-73)\n")
    all_log = battle_log + ("T1 c-001 Tamsin Harlow: no enemy to fight, no roam due -> random walk 13,19 -> 13,22 (up to 6 cell(s)) (OV-10)\n"
                            "T2 c-002 Ansel Thorne: no enemy to fight, no roam due -> random walk 5,18 -> 11,21 (up to 6 cell(s)) (OV-10)\n")
    return case("ai_trace", ["log_battle", "log_all"], {"what": "fixture ai_trace"}, log_battle=battle_log, log_all=all_log)


FIRST_NAMES = ("Avel", "Bram", "Cerys", "Dael", "Eirlys", "Faelan", "Garnet", "Hollis", "Idris", "Jessamy", "Kerensa", "Lowen", "Merryn", "Nessa", "Oswin", "Perran",
               "Quenby", "Rowan", "Sorrel", "Tamsin", "Ursel", "Vesper", "Wynn", "Xanthe", "Yestin", "Zennor", "Alder", "Briar", "Corin", "Dunstan", "Elin", "Fiske")


def first_names() -> dict:
    spawns = [{"ship": f"s-8{i:02d}", "captain": f"c-8{i:02d}", "class": "corsair", "cell": [1 + i % 20, 1 + i // 20], "name": f"{n} Marrow"} for i, n in enumerate(FIRST_NAMES)]
    return case("first_names", ["snapshot"], {"minimum": 30, "what": "fixture first names"}, snapshot=m4_snapshot(1, spawns=spawns))


# --------------------------------------------------------------------------- smoke/48 — save

def save_economy() -> dict:
    others = {"p-013": planet_record(name="Calder", cell=[22, 5], sector="3,4/7,10", disposition_to_player=-76, trades_with_player=False)}
    snap = m4_snapshot(3, player_cell=PLANET_CELL, respect=12, ship={"engine": 4.5}, planets=others, remove_planet_terrain=True)
    return case("save_economy", ["snapshot"], {"destroyed_planet": PLANET, "refused_respect": 12, "upgraded": {"engine": 4.5}, "what": "fixture save economy"}, snapshot=snap)


# --------------------------------------------------------------------------- live/10 — the market driver's observations

def market_bus() -> dict:
    obs = {"hello": {"turn_no": 0, "seed": 1234}, "player_id": PID, "ship_id": PSHIP, "planet": {"id": PLANET, "name": PLANET_NAME, "trades": True}, "cell": PLANET_CELL,
           "before": {"food": 30, "gold": 5, "silver": 50, "respect": 50}, "after": {"food": 34, "gold": 5, "silver": 41},
           "buy_reply": {"topic": "order.accepted", "reason": ""}, "resolved": {"turn_no": 1, "types": ["movement", "trade", "upkeep"], "trade_texts": [f"Bought 5 food from {PLANET_NAME} for 9 silver"]},
           "after_first": None, "notes": []}
    return case("market_bus", ["obs"], {"n": 5, "cost_options": [9, 5], "spg": 20}, obs=obs)


# --------------------------------------------------------------------------- assembly

def good_m4() -> dict:
    return {"item_hidden": item_hidden(), "item_scan": item_scan(), "item_pickup": item_pickup(NORMAL_ITEM, False), "item_special": item_pickup(SPECIAL_ITEM, True),
            "no_respawn": no_respawn(), "buy": buy(), "sell": sell(), "buy_rejected": buy_rejected(), "price_key": price_key(), "buy_ship": buy_ship(),
            "buy_ship_poor": buy_ship_poor(), "refused": refused(), "factor_100": factor_100(), "barter_planet_low": barter_planet(20), "barter_planet_high": barter_planet(90),
            "upgrades": upgrades_case(False), "upgrades_caps": upgrades_case(True), "barter_ai_low": barter_ai(20), "barter_ai_high": barter_ai(90),
            "planet_record": planet_record_case(), "planet_fight": planet_fight(), "planet_destroyed": planet_destroyed(), "planet_peace": planet_peace(),
            "planet_opens": planet_opens(), "planet_holds": planet_holds(60, "respect 60 ≥ planet_attack_respect_max 20"),
            "planet_holds_strong": planet_holds(10, "the ship's strength 1135 is not below planet strength 290 / attack_ratio_min 1.3"),
            "ai_supplies": ai_supplies(), "knobs_live": knobs_live(), "ai_economy": ai_economy(), "fire_all": fire_all(), "help_fire": help_fire(), "class_mix": class_mix(),
            "ai_trace": ai_trace(), "first_names": first_names(), "save_economy": save_economy(), "market_bus": market_bus()}


def broken_m4(good: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}

    c = copy.deepcopy(good["item_hidden"])              # smoke/37 — the unscanned stack is picked up at once
    t1 = c["report"]["per_turn"][0]
    t1["events"] = ["script order 'pickup iridium' accepted: picked up 2 iridium"]
    t1["pickups"] = [{"captain": PID, "element": "iridium", "units": 2, "rarity": "normal"}]
    t1["player"]["items"] = {"iridium": 2}
    c["snapshot"]["sectors"][KEY]["items"] = []
    c["snapshot"]["captains"][PID]["items"] = {"iridium": 2}
    out["sim_item_auto_pickup"] = c

    c = copy.deepcopy(good["buy"])                      # smoke/38 — the list price is charged, the respect factor ignored
    t1 = c["report"]["per_turn"][0]
    t1["trades"][0]["gave"], t1["trades"][0]["price_factor"] = {"silver": 20}, 1.0
    t1["player"]["silver"] = 30
    t1["events"] = [f"Bought 10 food from {PLANET_NAME} for 20 silver (OUT-07)"]
    out["sim_price_ignored"] = c

    c = copy.deepcopy(good["barter_planet_low"])        # smoke/39 — the planet accepts above the threshold
    t1 = c["report"]["per_turn"][0]
    t1["trades"][0].update(accepted=True, reason=None)
    t1["events"] = [f"Traded 1 iridium to {PLANET_NAME} for 14 food (OUT-07)"]
    out["sim_trade_wrong_threshold"] = c

    c = copy.deepcopy(good["upgrades_caps"])            # smoke/40 — shields pass shield_cap
    for t in c["report"]["per_turn"]:
        for u in t["upgrades"]:
            if u["component"] == "shields":
                u["after"], u["capped"] = 260.0, False
        t["events"] = [e.replace("60 → 100 (capped)", "60 → 260") for e in t["events"]]
    c["snapshot"]["ships"][PSHIP]["shield_max"] = 260.0
    out["sim_upgrade_uncapped"] = c

    c = copy.deepcopy(good["barter_ai_low"])            # smoke/41 — the AI captain accepts above its threshold
    t1 = c["report"]["per_turn"][0]
    t1["trades"][0].update(accepted=True, reason=None)
    out["sim_barter_ai_wrong"] = c

    c = copy.deepcopy(good["planet_destroyed"])         # smoke/42 — the volley lands but the planet never dies
    t2 = c["report"]["per_turn"][1]
    t2["planet_events"] = [e for e in t2["planet_events"] if e["event"] != "destroyed"]
    for b in t2["battles"]:
        b.update(ended=None, victor=None, transfer=None)
    for t in c["report"]["per_turn"][1:]:
        t["player"]["items"] = {}
    c["report"]["final"]["planets_destroyed"] = 0
    c["snapshot"]["planets"] = {PLANET: planet_record(shields=0.0)}
    out["sim_planet_immortal"] = c

    c = copy.deepcopy(good["planet_holds"])             # smoke/43 — the planet opens on a respect-60 ship
    t3 = c["report"]["per_turn"][2]
    t3["battles"] = [battle([PLANET, PID], 3, PLANET, {PID: 195.0, PLANET: 290.0}, [ex(PLANET, PSHIP, "torpedo", 1, True, 15, 45.0)])]
    t3["planet_events"] = [{"planet": PLANET, "event": "opened_fire", "by": PLANET}]
    out["sim_planet_too_wild"] = c

    c = copy.deepcopy(good["ai_supplies"])              # smoke/44 — a third of the AI mutinies
    c["report"]["final"]["ai_mutinies"] = 60
    out["sim_ai_starves"] = c

    c = copy.deepcopy(good["ai_economy"])               # smoke/45 — nobody sells anything
    for t in c["report"]["per_turn"]:
        t["ai_economy"]["sales"] = 0
    out["sim_ai_no_trade"] = c

    c = copy.deepcopy(good["fire_all"])                 # smoke/46 — `fire <id>` fires the phasers only
    for b in c["report"]["per_turn"][0]["battles"]:
        b["exchanges"] = [e for e in b["exchanges"] if not (e["attacker"] == PSHIP and e["weapon"] == "torpedo")]
    out["sim_fire_all_one_row"] = c

    c = copy.deepcopy(good["save_economy"])             # smoke/48 — the destroyed planet is back after load
    c["snapshot"]["planets"][PLANET] = planet_record(disposition_to_player=-76, trades_with_player=False)
    out["sim_save_planet_resurrects"] = c
    return out
