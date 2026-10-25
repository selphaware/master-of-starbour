"""Generate the canned fixtures that --selftest feeds the checkers.

    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\fixtures\\make_fixtures.py

M1: sim_good.json passes every report checker; every sim_<broken>.json fails exactly one
check (its own) when run through the same evaluation the gate uses.

M2 (specs/M2.md § 3): sim_m2_scenarios.json holds one good C2+C9 report per plan the gate
builds (persistent move, star fatal, countdown reset, hole held/escape, neutron star,
wormhole, warps); sim_m2_warp_bus.json, sim_m2_ai.json and sim_m2_game_over.json hold good
bus observations / snapshots; and one broken fixture per new row — sim_move_clipped,
sim_star_not_fatal, sim_countdown_no_reset, sim_hole_escapable, sim_wormhole_kills,
sim_warp_reach_wrong, sim_ai_suicide, sim_game_over_accepts_orders — fails that row.  The
reports are synthesised *from the plans* (same builders as the gate, _checks.plan_*), so a
change to a plan propagates.  All values follow CONTRACTS C2/C3/C9 and the default
controls_config.ini numbers below.  No franchise names (D-03).
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import _checks as C  # noqa: E402
from _checks import canonical_hash  # noqa: E402

# The config the fixtures were generated against (the repo defaults on 2026-10-25).
FIXTURE_CFG = {
    "food_start": 30, "water_start": 30, "warn_turns": 5, "mutiny_turns_at_zero": 3,
    "mutiny_ship_class": "Kestrel", "mutiny_supplies": 10, "mutiny_fear_delta": 10,
    "fear_start": 10, "respect_start": 10, "gold_start": 5, "silver_start": 50,
}
FIXTURE_M2 = {
    "k_g": 1.0, "falloff": 1.5, "pull_turns_scale": 7.0, "drain": {"low": 5.0, "medium": 10.0, "high": 20.0},
    "shield_recharge": 5.0, "warp_base": 16.0, "warp_exp": 4.0, "blackhole_mass": 20.0, "neutron_mass": 8.0,
    "universe_size": (8, 8), "galaxy_size": (16, 16), "sector_size": (24, 24), "starting_class": "kestrel",
    "ship": {"torpedoes": 6.0, "phasers": 3.0, "shield_max": 60.0, "engine": 4.0, "warp": 3.0},
}
# A synthetic world_index (C9) around the fixture's start sector 3,4/7,9.
M2_INDEX = {
    "player_start": [[3, 4], [7, 9], [12, 11]],
    "stars": [{"galaxy": [3, 4], "sector": [7, 9], "cell": [5, 5], "bucket": "medium", "mass": 1.0, "colour": "yellow"},
              {"galaxy": [3, 4], "sector": [7, 10], "cell": [20, 3], "bucket": "medium", "mass": 0.83, "colour": "orange"},
              {"galaxy": [3, 4], "sector": [8, 10], "cell": [2, 21], "bucket": "high", "mass": 20.1, "colour": "blue-white"}],
    "black_holes": [{"galaxy": [3, 4], "sector": [8, 9], "cell": [6, 6], "wormhole_to": None},
                    {"galaxy": [3, 4], "sector": [9, 9], "cell": [7, 7], "wormhole_to": {"galaxy": [3, 4], "sector": [10, 9], "cell": [8, 8]}},
                    {"galaxy": [3, 4], "sector": [10, 9], "cell": [8, 8], "wormhole_to": {"galaxy": [3, 4], "sector": [9, 9], "cell": [7, 7]}}],
    "neutron_stars": [{"galaxy": [3, 4], "sector": [7, 10], "cell": [12, 12]}],
    "planets": [{"galaxy": [3, 4], "sector": [7, 9], "cell": [6, 5], "name": "Oreth"}],
}
TURNS = 40
MUTINY_TURN = FIXTURE_CFG["food_start"] + FIXTURE_CFG["mutiny_turns_at_zero"]   # 33 (D-76)
CAPTAINS = 193
CONFIG_PATH = "E:\\CODE\\master-of-starbour\\controls_config.ini"


def fake_hash(tag: str) -> str:
    return hashlib.sha1(tag.encode()).hexdigest()


def make_snapshot(turn_no: int, food: int, water: int, fear: int, cell: tuple[int, int]) -> dict:
    """A C3-shaped snapshot: player plus one AI captain in the same sector."""
    return {
        "turn_no": turn_no, "seed": 1234, "version": "0.1.0",
        "sizes": {"universe": [8, 8], "galaxy": [16, 16], "sector": [24, 24]},
        "player_id": "c-000",
        "captains": {
            "c-000": {"name": "Harrow Vey", "is_player": True, "gold": 5, "silver": 50, "fear": fear, "respect": 10,
                      "food": food, "water": water, "ships": ["s-194"], "active_ship": "s-194", "items": [],
                      "is_master": False, "mutiny_countdown": None},
            "c-001": {"name": "Ilse Marrow", "is_player": False, "gold": 3, "silver": 20, "fear": 10, "respect": 10,
                      "food": 30, "water": 30, "ships": ["s-001"], "active_ship": "s-001", "items": [],
                      "is_master": False, "mutiny_countdown": None},
        },
        "ships": {
            "s-194": {"name": "Lantern", "class": "kestrel", "captain": "c-000", "torpedoes": 6, "phasers": 3.0,
                      "shield_max": 60.0, "shield_current": 60.0, "engine": 4.0, "warp": 3.0,
                      "position": [[3, 4], [7, 9], [cell[0], cell[1]]], "orders_queued": 0},
            "s-001": {"name": "Grey Heron", "class": "corsair", "captain": "c-001", "torpedoes": 12, "phasers": 5.0,
                      "shield_max": 80.0, "shield_current": 80.0, "engine": 6.0, "warp": 5.0,
                      "position": [[3, 4], [7, 9], [5, 20]], "orders_queued": 1},
        },
        "universe": {"galaxies": [[3, 4]]},
        "galaxies": {"3,4": {"sectors": [[7, 9]], "visited": [[7, 9]], "hazards": []}},
        "sectors": {"3,4/7,9": {"terrain": [{"kind": "star", "cell": [5, 5], "colour": "yellow", "bucket": "medium"},
                                             {"kind": "planet", "cell": [6, 5], "name": "Oreth"}],
                                 "items": [{"id": "i-017", "cell": [9, 2], "rarity": "normal", "revealed": False}]}},
        "fog_of_war": True,
        "view": {"level": "follow"},
    }


def player_state(t: int) -> tuple[int, int, int, bool]:
    """(food, water, fear, mutiny_this_turn) for turn t with the fixture config and no trade."""
    c = FIXTURE_CFG
    if t < MUTINY_TURN:
        f = max(0, c["food_start"] - t)
        return f, f, c["fear_start"], False
    if t == MUTINY_TURN:
        return c["mutiny_supplies"], c["mutiny_supplies"], max(0, c["fear_start"] - c["mutiny_fear_delta"]), True
    f = max(0, c["mutiny_supplies"] - (t - MUTINY_TURN))
    return f, f, max(0, c["fear_start"] - c["mutiny_fear_delta"]), False


def make_good(turns: int = TURNS) -> tuple[dict, dict]:
    per_turn = []
    captains = CAPTAINS
    x, y = 12, 11
    c = FIXTURE_CFG
    for t in range(1, turns + 1):
        food, water, fear, mutiny = player_state(t)
        x, y = (x + (1 if t % 3 else -1)) % 24, (y + (1 if t % 5 == 0 else 0)) % 24
        events = [f"movement: Lantern moved to {x},{y} at speed 4", f"upkeep: food {food}, water {water}"]
        low = min(food, water) <= c["warn_turns"]
        if mutiny:
            captains += 1
            events.append("mutiny: the crew seize the Lantern; Harrow Vey is demoted to a kestrel with 10 food and 10 water; fear −10")
        elif low and min(food, water) > 0:
            events.append(f"supplies warning: {min(food, water)} turns of food and water left")
        elif low:
            zero_turns = t - c["food_start"]
            events.append(f"supplies warning: food and water at 0 — mutiny in {c['mutiny_turns_at_zero'] + 1 - zero_turns} turns")
        per_turn.append({
            "turn_no": t,
            "orders_executed": {"player": 1, "ai": captains - 1},
            "ai_ships_moved": captains - 1 - 7,
            "player": {"food": food, "water": water, "fear": fear, "respect": c["respect_start"], "gold": c["gold_start"],
                       "silver": c["silver_start"], "ship_class": "kestrel", "position": [[3, 4], [7, 9], [x, y]]},
            "captains": captains,
            "events": events,
            "snapshot_hash": fake_hash(f"seed1234-turn{t}"),
            "snapshot_bytes": 184233 + t * 17,
            "resolve_ms": 41,
        })
    last = per_turn[-1]
    snapshot = make_snapshot(turns, last["player"]["food"], last["player"]["water"], last["player"]["fear"], (x, y))
    last["snapshot_hash"] = canonical_hash(snapshot)   # the save fixture must round-trip to this
    report = {
        "schema": 1, "seed": 1234, "config_path": CONFIG_PATH,
        "turns_requested": turns, "turns_completed": turns, "outcome": "running", "elapsed_s": 2.47,
        "per_turn": per_turn,
        "final": {"captains": captains, "ships": captains,
                  "mutinies": [{"turn_no": MUTINY_TURN, "captain": "player", "result": "demoted"}],
                  "game_over_turn": None, "mastery_turn": None},
    }
    return report, snapshot


def broken_variants(good: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}

    r = copy.deepcopy(good)                       # smoke/02 — turn_no never advances
    for t in r["per_turn"]:
        t["turn_no"] = 1
    out["sim_turns_stuck"] = r

    r = copy.deepcopy(good)                       # smoke/03 — AI never acts
    for t in r["per_turn"]:
        t["orders_executed"]["ai"] = 0
        t["ai_ships_moved"] = 0
    out["sim_ai_idle"] = r

    r = copy.deepcopy(good)                       # smoke/03 — AI orders exist but ships never move
    for t in r["per_turn"]:
        t["ai_ships_moved"] = 3
    out["sim_ai_frozen"] = r

    r = copy.deepcopy(good)                       # smoke/04 — snapshot hash never changes
    h = r["per_turn"][-1]["snapshot_hash"]
    for t in r["per_turn"]:
        t["snapshot_hash"] = h
    out["sim_hash_frozen"] = r

    r = copy.deepcopy(good)                       # smoke/05 — water uses a literal, not the ini
    for t in r["per_turn"]:
        if t["turn_no"] < MUTINY_TURN:
            t["player"]["water"] = FIXTURE_CFG["water_start"]
    out["sim_upkeep_wrong"] = r

    r = copy.deepcopy(good)                       # smoke/05 — no supplies warning ever
    for t in r["per_turn"]:
        t["events"] = [e for e in t["events"] if "warning" not in e]
    out["sim_no_warning"] = r

    r = copy.deepcopy(good)                       # smoke/05 — warning shown every turn (stale banner)
    for t in r["per_turn"]:
        if not any("warning" in e for e in t["events"]):
            t["events"].append("supplies warning: running low")
    out["sim_warning_always"] = r

    r = copy.deepcopy(good)                       # smoke/06 — mutiny never fires
    for t in r["per_turn"]:
        n = t["turn_no"]
        if n >= MUTINY_TURN:
            t["player"]["food"] = t["player"]["water"] = 0
            t["player"]["fear"] = FIXTURE_CFG["fear_start"]
            t["captains"] = CAPTAINS
            t["orders_executed"]["ai"] = CAPTAINS - 1
            t["events"] = [e for e in t["events"] if not e.startswith("mutiny")]
            if not any("warning" in e for e in t["events"]):
                t["events"].append("supplies warning: food and water at 0 — mutiny imminent")
    r["final"]["mutinies"] = []
    r["final"]["captains"] = r["final"]["ships"] = CAPTAINS
    out["sim_no_mutiny"] = r

    r = copy.deepcopy(good)                       # smoke/06 — mutiny one turn early (off-by-one, D-76)
    early, at = r["per_turn"][MUTINY_TURN - 2], r["per_turn"][MUTINY_TURN - 1]
    early["events"].append("mutiny: the crew seize the Lantern")
    r["final"]["mutinies"][0]["turn_no"] = MUTINY_TURN - 1
    out["sim_mutiny_early"] = r

    r = copy.deepcopy(good)                       # smoke/06 — fires, but the consequences are missing
    at = r["per_turn"][MUTINY_TURN - 1]
    at["player"]["food"] = at["player"]["water"] = 0
    at["player"]["fear"] = FIXTURE_CFG["fear_start"]
    at["captains"] = CAPTAINS
    out["sim_mutiny_no_effect"] = r

    return out


def make_config_reports() -> dict[str, dict]:
    """smoke/08 fixtures: a 2-turn run against an ini with food_start = 12."""
    base = {
        "schema": 1, "seed": 1234, "config_path": "<TMP>\\cfg12.ini",
        "turns_requested": 2, "turns_completed": 2, "outcome": "running", "elapsed_s": 0.4,
        "per_turn": [], "final": {"captains": 193, "ships": 193, "mutinies": [], "game_over_turn": None, "mastery_turn": None},
    }
    for t, food in ((1, 11), (2, 10)):
        base["per_turn"].append({
            "turn_no": t, "orders_executed": {"player": 1, "ai": 192}, "ai_ships_moved": 185,
            "player": {"food": food, "water": food, "fear": 10, "respect": 10, "gold": 5, "silver": 50, "ship_class": "kestrel",
                       "position": [[3, 4], [7, 9], [12 + t, 11]]},
            "captains": 193, "events": [f"upkeep: food {food}, water {food}"], "snapshot_hash": fake_hash(f"cfg{t}"),
            "snapshot_bytes": 184000, "resolve_ms": 30})
    ok = base
    ignored = copy.deepcopy(base)
    for t in ignored["per_turn"]:
        t["player"]["food"] = 30 - t["turn_no"]          # literal 30 ignores the ini
        t["player"]["water"] = 30 - t["turn_no"]
    return {"sim_config_ok": ok, "sim_config_ignored": ignored}


# --------------------------------------------------------------------------- M2 (specs/M2.md § 3, C9)

def synth_report(scn: dict, *, seed: int = 1234) -> dict:
    """A C2+C9 report that does exactly what the plan expects (the good fixture for a scenario)."""
    plan = scn["plan"]
    m = FIXTURE_M2
    if scn.get("teleport"):
        g0, g1, s0, s1, c0, c1 = (int(x) for x in scn["teleport"].split())
        pos = [[g0, g1], [s0, s1], [c0, c1]]
    else:
        pos = [list(x) for x in M2_INDEX["player_start"]]
    shield = m["ship"]["shield_max"]
    hazard: dict | None = None
    move_rem = None
    end = plan.get("end") or {}
    game_over = end.get("outcome") == "game_over"
    last_turn = end["turn"] if game_over else int(end.get("turns") or scn["turns"])
    by_turn = {e["turn"]: e for e in plan.get("turns") or []}
    per_turn = []
    for t in range(1, last_turn + 1):
        exp = by_turn.get(t, {})
        if "cell" in exp:
            pos = [pos[0], pos[1], list(exp["cell"])]
        if "sector" in exp:
            pos = [list(exp["sector"][0]), list(exp["sector"][1]), pos[2]]
        if "cell_near" in exp:
            cx, cy = exp["cell_near"]["centre"]
            pos = [pos[0], pos[1], [int(cx + 0.5), int(cy + 0.5)]]
        if "hazard" in exp:
            want = exp["hazard"]
            if want is None:
                hazard = None
            elif want == "any":
                hazard = {"kind": "black_hole", "cell": [8, 8], "distance": 1, "pull": 7.071, "turns_left": 0, "drain": 0}
            else:
                kind = want.get("kind", "star")
                hazard = {"kind": kind, "cell": list(pos[2]) if want.get("distance", 0) == 0 else [6, 6], "distance": want.get("distance", 0),
                          "pull": round(float(want.get("pull", 1.0)), 4), "turns_left": want.get("turns_left", 1),
                          "drain": int(m["drain"]["medium"]) if kind == "star" else 0}
        if "shield" in exp:
            shield = exp["shield"]
        if "move_remaining" in exp:
            move_rem = exp["move_remaining"]
        events = ["Orders: 1 from you, 192 from AI captains"]
        for sub in exp.get("events_contain") or []:
            events.append("Lantern is held by gravity (pull 3.85 against engine 4)" if sub == "held" else f"Lantern: the {sub} failed — pulled by gravity")
        warps = [dict(w, ship="s-194") for w in exp.get("warps") or []]
        destroyed = []
        if game_over and t == end["turn"]:
            destroyed = [{"ship": "s-194", "captain": "c-000", "cause": end["cause"], "cell": list(end.get("cell") or pos[2])}]
            events.append(f"Lantern destroyed: pulled into the {end['cause'].replace('_', ' ')} — game over")
        per_turn.append({
            "turn_no": t, "orders_executed": {"player": 1, "ai": 192}, "ai_ships_moved": 185,
            "player": {"food": 30 - t, "water": 30 - t, "fear": 10, "respect": 10, "gold": 5, "silver": 50, "ship_class": "kestrel",
                       "position": [list(p) for p in pos], "shield_current": shield, "shield_max": m["ship"]["shield_max"],
                       "hazard": copy.deepcopy(hazard), "move_remaining": list(move_rem) if move_rem else None},
            "captains": 193, "events": events, "snapshot_hash": fake_hash(f"m2-{plan['what']}-{t}"), "snapshot_bytes": 180000,
            "resolve_ms": 6.0, "warps": warps, "destroyed": destroyed,
        })
    return {
        "schema": 1, "seed": seed, "config_path": CONFIG_PATH, "turns_requested": scn["turns"], "turns_completed": last_turn,
        "outcome": "game_over" if game_over else "running", "elapsed_s": 1.0, "per_turn": per_turn,
        "final": {"captains": 193, "ships": 193, "mutinies": [], "game_over_turn": last_turn if game_over else None, "mastery_turn": None,
                  "game_over_cause": f"pulled into the {end['cause'].replace('_', ' ')}" if game_over else None,
                  "hazard_deaths": 1 if game_over else 0, "warps": sum(len(t["warps"]) for t in per_turn)},
    }


def m2_scenarios() -> dict[str, dict]:
    """Every plan the gate builds, instantiated on M2_INDEX with the fixture ini."""
    m = FIXTURE_M2
    k, f, scale, engine, shield = m["k_g"], m["falloff"], m["pull_turns_scale"], m["ship"]["engine"], m["ship"]["shield_max"]
    bh, ns = m["blackhole_mass"], m["neutron_mass"]
    start = M2_INDEX["player_start"]
    star, exit_ = C.pick_star(M2_INDEX, k_g=k, falloff=f, scale=scale, sector_size=m["sector_size"])
    plain = next(h for h in M2_INDEX["black_holes"] if not h["wormhole_to"])
    worm = next(h for h in M2_INDEX["black_holes"] if h["wormhole_to"])
    neutron = M2_INDEX["neutron_stars"][0]
    held_cell, held_sign = C.cell_at_distance(M2_INDEX, plain, 2, m["sector_size"], diagonal=False)
    path = tuple(C.escape_path(4, 3, engine=engine, mass=bh, k_g=k, falloff=f))
    esc_cell, esc_sign = C.cell_at_distance(M2_INDEX, plain, 4, m["sector_size"], diagonal=True, path=path)
    ns_cell, _ = C.cell_at_distance(M2_INDEX, neutron, 1, m["sector_size"], diagonal=False)
    worm_cell, _ = C.cell_at_distance(M2_INDEX, worm, 1, m["sector_size"], diagonal=False)
    plain1_cell, _ = C.cell_at_distance(M2_INDEX, plain, 1, m["sector_size"], diagonal=False)
    neg_cell, _ = C.cell_at_distance(M2_INDEX, plain, 2, m["sector_size"], diagonal=False)
    pos_cell, _ = C.cell_at_distance(M2_INDEX, plain, 3, m["sector_size"], diagonal=False)
    common_star = dict(k_g=k, falloff=f, drain=m["drain"]["medium"], shield_max=shield)
    scns = {
        "move_persist": C.plan_persistent_move(start, (0, 1), engine, variant="persist"),
        "move_stop": C.plan_persistent_move(start, (0, 1), engine, variant="stop"),
        "move_replace": C.plan_persistent_move(start, (0, 1), engine, variant="replace", perpendicular=(1, 0)),
        "star_fatal": C.plan_star_fatal(star, scale=scale, **common_star),
        "star_fatal_scale10": C.plan_star_fatal(star, scale=10.0, label=" with pull_turns_scale = 10", **common_star),
        "countdown_reset": C.plan_countdown_reset(star, exit_, k_g=k, falloff=f, scale=scale, drain=m["drain"]["medium"],
                                                  recharge=m["shield_recharge"], shield_max=shield),
        "hole_held": C.plan_hole_held(plain, held_cell, held_sign, kind="black_hole", mass=bh, k_g=k, falloff=f, scale=scale, engine=engine, shield_max=shield),
        "hole_escape": C.plan_hole_escape(plain, esc_cell, esc_sign, kind="black_hole", mass=bh, k_g=k, falloff=f, scale=scale, engine=engine),
        "neutron": C.plan_neutron_fatal(neutron, ns_cell, mass=ns, k_g=k, falloff=f, scale=scale, shield_max=shield),
        "wormhole": C.plan_wormhole(worm, worm_cell, mass=bh, k_g=k, falloff=f, scale=scale),
        "plain_hole": C.plan_plain_hole_kills(plain, plain1_cell, mass=bh, k_g=k, falloff=f, scale=scale),
        "warp_arrival": C.plan_warp_arrival(start, ((3, 4), (10, 9)), m["ship"]["warp"], pull_start=0.0, sector_size=m["sector_size"], label=" (distance 3)"),
        "warp_far": C.plan_warp_arrival(start, ((7, 7), (15, 15)), 10.0, pull_start=0.0, sector_size=m["sector_size"], label=" factor 10 to the farthest sector"),
        "warp_pull_neg": C.plan_warp_pull(plain, neg_cell, ((3, 4), (7, 9)), m["ship"]["warp"], mass=bh, k_g=k, falloff=f, scale=scale, require_event=True),
        "warp_pull_pos": C.plan_warp_pull(plain, pos_cell, ((3, 4), (7, 9)), m["ship"]["warp"], mass=bh, k_g=k, falloff=f, scale=scale, require_event=False),
    }
    out = {}
    for name, scn in scns.items():
        save_present = None if "save_present" not in scn["plan"] else scn["plan"]["save_present"]
        out[name] = {"scenario": scn, "report": synth_report(scn), "save_present": save_present}
    return out


def warp_bus_cases() -> tuple[list[dict], dict]:
    reach = C.warp_reach(3, 16, 4)
    cases = [
        {"label": "beyond reach", "command": "warp", "args": ["3", "4", "11", "9", "3"], "expect": "rejected", "reach": reach,
         "why": "distance 4 exceeds reach 3.07 for factor 3 (CMD-07, MV-02)"},
        {"label": "factor above warpcore_power", "command": "warp", "args": ["3", "4", "10", "9", "4"], "expect": "rejected",
         "reason_all": [r"warp[ _]?core[ _]?power"], "why": "factor 4 > warpcore_power 3 (CMD-07)"},
        {"label": "Void destination", "command": "warp", "args": ["3", "4", "5", "9", "3"], "expect": "rejected", "why": "Void (M2 § 1)"},
        {"label": "outside the universe", "command": "warp", "args": ["8", "8", "0", "0", "3"], "expect": "rejected", "why": "outside (M2 § 5)"},
    ]
    obs = {"hello": {"turn_no": 0, "seed": 1234}, "notes": [], "replies": [
        {"label": "beyond reach", "topic": "order.rejected", "reason": f"destination 4 sectors away is beyond the reach {reach:.2f} for factor 3 (CMD-07)"},
        {"label": "factor above warpcore_power", "topic": "order.rejected", "reason": "factor 4 exceeds warpcore_power 3 (CMD-07)"},
        {"label": "Void destination", "topic": "order.rejected", "reason": "destination 3,4/5,9 is Void (CMD-07)"},
        {"label": "outside the universe", "topic": "order.rejected", "reason": "galaxy 8,8 is outside the universe (CMD-07)"},
    ]}
    return cases, obs


def ai_fixtures() -> tuple[dict, dict]:
    """smoke/20: three snapshots (turns 0..2) with AI ships in and out of a hole's sector, plus a report."""
    plain = next(h for h in M2_INDEX["black_holes"] if not h["wormhole_to"])
    hx, hy = plain["cell"]

    def snapshot(turn_no: int, positions: dict[str, list], extra_captains: dict | None = None) -> dict:
        s = make_snapshot(turn_no, 30 - turn_no, 30 - turn_no, 10, (12, 11))
        s["game_over"] = False
        s["captains"]["c-002"] = {"name": "Tam Wyre", "is_player": False, "gold": 3, "silver": 20, "fear": 10, "respect": 10, "food": 30, "water": 30,
                                  "ships": ["s-002"], "active_ship": "s-002", "items": [], "is_master": False, "mutiny_countdown": None}
        s["ships"]["s-002"] = {"name": "Low Tide", "class": "corsair", "captain": "c-002", "torpedoes": 12, "phasers": 5.0, "shield_max": 80.0,
                               "shield_current": 80.0, "engine": 6.0, "warp": 5.0, "position": [[3, 4], [7, 9], [2, 2]], "orders_queued": 0}
        for sid, pos in positions.items():
            s["ships"][sid]["position"] = pos
        for ship in s["ships"].values():
            ship.setdefault("move_remaining", None)
            ship.setdefault("hazard", None)
            ship.setdefault("orders", [])
        if extra_captains:
            s["captains"].update(extra_captains)
        return s

    def report(destroyed_turn1: list[dict]) -> dict:
        per_turn = []
        for t in (1, 2):
            per_turn.append({"turn_no": t, "orders_executed": {"player": 0, "ai": 2}, "ai_ships_moved": 2,
                             "player": {"food": 30 - t, "water": 30 - t, "fear": 10, "respect": 10, "gold": 5, "silver": 50, "ship_class": "kestrel",
                                        "position": [[3, 4], [7, 9], [12, 11]], "shield_current": 60.0, "shield_max": 60.0, "hazard": None, "move_remaining": None},
                             "captains": 3, "events": ["AI moved"], "snapshot_hash": fake_hash(f"ai{t}"), "snapshot_bytes": 180000, "resolve_ms": 5.0,
                             "warps": [], "destroyed": destroyed_turn1 if t == 1 else []})
        return {"schema": 1, "seed": 1234, "config_path": CONFIG_PATH, "turns_requested": 2, "turns_completed": 2, "outcome": "running", "elapsed_s": 0.5,
                "per_turn": per_turn, "final": {"captains": 3, "ships": 3, "mutinies": [], "game_over_turn": None, "mastery_turn": None,
                                                "game_over_cause": None, "hazard_deaths": len(destroyed_turn1), "warps": 0}}

    hole_sector = [[3, 4], [8, 9]]
    away = [snapshot(0, {"s-001": [hole_sector[0], hole_sector[1], [hx + 3, hy]], "s-002": [[3, 4], [7, 9], [2, 2]]}),
            snapshot(1, {"s-001": [hole_sector[0], hole_sector[1], [hx + 5, hy]], "s-002": [[3, 4], [7, 9], [3, 2]]}),
            snapshot(2, {"s-001": [hole_sector[0], hole_sector[1], [hx + 7, hy + 1]], "s-002": [[3, 4], [7, 9], [4, 3]]})]
    good = {"index": M2_INDEX, "snapshots": away, "report": report([])}
    orphan = {"c-003": {"name": "Ren Astley", "is_player": False, "gold": 0, "silver": 0, "fear": 10, "respect": 10, "food": 30, "water": 30,
                        "ships": [], "active_ship": None, "items": [], "is_master": False, "mutiny_countdown": None}}
    toward = [snapshot(0, {"s-001": [hole_sector[0], hole_sector[1], [hx + 3, hy]], "s-002": [[3, 4], [7, 9], [2, 2]]}),
              snapshot(1, {"s-001": [hole_sector[0], hole_sector[1], [hx + 2, hy]], "s-002": [[3, 4], [7, 9], [5, 5]]}, orphan),
              snapshot(2, {"s-001": [hole_sector[0], hole_sector[1], [hx + 1, hy]], "s-002": [[3, 4], [7, 9], [5, 5]]}, orphan)]
    bad = {"index": M2_INDEX, "snapshots": toward,
           "report": report([{"ship": "s-002", "captain": "c-002", "cause": "star", "cell": [5, 5]}])}
    return good, bad


def game_over_obs(n: int = 7) -> tuple[dict, dict]:
    good = {"hello": {"turn_no": 0}, "seed": 1234, "ship_id": "s-194", "resolved": list(range(1, n + 1)), "last_snapshot_game_over": True,
            "game_over": {"turn_no": n, "cause": "pulled into the star at 5,5 in sector 3,4/7,9",
                          "summary": {"turns": n, "sectors_visited": 1, "mutinies": 0, "fear": 10, "respect": 10, "gold": 5, "silver": 50}},
            "game_over_wait_s": 0.01, "save_present_after_over": False,
            "after_over_reply": {"topic": "order.rejected", "reason": "game over — type new to start again (CMD-19)"},
            "hello_after_over": {"turn_no": n, "game_over": True}, "new_reply": {"topic": "order.accepted", "reason": ""},
            "hello_after_new": {"turn_no": 0, "seed": 1235, "game_over": False}, "resolved_after_new": 1,
            "save_present_after_new_turn": True, "notes": []}
    bad = copy.deepcopy(good)
    bad["after_over_reply"] = {"topic": "order.accepted", "reason": ""}          # the engine kept taking orders
    bad["save_present_after_over"] = True                                         # and kept the save
    bad["hello_after_over"] = None                                                # and never flagged game_over
    bad["last_snapshot_game_over"] = False
    return good, bad


def m2_broken(scen: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}

    s = copy.deepcopy(scen["move_persist"])      # smoke/13 — the move is clipped to one turn (the M1 rule, D-73 superseded by D-98)
    first = s["report"]["per_turn"][0]["player"]["position"]
    for t in s["report"]["per_turn"]:
        t["player"]["position"] = copy.deepcopy(first)
        t["player"]["move_remaining"] = None
    out["sim_move_clipped"] = s

    s = copy.deepcopy(scen["star_fatal"])        # smoke/15 — the countdown never reaches 0, nobody dies, the save stays
    r = s["report"]
    n = s["scenario"]["plan"]["end"]["turn"]
    last = copy.deepcopy(r["per_turn"][-1])
    last["destroyed"] = []
    last["events"] = [e for e in last["events"] if "destroyed" not in e]
    r["per_turn"][-1] = last
    for extra in range(n + 1, s["scenario"]["turns"] + 1):
        t = copy.deepcopy(last)
        t["turn_no"] = extra
        r["per_turn"].append(t)
    for t in r["per_turn"]:
        if t["player"]["hazard"]:
            t["player"]["hazard"]["turns_left"] = n - 1
    r.update(outcome="running", turns_completed=s["scenario"]["turns"])
    r["final"].update(game_over_turn=None, game_over_cause=None, hazard_deaths=0)
    s["save_present"] = True
    out["sim_star_not_fatal"] = s

    s = copy.deepcopy(scen["countdown_reset"])   # smoke/16 — the counter keeps counting across the excursion
    plan = s["scenario"]["plan"]
    reentry = next(e for e in plan["turns"] if e.get("note", "").startswith("re-entry"))
    stay = sum(1 for e in plan["turns"] if e["turn"] < reentry["turn"] and e.get("hazard"))
    for t in s["report"]["per_turn"]:
        if t["turn_no"] == reentry["turn"]:
            t["player"]["hazard"]["turns_left"] = reentry["hazard"]["turns_left"] - stay
    out["sim_countdown_no_reset"] = s

    s = copy.deepcopy(scen["hole_held"])         # smoke/17 — the ship walks away from a hole whose pull exceeds its engine
    r = s["report"]
    plan = s["scenario"]["plan"]
    hole_sector = plan["turns"][0]["cell"] if plan["turns"] else None
    cell0 = r["per_turn"][0]["player"]["position"][2]
    for i, t in enumerate(r["per_turn"], start=1):
        t["player"]["position"][2] = [cell0[0] + i, cell0[1]]
        t["events"] = [e for e in t["events"] if "held" not in e]
        t["destroyed"] = []
    last = copy.deepcopy(r["per_turn"][-1])
    last["turn_no"] = len(r["per_turn"]) + 1
    last["player"]["position"][2] = [cell0[0] + len(r["per_turn"]) + 1, cell0[1]]
    r["per_turn"].append(last)
    r.update(outcome="running", turns_completed=len(r["per_turn"]))
    r["final"].update(game_over_turn=None, game_over_cause=None, hazard_deaths=0)
    s["save_present"] = True
    out["sim_hole_escapable"] = s

    s = copy.deepcopy(scen["wormhole"])          # smoke/18 — the wormhole destroys the ship instead of carrying it
    r = s["report"]
    n = s["scenario"]["turns"]
    t = r["per_turn"][-1]
    g0, g1, s0, s1, c0, c1 = (int(x) for x in s["scenario"]["teleport"].split())
    t["player"]["position"] = [[g0, g1], [s0, s1], [c0, c1]]
    t["player"]["hazard"] = None
    t["destroyed"] = [{"ship": "s-194", "captain": "c-000", "cause": "black_hole", "cell": [c0, c1]}]
    r.update(outcome="game_over", turns_completed=n)
    r["final"].update(game_over_turn=n, game_over_cause="pulled into the black hole", hazard_deaths=1)
    out["sim_wormhole_kills"] = s

    s = copy.deepcopy(scen["warp_arrival"])      # smoke/19 — the warp never happens and the reach in the rejection is wrong
    r = s["report"]
    for t in r["per_turn"]:
        t["player"]["position"] = [list(x) for x in M2_INDEX["player_start"]]
        t["warps"] = []
    r["final"]["warps"] = 0
    cases, obs = warp_bus_cases()
    obs = copy.deepcopy(obs)
    obs["replies"][0]["reason"] = "destination 4 sectors away is beyond the reach 4.00 for factor 3 (CMD-07)"
    out["sim_warp_reach_wrong"] = {"arrival": s, "bus": {"cases": cases, "obs": obs}}
    return out


def write(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")


def main() -> int:
    good, snapshot = make_good()
    write(HERE / "sim_good.json", good)
    for name, rep in broken_variants(good).items():
        write(HERE / f"{name}.json", rep)
    for name, rep in make_config_reports().items():
        write(HERE / f"{name}.json", rep)
    write(HERE / "snapshot_good.json", snapshot)
    write(HERE / "save_good.json", {"snapshot": snapshot, "rng_state": [3, [1, 2, 3], None]})
    bad = copy.deepcopy(snapshot)
    bad["turn_no"] -= 1                                  # a save one turn stale
    write(HERE / "save_stale.json", {"snapshot": bad, "rng_state": [3, [1, 2, 3], None]})
    # M2
    scen = m2_scenarios()
    write(HERE / "sim_m2_scenarios.json", scen)
    for name, doc in m2_broken(scen).items():
        write(HERE / f"{name}.json", doc)
    cases, obs = warp_bus_cases()
    write(HERE / "sim_m2_warp_bus.json", {"cases": cases, "obs": obs})
    ai_good, ai_bad = ai_fixtures()
    write(HERE / "sim_m2_ai.json", ai_good)
    write(HERE / "sim_ai_suicide.json", ai_bad)
    go_good, go_bad = game_over_obs()
    write(HERE / "sim_m2_game_over.json", go_good)
    write(HERE / "sim_game_over_accepts_orders.json", go_bad)
    print(f"wrote fixtures to {HERE} (final hash {good['per_turn'][-1]['snapshot_hash'][:8]}…; {len(scen)} M2 scenarios)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
