"""M3 fixtures for --selftest (specs/M3.md § 4, CONTRACTS C10), called by make_fixtures.py.

sim_m3_scenarios.json holds one good synthetic C2+C9+C10 report (plus the save snapshot / probe
facts a row reads) per gate scenario, each with the exact checker call the gate makes
(`check`, `args`, `kwargs`), so the selftest proves every checker passes its good fixture and
fails its broken one for the right reason.  The broken fixtures the spec names —
sim_fight_never_opens, sim_phaser_flat, sim_torpedo_always_hits, sim_loot_missing,
sim_peace_wrong_victor, sim_escape_ignored, sim_rep_unscaled, sim_ai_never_attacks,
sim_capture_no_switch, sim_combat_no_game_over, sim_scan_hidden, sim_no_roam,
sim_wormhole_exit_1 — plus sim_mutiny_held, sim_mutiny_same_cell and sim_mutiny_star_cell
for the D-129 mutiny placement — are minimal edits of the good ones.  All numbers follow the default
controls_config.ini (FIXTURE_M3) and the temp inis the gate writes.  No franchise names (D-03).
"""
from __future__ import annotations

import copy
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _combat as CB  # noqa: E402
from _checks import canonical_hash  # noqa: E402

FIXTURE_M3 = {
    "phaser_range": 2, "torpedo_range": 5, "torpedo_hit_base": 0.7, "torpedo_evade": 0.03, "torpedo_damage": 25.0, "phaser_per_unit": 5.0,
    "shield_recharge": 5.0, "peace_loot_max": 50.0, "attack_ratio_min": 1.3, "attack_fear_max": 50, "peace_shield_pct": 30.0, "escape_shield_pct": 15.0,
    "yield_fear_min": 80, "peace_pct_at_fear100": 60.0, "surrender_shield_pct": 20.0, "roam_every_turns": 10,
    "fear_won": 8, "fear_lost": 8, "fear_start": 10, "win_vs_stronger": 10, "win_vs_weaker_peace": 8, "win_vs_weaker_started": 8, "start_fight_delta": 5,
    "respect_start": 10, "scale_min": 0.5, "scale_max": 2.0, "impulse_max": 10.0, "warp_max": 10.0, "gold_start": 5, "silver_start": 50,
    "wormhole_exit_distance": 2, "planet_distance_min": 1, "planet_distance_max": 3,
    "classes": {"kestrel": {"torpedoes": 6.0, "phasers": 3.0, "shield_max": 60.0, "engine": 4.0, "warp": 3.0},
                "corsair": {"torpedoes": 12.0, "phasers": 5.0, "shield_max": 80.0, "engine": 6.0, "warp": 5.0},
                "bastion": {"torpedoes": 20.0, "phasers": 8.0, "shield_max": 100.0, "engine": 3.0, "warp": 4.0}},
}
PID, PSHIP, PNAME = "c-000", "s-194", "Lantern"
SPAWN_C, SPAWN_S, SPAWN_NAME = "c-900", "s-900", "Cinder Lark"
SPAWN2_C, SPAWN2_S = "c-901", "s-901"
HOME = [[3, 4], [7, 9]]
CELL = [12, 11]
CONFIG_PATH = "E:\\CODE\\master-of-starbour\\controls_config.ini"


def fake_hash(tag: str) -> str:
    import hashlib
    return hashlib.sha1(tag.encode()).hexdigest()


# --------------------------------------------------------------------------- builders

def snapshot(turn_no: int, *, player_cell=CELL, spawns: list[dict] | None = None, player_ship: dict | None = None, captain: dict | None = None,
             player_ships: list[str] | None = None, active: str = PSHIP, extra_ships: dict | None = None, visible: bool = False) -> dict:
    ship = {"name": PNAME, "class": "kestrel", "captain": PID, "torpedoes": 6, "phasers": 3.0, "shield_max": 60.0, "shield_current": 60.0, "engine": 4.0, "warp": 3.0,
            "position": [HOME[0], HOME[1], list(player_cell)], "orders_queued": 0, "orders": [], "hazard": None, "move_remaining": None, "in_battle": None, "target": None}
    ship.update(player_ship or {})
    cap = {"name": "Harrow Vey", "is_player": True, "gold": 5, "silver": 50, "fear": 10, "respect": 10, "food": 29, "water": 29, "ships": player_ships or [PSHIP],
           "active_ship": active, "items": [], "is_master": False, "mutiny_countdown": None, "visible_reputation": False}
    cap.update(captain or {})
    snap = {"turn_no": turn_no, "seed": 1234, "version": "0.1.0", "sizes": {"universe": [8, 8], "galaxy": [16, 16], "sector": [24, 24]}, "player_id": PID,
            "captains": {PID: cap}, "ships": {PSHIP: ship}, "universe": {"galaxies": [[3, 4]]},
            "galaxies": {"3,4": {"sectors": [[7, 9], [8, 9], [9, 9], [10, 9], [7, 10]], "visited": [[7, 9]], "hazards": []}},
            "sectors": {"3,4/7,9": {"terrain": [{"kind": "star", "cell": [5, 5], "colour": "yellow", "bucket": "medium"}, {"kind": "planet", "cell": [6, 5], "name": "Oreth"}],
                                    "items": []}},
            "fog_of_war": True, "view": {"level": "follow"}, "game_over": False, "battles": []}
    for sp in spawns or []:
        cid, sid = sp["captain"], sp["ship"]
        snap["captains"][cid] = {"name": sp.get("name", SPAWN_NAME), "is_player": False, "gold": sp.get("gold", 3), "silver": sp.get("silver", 20), "fear": sp.get("fear", 10),
                                 "respect": sp.get("respect", 10), "food": 30, "water": 30, "ships": [sid], "active_ship": sid, "items": sp.get("items", []),
                                 "is_master": False, "mutiny_countdown": None, "visible_reputation": visible}
        cls = dict(FIXTURE_M3["classes"][sp.get("class", "kestrel")])
        cls.update(sp.get("stats", {}))
        snap["ships"][sid] = {"name": sp.get("name", SPAWN_NAME), "class": sp.get("class", "kestrel"), "captain": cid, "torpedoes": cls["torpedoes"], "phasers": cls["phasers"],
                              "shield_max": cls["shield_max"], "shield_current": sp.get("shield_current", cls["shield_max"]), "engine": cls["engine"], "warp": cls["warp"],
                              "position": [HOME[0], HOME[1], list(sp.get("cell", [13, 11]))], "orders_queued": 0, "orders": [], "hazard": None, "move_remaining": None,
                              "in_battle": None, "target": None}
    for sid, s in (extra_ships or {}).items():
        snap["ships"][sid] = s
    return snap


def ex(attacker: str, defender: str, weapon: str, distance: int, hit: bool, damage: float, after: float) -> dict:
    return {"attacker": attacker, "defender": defender, "weapon": weapon, "distance": distance, "hit": hit, "damage": float(damage), "shields_after": float(after)}


def battle(sides: list[str], opened_turn: int, opened_by: str, strength: dict, exchanges: list[dict], *, ended=None, victor=None, transfer=None, bid="b-001") -> dict:
    return {"id": bid, "sides": list(sides), "opened_turn": opened_turn, "opened_by": opened_by, "strength_at_open": dict(strength), "exchanges": list(exchanges),
            "ended": ended, "victor": victor, "transfer": transfer}


def rep(captain: str, rule: str, fear: int, respect: int, scale: float = 1.0) -> dict:
    return {"captain": captain, "rule": rule, "fear": fear, "respect": respect, "scale": scale}


def report(turns: list[dict], *, requested: int | None = None, outcome: str = "running", spawned: list[dict] | None = None, final: dict | None = None,
           seed: int = 1234, index: dict | None = None) -> dict:
    """turns: per-turn overrides {player: {...}, battles, reputation, captures, destroyed, events, warps, captains}."""
    per_turn = []
    captains = 193 + len(spawned or [])
    player = {"food": 29, "water": 29, "fear": FIXTURE_M3["fear_start"], "respect": FIXTURE_M3["respect_start"], "gold": 5, "silver": 50, "ship_class": "kestrel",
              "position": [HOME[0], HOME[1], list(CELL)], "shield_current": 60.0, "shield_max": 60.0, "hazard": None, "move_remaining": None, "in_battle": False, "ships": 1}
    for i, t in enumerate(turns, start=1):
        player = dict(player, food=max(0, 30 - i), water=max(0, 30 - i))
        player.update(t.get("player") or {})
        captains = t.get("captains", captains)
        battles = copy.deepcopy(t.get("battles") or [])
        player["in_battle"] = t.get("player", {}).get("in_battle", any(b.get("ended") is None and PID in b.get("sides", []) for b in battles))
        per_turn.append({"turn_no": i, "orders_executed": {"player": 1, "ai": 2}, "ai_ships_moved": 2, "player": player, "captains": captains,
                         "events": list(t.get("events") or [f"Turn {i} resolved"]), "snapshot_hash": fake_hash(f"m3-{seed}-{i}-{t.get('tag', '')}"),
                         "snapshot_bytes": 180000, "resolve_ms": 6.0, "warps": list(t.get("warps") or []), "destroyed": list(t.get("destroyed") or []),
                         "battles": battles, "reputation": list(t.get("reputation") or []), "captures": list(t.get("captures") or [])})
    n = len(per_turn)
    all_b = {}
    for t in per_turn:
        for b in t["battles"]:
            all_b[b["id"]] = b
    by_outcome = {"destroyed": 0, "peace": 0, "escape": 0, "open": 0}
    for b in all_b.values():
        by_outcome[b["ended"] or "open"] += 1
    fin = {"captains": captains, "ships": captains, "mutinies": [], "game_over_turn": n if outcome == "game_over" else None, "mastery_turn": None,
           "game_over_cause": None, "hazard_deaths": 0, "warps": sum(len(t["warps"]) for t in per_turn), "battles": len(all_b), "battles_by_outcome": by_outcome,
           "combat_deaths": sum(len(t["destroyed"]) for t in per_turn), "captures": sum(len(t["captures"]) for t in per_turn),
           "ai_vs_ai_battles": sum(1 for b in all_b.values() if PID not in b["sides"]), "player_fear": per_turn[-1]["player"]["fear"] if per_turn else 10,
           "player_respect": per_turn[-1]["player"]["respect"] if per_turn else 10, "roams": sum(1 for t in per_turn for w in t["warps"] if w.get("ship") != PSHIP)}
    fin.update(final or {})
    doc = {"schema": 1, "seed": seed, "config_path": CONFIG_PATH, "turns_requested": requested or n, "turns_completed": n, "outcome": outcome, "elapsed_s": 1.0,
           "per_turn": per_turn, "final": fin, "spawned": [{"ship": s["ship"], "captain": s["captain"], "class": s.get("class", "kestrel"), "cell": list(s.get("cell", [13, 11]))}
                                                             for s in (spawned or [])]}
    if index is not None:
        doc["world_index"] = index
    return doc


SP = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "kestrel", "cell": [13, 11]}
STR_K = CB.strength(60, 6, 3, torpedo_damage=25, phaser_per_unit=5)      # 255


def case(check: str, args: list[str], kwargs: dict, **data) -> dict:
    return {"check": check, "args": args, "kwargs": kwargs, **data}


# --------------------------------------------------------------------------- the good scenarios

def opening() -> dict:
    sides = [PID, SPAWN_C]
    strength = {PID: STR_K, SPAWN_C: STR_K}
    t1 = {"battles": [battle(sides, 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 1, True, 10, 50), ex(SPAWN_S, PSHIP, "torpedo", 1, False, 0, 60),
                                                         ex(SPAWN_S, PSHIP, "phaser", 1, True, 10, 50)])],
          "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5, "shield_current": 50.0}, "events": ["script order 'fire phaser s-900' accepted", "Lantern → Cinder Lark: phaser hit, 10 damage, shields left 50", "FR-06: respect −5"]}
    t2 = {"battles": [battle(sides, 1, PID, strength, [ex(SPAWN_S, PSHIP, "phaser", 1, True, 10, 40)])], "player": {"respect": 5, "shield_current": 40.0}}
    t3 = {"battles": [battle(sides, 1, PID, strength, [ex(SPAWN_S, PSHIP, "phaser", 1, True, 10, 30)])], "player": {"respect": 5, "shield_current": 30.0}}
    rpt = report([t1, t2, t3], spawned=[SP])
    kw = {"fired": True, "start_fight_delta": 5, "respect_start": 10, "what": "fixture opening"}
    return case("opening", ["report", "snapshot"], kw, report=rpt, snapshot=snapshot(3, spawns=[SP], player_ship={"in_battle": "b-001", "target": SPAWN_S}))


def no_fire() -> dict:
    rpt = report([{} for _ in range(10)], spawned=[SP])
    return case("opening", ["report", "snapshot"], {"fired": False, "start_fight_delta": 5, "respect_start": 10, "what": "fixture no fire"}, report=rpt,
                snapshot=snapshot(10, spawns=[SP]))


def phaser(d: int) -> dict:
    dmg = CB.phaser_damage(3, 5, d)
    sp = dict(SP, cell=[12 + d, 11])
    t1 = {"battles": [battle([PID, SPAWN_C], 1, PID, {PID: STR_K, SPAWN_C: STR_K}, [ex(PSHIP, SPAWN_S, "phaser", d, True, dmg, 60 - dmg), ex(SPAWN_S, PSHIP, "phaser", d, True, dmg, 60 - dmg)])],
          "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5, "shield_current": 60 - dmg}}
    return case("phaser", ["report", "snapshot"], {"distance": d, "phasers": 3.0, "per_unit": 5.0, "target_shield_max": 60.0, "what": f"fixture phaser d{d}"},
                report=report([t1], spawned=[sp]), snapshot=snapshot(1, spawns=[sp]))


def phaser_rejected() -> dict:
    sp = dict(SP, cell=[15, 11])
    t1 = {"events": ["script order 'fire phaser s-900' rejected: target s-900 is 3 cells away; phaser range 2 (CM-02)"]}
    return case("phaser_rejected", ["report"], {"distance": 3, "phaser_range": 2, "what": "fixture phaser rejected"}, report=report([t1], spawned=[sp]))


def torpedo_volley() -> dict:
    rows = [ex(PSHIP, SPAWN_S, "torpedo", 1, True, 25, 35), ex(PSHIP, SPAWN_S, "torpedo", 1, False, 0, 35), ex(PSHIP, SPAWN_S, "torpedo", 1, True, 25, 10),
            ex(SPAWN_S, PSHIP, "torpedo", 1, True, 25, 35), ex(SPAWN_S, PSHIP, "phaser", 1, True, 10, 25)]
    t1 = {"battles": [battle([PID, SPAWN_C], 1, PID, {PID: STR_K, SPAWN_C: STR_K}, rows)], "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5, "shield_current": 25.0}}
    rpt = report([t1], spawned=[SP])
    snap = snapshot(1, spawns=[SP], player_ship={"torpedoes": 3, "shield_current": 25.0, "in_battle": "b-001", "target": SPAWN_S})
    return case("torpedo_volley", ["report", "snapshot"], {"n": 3, "ammo_start": 6, "torpedo_damage": 25.0, "what": "fixture torpedo volley"},
                report=rpt, snapshot=snap, report2=copy.deepcopy(rpt))


def torpedo_rate(engine: float, hits_per_turn: int) -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": [15, 11]}
    turns = []
    shields = 100.0
    for t in range(10):
        rows = []
        for i in range(60):
            hit = i < hits_per_turn
            shields = max(0.0, shields - (0.1 if hit else 0.0))
            rows.append(ex(PSHIP, SPAWN_S, "torpedo", 3, hit, 0.1 if hit else 0.0, round(shields, 2)))
        turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, {PID: STR_K, SPAWN_C: 100.0}, rows)], "tag": f"rate{engine}"})
    p = CB.torpedo_hit_p(0.7, 0.03, engine)
    kw = {"p_expected": p, "bound": 0.45 if engine > 5 else 0.6, "at_most": engine > 5, "engine": engine, "shots_min": 300, "what": f"fixture hit rate engine {engine:g}"}
    return case("torpedo_rate", ["report"], kw, report=report(turns, spawned=[sp]))


def loot() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "corsair", "cell": list(CELL)}
    victim = {"ship": SPAWN_S, "captain": SPAWN_C, "gold": 3, "silver": 20, "items": [], "torpedoes": 12.0, "phasers": 5.0, "engine": 6.0, "warp": 5.0, "shield_max": 80.0}
    pf = {"ship": PSHIP, "captain": PID, "gold": 5, "silver": 50, "items": [], "torpedoes": 12.0, "phasers": 3.0, "engine": 4.0, "warp": 3.0, "shield_max": 360.0}
    strength = {PID: CB.strength(360, 12, 3, torpedo_damage=25, phaser_per_unit=5), SPAWN_C: CB.strength(80, 12, 5, torpedo_damage=25, phaser_per_unit=5)}
    r1 = [ex(PSHIP, SPAWN_S, "torpedo", 0, True, 25, 55), ex(PSHIP, SPAWN_S, "torpedo", 0, False, 0, 55), ex(PSHIP, SPAWN_S, "torpedo", 0, True, 25, 30),
          ex(PSHIP, SPAWN_S, "torpedo", 0, False, 0, 30), ex(SPAWN_S, PSHIP, "torpedo", 0, True, 25, 335), ex(PSHIP, SPAWN_S, "phaser", 0, True, 15, 15),
          ex(SPAWN_S, PSHIP, "phaser", 0, True, 25, 310)]
    r2 = [ex(PSHIP, SPAWN_S, "torpedo", 0, True, 25, 0), ex(PSHIP, SPAWN_S, "torpedo", 0, True, 25, 0), ex(SPAWN_S, PSHIP, "torpedo", 0, False, 0, 310),
          ex(SPAWN_S, PSHIP, "phaser", 0, True, 25, 285)]
    t1 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, r1)], "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5, "shield_current": 310.0, "shield_max": 360.0}}
    t2 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, r2, ended="destroyed", victor=PID, transfer={"gold": 3, "silver": 20, "items": {}, "pct": 100})],
          "destroyed": [{"ship": SPAWN_S, "captain": SPAWN_C, "cause": "combat", "by": PSHIP, "cell": list(CELL)}], "captains": 193,
          "reputation": [rep(PID, "FR-01", 8, 0, 1.0)], "player": {"respect": 5, "fear": 18, "gold": 8, "silver": 70, "shield_current": 285.0, "shield_max": 360.0, "in_battle": False},
          "events": ["Lantern destroyed Cinder Lark", "Loot from Cinder Lark: 3 gold, 20 silver, 10 torpedoes"]}
    rpt = report([t1, t2] + [{"player": {"respect": 5, "fear": 18, "gold": 8, "silver": 70, "shield_current": 285.0 + 5 * i, "shield_max": 360.0}, "captains": 193} for i in range(1, 7)],
                 spawned=[sp])
    snap = snapshot(8, player_ship={"torpedoes": 16, "phasers": 5.0, "engine": 6.0, "warp": 5.0, "shield_max": 360.0, "shield_current": 315.0}, captain={"gold": 8, "silver": 70, "fear": 18, "respect": 5})
    kw = {"victim": victim, "player_before": pf, "caps": {"phasers": 10.0, "engine": 10.0, "warp": 10.0}, "torpedo_damage": 25.0, "what": "fixture loot"}
    return case("destruction", ["report", "snapshot"], kw, report=rpt, snapshot=snap)


def mutual() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "kestrel", "cell": list(CELL)}
    strength = {PID: CB.strength(1, 0, 3, torpedo_damage=25, phaser_per_unit=5), SPAWN_C: CB.strength(1, 0, 3, torpedo_damage=25, phaser_per_unit=5)}
    t1 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 0, True, 15, 0), ex(SPAWN_S, PSHIP, "phaser", 0, True, 15, 0)])],
          "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5, "shield_current": 0.0, "shield_max": 1.0}}
    t2 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 0, True, 15, 0), ex(SPAWN_S, PSHIP, "phaser", 0, True, 15, 0)], ended="destroyed")],
          "destroyed": [{"ship": PSHIP, "captain": PID, "cause": "combat", "by": SPAWN_S, "cell": list(CELL)}, {"ship": SPAWN_S, "captain": SPAWN_C, "cause": "combat", "by": PSHIP, "cell": list(CELL)}],
          "captains": 193, "player": {"respect": 5, "shield_current": 0.0, "shield_max": 1.0, "in_battle": False}, "events": ["Lantern destroyed — game over"]}
    rpt = report([t1, t2], requested=3, outcome="game_over", spawned=[sp], final={"game_over_cause": "destroyed in combat by Cinder Lark"})
    return case("mutual_annihilation", ["report"], {"turn": 2, "what": "fixture mutual annihilation"}, report=rpt)


def peace(label: str) -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": [13, 11]}
    if label == "player_offers":
        mx, pct_zero = 100.0, False
    elif label == "victor_lower":
        mx, pct_zero = 50.0, True
    else:
        mx, pct_zero = 40.0, None
    ai_str = CB.strength(mx, 0, 1, torpedo_damage=25, phaser_per_unit=5)
    strength = {PID: STR_K, SPAWN_C: ai_str}
    sides = [PID, SPAWN_C]
    ai_hit = CB.phaser_damage(1, 5, 1)           # 3.33
    if label != "ai_offers":
        a1 = mx - 10
        p1 = 60 - ai_hit
        p2 = p1 - ai_hit
        t1 = {"battles": [battle(sides, 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 1, True, 10, a1), ex(SPAWN_S, PSHIP, "phaser", 1, True, ai_hit, p1)])],
              "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5, "shield_current": round(p1, 4)}}
        t2 = {"battles": [battle(sides, 1, PID, strength, [ex(SPAWN_S, PSHIP, "phaser", 1, True, ai_hit, p2)])], "player": {"respect": 5, "shield_current": round(p2, 4)},
              "events": ["script order 'peace offer c-900' accepted: peace offered to Cinder Lark"]}
        x = CB.peace_pct(a1, p2, 50)
        g, s = 5 * x // 100, 50 * x // 100
        t3 = {"battles": [battle(sides, 1, PID, strength, [], ended="peace", victor=SPAWN_C, transfer={"gold": g, "silver": s, "items": {}, "pct": x})],
              "player": {"respect": 5, "shield_current": round(p2, 4), "gold": 5 - g, "silver": 50 - s, "in_battle": False}, "events": ["Cinder Lark accepts peace"]}
        turns = [t1, t2, t3, {"player": {"respect": 5, "shield_current": round(min(60.0, p2 + 5), 4), "gold": 5 - g, "silver": 50 - s}}]
        offerer = "player"
    else:
        turns = []
        a = mx
        p = 60.0
        for t in range(1, 4):
            a -= 10
            p -= ai_hit
            rows = [ex(PSHIP, SPAWN_S, "phaser", 1, True, 10, a), ex(SPAWN_S, PSHIP, "phaser", 1, True, ai_hit, p)]
            turns.append({"battles": [battle(sides, 1, PID, strength, rows)], "player": {"respect": 5, "shield_current": round(p, 4)},
                          "reputation": [rep(PID, "FR-06", 0, -5)] if t == 1 else []})
        p -= ai_hit
        turns.append({"battles": [battle(sides, 1, PID, strength, [ex(SPAWN_S, PSHIP, "phaser", 1, True, ai_hit, p)])], "player": {"respect": 5, "shield_current": round(p, 4)},
                      "events": ["Cinder Lark offers peace", "script order 'peace accept c-900' rejected: no peace offer from c-900 stands yet (CMD-10)"]})
        x = CB.peace_pct(p, a, 50)
        g, s = 3 * x // 100, 20 * x // 100
        turns.append({"battles": [battle(sides, 1, PID, strength, [], ended="peace", victor=PID, transfer={"gold": g, "silver": s, "items": {}, "pct": x})],
                      "player": {"respect": 5, "shield_current": round(p, 4), "gold": 5 + g, "silver": 50 + s, "in_battle": False},
                      "events": ["script order 'peace accept c-900' accepted", "Peace with Cinder Lark: 39 % of its coins transferred"]})
        turns.append({"player": {"respect": 5, "shield_current": round(p + 5, 4), "gold": 5 + g, "silver": 50 + s}})
        offerer = "ai"
    rpt = report(turns, spawned=[sp])
    kw = {"offerer": offerer, "spawn_shield_max": mx, "loot_max": 50.0, "recharge": 5.0, "player_coins": [5, 50], "ai_coins": [3, 20], "pct_zero": pct_zero, "what": f"fixture peace {label}"}
    return case("peace", ["report", "snapshot"], kw, report=rpt, snapshot=snapshot(len(turns), spawns=[sp]))


def mutual_peace() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": [13, 11]}
    strength = {PID: STR_K, SPAWN_C: 55.0}
    t1 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 1, True, 10, 30)])], "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5}}
    t2 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, [], ended="peace", victor=None, transfer={"gold": 0, "silver": 0, "items": {}, "pct": 0})],
          "player": {"respect": 5, "in_battle": False}, "events": ["Both offered peace: no victor, nothing transfers"]}
    return case("mutual_peace", ["report"], {"what": "fixture mutual peace"}, report=report([t1, t2], spawned=[sp]))


def escape(ended: bool) -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": [14, 11]}
    strength = {PID: STR_K, SPAWN_C: 75.0}
    t1 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 2, True, 5, 55), ex(SPAWN_S, PSHIP, "phaser", 2, True, 1.67, 58.33)])],
          "reputation": [rep(PID, "FR-06", 0, -5)], "player": {"respect": 5, "shield_current": 58.33}}
    if ended:
        t2 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, [], ended="escape")], "player": {"respect": 5, "position": [HOME[0], HOME[1], [8, 11]], "shield_current": 58.33, "in_battle": False},
              "events": ["script order 'escape -4 +0' accepted", "Lantern escaped: no enemy in range (CB-02)"]}
        t3 = {"player": {"respect": 5, "position": [HOME[0], HOME[1], [8, 11]], "shield_current": 60.0}}
        kw = {"escape_turn": 2, "expect_ended": True, "end_distance": 6, "max_range": 5, "what": "fixture escape out"}
    else:
        t2 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, [])], "player": {"respect": 5, "position": [HOME[0], HOME[1], [11, 11]], "shield_current": 58.33},
              "events": ["script order 'escape -1 +0' accepted", "Lantern moved; Cinder Lark still within torpedo range 5"]}
        t3 = {"battles": [battle([PID, SPAWN_C], 1, PID, strength, [])], "player": {"respect": 5, "position": [HOME[0], HOME[1], [11, 11]], "shield_current": 58.33}}
        kw = {"escape_turn": 2, "expect_ended": False, "end_distance": 3, "max_range": 5, "what": "fixture escape in"}
    return case("escape", ["report", "snapshot"], kw, report=report([t1, t2, t3], spawned=[sp]), snapshot=snapshot(3, spawns=[sp]))


def rep_win(stronger: bool) -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": list(CELL)}
    if stronger:
        per_unit, respect0 = 100.0, 10
        sp_player = CB.strength(60, 6, 3, torpedo_damage=25, phaser_per_unit=per_unit)       # 1110
        shields = math.ceil(sp_player * 2.0 + 1)                                               # 2221
        ratio = shields / sp_player
        scale = CB.clamp_scale(ratio, 0.5, 2.0)
        rows = {"FR-01": [int(round(8 * scale)), 0], "FR-03": [0, int(round(10 * scale))]}
        dmg = 300.0
        require_offer = False
    else:
        per_unit, respect0 = 5.0, 50
        sp_player = STR_K
        shields = sp_player * 0.5                                                                # 127.5
        ratio = shields / sp_player
        scale = CB.clamp_scale(ratio, 0.5, 2.0)
        rows = {"FR-01": [int(round(8 * scale)), 0], "FR-04": [0, -int(round(8 * scale))], "FR-05": [0, -int(round(8 * scale))]}
        dmg = 15.0
        require_offer = True
    strength = {PID: sp_player, SPAWN_C: float(shields)}
    turns = []
    s = float(shields)
    t = 0
    offered = False
    while True:
        t += 1
        if s <= 0:
            exch = [ex(PSHIP, SPAWN_S, "phaser", 0, True, dmg, 0)]
            fear_t = 10 + rows["FR-01"][0]
            resp_t = respect0 - 5 + sum(r for _, r in rows.values())
            turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, strength, exch, ended="destroyed", victor=PID, transfer={"gold": 3, "silver": 20, "items": {}, "pct": 100})],
                          "destroyed": [{"ship": SPAWN_S, "captain": SPAWN_C, "cause": "combat", "by": PSHIP, "cell": list(CELL)}], "captains": 193,
                          "reputation": [rep(PID, rule, f, r, round(scale, 2)) for rule, (f, r) in rows.items()],
                          "player": {"fear": fear_t, "respect": resp_t, "gold": 8, "silver": 70, "in_battle": False}})
            break
        exch = []
        if t == 1:
            for i in range(6):
                hit = i % 2 == 0
                s = max(0.0, s - (25 if hit else 0))
                exch.append(ex(PSHIP, SPAWN_S, "torpedo", 0, hit, 25 if hit else 0, s))
        s = max(0.0, s - dmg)
        exch.append(ex(PSHIP, SPAWN_S, "phaser", 0, True, dmg, s))
        events = []
        if not stronger and not offered and s < 0.33 * shields:
            events.append("Cinder Lark offers peace")
            offered = True
        turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, strength, exch)], "reputation": [rep(PID, "FR-06", 0, -5)] if t == 1 else [],
                      "player": {"respect": respect0 - 5}, "events": events or [f"Turn {t} resolved"]})
    kw = {"expect_rows": rows, "scale": round(scale, 2), "ratio": ratio, "strength_player": sp_player, "strength_ai": float(shields), "fear_start": 10, "respect_start": respect0,
          "start_fight_delta": 5, "require_offer": require_offer, "what": f"fixture reputation {'stronger' if stronger else 'weaker'}"}
    return case("reputation_win", ["report", "snapshot"], kw, report=report(turns, spawned=[sp]), snapshot=snapshot(len(turns)))


HOLE_SECTORS = [[[3, 4], [8, 9]], [[3, 4], [9, 9]], [[3, 4], [10, 9]], [[3, 4], [7, 10]]]


def ai60(roams: int) -> dict:
    turns = []
    for t in range(1, 61):
        warps = []
        if roams and t % 10 == 0:
            warps.append({"ship": f"s-{t:03d}", "from": [[3, 4], [7, 9]], "to": [[3, 4], [8, 10]], "factor": 5.0, "effective": 5.0})
        fear = min(100, 10 + t // 7)
        turns.append({"warps": warps, "player": {"fear": fear, "respect": max(0, 10 - t // 20)}, "tag": f"ai{roams}"})
    return report(turns)


def roaming() -> dict:
    from make_fixtures import M2_INDEX  # noqa: E402
    return case("roaming", ["report", "report2", "index"], {"player_ship": PSHIP, "every": 10, "what": "fixture roaming"}, report=ai60(6), report2=ai60(0), index=M2_INDEX)


def bounds() -> dict:
    return case("reputation_bounds", ["report"], {"what": "fixture bounds"}, report=ai60(6))


def quiet() -> dict:
    return case("quiet", ["report"], {"fear_start": 10, "respect_start": 10, "what": "fixture quiet"}, report=report([{"player": {"food": 59 - i, "water": 59 - i}} for i in range(50)]))


def ai_opens() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "corsair", "cell": [13, 11]}
    str_c = CB.strength(80, 12, 5, torpedo_damage=25, phaser_per_unit=5)
    strength = {PID: STR_K, SPAWN_C: str_c}
    t1 = {"battles": [battle([PID, SPAWN_C], 1, SPAWN_C, strength, [ex(SPAWN_S, PSHIP, "torpedo", 1, True, 25, 35), ex(SPAWN_S, PSHIP, "phaser", 1, True, 16.67, 18.33)])],
          "reputation": [rep(SPAWN_C, "FR-06", 0, -5)], "player": {"shield_current": 18.33}, "events": ["Cinder Lark opens fire on Lantern (FR-06)"]}
    t2 = {"battles": [battle([PID, SPAWN_C], 1, SPAWN_C, strength, [ex(SPAWN_S, PSHIP, "phaser", 1, True, 16.67, 1.66)])], "player": {"shield_current": 1.66}}
    kw = {"within": 3, "ratio": str_c / STR_K, "ratio_min": 1.3, "player_fear": 10, "fear_max": 50, "start_fight_delta": 5, "what": "fixture AI opens"}
    return case("ai_opens", ["report", "snapshot"], kw, report=report([t1, t2], requested=4, spawned=[sp]), snapshot=snapshot(2, spawns=[sp]))


def ai_holds_off() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "corsair", "cell": [13, 11]}
    rpt = report([{"player": {"fear": 60}} for _ in range(10)], spawned=[sp])
    return case("no_player_battle", ["report", "snapshot"], {"turns": 10, "player_fear": 60, "fear_max": 50, "what": "fixture AI holds off"}, report=rpt,
                snapshot=snapshot(10, spawns=[sp], captain={"fear": 60}))


def ai_peace_escape() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": list(CELL)}
    strength = {PID: STR_K, SPAWN_C: 60.0}
    turns = []
    s = 60.0
    for t in range(1, 5):
        s -= 15
        events = ["Cinder Lark offers peace"] if t == 4 else [f"Turn {t} resolved"]
        turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 0, True, 15, s)])], "events": events,
                      "reputation": [rep(PID, "FR-06", 0, -5)] if t == 1 else [], "player": {"respect": 5}})
    turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, strength, [])], "events": ["script order 'fire phaser s-900' rejected: target s-900 is 4 cells away; phaser range 2 (CM-02)",
                                                                                      "Cinder Lark escapes at full power"], "player": {"respect": 5}})
    turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, strength, [], ended="escape")], "events": ["Battle with Cinder Lark ended: nobody in range (escape)"],
                  "player": {"respect": 5, "in_battle": False}})
    turns += [{"player": {"respect": 5}} for _ in range(2)]
    kw = {"shield_max": 60.0, "threshold_pct": 33.0, "escape_pct": 15.0, "spawn_cell": list(CELL), "engine": 4.0, "what": "fixture AI peace then escape"}
    snap = snapshot(8, spawns=[dict(sp, cell=[20, 11], shield_current=0.0)])
    return case("ai_peace_then_escape", ["report", "snapshot"], kw, report=report(turns, spawned=[sp]), snapshot=snap)


def ai_surrender() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": list(CELL)}
    strength = {PID: STR_K, SPAWN_C: 90.0}
    turns = []
    s = 90.0
    for t in range(1, 6):
        s -= 15
        turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 0, True, 15, s)])], "player": {"fear": 85, "respect": 5},
                      "reputation": [rep(PID, "FR-06", 0, -5)] if t == 1 else [], "events": ["Cinder Lark offers peace"] if t == 3 else [f"Turn {t} resolved"]})
    turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, strength, [], ended="peace", victor=PID, transfer=None)], "captures": [{"ship": SPAWN_S, "from": SPAWN_C, "to": PID}],
                  "captains": 193, "player": {"fear": 85, "respect": 5, "ships": 2, "in_battle": False}, "events": ["Cinder Lark surrenders: captured, joins your fleet (FL-02)"]})
    turns += [{"player": {"fear": 85, "respect": 5, "ships": 2}, "captains": 193} for _ in range(2)]
    snap = snapshot(8, player_ships=[PSHIP, SPAWN_S], captain={"fear": 85, "respect": 5},
                    extra_ships={SPAWN_S: {"name": SPAWN_NAME, "class": "bastion", "captain": PID, "torpedoes": 0, "phasers": 0.0, "shield_max": 90.0, "shield_current": 25.0,
                                           "engine": 4.0, "warp": 4.0, "position": [HOME[0], HOME[1], list(CELL)], "orders_queued": 0, "orders": [], "hazard": None,
                                           "move_remaining": None, "in_battle": None, "target": None}})
    kw = {"shield_max": 90.0, "surrender_pct": 20.0, "player_fear": 85, "yield_fear_min": 80, "what": "fixture surrender"}
    return case("capture", ["report", "snapshot"], kw, report=report(turns, spawned=[sp]), snapshot=snap)


def fleet() -> dict:
    spb = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": list(CELL)}
    spc = {"ship": SPAWN2_S, "captain": SPAWN2_C, "class": "corsair", "cell": [14, 11]}
    strength = {PID: STR_K, SPAWN_C: 90.0}
    turns = []
    s = 90.0
    for t in range(1, 6):
        s -= 15
        turns.append({"battles": [battle([PID, SPAWN_C], 1, PID, strength, [ex(PSHIP, SPAWN_S, "phaser", 0, True, 15, s)])], "player": {"fear": 85, "respect": 5}})
    turns.append({"captures": [{"ship": SPAWN_S, "from": SPAWN_C, "to": PID}], "captains": 194, "player": {"fear": 85, "respect": 5, "ships": 2}})
    s = 30.0
    for t in range(7, 12):
        s -= 5
        turns.append({"battles": [battle([PID, SPAWN2_C], 7, PID, {PID: STR_K, SPAWN2_C: 30.0}, [ex(PSHIP, SPAWN2_S, "phaser", 2, True, 5, s)], bid="b-002")],
                      "player": {"fear": 85, "respect": 0, "ships": 2}, "captains": 194})
    turns.append({"captures": [{"ship": SPAWN2_S, "from": SPAWN2_C, "to": PID}], "captains": 193, "player": {"fear": 85, "respect": 0, "ships": 3}})
    turns.append({"player": {"fear": 85, "respect": 0, "ships": 3}, "captains": 193, "events": ["script order 'ship s-900' accepted: active ship is now Cinder Lark"]})
    turns.append({"player": {"fear": 85, "respect": 0, "ships": 3, "position": [HOME[0], HOME[1], [12, 12]]}, "captains": 193})
    turns.append({"player": {"fear": 85, "respect": 0, "ships": 3, "position": [HOME[0], HOME[1], [12, 16]], "move_remaining": [0, 2]}, "captains": 193})
    def owned(sid: str, name: str, cls: str, cell: list, orders: list) -> dict:
        c = FIXTURE_M3["classes"][cls]
        return {"name": name, "class": cls, "captain": PID, "torpedoes": 0, "phasers": 0.0, "shield_max": c["shield_max"], "shield_current": 20.0, "engine": 4.0, "warp": c["warp"],
                "position": [HOME[0], HOME[1], list(cell)], "orders_queued": len(orders), "orders": orders, "hazard": None, "move_remaining": [0, 2] if orders else None,
                "in_battle": None, "target": None}
    snap = snapshot(len(turns), player_cell=[12, 16], player_ships=[PSHIP, SPAWN_S, SPAWN2_S], active=SPAWN_S, captain={"fear": 85, "respect": 0},
                    extra_ships={SPAWN_S: owned(SPAWN_S, SPAWN_NAME, "bastion", [12, 16], [{"command": "move", "args": ["+0", "+2"], "persisting": True}]),
                                 SPAWN2_S: owned(SPAWN2_S, "Tallow Wren", "corsair", [14, 11], [])})
    kw = {"spawns": [{"ship": SPAWN_S, "captain": SPAWN_C}, {"ship": SPAWN2_S, "captain": SPAWN2_C}], "player_cell": list(CELL), "cells": {"b": list(CELL), "c": [14, 11]},
          "moves": [[0, 1], [0, 4]], "persisting_remaining": [0, 2], "what": "fixture fleet"}
    return case("fleet", ["report", "snapshot"], kw, report=report(turns, spawned=[spb, spc]), snapshot=snap)


def combat_death() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "bastion", "cell": list(CELL)}
    str_b = CB.strength(100, 20, 8, torpedo_damage=25, phaser_per_unit=5)
    strength = {PID: STR_K, SPAWN_C: str_b}
    t1 = {"battles": [battle([PID, SPAWN_C], 1, SPAWN_C, strength, [ex(SPAWN_S, PSHIP, "torpedo", 0, False, 0, 60), ex(SPAWN_S, PSHIP, "phaser", 0, True, 40, 20)])],
          "reputation": [rep(SPAWN_C, "FR-06", 0, -5)], "player": {"shield_current": 20.0}}
    t2 = {"battles": [battle([PID, SPAWN_C], 1, SPAWN_C, strength, [ex(SPAWN_S, PSHIP, "torpedo", 0, True, 25, 0), ex(SPAWN_S, PSHIP, "phaser", 0, True, 40, 0)])], "player": {"shield_current": 0.0}}
    t3 = {"battles": [battle([PID, SPAWN_C], 1, SPAWN_C, strength, [ex(SPAWN_S, PSHIP, "phaser", 0, True, 40, 0)], ended="destroyed", victor=SPAWN_C, transfer={"gold": 5, "silver": 50, "items": {}, "pct": 100})],
          "destroyed": [{"ship": PSHIP, "captain": PID, "cause": "combat", "by": SPAWN_S, "cell": list(CELL)}], "player": {"shield_current": 0.0, "in_battle": False},
          "events": ["Lantern destroyed by Cinder Lark — game over"]}
    rpt = report([t1, t2, t3], requested=12, outcome="game_over", spawned=[sp], final={"game_over_cause": "destroyed in combat by Cinder Lark in sector 3,4/7,9"})
    return case("combat_game_over", ["report", "save_present"], {"max_turn": 12, "what": "fixture combat death"}, report=rpt, save_present=False)


def scan() -> dict:
    sp = {"ship": SPAWN_S, "captain": SPAWN_C, "class": "kestrel", "cell": [15, 11], "fear": 37, "respect": 43}
    t1 = {"events": ["script order 'scan' accepted: captains in this sector: Cinder Lark (Oriel Vance) fear 37 respect 43 at 15,11; items here: none"]}
    rpt = report([t1], spawned=[sp])
    rpt2 = report([{}], spawned=[sp])
    return case("scan", ["report", "snapshot", "report2", "snapshot2"], {"fear": 37, "respect": 43, "what": "fixture scan"}, report=rpt,
                snapshot=snapshot(1, spawns=[sp], visible=True), report2=rpt2, snapshot2=snapshot(1, spawns=[sp], visible=False))


WORM = {"galaxy": [3, 4], "sector": [9, 9], "cell": [7, 7]}
PARTNER = {"galaxy": [3, 4], "sector": [10, 9], "cell": [8, 8]}


def wormhole(cls: str) -> dict:
    hz = lambda d, tl: {"kind": "black_hole", "cell": PARTNER["cell"], "distance": d, "pull": round(20 / (1 + d) ** 1.5, 3), "turns_left": tl, "drain": 0}  # noqa: E731
    exit_cell = [PARTNER["cell"][0] + 2, PARTNER["cell"][1]]
    t1 = {"player": {"position": [PARTNER["galaxy"], PARTNER["sector"], exit_cell], "hazard": hz(2, 1)}, "events": ["Lantern pulled into the wormhole — emerges 2 cells from its partner"]}
    if cls == "kestrel":
        t2 = {"player": {"position": [PARTNER["galaxy"], PARTNER["sector"], exit_cell], "hazard": hz(2, 0)}, "events": ["script order 'move +1 +0' accepted", "Lantern is held by gravity (pull 3.85 against engine 4)"]}
        back = [WORM["cell"][0] + 2, WORM["cell"][1]]
        t3 = {"player": {"position": [WORM["galaxy"], WORM["sector"], back], "hazard": {"kind": "black_hole", "cell": WORM["cell"], "distance": 2, "pull": 3.849, "turns_left": 1, "drain": 0}},
              "events": ["Lantern pulled into the wormhole — emerges 2 cells from its partner"]}
        t4 = {"player": {"position": [WORM["galaxy"], WORM["sector"], back], "hazard": {"kind": "black_hole", "cell": WORM["cell"], "distance": 2, "pull": 3.849, "turns_left": 0, "drain": 0}}}
        kw = {"partner": PARTNER, "origin": WORM, "exit_distance": 2, "cls": "kestrel", "held": True, "away_cells": [4, 8], "what": "fixture wormhole exit kestrel"}
    else:
        t2 = {"player": {"position": [PARTNER["galaxy"], PARTNER["sector"], [PARTNER["cell"][0] + 4, PARTNER["cell"][1]]], "hazard": hz(4, 2), "ship_class": "corsair"}}
        t3 = {"player": {"position": [PARTNER["galaxy"], PARTNER["sector"], [PARTNER["cell"][0] + 8, PARTNER["cell"][1]]], "hazard": hz(8, 7), "ship_class": "corsair"}}
        t4 = {"player": {"position": [PARTNER["galaxy"], PARTNER["sector"], [PARTNER["cell"][0] + 8, PARTNER["cell"][1]]], "hazard": hz(8, 6), "ship_class": "corsair"}}
        kw = {"partner": PARTNER, "origin": WORM, "exit_distance": 2, "cls": "corsair", "held": False, "away_cells": [4, 8], "what": "fixture wormhole exit corsair"}
    return case("wormhole_exit", ["report"], kw, report=report([t1, t2, t3, t4]))


PLAIN = {"galaxy": [3, 4], "sector": [8, 9], "cell": [6, 6]}


def mutiny_hole() -> dict:
    """D-129: held 2 from a mass-20 hole (pull 3.849 ≥ engine 4 − 1); the new Kestrel appears at distance 3 (pull 2.5,
    floor(4 − 2.5) = 1 — it can move) with its own countdown (scale 27 → ceil(27 / 2.5) = 11); hazard stays non-null (HZ-04)."""
    hz = {"kind": "black_hole", "cell": PLAIN["cell"], "distance": 2, "pull": 3.849, "turns_left": 7, "drain": 0}
    held, placed = [8, 6], [9, 6]
    after = {"kind": "black_hole", "cell": PLAIN["cell"], "distance": 3, "pull": 2.5, "turns_left": 11, "drain": 0}
    turns = [{"player": {"position": [PLAIN["galaxy"], PLAIN["sector"], held], "hazard": dict(hz, turns_left=7 - t), "food": max(0, 1 - t), "water": max(0, 1 - t)}} for t in range(1, 4)]
    turns.append({"player": {"position": [PLAIN["galaxy"], PLAIN["sector"], placed], "hazard": dict(after), "food": 10, "water": 10, "fear": 0}, "captains": 194,
                  "events": ["Mutiny: the crew seize the Lantern; you are demoted to a Kestrel placed at the nearest cell where it can move (D-129)"]})
    turns.append({"player": {"position": [PLAIN["galaxy"], PLAIN["sector"], placed], "hazard": dict(after, turns_left=10), "food": 9, "water": 9, "fear": 0}, "captains": 194})
    rpt = report(turns, final={"mutinies": [{"turn_no": 4, "captain": "player", "result": "demoted"}]})
    return case("mutiny_placement", ["report", "snapshot"], {"mutiny_turn": 4, "hazard": PLAIN, "kind": "black_hole", "engine": 4.0, "what": "fixture mutiny placement (hole)"},
                report=rpt, snapshot=snapshot(5, player_cell=placed))


STAR = {"galaxy": [3, 4], "sector": [7, 9], "cell": [5, 5]}


def mutiny_star() -> dict:
    """D-115 at a star: held in the star's own cell (pull 1.0, drain 10); the new Kestrel appears in a neighbouring cell,
    outside the star's range (its own cell, HZ-01) — hazard null."""
    hz = {"kind": "star", "cell": STAR["cell"], "distance": 0, "pull": 1.0, "turns_left": 7, "drain": 10}
    placed = [STAR["cell"][0] + 1, STAR["cell"][1]]
    turns = [{"player": {"position": [STAR["galaxy"], STAR["sector"], list(STAR["cell"])], "hazard": dict(hz, turns_left=7 - t), "shield_current": 60.0 - 10 * t,
                         "food": max(0, 1 - t), "water": max(0, 1 - t)}} for t in range(1, 4)]
    turns.append({"player": {"position": [STAR["galaxy"], STAR["sector"], placed], "hazard": None, "shield_current": 60.0, "food": 10, "water": 10, "fear": 0}, "captains": 194,
                  "events": ["Mutiny: the crew seize the Lantern; you are demoted to a Kestrel placed outside every pull range (D-115)"]})
    turns.append({"player": {"position": [STAR["galaxy"], STAR["sector"], placed], "hazard": None, "shield_current": 60.0, "food": 9, "water": 9, "fear": 0}, "captains": 194})
    rpt = report(turns, final={"mutinies": [{"turn_no": 4, "captain": "player", "result": "demoted"}]})
    return case("mutiny_placement", ["report", "snapshot"], {"mutiny_turn": 4, "hazard": STAR, "kind": "star", "engine": 4.0, "what": "fixture mutiny placement (star)"},
                report=rpt, snapshot=snapshot(5, player_cell=placed))


def planet_index(spread: bool = True, bad: bool = False) -> dict:
    from make_fixtures import M2_INDEX  # noqa: E402
    idx = copy.deepcopy(M2_INDEX)
    idx["planets"] = [{"galaxy": [3, 4], "sector": [7, 9], "cell": [6, 5], "name": "Oreth"},
                      {"galaxy": [3, 4], "sector": [7, 10], "cell": [22, 5] if spread else [21, 4], "name": "Calder"},
                      {"galaxy": [3, 4], "sector": [8, 10], "cell": [5, 20] if spread else [3, 22], "name": "Iseult"}]
    if bad:
        idx["planets"].append({"galaxy": [3, 4], "sector": [7, 9], "cell": [5, 5], "name": "Ash"})
    return idx


def planets() -> dict:
    return case("planet_bands", ["indexes"], {"lo": 1, "hi": 3, "what": "fixture planet bands"}, indexes={"1234": planet_index(), "7": planet_index()})


def long_run() -> dict:
    turns = []
    for t in range(1, 41):
        b = [battle(["c-010", "c-011"], t, "c-010", {"c-010": 255.0, "c-011": 455.0}, [ex("s-010", "s-011", "phaser", 1, True, 10, 70)], bid=f"b-{t:03d}", ended="escape")] if t % 10 == 0 else []
        turns.append({"battles": b, "tag": "long"})
    rpt = report(turns)
    snap = snapshot(40, spawns=[{"ship": "s-010", "captain": "c-010"}, {"ship": "s-011", "captain": "c-011"}])
    return {"report": rpt, "snapshot": snap}


def good_m3() -> dict:
    out = {"opening": opening(), "no_fire": no_fire(), "phaser_d0": phaser(0), "phaser_d1": phaser(1), "phaser_d2": phaser(2), "phaser_rejected": phaser_rejected(),
           "torpedo_volley": torpedo_volley(), "rate_engine10": torpedo_rate(10.0, 24), "rate_engine0": torpedo_rate(0.0, 42), "loot": loot(), "mutual": mutual(),
           "peace_player_offers": peace("player_offers"), "peace_victor_lower": peace("victor_lower"), "peace_ai_offers": peace("ai_offers"), "mutual_peace": mutual_peace(),
           "escape_out": escape(True), "escape_in": escape(False), "rep_stronger": rep_win(True), "rep_weaker": rep_win(False), "bounds": bounds(), "quiet": quiet(),
           "ai_opens": ai_opens(), "ai_holds_off": ai_holds_off(), "ai_peace_escape": ai_peace_escape(), "ai_surrender": ai_surrender(), "fleet": fleet(),
           "combat_death": combat_death(), "scan": scan(), "roaming": roaming(), "wormhole_kestrel": wormhole("kestrel"), "wormhole_corsair": wormhole("corsair"),
           "mutiny_hole": mutiny_hole(), "mutiny_star": mutiny_star(), "planets": planets()}
    return out


# --------------------------------------------------------------------------- the broken counterparts (one per row)

def broken_m3(good: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}

    c = copy.deepcopy(good["opening"])                 # smoke/23 — fire accepted but no battle ever opens, no FR-06
    for t in c["report"]["per_turn"]:
        t["battles"], t["reputation"] = [], []
        t["player"]["in_battle"], t["player"]["respect"] = False, 10
    c["report"]["final"].update(battles=0, battles_by_outcome={"destroyed": 0, "peace": 0, "escape": 0, "open": 0})
    out["sim_fight_never_opens"] = c

    c = copy.deepcopy(good["phaser_d2"])               # smoke/24 — the same damage at every distance (no 1 − d/3)
    for b in c["report"]["per_turn"][0]["battles"]:
        for e in b["exchanges"]:
            e["damage"], e["shields_after"] = 15.0, 45.0
    out["sim_phaser_flat"] = c

    c = copy.deepcopy(good["rate_engine10"])           # smoke/25 — every torpedo hits whatever the target's engine
    for t in c["report"]["per_turn"]:
        for b in t["battles"]:
            for e in b["exchanges"]:
                e["hit"], e["damage"] = True, 0.1
    out["sim_torpedo_always_hits"] = c

    c = copy.deepcopy(good["loot"])                    # smoke/26 — the kill lands but nothing transfers
    for t in c["report"]["per_turn"]:
        for b in t["battles"]:
            if b["ended"] == "destroyed":
                b["transfer"] = None
        t["player"]["gold"], t["player"]["silver"] = 5, 50
    sh = c["snapshot"]["ships"][PSHIP]
    sh.update(torpedoes=6, phasers=3.0, engine=4.0, warp=3.0)
    c["snapshot"]["captains"][PID].update(gold=5, silver=50)
    out["sim_loot_missing"] = c

    c = copy.deepcopy(good["peace_player_offers"])     # smoke/27 — the offerer is made the victor and keeps its coins
    for t in c["report"]["per_turn"]:
        for b in t["battles"]:
            if b["ended"] == "peace":
                b["victor"] = PID
                b["transfer"] = {"gold": 0, "silver": 0, "items": {}, "pct": 0}
        t["player"]["gold"], t["player"]["silver"] = 5, 50
    out["sim_peace_wrong_victor"] = c

    c = copy.deepcopy(good["escape_out"])              # smoke/28 — the ship moved out of range but the battle stays open
    for t in c["report"]["per_turn"][1:]:
        t["battles"] = [battle([PID, SPAWN_C], 1, PID, {PID: STR_K, SPAWN_C: 75.0}, [])]
        t["player"]["in_battle"] = True
        t["events"] = [e for e in t["events"] if "escaped" not in e]
    c["report"]["final"]["battles_by_outcome"] = {"destroyed": 0, "peace": 0, "escape": 0, "open": 1}
    out["sim_escape_ignored"] = c

    c = copy.deepcopy(good["rep_stronger"])            # smoke/29 — deltas applied without the LV-03 scale
    for t in c["report"]["per_turn"]:
        for r in t["reputation"]:
            if r["rule"] == "FR-01":
                r.update(fear=8, scale=1.0)
            if r["rule"] == "FR-03":
                r.update(respect=10, scale=1.0)
        if t["destroyed"]:
            t["player"].update(fear=18, respect=15)
    out["sim_rep_unscaled"] = c

    c = copy.deepcopy(good["ai_opens"])                # smoke/30 — the Corsair never opens on a low-fear Kestrel
    for t in c["report"]["per_turn"]:
        t["battles"], t["reputation"], t["events"] = [], [], ["Turn resolved"]
        t["player"].update(in_battle=False, shield_current=60.0)
    c["report"]["final"].update(battles=0, battles_by_outcome={"destroyed": 0, "peace": 0, "escape": 0, "open": 0})
    out["sim_ai_never_attacks"] = c

    c = copy.deepcopy(good["fleet"])                   # smoke/31 — `ship <id>` ignored: the original ship stays active and moves alone
    c["snapshot"]["captains"][PID]["active_ship"] = PSHIP
    c["snapshot"]["ships"][SPAWN_S]["position"][2] = list(CELL)
    c["snapshot"]["ships"][SPAWN_S]["orders"], c["snapshot"]["ships"][SPAWN_S]["move_remaining"] = [], None
    c["snapshot"]["ships"][PSHIP]["orders"], c["snapshot"]["ships"][PSHIP]["move_remaining"] = [{"command": "move", "args": ["+0", "+2"], "persisting": True}], [0, 2]
    out["sim_capture_no_switch"] = c

    c = copy.deepcopy(good["combat_death"])            # smoke/32 — the player's ship is destroyed but the game goes on, save kept
    r = c["report"]
    last = r["per_turn"][-1]
    for b in last["battles"]:
        b["ended"] = None
    for t in range(4, 13):
        extra = copy.deepcopy(last)
        extra["turn_no"], extra["destroyed"], extra["battles"] = t, [], []
        r["per_turn"].append(extra)
    r.update(outcome="running", turns_completed=12)
    r["final"].update(game_over_turn=None, game_over_cause=None)
    c["save_present"] = True
    out["sim_combat_no_game_over"] = c

    c = copy.deepcopy(good["scan"])                    # smoke/33 — scan lists nobody and visible_reputation never flips
    c["report"]["per_turn"][0]["events"] = ["script order 'scan' accepted: items here: none"]
    c["snapshot"]["captains"][SPAWN_C]["visible_reputation"] = False
    out["sim_scan_hidden"] = c

    c = copy.deepcopy(good["roaming"])                 # smoke/34 — nobody roams with the default roam_every_turns
    for t in c["report"]["per_turn"]:
        t["warps"] = []
    c["report"]["final"].update(roams=0, warps=0)
    out["sim_no_roam"] = c

    c = copy.deepcopy(good["wormhole_kestrel"])        # smoke/36 — the ship emerges 1 cell from the partner (D-102's nearest free cell, superseded by D-114)
    t1 = c["report"]["per_turn"][0]["player"]
    t1["position"][2] = [PARTNER["cell"][0] + 1, PARTNER["cell"][1]]
    t1["hazard"]["distance"] = 1
    out["sim_wormhole_exit_1"] = c

    c = copy.deepcopy(good["mutiny_hole"])             # smoke/36 — the new Kestrel is moved but only to another held cell (distance 2, pull 3.849: floor(4 − 3.849) = 0)
    for t in c["report"]["per_turn"][3:]:
        t["player"]["position"][2] = [PLAIN["cell"][0], PLAIN["cell"][1] + 2]
        t["player"]["hazard"].update(distance=2, pull=3.849)
    c["snapshot"]["ships"][PSHIP]["position"][2] = [PLAIN["cell"][0], PLAIN["cell"][1] + 2]
    out["sim_mutiny_held"] = c

    c = copy.deepcopy(good["mutiny_hole"])             # smoke/36 — D-115 not applied: the new Kestrel appears in the cell the old one was held in
    for t in c["report"]["per_turn"][3:]:
        t["player"]["position"][2] = [8, 6]
        t["player"]["hazard"].update(distance=2, pull=3.849)
    c["snapshot"]["ships"][PSHIP]["position"][2] = [8, 6]
    out["sim_mutiny_same_cell"] = c

    c = copy.deepcopy(good["mutiny_star"])             # smoke/36 — the new Kestrel appears in the star's own cell, still inside its range
    for t in c["report"]["per_turn"][3:]:
        t["player"]["position"][2] = list(STAR["cell"])
        t["player"]["hazard"] = {"kind": "star", "cell": STAR["cell"], "distance": 0, "pull": 1.0, "turns_left": 7, "drain": 10}
    c["snapshot"]["ships"][PSHIP]["position"][2] = list(STAR["cell"])
    out["sim_mutiny_star_cell"] = c
    return out


def long_run_broken(good: dict) -> dict:
    r = copy.deepcopy(good["report"])
    for k in ("battles", "battles_by_outcome", "ai_vs_ai_battles", "combat_deaths", "captures", "roams", "player_fear", "player_respect"):
        r["final"].pop(k, None)
    return {"report": r, "snapshot": good["snapshot"]}
