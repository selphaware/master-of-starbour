"""verify/_combat.py — M3 checkers shared by smoke_test.py and live_test.py (specs/M3.md § 4, CONTRACTS C10).

Same split as _checks.py: **pure checkers** (functions of a C2+C9+C10 report, a snapshot, bus
observations or rendered text → a list of problem strings) and **thin I/O adapters** (the bus
combat driver) with no pass/fail logic.  Scenarios are *plans* built from world_index, the
ini and the C10 debug flags (`--spawn`, `--ai passive`, multi-order script lines); every
number comes from the ini the gate was handed, never a literal (M3 § 4).

Conventions the gate relies on, stated in every message that uses them (D-128):
  * a rejected script order is a C2 event string containing 'rejected' (the sim's Recorder note,
    D-104) — the order.rejected reason is carried in that string;
  * `spawned` (C10, "turn 0 only") is read from the report's top level, else from per_turn[0];
  * an AI ship in a battle holds its cell unless its EF-02 rules say escape, so a spawn distance
    is the exchange distance (M3 § 4: "so distances and strengths are known");
  * when several EF-02 rules apply at once, surrender > escape > peace;
  * a peace transfer is judged against D-46 with the shield numbers the report shows for the
    victor and the loser at the turn the battle ended, with or without that turn's recharge;
  * "in range" is Chebyshev distance ≤ range (CM-02), so an escape must end *beyond* the longest
    range either side has.
"""
from __future__ import annotations

import copy
import math
import re
import time
from typing import Any, Callable

from _checks import (BusClient, BusObservations, _close, _get, _norm, _short, ansi_cells, canonical_hash, chebyshev,
                     event_text, glyph_positions, in_sector, is_mutiny_event, player_ship, report_shape_errors,
                     signed, terrain_cells)

REJECTED_RULE = "a rejected script order is an event string containing 'rejected' (sim Recorder note, D-104, C10)"
PEACE_RULE = "a peace offer / acceptance is an event string containing 'peace' (OUT-05, C3 type 'peace')"
EXCHANGE_KEYS = ("attacker", "defender", "weapon", "distance", "hit", "damage", "shields_after")
BATTLE_KEYS = ("id", "sides", "opened_turn", "opened_by", "strength_at_open", "exchanges", "ended", "victor", "transfer")
REPUTATION_KEYS = ("captain", "rule", "fear", "respect", "scale")


# --------------------------------------------------------------------------- formulas (tabs 13 / 14)

def phaser_damage(phasers: float, per_unit: float, distance: int) -> float:
    """CM-04: phasers × phaser_damage_per_unit × (1 − d/3)."""
    return float(phasers) * float(per_unit) * (1.0 - float(distance) / 3.0)


def torpedo_hit_p(base: float, evade_per_engine: float, target_engine: float) -> float:
    """CM-03: p = torpedo_hit_base − torpedo_evade_per_engine × target engine."""
    return float(base) - float(evade_per_engine) * float(target_engine)


def strength(shield_current: float, torpedoes: float, phasers: float, *, torpedo_damage: float, phaser_per_unit: float) -> float:
    """CM-08 (M3 § 2): shield_current + torpedoes × torpedo_damage + phasers × phaser_damage_per_unit × 3."""
    return float(shield_current) + float(torpedoes) * float(torpedo_damage) + float(phasers) * float(phaser_per_unit) * 3.0


def clamp_scale(ratio: float, lo: float, hi: float) -> float:
    """LV-03 / CFG-11: clamp(opponent / you at open, scale_min, scale_max)."""
    return max(float(lo), min(float(hi), float(ratio)))


def peace_pct(victor_shields: float, loser_shields: float, loot_max: float) -> int:
    """D-46 / CB-03: X = floor(max(0, (V − L) / V) × peace_loot_max)."""
    v = float(victor_shields)
    if v <= 0:
        return 0
    return int(math.floor(max(0.0, (v - float(loser_shields)) / v) * float(loot_max) + 1e-9))


def peace_threshold_pct(peace_pct_at_fear0: float, peace_pct_at_fear100: float, player_fear: float) -> float:
    """EF-02 / CFG-30, CFG-72: peace_shield_pct + (peace_shield_pct_at_fear100 − peace_shield_pct) × fear / 100."""
    return float(peace_pct_at_fear0) + (float(peace_pct_at_fear100) - float(peace_pct_at_fear0)) * float(player_fear) / 100.0


def spawn_arg(cls: str, galaxy: Any, sector: Any, cell: Any, fear: int | None = None, respect: int | None = None) -> str:
    """C10: --spawn "<class> gx gy sx sy cx cy [fear] [respect]"."""
    s = f"{cls} {int(galaxy[0])} {int(galaxy[1])} {int(sector[0])} {int(sector[1])} {int(cell[0])} {int(cell[1])}"
    if fear is not None:
        s += f" {int(fear)}"
        if respect is not None:
            s += f" {int(respect)}"
    return s


# --------------------------------------------------------------------------- report accessors (pure)

def spawned_entries(report: dict) -> list[dict]:
    """C10 `spawned` (turn 0 only): the report's top level, else per_turn[0] (D-128)."""
    top = report.get("spawned")
    if isinstance(top, list):
        return [e for e in top if isinstance(e, dict)]
    per_turn = report.get("per_turn") or []
    if per_turn and isinstance(per_turn[0], dict) and isinstance(per_turn[0].get("spawned"), list):
        return [e for e in per_turn[0]["spawned"] if isinstance(e, dict)]
    return []


def spawned_error(report: dict, n: int) -> str | None:
    sp = spawned_entries(report)
    if len(sp) < n:
        return (f"report.spawned lists {len(sp)} ship(s), expected {n} from --spawn (C10: 'spawned (turn 0 only) = the ships --spawn created, "
                f"with ids' — read from the report's top level or per_turn[0]; D-126)")
    for i, e in enumerate(sp[:n]):
        for k in ("ship", "captain"):
            if not isinstance(e.get(k), str) or not e[k]:
                return f"report.spawned[{i}].{k} = {e.get(k)!r}, expected the created ship / captain id (C10, D-126)"
    return None


def battles_at(turn: dict) -> list[dict]:
    b = turn.get("battles")
    return [x for x in b if isinstance(x, dict)] if isinstance(b, list) else []


def all_battles(report: dict) -> dict[str, dict]:
    """Every battle id → its latest per-turn record (per_turn order)."""
    out: dict[str, dict] = {}
    for t in report.get("per_turn") or []:
        if not isinstance(t, dict):
            continue
        for b in battles_at(t):
            bid = str(b.get("id"))
            out[bid] = dict(b, _turn=t.get("turn_no"))
    return out


def battle_between(report: dict, a: str, b: str) -> dict | None:
    for bt in all_battles(report).values():
        sides = bt.get("sides")
        if isinstance(sides, list) and {str(x) for x in sides} == {a, b}:
            return bt
    return None


def exchanges_at(turn: dict, battle_sides: set[str] | None = None) -> list[dict]:
    rows = []
    for b in battles_at(turn):
        if battle_sides is not None and {str(x) for x in (b.get("sides") or [])} != battle_sides:
            continue
        for ex in b.get("exchanges") or []:
            if isinstance(ex, dict):
                rows.append(ex)
    return rows


def reputation_at(turn: dict) -> list[dict]:
    r = turn.get("reputation")
    return [x for x in r if isinstance(x, dict)] if isinstance(r, list) else []


def events_mentioning(turn: dict, word: str) -> list[str]:
    return [event_text(e) for e in (turn.get("events") or []) if word.lower() in event_text(e).lower()]


def battle_shape_errors(b: Any, where: str) -> list[str]:
    if not isinstance(b, dict):
        return [f"{where}: battle record is {type(b).__name__}, expected an object (C10)"]
    errs = [f"{where}: battles[*] lacks {k!r} (C10)" for k in BATTLE_KEYS if k not in b]
    if not isinstance(b.get("sides"), list) or len(b.get("sides") or []) != 2:
        errs.append(f"{where}: battles[*].sides = {_short(b.get('sides'))}, expected two captain ids (C10)")
    if b.get("ended") not in (None, "destroyed", "peace", "escape"):
        errs.append(f"{where}: battles[*].ended = {b.get('ended')!r}, expected null or destroyed|peace|escape (C10)")
    so = b.get("strength_at_open")
    if not isinstance(so, dict) or any(not isinstance(v, (int, float)) for v in so.values()):
        errs.append(f"{where}: battles[*].strength_at_open = {_short(so)}, expected {{captain: number}} (C10, CM-08, D-121)")
    for i, ex in enumerate((b.get("exchanges") or [])[:3]):
        if not isinstance(ex, dict):
            errs.append(f"{where}: exchanges[{i}] is not an object (C10)")
            continue
        for k in EXCHANGE_KEYS:
            if k not in ex:
                errs.append(f"{where}: exchanges[{i}] lacks {k!r} (C10)")
    return errs


def player_id_of(snapshot: dict | None, report: dict, spawned_captains: set[str]) -> str | None:
    """The player's captain id: the save snapshot's player_id, else the battle side that is not a spawn."""
    if isinstance(snapshot, dict) and isinstance(snapshot.get("player_id"), str):
        return snapshot["player_id"]
    for b in all_battles(report).values():
        others = [str(s) for s in (b.get("sides") or []) if str(s) not in spawned_captains]
        if len(others) == 1:
            return others[0]
    return None


def player_ship_id_of(snapshot: dict | None, report: dict, spawned_ships: set[str]) -> str | None:
    if isinstance(snapshot, dict):
        try:
            return player_ship(snapshot)[0]
        except KeyError:
            pass
    for t in report.get("per_turn") or []:
        for ex in exchanges_at(t):
            for k in ("attacker", "defender"):
                if isinstance(ex.get(k), str) and ex[k] not in spawned_ships:
                    return ex[k]
    return None


def _turns(report: dict) -> list[dict]:
    return [t for t in (report.get("per_turn") or []) if isinstance(t, dict)]


def _prefix(report: dict, what: str, n: int = 1) -> list[str]:
    """Shape errors that make a row's report unusable, prefixed with the scenario name."""
    shape = report_shape_errors(report)
    if shape:
        return [f"{what}: report shape: " + "; ".join(shape[:4])]
    se = spawned_error(report, n) if n else None
    return [f"{what}: {se}"] if se else []


# --------------------------------------------------------------------------- smoke/23 — opening (CM-01, FR-06, D-125)

def check_opening(report: dict, snapshot: dict | None, *, fired: bool, start_fight_delta: int, respect_start: int, what: str) -> list[str]:
    """Fired: a battle with opened_by = the player exists from turn 1, with an FR-06 row of
    −start_fight_delta respect for the player.  Not fired (passive AI, 10 turns): no battle with
    the player on a side, player.in_battle never true."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    pid = player_id_of(snapshot, report, {sp["captain"]})
    turns = _turns(report)
    if not fired:
        for t in turns:
            if t.get("player", {}).get("in_battle") is True:
                errs.append(f"{what}: turn {t.get('turn_no')}: player.in_battle is true although nobody fired and the AI is passive (--ai passive, D-125)")
                break
            mine = [b for b in battles_at(t) if pid in {str(s) for s in (b.get("sides") or [])}] if pid else battles_at(t)
            if mine:
                errs.append(f"{what}: turn {t.get('turn_no')}: a battle {_short(mine[0])} involves the player although no `fire` was given and "
                            f"--ai passive never opens one (CM-01, D-125)")
                break
        nb = _get(report, "final", "battles")
        if not isinstance(nb, int) or isinstance(nb, bool):
            errs.append(f"{what}: final.battles = {nb!r}, expected an integer count (C10)")
        return errs
    t1 = turns[0] if turns else {}
    if "in_battle" not in (t1.get("player") or {}):
        errs.append(f"{what}: per_turn.player has no 'in_battle' (C10 report additions)")
    elif t1["player"]["in_battle"] is not True:
        errs.append(f"{what}: turn 1: player.in_battle = {t1['player']['in_battle']!r}, expected true after `fire phaser` was accepted (CM-01, D-119)")
    if "battles" not in t1:
        errs.append(f"{what}: per_turn has no 'battles' list (C10)")
        return errs
    b = battle_between(report, pid, sp["captain"]) if pid else None
    if b is None:
        rej = events_mentioning(t1, "rejected")
        errs.append(f"{what}: no battle with sides {{player {pid}, spawn {sp['captain']}}} in any turn's battles[] (got {_short([x.get('sides') for x in all_battles(report).values()])})"
                    + (f"; the fire order was rejected: {rej[0][:140]!r}" if rej else "") + " — the first accepted `fire` opens the battle (CM-01, D-119)")
        return errs
    errs += battle_shape_errors(b, what)
    if b.get("opened_by") != pid:
        errs.append(f"{what}: battle.opened_by = {b.get('opened_by')!r}, expected the player {pid} who fired (CM-01)")
    if b.get("opened_turn") != 1:
        errs.append(f"{what}: battle.opened_turn = {b.get('opened_turn')!r}, expected 1 (the fire order was given at turn 1)")
    rows = [r for r in reputation_at(t1) if r.get("captain") == pid and str(r.get("rule", "")).upper() == "FR-06"]
    if "reputation" not in t1:
        errs.append(f"{what}: per_turn has no 'reputation' list (C10)")
    elif not rows:
        errs.append(f"{what}: turn 1: no reputation row {{captain {pid}, rule FR-06}}; rows were {_short(reputation_at(t1))} — starting a fight costs "
                    f"start_fight_delta respect, unscaled (FR-06, CFG-07, D-121)")
    else:
        r = rows[0]
        if r.get("respect") != -int(start_fight_delta) or (r.get("fear") not in (0, None)):
            errs.append(f"{what}: FR-06 row = {_short(r)}, expected respect −{start_fight_delta} and fear 0 ([respect] start_fight_delta; FR-06)")
        sc = r.get("scale")
        if sc is not None and not _close(sc, 1.0, 0.001):
            errs.append(f"{what}: FR-06 row scale = {sc!r}, expected 1.0 — FR-06 is unscaled (D-121, LV-03)")
    want = max(0, int(respect_start) - int(start_fight_delta))
    got = (t1.get("player") or {}).get("respect")
    if got != want:
        errs.append(f"{what}: turn 1: player.respect = {got!r}, expected {want} = respect start {respect_start} − start_fight_delta {start_fight_delta} (FR-06)")
    return errs


# --------------------------------------------------------------------------- smoke/24 — phasers (CM-02, CM-04)

def check_phaser(report: dict, snapshot: dict | None, *, distance: int, phasers: float, per_unit: float, target_shield_max: float,
                 what: str) -> list[str]:
    """One phaser volley at `distance`: a row attacker = player ship, defender = spawn, weapon phaser,
    distance d, hit true, damage phasers × per_unit × (1 − d/3) to 2 d.p., shields_after = max − damage."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    psid = player_ship_id_of(snapshot, report, {sp["ship"]})
    turns = _turns(report)
    t1 = turns[0] if turns else {}
    rows = [ex for ex in exchanges_at(t1) if ex.get("attacker") == psid and ex.get("defender") == sp["ship"] and ex.get("weapon") == "phaser"]
    if not rows:
        rej = events_mentioning(t1, "rejected")
        errs.append(f"{what}: turn 1: no exchange row {{attacker {psid}, defender {sp['ship']}, weapon phaser}}; exchanges were {_short(exchanges_at(t1))}"
                    + (f"; rejected: {rej[0][:140]!r}" if rej else "") + " (CM-02 phaser range ≥ distance; OUT-04; C10)")
        return errs
    ex = rows[0]
    want = phaser_damage(phasers, per_unit, distance)
    if ex.get("distance") != distance:
        errs.append(f"{what}: exchange.distance = {ex.get('distance')!r}, expected {distance} (the spawn distance; an AI ship in a battle holds its cell "
                    f"unless escaping, D-128; Chebyshev after TP-02, CM-02)")
    if ex.get("hit") is not True:
        errs.append(f"{what}: exchange.hit = {ex.get('hit')!r}, expected true — phasers always hit (CM-04)")
    if not _close(ex.get("damage"), want, 0.0051):
        errs.append(f"{what}: exchange.damage = {ex.get('damage')!r}, expected {want:.2f} = phasers {phasers:g} × phaser_damage_per_unit {per_unit:g} × "
                    f"(1 − {distance}/3) (CM-04, CFG-26)")
    sa = ex.get("shields_after")
    if not _close(sa, max(0.0, float(target_shield_max) - want), 0.0051):
        errs.append(f"{what}: exchange.shields_after = {sa!r}, expected {max(0.0, target_shield_max - want):.2f} = shield_max {target_shield_max:g} − {want:.2f} "
                    f"(a fresh spawn, no torpedoes from the player this turn; CM-06, HZ-09)")
    return errs


def check_phaser_rejected(report: dict, *, distance: int, phaser_range: int, what: str) -> list[str]:
    """`fire phaser` beyond phaser_range → rejected at submission naming the range and the distance; no battle opens."""
    errs = _prefix(report, what)
    if errs:
        return errs
    turns = _turns(report)
    t1 = turns[0] if turns else {}
    rej = events_mentioning(t1, "rejected")
    if not rej:
        errs.append(f"{what}: turn 1: no event containing 'rejected' after `fire phaser` at distance {distance} > phaser_range {phaser_range}; events: "
                    f"{[event_text(e)[:60] for e in (t1.get('events') or [])][:6]} ({REJECTED_RULE}; CM-02, C10 order.rejected names the range and the distance)")
    else:
        text = rej[0]
        nums = {int(m) for m in re.findall(r"\b\d+\b", text)}
        if not ({distance, phaser_range} <= nums and re.search(r"range", text, re.I)):
            errs.append(f"{what}: the rejection {text[:160]!r} does not name both the distance {distance} and the phaser range {phaser_range} "
                        f"(C10: 'target s-017 is 4 cells away; phaser range 2 (CM-02)')")
    if any(battles_at(t) for t in turns):
        errs.append(f"{what}: a battle was opened although the fire order was out of range: {_short([b.get('sides') for b in all_battles(report).values()])} (CM-01: only an accepted fire opens one)")
    nb = _get(report, "final", "battles")
    if isinstance(nb, int) and nb != 0:
        errs.append(f"{what}: final.battles = {nb}, expected 0")
    return errs


# --------------------------------------------------------------------------- smoke/25 — torpedoes (CM-03, CFG-25, D-67)

def player_torpedo_rows(report: dict, psid: str | None, target: str) -> list[tuple[int, dict]]:
    out = []
    for t in _turns(report):
        for ex in exchanges_at(t):
            if ex.get("weapon") == "torpedo" and ex.get("defender") == target and (psid is None or ex.get("attacker") == psid):
                out.append((int(t.get("turn_no") or 0), ex))
    return out


def check_torpedo_volley(report: dict, snapshot: dict | None, *, n: int, ammo_start: int, torpedo_damage: float, what: str) -> list[str]:
    """`fire torpedo <id> n` → n rows at turn 1, damage == torpedo_damage iff hit, ammo − n in the snapshot."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    psid = player_ship_id_of(snapshot, report, {sp["ship"]})
    turns = _turns(report)
    rows = [ex for tn, ex in player_torpedo_rows(report, psid, sp["ship"]) if tn == 1]
    if len(rows) != n:
        rej = events_mentioning(turns[0] if turns else {}, "rejected")
        errs.append(f"{what}: turn 1: {len(rows)} torpedo exchange row(s) from {psid} at {sp['ship']}, expected {n} (one row per torpedo, D-120)"
                    + (f"; rejected: {rej[0][:140]!r}" if rej else ""))
    for i, ex in enumerate(rows):
        hit, dmg = ex.get("hit"), ex.get("damage")
        if not isinstance(hit, bool):
            errs.append(f"{what}: torpedo row {i}: hit = {hit!r}, expected a boolean (CM-03)")
        elif hit and not _close(dmg, torpedo_damage, 1e-6):
            errs.append(f"{what}: torpedo row {i}: hit but damage = {dmg!r}, expected torpedo_damage {torpedo_damage:g} (CM-03, CFG-25)")
        elif not hit and not _close(dmg, 0.0, 1e-6):
            errs.append(f"{what}: torpedo row {i}: a miss with damage = {dmg!r}, expected 0")
    if isinstance(snapshot, dict):
        ship = (snapshot.get("ships") or {}).get(psid) or {}
        left = ship.get("torpedoes")
        if not _close(left, ammo_start - n, 1e-6):
            errs.append(f"{what}: the player's ship has {left!r} torpedoes after the volley, expected {ammo_start} − {n} = {ammo_start - n} (ammo is spent per torpedo, CMD-08)")
    return errs


def check_torpedo_determinism(report_a: dict, report_b: dict, what: str) -> list[str]:
    """Same seed + same script → identical hit sequences and identical snapshot hashes (D-67)."""
    def hits(r: dict) -> list[bool]:
        return [bool(ex.get("hit")) for _, ex in player_torpedo_rows(r, None, spawned_entries(r)[0]["ship"])] if spawned_entries(r) else []
    ha, hb = hits(report_a), hits(report_b)
    errs = []
    if not ha:
        errs.append(f"{what}: no torpedo rows to compare")
    elif ha != hb:
        errs.append(f"{what}: two runs with the same seed rolled different torpedo hits {ha[:8]} vs {hb[:8]} — the hit roll must come from the engine RNG derived from the seed (D-67, CM-03)")
    sa = [t.get("snapshot_hash") for t in _turns(report_a)]
    sb = [t.get("snapshot_hash") for t in _turns(report_b)]
    if sa != sb:
        errs.append(f"{what}: same seed, same script, different snapshot hashes — combat consumed global random or iterated a set (D-67)")
    return errs


def check_torpedo_rate(report: dict, *, p_expected: float, bound: float, at_most: bool, engine: float, shots_min: int, what: str) -> list[str]:
    """Hit rate over every player torpedo row ≤ bound (at_most) or ≥ bound, with ≥ shots_min rows (CM-03 dice wired to engine)."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    rows = [ex for _, ex in player_torpedo_rows(report, None, sp["ship"])]
    if len(rows) < shots_min:
        rej = [e for t in _turns(report) for e in events_mentioning(t, "rejected")]
        errs.append(f"{what}: only {len(rows)} torpedo rows at {sp['ship']}, need ≥ {shots_min} for the rate (the volley size n ≤ ammo is config; CMD-08)"
                    + (f"; rejected: {rej[0][:140]!r}" if rej else ""))
        return errs
    rate = sum(1 for ex in rows if ex.get("hit") is True) / len(rows)
    ok = rate <= bound if at_most else rate >= bound
    if not ok:
        errs.append(f"{what}: hit rate {rate:.3f} over {len(rows)} shots at a target with engine {engine:g}, expected {'≤' if at_most else '≥'} {bound:g} "
                    f"(p = torpedo_hit_base − torpedo_evade_per_engine × engine = {p_expected:.2f}; CM-03, CFG-25)")
    return errs


# --------------------------------------------------------------------------- smoke/26 — destruction and loot (CB-01, HZ-09, D-105, D-123)

def destroyed_rows(turn: dict) -> list[dict]:
    d = turn.get("destroyed")
    return [x for x in d if isinstance(x, dict)] if isinstance(d, list) else []


def check_destruction(report: dict, snapshot: dict | None, *, victim: dict, player_before: dict, caps: dict, torpedo_damage: float,
                      what: str) -> list[str]:
    """The spawn dies in combat by a player hit; loot per CB-01 / D-123: gold, silver, items and the
    remaining torpedoes transfer; phasers/engine/warp = max(own, victim) capped; shields untouched;
    the AI captain is gone the same turn (captains −1, D-105).

    victim = {ship, captain, gold, silver, items, torpedoes, phasers, engine, warp, shield_max} from a probe
    run (same seed, same spawn, no fire); player_before = {gold, silver, torpedoes, phasers, engine, warp}."""
    errs = _prefix(report, what)
    if errs:
        return errs
    turns = _turns(report)
    vs, vc = victim["ship"], victim["captain"]
    psid = player_ship_id_of(snapshot, report, {vs})
    kill = next(((t, d) for t in turns for d in destroyed_rows(t) if d.get("ship") == vs), None)
    if kill is None:
        last = turns[-1] if turns else {}
        tail = [ex for ex in exchanges_at(last) if ex.get("defender") == vs]
        errs.append(f"{what}: {vs} was never listed in destroyed[] over {len(turns)} turns (last exchange rows at it: {_short(tail[-2:])}); "
                    f"a hit landing on 0 shields destroys (HZ-09, CB-01) — outcome {report.get('outcome')!r}")
        return errs
    t, d = kill
    n = int(t.get("turn_no") or 0)
    if d.get("cause") != "combat":
        errs.append(f"{what}: destroyed[*].cause = {d.get('cause')!r}, expected 'combat' (C10)")
    if d.get("by") != psid:
        errs.append(f"{what}: destroyed[*].by = {d.get('by')!r}, expected the player's ship {psid} that landed the hit (C10, D-123)")
    if d.get("captain") != vc:
        errs.append(f"{what}: destroyed[*].captain = {d.get('captain')!r}, expected {vc} (C9)")
    prev = next((x for x in turns if x.get("turn_no") == n - 1), None)
    c0, c1 = (prev or {}).get("captains"), t.get("captains")
    if prev is not None and not (isinstance(c0, int) and isinstance(c1, int) and c1 == c0 - 1):
        errs.append(f"{what}: captains {c0} → {c1} across turn {n}, expected −1 — an AI captain with no ships is removed the same turn (D-105)")
    b = battle_between(report, player_id_of(snapshot, report, {vc}) or "", vc)
    if b is None:
        errs.append(f"{what}: no battle record between the player and {vc} (C10)")
    else:
        if b.get("ended") != "destroyed":
            errs.append(f"{what}: battle.ended = {b.get('ended')!r}, expected 'destroyed' (CB-01, D-119)")
        if b.get("victor") != player_id_of(snapshot, report, {vc}):
            errs.append(f"{what}: battle.victor = {b.get('victor')!r}, expected the player (D-123)")
        tr = b.get("transfer")
        if not isinstance(tr, dict):
            errs.append(f"{what}: battle.transfer = {tr!r}, expected {{gold, silver, items, pct}} on destruction (C10, CB-01)")
        else:
            if tr.get("gold") != victim["gold"] or tr.get("silver") != victim["silver"]:
                errs.append(f"{what}: transfer gold/silver = {tr.get('gold')}/{tr.get('silver')}, expected all of the victim's {victim['gold']}/{victim['silver']} (CB-01)")
    pl = t.get("player") or {}
    if pl.get("gold") != player_before["gold"] + victim["gold"] or pl.get("silver") != player_before["silver"] + victim["silver"]:
        errs.append(f"{what}: turn {n}: player gold/silver = {pl.get('gold')}/{pl.get('silver')}, expected {player_before['gold']} + {victim['gold']} / "
                    f"{player_before['silver']} + {victim['silver']} (CB-01: the victor gains all coins)")
    if not isinstance(snapshot, dict):
        errs.append(f"{what}: no save snapshot after the run to read the looted ship stats from (C6)")
        return errs
    ship = (snapshot.get("ships") or {}).get(psid) or {}
    fired = sum(1 for tn, ex in player_torpedo_rows(report, psid, vs))
    v_fired = sum(1 for x in turns for ex in exchanges_at(x) if ex.get("attacker") == vs and ex.get("weapon") == "torpedo")
    want_t = player_before["torpedoes"] - fired + (victim["torpedoes"] - v_fired)
    if not _close(ship.get("torpedoes"), want_t, 1e-6):
        errs.append(f"{what}: player torpedoes after = {ship.get('torpedoes')!r}, expected {player_before['torpedoes']} − {fired} fired + the victim's remaining "
                    f"{victim['torpedoes']} − {v_fired} = {want_t} (CB-01)")
    for k in ("phasers", "engine", "warp"):
        want = min(float(caps[k]), max(float(player_before[k]), float(victim[k])))
        if not _close(ship.get(k), want, 1e-6):
            errs.append(f"{what}: player {k} after = {ship.get(k)!r}, expected max({player_before[k]:g}, {victim[k]:g}) capped at {caps[k]:g} = {want:g} (CB-01)")
    if not _close(ship.get("shield_max"), player_before["shield_max"], 1e-6):
        errs.append(f"{what}: player shield_max after = {ship.get('shield_max')!r}, expected {player_before['shield_max']:g} unchanged (CB-01: shields untouched)")
    taken = sum(float(ex.get("damage") or 0) for ex in exchanges_at(t) if ex.get("defender") == psid and ex.get("hit"))
    before = float((prev or {}).get("player", {}).get("shield_current", player_before["shield_max"]))
    want_s = max(0.0, before - taken)
    if not _close(pl.get("shield_current"), want_s, 0.01):
        errs.append(f"{what}: turn {n}: player shield_current = {pl.get('shield_current')!r}, expected {want_s:.2f} = {before:g} − {taken:g} taken this exchange "
                    f"(loot never restores shields; CB-01)")
    vc_items = victim.get("items") or []
    cap = (snapshot.get("captains") or {}).get(player_id_of(snapshot, report, {vc}) or "") or {}
    if vc_items and len(cap.get("items") or []) < len(player_before.get("items") or []) + len(vc_items):
        errs.append(f"{what}: the victim held {len(vc_items)} item(s) but the player's items are {_short(cap.get('items'))} (CB-01: all items transfer)")
    cd = _get(report, "final", "combat_deaths")
    if not isinstance(cd, int) or cd < 1:
        errs.append(f"{what}: final.combat_deaths = {cd!r}, expected ≥ 1 (C10)")
    if _get(report, "final", "battles_by_outcome", "destroyed") in (None, 0):
        errs.append(f"{what}: final.battles_by_outcome.destroyed = {_get(report, 'final', 'battles_by_outcome', 'destroyed')!r}, expected ≥ 1 (C10)")
    return errs


def check_mutual_annihilation(report: dict, *, turn: int, what: str) -> list[str]:
    """Both last ships die in one exchange: two destroyed rows (cause combat, by each other), transfer null, no victor, game over."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    turns = _turns(report)
    t = next((x for x in turns if x.get("turn_no") == turn), None)
    if t is None:
        errs.append(f"{what}: turn {turn} missing (run has {len(turns)} turns, outcome {report.get('outcome')!r}) — both ships at 0 shields take a phaser hit at turn {turn} (HZ-09)")
        return errs
    rows = destroyed_rows(t)
    ships = {d.get("ship") for d in rows}
    if sp["ship"] not in ships or len(rows) < 2:
        errs.append(f"{what}: turn {turn}: destroyed = {_short(rows)}, expected both the player's ship and {sp['ship']} (hits computed from the state at the start "
                    f"of the weapon phase land on both; M3 § 6 simultaneity, HZ-09)")
    for d in rows:
        if d.get("cause") != "combat" or not d.get("by"):
            errs.append(f"{what}: destroyed row {_short(d)} lacks cause 'combat' / 'by' (C10)")
    if report.get("outcome") != "game_over":
        errs.append(f"{what}: outcome = {report.get('outcome')!r}, expected 'game_over' (the player's active ship died; CB-04)")
    b = next((x for x in all_battles(report).values() if sp["captain"] in {str(s) for s in (x.get("sides") or [])}), None)
    if b is None:
        errs.append(f"{what}: no battle record involving {sp['captain']}")
    else:
        if b.get("transfer") is not None:
            errs.append(f"{what}: battle.transfer = {_short(b.get('transfer'))}, expected null — mutual annihilation transfers nothing (D-123)")
        if b.get("victor") is not None:
            errs.append(f"{what}: battle.victor = {b.get('victor')!r}, expected null (D-123)")
        if b.get("ended") != "destroyed":
            errs.append(f"{what}: battle.ended = {b.get('ended')!r}, expected 'destroyed'")
    return errs


# --------------------------------------------------------------------------- smoke/27 — peace (CB-03, CM-09, D-46, D-122)

def shields_track(report: dict, ship_id: str, shield_max: float, until_turn: int) -> float:
    """A ship's shields at the end of `until_turn`, followed through exchange rows (no recharge in battle, CM-05)."""
    cur = float(shield_max)
    for t in _turns(report):
        if int(t.get("turn_no") or 0) > until_turn:
            break
        for ex in exchanges_at(t):
            if ex.get("defender") == ship_id and isinstance(ex.get("shields_after"), (int, float)):
                cur = float(ex["shields_after"])
    return cur


def check_peace(report: dict, snapshot: dict | None, *, offerer: str, spawn_shield_max: float, loot_max: float, recharge: float,
                player_coins: tuple[int, int], ai_coins: tuple[int, int], pct_zero: bool | None, what: str) -> list[str]:
    """offerer = 'player' (the AI accepts; victor = the AI; the player pays X %) or 'ai' (the AI offered at its
    threshold; the player accepted; victor = the player).  X per D-46 from the shield numbers the report shows."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    pid = player_id_of(snapshot, report, {sp["captain"]})
    b = battle_between(report, pid or "", sp["captain"])
    turns = _turns(report)
    if b is None:
        errs.append(f"{what}: no battle between the player and {sp['captain']} in battles[] (C10)")
        return errs
    errs += battle_shape_errors(b, what)
    if b.get("ended") != "peace":
        peace = [(t.get("turn_no"), e[:80]) for t in turns for e in events_mentioning(t, "peace")]
        errs.append(f"{what}: battle.ended = {b.get('ended')!r} after {len(turns)} turns, expected 'peace' — "
                    + ("the weaker AI accepts the player's offer within the turn or the next (D-125, D-122)" if offerer == "player" else
                       "the AI offers below its fear-scaled shield threshold and the player's `peace accept` closes it (EF-02, CM-09)")
                    + f"; peace events: {peace[:4]} ({PEACE_RULE})")
        return errs
    victor_want = sp["captain"] if offerer == "player" else pid
    if b.get("victor") != victor_want:
        errs.append(f"{what}: battle.victor = {b.get('victor')!r}, expected {victor_want} — the victor is the side that did not offer (CB-03, D-122)")
    end_turn = int(b.get("_turn") or 0)
    tr = b.get("transfer")
    if not isinstance(tr, dict) or not isinstance(tr.get("pct"), int):
        errs.append(f"{what}: battle.transfer = {_short(tr)}, expected {{gold, silver, items, pct}} with an integer pct (C10, D-46)")
        return errs
    # shield numbers: the victor's and the loser's at the end turn, with or without that turn's recharge (D-128)
    tcur = next((t for t in turns if t.get("turn_no") == end_turn), turns[-1])
    tprev = next((t for t in turns if t.get("turn_no") == end_turn - 1), None)
    p_now = float((tcur.get("player") or {}).get("shield_current") or 0.0)
    p_prev = float(((tprev or {}).get("player") or {}).get("shield_current") or p_now) if tprev else p_now
    a_prev = shields_track(report, sp["ship"], spawn_shield_max, end_turn)
    a_cands = {a_prev, min(float(spawn_shield_max), a_prev + float(recharge))}
    p_cands = {p_now, p_prev, min(float(p_now + recharge), max(p_now, p_prev))}
    if offerer == "player":
        xs = {peace_pct(a, p, loot_max) for a in a_cands for p in p_cands}
        loser_coins, loser = player_coins, "player"
    else:
        xs = {peace_pct(p, a, loot_max) for a in a_cands for p in p_cands}
        loser_coins, loser = ai_coins, sp["captain"]
    if tr["pct"] not in xs:
        errs.append(f"{what}: transfer.pct = {tr['pct']}, expected X = floor(max(0, (V − L)/V) × peace_loot_max {loot_max:g}) ∈ {sorted(xs)} from the report's "
                    f"shields at turn {end_turn} (victor {'AI' if offerer == 'player' else 'player'} {sorted(a_cands) if offerer == 'player' else sorted(p_cands)}, "
                    f"loser {sorted(p_cands) if offerer == 'player' else sorted(a_cands)}) (D-46, CB-03)")
    if pct_zero is True and tr["pct"] != 0:
        errs.append(f"{what}: transfer.pct = {tr['pct']}, expected 0 — the victor's shields are lower than the loser's (D-46: max(0, …))")
    if pct_zero is False and tr["pct"] <= 0:
        errs.append(f"{what}: transfer.pct = {tr['pct']}, expected > 0 — the victor's shields are higher than the loser's here (D-46)")
    x = tr["pct"]
    g_want, s_want = int(loser_coins[0] * x // 100), int(loser_coins[1] * x // 100)
    if tr.get("gold") != g_want or tr.get("silver") != s_want:
        errs.append(f"{what}: transfer gold/silver = {tr.get('gold')}/{tr.get('silver')}, expected floor({loser_coins[0]} × {x}/100) = {g_want} / "
                    f"floor({loser_coins[1]} × {x}/100) = {s_want} from the loser {loser} (D-46, D-122 rounded down)")
    pl = tcur.get("player") or {}
    if offerer == "player":
        want = (player_coins[0] - g_want, player_coins[1] - s_want)
    else:
        want = (player_coins[0] + g_want, player_coins[1] + s_want)
    if (pl.get("gold"), pl.get("silver")) != want:
        errs.append(f"{what}: turn {end_turn}: player gold/silver = {pl.get('gold')}/{pl.get('silver')}, expected {want[0]}/{want[1]} after the transfer (CB-03)")
    if _get(report, "final", "battles_by_outcome", "peace") in (None, 0):
        errs.append(f"{what}: final.battles_by_outcome.peace = {_get(report, 'final', 'battles_by_outcome', 'peace')!r}, expected ≥ 1 (C10)")
    if offerer == "ai":
        offers = [(int(t.get("turn_no") or 0)) for t in turns for _ in events_mentioning(t, "peace")]
        if not offers:
            errs.append(f"{what}: no event mentions the AI's peace offer ({PEACE_RULE}; OUT-05)")
    return errs


def check_mutual_peace(report: dict, *, what: str) -> list[str]:
    """Both sides offered in the same turn: peace with no victor, pct 0, no reputation rows that turn (D-122)."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    b = next((x for x in all_battles(report).values() if sp["captain"] in {str(s) for s in (x.get("sides") or [])}), None)
    if b is None or b.get("ended") != "peace":
        errs.append(f"{what}: expected a battle ended by peace, got {_short(b)}")
        return errs
    if b.get("victor") is not None:
        errs.append(f"{what}: battle.victor = {b.get('victor')!r}, expected null — mutual offers make peace with no victor (D-122)")
    tr = b.get("transfer")
    if isinstance(tr, dict) and (tr.get("pct") not in (0, None) or tr.get("gold") or tr.get("silver")):
        errs.append(f"{what}: transfer = {_short(tr)}, expected X = 0 and nothing moved (D-122)")
    t = next((x for x in _turns(report) if x.get("turn_no") == b.get("_turn")), None)
    if t is not None and reputation_at(t):
        errs.append(f"{what}: turn {t.get('turn_no')}: reputation rows {_short(reputation_at(t))} although nobody won (D-122: no fear or respect moves)")
    return errs


# --------------------------------------------------------------------------- smoke/28 — escape (CB-02, CMD-11, D-119, D-123)

def check_escape(report: dict, snapshot: dict | None, *, escape_turn: int, expect_ended: bool, end_distance: int, max_range: int, what: str) -> list[str]:
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    pid = player_id_of(snapshot, report, {sp["captain"]})
    b = battle_between(report, pid or "", sp["captain"])
    turns = _turns(report)
    if b is None:
        errs.append(f"{what}: no battle between the player and {sp['captain']} (the opening `fire phaser` at turn 1 must be accepted; CM-01)")
        return errs
    te = next((t for t in turns if t.get("turn_no") == escape_turn), None)
    if te is None:
        errs.append(f"{what}: turn {escape_turn} missing from the report ({len(turns)} turns, outcome {report.get('outcome')!r})")
        return errs
    rec = next((x for x in battles_at(te) if str(x.get("id")) == str(b.get("id"))), None)
    if expect_ended:
        if rec is None or rec.get("ended") != "escape":
            rej = events_mentioning(te, "rejected")
            errs.append(f"{what}: turn {escape_turn}: battle.ended = {(rec or {}).get('ended')!r}, expected 'escape' — the ship ended TP-02 at distance "
                        f"{end_distance} > every range (longest {max_range}), so nobody is in range (CB-02, D-119)"
                        + (f"; the escape order was rejected: {rej[0][:140]!r}" if rej else ""))
        else:
            if rec.get("transfer") is not None:
                errs.append(f"{what}: battle.transfer = {_short(rec.get('transfer'))}, expected null on escape (CB-02: nothing transfers)")
            if rec.get("victor") is not None:
                errs.append(f"{what}: battle.victor = {rec.get('victor')!r}, expected null on escape")
        if reputation_at(te):
            errs.append(f"{what}: turn {escape_turn}: reputation rows {_short(reputation_at(te))}, expected none — an escape is not a win or a loss (CB-02, FR-10)")
        if (te.get("player") or {}).get("in_battle") is True:
            errs.append(f"{what}: turn {escape_turn}: player.in_battle still true after the escape (C10)")
        if not events_mentioning(te, "escape"):
            errs.append(f"{what}: turn {escape_turn}: no event mentions the escape (OUT-05: escape reports success; C3 type 'escape')")
        if _get(report, "final", "battles_by_outcome", "escape") in (None, 0):
            errs.append(f"{what}: final.battles_by_outcome.escape = {_get(report, 'final', 'battles_by_outcome', 'escape')!r}, expected ≥ 1 (C10)")
    else:
        if rec is None or rec.get("ended") is not None:
            errs.append(f"{what}: turn {escape_turn}: battle.ended = {(rec or {}).get('ended')!r}, expected null (still open) — at distance {end_distance} the "
                        f"spawn is still within the player's torpedo range {max_range} (CM-02, D-119: a battle ends only when nobody is in range)")
        last = turns[-1]
        rec_last = next((x for x in battles_at(last) if str(x.get("id")) == str(b.get("id"))), None)
        if rec_last is None or rec_last.get("ended") is not None:
            errs.append(f"{what}: turn {last.get('turn_no')}: battle no longer open ({(rec_last or {}).get('ended')!r}) — the AI holds its cell in battle (D-128), "
                        f"so the pair stays in range")
    pos = (te.get("player") or {}).get("position")
    sp_cell = sp.get("cell") or sp.get("position")
    if isinstance(pos, list) and len(pos) == 3 and isinstance(sp_cell, list):
        cell = sp_cell[2] if len(sp_cell) == 3 and isinstance(sp_cell[0], list) else sp_cell
        if chebyshev(pos[2], cell) != end_distance:
            errs.append(f"{what}: turn {escape_turn}: player at {pos[2]} is {chebyshev(pos[2], cell)} from the spawn cell {cell}, expected {end_distance} "
                        f"(escape is a move, CMD-11, D-123)")
    return errs


# --------------------------------------------------------------------------- smoke/29 — reputation (FR-01…10, LV-03, D-121)

def check_reputation_win(report: dict, snapshot: dict | None, *, expect_rows: dict[str, tuple[int, int]], scale: float, ratio: float,
                         strength_player: float, strength_ai: float, fear_start: int, respect_start: int, start_fight_delta: int,
                         require_offer: bool, what: str) -> list[str]:
    """The player destroys the spawn: the rows in expect_rows ({rule: (fear, respect)}) appear on the destruction
    turn with `scale` (2 d.p.), strength_at_open matches CM-08, and fear / respect end at the clamped sums."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    pid = player_id_of(snapshot, report, {sp["captain"]})
    turns = _turns(report)
    kill = next((t for t in turns if any(d.get("ship") == sp["ship"] for d in destroyed_rows(t))), None)
    if kill is None:
        errs.append(f"{what}: {sp['ship']} was never destroyed in {len(turns)} turns (outcome {report.get('outcome')!r}; last exchanges "
                    f"{_short(exchanges_at(turns[-1])[-2:] if turns else [])}) — the scenario expects the player's volleys to finish it")
        return errs
    n = int(kill.get("turn_no") or 0)
    b = battle_between(report, pid or "", sp["captain"])
    if b is None:
        errs.append(f"{what}: no battle record between the player and {sp['captain']}")
    else:
        so = b.get("strength_at_open") or {}
        if not (_close(so.get(pid), strength_player, 0.51) and _close(so.get(sp["captain"]), strength_ai, 0.51)):
            errs.append(f"{what}: strength_at_open = {_short(so)}, expected {{{pid}: {strength_player:g}, {sp['captain']}: {strength_ai:g}}} = Σ shield_current + "
                        f"torpedoes × torpedo_damage + phasers × phaser_damage_per_unit × 3 at the open (CM-08, D-121)")
    rows = {str(r.get("rule", "")).upper(): r for r in reputation_at(kill) if r.get("captain") == pid}
    for rule, (fear_d, resp_d) in expect_rows.items():
        r = rows.get(rule)
        if r is None:
            errs.append(f"{what}: turn {n}: no reputation row {{captain {pid}, rule {rule}}}; rows were {_short(reputation_at(kill))} "
                        f"({rule}: fear {fear_d:+d}, respect {resp_d:+d} at scale {scale:.2f}; D-121)")
            continue
        if r.get("fear") != fear_d or r.get("respect") != resp_d:
            errs.append(f"{what}: {rule} row = {_short(r)}, expected fear {fear_d:+d}, respect {resp_d:+d} (delta × clamp(opponent/you = {ratio:.2f}, "
                        f"scale_min, scale_max) = × {scale:.2f}; LV-03, CFG-11)")
        if not _close(r.get("scale"), scale, 0.0051):
            errs.append(f"{what}: {rule} row scale = {r.get('scale')!r}, expected {scale:.2f} (2 d.p.; LV-03, D-121: the ratio at the open)")
    unexpected = [k for k in rows if k not in expect_rows and k != "FR-06"]
    if unexpected:
        errs.append(f"{what}: turn {n}: unexpected reputation rows for the player: {[_short(rows[k]) for k in unexpected]} (FR-10: nothing else moves)")
    fear_want = max(0, min(100, fear_start + sum(f for f, _ in expect_rows.values())))
    resp_want = max(0, min(100, respect_start - start_fight_delta + sum(r for _, r in expect_rows.values())))
    pl = kill.get("player") or {}
    if pl.get("fear") != fear_want or pl.get("respect") != resp_want:
        errs.append(f"{what}: turn {n}: player fear/respect = {pl.get('fear')}/{pl.get('respect')}, expected {fear_want}/{resp_want} = start {fear_start}/{respect_start} "
                    f"− FR-06 {start_fight_delta} at the open + the rows above, ints clamped 0–100 (LV-01, LV-05)")
    if require_offer and not any(events_mentioning(t, "peace") for t in turns if int(t.get("turn_no") or 0) <= n):
        errs.append(f"{what}: no peace offer from {sp['captain']} before turn {n} ({PEACE_RULE}) — the scenario needs the weaker AI to offer below its threshold (EF-02) so FR-04 applies")
    return errs


def check_reputation_bounds(report: dict, what: str) -> list[str]:
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    for t in _turns(report):
        pl = t.get("player") or {}
        for k in ("fear", "respect"):
            v = pl.get(k)
            if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100:
                errs.append(f"{what}: turn {t.get('turn_no')}: player.{k} = {v!r}, expected an int in 0–100 (LV-01, LV-05 clamp)")
                return errs
    for k in ("player_fear", "player_respect"):
        v = _get(report, "final", k)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100:
            errs.append(f"{what}: final.{k} = {v!r}, expected an int in 0–100 (C10)")
    return errs


def check_quiet(report: dict, *, fear_start: int, respect_start: int, what: str) -> list[str]:
    """FR-10: no battle, no trade → fear and respect never move and no reputation rows appear."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    for t in _turns(report):
        pl = t.get("player") or {}
        if pl.get("fear") != fear_start or pl.get("respect") != respect_start:
            errs.append(f"{what}: turn {t.get('turn_no')}: fear/respect = {pl.get('fear')}/{pl.get('respect')}, expected {fear_start}/{respect_start} unchanged "
                        f"over quiet turns (FR-10: no decay, nothing else moves them)")
            return errs
        if reputation_at(t):
            errs.append(f"{what}: turn {t.get('turn_no')}: reputation rows {_short(reputation_at(t))} in a quiet turn (FR-10)")
            return errs
        if any(is_mutiny_event(event_text(e)) for e in (t.get("events") or [])):
            errs.append(f"{what}: turn {t.get('turn_no')}: a mutiny happened — the quiet run's temp ini must keep supplies above zero (gate logic)")
            return errs
    return errs


# --------------------------------------------------------------------------- smoke/30 — AI rules (CM-10, EF-01, EF-02, CFG-29/30/44/72, D-125)

def check_ai_opens(report: dict, snapshot: dict | None, *, within: int, ratio: float, ratio_min: float, player_fear: int, fear_max: int,
                   start_fight_delta: int, what: str) -> list[str]:
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    pid = player_id_of(snapshot, report, {sp["captain"]})
    b = battle_between(report, pid or "", sp["captain"]) if pid else next((x for x in all_battles(report).values()
                                                                             if sp["captain"] in {str(s) for s in (x.get("sides") or [])}), None)
    if b is None:
        errs.append(f"{what}: no battle between {sp['captain']} and the player within {within} turns (ratio {ratio:.2f} ≥ attack_ratio_min {ratio_min:g}, "
                    f"player fear {player_fear} < attack_fear_max {fear_max}, in weapon range) — the AI must open one (CM-10, EF-01, D-125)")
        return errs
    if b.get("opened_by") != sp["captain"]:
        errs.append(f"{what}: battle.opened_by = {b.get('opened_by')!r}, expected the AI {sp['captain']} (the player gave no orders)")
    ot = b.get("opened_turn")
    if not isinstance(ot, int) or ot > within:
        errs.append(f"{what}: battle.opened_turn = {ot!r}, expected ≤ {within} (CM-10)")
    t = next((x for x in _turns(report) if x.get("turn_no") == ot), None)
    if t is not None:
        rows = [r for r in reputation_at(t) if r.get("captain") == sp["captain"] and str(r.get("rule", "")).upper() == "FR-06"]
        if not rows:
            errs.append(f"{what}: turn {ot}: no FR-06 row for the AI opener {sp['captain']} (−{start_fight_delta} respect applies to AI openers too; FR-06, D-121)")
        elif rows[0].get("respect") != -int(start_fight_delta):
            errs.append(f"{what}: AI FR-06 row = {_short(rows[0])}, expected respect −{start_fight_delta}")
    return errs


def check_no_player_battle(report: dict, snapshot: dict | None, *, turns: int, player_fear: int, fear_max: int, what: str) -> list[str]:
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    pid = player_id_of(snapshot, report, {sp["captain"]})
    for t in _turns(report):
        if (t.get("player") or {}).get("in_battle") is True:
            errs.append(f"{what}: turn {t.get('turn_no')}: player.in_battle true — with player fear {player_fear} ≥ attack_fear_max {fear_max} no AI opens on the "
                        f"player (EF-01, CFG-29)")
            return errs
        for b in battles_at(t):
            sides = {str(s) for s in (b.get("sides") or [])}
            if (pid and pid in sides) or (not pid and sp["captain"] in sides):
                errs.append(f"{what}: turn {t.get('turn_no')}: battle {_short(b.get('sides'))} involves the player at fear {player_fear} ≥ attack_fear_max {fear_max} (EF-01)")
                return errs
    if len(_turns(report)) < turns:
        errs.append(f"{what}: only {len(_turns(report))} of {turns} turns ran (outcome {report.get('outcome')!r})")
    return errs


def ai_shields_after(report: dict, ship_id: str, shield_max: float) -> dict[int, float]:
    """turn_no → the spawn's shields at the end of that turn, from exchange rows (no recharge in battle)."""
    out, cur = {}, float(shield_max)
    for t in _turns(report):
        for ex in exchanges_at(t):
            if ex.get("defender") == ship_id and isinstance(ex.get("shields_after"), (int, float)):
                cur = float(ex["shields_after"])
        out[int(t.get("turn_no") or 0)] = cur
    return out


def check_ai_peace_then_escape(report: dict, snapshot: dict | None, *, shield_max: float, threshold_pct: float, escape_pct: float, spawn_cell: list,
                               engine: float, what: str) -> list[str]:
    """The weaker spawn offers peace the turn after its shields fall below threshold_pct % (not before), escapes
    (moves directly away at full power) once below escape_pct %, and the battle ends by escape with the spawn alive."""
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    turns = _turns(report)
    sh = ai_shields_after(report, sp["ship"], shield_max)
    below = next((n for n in sorted(sh) if sh[n] < threshold_pct / 100.0 * shield_max), None)
    offers = [int(t.get("turn_no") or 0) for t in turns if events_mentioning(t, "peace")]
    if below is None:
        errs.append(f"{what}: the spawn's shields never fell below {threshold_pct:.1f} % of {shield_max:g} (track {sorted(sh.items())[:6]}) — the player's phasers "
                    f"must land each turn (CM-04)")
        return errs
    if not offers:
        errs.append(f"{what}: no peace offer in {len(turns)} turns although the spawn's shields were {sh[below]:g} < {threshold_pct:.1f} % of {shield_max:g} after turn "
                    f"{below} and it is weaker (EF-02: peace_shield_pct + (peace_shield_pct_at_fear100 − peace_shield_pct) × fear/100; CFG-30, CFG-72; {PEACE_RULE})")
    else:
        if offers[0] != below + 1:
            errs.append(f"{what}: first peace offer at turn {offers[0]}, expected turn {below + 1} — the turn after the shields first fell below "
                        f"{threshold_pct:.1f} % ({sh.get(below - 1, shield_max):g} → {sh[below]:g} at turn {below}); earlier offers mean the threshold is ignored (EF-02)")
    esc_below = next((n for n in sorted(sh) if sh[n] < escape_pct / 100.0 * shield_max), None)
    if esc_below is None:
        errs.append(f"{what}: the spawn's shields never fell below escape_shield_pct {escape_pct:g} % (track {sorted(sh.items())[:8]})")
        return errs
    sp_captain = sp["captain"]
    b = next((x for x in all_battles(report).values() if sp_captain in {str(s) for s in (x.get("sides") or [])}), None)
    if b is None or b.get("ended") != "escape":
        errs.append(f"{what}: battle.ended = {(b or {}).get('ended')!r}, expected 'escape' — below {escape_pct:g} % the AI moves directly away at full power "
                    f"(engine {engine:g}) until nobody is in range (EF-02, CFG-30, D-125, D-119)")
    if any(d.get("ship") == sp["ship"] for t in turns for d in destroyed_rows(t)):
        errs.append(f"{what}: the spawn was destroyed — it should have escaped once below {escape_pct:g} % (its shields floor at 0, HZ-09; the player stops firing)")
    if isinstance(snapshot, dict):
        ship = (snapshot.get("ships") or {}).get(sp["ship"])
        if not isinstance(ship, dict):
            errs.append(f"{what}: the spawn {sp['ship']} is missing from the final snapshot (it should be alive after escaping; D-105 removes only shipless captains)")
        else:
            pos = ship.get("position")
            if isinstance(pos, list) and len(pos) == 3 and chebyshev(pos[2], spawn_cell) < 1:
                errs.append(f"{what}: the spawn is still at its spawn cell {spawn_cell} at the end — it never moved away (EF-02 escape: moves directly away at full power)")
    return errs


def check_capture(report: dict, snapshot: dict | None, *, shield_max: float, surrender_pct: float, player_fear: int, yield_fear_min: int,
                  what: str) -> list[str]:
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    pid = player_id_of(snapshot, report, {sp["captain"]})
    turns = _turns(report)
    sh = ai_shields_after(report, sp["ship"], shield_max)
    below = next((n for n in sorted(sh) if sh[n] < surrender_pct / 100.0 * shield_max), None)
    caps = [(int(t.get("turn_no") or 0), c) for t in turns for c in (t.get("captures") or []) if isinstance(c, dict)]
    if "captures" not in (turns[0] if turns else {}):
        errs.append(f"{what}: per_turn has no 'captures' list (C10)")
    if not caps:
        errs.append(f"{what}: no captures[] row in {len(turns)} turns although player fear {player_fear} ≥ yield_fear_min {yield_fear_min} and the weaker spawn's "
                    f"shields were {sh.get(below, '?')} < {surrender_pct:g} % of {shield_max:g} after turn {below} (EF-02, CFG-44, CFG-72, FL-02, D-124; "
                    f"surrender > escape > peace when several apply, D-128)")
        return errs
    n, c = caps[0]
    if c.get("ship") != sp["ship"] or c.get("from") != sp["captain"] or (pid and c.get("to") != pid):
        errs.append(f"{what}: captures[0] = {_short(c)}, expected {{ship {sp['ship']}, from {sp['captain']}, to {pid}}} (C10)")
    if below is not None and n != below + 1:
        errs.append(f"{what}: capture at turn {n}, expected turn {below + 1} — the turn after the shields first fell below {surrender_pct:g} % "
                    f"(track {sorted(sh.items())[:8]})")
    t = next(x for x in turns if x.get("turn_no") == n)
    if (t.get("player") or {}).get("ships") != 2:
        errs.append(f"{what}: turn {n}: player.ships = {(t.get('player') or {}).get('ships')!r}, expected 2 after the capture (C10, FL-02)")
    prev = next((x for x in turns if x.get("turn_no") == n - 1), None)
    if prev is not None and not (isinstance(t.get("captains"), int) and t["captains"] == prev.get("captains", 0) - 1):
        errs.append(f"{what}: captains {prev.get('captains')} → {t.get('captains')} across the capture turn {n}, expected −1 (a shipless AI captain is removed; D-105, D-124)")
    if isinstance(snapshot, dict):
        cap = (snapshot.get("captains") or {}).get(pid or "") or {}
        if sp["ship"] not in (cap.get("ships") or []):
            errs.append(f"{what}: snapshot captains[{pid}].ships = {cap.get('ships')!r} lacks the captured {sp['ship']} (FL-02)")
        ship = (snapshot.get("ships") or {}).get(sp["ship"]) or {}
        if ship.get("captain") != pid:
            errs.append(f"{what}: snapshot ships[{sp['ship']}].captain = {ship.get('captain')!r}, expected {pid} (D-124: the ship changes owner in place)")
        if sp["captain"] in (snapshot.get("captains") or {}):
            errs.append(f"{what}: the surrendering captain {sp['captain']} is still in the snapshot (D-105)")
    if not any(events_mentioning(x, "captur") or events_mentioning(x, "surrender") for x in turns):
        errs.append(f"{what}: no event mentions the capture / surrender (OUT-11, C3 type 'capture')")
    if _get(report, "final", "captures") in (None, 0):
        errs.append(f"{what}: final.captures = {_get(report, 'final', 'captures')!r}, expected ≥ 1 (C10)")
    return errs


# --------------------------------------------------------------------------- smoke/31 — fleet (FL-03, MV-06, UX-07, D-124)

def check_fleet(report: dict, snapshot: dict | None, *, spawns: list[dict], player_cell: list, cells: dict[str, list], moves: list[tuple[int, int]],
                persisting_remaining: list[int], what: str) -> list[str]:
    """After two captures (B in the player's cell, C elsewhere): `ship B` switches the active ship; a move on the
    active ship carries A (same cell) and not C; the last persisting move sits in one ship's queue only."""
    errs = _prefix(report, what, len(spawns))
    if errs:
        return errs
    if not isinstance(snapshot, dict):
        return [f"{what}: no save snapshot after the run (C6) — the fleet is judged from captains[player].ships and ships[*].position / orders"]
    pid = snapshot.get("player_id")
    cap = (snapshot.get("captains") or {}).get(pid) or {}
    ships = snapshot.get("ships") or {}
    sb, sc = spawns[0]["ship"], spawns[1]["ship"]
    owned = list(cap.get("ships") or [])
    if len(owned) != 1 + len(spawns) or sb not in owned or sc not in owned:
        caps = [(t.get("turn_no"), c) for t in _turns(report) for c in (t.get("captures") or [])]
        errs.append(f"{what}: captains[{pid}].ships = {owned}, expected the original ship plus the captured {sb} and {sc} (captures: {_short(caps)}; FL-02, D-124)")
        return errs
    sa = next(s for s in owned if s not in (sb, sc))
    if cap.get("active_ship") != sb:
        errs.append(f"{what}: captains[{pid}].active_ship = {cap.get('active_ship')!r}, expected {sb} after `ship {sb}` (CMD ship <id|n>, D-124)")
    dx = sum(m[0] for m in moves)
    dy = sum(m[1] for m in moves)
    want_ab = [player_cell[0] + dx, player_cell[1] + dy]
    for sid, label in ((sa, "the original ship A"), (sb, "the captured ship B (active)")):
        pos = (ships.get(sid) or {}).get("position")
        if not (isinstance(pos, list) and len(pos) == 3 and _norm(pos[2]) == want_ab):
            errs.append(f"{what}: {label} {sid} is at {pos[2] if isinstance(pos, list) and len(pos) == 3 else pos!r}, expected {want_ab} — owned ships in the active "
                        f"ship's cell move with it (MV-06, FL-03; moves {moves} from {player_cell})")
    pos_c = (ships.get(sc) or {}).get("position")
    if not (isinstance(pos_c, list) and len(pos_c) == 3 and _norm(pos_c[2]) == _norm(cells["c"])):
        errs.append(f"{what}: the captured ship C {sc} is at {pos_c[2] if isinstance(pos_c, list) and len(pos_c) == 3 else pos_c!r}, expected to hold at {cells['c']} — "
                    f"a ship in another cell does not follow the active ship (FL-03)")
    def persisting(sid: str) -> bool:
        for o in (ships.get(sid) or {}).get("orders") or []:
            try:
                if isinstance(o, dict) and o.get("command") == "move" and [int(a) for a in (o.get("args") or [])[:2]] == list(persisting_remaining):
                    return True
            except (TypeError, ValueError):
                pass
        return False
    with_q = [s for s in owned if persisting(s)]
    if with_q != [sb]:
        errs.append(f"{what}: ships with the persisting move {persisting_remaining} in their queue: {with_q}, expected only the active {sb} — each owned ship keeps its "
                    f"own order queue (UX-07, D-124; snapshot ships[*].orders, C9)")
    if (_turns(report)[-1].get("player") or {}).get("ships") != len(owned):
        errs.append(f"{what}: per_turn.player.ships = {(_turns(report)[-1].get('player') or {}).get('ships')!r}, expected {len(owned)} (C10)")
    return errs


# --------------------------------------------------------------------------- smoke/32 — player death in combat (CB-04, OV-09b, OV-09c)

def check_combat_game_over(report: dict, save_present: bool, *, max_turn: int, what: str) -> list[str]:
    errs = _prefix(report, what)
    if errs:
        return errs
    sp = spawned_entries(report)[0]
    if report.get("outcome") != "game_over":
        errs.append(f"{what}: outcome = {report.get('outcome')!r} after {report.get('turns_completed')} turns, expected 'game_over' — a Bastion at distance 0 "
                    f"destroys a Kestrel in a few exchanges (CB-04, OV-09b)")
        return errs
    n = _get(report, "final", "game_over_turn")
    if not isinstance(n, int) or n > max_turn:
        errs.append(f"{what}: final.game_over_turn = {n!r}, expected ≤ {max_turn}")
    cause = str(_get(report, "final", "game_over_cause") or "")
    if "combat" not in cause.lower():
        errs.append(f"{what}: final.game_over_cause = {cause!r}, expected it to contain 'combat' (C10)")
    t = next((x for x in _turns(report) if x.get("turn_no") == n), None)
    if t is not None:
        rows = [d for d in destroyed_rows(t) if d.get("cause") == "combat" and d.get("ship") != sp["ship"]]
        if not rows:
            errs.append(f"{what}: turn {n}: destroyed = {_short(destroyed_rows(t))}, expected the player's ship with cause 'combat' (C10)")
        elif rows[0].get("by") != sp["ship"]:
            errs.append(f"{what}: destroyed[*].by = {rows[0].get('by')!r}, expected the Bastion {sp['ship']} (C10)")
    if save_present:
        errs.append(f"{what}: the save file still exists after game over (OV-09c, C6)")
    return errs


# --------------------------------------------------------------------------- smoke/33 — scan (CMD-14, LV-04)

def check_scan(report_scan: dict, snap_scan: dict | None, report_plain: dict, snap_plain: dict | None, *, fear: int, respect: int, what: str) -> list[str]:
    errs = _prefix(report_scan, what)
    if errs:
        return errs
    sp = spawned_entries(report_scan)[0]
    t1 = _turns(report_scan)[0]
    texts = events_mentioning(t1, "scan")
    if not texts:
        errs.append(f"{what}: turn 1: no event mentions 'scan' (the sim records the immediate command's reply as an event; D-92, D-104)")
    else:
        joined = " ".join(texts)
        nums = {int(m) for m in re.findall(r"\b\d+\b", joined)}
        if fear not in nums or respect not in nums:
            errs.append(f"{what}: the scan text {joined[:200]!r} does not list the spawned captain's fear {fear} and respect {respect} (CMD-14, LV-04: scan lists "
                        f"captains in the sector with fear and respect)")
    for label, snap, want in (("after scan", snap_scan, True), ("without scan", snap_plain, False)):
        if not isinstance(snap, dict):
            errs.append(f"{what}: no save snapshot {label} (C6)")
            continue
        cap = (snap.get("captains") or {}).get(sp["captain"])
        if not isinstance(cap, dict):
            errs.append(f"{what}: {label}: captains[{sp['captain']}] missing from the snapshot")
        elif "visible_reputation" not in cap:
            errs.append(f"{what}: {label}: captains[*] has no 'visible_reputation' (C10 snapshot additions)")
        elif bool(cap["visible_reputation"]) is not want:
            errs.append(f"{what}: {label}: captains[{sp['captain']}].visible_reputation = {cap['visible_reputation']!r}, expected {want} (LV-04: true for captains in "
                        f"the player's sector after a scan this turn)")
    return errs


# --------------------------------------------------------------------------- smoke/34 — roaming (CFG-73, D-125)

def hole_sectors(index: dict) -> set[tuple]:
    out = set()
    for name in ("black_holes", "neutron_stars"):
        for h in index.get(name) or []:
            if isinstance(h, dict):
                out.add((tuple(int(v) for v in h["galaxy"]), tuple(int(v) for v in h["sector"])))
    return out


def check_roaming(report: dict, report_off: dict, index: dict, *, player_ship: str | None, every: int, what: str) -> list[str]:
    errs = _prefix(report, what, 0) + _prefix(report_off, what + " (roam_every_turns = 0)", 0)
    if errs:
        return errs
    roams = _get(report, "final", "roams")
    if not isinstance(roams, int) or isinstance(roams, bool):
        errs.append(f"{what}: final.roams = {roams!r}, expected an integer count of AI warps (C10)")
    elif roams < 1:
        errs.append(f"{what}: final.roams = 0 over {report.get('turns_completed')} turns with roam_every_turns = {every} — AI captains must warp (OV-10, CFG-73, D-125)")
    off = _get(report_off, "final", "roams")
    if off != 0:
        errs.append(f"{what}: final.roams = {off!r} with roam_every_turns = 0 in the temp ini, expected 0 — 0 disables roaming (CFG-73)")
    holes = hole_sectors(index)
    ai_warps, bad = 0, []
    for t in _turns(report):
        for w in t.get("warps") or []:
            if not isinstance(w, dict) or w.get("ship") == player_ship:
                continue
            ai_warps += 1
            to = w.get("to")
            if isinstance(to, list) and len(to) == 2 and (tuple(int(v) for v in to[0]), tuple(int(v) for v in to[1])) in holes:
                bad.append((t.get("turn_no"), w.get("ship"), to))
    if bad:
        errs.append(f"{what}: {len(bad)} AI warp(s) arrived in a sector holding a black hole or neutron star, e.g. turn {bad[0][0]} ship {bad[0][1]} → {bad[0][2]} "
                    f"(D-125: a random reachable sector with no hole; world_index)")
    if isinstance(roams, int) and roams >= 1 and ai_warps == 0:
        errs.append(f"{what}: final.roams = {roams} but per_turn.warps lists no AI ship — warps[] must record every warp (C9, C10) so arrivals can be checked")
    return errs


# --------------------------------------------------------------------------- smoke/36 — M2 fix-ups (D-114, D-115, D-116)

def check_wormhole_exit(report: dict, *, partner: dict, origin: dict, exit_distance: int, cls: str, held: bool, away_cells: list[int], what: str) -> list[str]:
    """Turn 1: the ship emerges `exit_distance` from the partner hole.  Kestrel (held): turn 2's move away is
    held (event 'held'), turn 3 it is pulled back through to the origin side.  Corsair: moves away by
    floor(engine − pull) cells per turn (away_cells = distances after each move) and lives."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    turns = _turns(report)
    if not turns:
        return [f"{what}: no turns in the report"]
    t1 = turns[0]
    pos = (t1.get("player") or {}).get("position")
    pg, ps = [list(partner["galaxy"]), list(partner["sector"])]
    if not (isinstance(pos, list) and len(pos) == 3):
        return [f"{what}: turn 1: player.position = {_short(pos)} (SH-09)"]
    if _norm(pos[:2]) != [pg, ps]:
        errs.append(f"{what}: turn 1: player in {pos[0]}/{pos[1]}, expected the partner's sector {pg}/{ps} (HZ-05 wormhole crossing)")
        return errs
    d1 = chebyshev(pos[2], partner["cell"])
    if d1 != exit_distance:
        errs.append(f"{what}: turn 1: emerged at {pos[2]}, {d1} from the partner hole {list(partner['cell'])}, expected wormhole_exit_distance {exit_distance} (CFG-62, D-114)")
    if held:
        t2 = turns[1] if len(turns) > 1 else None
        if t2 is None:
            errs.append(f"{what}: turn 2 missing (outcome {report.get('outcome')!r}) — a Kestrel at distance {exit_distance} is held, not destroyed (D-114)")
            return errs
        if not events_mentioning(t2, "held"):
            errs.append(f"{what}: turn 2: no event contains 'held' after a move away — floor(engine − pull) ≤ 0 at distance {exit_distance} holds a Kestrel (HZ-04, D-114)")
        p2 = (t2.get("player") or {}).get("position")
        if isinstance(p2, list) and len(p2) == 3 and _norm(p2[2]) != _norm(pos[2]):
            errs.append(f"{what}: turn 2: the Kestrel moved from {pos[2]} to {p2[2]} — it must be held in place (HZ-04)")
        t3 = turns[2] if len(turns) > 2 else None
        og, os_ = [list(origin["galaxy"]), list(origin["sector"])]
        if t3 is None:
            errs.append(f"{what}: turn 3 missing (outcome {report.get('outcome')!r}, cause {_get(report, 'final', 'game_over_cause')!r}) — the partner is a wormhole too: "
                        f"pulled in again, the ship crosses back alive (HZ-05, D-114)")
        else:
            p3 = (t3.get("player") or {}).get("position")
            if not (isinstance(p3, list) and len(p3) == 3 and _norm(p3[:2]) == [og, os_] and chebyshev(p3[2], origin["cell"]) == exit_distance):
                errs.append(f"{what}: turn 3: player at {_short(p3)}, expected back in {og}/{os_} at distance {exit_distance} from the origin hole {list(origin['cell'])} "
                            f"(N = ceil(pull_turns_scale / pull) at distance {exit_distance} is 2 with the defaults; the counter reset on crossing, D-113)")
    else:
        for i, want in enumerate(away_cells, start=2):
            t = turns[i - 1] if len(turns) >= i else None
            if t is None:
                errs.append(f"{what}: turn {i} missing (outcome {report.get('outcome')!r}) — a {cls} pulls away from distance {exit_distance} (D-114)")
                break
            p = (t.get("player") or {}).get("position")
            d = chebyshev(p[2], partner["cell"]) if isinstance(p, list) and len(p) == 3 else None
            if d != want:
                errs.append(f"{what}: turn {i}: the {cls} is {d} from the partner hole, expected {want} after moving directly away at full power "
                            f"(floor(engine − pull) cells; HZ-04, D-114 'a Corsair pulls away')")
                break
        if report.get("outcome") != "running":
            errs.append(f"{what}: outcome = {report.get('outcome')!r}, expected 'running' — the {cls} escapes the partner hole (D-114)")
    return errs


def check_mutiny_placement(report: dict, snapshot: dict | None, *, mutiny_turn: int, hazard: dict, kind: str, engine: float, what: str) -> list[str]:
    """SUP-04b, D-115, D-129: on the mutiny turn the demoted captain's new ship has left the cell it was held in,
    in the same sector.  kind 'star' (range = its own cell): player.hazard is null.  A hole or neutron star (range =
    the whole sector, HZ-04/06, so null may be impossible): player.hazard is null, or its pull lets the new ship
    move — floor(engine − pull) ≥ 1 — with turns_left > 0.  `engine` is the mutiny ship class's (CFG mutiny_ship_class)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    where = f"{hazard['galaxy']}/{hazard['sector']}"
    hcell = list(hazard["cell"])
    noun = "star" if kind == "star" else "hole"
    turns = _turns(report)
    t = next((x for x in turns if x.get("turn_no") == mutiny_turn), None)
    if t is None:
        errs.append(f"{what}: turn {mutiny_turn} missing (outcome {report.get('outcome')!r}, cause {_get(report, 'final', 'game_over_cause')!r}) — the ship must "
                    f"survive at the {noun} until the mutiny (the temp ini stretches pull_turns_scale)")
        return errs
    muts = [m for m in (_get(report, "final", "mutinies", default=[]) or []) if isinstance(m, dict) and m.get("turn_no") == mutiny_turn]
    if not muts:
        errs.append(f"{what}: final.mutinies has no entry at turn {mutiny_turn} ({_short(_get(report, 'final', 'mutinies'))}; D-76, SUP-03)")
        return errs
    prev = next((x for x in turns if x.get("turn_no") == mutiny_turn - 1), {})
    pprev = (prev.get("player") or {})
    if pprev.get("hazard") is None:
        errs.append(f"{what}: turn {mutiny_turn - 1}: player.hazard is null — the scenario needs the ship inside the {noun}'s range before the mutiny (gate logic)")
    held = pprev.get("position")
    held_cell = _norm(held[2]) if isinstance(held, list) and len(held) == 3 else None
    pl = t.get("player") or {}
    pos = pl.get("position")
    if not (isinstance(pos, list) and len(pos) == 3):
        errs.append(f"{what}: turn {mutiny_turn}: player.position = {_short(pos)} (SH-09)")
        return errs
    if _norm(pos[:2]) != [list(hazard["galaxy"]), list(hazard["sector"])]:
        errs.append(f"{what}: turn {mutiny_turn}: player in {pos[0]}/{pos[1]} after the mutiny, expected the same sector {where} — the demoted captain's "
                    f"Kestrel appears at the nearest safe cell *of the same sector* (SUP-04b, D-115)")
        return errs
    if held_cell is not None and _norm(pos[2]) == held_cell:
        errs.append(f"{what}: turn {mutiny_turn}: player.position still {pos[2]} after the mutiny — the demoted captain's Kestrel must leave the cell it was held in "
                    f"({chebyshev(pos[2], hcell)} from the {noun} at {hcell} in {where}; SUP-04b, D-115, D-129)")
    hz = pl.get("hazard")
    if kind == "star":
        if hz is not None:
            errs.append(f"{what}: turn {mutiny_turn}: player.hazard = {_short(hz)} after the mutiny, expected null — a star's range is its own cell, so the nearest cell "
                        f"outside every pull range is a neighbour of {hcell} in {where} (HZ-01, SUP-04b, D-115, D-129)")
        return errs
    if hz is None:
        return errs
    if not isinstance(hz, dict):
        errs.append(f"{what}: turn {mutiny_turn}: player.hazard = {_short(hz)} after the mutiny, expected null or a hazard object (C2)")
        return errs
    pull, left = hz.get("pull"), hz.get("turns_left")
    if not isinstance(pull, (int, float)):
        errs.append(f"{what}: turn {mutiny_turn}: player.hazard.pull = {_short(pull)} after the mutiny, expected a number (C2)")
        return errs
    room = math.floor(float(engine) - float(pull))
    if room < 1:
        errs.append(f"{what}: turn {mutiny_turn}: player.hazard.pull = {float(pull):.3f} at {pos[2]} after the mutiny: floor(engine {float(engine):g} − pull) = {room} < 1, "
                    f"so the new Kestrel is held like the old one — with the whole sector in the {noun}'s range (HZ-04) it must appear at the nearest cell "
                    f"where it can move ({hcell} in {where}; SUP-04b, D-129)")
    if not (isinstance(left, int) and not isinstance(left, bool) and left > 0):
        errs.append(f"{what}: turn {mutiny_turn}: player.hazard.turns_left = {_short(left)} after the mutiny, expected > 0 — the new ship starts its own "
                    f"countdown at its own distance (HZ-04, HZ-08, D-129)")
    return errs


def check_planet_bands(indexes: dict[int, dict], *, lo: int, hi: int, what: str) -> list[str]:
    """D-116 / CFG-61: every indexed planet lies lo…hi (Chebyshev) from its nearest star; some lie beyond lo."""
    errs: list[str] = []
    judged, spread, bad = 0, 0, []
    for seed, index in indexes.items():
        stars: dict[tuple, list[tuple[int, int]]] = {}
        for s in index.get("stars") or []:
            if isinstance(s, dict):
                stars.setdefault((tuple(int(v) for v in s["galaxy"]), tuple(int(v) for v in s["sector"])), []).append((int(s["cell"][0]), int(s["cell"][1])))
        for p in index.get("planets") or []:
            if not isinstance(p, dict):
                continue
            key = (tuple(int(v) for v in p["galaxy"]), tuple(int(v) for v in p["sector"]))
            if key not in stars:
                continue
            judged += 1
            d = min(chebyshev(p["cell"], c) for c in stars[key])
            if d > lo:
                spread += 1
            if not lo <= d <= hi:
                bad.append((seed, key, list(p["cell"]), d))
    if judged == 0:
        errs.append(f"{what}: no planet shares a sector with an indexed star — nothing to judge (C9 world_index lists ≤ 200 nearest entries per list)")
        return errs
    if bad:
        errs.append(f"{what}: {len(bad)} planet(s) outside the band {lo}…{hi} from their nearest star, e.g. seed {bad[0][0]} {bad[0][1]} planet at {bad[0][2]} at distance "
                    f"{bad[0][3]} (SP-04, CFG-61 planet_distance_min/max, D-116)")
    if spread == 0:
        errs.append(f"{what}: all {judged} judged planets sit at distance {lo} from their star — the band {lo}…{hi} must be used (D-116: some systems spread)")
    return errs


# --------------------------------------------------------------------------- smoke/12 (extended) — owners (D-105)

def check_owners(snapshot: dict | None, what: str) -> list[str]:
    if not isinstance(snapshot, dict):
        return []
    caps = snapshot.get("captains") or {}
    owners = {s.get("captain") for s in (snapshot.get("ships") or {}).values() if isinstance(s, dict)}
    shipless = [c for c, cap in caps.items() if c not in owners and not (cap or {}).get("is_player")]
    if shipless:
        return [f"{what}: {len(shipless)} captain(s) own no ship in the final snapshot, e.g. {shipless[0]} — a captain whose last ship is destroyed or captured is "
                f"removed the same turn (D-105); captain count must equal the distinct ship owners"]
    if len(caps) != len(owners | {snapshot.get("player_id")}):
        return [f"{what}: {len(caps)} captains but {len(owners)} distinct ship owners in the final snapshot (D-105)"]
    return []


# --------------------------------------------------------------------------- smoke/35 — combat UI (SYM-11/12, GRD-08, PNL-05/06, OUT-04…09)

ENEMY_SHIELDS = 37.0
COMBAT_EVENTS = [
    {"phase": "TP-04", "type": "combat", "severity": "alert", "text": "Lantern → Grey Heron: torpedo hit, 25 damage, shields left 37", "ids": ["OUT-04"]},
    {"phase": "TP-04", "type": "combat", "severity": "info", "text": "Grey Heron → Lantern: phaser hit, 10 damage, shields left 50", "ids": ["OUT-04"]},
    {"phase": "TP-04", "type": "peace", "severity": "highlight", "text": "Grey Heron offers peace", "ids": ["OUT-05"]},
    {"phase": "TP-04", "type": "loot", "severity": "highlight", "text": "Loot from Grey Heron: 2 gold, 10 silver, 1 item", "ids": ["OUT-06"]},
    {"phase": "TP-07", "type": "reputation", "severity": "info", "text": "FR-01: fear +8, respect +0 → fear 18, respect 10", "ids": ["OUT-09"]},
]


def combat_render_snapshots(snap: dict) -> dict[str, Any]:
    """From a real C3 snapshot: 'without' (no battle), 'combat' (the player in battle b-001 with an AI ship one
    cell away, that ship the last target, a second owned ship in the fleet) and 'shifted' (the player's ship one
    cell to the right, to measure the grid's cell width)."""
    base = copy.deepcopy(snap)
    sid, ship = player_ship(base)
    pid = base["player_id"]
    w, h = base["sizes"]["sector"]
    (gx, gy), (sx, sy), _ = ship["position"]
    cx, cy = (10, 8) if (w > 12 and h > 10) else (min(w - 2, 2), min(h - 2, 2))
    ship["position"] = [[gx, gy], [sx, sy], [cx, cy]]
    ship.update(hazard=None, move_remaining=None, orders=[], in_battle=None, target=None)
    base["game_over"] = False
    base["battles"] = []
    key = f"{gx},{gy}/{sx},{sy}"
    sector = base.setdefault("sectors", {}).setdefault(key, {"terrain": [], "items": []})
    sector["terrain"] = [t for t in sector.get("terrain") or [] if list(t.get("cell") or []) not in ([cx, cy], [cx + 1, cy], [cx + 2, cy], [cx + 1, cy + 1])]
    for other_sid, other in list(base["ships"].items()):              # every other ship out of the probe cells
        if other_sid != sid and other.get("position", [[], [], []])[:2] == [[gx, gy], [sx, sy]]:
            other["position"][2] = [max(0, cx - 4), max(0, cy - 4)]
        other.setdefault("in_battle", None)
        other.setdefault("target", None)
    for cap in base["captains"].values():
        cap.setdefault("visible_reputation", False)
    enemy_sid, enemy_cid = "s-gate-enemy", "c-gate-enemy"
    fleet_sid = "s-gate-fleet"
    base["captains"][enemy_cid] = {"name": "Vesper Quill", "is_player": False, "gold": 3, "silver": 20, "fear": 25, "respect": 12, "food": 30, "water": 30,
                                   "ships": [enemy_sid], "active_ship": enemy_sid, "items": [], "is_master": False, "mutiny_countdown": None,
                                   "visible_reputation": False}
    base["ships"][enemy_sid] = {"name": "Grey Heron", "class": "corsair", "captain": enemy_cid, "torpedoes": 11, "phasers": 5.0, "shield_max": 80.0,
                                "shield_current": 80.0, "engine": 6.0, "warp": 5.0, "position": [[gx, gy], [sx, sy], [cx + 1, cy]], "orders_queued": 0,
                                "orders": [], "hazard": None, "move_remaining": None, "in_battle": None, "target": None}
    without = base
    combat = copy.deepcopy(base)
    combat["ships"][sid].update(in_battle="b-001", target=enemy_sid)
    combat["ships"][enemy_sid].update(in_battle="b-001", target=sid, shield_current=ENEMY_SHIELDS)
    combat["ships"][fleet_sid] = {"name": "Tallow Wren", "class": "kestrel", "captain": pid, "torpedoes": 4, "phasers": 3.0, "shield_max": 60.0,
                                  "shield_current": 52.0, "engine": 4.0, "warp": 3.0, "position": [[gx, gy], [sx, sy], [cx + 1, cy + 1]], "orders_queued": 0,
                                  "orders": [], "hazard": None, "move_remaining": None, "in_battle": None, "target": None}
    combat["captains"][pid]["ships"] = [sid, fleet_sid]
    combat["battles"] = [{"id": "b-001", "sides": [pid, enemy_cid], "opened_turn": max(1, int(combat.get("turn_no") or 1) - 1), "opened_by": enemy_cid,
                          "strength_at_open": {pid: 255.0, enemy_cid: 455.0}, "ended": None, "victor": None, "transfer": None}]
    shifted = copy.deepcopy(without)
    shifted["ships"][sid]["position"] = [[gx, gy], [sx, sy], [cx + 1, cy]]
    shifted["ships"][enemy_sid]["position"] = [[gx, gy], [sx, sy], [cx + 2, cy]]
    return {"without": without, "combat": combat, "shifted": shifted, "ship_id": sid, "enemy_ship": enemy_sid, "enemy_captain": enemy_cid,
            "fleet_ship": fleet_sid, "player_cell": [cx, cy], "enemy_cell": [cx + 1, cy]}


def check_combat_render(probe: dict, glyph: str, variants: dict) -> list[str]:
    """probe = {"renders": {"grid": [combat, without, shifted], "status": [combat, without], "output": [events_text]},
    "styled": {"grid": [combat_ansi, without_ansi]}, "errors": {…}}."""
    errs: list[str] = []
    for role, err in (probe.get("errors") or {}).items():
        errs.append(f"render_{role}.render() raised: {err.strip().splitlines()[-1] if err.strip() else err}")
    for mod, err in (probe.get("import_errors") or {}).items():
        errs.append(f"import {mod} failed: {err.strip().splitlines()[-1] if err.strip() else err}")
    renders, styled = probe.get("renders") or {}, probe.get("styled") or {}
    g, gs = renders.get("grid") or [], styled.get("grid") or []
    enemy = variants["combat"]["ships"][variants["enemy_ship"]]
    if len(g) < 3 or len(gs) < 2:
        errs.append("render_grid: no rendered text captured for the combat / no-battle / shifted snapshots")
    else:
        combat_t, without_t, shifted_t = g[0], g[1], g[2]
        p0, p1 = glyph_positions(without_t, glyph), glyph_positions(shifted_t, glyph)
        old, new = p0 - p1, p1 - p0                       # the glyph that moved is the ship (the legend's copy stays put)
        if len(old) != 1 or len(new) != 1:
            errs.append(f"render_grid: shifting the ship one cell right should move exactly one {glyph!r}; positions changed {sorted(old)} → {sorted(new)} (SYM-10, GRD-01)")
        else:
            (l0, c0), (l1, c1) = next(iter(old)), next(iter(new))
            cell_w = c1 - c0
            if l1 != l0 or cell_w <= 0:
                errs.append(f"render_grid: shifting the ship one cell right moved the glyph from line {l0} col {c0} to line {l1} col {c1} (GRD-01)")
            else:
                ex, ey = variants["enemy_cell"][0] - variants["player_cell"][0], variants["enemy_cell"][1] - variants["player_cell"][1]
                line, col = l0 + ey, c0 + ex * cell_w
                lc, lw = combat_t.splitlines(), without_t.splitlines()
                win_c = lc[line][max(0, col - cell_w): col + 2 * cell_w] if line < len(lc) else ""
                win_w = lw[line][max(0, col - cell_w): col + 2 * cell_w] if line < len(lw) else ""
                if win_c == win_w:
                    errs.append(f"render_grid: the target cell {variants['enemy_cell']} reads {win_w!r} with and without the battle — no GRD-08 target marker on the "
                                f"last fire/peace target (ships[*].target; C4)")
                a, b = ansi_cells(gs[0]), ansi_cells(gs[1])
                if line < len(a) and line < len(b):
                    sa = [s for ch, s in a[line][col: col + cell_w] if not ch.isspace()][:1]     # the glyph's own style
                    sb = [s for ch, s in b[line][col: col + cell_w] if not ch.isspace()][:1]
                    if sa == sb:
                        errs.append(f"render_grid: the AI ship at {variants['enemy_cell']} has the same style with in_battle = 'b-001' as without — an AI ship in battle "
                                    f"with the player renders in the SYM-12 battle colour (C4)")
                else:
                    errs.append("render_grid: the enemy's row is missing from the styled render")
    st = renders.get("status") or []
    if len(st) < 2:
        errs.append("render_status: no rendered text captured for the combat / no-battle snapshots")
    else:
        combat_s, without_s = st[0], st[1]
        new_lines = [ln for ln in combat_s.splitlines() if ln not in set(without_s.splitlines())]
        joined = "\n".join(new_lines)
        name = enemy["name"]
        if name not in joined:
            errs.append(f"render_status: the enemy in range {name!r} is not among the lines added by the battle (PNL-06: enemies in range with name, distance, shields; C4)"
                        + (f"; added lines: {new_lines[:5]!r}" if new_lines else "; no line differs from the no-battle panel"))
        shields = enemy["shield_current"]
        if not re.search(rf"\b{int(shields)}(\.0+)?\b", joined):
            errs.append(f"render_status: the enemy's shields {shields:g} do not appear in the lines added by the battle (PNL-06)")
        d = chebyshev(variants["enemy_cell"], variants["player_cell"])
        if not re.search(rf"\b{d}\b", joined):
            errs.append(f"render_status: the enemy's distance {d} does not appear in the lines added by the battle (PNL-06)")
        fleet = variants["combat"]["ships"][variants["fleet_ship"]]["name"]
        if fleet not in combat_s:
            errs.append(f"render_status: the second owned ship {fleet!r} is not listed (PNL-05: every owned ship; C4)")
    out = renders.get("output") or []
    if not out:
        errs.append("render_output: no rendered text captured for the combat events")
    else:
        text = out[0]
        checks = (("torpedo", "OUT-04 weapon"), ("hit", "OUT-04 hit/miss"), ("25", "OUT-04 damage"), ("37", "OUT-04 shields left"),
                  ("peace", "OUT-05 peace"), ("gold", "OUT-06 loot"), ("FR-01", "OUT-09 rule"), ("+8", "OUT-09 Δfear"))
        for needle, label in checks:
            if needle.lower() not in text.lower():
                errs.append(f"render_output: {label}: {needle!r} missing from the render of the combat events (C4, {label.split()[0]})")
        last = -1
        for ev in COMBAT_EVENTS:
            i = text.find(ev["text"].split(":")[0])
            if i >= 0 and i < last:
                errs.append("render_output: combat events are not in the given (phase) order (OB-04)")
                break
            last = max(last, i)
    return errs


# --------------------------------------------------------------------------- live/09 — combat over the bus (I/O driver + pure checker)

def spawn_ship_in(snapshot: dict, cell: Any, player_id: str | None) -> tuple[str, dict] | None:
    """The non-player ship standing on `cell` in the player's sector (the --spawn), from a snapshot."""
    try:
        _, pship = player_ship(snapshot)
    except KeyError:
        return None
    sector = pship["position"][:2]
    for sid, s in (snapshot.get("ships") or {}).items():
        if not isinstance(s, dict) or s.get("captain") == player_id:
            continue
        pos = s.get("position")
        if isinstance(pos, list) and len(pos) == 3 and _norm(pos[:2]) == _norm(sector) and _norm(pos[2]) == _norm(list(cell)):
            return sid, s
    return None


def drive_combat(pub_port: int, pull_port: int, *, spawn_cell: Any, turns: int = 2, hello_timeout: float = 15.0, reply_timeout: float = 5.0,
                 turn_timeout: float = 10.0, after_first: Callable[[dict], Any] | None = None) -> dict:
    """live/09: find the spawned ship in state.hello, push `fire phaser <id>`, then turn.end × turns; record the
    reply, each turn.resolved's event types and the player ship's in_battle.  `after_first(obs)` runs once the
    first turn.resolved is in (the live gate reads the heartbeats there)."""
    obs: dict = {"hello": None, "player_id": None, "ship_id": None, "target": None, "fire_reply": None, "resolved": [], "after_first": None, "notes": []}
    bo = BusObservations()
    client = BusClient(pub_port, pull_port)
    try:
        first = client.wait_for("state.hello", hello_timeout, bo)
        if first is None:
            obs["notes"].append(f"no state.hello within {hello_timeout:.0f} s (topics seen: {dict(bo.topics) or 'none'})")
            return obs
        snapshot = first.get("snapshot") or {}
        obs["hello"] = {"turn_no": first.get("turn_no"), "seed": first.get("seed")}
        obs["player_id"] = snapshot.get("player_id")
        turn_no = int(first.get("turn_no") or snapshot.get("turn_no") or 0)
        try:
            sid, _ = player_ship(snapshot)
        except KeyError as e:
            obs["notes"].append(str(e))
            return obs
        obs["ship_id"] = sid
        got = spawn_ship_in(snapshot, spawn_cell, obs["player_id"])
        if got is None:
            obs["notes"].append(f"no non-player ship at the spawn cell {list(spawn_cell)} in state.hello's snapshot (--spawn, D-126)")
            return obs
        obs["target"] = got[0]
        client.send("order.submitted", {"command": "fire", "args": ["phaser", got[0]], "ship_id": sid, "turn_no": turn_no, "client": "command"})
        rep = client.wait_for_any(("order.accepted", "order.rejected"), reply_timeout, bo)
        obs["fire_reply"] = {"topic": rep[0], "reason": str((rep[1] or {}).get("reason", ""))} if rep else None
        for i in range(turns):
            client.send("turn.end", {"turn_no": turn_no})
            res = client.wait_for("turn.resolved", turn_timeout, bo, pred=lambda p: isinstance(p.get("turn_no"), int) and p["turn_no"] > turn_no)
            if res is None:
                obs["notes"].append(f"no turn.resolved within {turn_timeout:.0f} s after turn.end for turn {turn_no}")
                return obs
            turn_no = int(res["turn_no"])
            snap = res.get("snapshot") or {}
            ship = (snap.get("ships") or {}).get(sid) or {}
            evs = res.get("events") or []
            obs["resolved"].append({"turn_no": turn_no, "types": [e.get("type") for e in evs if isinstance(e, dict)],
                                    "combat_texts": [str(e.get("text", ""))[:120] for e in evs if isinstance(e, dict) and e.get("type") == "combat"],
                                    "in_battle": ship.get("in_battle", "<missing>"), "target": ship.get("target", "<missing>"),
                                    "battles": len(snap.get("battles") or []) if isinstance(snap.get("battles"), list) else "<missing>"})
            if i == 0 and after_first is not None:
                obs["after_first"] = after_first(obs)
        return obs
    finally:
        client.close()


def check_combat_bus(obs: dict, *, turns: int = 2) -> list[str]:
    """live/09 — CM-01, OUT-04, C3 (event type 'combat'), C10 snapshot additions."""
    errs: list[str] = []
    if obs.get("hello") is None:
        return ["no state.hello from the engine: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C3)"]
    if obs.get("target") is None:
        return ["the spawned ship was not found: " + "; ".join(obs.get("notes") or ["no notes"]) + " (--spawn is forwarded by the launcher to the engine, C10 C1 additions)"]
    fr = obs.get("fire_reply")
    if fr is None:
        errs.append(f"no reply to order.submitted 'fire phaser {obs['target']}' (MSG-06 / MSG-07)")
    elif fr.get("topic") != "order.accepted":
        errs.append(f"'fire phaser {obs['target']}' was rejected: {fr.get('reason')!r} — a spawn one cell away is within phaser range (CM-02, CMD-08)")
    res = obs.get("resolved") or []
    if len(res) < turns:
        errs.append(f"{len(res)} turn.resolved for {turns} turn.end pushes: " + "; ".join(obs.get("notes") or ["no notes"]) + " (MSG-02 → MSG-03)")
        if not res:
            return errs
    first = res[0]
    if "combat" not in (first.get("types") or []):
        errs.append(f"turn.resolved {first.get('turn_no')} events carry no type 'combat' (types {first.get('types')}) — the exchange publishes OUT-04 lines (C3 additions, CM-06)")
    if first.get("in_battle") == "<missing>":
        errs.append("turn.resolved snapshot ships[*] has no 'in_battle' (C10 snapshot additions)")
    elif not first.get("in_battle"):
        errs.append(f"turn.resolved snapshot: the player's ship in_battle = {first.get('in_battle')!r}, expected the battle id (C10, CM-01)")
    if first.get("target") == "<missing>":
        errs.append("turn.resolved snapshot ships[*] has no 'target' (C10 snapshot additions, GRD-08)")
    elif first.get("target") != obs["target"]:
        errs.append(f"turn.resolved snapshot: the player's ship target = {first.get('target')!r}, expected {obs['target']} (GRD-08)")
    if first.get("battles") == "<missing>":
        errs.append("turn.resolved snapshot has no 'battles' list (C10 snapshot additions)")
    elif first.get("battles") == 0:
        errs.append("turn.resolved snapshot.battles is empty although a fire was accepted (CM-01, D-119)")
    return errs
