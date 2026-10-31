"""M5 fixtures for --selftest (specs/M5.md § 4, CONTRACTS C12), called by make_fixtures.py.

sim_m5_scenarios.json holds one good synthetic C2+…+C12 report (plus the save snapshot / log / probe facts a
row reads) per gate scenario, each with the exact checker call the gate makes (`check`, `args`, `kwargs`)
against verify/_endgame.py, so the selftest proves every checker passes its good fixture and fails its broken
one for the right reason.  The broken fixtures the spec names — sim_void_rejected, sim_void_item_respawns,
sim_help_truncated, sim_log_missing_commands, sim_lodestone_too_near, sim_guardian_offers_peace,
sim_mastery_stats_not_maxed, sim_mastery_wealth_deducted, sim_master_replays_ceremony,
sim_ai_player_trade_starves — are minimal edits of the good ones.  All numbers follow the default
controls_config.ini (FIXTURE_M5) and the conventions of D-162.  The help renders are made with Rich at the
probe's widths, as the gate makes them.  No franchise names (D-03).
"""
from __future__ import annotations

import copy
import io
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from make_fixtures_m3 import CELL, HOME, PID, PSHIP, SPAWN_C, SPAWN_S, battle, case, ex, rep, snapshot  # noqa: E402
from make_fixtures_m4 import PLANET, PLANET_CELL, PLANET_NAME, m4_report, m4_snapshot, planet_record, trade  # noqa: E402

FIXTURE_M5 = {
    "respect_min": 90, "fear_min": 90, "wealth_min_silver": 2000, "required_special_distinct": 3, "guard_ships": 4, "ultimate_item_name": "Lodestone of Starbour",
    "placement_distance_pct": 50, "master_torpedoes": 99, "ceremony_seconds": 6, "void_item_pct": 25, "roam_void_pct": 15, "torpedo_range": 5,
    "shield_cap": 100.0, "phaser_max": 10.0, "impulse_max": 10.0, "warp_max": 10.0, "silver_per_gold": 20, "planet_price_band": 20.0,
    "universe_size": [8, 8], "galaxy_size": [16, 16], "sector_size": [24, 24],
}
CAPS = {"shield_cap": 100.0, "phaser_max": 10.0, "impulse_max": 10.0, "warp_max": 10.0}
KEY = "3,4/7,9"
VOID = [[3, 4], [5, 9]]                     # (5 + 9) % 7 == 0: a Void cell of the stub's home galaxy
VOID_KEY = "3,4/5,9"
VOID_ITEM = {"galaxy": VOID[0], "sector": VOID[1], "cell": [4, 4], "element": "osmium", "units": 2, "rarity": "normal"}
LODE_GALAXY, LODE_SECTOR, LODE_CELL = [7, 1], [9, 12], [12, 11]      # flat (121, 28) vs home (55, 73): 66 ≥ 64
LODE_KEY = "7,1/9,12"
GUARDS = {f"c-95{i}": f"s-95{i}" for i in range(1, 5)}
GUARD_CELLS = {"s-951": [14, 11], "s-952": [10, 9], "s-953": [12, 14], "s-954": [8, 12]}
ITEM_NAME = FIXTURE_M5["ultimate_item_name"]
COMMANDS = ("help", "status", "ship", "fleet", "view", "move", "warp", "fire", "shields", "peace", "escape", "trade", "upgrade", "scan", "pickup", "orders", "end", "quit",
            "new", "buy", "sell", "stop")
HELP_GROUPS = [
    ("Getting about", [("move", "at end", "Impulse move within the sector: absolute cell x y, or signed +dx +dy relative; speed at most the engine power. A far target is "
                                        "not clipped: the move persists turn after turn until the ship arrives, a new move replaces it, or stop cancels it."),
                       ("warp", "at end", "Warp to a sector in any galaxy; factor at most the warp core. Reach follows the warp curve; gravity nets the factor at departure. "
                                        "A Void cell is deep space and a valid destination."),
                       ("stop", "now", "All stop: cancel the active ship's persisting move and its queued move. stop all does it for every owned ship."),
                       ("view", "now", "Change the Grid terminal level; follow returns to auto-follow.")]),
    ("Looking", [("scan", "now", "Reveal what is in the current cell, the sector with fog of war on, and the captains here with their fear and respect."),
                 ("status", "now", "Show the active ship's stats and the captain's wealth, fear, respect, food and water."),
                 ("shields", "now", "Show current and maximum shields; they drain in a star's cell and recharge outside hazards and battle."),
                 ("fleet", "now", "List every owned ship, its location and state."),
                 ("ship", "now", "Switch the active ship by name, fleet number or id; each ship keeps its own order queue."),
                 ("orders", "now", "Review or cancel the active ship's queued orders; a persisting move counts.")]),
    ("Fighting", [("fire", "at end", "Attack a ship or planet in range after movement; fire target fires one torpedo and the phasers, the long forms one weapon. "
                                    "Firing on a target you are not in battle with starts a fight and costs respect."),
                  ("peace", "at end", "In a battle: offer peace, accept the other side's offer, or reject it. The side that did not offer is the victor."),
                  ("escape", "at end", "A move that reports whether it left every enemy's weapon range; any move that does ends the battle.")]),
    ("Trading & items", [("buy", "at end", "Buy from the planet in your cell at its prices; buy ship class pays gold and adds the ship to your fleet here."),
                         ("sell", "at end", "Sell to the planet in your cell at its prices; elements at a share of their value."),
                         ("trade", "at end", "Propose a barter with the planet or the captain in your cell; the counterparty accepts when what it receives is worth enough."),
                         ("upgrade", "at end", "Spend one unit of an element on torpedoes, phasers, shields, engine or warp per the upgrade map; stats stop at their caps."),
                         ("pickup", "at end", "Pick up an element stack lying in the current cell after a scan; never automatic.")]),
    ("Game", [("help", "now", "List the commands by group, one group, or one command's panel."),
              ("end", "now", "End the turn; every captain's queued orders resolve. Asks first in a hazard countdown with no move queued."),
              ("new", "now", "Start a new game with a new or given seed; asks for confirmation."),
              ("quit", "now", "Confirm, autosave, then close every window.")]),
]
WARP_PANEL = ["Syntax   warp <gx gy> <sx sy> [factor]", "Does     Warp to a sector in any galaxy; a Void cell is deep space and a valid destination.",
              "When     at end (TP-02)", "Example  warp 3 4 7 9 3", "Numbers  reach 3.07 sectors at factor 3", "IDs      CMD-07, MV-02, MV-05, HZ-10, D-153"]


# --------------------------------------------------------------------------- builders

def m5_report(turns: list[dict], **kw) -> dict:
    """m4_report plus the C12 per-turn fields (void_visits, lodestone, player.master / infinite_wealth) and final counts."""
    doc = m4_report(turns, **kw)
    for t, spec in zip(doc["per_turn"], turns):
        t["void_visits"] = bool(spec.get("void_visits", False))
        t["lodestone"] = copy.deepcopy(spec.get("lodestone"))
        t["player"].setdefault("master", bool(spec.get("master", False)))
        t["player"].setdefault("infinite_wealth", bool(spec.get("master", False)))
    fin = doc["final"]
    fin.setdefault("eligible_turn", next((t["turn_no"] for t in doc["per_turn"] if t.get("lodestone")), None))
    fin.setdefault("ai_void_visits", 0)
    fin.setdefault("guardians_destroyed", sum(1 for t in doc["per_turn"] for d in t["destroyed"] if d.get("ship") in GUARDS.values()))
    if any(t["player"].get("master") for t in doc["per_turn"]):
        fin["mastery_turn"] = next(t["turn_no"] for t in doc["per_turn"] if t["player"].get("master"))
        doc["outcome"] = "mastery"
    return doc


def m5_snapshot(turn_no: int, *, lodestone: dict | None = None, guardians: bool = False, master: bool = False, sector_kind: str | None = "system", **kw) -> dict:
    snap = m4_snapshot(turn_no, **kw)
    for cid, cap in snap["captains"].items():
        cap.setdefault("master", False)
        cap.setdefault("infinite_wealth", False)
        cap.setdefault("guardian", False)
    if sector_kind is not None:
        for sec in snap["sectors"].values():
            sec["kind"] = sector_kind
    snap["lodestone"] = copy.deepcopy(lodestone)
    if guardians:
        for cid, sid in GUARDS.items():
            snap["captains"][cid] = {"name": f"Warden {cid[-1]}", "is_player": False, "gold": 0, "silver": 0, "fear": 50, "respect": 50, "food": 30, "water": 30, "ships": [sid],
                                     "active_ship": sid, "items": {}, "is_master": False, "mutiny_countdown": None, "visible_reputation": False, "master": False,
                                     "infinite_wealth": False, "guardian": True}
            snap["ships"][sid] = {"name": f"Iron Warden {sid[-1]}", "class": "bastion", "captain": cid, "torpedoes": 20, "phasers": 8.0, "shield_max": 100.0,
                                  "shield_current": 100.0, "engine": 3.0, "warp": 4.0, "position": [LODE_GALAXY, LODE_SECTOR, GUARD_CELLS[sid]], "orders_queued": 0,
                                  "orders": [], "hazard": None, "move_remaining": None, "in_battle": None, "target": None}
        snap["universe"]["galaxies"].append(LODE_GALAXY)
        snap["galaxies"]["7,1"] = {"sectors": [[9, 12], [9, 13], [10, 12]], "visited": [], "hazards": [[10, 12]]}
    if master:
        cap = snap["captains"][PID]
        cap.update(master=True, infinite_wealth=True, fear=100, respect=100, is_master=True)
        snap["ships"][PSHIP].update(torpedoes=99, phasers=10.0, shield_max=100.0, shield_current=100.0, engine=10.0, warp=10.0)
    return snap


def lodestone_row(alive: int = 4, picked: int | None = None, guardians: list[str] | None = None, **over) -> dict:
    row = {"sector": LODE_KEY, "cell": list(LODE_CELL), "distance": 66, "placed_turn": 1, "guardians": list(guardians or GUARDS), "guardians_alive": alive, "picked_up_turn": picked}
    row.update(over)
    return row


def at(sector: list, cell: list, **player) -> dict:
    return {"position": [sector[0], sector[1], list(cell)], **player}


def help_render_texts(widths: tuple[int, ...] = (80, 120, 400)) -> dict[str, str]:
    """The probe's renders dict made with Rich: grouped tables (Command · When · What) at every width, `help fighting`, the `help warp` panel."""
    from rich import box
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.table import Table

    def table(title: str, rows: list) -> Table:
        t = Table(title=title, box=box.SIMPLE_HEAD, expand=False, title_justify="left", show_edge=False, pad_edge=False)
        t.add_column("Command", no_wrap=True)
        t.add_column("When", no_wrap=True)
        t.add_column("What")
        for name, when, what in rows:
            t.add_row(name, when, what)
        return t

    def export(renderable, width: int) -> str:
        con = Console(file=io.StringIO(), width=width, record=True, force_terminal=False, color_system=None, legacy_windows=False, highlight=False)
        con.print(renderable)
        return con.export_text(styles=False)
    out: dict[str, str] = {}
    for w in widths:
        out[f"all@{w}"] = export(Group(*[table(title, rows) for title, rows in HELP_GROUPS]), w)
        out[f"fighting@{w}"] = export(table("Fighting", HELP_GROUPS[2][1]), w)
        out[f"warp@{w}"] = export(Panel("\n".join(WARP_PANEL), title="warp", expand=False), w)
    return out


# --------------------------------------------------------------------------- smoke/19 (amended) and smoke/49 — deep space

def deep_space_warp() -> dict:
    turns = [{"warps": [{"ship": PSHIP, "from": HOME, "to": VOID, "factor": 3.0, "effective": 3.0}], "events": ["Lantern warped to 3,4/5,9 (deep space)"],
              "player": at(VOID, [11, 11]), "void_visits": True},
             {"events": ["script order 'scan' accepted: Deep space — nothing here (OUT-10)"], "player": at(VOID, [11, 11])},
             {"warps": [{"ship": PSHIP, "from": VOID, "to": HOME, "factor": 3.0, "effective": 3.0}], "events": ["Lantern warped to 3,4/7,9"], "player": at(HOME, [11, 11])}]
    return case("deep_space_warp", ["report"], {"void": [VOID[0], VOID[1]], "start": [HOME[0], HOME[1]], "warp_turn": 1, "scan_turn": 2, "back_turn": 3,
                                                "what": "fixture deep-space warp"}, report=m5_report(turns))


def void_sector_snapshot() -> dict:
    snap = m5_snapshot(1, player_cell=[11, 11])
    snap["sectors"][VOID_KEY] = {"kind": "void", "terrain": [], "items": []}
    snap["ships"][PSHIP]["position"] = [VOID[0], VOID[1], [11, 11]]
    return case("void_sector_snapshot", ["snapshot"], {"key": VOID_KEY, "what": "fixture void sector"}, snapshot=snap)


def void_render() -> dict:
    grid = "sector 3,4/5,9 · deep space\n" + "\n".join(f"{y:>2} " + "".join("▲ " if (x, y) == (11, 11) else "· " for x in range(24)) for y in range(24))
    status = "Turn 2\nDeep space 3,4/5,9 · cell 11,11\nfood 28  water 28"
    return case("void_render", ["grid_text", "glyph", "cell", "status_text", "key", "what"], {}, grid_text=grid, glyph="▲", cell=[11, 11], status_text=status, key=VOID_KEY,
                what="fixture void render")


def voids_listing() -> dict:
    voids = []
    n = 0
    for sx in range(16):
        for sy in range(16):
            if (sx + sy) % 7 == 0:
                n += 1
                item = {"element": "osmium" if n % 8 else "orichalcum", "units": 1 + n % 3, "rarity": "normal" if n % 8 else "special", "cell": [n % 24, (n * 5) % 24]} if n % 4 == 0 else None
                voids.append({"sector": f"3,4/{sx},{sy}", "item": item})
    expected = {v["sector"] for v in voids}
    return case("voids_listing", ["listings", "expected"], {"pct": 25.0, "what": "fixture voids listing"},
                listings={"1234": [voids, copy.deepcopy(voids)]}, expected={"1234": sorted(expected)})


def void_item() -> dict:
    el, cell = VOID_ITEM["element"], VOID_ITEM["cell"]
    nb = [[3, 4], [6, 9]]
    turns = [{"events": [f"script order 'pickup {el}' rejected: nothing has been scanned in this cell (IT-04, CMD-15)"], "player": at(VOID, cell), "void_visits": True},
             {"events": [f"script order 'scan' accepted: Deep space — {el} (2 units, normal) lies here (OUT-10)"], "player": at(VOID, cell)},
             {"events": [f"Picked up 2 {el} (OUT-10)"], "pickups": [{"captain": PID, "element": el, "units": 2, "rarity": "normal"}], "player": at(VOID, cell, items={el: 2})},
             {"warps": [{"ship": PSHIP, "from": VOID, "to": nb, "factor": 3.0, "effective": 3.0}], "player": at(nb, [11, 11], items={el: 2})},
             {"warps": [{"ship": PSHIP, "from": nb, "to": VOID, "factor": 3.0, "effective": 3.0}], "player": at(VOID, [11, 11], items={el: 2}), "void_visits": True},
             {"events": ["script order 'scan' accepted: Deep space — nothing here (OUT-10)"], "player": at(VOID, [11, 11], items={el: 2})}]
    snap = m5_snapshot(6, player_cell=[11, 11], items={el: 2})
    snap["sectors"][VOID_KEY] = {"kind": "void", "terrain": [], "items": []}
    snap["ships"][PSHIP]["position"] = [VOID[0], VOID[1], [11, 11]]
    return case("void_item", ["report", "snapshot"], {"item": VOID_ITEM, "key": VOID_KEY, "el": el, "blind_turn": 1, "scan_turn": 2, "pickup_turn": 3, "reenter_turn": 5,
                                                      "rescan_turn": 6, "what": "fixture deep-space stack"}, report=m5_report(turns), snapshot=snap)


def ai_void_visits() -> dict:
    from make_fixtures_m4 import ai_turns
    default = m5_report([dict(t, void_visits=False) for t in ai_turns(200)], final={"ai_mutinies": 5, "ai_void_visits": 37})
    zero = m5_report([dict(t, void_visits=False) for t in ai_turns(60)], final={"ai_mutinies": 1, "ai_void_visits": 0})
    return case("ai_void_visits", ["report", "report2"], {"turns_default": 200, "turns_zero": 60, "what": "fixture AI void visits"}, report=default, report2=zero)


# --------------------------------------------------------------------------- smoke/50 — help

def help_render() -> dict:
    return case("help_render", ["renders"], {"widths": [80, 120], "commands": list(COMMANDS), "what": "fixture help render"}, renders=help_render_texts())


# --------------------------------------------------------------------------- smoke/51 — log

LOG_LINES = [(1, "move +3 +0"), (2, "warp 8 8 0 0"), (3, "scan"), (4, "help"), (5, "status")]
WARP_REASON = "galaxy 8,8 is outside the universe 8x8 (CMD-07, VW-01)"


def log_text(lines: list[tuple[int, str]] = LOG_LINES, header: bool = True) -> str:
    head = ("# Master of Starbour gameplay log - seed 1234. 'T<n> <phase> <severity>/<type>: text [ids]' is a T3 event; 'T<n> <captain> <name>: reason (ids)' is an AI "
            "decision; 'T<n> you > <line>: <outcome>' is a player line (queued as / rejected: / the reply). Never read by the game.\n") if header else ""
    outcomes = {"move +3 +0": "queued as move +3 +0 (persisting)", "warp 8 8 0 0": f"rejected: {WARP_REASON}", "scan": "Scan: nothing to pick up in cell 12,11",
                "help": "Getting about: move, warp, stop, view", "status": "Harrow Vey  turn 5  seed 1234"}
    body = []
    for t, ln in lines:
        body.append(f"T{t} you > {ln}: {outcomes[ln]}")
        body.append(f"T{t} TP-02 info/movement: Lantern moved to {12 + 3 * min(t, 1)},11 [MV-01]")
    return head + "\n".join(body) + "\n"


def gameplay_log() -> dict:
    turns = [{"events": ["Lantern moved to 15,11"], "player": at(HOME, [15, 11])},
             {"events": [f"script order 'warp 8 8 0 0' rejected: {WARP_REASON}"], "player": at(HOME, [15, 11])},
             {"events": ["script order 'scan' accepted: Scan: nothing to pick up in cell 15,11"], "player": at(HOME, [15, 11])},
             {"events": ["script order 'help' accepted: Getting about: move, warp, stop, view"], "player": at(HOME, [15, 11])},
             {"events": ["script order 'status' accepted: Harrow Vey  turn 5  seed 1234"], "player": at(HOME, [15, 11])}]
    return case("gameplay_log", ["log_text", "report"], {"lines": LOG_LINES, "queued": ["move +3 +0"], "rejected": ["warp 8 8 0 0"], "what": "fixture gameplay log"},
                log_text=log_text(), report=m5_report(turns))


def trace_scope() -> dict:
    spawns = [{"ship": "s-910", "captain": "c-910", "class": "corsair", "cell": [3, 3], "name": "Tamsin Harlow"}]
    snap = m5_snapshot(2, spawns=spawns)
    snap["ships"]["s-910"]["position"] = [[3, 4], [8, 9], [3, 3]]                        # outside the player's sector
    all_log = ("T1 c-910 Tamsin Harlow: no enemy to fight, no roam due -> random walk 3,3 -> 5,4 (up to 6 cell(s)) (OV-10)\n"
               "T1 TP-02 info/movement: Lantern moved [MV-01]\nT2 c-910 Tamsin Harlow: no enemy to fight -> random walk 5,4 -> 7,7 (OV-10)\n")
    off_log = "T1 TP-02 info/movement: Lantern moved [MV-01]\nT2 TP-02 info/movement: Lantern moved [MV-01]\n"
    return case("trace_scope", ["log_all", "log_off", "snapshot"], {"what": "fixture ai_trace all vs off"}, log_all=all_log, log_off=off_log, snapshot=snap)


ECHOES = [{"line": "help fighting", "handled": "T1", "summary": "Fighting: fire, peace, escape"}, {"line": "status", "handled": "T1", "summary": "Harrow Vey  turn 0  seed 1234"}]


def echo_harmless() -> dict:
    hashes = ["a" * 40, "b" * 40]
    obs_echo = {"hello": {"turn_no": 0, "seed": 1234}, "hashes": list(hashes), "echo_topics": [], "resolved": [1, 2], "notes": []}
    obs_plain = {"hello": {"turn_no": 0, "seed": 1234}, "hashes": list(hashes), "echo_topics": [], "resolved": [1, 2], "notes": []}
    log = log_text([(1, "move +3 +0")]) + "T0 you > help fighting: Fighting: fire, peace, escape\nT0 you > status: Harrow Vey  turn 0  seed 1234\n"
    return case("echo_harmless", ["obs_echo", "obs_plain", "log_text"], {"echoes": ECHOES, "turns": 2, "what": "fixture command.echo"}, obs_echo=obs_echo, obs_plain=obs_plain, log_text=log)


# --------------------------------------------------------------------------- smoke/52 — eligibility & placement

def eligible() -> dict:
    t1 = {"events": [f"Eligible for the {ITEM_NAME}: it lies in sector {LODE_KEY}, 66 sectors away, guarded by 4 ships (EG-01…04, SP-08)"],
          "player": at(HOME, CELL, fear=95, respect=95, gold=100, silver=0, items={"orichalcum": 1, "adamantine": 1, "aetherite": 1}), "lodestone": lodestone_row(), "captains": 1}
    snap = m5_snapshot(1, guardians=True, lodestone=lodestone_row(), respect=95, items={"orichalcum": 1, "adamantine": 1, "aetherite": 1}, captain={"fear": 95, "gold": 100, "silver": 0})
    return case("eligible", ["report", "snapshot"], {"threshold": 64, "guard_ships": 4, "torpedo_range": 5, "galaxy_size": [16, 16], "eligible_turn": 1, "what": "fixture eligible"},
                report=m5_report([t1], final={"captains": 1, "captains_start": 1, "population_min": 1, "population_max": 1}), snapshot=snap)


def not_eligible() -> dict:
    turns = [{"player": at(HOME, CELL, fear=95, respect=85, gold=100, silver=0, items={"orichalcum": 1, "adamantine": 1, "aetherite": 1})} for _ in range(20)]
    return case("not_eligible", ["report"], {"turns": 20, "why": "respect 85 < respect_min 90", "what": "fixture not eligible"}, report=m5_report(turns))


def progress_render() -> dict:
    text = "Harrow Vey · turn 20 · seed 1234\ngold 100  silver 0\nfear 95/100  respect 85/100\nMastery: wealth ✓ · fear ✓ · respect 85/90 · specials 3/3"
    return case("progress_render", ["text"], {"fractions": {"respect": [85, 90]}, "what": "fixture progress line"}, text=text)


def lodestone_render() -> dict:
    text = f"Harrow Vey · turn 1 · seed 1234\ngold 100  silver 0\nLodestone: {LODE_KEY} · guardians 4 alive"
    return case("lodestone_render", ["text"], {"key": LODE_KEY, "alive": 4, "name": ITEM_NAME, "what": "fixture lodestone line"}, text=text)


# --------------------------------------------------------------------------- smoke/53 — guardians

def placed_snapshot(turn_no: int, revealed: bool, **kw) -> dict:
    snap = m5_snapshot(turn_no, guardians=True, lodestone=lodestone_row(), **kw)
    item = {"id": "i-lode", "cell": list(LODE_CELL), "rarity": "special", "revealed": revealed}
    if revealed:
        item.update(element="lodestone", units=1, ultimate=True)
    snap["sectors"][LODE_KEY] = {"kind": "system", "terrain": [{"kind": "star", "cell": [5, 5], "colour": "yellow", "bucket": "medium"}, {"kind": "planet", "cell": [6, 6], "name": "Oreth"}],
                                 "items": [item]}
    snap["galaxies"]["7,1"]["visited"] = [[9, 12]]
    snap["ships"][PSHIP]["position"] = [LODE_GALAXY, LODE_SECTOR, list(LODE_CELL)]
    return snap


def placed_sector() -> dict:
    return case("placed_sector", ["snapshot"], {"key": LODE_KEY, "cell": LODE_CELL, "what": "fixture placed sector"}, snapshot=placed_snapshot(4, True))


def guardian_turns(*, peace_at: int | None = None) -> list[dict]:
    """t1 idle, t2 warp + every guardian in range fires, t3 move to the item, t4..t12 every guardian fires; the gate's one torpedo a turn from t4 lands 15 each (hit base 1)."""
    sides_of = {sid: [PID, cid] for cid, sid in GUARDS.items()}
    turns: list[dict] = [{"player": at(HOME, CELL, fear=95, respect=95, gold=100, silver=0, shield_max=5000.0, shield_current=5000.0), "lodestone": lodestone_row(), "captains": 1}]
    shields = {sid: 100.0 for sid in GUARDS.values()}
    pshield = 5000.0
    for n in range(2, 13):
        cell = [11, 11] if n == 2 else list(LODE_CELL)
        rows, bts = [], []
        for cid, sid in GUARDS.items():
            if n == 2 and sid not in ("s-951", "s-952"):
                continue
            pshield -= 15.0
            rows.append(ex(sid, PSHIP, "torpedo", 2, True, 15.0, pshield))
            if n >= 4 and sid == "s-951" and n <= 8:
                shields[sid] -= 15.0
                rows.append(ex(PSHIP, sid, "torpedo", 2, True, 15.0, shields[sid]))
        for cid, sid in GUARDS.items():
            mine = [r for r in rows if sid in (r["attacker"], r["defender"])]
            if mine:
                bts.append(battle(sides_of[sid], 2, cid, {PID: 20135.0, cid: 520.0}, mine, bid=f"b-{sid[-3:]}"))
        events = []
        if n == 9:
            events.append("script order 'pickup lodestone' rejected: the guardians still hold the sector (CM-13)")
        if peace_at is not None and n == peace_at:
            events.append("Warden 1 offers peace to Harrow Vey (OUT-05)")
            for b in bts:
                if b["sides"][1] == "c-951":
                    b["ended"], b["victor"] = "peace", PID
        turns.append({"battles": bts, "events": events,
                      "player": {"position": [LODE_GALAXY, LODE_SECTOR, cell], "fear": 95, "respect": 95, "gold": 100, "silver": 0, "shield_max": 5000.0, "shield_current": pshield,
                                 "in_battle": True},
                      "lodestone": lodestone_row(), "captains": 1, "void_visits": False})
    return turns


def guardians() -> dict:
    turns = guardian_turns()
    snap = placed_snapshot(12, True, captain={"fear": 95, "gold": 100, "silver": 0}, respect=95, ship={"shield_max": 5000.0, "shield_current": 4000.0})
    return case("guardians", ["report", "snapshot"], {"guardians": dict(GUARDS), "sector": LODE_KEY, "arrival_turn": 2, "close_turn": 4, "low_turn": 8, "pickup_turn": 9,
                                                      "until_turn": 12, "target_ship": "s-951", "low_shields": 25.0, "ratio": 0.026, "ratio_min": 1.3, "fear": 95, "fear_max": 50,
                                                      "what": "fixture guardians"}, report=m5_report(turns, final={"captains": 1, "captains_start": 1}), snapshot=snap)


def guardian_trace() -> dict:
    log = ("T2 c-951 Warden 1: guardian of the Lodestone, Lantern in the sector -> fires torpedo at s-194 regardless of ratio or fear (CM-13, D-158)\n"
           "T2 c-952 Warden 2: guardian, Lantern in the sector -> fires torpedo at s-194 (CM-13, D-158)\nT2 TP-04 alert/combat: Iron Warden 1 → Lantern: torpedo hit [OUT-04]\n")
    return case("guardian_trace", ["log_text"], {"guardians": dict(GUARDS), "what": "fixture guardian trace"}, log_text=log)


# --------------------------------------------------------------------------- smoke/54 — mastery

def mastery_turns() -> list[dict]:
    one = {"c-951": "s-951"}
    base = {"fear": 95, "respect": 95, "gold": 100, "silver": 0, "shield_max": 400.0, "shield_current": 400.0}
    t1 = {"player": {"position": [HOME[0], HOME[1], list(CELL)], **base}, "lodestone": lodestone_row(1, guardians=list(one)), "captains": 2}
    t2 = {"player": {"position": [LODE_GALAXY, LODE_SECTOR, [11, 11]], **dict(base, shield_current=385.0), "in_battle": True},
          "battles": [battle([PID, "c-951"], 2, "c-951", {PID: 15535.0, "c-951": 520.0}, [ex("s-951", PSHIP, "torpedo", 2, True, 15.0, 385.0)])],
          "lodestone": lodestone_row(1, guardians=list(one)), "captains": 2}
    kill = [ex(PSHIP, "s-951", "torpedo", 1, True, 15.0, 100.0 - 15.0 * (i + 1)) for i in range(7)]
    t3 = {"player": {"position": [LODE_GALAXY, LODE_SECTOR, list(LODE_CELL)], **dict(base, shield_current=385.0), "in_battle": False},
          "battles": [battle([PID, "c-951"], 2, "c-951", {PID: 15535.0, "c-951": 520.0}, kill, ended="destroyed", victor=PID, transfer={"gold": 0, "silver": 0, "items": {}, "pct": 100})],
          "destroyed": [{"ship": "s-951", "captain": "c-951", "cause": "combat", "by": PSHIP, "cell": [14, 11]}], "events": ["Iron Warden 1 destroyed (OUT-11)"],
          "lodestone": lodestone_row(0, guardians=list(one)), "captains": 2}
    m = {"fear": 100, "respect": 100, "gold": 100, "silver": 0, "shield_max": 100.0, "shield_current": 100.0, "food": 26, "water": 26}
    t4 = {"player": {"position": [LODE_GALAXY, LODE_SECTOR, list(LODE_CELL)], **m}, "events": [f"★ MASTER OF STARBOUR ★ Harrow Vey holds the {ITEM_NAME} (OUT-15)"],
          "pickups": [{"captain": PID, "element": "lodestone", "units": 1, "rarity": "ultimate"}], "lodestone": lodestone_row(0, 4, guardians=list(one)), "master": True, "captains": 2}
    turns = [t1, t2, t3, t4]
    for n in range(5, 15):
        p = {"position": [HOME[0], HOME[1], list(PLANET_CELL) if n >= 7 else [11, 11]], **m}
        t: dict = {"player": p, "lodestone": lodestone_row(0, 4, guardians=list(one)), "master": True, "captains": 2}
        if n == 5:
            t["warps"] = [{"ship": PSHIP, "from": [LODE_GALAXY, LODE_SECTOR], "to": HOME, "factor": 10.0, "effective": 10.0}]
        if n >= 9:
            p["food"] = 526
        if n == 9:
            t["trades"] = [trade(PLANET, "buy", {}, {"food": 500}, factor=0.8)]
            t["events"] = [f"Bought 500 food from {PLANET_NAME} for 0 silver (OUT-07)"]
        if n == 10:
            t["battles"] = [battle([PID, SPAWN_C], 10, PID, {PID: 1735.0, SPAWN_C: 3470.0}, [ex(PSHIP, SPAWN_S, "torpedo", 1, True, 15.0, 3200.0)], ended="peace", victor=PID,
                                   transfer={"gold": 0, "silver": 0, "items": {}, "pct": 0})]
            t["captures"] = [{"ship": SPAWN_S, "from": SPAWN_C, "to": PID}]
            t["events"] = ["Oriel Vance yields the Cinder Lark to the Master (FL-02, EF-06)"]
            p["ships"] = 2
        turns.append(t)
    return turns


def mastery() -> dict:
    spawns = [{"ship": SPAWN_S, "captain": SPAWN_C, "class": "corsair", "cell": [7, 5]}]
    snap = m5_snapshot(14, master=True, lodestone=lodestone_row(0, 4, guardians=["c-951"]), player_cell=PLANET_CELL, spawns=spawns, planets={PLANET: planet_record()},
                       ships_owned=[PSHIP, SPAWN_S], captain={"gold": 100, "silver": 0, "food": 526, "water": 26})
    snap["ships"][SPAWN_S]["captain"] = PID
    snap["captains"].pop(SPAWN_C, None)
    rep = m5_report(mastery_turns(), spawned=spawns, final={"guardians_destroyed": 1, "captains": 2, "captains_start": 2})
    return case("mastery", ["report", "snapshot"], {"mastery_turn": 4, "kill_turn": 3, "buy_turn": 9, "buy_n": 500, "buy_factor": 0.8, "planet_id": PLANET, "fire_turn": 10,
                                                    "spawn": {"captain": SPAWN_C, "ship": SPAWN_S}, "caps": CAPS, "master_torpedoes": 99, "turns": 14, "what": "fixture mastery"},
                report=rep, snapshot=snap)


def ceremony_probe() -> dict:
    import random
    rng = random.Random(7)
    frames = []
    for _ in range(3):
        rows = [[" "] * 120 for _ in range(20)]
        for _ in range(60):
            rows[rng.randrange(20)][rng.randrange(120)] = rng.choice("*+.o")
        frames.append("\n".join("".join(r).rstrip() for r in rows))
    title = "\n".join(["███ ███ ███ ███ ███ ███", "█ █ █ █ █   █   █   █ █", "███ ███ ███ ███ ███ ███", "█ █ █ █   █ █   █   █ █", "█ █ █ █ ███ ███ ███ █ █", "MASTER OF STARBOUR"])
    probe = {"ceremony": {"frames": frames, "title": title, "render": f"[TP-05] ★ MASTER OF STARBOUR ★ Harrow Vey holds the {ITEM_NAME}\n" + frames[0]}, "errors": {}}
    return case("ceremony_probe", ["probe"], {"width": 120, "height": 20, "n": 3, "what": "fixture ceremony"}, probe=probe)


def master_render() -> dict:
    text = "★ MASTER OF STARBOUR ★  Harrow Vey · turn 14 · seed 1234\ngold ∞  silver ∞\nfood ∞  water ∞\nfear 100/100  respect 100/100"
    return case("master_render", ["text"], {"infinity": "∞", "what": "fixture master header"}, text=text)


def master_resume() -> dict:
    obs = {"hello": {"turn_no": 14, "seed": 1234}, "master": True, "topics_after": {"state.hello": 2}, "mastery_seen": False, "notes": []}
    return case("master_resume", ["obs"], {"what": "fixture master resume"}, obs=obs)


# --------------------------------------------------------------------------- smoke/12 ext. and --long

def trade_turns(n: int, *, restock_every: int = 40, mutinies_at: tuple[int, ...] = ()) -> list[dict]:
    from make_fixtures_m4 import ai_turns
    turns = ai_turns(n)
    food = 30
    for no, t in enumerate(turns, start=1):
        food = max(0, food - 1)
        if no % restock_every == 0:
            food += 25
        if no in mutinies_at:
            food = 10
        t["player"] = dict(t.get("player") or {}, food=food, water=food)
        t["void_visits"] = False
    return turns


def trade_player() -> dict:
    turns = trade_turns(500, mutinies_at=(230,))
    rep = m5_report(turns, final={"ai_mutinies": 6, "mutinies": [{"turn_no": 230, "captain": "player", "result": "demoted"}]})
    return case("trade_player", ["report"], {"turns": 500, "what": "fixture --ai-player trade"}, report=rep)


def long_tier() -> dict:
    turns = trade_turns(300)
    for i, t in enumerate(turns):
        t["resolve_ms"] = 40.0 + (i % 7)
    rep = m5_report(turns, final={"ai_mutinies": 3})
    for t, spec in zip(rep["per_turn"], turns):
        t["resolve_ms"] = spec["resolve_ms"]
    return case("long_tier", ["report"], {"turns": 300, "peak_rss_mb": 420.0, "rss_limit_mb": 1024.0, "what": "fixture --long tier"}, report=rep)


# --------------------------------------------------------------------------- live/11 — the mastery driver's observations

def mastery_bus() -> dict:
    obs = {"hello": {"turn_no": 0, "seed": 1234}, "player_id": PID, "ship_id": PSHIP, "lodestone": lodestone_row(1, guardians=["c-901"], sector=KEY, cell=[14, 11]),
           "warp_reply": {"topic": "order.accepted", "reason": "", "text": "warp queued"}, "arrived": True, "move_reply": {"topic": "order.accepted", "reason": "", "text": "move queued"},
           "at_cell": True, "scan_reply": {"topic": "order.accepted", "reason": "", "text": f"Scan: 1 item(s) in this cell: {ITEM_NAME}"},
           "fire_replies": [{"topic": "order.accepted", "reason": "", "text": "fire torpedo at s-901 queued"}], "kill_turn": 4, "guardians_alive": 0,
           "pickup_reply": {"topic": "order.accepted", "reason": "", "text": "pickup lodestone queued"}, "mastery": {"turn_no": 5, "captain": "Harrow Vey", "item": ITEM_NAME},
           "mastery_wait_s": 0.05, "mastery_turn": 5, "resolved_types": ["movement", "mastery", "upkeep"], "master_flag": True, "after_mastery": None, "extra_turn": 6, "notes": []}
    return case("mastery_bus", ["obs"], {"what": "fixture mastery bus"}, obs=obs)


def ceremony_heartbeat() -> dict:
    seen = {"turn_no": 5, "waited_s": 0.1, "wait_s": 3.0, "problems": [], "render_ms": {"grid": 1, "output": 1, "status": 1}, "running_after_s": 0.2, "done_after_s": 6.4, "master": True}
    return case("ceremony_heartbeat", ["seen"], {"ceremony_seconds": 6.0, "what": "fixture ceremony heartbeat"}, seen=seen)


# --------------------------------------------------------------------------- assembly

def good_m5() -> dict:
    return {"deep_space_warp": deep_space_warp(), "void_sector_snapshot": void_sector_snapshot(), "void_render": void_render(), "voids_listing": voids_listing(),
            "void_item": void_item(), "ai_void_visits": ai_void_visits(), "help_render": help_render(), "gameplay_log": gameplay_log(), "trace_scope": trace_scope(),
            "echo_harmless": echo_harmless(), "eligible": eligible(), "not_eligible": not_eligible(), "progress_render": progress_render(), "lodestone_render": lodestone_render(),
            "placed_sector": placed_sector(), "guardians": guardians(), "guardian_trace": guardian_trace(), "mastery": mastery(), "ceremony_probe": ceremony_probe(),
            "master_render": master_render(), "master_resume": master_resume(), "trade_player": trade_player(), "long_tier": long_tier(), "mastery_bus": mastery_bus(),
            "ceremony_heartbeat": ceremony_heartbeat()}


def broken_m5(good: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}

    c = copy.deepcopy(good["deep_space_warp"])          # smoke/19 — the warp into a Void cell is still rejected
    t1 = c["report"]["per_turn"][0]
    t1["warps"], t1["void_visits"] = [], False
    t1["events"] = ["script order 'warp 3 4 5 9' rejected: destination 3,4/5,9 is Void - there is no sector there (CMD-07, WG-02)"]
    t1["player"]["position"] = [HOME[0], HOME[1], [11, 11]]
    c["report"]["per_turn"][1]["player"]["position"] = [HOME[0], HOME[1], [11, 11]]
    c["report"]["per_turn"][1]["events"] = ["script order 'scan' accepted: Scan: nothing to pick up in cell 11,11"]
    c["report"]["per_turn"][2]["warps"] = []
    out["sim_void_rejected"] = c

    c = copy.deepcopy(good["void_item"])                # smoke/49 — the stack is back after leaving and re-entering
    el = VOID_ITEM["element"]
    c["report"]["per_turn"][5]["events"] = [f"script order 'scan' accepted: Deep space — {el} (2 units, normal) lies here (OUT-10)"]
    c["snapshot"]["sectors"][VOID_KEY]["items"] = [{"id": "i-void", "cell": list(VOID_ITEM["cell"]), "rarity": "normal", "revealed": True, "element": el, "units": 2}]
    out["sim_void_item_respawns"] = c

    c = copy.deepcopy(good["help_render"])              # smoke/50 — the 80-column help is the wide render cropped (the M4 bug)
    wide = c["renders"]["all@400"]
    c["renders"]["all@80"] = "\n".join((ln[:79] + "…") if len(ln) > 80 else ln for ln in wide.splitlines())
    out["sim_help_truncated"] = c

    c = copy.deepcopy(good["gameplay_log"])             # smoke/51 — the log keeps the orders but not the lines T1 answered locally
    c["log_text"] = log_text([(1, "move +3 +0"), (2, "warp 8 8 0 0")])
    out["sim_log_missing_commands"] = c

    c = copy.deepcopy(good["eligible"])                 # smoke/52 — the item lands one sector away
    near = lodestone_row(distance=1, sector="3,4/8,9")
    c["report"]["per_turn"][0]["lodestone"] = near
    c["report"]["per_turn"][0]["events"] = [f"Eligible for the {ITEM_NAME}: it lies in sector 3,4/8,9, 1 sector away (EG-01…04)"]
    c["snapshot"]["lodestone"] = near
    for sid in GUARDS.values():
        c["snapshot"]["ships"][sid]["position"] = [HOME[0], [8, 9], GUARD_CELLS[sid]]
    out["sim_lodestone_too_near"] = c

    c = copy.deepcopy(good["guardians"])                # smoke/53 — the worn-down guardian sues for peace
    turns = guardian_turns(peace_at=9)
    c["report"] = m5_report(turns, final={"captains": 1, "captains_start": 1})
    out["sim_guardian_offers_peace"] = c

    c = copy.deepcopy(good["mastery"])                  # smoke/54 — the Master keeps the temp class's stats instead of the caps
    c["snapshot"]["ships"][PSHIP].update(shield_max=400.0, shield_current=400.0, torpedoes=969)
    for t in c["report"]["per_turn"][3:]:
        t["player"].update(shield_max=400.0, shield_current=400.0)
    out["sim_mastery_stats_not_maxed"] = c

    c = copy.deepcopy(good["mastery"])                  # smoke/54 — the buy is charged
    t9 = c["report"]["per_turn"][8]
    t9["trades"][0]["gave"] = {"silver": 800}
    for t in c["report"]["per_turn"][8:]:
        t["player"].update(gold=60, silver=0)
    c["snapshot"]["captains"][PID].update(gold=60, silver=0)
    out["sim_mastery_wealth_deducted"] = c

    c = copy.deepcopy(good["master_resume"])            # smoke/54 — loading the save replays the ceremony
    c["obs"]["mastery_seen"] = True
    c["obs"]["topics_after"] = {"state.hello": 2, "game.mastery": 1}
    out["sim_master_replays_ceremony"] = c

    c = copy.deepcopy(good["trade_player"])             # smoke/12 ext. — the 'trading' player starves every 33 turns and never restocks
    muts = [{"turn_no": 33 * i, "captain": "player", "result": "demoted"} for i in range(1, 16)]
    c["report"]["final"]["mutinies"] = muts
    food = 30
    for t in c["report"]["per_turn"]:
        n = t["turn_no"]
        food = 10 if n in {33 * i for i in range(1, 16)} else max(0, food - 1)
        t["player"]["food"] = t["player"]["water"] = food
    out["sim_ai_player_trade_starves"] = c
    return out
