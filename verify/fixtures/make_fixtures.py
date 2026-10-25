"""Generate the canned fixtures that --selftest feeds the checkers.

    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\fixtures\\make_fixtures.py

sim_good.json passes every report checker; every sim_<broken>.json fails exactly one
check (its own) when run through the same evaluation the gate uses.  The fixtures are
derived programmatically from the good one so a change to the good shape propagates.
All values follow CONTRACTS C2/C3 and the default controls_config.ini numbers below.
No franchise names (D-03).
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from _checks import canonical_hash  # noqa: E402

# The config the fixtures were generated against (the repo defaults on 2026-10-25).
FIXTURE_CFG = {
    "food_start": 30, "water_start": 30, "warn_turns": 5, "mutiny_turns_at_zero": 3,
    "mutiny_ship_class": "Kestrel", "mutiny_supplies": 10, "mutiny_fear_delta": 10,
    "fear_start": 10, "respect_start": 10, "gold_start": 5, "silver_start": 50,
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
    print(f"wrote fixtures to {HERE} (final hash {good['per_turn'][-1]['snapshot_hash'][:8]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
