"""verify/_economy.py — M4 checkers shared by smoke_test.py and live_test.py (specs/M4.md § 4, CONTRACTS C11).

Same split as _checks.py and _combat.py: **pure checkers** (functions of a C2+C9+C10+C11 report, a
snapshot, bus observations or rendered text → a list of problem strings) and **thin I/O adapters**
(the bus market driver) with no pass/fail logic.  Scenarios are *plans* built from world_index
(planets, items), the probe snapshot and the ini plus the C11 debug flags (`--give`, `--coins`,
`--respect`, `--spawn`, `--ai passive`); every number comes from the ini the gate was handed,
never a literal (M4 § 4).

Conventions the gate relies on, stated in every message that uses them (D-148):
  * prices: a price is floored to whole silver after the respect factor; the gate accepts the floor
    on the total or per unit (D-142 'to the unit') and judges coins in silver-equivalent
    (gold × silver_per_gold + silver), so gold conversion is the engine's business;
  * barter list values: food / water / torpedoes are valued at the planet's `*_buy` price
    (PRC-01…03), elements at their [elements] value; the counterparty accepts when what it receives
    ≥ what it gives × (1 − band/100 × disposition/100) (D-142); the gate tests the boundary one
    step away from any rounding ambiguity;
  * an OUT-07 trade event is a C2 event string naming the traded item and the silver amount;
  * a refused or rejected trade shows either as a `trades[]` row with accepted false and a reason,
    or as a rejected-order event (D-128 (3)); EF-04 refusals name 'EF-04' or 'refuse';
  * `pickups[]` rows carry `captain` and list every captain's pickups (AI included); `final.pickups`
    counts them all [ASSUMED C11 addition];
  * a planet answers a peace offer like an AI captain (accepts when weaker); the victor is the side
    that did not offer (CB-03) and X % of the loser's coins / stock move (D-46, D-145);
  * planets never hold coins (D-140): what a planet loses on peace or destruction is stock.
"""
from __future__ import annotations

import copy
import math
import re
import time
from collections import Counter
from typing import Any, Callable

from _checks import (BusClient, BusObservations, _close, _get, _norm, _short, chebyshev, event_text, glyph_positions, player_ship,
                     report_shape_errors)
from _combat import (REJECTED_RULE, _prefix, _turns, all_battles, battle_between, battles_at, events_mentioning, exchanges_at,
                     peace_pct, phaser_damage, reputation_at, spawned_entries, strength)

TRADE_EVENT_RULE = "an OUT-07 trade event is a C2 event string naming the traded item and the silver amount (C3 type 'trade'; D-148)"
ROUNDING_RULE = "a price is floored to whole silver after the factor, on the total or per unit (D-142; D-148) — judged in silver-equivalent"
LIST_PRICE_RULE = ("in a barter food / water / torpedoes are valued at the planet's *_buy price (PRC-01…03) and elements at their [elements] value; "
                   "accept when received ≥ given × (1 − band/100 × disposition/100) (D-142; D-148)")
REFUSAL_RULE = "a refused / rejected trade is a trades[] row with accepted false and a reason, or a C2 event string containing 'rejected' (D-128 (3); D-148)"
PICKUP_RULE = "pickups[] rows carry 'captain' and list every captain's pickups; final.pickups counts them all (C11 [ASSUMED]; D-148)"
PLANET_PEACE_RULE = "a planet answers a peace offer like an AI captain (accepts when weaker); the victor is the side that did not offer (CB-03, D-145; D-148)"
TRADE_KEYS = ("with", "kind", "gave", "received", "accepted", "reason", "price_factor")
PLANET_KEYS = ("name", "president", "cell", "sector", "shields", "shield_max", "torpedoes", "phasers", "stock", "disposition_to_player",
               "trades_with_player", "in_battle", "destroyed")
SUPPLY_ITEMS = ("food", "water", "torpedo")


# --------------------------------------------------------------------------- formulas (tabs 11 / 12 / 14; D-142)

def disposition(respect: float) -> int:
    """PL-06 / D-142: disposition = 2 × respect − 100."""
    return int(round(2 * float(respect) - 100))


def price_factor(respect: float, band: float, *, selling: bool) -> float:
    """D-142: buying from a planet 1 − band/100 × respect/100; selling to it 1 + …"""
    f = float(band) / 100.0 * float(respect) / 100.0
    return 1.0 + f if selling else 1.0 - f


def barter_factor(disp: float, band: float) -> float:
    """TR-06 / D-142: the counterparty accepts when received ≥ given × (1 − band/100 × disposition/100)."""
    return 1.0 - float(band) / 100.0 * float(disp) / 100.0


def price_options(unit_price: float, n: int, factor: float) -> set[int]:
    """Both floors (ROUNDING_RULE): floor(unit × n × factor) and n × floor(unit × factor)."""
    total = int(math.floor(float(unit_price) * int(n) * float(factor) + 1e-9))
    per_unit = int(n) * int(math.floor(float(unit_price) * float(factor) + 1e-9))
    return {total, per_unit}


def element_sell_options(value: float, pct: float, factor: float, n: int = 1) -> set[int]:
    """PRC-04: a planet pays element_sell_pct % of the value, times the selling factor."""
    return price_options(float(value) * float(pct) / 100.0, n, factor)


def barter_bounds(received_value: float, give_unit_value: float, factor: float, *, cap: int | None = None) -> tuple[int, int]:
    """(n_accept, n_reject) for 'give <received_value> for <item>:n' where the counterparty gives n units worth
    give_unit_value each: n_accept is the largest n accepted exactly; n_reject the smallest n above it rejected
    even when the given side is floored (so either rounding rejects it).  cap bounds n by the stock available."""
    unit = float(give_unit_value) * float(factor)
    n_accept = int(math.floor(float(received_value) / unit + 1e-9)) if unit > 0 else 1
    n_accept = max(1, n_accept)
    n_reject = n_accept + 1
    while math.floor(n_reject * unit + 1e-9) <= float(received_value) + 1e-9 and n_reject < n_accept + 10:
        n_reject += 1
    if cap is not None and n_reject > cap:
        n_accept, n_reject = min(n_accept, cap - 1), min(n_reject, cap)
    return n_accept, n_reject


def wealth(player: dict, silver_per_gold: float) -> int | None:
    """Coins in silver-equivalent (ROUNDING_RULE); None when the fields are missing."""
    g, s = player.get("gold"), player.get("silver")
    if not isinstance(g, (int, float)) or not isinstance(s, (int, float)):
        return None
    return int(round(float(g) * float(silver_per_gold) + float(s)))


def peace_threshold(peace_pct: float, peace_pct_fear100: float, fear: float) -> float:
    return float(peace_pct) + (float(peace_pct_fear100) - float(peace_pct)) * float(fear) / 100.0


# --------------------------------------------------------------------------- accessors (pure)

def _rows(turn: dict, key: str) -> list[dict]:
    v = turn.get(key)
    return [x for x in v if isinstance(x, dict)] if isinstance(v, list) else []


def trades_at(turn: dict) -> list[dict]:
    return _rows(turn, "trades")


def pickups_at(turn: dict) -> list[dict]:
    return _rows(turn, "pickups")


def upgrades_at(turn: dict) -> list[dict]:
    return _rows(turn, "upgrades")


def planet_events_at(turn: dict) -> list[dict]:
    return _rows(turn, "planet_events")


def ai_economy_at(turn: dict) -> dict:
    v = turn.get("ai_economy")
    return v if isinstance(v, dict) else {}


def ai_economy_sum(report: dict, key: str) -> int | None:
    """Sum of per-turn ai_economy[key]; None when no turn carries the field."""
    vals = [ai_economy_at(t).get(key) for t in _turns(report)]
    nums = [int(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    return sum(nums) if nums else None


def items_of(cap: dict | None) -> dict[str, int]:
    """captains[*].items as {element: units}: a C11 dict, or an M3 list of names / {element, units} dicts."""
    items = (cap or {}).get("items")
    out: Counter = Counter()
    if isinstance(items, dict):
        for k, v in items.items():
            try:
                out[str(k)] += int(v)
            except (TypeError, ValueError):
                out[str(k)] += 1
    elif isinstance(items, list):
        for it in items:
            if isinstance(it, dict):
                try:
                    out[str(it.get("element") or it.get("name"))] += int(it.get("units", 1))
                except (TypeError, ValueError):
                    out[str(it.get("element") or it.get("name"))] += 1
            else:
                out[str(it)] += 1
    return {k: v for k, v in out.items() if v}


def player_items_at(turn: dict) -> dict[str, int] | None:
    v = (turn.get("player") or {}).get("items")
    return items_of({"items": v}) if isinstance(v, (dict, list)) else None


def sector_key(galaxy: Any, sector: Any) -> str:
    return f"{int(galaxy[0])},{int(galaxy[1])}/{int(sector[0])},{int(sector[1])}"


def sector_items(snapshot: dict | None, key: str) -> list[dict]:
    sec = ((snapshot or {}).get("sectors") or {}).get(key) or {}
    items = sec.get("items")
    return [i for i in items if isinstance(i, dict)] if isinstance(items, list) else []


def sector_terrain(snapshot: dict | None, key: str) -> list[dict]:
    sec = ((snapshot or {}).get("sectors") or {}).get(key) or {}
    t = sec.get("terrain")
    return [x for x in t if isinstance(x, dict)] if isinstance(t, list) else []


def item_at(snapshot: dict | None, key: str, cell: Any, element: str | None = None) -> dict | None:
    for it in sector_items(snapshot, key):
        if _norm(it.get("cell")) == _norm(list(cell)) and (element is None or it.get("element") in (None, element)):
            return it
    return None


def planets_of(snapshot: dict | None) -> dict[str, dict]:
    p = (snapshot or {}).get("planets")
    return {str(k): v for k, v in p.items() if isinstance(v, dict)} if isinstance(p, dict) else {}


def planet_in(snapshot: dict | None, key: str, cell: Any) -> tuple[str, dict] | None:
    """The C11 planet record standing on `cell` of sector `key` (a 'gx,gy/sx,sy' string), by id."""
    for pid, p in planets_of(snapshot).items():
        if str(p.get("sector")) == key and _norm(p.get("cell")) == _norm(list(cell)):
            return pid, p
    return None


def planet_shape_errors(pid: str, p: Any, where: str) -> list[str]:
    if not isinstance(p, dict):
        return [f"{where}: planets[{pid}] is {type(p).__name__}, expected an object (C11)"]
    errs = [f"{where}: planets[{pid}] lacks {k!r} (C11 snapshot additions)" for k in PLANET_KEYS if k not in p]
    st = p.get("stock")
    if isinstance(st, dict):
        for k in ("food", "water", "elements"):
            if k not in st:
                errs.append(f"{where}: planets[{pid}].stock lacks {k!r} (C11)")
    return errs


def trade_shape_errors(row: Any, where: str) -> list[str]:
    if not isinstance(row, dict):
        return [f"{where}: trades[*] is {type(row).__name__}, expected an object (C11)"]
    return [f"{where}: trades[*] lacks {k!r} (C11 report additions)" for k in TRADE_KEYS if k not in row]


def _numbers(text: str) -> set[int]:
    return {int(m) for m in re.findall(r"(?<![\d.])\d+(?![\d.])", text)}


def rejected_texts(turn: dict) -> list[str]:
    return events_mentioning(turn, "rejected")


def refusal_texts(turn: dict, counterparty: str | None = None) -> list[str]:
    """REFUSAL_RULE: reasons of trades[] rows with accepted false (for `counterparty` when given) plus rejected events."""
    out = []
    for r in trades_at(turn):
        if r.get("accepted") is False and (counterparty is None or str(r.get("with")) == counterparty):
            out.append(str(r.get("reason") or ""))
    return out + rejected_texts(turn)


def accepted_trades(turn: dict, counterparty: str | None = None, kind: str | None = None) -> list[dict]:
    return [r for r in trades_at(turn) if r.get("accepted") is True and (counterparty is None or str(r.get("with")) == counterparty)
            and (kind is None or str(r.get("kind")) == kind)]


def fr_rows(turn: dict, captain: str | None, rule: str) -> list[dict]:
    return [r for r in reputation_at(turn) if (captain is None or r.get("captain") == captain) and str(r.get("rule", "")).upper() == rule]


def player_id(snapshot: dict | None, report: dict) -> str | None:
    if isinstance(snapshot, dict) and isinstance(snapshot.get("player_id"), str):
        return snapshot["player_id"]
    for t in _turns(report):
        for b in battles_at(t):
            sides = [str(s) for s in (b.get("sides") or [])]
            mine = [s for s in sides if s.startswith("c-")]
            if len(sides) == 2 and len(mine) == 1:
                return mine[0]
    return None


def turn_no(report: dict, n: int) -> dict:
    return next((t for t in _turns(report) if t.get("turn_no") == n), {})


# --------------------------------------------------------------------------- smoke/37 — items (SP-03, IT-04, IT-05, CMD-14, CMD-15, FR-08, D-141)

def check_item_hidden(report: dict, snapshot: dict | None, *, item: dict, key: str, what: str) -> list[str]:
    """No scan: turn 1 `pickup <element>` is rejected (IT-04) and the saved sector item at the cell is not revealed."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t1 = turn_no(report, 1)
    if not rejected_texts(t1):
        errs.append(f"{what}: turn 1: `pickup {item['element']}` before any scan was not rejected (events {[event_text(e)[:60] for e in (t1.get('events') or [])][:5]}); "
                    f"items are never picked up unscanned (IT-04, CMD-15; {REJECTED_RULE})")
    if pickups_at(t1):
        errs.append(f"{what}: turn 1: pickups[] = {_short(pickups_at(t1))} although nothing was scanned (IT-04)")
    if not isinstance(snapshot, dict):
        errs.append(f"{what}: no save snapshot after the run (C6)")
        return errs
    it = item_at(snapshot, key, item["cell"])
    if it is None:
        errs.append(f"{what}: the saved sector {key} lists no item at {item['cell']} (world_index.items says {item['element']} × {item.get('units')} lies there; C11, SP-03)")
    elif it.get("revealed") is not False:
        errs.append(f"{what}: sectors[{key}].items at {item['cell']} has revealed = {it.get('revealed')!r} without a scan, expected false (SYM-09, GRD-04)")
    return errs


def check_item_scan(report: dict, snapshot: dict | None, *, item: dict, key: str, what: str) -> list[str]:
    """Turn 1 `scan`: the OUT-10 event names the element and the units; the saved item is revealed with element and units."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t1 = turn_no(report, 1)
    texts = [event_text(e) for e in (t1.get("events") or []) if item["element"].lower() in event_text(e).lower()]
    if not texts:
        errs.append(f"{what}: turn 1: no event names the scanned item {item['element']!r} (events {[event_text(e)[:60] for e in (t1.get('events') or [])][:6]}) — "
                    f"scan lists the items in the cell with element and units (OUT-10, CMD-14)")
    elif isinstance(item.get("units"), int) and not any(item["units"] in _numbers(t) for t in texts):
        errs.append(f"{what}: turn 1: the scan text {texts[0][:160]!r} does not name the stack's units {item['units']} (OUT-10, D-141)")
    if not isinstance(snapshot, dict):
        errs.append(f"{what}: no save snapshot after the run (C6)")
        return errs
    it = item_at(snapshot, key, item["cell"])
    if it is None:
        errs.append(f"{what}: the saved sector {key} lists no item at {item['cell']} after the scan (C11)")
    else:
        if it.get("revealed") is not True:
            errs.append(f"{what}: sectors[{key}].items at {item['cell']} has revealed = {it.get('revealed')!r} after a scan, expected true (SYM-09, CMD-14)")
        if it.get("element") != item["element"]:
            errs.append(f"{what}: the revealed item carries element = {it.get('element')!r}, expected {item['element']!r} (C11: element and units once revealed)")
        if "units" not in it or (isinstance(item.get("units"), int) and it.get("units") != item["units"]):
            errs.append(f"{what}: the revealed item carries units = {it.get('units')!r}, expected {item.get('units')} (C11, CFG-80)")
    return errs


def check_item_pickup(report: dict, snapshot: dict | None, *, item: dict, key: str, pickup_turn: int, respect_start: int, special_delta: int,
                      what: str) -> list[str]:
    """`pickup <element>` at pickup_turn after a scan: a pickups[] row (element, units, rarity), the stack in the player's
    items, the item gone from the sector, FR-08 +special_delta once for a special stack and nothing for a normal one;
    later scans find nothing (no respawn, IT-05)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    special = str(item.get("rarity", "")).lower() == "special"
    t = turn_no(report, pickup_turn)
    if "pickups" not in t:
        errs.append(f"{what}: per_turn has no 'pickups' list (C11 report additions)")
        return errs
    mine = [p for p in pickups_at(t) if p.get("element") == item["element"] and (pid is None or p.get("captain") in (None, pid))]
    if not mine:
        rej = rejected_texts(t)
        errs.append(f"{what}: turn {pickup_turn}: no pickups[] row for {item['element']} ({_short(pickups_at(t))})" + (f"; rejected: {rej[0][:140]!r}" if rej else "")
                    + f" — `pickup <element>` after a scan moves the stack to the captain (CMD-15, D-141; {PICKUP_RULE})")
        return errs
    row = mine[0]
    if isinstance(item.get("units"), int) and row.get("units") != item["units"]:
        errs.append(f"{what}: pickups[*].units = {row.get('units')!r}, expected the stack's {item['units']} (C11, CFG-80)")
    if str(row.get("rarity", "")).lower() != str(item.get("rarity", "")).lower():
        errs.append(f"{what}: pickups[*].rarity = {row.get('rarity')!r}, expected {item.get('rarity')!r} (C11, IT-05)")
    pl_items = player_items_at(t)
    if pl_items is None:
        errs.append(f"{what}: per_turn.player has no 'items' (C11 report additions: player.items = {{element: units}})")
    elif pl_items.get(item["element"], 0) < int(item.get("units") or 1):
        errs.append(f"{what}: turn {pickup_turn}: player.items = {pl_items}, expected {item['element']} ≥ {item.get('units') or 1} after the pickup (CMD-15)")
    rows = fr_rows(t, pid, "FR-08")
    want = respect_start + (special_delta if special else 0)
    got = (t.get("player") or {}).get("respect")
    if special:
        if not rows:
            errs.append(f"{what}: turn {pickup_turn}: no reputation row FR-08 for a special stack ({item['element']}); rows {_short(reputation_at(t))} — "
                        f"special item acquired: respect +{special_delta} (FR-08, CFG-09, D-141)")
        elif rows[0].get("respect") != special_delta:
            errs.append(f"{what}: FR-08 row respect = {rows[0].get('respect')!r}, expected +{special_delta} unscaled ([respect] special_item_delta; D-121)")
    elif rows:
        errs.append(f"{what}: turn {pickup_turn}: a reputation row FR-08 for a normal stack {_short(rows[0])} — FR-08 is for special items only (FR-08, D-141)")
    if isinstance(got, int) and got != min(100, want):
        errs.append(f"{what}: turn {pickup_turn}: player.respect = {got}, expected {min(100, want)} ({'start + special_item_delta' if special else 'unchanged'}; FR-08)")
    later = [x for x in _turns(report) if int(x.get("turn_no") or 0) > pickup_turn]
    for x in later:
        if any(p.get("element") == item["element"] for p in pickups_at(x)):
            errs.append(f"{what}: turn {x.get('turn_no')}: {item['element']} was picked up again at the same cell — no respawn (IT-05, D-141)")
            break
        hits = [event_text(e) for e in (x.get("events") or []) if "scan" in event_text(e).lower() and item["element"].lower() in event_text(e).lower()
                and "none" not in event_text(e).lower()]
        if hits:
            errs.append(f"{what}: turn {x.get('turn_no')}: a later scan still lists {item['element']} at the cell: {hits[0][:140]!r} — the stack left the sector (IT-05)")
            break
    if not isinstance(snapshot, dict):
        errs.append(f"{what}: no save snapshot after the run (C6)")
        return errs
    if item_at(snapshot, key, item["cell"], item["element"]) is not None:
        errs.append(f"{what}: the saved sector {key} still lists {item['element']} at {item['cell']} after the pickup — the item leaves the sector (CMD-15, IT-05)")
    cap = (snapshot.get("captains") or {}).get(pid or "") or {}
    have = items_of(cap)
    if have.get(item["element"], 0) < int(item.get("units") or 1):
        errs.append(f"{what}: snapshot captains[{pid}].items = {have}, expected {item['element']} ≥ {item.get('units') or 1} (C11 captains[*].items)")
    return errs


def check_no_respawn(report: dict, index: dict | None, *, what: str) -> list[str]:
    """An AI run: final.pickups is a count, equals the per-turn rows, and never exceeds the generated items when the
    (≤ 200-entry) index is complete."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    fp = _get(report, "final", "pickups")
    if not isinstance(fp, int) or isinstance(fp, bool) or fp < 0:
        errs.append(f"{what}: final.pickups = {fp!r}, expected an integer count ≥ 0 (C11 final additions)")
        return errs
    rows = sum(len(pickups_at(t)) for t in _turns(report))
    if rows != fp:
        errs.append(f"{what}: final.pickups = {fp} but the per-turn pickups[] rows sum to {rows} ({PICKUP_RULE})")
    items = (index or {}).get("items")
    if isinstance(items, list) and len(items) < 200 and fp > len(items):
        errs.append(f"{what}: final.pickups = {fp} exceeds the {len(items)} items world_index lists — items never respawn (IT-05, D-141)")
    return errs


# --------------------------------------------------------------------------- smoke/38 — buy / sell (CMD-20, PRC-01…05, TR-04, PR-04, D-142)

def check_buy(report: dict, snapshot: dict | None, *, planet_id: str, turn: int, item: str, n: int, unit_price: float, band: float, spg: float,
              respect_before: int, baseline_turn: dict | None, stock_before: int | None, regen: int, cap: int, turns_total: int, what: str,
              coins_before: tuple[int, int] | None = None) -> list[str]:
    """`buy <item> <n>` at `turn`: an accepted trades[] row, coins down by the factored price (ROUNDING_RULE), the player's
    supply up by n against the baseline turn, the planet's stock down by n (plus its regeneration), an OUT-07 event.
    coins_before = the --coins (gold, silver) when the buy is at turn 1 and no previous turn carries the coins."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t = turn_no(report, turn)
    prev = turn_no(report, turn - 1)
    if "trades" not in t:
        errs.append(f"{what}: per_turn has no 'trades' list (C11 report additions)")
        return errs
    rows = accepted_trades(t, planet_id, "buy")
    if not rows:
        ref = refusal_texts(t, planet_id)
        errs.append(f"{what}: turn {turn}: no accepted trades[] row {{with {planet_id}, kind buy}} (rows {_short(trades_at(t))})"
                    + (f"; refused: {ref[0][:140]!r}" if ref else "") + " (CMD-20, PR-04, C11)")
        return errs
    errs += trade_shape_errors(rows[0], what)
    factor = price_factor(respect_before, band, selling=False)
    options = price_options(unit_price, n, factor)
    before = wealth(prev.get("player") or {}, spg)
    if before is None and coins_before is not None:
        before = wealth({"gold": coins_before[0], "silver": coins_before[1]}, spg)
    after = wealth(t.get("player") or {}, spg)
    if before is None or after is None:
        errs.append(f"{what}: turn {turn}: player gold/silver missing from the report (C2)")
    elif before - after not in options:
        errs.append(f"{what}: turn {turn}: coins fell by {before - after} silver-equivalent, expected {sorted(options)} = {n} × {unit_price:g} × factor {factor:.3f} "
                    f"(respect {respect_before}, planet_price_band {band:g}; PRC-01…03, EF-04, D-142; {ROUNDING_RULE})")
    pf = rows[0].get("price_factor")
    if isinstance(pf, (int, float)) and not _close(pf, factor, 0.0051):
        errs.append(f"{what}: trades[*].price_factor = {pf!r}, expected {factor:.2f} (D-142)")
    if item in ("food", "water") and baseline_turn is not None:
        got, base = (t.get("player") or {}).get(item), (baseline_turn.get("player") or {}).get(item)
        if isinstance(got, int) and isinstance(base, int) and got != base + n:
            errs.append(f"{what}: turn {turn}: player.{item} = {got}, expected {base} (the same turn without the buy) + {n} (CMD-20, TR-04)")
    cost_texts = [event_text(e) for e in (t.get("events") or []) if item in event_text(e).lower() and any(c in _numbers(event_text(e)) for c in options)]
    if not cost_texts:
        errs.append(f"{what}: turn {turn}: no event names the {item} bought and the silver paid {sorted(options)} ({TRADE_EVENT_RULE}; OUT-07)")
    if isinstance(snapshot, dict) and stock_before is not None and item in ("food", "water"):
        p = planets_of(snapshot).get(planet_id)
        if p is None:
            errs.append(f"{what}: planets[{planet_id}] missing from the saved snapshot (C11)")
        else:
            st = (p.get("stock") or {}).get(item)
            allowed = {min(cap, stock_before - n + regen * k) for k in (0, turns_total - 1, turns_total)} | {stock_before - n}
            if not isinstance(st, int) or st not in allowed:
                errs.append(f"{what}: planets[{planet_id}].stock.{item} = {st!r} after {turns_total} turn(s), expected {sorted(allowed)} = {stock_before} − {n} bought "
                            f"(+ stock_regen {regen} a turn to stock_cap {cap}; PL-02, PR-03, CFG-78)")
    return errs


def check_sell(report: dict, snapshot: dict | None, *, planet_id: str, turn: int, element: str, n: int, value: float, pct: float, band: float, spg: float,
               respect_before: int, what: str) -> list[str]:
    """`sell <element> <n>`: an accepted row, coins up by element_sell_pct % of the value × the selling factor, the units gone."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t, prev = turn_no(report, turn), turn_no(report, turn - 1)
    rows = accepted_trades(t, planet_id, "sell")
    if not rows:
        ref = refusal_texts(t, planet_id)
        errs.append(f"{what}: turn {turn}: no accepted trades[] row {{with {planet_id}, kind sell}} (rows {_short(trades_at(t))})"
                    + (f"; refused: {ref[0][:140]!r}" if ref else "") + " (CMD-20, PRC-04, C11)")
        return errs
    factor = price_factor(respect_before, band, selling=True)
    options = element_sell_options(value, pct, factor, n)
    before, after = wealth(prev.get("player") or {}, spg), wealth(t.get("player") or {}, spg)
    if before is None or after is None:
        errs.append(f"{what}: turn {turn}: player gold/silver missing from the report (C2)")
    elif after - before not in options:
        errs.append(f"{what}: turn {turn}: coins rose by {after - before}, expected {sorted(options)} = {n} × {value:g} × element_sell_pct {pct:g} % × factor {factor:.3f} "
                    f"(PRC-04, D-142; {ROUNDING_RULE})")
    pl_items = player_items_at(t)
    if pl_items is not None and pl_items.get(element, 0) != 0:
        errs.append(f"{what}: turn {turn}: player.items = {pl_items} after selling {n} {element}, expected none left (CMD-20)")
    if isinstance(snapshot, dict):
        p = planets_of(snapshot).get(planet_id)
        els = ((p or {}).get("stock") or {}).get("elements")
        if isinstance(els, dict) and int(els.get(element, 0) or 0) < n:
            errs.append(f"{what}: planets[{planet_id}].stock.elements = {els} after the sale, expected {element} ≥ {n} (PL-03: sold elements join the stock)")
    if not any(element in event_text(e).lower() and any(c in _numbers(event_text(e)) for c in options) for e in (t.get("events") or [])):
        errs.append(f"{what}: turn {turn}: no event names the {element} sold and the silver received {sorted(options)} ({TRADE_EVENT_RULE}; OUT-07)")
    return errs


def check_buy_rejected(report: dict, *, planet_id: str, turn: int, item: str, n: int, cost_options: set[int], stock_numbers: set[int], what: str) -> list[str]:
    """`buy <item> <n>` beyond the coins or the stock: refused naming the shortfall (the cost or the stock), nothing changes."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    cost_options, stock_numbers = {int(x) for x in cost_options}, {int(x) for x in stock_numbers}      # JSON fixtures hand lists
    t, prev = turn_no(report, turn), turn_no(report, turn - 1)
    if accepted_trades(t, planet_id, "buy"):
        errs.append(f"{what}: turn {turn}: `buy {item} {n}` was accepted ({_short(accepted_trades(t, planet_id))}) although the coins / stock cannot cover it (CMD-20, TR-04)")
        return errs
    texts = refusal_texts(t, planet_id)
    if not texts:
        errs.append(f"{what}: turn {turn}: `buy {item} {n}` was neither accepted nor refused (trades {_short(trades_at(t))}; events "
                    f"{[event_text(e)[:50] for e in (t.get('events') or [])][:5]}) ({REFUSAL_RULE})")
        return errs
    nums = set().union(*[_numbers(x) for x in texts])
    joined = " | ".join(texts).lower()
    if not ((nums & cost_options and ("silver" in joined or "gold" in joined or "coin" in joined)) or (nums & stock_numbers and ("stock" in joined or item in joined))):
        errs.append(f"{what}: the refusal {texts[0][:160]!r} does not name the shortfall — the silver needed {sorted(cost_options)} or the stock {sorted(stock_numbers)} "
                    f"(C11 order.rejected 'not enough silver: 40 needed, 12 held'; CMD-20)")
    a, b = (prev.get("player") or {}), (t.get("player") or {})
    if isinstance(a.get("silver"), int) and isinstance(b.get("silver"), int) and (a.get("gold"), a.get("silver")) != (b.get("gold"), b.get("silver")):
        errs.append(f"{what}: turn {turn}: coins changed {a.get('gold')}/{a.get('silver')} → {b.get('gold')}/{b.get('silver')} on a refused buy (TR-04)")
    return errs


def check_buy_ship(report: dict, snapshot: dict | None, *, planet_id: str, turn: int, cls: str, price_gold: int, gold_before: int, what: str) -> list[str]:
    """`buy ship <class>` with enough gold: a kind 'ship' row, gold down by the price, player.ships 2, the new ship of that class in the cell."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t = turn_no(report, turn)
    rows = accepted_trades(t, planet_id, "ship")
    if not rows:
        ref = refusal_texts(t, planet_id)
        errs.append(f"{what}: turn {turn}: no accepted trades[] row {{with {planet_id}, kind ship}} ({_short(trades_at(t))})" + (f"; refused: {ref[0][:140]!r}" if ref else "")
                    + f" — `buy ship {cls}` costs ship_{cls} gold (PRC-05, FL-02, D-142)")
        return errs
    pl = t.get("player") or {}
    if pl.get("gold") != gold_before - price_gold:
        errs.append(f"{what}: turn {turn}: player.gold = {pl.get('gold')!r}, expected {gold_before} − {price_gold} = {gold_before - price_gold} ([prices] ship_{cls}; PRC-05)")
    if pl.get("ships") != 2:
        errs.append(f"{what}: turn {turn}: player.ships = {pl.get('ships')!r}, expected 2 (FL-02, C10)")
    if isinstance(snapshot, dict):
        pid = snapshot.get("player_id")
        cap = (snapshot.get("captains") or {}).get(pid) or {}
        ships = [s for s in (cap.get("ships") or []) if s in (snapshot.get("ships") or {})]
        if len(ships) != 2:
            errs.append(f"{what}: snapshot captains[{pid}].ships = {cap.get('ships')!r}, expected two ships (FL-02)")
        else:
            try:
                sid, me = player_ship(snapshot)
            except KeyError:
                sid, me = None, {}
            other = next((snapshot["ships"][s] for s in ships if s != sid), None)
            if other is None or str(other.get("class", "")).lower() != cls:
                errs.append(f"{what}: the bought ship's class is {(other or {}).get('class')!r}, expected {cls!r} (PRC-05)")
            elif _norm(other.get("position")) != _norm(me.get("position")):
                errs.append(f"{what}: the bought ship is at {other.get('position')}, expected the planet's cell {me.get('position')} (D-142: added to the fleet in the planet's cell)")
    return errs


def check_buy_ship_rejected(report: dict, *, planet_id: str, turn: int, cls: str, price_gold: int, gold_held: int, what: str) -> list[str]:
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t = turn_no(report, turn)
    if accepted_trades(t, planet_id, "ship"):
        errs.append(f"{what}: turn {turn}: `buy ship {cls}` was accepted with {gold_held} gold against a price of {price_gold} (PRC-05)")
        return errs
    texts = refusal_texts(t, planet_id)
    if not texts:
        errs.append(f"{what}: turn {turn}: `buy ship {cls}` was neither accepted nor refused ({REFUSAL_RULE})")
    elif "gold" not in " ".join(texts).lower():
        errs.append(f"{what}: the refusal {texts[0][:160]!r} does not name gold (PRC-05: ship prices are in gold; C11 rejections name the rule and the shortfall)")
    pl = t.get("player") or {}
    if isinstance(pl.get("ships"), int) and pl["ships"] != 1:
        errs.append(f"{what}: turn {turn}: player.ships = {pl.get('ships')} after a refused purchase, expected 1")
    return errs


# --------------------------------------------------------------------------- smoke/39 — disposition and barter (PL-06, PR-05, TR-06, EF-04, FR-07, D-142)

def check_refused(report: dict, snapshot: dict | None, *, planet_id: str, turn: int, respect: int, refuse_respect: int, what: str) -> list[str]:
    """Below planet_refuse_respect every trade is refused citing EF-04; the planet record says so."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t = turn_no(report, turn)
    if accepted_trades(t, planet_id):
        errs.append(f"{what}: turn {turn}: a trade with {planet_id} was accepted at respect {respect} < planet_refuse_respect {refuse_respect} ({_short(accepted_trades(t, planet_id))}; EF-04, PR-05)")
    texts = refusal_texts(t, planet_id)
    if not texts:
        errs.append(f"{what}: turn {turn}: no refusal recorded for the trade at respect {respect} ({REFUSAL_RULE}; EF-04)")
    elif not any("ef-04" in x.lower() or "refus" in x.lower() for x in texts):
        errs.append(f"{what}: the refusal {texts[0][:160]!r} does not cite EF-04 / 'refuses' (C11: 'Oreth refuses to trade: respect 12 < 20 (EF-04)')")
    if isinstance(snapshot, dict):
        p = planets_of(snapshot).get(planet_id)
        if p is None:
            errs.append(f"{what}: planets[{planet_id}] missing from the saved snapshot (C11)")
        else:
            if p.get("trades_with_player") is not False:
                errs.append(f"{what}: planets[{planet_id}].trades_with_player = {p.get('trades_with_player')!r} at respect {respect}, expected false (EF-04, C11)")
            want = disposition(respect)
            if p.get("disposition_to_player") != want:
                errs.append(f"{what}: planets[{planet_id}].disposition_to_player = {p.get('disposition_to_player')!r}, expected {want} = 2 × {respect} − 100 (PL-06, D-142)")
    return errs


def check_barter(report: dict, snapshot: dict | None, *, counterparty: str, give: tuple[str, int], want_item: str, n_accept: int, n_reject: int,
                 accept_turn: int, reject_turn: int, factor: float, trade_delta: int, respect_before: int, what: str) -> list[str]:
    """`trade <counterparty> give <el>:<k> for <item>:<n>`: accepted at n_accept (kind barter, FR-07 +trade_delta, the goods move),
    refused at n_reject (LIST_PRICE_RULE)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    el, k = give
    ta, tr = turn_no(report, accept_turn), turn_no(report, reject_turn)
    if "trades" not in ta:
        errs.append(f"{what}: per_turn has no 'trades' list (C11 report additions)")
        return errs
    rows = accepted_trades(ta, counterparty, "barter")
    if not rows:
        ref = refusal_texts(ta, counterparty)
        errs.append(f"{what}: turn {accept_turn}: `trade {counterparty} give {el}:{k} for {want_item}:{n_accept}` was not accepted (rows {_short(trades_at(ta))})"
                    + (f"; refused: {ref[0][:140]!r}" if ref else "") + f" — {n_accept} × list value × factor {factor:.3f} ≤ {k} × value({el}) ({LIST_PRICE_RULE}; TR-06)")
    else:
        errs += trade_shape_errors(rows[0], what)
        fr = fr_rows(ta, pid, "FR-07")
        if not fr:
            errs.append(f"{what}: turn {accept_turn}: no reputation row FR-07 after the accepted barter (rows {_short(reputation_at(ta))}; FR-07, TR-09)")
        elif fr[0].get("respect") != trade_delta:
            errs.append(f"{what}: FR-07 row respect = {fr[0].get('respect')!r}, expected +{trade_delta} unscaled ([respect] trade_accepted_delta; D-121)")
        got = (ta.get("player") or {}).get("respect")
        if isinstance(got, int) and got != min(100, respect_before + trade_delta):
            errs.append(f"{what}: turn {accept_turn}: player.respect = {got}, expected {min(100, respect_before + trade_delta)} (FR-07 +{trade_delta})")
        prev = turn_no(report, accept_turn - 1)
        if want_item in ("food", "water"):
            a, b = (prev.get("player") or {}).get(want_item), (ta.get("player") or {}).get(want_item)
            if isinstance(a, int) and isinstance(b, int) and b - a not in (n_accept, n_accept - 1):
                errs.append(f"{what}: turn {accept_turn}: player.{want_item} {a} → {b}, expected +{n_accept} (less the turn's upkeep) from the barter (TR-03)")
        elif want_item == "silver":
            a, b = (prev.get("player") or {}).get("silver"), (ta.get("player") or {}).get("silver")
            if isinstance(a, int) and isinstance(b, int) and b - a != n_accept:
                errs.append(f"{what}: turn {accept_turn}: player.silver {a} → {b}, expected +{n_accept} from the barter (TR-03)")
        pl_items = player_items_at(ta)
        prev_items = player_items_at(prev) if prev else None
        if pl_items is not None and prev_items is not None and prev_items.get(el, 0) - pl_items.get(el, 0) != k:
            errs.append(f"{what}: turn {accept_turn}: player.items {el} {prev_items.get(el, 0)} → {pl_items.get(el, 0)}, expected −{k} given away (TR-03)")
    if accepted_trades(tr, counterparty, "barter"):
        errs.append(f"{what}: turn {reject_turn}: `trade {counterparty} give {el}:{k} for {want_item}:{n_reject}` was accepted although {n_reject} × list value × factor "
                    f"{factor:.3f} > {k} × value({el}) ({LIST_PRICE_RULE}; TR-06, EF-03/04)")
    elif not refusal_texts(tr, counterparty):
        errs.append(f"{what}: turn {reject_turn}: the barter for {want_item}:{n_reject} was neither accepted nor refused ({REFUSAL_RULE})")
    return errs


def check_factor_at(report: dict, *, planet_id: str, turn: int, respect: int, band: float, unit_price: float, n: int, spg: float, what: str,
                    coins_before: tuple[int, int] | None = None) -> list[str]:
    """A buy at `respect`: the factor 1 − band/100 × respect/100 to 2 d.p. (price_factor when reported, and the coins)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t, prev = turn_no(report, turn), turn_no(report, turn - 1)
    rows = accepted_trades(t, planet_id, "buy")
    factor = price_factor(respect, band, selling=False)
    if not rows:
        ref = refusal_texts(t, planet_id)
        errs.append(f"{what}: turn {turn}: no accepted buy with {planet_id} at respect {respect}" + (f"; refused: {ref[0][:140]!r}" if ref else "") + " (EF-04, D-142)")
        return errs
    pf = rows[0].get("price_factor")
    if isinstance(pf, (int, float)) and not _close(pf, factor, 0.0051):
        errs.append(f"{what}: trades[*].price_factor = {pf!r}, expected {factor:.2f} = 1 − planet_price_band {band:g}/100 × respect {respect}/100 (EF-04, D-142)")
    before, after = wealth(prev.get("player") or {}, spg), wealth(t.get("player") or {}, spg)
    if before is None and coins_before is not None:
        before = wealth({"gold": coins_before[0], "silver": coins_before[1]}, spg)
    options = price_options(unit_price, n, factor)
    if before is not None and after is not None and before - after not in options:
        errs.append(f"{what}: turn {turn}: coins fell by {before - after}, expected {sorted(options)} at factor {factor:.2f} (respect {respect}; D-142; {ROUNDING_RULE})")
    return errs


# --------------------------------------------------------------------------- smoke/40 — upgrades (CMD-13, UP-01…05, CFG-33…37, CFG-76, CFG-82, D-143)

STAT_OF = {"torpedoes": "torpedoes", "phasers": "phasers", "shields": "shield_max", "engine": "engine", "warp": "warp"}


def check_upgrades(report: dict, snapshot: dict | None, *, pairs: list[dict], caps: dict, base: dict, wrong: dict | None, what: str) -> list[str]:
    """pairs: [{turn, component, element, amount}] applied one a turn; caps: {component: cap or None}; base: the ship's stats
    before (from the probe); wrong: {turn, component, element} that must be rejected.  Each row: before/after by the map
    amount (or to the cap, capped true), an OUT-08 event, the unit consumed; the final ship stats are base + amounts, capped."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    stats = dict(base)
    for p in pairs:
        t = turn_no(report, p["turn"])
        if "upgrades" not in t:
            errs.append(f"{what}: per_turn has no 'upgrades' list (C11 report additions)")
            return errs
        rows = [u for u in upgrades_at(t) if str(u.get("component")) == p["component"] and str(u.get("element")) == p["element"]]
        stat = STAT_OF[p["component"]]
        cap = caps.get(p["component"])
        want_after = stats[stat] + p["amount"]
        capped = cap is not None and want_after > cap + 1e-9
        if capped:
            want_after = float(cap)
        if not rows:
            rej = rejected_texts(t)
            errs.append(f"{what}: turn {p['turn']}: no upgrades[] row {{component {p['component']}, element {p['element']}}} ({_short(upgrades_at(t))})"
                        + (f"; rejected: {rej[0][:140]!r}" if rej else "") + " — the [upgrades] map pairs them (CMD-13, UP-01…05, CFG-33…37)")
        else:
            r = rows[0]
            if not _close(r.get("before"), stats[stat], 1e-6):
                errs.append(f"{what}: turn {p['turn']}: upgrades[*].before = {r.get('before')!r}, expected {stats[stat]:g} (the stat before this upgrade; OUT-08)")
            if not _close(r.get("after"), want_after, 1e-6):
                errs.append(f"{what}: turn {p['turn']}: upgrades[*].after = {r.get('after')!r}, expected {want_after:g} = {stats[stat]:g} + {p['amount']:g}"
                            + (f" capped at {cap:g}" if capped else "") + f" ([upgrades] {p['component']} = …{p['element']}:{p['amount']:g}; UP-01…05, D-143)")
            if bool(r.get("capped")) != capped:
                errs.append(f"{what}: turn {p['turn']}: upgrades[*].capped = {r.get('capped')!r}, expected {capped} (caps: phaser_max / shield_cap / impulse_max / warp_max; D-143)")
            texts = [event_text(e) for e in (t.get("events") or []) if p["component"] in event_text(e).lower()]
            if not any(f"{want_after:g}" in x or int(round(want_after)) in _numbers(x) for x in texts):
                errs.append(f"{what}: turn {p['turn']}: no event names the {p['component']} upgrade with the new value {want_after:g} (OUT-08: component, before → after)")
            items = player_items_at(t)
            prev_items = player_items_at(turn_no(report, p["turn"] - 1))
            if items is not None and prev_items is not None and prev_items.get(p["element"], 0) - items.get(p["element"], 0) != 1:
                errs.append(f"{what}: turn {p['turn']}: player.items {p['element']} {prev_items.get(p['element'], 0)} → {items.get(p['element'], 0)}, expected one unit consumed (D-143)")
        stats[stat] = want_after
    if wrong is not None:
        t = turn_no(report, wrong["turn"])
        if upgrades_at(t):
            errs.append(f"{what}: turn {wrong['turn']}: `upgrade {wrong['component']} with {wrong['element']}` produced {_short(upgrades_at(t))} — the pairing is not in [upgrades] (CMD-13)")
        elif not rejected_texts(t):
            errs.append(f"{what}: turn {wrong['turn']}: `upgrade {wrong['component']} with {wrong['element']}` was not rejected ({REJECTED_RULE}; CMD-13)")
    if not isinstance(snapshot, dict):
        errs.append(f"{what}: no save snapshot after the run (C6)")
        return errs
    try:
        _, ship = player_ship(snapshot)
    except KeyError as e:
        return errs + [f"{what}: {e}"]
    for comp, stat in STAT_OF.items():
        if comp in {p["component"] for p in pairs} and not _close(ship.get(stat), stats[stat], 1e-6):
            errs.append(f"{what}: final ship {stat} = {ship.get(stat)!r}, expected {stats[stat]:g} after the upgrades (UP-01…05; caps CFG-76/82/22)")
    return errs


# --------------------------------------------------------------------------- smoke/41 — barter with an AI captain (CMD-12, TR-06, EF-03, D-142)

def check_barter_ai(report: dict, snapshot: dict | None, *, spawn: dict, give: tuple[str, int], n_accept: int, n_reject: int, accept_turn: int, reject_turn: int,
                    factor: float, trade_delta: int, respect_before: int, player_silver_before: int, what: str) -> list[str]:
    """As check_barter against the spawned captain, plus both sides' coins and items in the final snapshot."""
    errs = check_barter(report, snapshot, counterparty=spawn["captain"], give=give, want_item="silver", n_accept=n_accept, n_reject=n_reject, accept_turn=accept_turn,
                        reject_turn=reject_turn, factor=factor, trade_delta=trade_delta, respect_before=respect_before, what=what)
    if not isinstance(snapshot, dict):
        return errs
    el, k = give
    cap = (snapshot.get("captains") or {}).get(spawn["captain"])
    if not isinstance(cap, dict):
        errs.append(f"{what}: the spawned captain {spawn['captain']} is missing from the final snapshot (C10)")
        return errs
    have = items_of(cap)
    if have.get(el, 0) < k:
        errs.append(f"{what}: captains[{spawn['captain']}].items = {have} after the accepted barter, expected {el} ≥ {k} (TR-03: the goods move to the AI captain)")
    sb = spawn.get("silver")
    if isinstance(sb, int) and isinstance(cap.get("silver"), int) and cap["silver"] != sb - n_accept:
        errs.append(f"{what}: captains[{spawn['captain']}].silver = {cap['silver']}, expected {sb} − {n_accept} = {sb - n_accept} after paying the barter (TR-01, TR-03)")
    pcap = (snapshot.get("captains") or {}).get(snapshot.get("player_id")) or {}
    if isinstance(pcap.get("silver"), int) and pcap["silver"] != player_silver_before + n_accept:
        errs.append(f"{what}: the player's silver = {pcap['silver']}, expected {player_silver_before} + {n_accept} (TR-03)")
    return errs


# --------------------------------------------------------------------------- smoke/42 — planets in combat (CM-11, PR-02, CB-01, CB-03, D-145)

def check_planet_record(snapshot: dict | None, *, planet_id: str, stats: dict, what: str) -> list[str]:
    """A planet carries CFG-77 defence (shield_max, torpedoes, phasers) and full shields before any fight."""
    if not isinstance(snapshot, dict):
        return [f"{what}: no save snapshot to read the planet from (C6)"]
    p = planets_of(snapshot).get(planet_id)
    if p is None:
        return [f"{what}: planets[{planet_id}] missing from the snapshot (C11 snapshot additions)"]
    errs = planet_shape_errors(planet_id, p, what)
    for k in ("shield_max", "torpedoes", "phasers"):
        if k in p and not _close(p.get(k), stats[k], 1e-6):
            errs.append(f"{what}: planets[{planet_id}].{k} = {p.get(k)!r}, expected [planets] {k} = {stats[k]:g} (CFG-77, PL-04, D-140)")
    if "shields" in p and "shield_max" in p and not _close(p.get("shields"), p.get("shield_max"), 1e-6):
        errs.append(f"{what}: planets[{planet_id}].shields = {p.get('shields')!r} before any fight, expected shield_max {p.get('shield_max')!r}")
    return errs


def planet_rows(turn: dict, planet_id: str) -> list[dict]:
    return [ex for ex in exchanges_at(turn) if ex.get("attacker") == planet_id]


def check_planet_fight(report: dict, snapshot: dict | None, *, planet_id: str, planet_cell: Any, distance: int, phasers: float, per_unit: float, torpedo_damage: float,
                       start_fight_delta: int, respect_start: int, until_turn: int, what: str) -> list[str]:
    """The player's `fire` on a planet opens a battle (opened_by player, FR-06); every open turn the planet fires one torpedo and
    its phasers back at the spawn distance (CFG-77 stats, phaser damage by CM-04) and never moves (CM-11, D-145)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    t1 = turn_no(report, 1)
    b = battle_between(report, pid or "", planet_id)
    if b is None:
        rej = rejected_texts(t1)
        errs.append(f"{what}: no battle with sides {{player {pid}, planet {planet_id}}} in battles[] (got {_short([x.get('sides') for x in all_battles(report).values()])})"
                    + (f"; rejected: {rej[0][:140]!r}" if rej else "") + " — a `fire` on a planet opens a battle like any other (CM-11, PR-02, D-145)")
        return errs
    if b.get("opened_by") != pid:
        errs.append(f"{what}: battle.opened_by = {b.get('opened_by')!r}, expected the player {pid} (CM-01)")
    if not fr_rows(t1, pid, "FR-06"):
        errs.append(f"{what}: turn 1: no FR-06 row for the player after firing on the planet (rows {_short(reputation_at(t1))}; FR-06 −{start_fight_delta}, D-145)")
    if not any(e.get("planet") == planet_id and str(e.get("event")) == "attacked" for e in planet_events_at(t1)):
        errs.append(f"{what}: turn 1: no planet_events row {{planet {planet_id}, event attacked}} ({_short(planet_events_at(t1))}; C11, PR-02)")
    open_turns = [t for t in _turns(report) if int(t.get("turn_no") or 0) <= until_turn
                  and any(x.get("id") == b.get("id") and x.get("ended") is None for x in battles_at(t))]
    if not open_turns:
        errs.append(f"{what}: the battle with {planet_id} is never open at the end of a turn ≤ {until_turn} (battle {_short(b)})")
    for t in open_turns:
        rows = planet_rows(t, planet_id)
        weapons = Counter(str(r.get("weapon")) for r in rows)
        if weapons.get("torpedo", 0) != 1 or weapons.get("phaser", 0) != 1:
            errs.append(f"{what}: turn {t.get('turn_no')}: the planet's exchange rows are {dict(weapons)}, expected one torpedo and one phaser volley each open turn "
                        f"(D-145: fires both weapons at the nearest enemy in range; CFG-77)")
            break
        for r in rows:
            if r.get("distance") != distance:
                errs.append(f"{what}: turn {t.get('turn_no')}: the planet's exchange distance = {r.get('distance')!r}, expected {distance} — a planet never moves (CM-11)")
                break
            if r.get("weapon") == "phaser":
                want = phaser_damage(phasers, per_unit, distance)
                if r.get("hit") is not True or not _close(r.get("damage"), want, 0.0051):
                    errs.append(f"{what}: turn {t.get('turn_no')}: the planet's phaser row {_short(r)}, expected a hit for {want:.2f} = {phasers:g} × {per_unit:g} × (1 − {distance}/3) (CM-04, CFG-77)")
            elif r.get("weapon") == "torpedo" and r.get("hit") and not _close(r.get("damage"), torpedo_damage, 0.0051):
                errs.append(f"{what}: turn {t.get('turn_no')}: the planet's torpedo hit did {r.get('damage')!r}, expected torpedo_damage {torpedo_damage:g} (CM-03)")
    if isinstance(snapshot, dict):
        p = planets_of(snapshot).get(planet_id)
        if p is not None and _norm(p.get("cell")) != _norm(list(planet_cell)) and not p.get("destroyed"):
            errs.append(f"{what}: planets[{planet_id}].cell = {p.get('cell')}, expected {list(planet_cell)} — planets cannot move (CM-11, PL-04)")
    return errs


def check_planet_destroyed(report: dict, snapshot: dict | None, *, planet_id: str, key: str, planet_cell: Any, player_before: dict, stock: dict,
                           planet_torpedoes: float | None, what: str) -> list[str]:
    """The planet's shields reach 0 and a hit lands: planet_events destroyed by the player, battle ended 'destroyed' with the player
    as victor, loot = its stock (food, water, elements) and remaining torpedoes, the planet gone from the snapshot and the terrain.
    stock = {"food": {allowed deltas}, "water": {…}, "elements": {element: units}} — the deltas allow for the turn's upkeep and the
    stock regeneration between generation and the kill (PL-02); planet_torpedoes = its CFG-77 ammo before the fight."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    kill = next(((t, e) for t in _turns(report) for e in planet_events_at(t) if e.get("planet") == planet_id and str(e.get("event")) == "destroyed"), None)
    if kill is None:
        last = _turns(report)[-1] if _turns(report) else {}
        tail = [ex for ex in exchanges_at(last) if ex.get("defender") == planet_id]
        errs.append(f"{what}: no planet_events row {{planet {planet_id}, event destroyed}} over {len(_turns(report))} turns (last rows at it: {_short(tail[-2:])}) — "
                    f"a hit on 0 shields destroys a planet too (CM-11, HZ-09, D-145; C11 planet_events)")
        return errs
    t, ev = kill
    n = int(t.get("turn_no") or 0)
    if ev.get("by") not in (pid, (player_before or {}).get("ship")):
        errs.append(f"{what}: planet_events destroyed.by = {ev.get('by')!r}, expected the player {pid} / {player_before.get('ship')} (C11, PR-02)")
    b = battle_between(report, pid or "", planet_id)
    if b is None:
        errs.append(f"{what}: no battle record between the player and {planet_id} (C10)")
    else:
        if b.get("ended") != "destroyed":
            errs.append(f"{what}: battle.ended = {b.get('ended')!r}, expected 'destroyed' (CB-01)")
        if b.get("victor") != pid:
            errs.append(f"{what}: battle.victor = {b.get('victor')!r}, expected the player {pid} (D-134)")
    prev = turn_no(report, n - 1)
    pl, pp = t.get("player") or {}, prev.get("player") or {}
    for k in ("food", "water"):
        allowed = stock.get(k)
        if allowed and isinstance(pl.get(k), int) and isinstance(pp.get(k), int) and pl[k] - pp[k] not in allowed:
            errs.append(f"{what}: turn {n}: player.{k} {pp[k]} → {pl[k]}, expected one of +{sorted(allowed)} looted from the planet's stock (less the turn's upkeep; "
                        f"regeneration since generation allowed) (CM-11, PR-02, D-140)")
    els = stock.get("elements") if isinstance(stock.get("elements"), dict) else {}
    items = player_items_at(t)
    if items is not None:
        for el, u in els.items():
            if items.get(el, 0) < int(u):
                errs.append(f"{what}: turn {n}: player.items = {items}, expected the planet's {el} × {u} looted (CM-11, D-140)")
                break
    if isinstance(snapshot, dict):
        try:
            sid, ship = player_ship(snapshot)
        except KeyError:
            sid, ship = None, {}
        fired = sum(1 for x in _turns(report) for ex in exchanges_at(x) if ex.get("attacker") == sid and ex.get("defender") == planet_id and ex.get("weapon") == "torpedo")
        p_fired = sum(1 for x in _turns(report) for ex in exchanges_at(x) if ex.get("attacker") == planet_id and ex.get("weapon") == "torpedo")
        pt = planet_torpedoes
        if isinstance(pt, (int, float)) and isinstance(player_before.get("torpedoes"), (int, float)):
            want = player_before["torpedoes"] - fired + (pt - p_fired)
            if not _close(ship.get("torpedoes"), want, 1e-6):
                errs.append(f"{what}: player torpedoes after = {ship.get('torpedoes')!r}, expected {player_before['torpedoes']:g} − {fired} fired + the planet's remaining "
                            f"{pt:g} − {p_fired} = {want:g} (CM-11 loot includes torpedoes; D-140)")
        p = planets_of(snapshot).get(planet_id)
        if p is not None and p.get("destroyed") is not True:
            errs.append(f"{what}: planets[{planet_id}] is still in the snapshot with destroyed = {p.get('destroyed')!r} — a destroyed planet is removed (CM-11, D-145)")
        if any(str(x.get("kind")) == "planet" and _norm(x.get("cell")) == _norm(list(planet_cell)) for x in sector_terrain(snapshot, key)):
            errs.append(f"{what}: sectors[{key}].terrain still holds a planet at {list(planet_cell)} after its destruction — the cell becomes Void (M4 § 6, D-145)")
    pd = _get(report, "final", "planets_destroyed")
    if not isinstance(pd, int) or pd < 1:
        errs.append(f"{what}: final.planets_destroyed = {pd!r}, expected ≥ 1 (C11 final additions)")
    return errs


def check_planet_peace(report: dict, snapshot: dict | None, *, planet_id: str, loot_max: float, offer_turn: int, coins_before: tuple[int, int], planet_shield_max: float,
                       what: str) -> list[str]:
    """`peace offer <planet>` from the player: the planet accepts within two turns, victor = planet (CB-03), X per D-46 from the
    shield numbers the report shows, X % of the player's coins transferred (floored), a planet_events 'peace' row."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    b = battle_between(report, pid or "", planet_id)
    if b is None:
        errs.append(f"{what}: no battle between the player and {planet_id} (CM-11)")
        return errs
    end_turn = b.get("_turn")
    if b.get("ended") != "peace" or not isinstance(end_turn, int) or end_turn > offer_turn + 1:
        errs.append(f"{what}: battle.ended = {b.get('ended')!r} at turn {end_turn}, expected 'peace' by turn {offer_turn + 1} after the offer at turn {offer_turn} "
                    f"({PLANET_PEACE_RULE}; CM-09, D-122)")
        return errs
    if b.get("victor") != planet_id:
        errs.append(f"{what}: battle.victor = {b.get('victor')!r}, expected the planet {planet_id} — the side that did not offer (CB-03)")
    tr = b.get("transfer")
    if not isinstance(tr, dict) or not isinstance(tr.get("pct"), (int, float)):
        errs.append(f"{what}: battle.transfer = {_short(tr)}, expected {{gold, silver, items, pct}} (CB-03, C10)")
        return errs
    te = turn_no(report, end_turn)
    tp = turn_no(report, end_turn - 1)
    v_shields = planet_shield_max
    for x in _turns(report):
        if int(x.get("turn_no") or 0) > end_turn:
            break
        for ex in exchanges_at(x):
            if ex.get("defender") == planet_id and isinstance(ex.get("shields_after"), (int, float)):
                v_shields = float(ex["shields_after"])
    loser = [float(v) for v in ((te.get("player") or {}).get("shield_current"), (tp.get("player") or {}).get("shield_current")) if isinstance(v, (int, float))]
    wants = {peace_pct(v_shields, ls, loot_max) for ls in loser} if loser else set()
    if wants and int(tr["pct"]) not in wants:
        errs.append(f"{what}: transfer.pct = {tr['pct']}, expected {sorted(wants)} = floor(max(0, (V − L)/V) × peace_loot_max {loot_max:g}) with the planet's shields {v_shields:g} "
                    f"and the player's {loser} at the turn the battle ended (D-46, CB-03)")
    pct = int(tr["pct"])
    g0, s0 = coins_before
    pl = te.get("player") or {}
    if isinstance(pl.get("gold"), int) and pl["gold"] != g0 - int(math.floor(g0 * pct / 100.0 + 1e-9)):
        errs.append(f"{what}: turn {end_turn}: player.gold = {pl['gold']}, expected {g0} − floor({g0} × {pct} %) (CB-03, D-122)")
    if isinstance(pl.get("silver"), int) and pl["silver"] != s0 - int(math.floor(s0 * pct / 100.0 + 1e-9)):
        errs.append(f"{what}: turn {end_turn}: player.silver = {pl['silver']}, expected {s0} − floor({s0} × {pct} %) (CB-03, D-122)")
    if not any(e.get("planet") == planet_id and str(e.get("event")) == "peace" for e in planet_events_at(te)):
        errs.append(f"{what}: turn {end_turn}: no planet_events row {{planet {planet_id}, event peace}} ({_short(planet_events_at(te))}; C11)")
    return errs


# --------------------------------------------------------------------------- smoke/43 — EF-05 (CFG-31, D-145)

def check_planet_opens(report: dict, snapshot: dict | None, *, planet_id: str, within: int, respect: int, respect_max: int, ratio: float, ratio_min: float, what: str) -> list[str]:
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    b = battle_between(report, pid or "", planet_id)
    if b is None or not isinstance(b.get("opened_turn"), int) or b["opened_turn"] > within:
        errs.append(f"{what}: no battle opened by the planet {planet_id} within {within} turns (battles {_short([x.get('sides') for x in all_battles(report).values()])}) — "
                    f"respect {respect} < planet_attack_respect_max {respect_max} and strength ratio {ratio:.2f} ≥ attack_ratio_min {ratio_min:g} (EF-05, CFG-31, D-145)")
        return errs
    if b.get("opened_by") != planet_id:
        errs.append(f"{what}: battle.opened_by = {b.get('opened_by')!r}, expected the planet {planet_id} (EF-05)")
    t = turn_no(report, b["opened_turn"])
    if not any(e.get("planet") == planet_id and str(e.get("event")) == "opened_fire" for e in planet_events_at(t)):
        errs.append(f"{what}: turn {b['opened_turn']}: no planet_events row {{planet {planet_id}, event opened_fire}} ({_short(planet_events_at(t))}; C11, PR-02)")
    if not planet_rows(t, planet_id):
        errs.append(f"{what}: turn {b['opened_turn']}: no exchange row with the planet as attacker (D-145: it fires both weapons at the enemy in range)")
    return errs


def check_planet_holds(report: dict, snapshot: dict | None, *, planet_id: str, turns: int, why: str, what: str) -> list[str]:
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    for t in _turns(report):
        if int(t.get("turn_no") or 0) > turns:
            break
        mine = [b for b in battles_at(t) if planet_id in {str(s) for s in (b.get("sides") or [])} and (pid is None or pid in {str(s) for s in (b.get("sides") or [])})]
        if mine:
            errs.append(f"{what}: turn {t.get('turn_no')}: the planet {planet_id} is in a battle with the player ({_short(mine[0])}) although {why} (EF-05, CFG-31, D-145)")
            break
        if any(e.get("planet") == planet_id and str(e.get("event")) == "opened_fire" for e in planet_events_at(t)):
            errs.append(f"{what}: turn {t.get('turn_no')}: planet_events says {planet_id} opened fire although {why} (EF-05)")
            break
    if (report.get("turns_completed") or 0) < turns and report.get("outcome") != "running":
        errs.append(f"{what}: the run ended by {report.get('outcome')!r} at turn {report.get('turns_completed')} before the {turns} quiet turns were observed")
    return errs


# --------------------------------------------------------------------------- smoke/44, 45, 12 — the AI economy (D-60, D-144, SUP-03, CFG-81, OV-10)

def _int(v: Any) -> int | None:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def check_ai_supplies(report: dict, *, turns: int, what: str) -> list[str]:
    """200-turn AI-only run: AI buys and restocks, the population stays within 0.7…1.3 × captains_start, AI mutinies ≤ 10 %."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    final = report.get("final") or {}
    start = _int(final.get("captains_start"))
    if start is None:
        errs.append(f"{what}: final.captains_start = {final.get('captains_start')!r}, expected the captain count at turn 0 (C11 final additions)")
        first = _turns(report)[0] if _turns(report) else {}
        start = _int(first.get("captains"))
    pur = _int(final.get("ai_purchases"))
    if pur is None or pur < 1:
        errs.append(f"{what}: final.ai_purchases = {final.get('ai_purchases')!r}, expected ≥ 1 over {turns} turns — AI captains restock at planets (D-60 lifted, D-144, CFG-81)")
    trips = ai_economy_sum(report, "restock_trips")
    if trips is None:
        errs.append(f"{what}: per_turn.ai_economy.restock_trips missing (C11: ai_economy = {{purchases, sales, restock_trips, docked}})")
    elif trips < 1:
        errs.append(f"{what}: ai_economy.restock_trips sums to 0 over {turns} turns — an AI below buy_supplies_below heads for a planet (D-144, CFG-81)")
    pmin, pmax = _int(final.get("population_min")), _int(final.get("population_max"))
    counts = [c for c in (_int(t.get("captains")) for t in _turns(report)) if c is not None]
    if pmin is None or pmax is None:
        errs.append(f"{what}: final.population_min / population_max = {final.get('population_min')!r} / {final.get('population_max')!r}, expected integers (C11)")
    elif counts and (pmin > min(counts) or pmax < max(counts)):
        errs.append(f"{what}: final.population_min/max {pmin}/{pmax} disagree with the per-turn captains range {min(counts)}…{max(counts)} (C11)")
    if start:
        hi, lo = 1.3 * start, 0.7 * start
        highs = counts + ([pmax] if pmax is not None else [])
        lows = counts + ([pmin] if pmin is not None else [])
        top = max(highs) if highs else None
        bottom = min(lows) if lows else None
        if top is not None and top > hi:
            errs.append(f"{what}: population_max {top} > 1.3 × captains_start {start} = {hi:.0f} — mutinies breed captains faster than the economy feeds them (SUP-04c, D-144, M4 § 6)")
        if bottom is not None and bottom < lo:
            errs.append(f"{what}: population_min {bottom} < 0.7 × captains_start {start} = {lo:.0f} — the AI dies out (D-144)")
        mut = _int(final.get("ai_mutinies"))
        if mut is None:
            errs.append(f"{what}: final.ai_mutinies = {final.get('ai_mutinies')!r}, expected a count (C11)")
        elif mut > 0.1 * start:
            errs.append(f"{what}: final.ai_mutinies = {mut} > 10 % of captains_start {start} over {turns} turns — the AI starves (SUP-03, D-144, CFG-81)")
    return errs


def check_knobs_live(report_default: dict, report_off: dict, *, what: str) -> list[str]:
    """With docking_income_silver = 0 and buy_supplies_below = 0 the AI cannot restock, so ai_mutinies rise above the default run's."""
    errs = _prefix(report_off, what, 0)
    if errs:
        return errs
    d, o = _int(_get(report_default, "final", "ai_mutinies")), _int(_get(report_off, "final", "ai_mutinies"))
    if d is None or o is None:
        errs.append(f"{what}: final.ai_mutinies = {d!r} (default) / {o!r} (knobs at 0), expected counts (C11)")
    elif not o > d:
        errs.append(f"{what}: final.ai_mutinies with buy_supplies_below = 0 and docking_income_silver = 0 is {o}, not above the default run's {d} over its {report_default.get('turns_completed')} "
                    f"turns — the CFG-81 knobs are not live (D-49, D-144)")
    return errs


def check_ai_economy(report: dict, *, turns: int, what: str) -> list[str]:
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    sales, pur = ai_economy_sum(report, "sales"), ai_economy_sum(report, "purchases")
    if sales is None or pur is None:
        errs.append(f"{what}: per_turn.ai_economy sales / purchases missing (C11)")
    else:
        if sales < 1:
            errs.append(f"{what}: ai_economy.sales sums to 0 over {turns} turns — docked AI captains sell their elements (OV-10, D-144)")
        if pur < 1:
            errs.append(f"{what}: ai_economy.purchases sums to 0 over {turns} turns — docked AI captains buy supplies and torpedoes (D-144)")
    fp = _int(_get(report, "final", "pickups"))
    if fp is None or fp < 1:
        errs.append(f"{what}: final.pickups = {_get(report, 'final', 'pickups')!r}, expected ≥ 1 over {turns} turns — an AI picks up the items it lands on after its automatic scan "
                    f"(D-144 [ASSUMED]; {PICKUP_RULE})")
    return errs


def check_long_run_economy(report: dict | None, *, turns: int, what: str) -> list[str]:
    """smoke/12 at M4: the universe trades and stays bounded (D-69, D-144)."""
    if not isinstance(report, dict):
        return []
    errs: list[str] = []
    final = report.get("final") or {}
    tr = _int(final.get("trades"))
    if tr is None or tr < 1:
        errs.append(f"{what}: final.trades = {final.get('trades')!r}, expected ≥ 1 over {turns} AI-only turns (C11, OV-10, D-144)")
    pd = _int(final.get("planets_destroyed"))
    if pd is None or pd < 0:
        errs.append(f"{what}: final.planets_destroyed = {final.get('planets_destroyed')!r}, expected an integer ≥ 0 (C11)")
    start, pmax = _int(final.get("captains_start")), _int(final.get("population_max"))
    if start is None or pmax is None:
        errs.append(f"{what}: final.captains_start / population_max = {final.get('captains_start')!r} / {final.get('population_max')!r}, expected integers (C11)")
    elif pmax > 1.3 * start:
        errs.append(f"{what}: population_max {pmax} > 1.3 × captains_start {start} over {turns} turns (D-144, D-69)")
    for k in ("pickups", "upgrades", "ai_purchases", "ai_mutinies", "population_min"):
        if _int(final.get(k)) is None:
            errs.append(f"{what}: final.{k} = {final.get(k)!r}, expected an integer (C11 final additions)")
    return errs


# --------------------------------------------------------------------------- smoke/46 — M3 fix-ups (D-137, D-138, D-139, CFG-74, CFG-75, D-146)

def check_fire_all(report: dict, snapshot: dict | None, *, what: str) -> list[str]:
    """`fire <id>` at turn 1: exactly one torpedo row and one phaser row from the player's ship at the spawn (D-137)."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    t1 = turn_no(report, 1)
    try:
        psid = player_ship(snapshot)[0] if isinstance(snapshot, dict) else None
    except KeyError:
        psid = None
    rows = [ex for ex in exchanges_at(t1) if ex.get("defender") == sp["ship"] and (psid is None or ex.get("attacker") == psid)]
    weapons = Counter(str(r.get("weapon")) for r in rows)
    if weapons.get("torpedo", 0) != 1 or weapons.get("phaser", 0) != 1:
        rej = rejected_texts(t1)
        errs.append(f"{what}: turn 1: the player's exchange rows at {sp['ship']} are {dict(weapons)}, expected exactly one torpedo and one phaser row"
                    + (f"; rejected: {rej[0][:140]!r}" if rej else "") + " — `fire <target>` fires one torpedo and the phasers (CMD-08, D-137)")
    return errs


def check_help_fire(text: str | None, *, torpedo_damage: float, hit_pct: int, phaser_damages: list[float], what: str) -> list[str]:
    """`help fire` prints the live numbers: torpedo damage, hit chance vs engine 4 (as a percentage), phaser damage at d 0/1/2 (UX-09, D-138)."""
    if not isinstance(text, str) or not text.strip():
        return [f"{what}: no `help fire` text came back from render_command.help_text('fire', cfg) (UX-09, D-138, C4)"]
    errs: list[str] = []
    low = text.lower()
    if f"{torpedo_damage:g}" not in text:
        errs.append(f"{what}: `help fire` does not show torpedo_damage {torpedo_damage:g} from the ini (UX-09: live numbers, never literals; D-138, D-49)")
    if str(hit_pct) not in text:
        errs.append(f"{what}: `help fire` does not show the torpedo hit chance {hit_pct} % against engine 4 (UX-09 example 'torpedo 25 at 58 % vs engine 4')")
    for d, dmg in enumerate(phaser_damages):
        if f"{dmg:g}" not in text:
            errs.append(f"{what}: `help fire` does not show the phaser damage {dmg:g} at distance {d} (UX-09 'phasers 15 / 10 / 5')")
    if "torpedo" not in low or "phaser" not in low:
        errs.append(f"{what}: `help fire` text {text[:120]!r} names neither weapon (CMD-08)")
    return errs


def check_class_mix(snapshot: dict | None, *, cls: str, what: str) -> list[str]:
    """[world] ai_class_mix = <cls>:100 → every generated AI ship is of that class (CFG-75, D-131, D-146)."""
    if not isinstance(snapshot, dict):
        return [f"{what}: no snapshot to read the AI ships from (C6)"]
    pid = snapshot.get("player_id")
    others = {sid: s for sid, s in (snapshot.get("ships") or {}).items() if isinstance(s, dict) and s.get("captain") != pid}
    wrong = {sid: s.get("class") for sid, s in others.items() if str(s.get("class", "")).lower() != cls}
    if not others:
        return [f"{what}: the snapshot holds no AI ship (SP-06)"]
    if wrong:
        sid = next(iter(wrong))
        return [f"{what}: {len(wrong)} of {len(others)} AI ships are not {cls!r} with [world] ai_class_mix = {cls}:100, e.g. {sid} is {wrong[sid]!r} — the class mix is read from "
                f"the ini, not code (CFG-75, D-131, D-146)"]
    return []


def check_ai_trace(log_battle: str | None, log_all: str | None, *, what: str) -> list[str]:
    """[logging] ai_trace = battle → no 'random walk' decision lines; all → some (CFG-74, D-139, OB-05)."""
    errs: list[str] = []
    if log_battle is None or log_all is None:
        return [f"{what}: logs/game-<seed>.log missing under the sim's --save-dir ({'battle' if log_battle is None else 'all'} run) — the engine writes gameplay logs (D-135, D-139, OB-05)"]
    nb, na = log_battle.lower().count("random walk"), log_all.lower().count("random walk")
    if nb != 0:
        errs.append(f"{what}: {nb} 'random walk' line(s) in game-<seed>.log with [logging] ai_trace = battle, expected 0 (CFG-74: battle = battles and roams only; D-139)")
    if na < 1:
        errs.append(f"{what}: no 'random walk' line in game-<seed>.log with [logging] ai_trace = all, expected some (CFG-74, OB-05)")
    return errs


def check_first_names(snapshot: dict | None, *, minimum: int, what: str) -> list[str]:
    if not isinstance(snapshot, dict):
        return [f"{what}: no snapshot to read the captains from (C6)"]
    pid = snapshot.get("player_id")
    names = {str(c.get("name", "")).split()[0] for cid, c in (snapshot.get("captains") or {}).items() if cid != pid and isinstance(c, dict) and str(c.get("name", "")).strip()}
    if len(names) < minimum:
        return [f"{what}: {len(names)} distinct AI first names among the captains, expected ≥ {minimum} (D-90, D-146: at least 40 first names in the pool)"]
    return []


# --------------------------------------------------------------------------- smoke/47 — economy UI (PNL-04, SYM-09, OUT-07, OUT-08, OUT-10, UX-09)

ECONOMY_EVENTS = [
    {"phase": "TP-05", "type": "trade", "severity": "info", "text": "Bought 10 food from Halvenmoor for 18 silver", "ids": ["OUT-07"]},
    {"phase": "TP-05", "type": "upgrade", "severity": "info", "text": "Shields upgraded with rhodium: 60 → 65", "ids": ["OUT-08"]},
    {"phase": "TP-05", "type": "pickup", "severity": "highlight", "text": "Picked up 2 iridium", "ids": ["OUT-10"]},
    {"phase": "TP-01", "type": "scan", "severity": "info", "text": "Scan: orichalcum (1 unit, special) lies in this cell", "ids": ["OUT-10"]},
    {"phase": "TP-05", "type": "planet", "severity": "alert", "text": "Halvenmoor refuses to trade: respect 12 < 20 (EF-04)", "ids": ["PR-02"]},
]
ENEMY_SHIELDS, ENEMY_MAX = 37.0, 80.0


def economy_render_snapshots(snap: dict, k: dict) -> dict[str, Any]:
    """From a real C3 snapshot: 'plain' (no cargo, no planet, no item, no battle); 'cargo' (two stacks); 'planet_trades' /
    'planet_refuses' (a planet in the player's cell, C11 record); 'item_hidden' / 'item_revealed' (one stack two cells right);
    'battle' (an enemy in battle at 37/80 shields, scanned, player fear from the snapshot).  k: the ini numbers the panel shows."""
    base = copy.deepcopy(snap)
    sid, ship = player_ship(base)
    pid = base["player_id"]
    w, h = base["sizes"]["sector"]
    (gx, gy), (sx, sy), _ = ship["position"]
    cx, cy = (10, 8) if (w > 12 and h > 10) else (min(w - 3, 2), min(h - 2, 2))
    ship["position"] = [[gx, gy], [sx, sy], [cx, cy]]
    ship.update(hazard=None, move_remaining=None, orders=[], in_battle=None, target=None)
    base["game_over"] = False
    base["battles"] = []
    base.setdefault("planets", {})
    key = f"{gx},{gy}/{sx},{sy}"
    sector = base.setdefault("sectors", {}).setdefault(key, {"terrain": [], "items": []})
    probe_cells = ([cx + dx, cy] for dx in (-1, 0, 1, 2, 3))
    blocked = [list(c) for c in probe_cells]
    sector["terrain"] = [t for t in sector.get("terrain") or [] if list(t.get("cell") or []) not in blocked]
    sector["items"] = [i for i in sector.get("items") or [] if list(i.get("cell") or []) not in blocked]
    for other_sid, other in list(base["ships"].items()):
        if other_sid != sid and other.get("position", [[], [], []])[:2] == [[gx, gy], [sx, sy]]:
            other["position"][2] = [max(0, cx - 5), max(0, cy - 5)]
        other.setdefault("in_battle", None)
        other.setdefault("target", None)
    cap = base["captains"][pid]
    cap["items"] = {}
    cap.setdefault("visible_reputation", False)
    cap["respect"] = 50
    plain = base
    cargo = copy.deepcopy(plain)
    cargo["captains"][pid]["items"] = {"iridium": 2, "orichalcum": 1}
    planet_id = "p-gate"
    rec = {"name": "Halvenmoor", "president": "Ysolde Marrick", "cell": [cx, cy], "sector": key, "shields": float(k["planet_shield_max"]), "shield_max": float(k["planet_shield_max"]),
           "torpedoes": int(k["planet_torpedoes"]), "phasers": float(k["planet_phasers"]), "stock": {"food": 100, "water": 100, "elements": {"iridium": 3}},
           "disposition_to_player": 0, "trades_with_player": True, "in_battle": None, "destroyed": False}
    trades = copy.deepcopy(plain)
    trades["planets"][planet_id] = copy.deepcopy(rec)
    trades["sectors"][key]["terrain"].append({"kind": "planet", "cell": [cx, cy], "name": "Halvenmoor"})
    refuses = copy.deepcopy(trades)
    refuses["captains"][pid]["respect"] = 10
    refuses["planets"][planet_id].update(disposition_to_player=-80, trades_with_player=False)
    item_cell = [cx + 2, cy]
    hidden = copy.deepcopy(plain)
    hidden["sectors"][key]["items"].append({"id": "i-gate", "cell": item_cell, "rarity": "normal", "revealed": False})
    revealed = copy.deepcopy(plain)
    revealed["sectors"][key]["items"].append({"id": "i-gate", "cell": item_cell, "element": "iridium", "units": 2, "rarity": "normal", "revealed": True})
    enemy_sid, enemy_cid = "s-gate-enemy", "c-gate-enemy"
    battle = copy.deepcopy(plain)
    battle["captains"][enemy_cid] = {"name": "Vesper Quill", "is_player": False, "gold": 3, "silver": 20, "fear": 25, "respect": 12, "food": 30, "water": 30,
                                     "ships": [enemy_sid], "active_ship": enemy_sid, "items": {}, "is_master": False, "mutiny_countdown": None, "visible_reputation": True}
    battle["ships"][enemy_sid] = {"name": "Grey Heron", "class": "corsair", "captain": enemy_cid, "torpedoes": 11, "phasers": 5.0, "shield_max": ENEMY_MAX,
                                  "shield_current": ENEMY_SHIELDS, "engine": 6.0, "warp": 5.0, "position": [[gx, gy], [sx, sy], [cx + 1, cy]], "orders_queued": 0,
                                  "orders": [], "hazard": None, "move_remaining": None, "in_battle": "b-001", "target": sid}
    battle["ships"][sid].update(in_battle="b-001", target=enemy_sid)
    battle["battles"] = [{"id": "b-001", "sides": [pid, enemy_cid], "opened_turn": max(1, int(battle.get("turn_no") or 1) - 1), "opened_by": enemy_cid,
                          "strength_at_open": {pid: 255.0, enemy_cid: 455.0}, "ended": None, "victor": None, "transfer": None}]
    fear = float(battle["captains"][pid].get("fear", 10))
    thr = peace_threshold(k["peace_shield_pct"], k["peace_pct_at_fear100"], fear)
    return {"plain": plain, "cargo": cargo, "planet_trades": trades, "planet_refuses": refuses, "item_hidden": hidden, "item_revealed": revealed, "battle": battle,
            "ship_id": sid, "player_id": pid, "planet_id": planet_id, "planet": rec, "item_cell": item_cell, "enemy_ship": enemy_sid, "enemy_captain": enemy_cid,
            "shield_pct": int(round(ENEMY_SHIELDS / ENEMY_MAX * 100)), "peace_threshold": thr, "player_cell": [cx, cy],
            "food_price_texts": sorted({f"{k['food_buy']:g}", f"{k['food_buy'] * price_factor(50, k['planet_price_band'], selling=False):.1f}",
                                        f"{k['food_buy'] * price_factor(50, k['planet_price_band'], selling=False):.2f}",
                                        str(int(math.floor(k['food_buy'] * price_factor(50, k['planet_price_band'], selling=False) + 1e-9)))})}


def _added_lines(a: str, b: str) -> list[str]:
    seen = set(b.splitlines())
    return [ln for ln in a.splitlines() if ln not in seen]


def check_economy_render(probe: dict, variants: dict, k: dict) -> list[str]:
    """probe = {"renders": {"status": [cargo, plain, planet_trades, planet_refuses, battle], "grid": [item_hidden, item_revealed, plain],
    "output": [events_text]}, "errors": {…}}."""
    errs: list[str] = []
    for role, err in (probe.get("errors") or {}).items():
        errs.append(f"render_{role}.render() raised: {err.strip().splitlines()[-1] if err.strip() else err}")
    for mod, err in (probe.get("import_errors") or {}).items():
        errs.append(f"import {mod} failed: {err.strip().splitlines()[-1] if err.strip() else err}")
    renders = probe.get("renders") or {}
    st = renders.get("status") or []
    if len(st) < 5:
        errs.append("render_status: no rendered text captured for the cargo / plain / planet / battle snapshots")
    else:
        cargo, plain, trades, refuses, battle = st[:5]
        added = "\n".join(_added_lines(cargo, plain)).lower()
        for el, units, value in (("iridium", 2, k["elements"]["iridium"]), ("orichalcum", 1, k["elements"]["orichalcum"])):
            if el not in added:
                errs.append(f"render_status: the cargo stack {el!r} is not among the lines added by the items (PNL-04 Cargo: each element stack with units and value; C4)")
            elif not re.search(rf"\b{units}\b", added):
                errs.append(f"render_status: the units {units} of {el} do not appear in the Cargo lines (PNL-04)")
            elif not (re.search(rf"\b{int(value * units)}\b", added) or re.search(rf"\b{int(value)}\b", added)):
                errs.append(f"render_status: neither the stack value {int(value * units)} nor the unit value {int(value)} of {el} appears in the Cargo lines (PNL-04: list value; [elements])")
        p = variants["planet"]
        padd = "\n".join(_added_lines(trades, plain))
        low = padd.lower()
        if p["name"].lower() not in low:
            errs.append(f"render_status: the planet in the player's cell {p['name']!r} is not among the lines added by the planet (C4: T4 shows the planet in the player's cell)")
        if p["president"].lower() not in low:
            errs.append(f"render_status: the president {p['president']!r} does not appear in the planet panel (PL-05, C4)")
        if not re.search(r"(?<![\d-])0\b", padd) or "trade" not in low:
            errs.append("render_status: the planet panel does not show disposition 0 and that the planet trades (PL-06, EF-04, C4)")
        if "food" not in low or not any(re.search(rf"(?<![\d.]){re.escape(txt)}(?![\d.])", padd) for txt in variants["food_price_texts"]):
            errs.append(f"render_status: the planet panel does not show the food buy price (one of {variants['food_price_texts']} with 'food'; PRC-01, C4)")
        radd = "\n".join(_added_lines(refuses, plain)).lower()
        if "refus" not in radd or "80" not in radd:
            errs.append("render_status: with trades_with_player false and disposition −80 the planet panel does not say it refuses / show −80 (EF-04, PL-06, C4)")
        badd = "\n".join(_added_lines(battle, plain))
        pct, thr = variants["shield_pct"], variants["peace_threshold"]
        if not re.search(rf"\b{pct}\s*%", badd):
            errs.append(f"render_status: the battle panel does not show the enemy's shields as {pct} % ({ENEMY_SHIELDS:g} of {ENEMY_MAX:g}; UX-09, D-138)")
        if not any(re.search(rf"\b{t}\b", badd) for t in {f"{thr:g}", str(int(round(thr))), str(int(thr))}):
            errs.append(f"render_status: the battle panel does not show the scanned enemy's peace threshold {thr:g} % (peace_shield_pct + (at_fear100 − peace_shield_pct) × fear/100; UX-09, EF-02, D-138)")
    g = renders.get("grid") or []
    if len(g) < 3:
        errs.append("render_grid: no rendered text captured for the hidden / revealed / plain item snapshots")
    else:
        hidden, revealed, plain = g[:3]
        if hidden != plain:
            errs.append(f"render_grid: an unrevealed item at {variants['item_cell']} changes the grid — items show only after a scan (SYM-09, GRD-04)")
        if revealed == plain:
            errs.append(f"render_grid: a revealed item at {variants['item_cell']} draws nothing (SYM-09: item glyph after a scan)")
    out = renders.get("output") or []
    if not out:
        errs.append("render_output: no rendered text captured for the economy events")
    else:
        text = out[0].lower()
        for needle, label in (("18", "OUT-07 silver"), ("food", "OUT-07 item"), ("65", "OUT-08 after"), ("shields", "OUT-08 component"), ("iridium", "OUT-10 pickup"),
                              ("orichalcum", "OUT-10 scan"), ("ef-04", "planet event")):
            if needle not in text:
                errs.append(f"render_output: {label}: {needle!r} missing from the render of the economy events (C4, {label.split()[0]})")
        last = -1
        for ev in ECONOMY_EVENTS:
            i = text.find(ev["text"].split(":")[0].lower()[:12])
            if 0 <= i < last:
                errs.append("render_output: economy events are not in the given (phase) order (OB-04)")
                break
            last = max(last, i)
    return errs


# --------------------------------------------------------------------------- smoke/48 — save / load (C6, OV-12)

def check_save_economy(snapshot: dict | None, *, destroyed_planet: str, refused_respect: int | None, upgraded: dict, what: str) -> list[str]:
    """The saved snapshot after the run: the destroyed planet stays gone, every other planet refuses at the saved respect, the
    upgraded stats hold; upgraded = {stat: value}."""
    if not isinstance(snapshot, dict):
        return [f"{what}: no save snapshot (C6)"]
    errs: list[str] = []
    planets = planets_of(snapshot)
    p = planets.get(destroyed_planet)
    if p is not None and p.get("destroyed") is not True:
        errs.append(f"{what}: the destroyed planet {destroyed_planet} is back in the saved snapshot with destroyed = {p.get('destroyed')!r} (C6, CM-11: a destroyed planet stays gone)")
    others = {k: v for k, v in planets.items() if k != destroyed_planet and not v.get("destroyed")}
    if refused_respect is not None:
        bad = [k for k, v in others.items() if v.get("trades_with_player") is not False or v.get("disposition_to_player") != disposition(refused_respect)]
        if bad:
            errs.append(f"{what}: planets {bad[:3]} do not record the refusal at respect {refused_respect} (trades_with_player false, disposition {disposition(refused_respect)}; "
                        f"C6 save: disposition inputs survive)")
    try:
        _, ship = player_ship(snapshot)
    except KeyError as e:
        return errs + [f"{what}: {e}"]
    for stat, want in upgraded.items():
        if not _close(ship.get(stat), want, 1e-6):
            errs.append(f"{what}: the saved ship's {stat} = {ship.get(stat)!r}, expected {want:g} after the upgrade (C6, UP-01…05)")
    return errs


# --------------------------------------------------------------------------- live/10 — a buy over the bus (I/O driver + pure checker)

def drive_market(pub_port: int, pull_port: int, *, planet_cell: Any, n: int = 5, hello_timeout: float = 15.0, reply_timeout: float = 5.0, turn_timeout: float = 10.0,
                 after_first: Callable[[dict], Any] | None = None) -> dict:
    """live/10: from state.hello read the player's coins / food and the planet in its cell, push `buy food n`, then turn.end;
    record the reply, the turn.resolved event types and the player's food / coins after.  `after_first(obs)` runs once the
    turn.resolved is in (the live gate reads the heartbeats there)."""
    obs: dict = {"hello": None, "player_id": None, "ship_id": None, "planet": None, "cell": None, "before": None, "after": None, "buy_reply": None, "resolved": None,
                 "after_first": None, "notes": []}
    bo = BusObservations()
    client = BusClient(pub_port, pull_port)
    try:
        first = client.wait_for("state.hello", hello_timeout, bo)
        if first is None:
            obs["notes"].append(f"no state.hello within {hello_timeout:.0f} s (topics seen: {dict(bo.topics) or 'none'})")
            return obs
        snapshot = first.get("snapshot") or {}
        obs["hello"] = {"turn_no": first.get("turn_no"), "seed": first.get("seed")}
        pid = snapshot.get("player_id")
        obs["player_id"] = pid
        turn_no_ = int(first.get("turn_no") or snapshot.get("turn_no") or 0)
        try:
            sid, ship = player_ship(snapshot)
        except KeyError as e:
            obs["notes"].append(str(e))
            return obs
        obs["ship_id"] = sid
        pos = ship.get("position") or [[0, 0], [0, 0], [0, 0]]
        obs["cell"] = pos[2]
        key = sector_key(pos[0], pos[1])
        got = planet_in(snapshot, key, pos[2])
        obs["planet"] = {"id": got[0], "name": got[1].get("name"), "trades": got[1].get("trades_with_player")} if got else None
        if _norm(pos[2]) != _norm(list(planet_cell)):
            obs["notes"].append(f"the player's ship stands at {pos[2]}, not on the planet cell {list(planet_cell)} (--teleport)")
        cap = (snapshot.get("captains") or {}).get(pid) or {}
        obs["before"] = {"food": cap.get("food"), "gold": cap.get("gold"), "silver": cap.get("silver"), "respect": cap.get("respect")}
        client.send("order.submitted", {"command": "buy", "args": ["food", str(n)], "ship_id": sid, "turn_no": turn_no_, "client": "command"})
        rep = client.wait_for_any(("order.accepted", "order.rejected"), reply_timeout, bo)
        obs["buy_reply"] = {"topic": rep[0], "reason": str((rep[1] or {}).get("reason", ""))} if rep else None
        client.send("turn.end", {"turn_no": turn_no_})
        res = client.wait_for("turn.resolved", turn_timeout, bo, pred=lambda p: isinstance(p.get("turn_no"), int) and p["turn_no"] > turn_no_)
        if res is None:
            obs["notes"].append(f"no turn.resolved within {turn_timeout:.0f} s after turn.end for turn {turn_no_}")
            return obs
        snap = res.get("snapshot") or {}
        cap2 = (snap.get("captains") or {}).get(pid) or {}
        evs = res.get("events") or []
        obs["resolved"] = {"turn_no": res.get("turn_no"), "types": [e.get("type") for e in evs if isinstance(e, dict)],
                           "trade_texts": [str(e.get("text", ""))[:120] for e in evs if isinstance(e, dict) and e.get("type") == "trade"]}
        obs["after"] = {"food": cap2.get("food"), "gold": cap2.get("gold"), "silver": cap2.get("silver")}
        if after_first is not None:
            obs["after_first"] = after_first(obs)
        return obs
    finally:
        client.close()


def check_market_bus(obs: dict, *, n: int, cost_options: set[int], spg: float) -> list[str]:
    """live/10 — CMD-20, OUT-07 (C3 type 'trade'), C11 snapshot additions."""
    errs: list[str] = []
    cost_options = {int(x) for x in cost_options}
    if obs.get("hello") is None:
        return ["no state.hello from the engine: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C3)"]
    if obs.get("ship_id") is None:
        return ["the player's ship is missing from state.hello: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C3)"]
    for note in obs.get("notes") or []:
        if "not on the planet cell" in note:
            errs.append(note + " — the launcher forwards --teleport to the engine (C9, D-104)")
    if obs.get("planet") is None:
        errs.append(f"state.hello's snapshot lists no planet at the player's cell {obs.get('cell')} in snapshot.planets (C11 snapshot additions)")
    elif obs["planet"].get("trades") is not True:
        errs.append(f"snapshot.planets[{obs['planet'].get('id')}].trades_with_player = {obs['planet'].get('trades')!r} at the launch respect, expected true (EF-04, --respect)")
    br = obs.get("buy_reply")
    if br is None:
        errs.append(f"no reply to order.submitted 'buy food {n}' (MSG-06 / MSG-07)")
    elif br.get("topic") != "order.accepted":
        errs.append(f"'buy food {n}' was rejected: {br.get('reason')!r} — on a trading planet with coins to spare it is queued (CMD-20, PR-04)")
    res = obs.get("resolved")
    if res is None:
        errs.append("no turn.resolved after turn.end: " + "; ".join(obs.get("notes") or ["no notes"]) + " (MSG-02 → MSG-03)")
        return errs
    if "trade" not in (res.get("types") or []):
        errs.append(f"turn.resolved {res.get('turn_no')} events carry no type 'trade' (types {res.get('types')}) — the buy publishes an OUT-07 line (C3 additions, C11)")
    b, a = obs.get("before") or {}, obs.get("after") or {}
    if isinstance(b.get("food"), int) and isinstance(a.get("food"), int) and a["food"] - b["food"] not in (n, n - 1):
        errs.append(f"turn.resolved snapshot: the player's food {b['food']} → {a['food']}, expected +{n} (less the turn's upkeep) (CMD-20, TR-04)")
    wb, wa = wealth(b, spg), wealth(a, spg)
    if wb is not None and wa is not None and wb - wa not in cost_options:
        errs.append(f"turn.resolved snapshot: coins fell by {wb - wa} silver-equivalent, expected {sorted(cost_options)} for {n} food (PRC-01, EF-04 factor; {ROUNDING_RULE})")
    return errs
