"""verify/_endgame.py — M5 checkers shared by smoke_test.py and live_test.py (specs/M5.md § 4, CONTRACTS C12).

Same split as _checks.py, _combat.py and _economy.py: **pure checkers** (functions of a C2+…+C12 report, a
snapshot, bus observations, a gameplay log or rendered text → a list of problem strings) and **thin I/O
adapters** (the bus mastery driver) with no pass/fail logic.  Scenarios are *plans* built from world_index,
`--world-index voids`, the probe snapshot and the ini plus the debug flags (`--give`, `--coins`, `--respect`,
`--fear`, `--spawn`, `--teleport`, temp inis); every number comes from the ini the gate was handed, never a
literal (M5 § 4).

Conventions the gate relies on, stated in every message that uses them (D-162, M5 § 4a):
  * a deep-space scan is an OUT-10 event whose text contains 'deep space'; `--world-index voids` entries are
    {sector: 'g,g/s,s', item: null | {element, units, rarity, cell}} and the void_item_pct share is judged on the
    listings of three seeds pooled (one galaxy's ~50 sectors would make ±10 points a coin toss);
  * `help` is probed through `render_command.render_help(topic, width, cfg, snapshot)` printed by a Rich Console of
    that width: a command row is a line whose first token is the command name followed by the When cell (`now` /
    `at end`); the 80-column render must keep every word of a 400-column render (wrapping, never cropping);
  * the gameplay log's player lines read `T<n> you > <line>: <outcome>` exactly as C12 says; `queued` / `rejected:`
    / the reply's first line; the engine logs a command.echo as such a line and answers it with nothing;
  * eligibility and placement are judged from the report's `lodestone` row and `final.eligible_turn`; the placed
    sector's terrain is read in the row that visits it (smoke/53), since a snapshot lists only visited sectors;
  * guardians are tested with torpedo_hit_base 1 and torpedo_evade_per_engine 0 in a temp ini so the damage the
    gate deals is deterministic; the guardians fire at a fear ≥ attack_fear_max *and* a ratio < attack_ratio_min, which
    CM-10 would refuse on both counts (the spec's 'fear of 0' tests only the ratio waiver);
  * a Master's stats are read from the final save (the per-turn row carries shields only); the T3 title frame must
    carry the words MASTER OF STARBOUR in its plain text (block letters alone cannot be read back);
  * `--ai-player trade` runs allow at most 2 player mutinies (D-161 [ASSUMED]) and need one restock (food or water
    rising on a turn without a mutiny).
"""
from __future__ import annotations

import json
import re
import statistics
import time
from collections import Counter
from typing import Any, Callable

from _checks import (BusClient, BusObservations, _close, _get, _norm, _short, canonical_hash, chebyshev, event_text, flat_sector, player_ship,
                     report_shape_errors)
from _combat import REJECTED_RULE, _prefix, _turns, battles_at, events_mentioning, exchanges_at
from _economy import _int, items_of, player_id, sector_items, sector_terrain, trades_at, turn_no, wealth

DEEP_SPACE_RULE = "a deep-space scan is an OUT-10 event whose text contains 'deep space' (D-153, D-156; D-162)"
VOIDS_RULE = "--world-index voids lists {sector: 'g,g/s,s', item: null | {element, units, rarity, cell}} for the start galaxy (C12; D-162)"
HELP_ROW_RULE = "a help command row is a line whose first token is the command name followed by the When cell 'now' / 'at end' (CMD-01, D-154; D-162)"
LOG_LINE_RULE = "a player line in logs/game-<seed>.log reads 'T<n> you > <line>: <outcome>' (C12, D-155, D-160)"
GUARDIAN_RULE = "guardians are judged with torpedo_hit_base 1 / torpedo_evade_per_engine 0 (deterministic damage) at a fear ≥ attack_fear_max and a ratio < attack_ratio_min (CM-13, D-158; D-162)"
TRADE_PLAYER_RULE = "an --ai-player trade run allows ≤ 2 player mutinies and needs one restock (D-161 [ASSUMED]; D-162)"
LODESTONE_KEYS = ("sector", "cell", "distance", "placed_turn", "guardians", "guardians_alive", "picked_up_turn")
HELP_GROUPS = ("about", "looking", "fighting", "trading", "game")
BOX = "│|┃╎┆┊║"
_ROW = re.compile(rf"^[\s{BOX}]*(?P<name>[a-z?]+)[\s{BOX}]+(?P<when>now|at end)\b", re.I)
_DECISION = re.compile(r"^T(\d+) (c-\d+) [^:]*: ")
_YOU = re.compile(r"^T(\d+) you > (.*?): (.*)$")
_SECTOR_KEY = re.compile(r"(\d+)\s*,\s*(\d+)\s*/\s*(\d+)\s*,\s*(\d+)")


# --------------------------------------------------------------------------- accessors (pure)

def sector_key(galaxy: Any, sector: Any) -> str:
    return f"{int(galaxy[0])},{int(galaxy[1])}/{int(sector[0])},{int(sector[1])}"


def parse_sector_key(key: Any) -> tuple | None:
    m = _SECTOR_KEY.search(str(key or ""))
    return ((int(m.group(1)), int(m.group(2))), (int(m.group(3)), int(m.group(4)))) if m else None


def keys_in(text: str) -> set[str]:
    """Every 'g,g/s,s' sector key written in a text (spaces around the slash allowed)."""
    return {f"{a},{b}/{c},{d}" for a, b, c, d in _SECTOR_KEY.findall(text or "")}


def lodestone_at(turn: dict) -> dict | None:
    v = turn.get("lodestone")
    return v if isinstance(v, dict) else None


def guardians_of(snapshot: dict | None) -> dict[str, dict]:
    return {cid: c for cid, c in ((snapshot or {}).get("captains") or {}).items() if isinstance(c, dict) and c.get("guardian") is True}


def special_elements(elements: dict) -> list[str]:
    """The special-rarity element names of [elements], sorted (EL-09…12, D-157)."""
    return sorted(n for n, e in elements.items() if str(e.get("rarity", "")).lower() == "special")


def eligibility_coins(wealth_min_silver: int, silver_per_gold: int) -> tuple[int, int]:
    """Gold that clears wealth_min_silver on its own (EG-01, CFG-50)."""
    return (int(-(-int(wealth_min_silver) // max(1, int(silver_per_gold)))), 0)


def placement_threshold(universe_size: Any, galaxy_size: Any, pct: float) -> int:
    """SP-08 / CFG-87: placement_distance_pct % of the universe diameter (universe × galaxy, flattened)."""
    diameter = max(int(universe_size[0]) * int(galaxy_size[0]), int(universe_size[1]) * int(galaxy_size[1]))
    return int(-(-diameter * float(pct) // 100))


def player_sector(turn: dict) -> tuple | None:
    pos = (turn.get("player") or {}).get("position")
    try:
        return (tuple(int(v) for v in pos[0]), tuple(int(v) for v in pos[1]))
    except (TypeError, ValueError, IndexError):
        return None


def as_gs(gs: Any) -> tuple | None:
    """((gx, gy), (sx, sy)) from a tuple, a list pair or a 'g,g/s,s' key."""
    if isinstance(gs, str):
        return parse_sector_key(gs)
    try:
        return (tuple(int(v) for v in gs[0]), tuple(int(v) for v in gs[1]))
    except (TypeError, ValueError, IndexError):
        return None


def player_cell(turn: dict) -> tuple | None:
    pos = (turn.get("player") or {}).get("position")
    try:
        return (int(pos[2][0]), int(pos[2][1]))
    except (TypeError, ValueError, IndexError):
        return None


def deep_space_texts(turn: dict) -> list[str]:
    return [t for t in (event_text(e) for e in (turn.get("events") or [])) if "deep space" in t.lower()]


def words(text: str) -> Counter:
    """Alphanumeric tokens of a render, box-drawing and punctuation dropped (wrapping keeps them, cropping loses them)."""
    return Counter(re.findall(r"[A-Za-z0-9_<>+\-]+", text or ""))


def command_rows(text: str) -> Counter:
    """HELP_ROW_RULE: command name → number of rows."""
    out: Counter = Counter()
    for ln in (text or "").splitlines():
        m = _ROW.match(ln)
        if m:
            out[m.group("name").lower()] += 1
    return out


def long_lines(text: str, width: int) -> list[str]:
    return [ln for ln in (text or "").splitlines() if len(ln.rstrip()) > width]


def you_lines(log_text: str) -> list[tuple[int, str, str]]:
    """LOG_LINE_RULE: (turn, line, outcome) for every player line of a gameplay log."""
    out = []
    for ln in (log_text or "").splitlines():
        m = _YOU.match(ln.rstrip())
        if m:
            out.append((int(m.group(1)), m.group(2), m.group(3)))
    return out


def decision_captains(log_text: str) -> Counter:
    out: Counter = Counter()
    for ln in (log_text or "").splitlines():
        m = _DECISION.match(ln)
        if m:
            out[m.group(2)] += 1
    return out


def rejected_reason(turn: dict, line: str) -> str | None:
    """The reason the sim recorded for a rejected script line: "script order '<line>' rejected: <reason>"."""
    for t in events_mentioning(turn, "rejected"):
        i = t.find("rejected:")
        if line.lower() in t.lower() and i >= 0:
            return t[i + len("rejected:"):].strip()
    return None


# --------------------------------------------------------------------------- smoke/19 (amended) — deep space is a destination (CMD-07, D-153)

def check_deep_space_warp(report: dict, *, void: tuple, start: tuple, warp_turn: int, scan_turn: int, back_turn: int, what: str) -> list[str]:
    """turn warp_turn: `warp` into a Void coordinate within reach arrives (warps[].to, position); scan_turn: the scan says deep
    space (OUT-10); back_turn: a warp back arrives at the start sector; `void_visits` true on the arrival turn (C12)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    void, start = as_gs(void), as_gs(start)
    vk, sk = sector_key(*void), sector_key(*start)
    t = turn_no(report, warp_turn)
    rows = [w for w in (t.get("warps") or []) if isinstance(w, dict) and _norm(w.get("to")) == _norm([list(void[0]), list(void[1])])]
    if not rows:
        rej = events_mentioning(t, "rejected")
        hint = f"; rejected: {rej[0][:140]!r}" if rej else f"; warps {_short(t.get('warps'))}"
        errs.append(f"{what}: turn {warp_turn}: no warps[] row to the deep-space sector {vk}{hint} — a Void cell is a valid destination like any other "
                    f"(CMD-07, WG-02, D-153)")
    if player_sector(t) != void:
        errs.append(f"{what}: turn {warp_turn}: the player is at {player_sector(t)}, expected the deep-space sector {vk} (MV-05, D-153)")
    vv = t.get("void_visits")
    if vv is not True:
        errs.append(f"{what}: turn {warp_turn}: void_visits = {vv!r}, expected true the turn the player enters a deep-space sector (C12 report additions)")
    ts = turn_no(report, scan_turn)
    if not deep_space_texts(ts):
        errs.append(f"{what}: turn {scan_turn}: no event names deep space after `scan` (events {[event_text(e)[:60] for e in (ts.get('events') or [])][:5]}); "
                    f"OUT-10: 'Deep space — nothing here' or the stack ({DEEP_SPACE_RULE})")
    tb = turn_no(report, back_turn)
    if player_sector(tb) != start:
        errs.append(f"{what}: turn {back_turn}: the player is at {player_sector(tb)}, expected back at {sk} after the return warp (CMD-07, MV-02)")
    if tb.get("void_visits") is True:
        errs.append(f"{what}: turn {back_turn}: void_visits = true on the turn the player returned to a system (C12)")
    return errs


def check_void_sector_snapshot(snapshot: dict | None, *, key: str, what: str) -> list[str]:
    """The sector the ship stands in is listed with kind 'void' and no terrain (C12 snapshot additions, WG-02)."""
    if not isinstance(snapshot, dict):
        return [f"{what}: no save snapshot (C6)"]
    sec = (snapshot.get("sectors") or {}).get(key)
    if not isinstance(sec, dict):
        return [f"{what}: the saved snapshot lists no sectors[{key!r}] although the player's ship is there — a deep-space sector the ship stands in is "
                f"listed with kind 'void' (C12, D-156)"]
    errs: list[str] = []
    if sec.get("kind") != "void":
        errs.append(f"{what}: sectors[{key}].kind = {sec.get('kind')!r}, expected 'void' (C12: sectors[*].kind = 'system' | 'void')")
    terrain = sec.get("terrain")
    if terrain:
        errs.append(f"{what}: sectors[{key}].terrain = {_short(terrain)}, expected none — deep space has no star, planet or hazard (D-153, WG-02)")
    return errs


def check_void_render(grid_text: str | None, glyph: str, cell: Any, status_text: str | None, key: str, what: str) -> list[str]:
    """T2 draws the deep-space sector with the ship at its cell; T4's location line says 'Deep space g,g/s,s' (C4, PNL-01, D-156)."""
    from _checks import glyph_positions
    errs: list[str] = []
    if not isinstance(grid_text, str) or not grid_text.strip():
        errs.append(f"{what}: render_grid produced no text for the deep-space sector (C4)")
    elif glyph not in grid_text:
        errs.append(f"{what}: render_grid's sector view shows no active-ship glyph {glyph!r} in a deep-space sector (SYM-10, D-153)")
    if not isinstance(status_text, str) or not status_text.strip():
        errs.append(f"{what}: render_status produced no text for the deep-space snapshot (C4)")
    else:
        low = status_text.lower()
        if "deep space" not in low:
            errs.append(f"{what}: T4 does not say 'Deep space' for a ship in a deep-space sector (C4 / C12: location line 'Deep space g,g/s,s'; D-156)")
        elif key not in keys_in(status_text):
            errs.append(f"{what}: T4's location line does not name the sector {key} (C12: 'Deep space g,g/s,s')")
    return errs


# --------------------------------------------------------------------------- smoke/49 — deep space content (D-153, D-156, D-67, IT-05)

def voids_shape_errors(voids: Any, what: str) -> list[str]:
    if not isinstance(voids, list):
        return [f"{what}: world_index.voids is {type(voids).__name__}, expected a list ({VOIDS_RULE})"]
    errs: list[str] = []
    for i, v in enumerate(voids[:5]):
        if not isinstance(v, dict) or parse_sector_key(v.get("sector")) is None or "item" not in v:
            errs.append(f"{what}: world_index.voids[{i}] = {_short(v)}, expected {{sector: 'g,g/s,s', item: …}} ({VOIDS_RULE})")
            continue
        it = v.get("item")
        if it is not None and not (isinstance(it, dict) and isinstance(it.get("element"), str) and isinstance(it.get("units"), int) and it["units"] >= 1
                                   and str(it.get("rarity", "")).lower() in ("normal", "special") and isinstance(it.get("cell"), list) and len(it["cell"]) == 2):
            errs.append(f"{what}: world_index.voids[{i}].item = {_short(it)}, expected null or {{element, units ≥ 1, rarity normal|special, cell}} (C12, CFG-80, SP-03)")
    return errs


def check_voids_listing(listings: dict[int, tuple[list, list]], expected: dict[int, set[str]], *, pct: float, tolerance: float = 10.0, what: str) -> list[str]:
    """listings: seed → (voids run 1, voids run 2); expected: seed → the start galaxy's void sector keys from the snapshot.  Both runs identical
    (D-67), every expected sector listed once, and the pooled share of sectors with a stack within ±tolerance points of void_item_pct."""
    errs: list[str] = []
    total = with_item = 0
    expected = {str(k): set(v or ()) for k, v in expected.items()}
    for seed, (a, b) in sorted(listings.items(), key=lambda kv: str(kv[0])):
        seed = str(seed)
        errs += voids_shape_errors(a, f"{what} seed {seed}")
        if errs:
            return errs
        if canonical_hash(a) != canonical_hash(b):
            da = {v.get("sector"): v.get("item") for v in a if isinstance(v, dict)}
            db = {v.get("sector"): v.get("item") for v in b if isinstance(v, dict)}
            diff = [k for k in sorted(set(da) | set(db)) if da.get(k) != db.get(k)]
            errs.append(f"{what} seed {seed}: two `--world-index voids` listings differ at {diff[:4]} ({len(diff)} sector(s)) — deep-space content is "
                        f"random.Random(hash(seed, gx, gy, sx, sy)), never the world or in-game RNG (D-67, D-156)")
        keys = [str(v.get("sector")) for v in a if isinstance(v, dict)]
        dup = [k for k, n in Counter(keys).items() if n > 1]
        if dup:
            errs.append(f"{what} seed {seed}: voids lists {dup[:3]} more than once (C12)")
        want = expected.get(seed) or set()
        missing, extra = sorted(want - set(keys)), sorted(set(keys) - want)
        if missing or extra:
            errs.append(f"{what} seed {seed}: voids lists {len(keys)} sectors but the start galaxy has {len(want)} deep-space cells (missing {missing[:3]}, "
                        f"unexpected {extra[:3]}) — every galaxy cell without a sector is deep space (D-153, WG-02)")
        total += len(a)
        with_item += sum(1 for v in a if isinstance(v, dict) and v.get("item") is not None)
    if total:
        share = 100.0 * with_item / total
        if abs(share - float(pct)) > float(tolerance):
            errs.append(f"{what}: {with_item} of {total} deep-space sectors over seeds {sorted(listings)} hold a stack ({share:.1f} %), expected "
                        f"void_item_pct {pct:g} ± {tolerance:g} points (CFG-84, D-156; pooled over three seeds, D-162)")
    return errs


def check_void_item(report: dict, snapshot: dict | None, *, item: dict, key: str, el: str, blind_turn: int, scan_turn: int, pickup_turn: int, reenter_turn: int,
                    rescan_turn: int, what: str) -> list[str]:
    """Teleported onto a deep-space stack: `pickup` before a scan is rejected (IT-04), the scan names the element, the pickup lands in
    player.items, leaving and re-entering finds nothing (no respawn, IT-05), and the saved sector holds no stack at the cell (D-156)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    tb = turn_no(report, blind_turn)
    if not events_mentioning(tb, "rejected"):
        errs.append(f"{what}: turn {blind_turn}: `pickup {el}` before any scan was not rejected — items are never picked up unscanned (IT-04, CMD-15; {REJECTED_RULE})")
    ts = turn_no(report, scan_turn)
    if not any(el.lower() in t.lower() for t in deep_space_texts(ts) + events_mentioning(ts, el)):
        errs.append(f"{what}: turn {scan_turn}: no scan event names the stack {el!r} in deep space (events {[event_text(e)[:60] for e in (ts.get('events') or [])][:5]}; "
                    f"OUT-10, {DEEP_SPACE_RULE})")
    tp = turn_no(report, pickup_turn)
    rows = [r for r in (tp.get("pickups") or []) if isinstance(r, dict) and str(r.get("element")) == el]
    if not rows:
        errs.append(f"{what}: turn {pickup_turn}: pickups[] has no row for {el!r} ({_short(tp.get('pickups'))}) — the stack is picked up after the scan (CMD-15, D-156)")
    held = (tp.get("player") or {}).get("items")
    if not (isinstance(held, dict) and int(held.get(el, 0)) >= 1):
        errs.append(f"{what}: turn {pickup_turn}: player.items = {_short(held)}, expected {el!r} ≥ 1 after the pickup (C11 player.items)")
    tr = turn_no(report, reenter_turn)
    if tr.get("void_visits") is not True:
        errs.append(f"{what}: turn {reenter_turn}: void_visits = {tr.get('void_visits')!r}, expected true on re-entering the deep-space sector (C12)")
    if player_sector(tr) != parse_sector_key(key):
        errs.append(f"{what}: turn {reenter_turn}: the player is at {player_sector(tr)}, expected back in {key} (CMD-07)")
    trs = turn_no(report, rescan_turn)
    if any(el.lower() in t.lower() for t in events_mentioning(trs, el)) or (trs.get("pickups") or []):
        errs.append(f"{what}: turn {rescan_turn}: the scan after leaving and re-entering still names {el!r} — a removed deep-space stack never respawns "
                    f"(IT-05, D-156: removed stacks are recorded by coordinates)")
    if not deep_space_texts(trs):
        errs.append(f"{what}: turn {rescan_turn}: no deep-space scan event after re-entry ({DEEP_SPACE_RULE})")
    if isinstance(snapshot, dict):
        sec = (snapshot.get("sectors") or {}).get(key)
        if isinstance(sec, dict):
            left = [i for i in sector_items(snapshot, key) if _norm(i.get("cell")) == _norm(list(item["cell"]))]
            if left:
                errs.append(f"{what}: the saved sector {key} still lists a stack at {item['cell']} after the pickup: {_short(left)} (IT-05, C6: the removal survives save/load)")
            if sec.get("kind") != "void":
                errs.append(f"{what}: saved sectors[{key}].kind = {sec.get('kind')!r}, expected 'void' (C12)")
        items_now = items_of((snapshot.get("captains") or {}).get(snapshot.get("player_id")))
        if items_now.get(el, 0) < 1:
            errs.append(f"{what}: the saved captain holds no {el!r} after the pickup (C6, C11)")
    else:
        errs.append(f"{what}: no save snapshot after the run (C6)")
    return errs


def check_ai_void_visits(report_default: dict, report_zero: dict, *, turns_default: int, turns_zero: int, what: str) -> list[str]:
    """final.ai_void_visits ≥ 1 over an AI run with the ini's roam_void_pct; 0 with roam_void_pct = 0 in a temp ini (D-156, CFG-85)."""
    errs: list[str] = []
    for label, rep in (("default", report_default), ("roam_void_pct = 0", report_zero)):
        errs += _prefix(rep, f"{what} ({label})", 0)
    if errs:
        return errs
    d, z = _int(_get(report_default, "final", "ai_void_visits")), _int(_get(report_zero, "final", "ai_void_visits"))
    if d is None:
        errs.append(f"{what}: final.ai_void_visits = {_get(report_default, 'final', 'ai_void_visits')!r}, expected a count of AI arrivals in deep space (C12 final additions)")
    elif d < 1:
        errs.append(f"{what}: final.ai_void_visits = 0 over {turns_default} AI turns — roaming picks a deep-space destination with probability roam_void_pct (D-125, D-156, CFG-85)")
    if z is None:
        errs.append(f"{what}: with roam_void_pct = 0: final.ai_void_visits = {_get(report_zero, 'final', 'ai_void_visits')!r}, expected 0 (C12)")
    elif z != 0:
        errs.append(f"{what}: with [ai] roam_void_pct = 0 in a temp ini final.ai_void_visits = {z} over {turns_zero} turns, expected 0 — the knob is not live (CFG-85, D-49)")
    for label, rep in (("default", report_default), ("roam_void_pct = 0", report_zero)):
        bad = [t.get("turn_no") for t in _turns(rep) if not isinstance(t.get("void_visits"), bool)]
        if bad:
            errs.append(f"{what} ({label}): per_turn.void_visits missing or not a bool at turns {bad[:4]} (C12 report additions)")
    return errs


# --------------------------------------------------------------------------- smoke/50 — structured help (CMD-01, UX-09, D-154)

def check_help_render(renders: dict, *, widths: list[int], commands: list[str], group_words: tuple[str, ...] = HELP_GROUPS, fighting: tuple[str, ...] = ("fire", "peace", "escape"),
                      panel_topic: str = "warp", panel_ids: tuple[str, ...] = ("CMD-07",), wide: int = 400, what: str) -> list[str]:
    """renders: {'all@80': text, 'all@120': …, 'all@400': the wide reference, 'fighting@80': …, 'warp@80': …}.  At every width: no line longer than
    the width, no ellipsis, every word of the wide render kept, each catalogue command exactly one row, the five group titles; `help fighting`
    lists exactly fire, peace, escape; `help warp` is a panel with Syntax / Does / When / Example and its IDs."""
    errs: list[str] = []
    ref = renders.get(f"all@{wide}")
    if not isinstance(ref, str) or not ref.strip():
        return [f"{what}: render_help(None, {wide}, cfg, snapshot) produced no text (CMD-01, D-154, C4)"]
    ref_words = words(ref)
    for w in widths:
        for topic in ("all", "fighting", panel_topic):
            text = renders.get(f"{topic}@{w}")
            label = "help" if topic == "all" else f"help {topic}"
            if not isinstance(text, str) or not text.strip():
                errs.append(f"{what}: `{label}` at width {w} produced no text (CMD-01, D-154)")
                continue
            over = long_lines(text, w)
            if over:
                errs.append(f"{what}: `{label}` at width {w}: {len(over)} line(s) longer than {w}, e.g. {over[0][:60]!r}… ({len(over[0].rstrip())} chars) — "
                            f"help fits the console width (D-154)")
            if "…" in text:
                errs.append(f"{what}: `{label}` at width {w} contains an ellipsis — text was cropped, not wrapped (D-154: wrapping on)")
        text = renders.get(f"all@{w}") or ""
        lost = ref_words - words(text)
        if lost:
            errs.append(f"{what}: `help` at width {w} lost {sum(lost.values())} word(s) of the {wide}-column render, e.g. {sorted(lost)[:5]} — "
                        f"descriptions wrap, they are never cut off (D-154; the 200-column buffer was the bug)")
        rows = command_rows(text)
        missing = [c for c in commands if rows.get(c, 0) == 0]
        twice = [c for c in commands if rows.get(c, 0) > 1]
        if missing or twice:
            errs.append(f"{what}: `help` at width {w}: commands with no row {missing[:6]}, with several rows {twice[:6]} — every catalogue command appears "
                        f"exactly once as Command · When · What ({HELP_ROW_RULE})")
        extra = [c for c in rows if c not in commands and c != "?"]
        if extra:
            errs.append(f"{what}: `help` at width {w}: rows for names not in the catalogue {extra[:5]} (CMD-01)")
        title_lines = [ln for ln in text.splitlines() if not _ROW.match(ln)]
        titles = " ".join(title_lines).lower()
        no_title = [g for g in group_words if g not in titles]
        if no_title:
            errs.append(f"{what}: `help` at width {w}: no group title names {no_title} — five groups: Getting about · Looking · Fighting · Trading & items · Game (D-154)")
        ftext = renders.get(f"fighting@{w}") or ""
        frows = set(command_rows(ftext))
        if frows != set(fighting):
            errs.append(f"{what}: `help fighting` at width {w} lists {sorted(frows)}, expected exactly {sorted(fighting)} (D-154: help <group> shows one group)")
        ptext = renders.get(f"{panel_topic}@{w}") or ""
        low = ptext.lower()
        labels = [lab for lab in ("syntax", "does", "when", "example") if lab not in low]
        if labels:
            errs.append(f"{what}: `help {panel_topic}` at width {w} lacks the panel label(s) {labels} — Syntax · Does · When · Example · Numbers · IDs (D-154)")
        ids = [i for i in panel_ids if i.lower() not in low]
        if ids:
            errs.append(f"{what}: `help {panel_topic}` at width {w} does not cite {ids} (D-154: the panel ends with the blueprint IDs)")
        if panel_topic not in low:
            errs.append(f"{what}: `help {panel_topic}` at width {w} never names {panel_topic!r} (CMD-01)")
    return errs


# --------------------------------------------------------------------------- smoke/51 — the gameplay log covers every player line (OB-05, D-155, D-160)

def check_gameplay_log(log_text: str | None, report: dict, *, lines: list[tuple[int, str]], queued: tuple[str, ...], rejected: tuple[str, ...], what: str) -> list[str]:
    """Every (turn, line) the script typed has a 'T<n> you > <line>: <outcome>' line; `queued` lines show 'queued', `rejected` lines show
    'rejected: <reason>' with the reason the report recorded, verbatim; the header documents the three line forms (C12, D-155)."""
    if not isinstance(log_text, str):
        return [f"{what}: logs/game-<seed>.log missing under the sim's --save-dir — the engine writes the gameplay log (D-135, OB-05)"]
    errs: list[str] = []
    header = next((ln for ln in log_text.splitlines() if ln.startswith("#")), "")
    if "you >" not in header:
        errs.append(f"{what}: the log header {header[:100]!r} does not document the 'you >' line form (C12: the header documents all three line forms)")
    got = you_lines(log_text)
    by_line = {(t, ln): out for t, ln, out in got}
    for turn, line in lines:
        out = by_line.get((turn, line))
        if out is None:
            same = [(t, l) for t, l, _ in got if l == line]
            errs.append(f"{what}: no line 'T{turn} you > {line}: <outcome>' in game-<seed>.log" + (f" (seen at turns {[t for t, _ in same][:3]})" if same else "")
                        + f"; player lines found: {len(got)} ({LOG_LINE_RULE}; D-155: every line typed appears with its outcome)")
            continue
        if not out.strip():
            errs.append(f"{what}: 'T{turn} you > {line}:' has an empty outcome (D-155: queued / rejected: reason / the reply's first line)")
        if line in queued and not out.lower().startswith("queued"):
            errs.append(f"{what}: 'T{turn} you > {line}: {out[:80]}' — a queued order's outcome reads 'queued as <order>' (C12)")
        if line in rejected:
            reason = rejected_reason(turn_no(report, turn), line)
            if not out.lower().startswith("rejected"):
                errs.append(f"{what}: 'T{turn} you > {line}: {out[:80]}' — a rejected order's outcome reads 'rejected: <reason>' (C12, D-155)")
            elif reason and reason.lower() not in out.lower():
                errs.append(f"{what}: 'T{turn} you > {line}: {out[:100]}' does not carry the reason the report shows verbatim: {reason[:100]!r} (D-155: the exact text T1 shows)")
    return errs


def check_trace_scope(log_all: str | None, log_off: str | None, snapshot: dict | None, *, what: str) -> list[str]:
    """[logging] ai_trace = all → decision lines for captains outside the player's sector; off → no decision line at all (CFG-74, D-139)."""
    if log_all is None or log_off is None:
        return [f"{what}: logs/game-<seed>.log missing for the ai_trace {'all' if log_all is None else 'off'} run (D-135, OB-05)"]
    errs: list[str] = []
    off = decision_captains(log_off)
    if off:
        errs.append(f"{what}: {sum(off.values())} AI decision line(s) with [logging] ai_trace = off, expected none (CFG-74)")
    alls = decision_captains(log_all)
    if not alls:
        errs.append(f"{what}: no AI decision line with [logging] ai_trace = all (CFG-74, OB-05)")
    elif isinstance(snapshot, dict):
        try:
            _, pship = player_ship(snapshot)
            psec = _norm((pship.get("position") or [[], []])[:2])
        except KeyError:
            psec = None
        outside = 0
        for cid in alls:
            cap = (snapshot.get("captains") or {}).get(cid) or {}
            ship = (snapshot.get("ships") or {}).get(cap.get("active_ship")) or {}
            if ship and _norm((ship.get("position") or [[], []])[:2]) != psec:
                outside += 1
        if outside == 0:
            errs.append(f"{what}: with ai_trace = all the {len(alls)} captain(s) with decision lines all sit in the player's sector — 'all' logs every AI decision (CFG-74, D-139)")
    return errs


def drive_echo(pub_port: int, pull_port: int, *, turns: int, echoes: list[dict], hello_timeout: float = 15.0, turn_timeout: float = 10.0, quiet_s: float = 1.0) -> dict:
    """smoke/51 (bus): push the command.echo messages before turn 1 and again after it, then turn.end × turns; record the topics that arrive
    in the quiet second after the echoes and the canonical hash of every turn.resolved snapshot."""
    obs: dict = {"hello": None, "hashes": [], "echo_topics": [], "resolved": [], "notes": []}
    bo = BusObservations()
    client = BusClient(pub_port, pull_port)
    try:
        first = client.wait_for("state.hello", hello_timeout, bo)
        if first is None:
            obs["notes"].append(f"no state.hello within {hello_timeout:.0f} s (topics seen: {dict(bo.topics) or 'none'})")
            return obs
        obs["hello"] = {"turn_no": first.get("turn_no"), "seed": first.get("seed")}
        turn_no_ = int(first.get("turn_no") or 0)

        def send_echoes() -> None:
            for e in echoes:
                client.send("command.echo", dict(e))
            t0 = time.perf_counter()
            while time.perf_counter() - t0 < quiet_s:
                got = client.recv(0.2)
                if got is not None:
                    bo.topics[got[0]] += 1
                    if got[0] != "state.hello":
                        obs["echo_topics"].append(got[0])
        for i in range(turns):
            if echoes and i <= 1:
                send_echoes()
            client.send("turn.end", {"turn_no": turn_no_})
            res = client.wait_for("turn.resolved", turn_timeout, bo, pred=lambda p: isinstance(p.get("turn_no"), int) and p["turn_no"] > turn_no_)
            if res is None:
                obs["notes"].append(f"no turn.resolved within {turn_timeout:.0f} s after turn.end for turn {turn_no_}")
                return obs
            turn_no_ = int(res["turn_no"])
            obs["resolved"].append(turn_no_)
            obs["hashes"].append(canonical_hash(res.get("snapshot") or {}))
        return obs
    finally:
        client.close()


def check_echo_harmless(obs_echo: dict, obs_plain: dict, log_text: str | None, *, echoes: list[dict], turns: int, what: str) -> list[str]:
    """command.echo (MSG-10): the engine answers nothing and re-publishes nothing, every turn's snapshot hash equals the run without echoes, and
    the gameplay log carries one 'you >' line per echo (C12, D-160)."""
    errs: list[str] = []
    for label, o in (("with echoes", obs_echo), ("without", obs_plain)):
        if o.get("hello") is None:
            errs.append(f"{what} ({label}): no state.hello from the --bus engine: " + "; ".join(o.get("notes") or ["no notes"]) + " (C3)")
        elif len(o.get("hashes") or []) < turns:
            errs.append(f"{what} ({label}): {len(o.get('hashes') or [])} turn.resolved for {turns} turn.end pushes: " + "; ".join(o.get("notes") or ["no notes"]) + " (MSG-02 → MSG-03)")
    if errs:
        return errs
    bad = [t for t in obs_echo.get("echo_topics") or [] if t in ("order.accepted", "order.rejected", "command.echo", "turn.resolved")]
    if bad:
        errs.append(f"{what}: the engine answered command.echo with {sorted(set(bad))} — an echo is logged and dropped, never answered or re-published (C12 MSG-10, D-160)")
    diff = [i + 1 for i, (a, b) in enumerate(zip(obs_echo["hashes"], obs_plain["hashes"])) if a != b]
    if diff:
        errs.append(f"{what}: the snapshot hashes differ at turn(s) {diff[:4]} between the run with command.echo and the run without — the echo is not an order and "
                    f"changes no state (C12, D-160, M5 § 6)")
    if not isinstance(log_text, str):
        errs.append(f"{what}: logs/game-<seed>.log missing under the --bus sim's --save-dir (D-135, OB-05)")
    else:
        got = [(ln, out) for _, ln, out in you_lines(log_text)]
        for e in echoes:
            line = str(e.get("line", ""))
            if not any(ln == line for ln, _ in got):
                errs.append(f"{what}: no 'T<n> you > {line}: …' line for the command.echo in game-<seed>.log ({len(got)} player lines) — the engine logs every echo (D-160, D-155)")
                break
    return errs


# --------------------------------------------------------------------------- smoke/52 — eligibility & placement (EG-01…04, SP-08, EG-06, D-157, D-158)

def lodestone_shape_errors(ld: Any, where: str) -> list[str]:
    if not isinstance(ld, dict):
        return [f"{where}: lodestone = {_short(ld)}, expected {{sector, cell, distance, placed_turn, guardians, guardians_alive, picked_up_turn}} (C12 report additions)"]
    errs = [f"{where}: lodestone lacks {k!r} (C12)" for k in LODESTONE_KEYS if k not in ld]
    if parse_sector_key(ld.get("sector")) is None:
        errs.append(f"{where}: lodestone.sector = {ld.get('sector')!r}, expected 'g,g/s,s' (C12)")
    if not (isinstance(ld.get("cell"), list) and len(ld["cell"]) == 2):
        errs.append(f"{where}: lodestone.cell = {ld.get('cell')!r}, expected [cx, cy] (C12)")
    if not isinstance(ld.get("guardians"), list):
        errs.append(f"{where}: lodestone.guardians = {_short(ld.get('guardians'))}, expected a list of captain ids (C12)")
    return errs


def check_eligible(report: dict, snapshot: dict | None, *, threshold: int, guard_ships: int, torpedo_range: int, galaxy_size: Any, eligible_turn: int, what: str) -> list[str]:
    """Turn eligible_turn: an 'eligible' event names the placed sector; the lodestone row has the C12 shape, distance ≥ threshold (SP-08) both as
    reported and as the gate computes it from the player's position; guard_ships guardian captains flagged `guardian` with one Bastion each within
    torpedo_range of the item, in the sector; population counts exclude them; the sector is a real one without a hole (EG-06, D-158)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    t = turn_no(report, eligible_turn)
    texts = events_mentioning(t, "eligib")
    ld = lodestone_at(t)
    errs += lodestone_shape_errors(ld, f"{what}: turn {eligible_turn}")
    if errs:
        return errs
    key = str(ld["sector"])
    gs = parse_sector_key(key)
    if not texts:
        errs.append(f"{what}: turn {eligible_turn}: no event mentions eligibility (events {[event_text(e)[:60] for e in (t.get('events') or [])][:5]}) — "
                    f"endgame.eligible is announced the first turn all four thresholds hold (EG-01…04, C12 event type 'eligible')")
    elif not any(key in keys_in(x) for x in texts):
        errs.append(f"{what}: turn {eligible_turn}: the eligibility event {texts[0][:140]!r} does not name the placed sector {key} (D-158: announcing the sector is necessary)")
    et = _get(report, "final", "eligible_turn")
    if et != eligible_turn:
        errs.append(f"{what}: final.eligible_turn = {et!r}, expected {eligible_turn} (C12 final additions)")
    if ld.get("placed_turn") != eligible_turn:
        errs.append(f"{what}: lodestone.placed_turn = {ld.get('placed_turn')!r}, expected {eligible_turn} — placement happens in the eligibility phase (D-158)")
    if ld.get("picked_up_turn") is not None:
        errs.append(f"{what}: lodestone.picked_up_turn = {ld.get('picked_up_turn')!r} at placement (C12)")
    dist = ld.get("distance")
    if not isinstance(dist, int) or dist < threshold:
        errs.append(f"{what}: lodestone.distance = {dist!r}, expected ≥ {threshold} (placement_distance_pct % of the universe diameter; SP-08, CFG-87)")
    ps = player_sector(t)
    if ps is not None and gs is not None:
        mine = chebyshev(flat_sector(gs[0], gs[1], galaxy_size), flat_sector(ps[0], ps[1], galaxy_size))
        if mine < threshold:
            errs.append(f"{what}: the placed sector {key} is {mine} flattened sectors from the player's sector {sector_key(*ps)}, below {threshold} (SP-08, D-158)")
        elif isinstance(dist, int) and abs(mine - dist) > 1:
            errs.append(f"{what}: lodestone.distance = {dist} but the gate measures {mine} from the player's turn-{eligible_turn} sector {sector_key(*ps)} to {key} (flattened Chebyshev, CFG-87)")
    gids = [str(g) for g in ld.get("guardians") or []]
    if len(gids) != guard_ships or ld.get("guardians_alive") != guard_ships:
        errs.append(f"{what}: lodestone.guardians = {gids} / guardians_alive = {ld.get('guardians_alive')!r}, expected {guard_ships} guardians ([endgame] guard_ships; EG-06, CM-13)")
    if (t.get("player") or {}).get("master") not in (False, None) or (t.get("player") or {}).get("infinite_wealth") not in (False, None):
        errs.append(f"{what}: turn {eligible_turn}: player.master / infinite_wealth = {(t.get('player') or {}).get('master')!r} / {(t.get('player') or {}).get('infinite_wealth')!r} "
                    f"before any pickup (C12)")
    if not isinstance(snapshot, dict):
        errs.append(f"{what}: no save snapshot to read the guardians from (C6)")
        return errs
    gal = (snapshot.get("galaxies") or {}).get(f"{gs[0][0]},{gs[0][1]}") if gs else None
    if not isinstance(gal, dict):
        errs.append(f"{what}: the placed sector {key} lies in a galaxy the snapshot does not list — a real system, never deep space (SP-08, D-158)")
    else:
        if list(gs[1]) not in [list(x) for x in gal.get("sectors") or []]:
            errs.append(f"{what}: the placed sector {key} is not a sector of its galaxy (a Void cell) — the item lies in a real system with ≥ 1 star (SP-08, D-158)")
        if list(gs[1]) in [list(x) for x in gal.get("hazards") or []]:
            errs.append(f"{what}: the placed sector {key} holds a black hole or neutron star (galaxies[*].hazards) — placement avoids hole sectors (SP-08, D-158)")
    guards = guardians_of(snapshot)
    if set(guards) != set(gids):
        errs.append(f"{what}: captains flagged guardian are {sorted(guards)[:6]}, lodestone.guardians says {gids} (C12 captains[*].guardian, D-158)")
    ships = snapshot.get("ships") or {}
    for cid in gids:
        cap = (snapshot.get("captains") or {}).get(cid)
        if not isinstance(cap, dict):
            errs.append(f"{what}: guardian {cid} is not in the snapshot's captains (C12)")
            continue
        owned = [s for s in cap.get("ships") or [] if s in ships]
        if len(owned) != 1 or str(ships[owned[0]].get("class", "")).lower() != "bastion":
            errs.append(f"{what}: guardian {cid} owns {[ships[s].get('class') for s in owned]}, expected exactly one Bastion (EG-06, CM-13)")
            continue
        ship = ships[owned[0]]
        pos = ship.get("position") or [[], [], []]
        if _norm(pos[:2]) != _norm([list(gs[0]), list(gs[1])]):
            errs.append(f"{what}: guardian {cid}'s ship {owned[0]} stands in {_norm(pos[:2])}, not the placed sector {key} (D-158: never leave)")
        elif chebyshev(pos[2], ld["cell"]) > torpedo_range:
            errs.append(f"{what}: guardian {cid}'s ship at {pos[2]} is {chebyshev(pos[2], ld['cell'])} cells from the item at {ld['cell']}, beyond torpedo_range {torpedo_range} (D-158)")
    non_guard = sum(1 for cid, c in (snapshot.get("captains") or {}).items() if isinstance(c, dict) and c.get("guardian") is not True)
    last = _turns(report)[-1] if _turns(report) else {}
    counted = _int(last.get("captains"))
    fin_c = _int(_get(report, "final", "captains"))
    if counted is not None and counted != non_guard:
        errs.append(f"{what}: per_turn.captains = {counted} at the last turn but the snapshot holds {non_guard} non-guardian captains (+{len(guards)} guardians) — "
                    f"guardians are excluded from captains / population_* (C12, D-158)")
    if fin_c is not None and fin_c != non_guard:
        errs.append(f"{what}: final.captains = {fin_c}, expected {non_guard} (guardians excluded; C12)")
    pmax = _int(_get(report, "final", "population_max"))
    if pmax is not None and counted is not None and pmax < counted:
        errs.append(f"{what}: final.population_max {pmax} < the last turn's captains {counted} (C11)")
    return errs


def check_not_eligible(report: dict, *, turns: int, why: str, what: str) -> list[str]:
    """No eligibility event, no lodestone row and final.eligible_turn null over `turns` turns (EG-01…04)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    hit = [t.get("turn_no") for t in _turns(report) if events_mentioning(t, "eligib") or lodestone_at(t) is not None]
    if hit:
        errs.append(f"{what}: eligibility fired / the item was placed at turn(s) {hit[:3]} although {why} (EG-01…04, D-157)")
    et = _get(report, "final", "eligible_turn")
    if et is not None:
        errs.append(f"{what}: final.eligible_turn = {et!r}, expected null ({why}; C12)")
    tc = report.get("turns_completed")
    if tc != turns:
        errs.append(f"{what}: turns_completed = {tc}, expected {turns} (C2)")
    return errs


def check_progress_render(text: str | None, *, fractions: dict[str, tuple[int, int]], what: str) -> list[str]:
    """T4's PNL-08 progress line before placement: 'Mastery: wealth ✓ · fear 72/90 · respect ✓ · specials 1/3'; the unmet thresholds in
    `fractions` ({label: (value, minimum)}) show as value/minimum (D-158, PNL-08 Proposed)."""
    if not isinstance(text, str) or not text.strip():
        return [f"{what}: render_status produced no text (C4)"]
    lines = [ln for ln in text.splitlines() if "mastery" in ln.lower()]
    if not lines:
        return [f"{what}: T4 shows no 'Mastery:' progress line before placement (PNL-08, D-158)"]
    errs: list[str] = []
    line = " ".join(lines)
    for label, (value, minimum) in fractions.items():
        if not re.search(rf"{label}\D{{0,4}}{value}\s*/\s*{minimum}\b", line, re.I):
            errs.append(f"{what}: the Mastery line {line.strip()[:120]!r} does not show {label} {value}/{minimum} for the unmet threshold (PNL-08: 'fear 72/90' form, D-158)")
    return errs


def check_lodestone_render(text: str | None, *, key: str, alive: int, name: str, what: str) -> list[str]:
    """T4 after placement: 'Lodestone: 5,1/9,12 · guardians 4 alive' (PNL-08, D-158)."""
    if not isinstance(text, str) or not text.strip():
        return [f"{what}: render_status produced no text (C4)"]
    first = str(name).split()[0] if str(name).strip() else "Lodestone"
    lines = [ln for ln in text.splitlines() if "lodestone" in ln.lower() or first.lower() in ln.lower()]
    if not lines:
        return [f"{what}: T4 shows no 'Lodestone:' line after placement (PNL-08, D-158)"]
    errs: list[str] = []
    line = " ".join(lines)
    if key not in keys_in(line):
        errs.append(f"{what}: the Lodestone line {line.strip()[:120]!r} does not name the sector {key} (PNL-08)")
    if "guardian" not in line.lower() or not re.search(rf"\b{alive}\b", line):
        errs.append(f"{what}: the Lodestone line {line.strip()[:120]!r} does not say 'guardians {alive} alive' (PNL-08)")
    return errs


# --------------------------------------------------------------------------- smoke/53 — guardians (CM-13, D-158)

def check_placed_sector(snapshot: dict | None, *, key: str, cell: Any, what: str) -> list[str]:
    """The visited placed sector: kind 'system', ≥ 1 star, no hole, no terrain at the item's cell, the item listed there (ultimate once revealed)."""
    if not isinstance(snapshot, dict):
        return [f"{what}: no save snapshot (C6)"]
    sec = (snapshot.get("sectors") or {}).get(key)
    if not isinstance(sec, dict):
        return [f"{what}: the saved snapshot lists no sectors[{key!r}] although the player visited it (C3)"]
    errs: list[str] = []
    if sec.get("kind") not in (None, "system"):
        errs.append(f"{what}: sectors[{key}].kind = {sec.get('kind')!r}, expected 'system' — the item is placed in a real system (SP-08, C12)")
    terrain = sector_terrain(snapshot, key)
    kinds = Counter(str(t.get("kind")) for t in terrain)
    if kinds.get("star", 0) < 1:
        errs.append(f"{what}: sectors[{key}] holds no star ({dict(kinds)}) — placement needs ≥ 1 star (SP-08, D-158)")
    holes = {k for k in kinds if k in ("black_hole", "wormhole", "neutron_star")}
    if holes:
        errs.append(f"{what}: sectors[{key}] holds {sorted(holes)} — placement avoids hole sectors (SP-08, D-158)")
    if any(_norm(t.get("cell")) == _norm(list(cell)) for t in terrain):
        errs.append(f"{what}: the item cell {list(cell)} holds terrain — the item lies at the Void cell nearest the sector centre (D-158)")
    it = next((i for i in sector_items(snapshot, key) if _norm(i.get("cell")) == _norm(list(cell))), None)
    if it is None:
        errs.append(f"{what}: sectors[{key}].items lists nothing at the item cell {list(cell)} (C12: the placed item is a sector item)")
    elif it.get("revealed") is True and it.get("ultimate") is not True:
        errs.append(f"{what}: the revealed item at {list(cell)} carries ultimate = {it.get('ultimate')!r}, expected true (C12 snapshot additions)")
    return errs


def check_guardians(report: dict, snapshot: dict | None, *, guardians: dict[str, str], sector: str, arrival_turn: int, close_turn: int, low_turn: int, pickup_turn: int,
                    until_turn: int, target_ship: str, low_shields: float, ratio: float, ratio_min: float, fear: int, fear_max: int, what: str) -> list[str]:
    """guardians: captain id → ship id.  From the arrival turn a guardian fires on the player (CM-10 waived: ratio < ratio_min, fear ≥ fear_max); from
    close_turn every guardian fires every turn; the target's shields are ≤ low_shields by low_turn and still no guardian offers peace, escapes or
    surrenders or leaves the sector through until_turn; `pickup lodestone` at pickup_turn is rejected citing CM-13; nobody died (D-158)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    try:
        psid = player_ship(snapshot)[0] if isinstance(snapshot, dict) else None
    except KeyError:
        psid = None
    gships = set(guardians.values())
    names = set()
    if isinstance(snapshot, dict):
        for cid in guardians:
            cap = (snapshot.get("captains") or {}).get(cid) or {}
            if cap.get("name"):
                names.add(str(cap["name"]).lower())
    ta = turn_no(report, arrival_turn)
    fired = {ex.get("attacker") for ex in exchanges_at(ta) if ex.get("attacker") in gships and (psid is None or ex.get("defender") == psid)}
    if not fired:
        errs.append(f"{what}: turn {arrival_turn} (arrival): no guardian fired on the player (exchanges {len(exchanges_at(ta))}) although the ratio {ratio:.2f} < attack_ratio_min "
                    f"{ratio_min:g} and the player's fear {fear} ≥ attack_fear_max {fear_max} — guardians fire from the TP-04 of the arrival turn regardless (CM-13, D-158; {GUARDIAN_RULE})")
    opened = [b for b in battles_at(ta) if str(b.get("opened_by")) in guardians]
    if not opened and fired:
        errs.append(f"{what}: turn {arrival_turn}: battles[] has no battle opened by a guardian ({[(b.get('opened_by'), b.get('sides')) for b in battles_at(ta)][:3]}) (C10, CM-13)")
    dead: set[str] = set()
    for t in _turns(report):
        n = t.get("turn_no")
        for d in t.get("destroyed") or []:
            if isinstance(d, dict) and d.get("ship") in gships:
                dead.add(str(d["ship"]))
                errs.append(f"{what}: turn {n}: guardian ship {d['ship']} was destroyed — the gate fires only to lower one guardian's shields (gate logic; torpedo_hit_base 1)")
        if not isinstance(n, int) or n < close_turn or n > until_turn:
            continue
        attackers = {ex.get("attacker") for ex in exchanges_at(t) if ex.get("attacker") in gships}
        quiet = sorted(gships - attackers - dead)
        if quiet:
            errs.append(f"{what}: turn {n}: guardian ship(s) {quiet} did not fire on the player standing on the item cell (within torpedo_range of every guardian) — "
                        f"guardians fire every turn on any non-guardian ship in the sector (CM-13, D-158)")
        for x in events_mentioning(t, "peace"):
            low = x.lower()
            if any(nm in low for nm in names) or any(cid in x for cid in guardians):
                errs.append(f"{what}: turn {n}: a guardian is party to a peace event {x[:120]!r} — guardians never offer or accept peace (D-158)")
                break
        for x in events_mentioning(t, "escape"):
            low = x.lower()
            if any(nm in low for nm in names) or any(cid in x for cid in guardians):
                errs.append(f"{what}: turn {n}: a guardian escape event {x[:120]!r} — guardians never escape (D-158)")
                break
        for c in t.get("captures") or []:
            if isinstance(c, dict) and (c.get("ship") in gships or str(c.get("from")) in guardians):
                errs.append(f"{what}: turn {n}: a guardian surrendered its ship ({_short(c)}) — guardians never surrender (D-158, EF-02 waived)")
        for b in battles_at(t):
            if b.get("ended") == "escape" and any(str(s) in guardians for s in (b.get("sides") or [])):
                errs.append(f"{what}: turn {n}: battle {b.get('id')} with a guardian ended by escape (D-158)")
            if b.get("ended") == "peace" and any(str(s) in guardians for s in (b.get("sides") or [])):
                errs.append(f"{what}: turn {n}: battle {b.get('id')} with a guardian ended by peace (D-158)")
    tl = turn_no(report, low_turn)
    after = [float(ex.get("shields_after")) for t in _turns(report) if isinstance(t.get("turn_no"), int) and t["turn_no"] <= low_turn
             for ex in exchanges_at(t) if ex.get("defender") == target_ship and isinstance(ex.get("shields_after"), (int, float))]
    if not after:
        errs.append(f"{what}: by turn {low_turn} the player's torpedoes never hit {target_ship} (no exchange row with it as defender) — with torpedo_hit_base 1 every "
                    f"torpedo hits (gate logic, CM-03)")
    elif after[-1] > low_shields:
        errs.append(f"{what}: {target_ship}'s shields after the gate's torpedoes are {after[-1]:g} at turn {low_turn}, expected ≤ {low_shields:g} (below peace_shield_pct) so "
                    f"the no-peace rule is actually exercised (gate logic; CM-03 with torpedo_hit_base 1)")
    tp = turn_no(report, pickup_turn)
    rej = events_mentioning(tp, "rejected")
    if not rej:
        errs.append(f"{what}: turn {pickup_turn}: `pickup lodestone` with guardians alive was not rejected ({[event_text(e)[:60] for e in (tp.get('events') or [])][:5]}) — "
                    f"'the guardians still hold the sector (CM-13)' (C12, {REJECTED_RULE})")
    elif not any("cm-13" in x.lower() for x in rej):
        errs.append(f"{what}: turn {pickup_turn}: the rejection {rej[0][:140]!r} does not cite CM-13 (C12: 'the guardians still hold the sector (CM-13)')")
    ld = lodestone_at(turn_no(report, until_turn))
    if isinstance(ld, dict) and ld.get("guardians_alive") != len(guardians):
        errs.append(f"{what}: turn {until_turn}: lodestone.guardians_alive = {ld.get('guardians_alive')!r}, expected {len(guardians)} (C12)")
    if isinstance(snapshot, dict):
        ships = snapshot.get("ships") or {}
        for cid, sid in guardians.items():
            s = ships.get(sid)
            if not isinstance(s, dict):
                errs.append(f"{what}: guardian ship {sid} is gone from the final snapshot (D-158)")
            elif _norm((s.get("position") or [[], []])[:2]) != _norm([list(parse_sector_key(sector)[0]), list(parse_sector_key(sector)[1])]):
                errs.append(f"{what}: guardian ship {sid} ended in {_norm((s.get('position') or [[], []])[:2])}, not the placed sector {sector} — guardians never leave (D-158)")
            elif s.get("captain") != cid:
                errs.append(f"{what}: guardian ship {sid} now belongs to {s.get('captain')!r}, not {cid} (D-158: never surrender)")
            cap = (snapshot.get("captains") or {}).get(cid) or {}
            if cap.get("guardian") is not True:
                errs.append(f"{what}: captain {cid} lost its guardian flag (C12 captains[*].guardian)")
    return errs


def check_guardian_trace(log_text: str | None, *, guardians: dict[str, str], what: str) -> list[str]:
    """The gameplay log's decision lines for guardian captains name 'guardian' (OB-05, CFG-74, D-158)."""
    if not isinstance(log_text, str):
        return [f"{what}: logs/game-<seed>.log missing under the sim's --save-dir (D-135, OB-05)"]
    lines = [ln for ln in log_text.splitlines() if _DECISION.match(ln) and _DECISION.match(ln).group(2) in guardians]
    if not lines:
        return [f"{what}: no AI decision line for a guardian captain {sorted(guardians)[:4]} in game-<seed>.log — guardians are AI captains in the player's sector, "
                f"logged with ai_trace = sector (OB-05, CFG-74, D-158)"]
    if not any("guardian" in ln.lower() for ln in lines):
        return [f"{what}: {len(lines)} guardian decision line(s) but none says 'guardian', e.g. {lines[0][:140]!r} (M5 § 4 smoke/53: ai_trace lines name guardian)"]
    return []


# --------------------------------------------------------------------------- smoke/54 — mastery (EG-07, EG-08, EF-06, SUP-05, OUT-15, D-159)

def check_mastery(report: dict, snapshot: dict | None, *, mastery_turn: int, kill_turn: int, buy_turn: int, buy_n: int, buy_factor: float, planet_id: str, fire_turn: int,
                  spawn: dict, caps: dict, master_torpedoes: int, turns: int, what: str) -> list[str]:
    """spawn: {captain, ship}; caps: {shield_cap, phaser_max, impulse_max, warp_max}.  The kill at kill_turn leaves guardians_alive 0; `pickup` at
    mastery_turn raises game.mastery the same turn (event, final.mastery_turn, picked_up_turn); from then on master / infinite_wealth true, fear and
    respect 100 (also after the started fight at fire_turn), coins unchanged, food and water never fall (+buy_n at buy_turn), shields at shield_cap;
    the buy is accepted at factor buy_factor; the spawn never opens and surrenders the turn the master fires; the run keeps counting to `turns`;
    the final save shows the maxed stats (D-159)."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    pid = player_id(snapshot, report)
    tk = turn_no(report, kill_turn)
    ldk = lodestone_at(tk)
    if not isinstance(ldk, dict) or ldk.get("guardians_alive") != 0:
        errs.append(f"{what}: turn {kill_turn}: lodestone.guardians_alive = {(ldk or {}).get('guardians_alive')!r} after the gate's volley, expected 0 (C12; the kill is the precondition)")
    gd = _int(_get(report, "final", "guardians_destroyed"))
    if gd is None or gd < 1:
        errs.append(f"{what}: final.guardians_destroyed = {_get(report, 'final', 'guardians_destroyed')!r}, expected ≥ 1 (C12 final additions)")
    tm = turn_no(report, mastery_turn)
    if not events_mentioning(tm, "master"):
        errs.append(f"{what}: turn {mastery_turn}: no mastery event after `pickup lodestone` with no guardian alive (events "
                    f"{[event_text(e)[:60] for e in (tm.get('events') or [])][:5]}) — game.mastery fires at TP-05 the turn of the pickup (EG-07, OUT-15, C12 type 'mastery')")
    mt = _get(report, "final", "mastery_turn")
    if mt != mastery_turn:
        errs.append(f"{what}: final.mastery_turn = {mt!r}, expected {mastery_turn} (C2, C12)")
    if report.get("outcome") != "mastery":
        errs.append(f"{what}: outcome = {report.get('outcome')!r}, expected 'mastery' (C2)")
    if report.get("turns_completed") != turns:
        errs.append(f"{what}: turns_completed = {report.get('turns_completed')!r}, expected {turns} — play continues after mastery, the sim keeps counting (EG-07, D-159, OV-06)")
    ldm = lodestone_at(tm)
    if not isinstance(ldm, dict) or ldm.get("picked_up_turn") != mastery_turn:
        errs.append(f"{what}: turn {mastery_turn}: lodestone.picked_up_turn = {(ldm or {}).get('picked_up_turn')!r}, expected {mastery_turn} (C12)")
    before = turn_no(report, mastery_turn - 1).get("player") or {}
    if before.get("master") is True:
        errs.append(f"{what}: turn {mastery_turn - 1}: player.master already true before the pickup (C12)")
    base = tm.get("player") or {}
    w0 = wealth(base, 1)             # judged in raw coins: nothing may move
    food0, water0 = base.get("food"), base.get("water")
    prev_food, prev_water = food0, water0
    for t in _turns(report):
        n = t.get("turn_no")
        if not isinstance(n, int) or n < mastery_turn:
            continue
        p = t.get("player") or {}
        if p.get("master") is not True or p.get("infinite_wealth") is not True:
            errs.append(f"{what}: turn {n}: player.master / infinite_wealth = {p.get('master')!r} / {p.get('infinite_wealth')!r}, expected true from the mastery turn on (C12, EG-07)")
        if p.get("fear") != 100 or p.get("respect") != 100:
            errs.append(f"{what}: turn {n}: fear / respect = {p.get('fear')!r} / {p.get('respect')!r}, expected 100 / 100 locked (EG-07, D-159" + (f"; the fight started at turn {fire_turn} must not cost FR-06" if n >= fire_turn else "") + ")")
        if (p.get("gold"), p.get("silver")) != (base.get("gold"), base.get("silver")):
            errs.append(f"{what}: turn {n}: coins {p.get('gold')}/{p.get('silver')} changed from {base.get('gold')}/{base.get('silver')} at mastery — infinite wealth deducts nothing (EG-07, SUP-05, D-159)")
        if isinstance(p.get("shield_current"), (int, float)) and isinstance(p.get("shield_max"), (int, float)):
            if not _close(p["shield_max"], caps["shield_cap"], 1e-6) or not _close(p["shield_current"], caps["shield_cap"], 1e-6):
                errs.append(f"{what}: turn {n}: shields {p['shield_current']:g}/{p['shield_max']:g}, expected {caps['shield_cap']:g}/{caps['shield_cap']:g} (shield_cap, full each turn; EG-07, D-159)")
        if isinstance(p.get("food"), int) and isinstance(prev_food, int):
            want = prev_food + (buy_n if n == buy_turn else 0)
            if p["food"] != want:
                errs.append(f"{what}: turn {n}: food {prev_food} → {p['food']}, expected {want} — a Master's food never falls" + (f" and the buy adds {buy_n}" if n == buy_turn else "") + " (SUP-05, D-159)")
        if isinstance(p.get("water"), int) and isinstance(prev_water, int) and p["water"] != prev_water:
            errs.append(f"{what}: turn {n}: water {prev_water} → {p['water']} — a Master's water never falls (SUP-05)")
        prev_food, prev_water = p.get("food"), p.get("water")
        for b in battles_at(t):
            if str(b.get("opened_by")) == str(spawn.get("captain")):
                errs.append(f"{what}: turn {n}: the AI {spawn['captain']} opened a battle on the Master (ratio 2 would allow it for anyone else) — no AI opens fire on a Master (EF-06, D-159)")
    tb = turn_no(report, buy_turn)
    rows = [r for r in trades_at(tb) if str(r.get("with")) == planet_id]
    if not rows:
        rej = events_mentioning(tb, "rejected") + [str(r.get("reason")) for r in trades_at(tb) if r.get("accepted") is False]
        errs.append(f"{what}: turn {buy_turn}: no trades[] row with {planet_id} for `buy food {buy_n}`" + (f"; rejected: {rej[0][:120]!r}" if rej else "") +
                    " — every price check passes for a Master (EG-07, EF-06: every planet trades)")
    else:
        r = rows[0]
        if r.get("accepted") is not True:
            errs.append(f"{what}: turn {buy_turn}: the Master's `buy food {buy_n}` at {planet_id} was refused: {r.get('reason')!r} (EG-07, EF-06)")
        pf = r.get("price_factor")
        if not isinstance(pf, (int, float)) or not _close(pf, buy_factor, 0.005):
            errs.append(f"{what}: turn {buy_turn}: trades[].price_factor = {pf!r}, expected {buy_factor:.2f} — a Master trades at respect-100 prices (EF-06, D-142, D-159)")
        gave = r.get("gave") or {}
        if any(int(v or 0) for k, v in gave.items() if k in ("gold", "silver")):
            errs.append(f"{what}: turn {buy_turn}: the Master gave {gave} for the food — nothing is deducted (EG-07, SUP-05)")
    tf = turn_no(report, fire_turn)
    caps_rows = [c for c in (tf.get("captures") or []) if isinstance(c, dict) and c.get("ship") == spawn.get("ship")]
    if not caps_rows:
        errs.append(f"{what}: turn {fire_turn}: captures[] has no row for {spawn.get('ship')} ({_short(tf.get('captures'))}) — an AI the Master fires on surrenders that turn "
                    f"(FL-02 with the shield condition waived; EF-06 'every ship yields', D-159)")
    elif str(caps_rows[0].get("to")) != str(pid):
        errs.append(f"{what}: turn {fire_turn}: {spawn.get('ship')} was captured by {caps_rows[0].get('to')!r}, expected the player {pid} (FL-02)")
    if not isinstance(snapshot, dict):
        errs.append(f"{what}: no save snapshot after the run (C6)")
        return errs
    cap = (snapshot.get("captains") or {}).get(snapshot.get("player_id")) or {}
    if cap.get("master") is not True:
        errs.append(f"{what}: the saved captain's master = {cap.get('master')!r}, expected true (C12 snapshot additions, C6)")
    if cap.get("infinite_wealth") is not True:
        errs.append(f"{what}: the saved captain's infinite_wealth = {cap.get('infinite_wealth')!r}, expected true (C12, C6)")
    if (cap.get("fear"), cap.get("respect")) != (100, 100):
        errs.append(f"{what}: the saved fear / respect = {cap.get('fear')!r} / {cap.get('respect')!r}, expected 100 / 100 (C6, D-159)")
    try:
        _, ship = player_ship(snapshot)
    except KeyError as e:
        return errs + [f"{what}: {e}"]
    want = {"torpedoes": master_torpedoes, "phasers": caps["phaser_max"], "shield_max": caps["shield_cap"], "shield_current": caps["shield_cap"],
            "engine": caps["impulse_max"], "warp": caps["warp_max"]}
    for stat, v in want.items():
        if not _close(ship.get(stat), v, 1e-6):
            errs.append(f"{what}: the saved ship's {stat} = {ship.get(stat)!r}, expected {v:g} ({'master_torpedoes, refilled each turn' if stat == 'torpedoes' else 'the cap'}; "
                        f"EG-07, D-159, CFG-88)")
    sp_ship = (snapshot.get("ships") or {}).get(spawn.get("ship")) or {}
    if sp_ship and sp_ship.get("captain") != pid:
        errs.append(f"{what}: the surrendered ship {spawn.get('ship')} belongs to {sp_ship.get('captain')!r} in the save, expected the player (FL-02)")
    return errs


def check_ceremony_probe(probe: dict, *, width: int, height: int, n: int, what: str) -> list[str]:
    """probe['ceremony'] = {frames: [text…], title: text, render: text}: ≥ 1 fireworks frame fitting width × height and not blank (two
    frames differ when n > 1), a title frame of ≥ 5 rows fitting the width whose plain text carries MASTER OF STARBOUR, and the T3 render of
    the game.mastery payload non-empty (OUT-15, EG-08, D-159, C4)."""
    errs: list[str] = []
    for role, err in (probe.get("errors") or {}).items():
        if role.startswith("ceremony"):
            errs.append(f"{role}: {err.strip().splitlines()[-1] if err.strip() else err} (C4: fireworks_frames(width, height, rng, n), title_frame(width))")
    cer = probe.get("ceremony")
    if not isinstance(cer, dict):
        return errs or [f"{what}: the render probe returned no ceremony section (render_output.fireworks_frames / title_frame missing; OUT-15, C4)"]
    frames = cer.get("frames")
    if not isinstance(frames, list) or not frames:
        errs.append(f"{what}: fireworks_frames({width}, {height}, rng, {n}) returned {_short(frames)}, expected ≥ 1 frame (EG-08, D-159)")
    else:
        if len(frames) != n:
            errs.append(f"{what}: fireworks_frames(…, n={n}) returned {len(frames)} frames (EG-08)")
        for i, f in enumerate(frames[:n]):
            lines = str(f).splitlines()
            if len(lines) > height or any(len(ln) > width for ln in lines):
                errs.append(f"{what}: fireworks frame {i} is {len(lines)} rows × {max((len(ln) for ln in lines), default=0)} columns, expected ≤ {height} × {width} (C4)")
                break
        if all(not str(f).strip() for f in frames):
            errs.append(f"{what}: every fireworks frame is blank (OUT-15: a fireworks animation)")
        if n > 1 and len(frames) > 1 and len({str(f) for f in frames}) == 1:
            errs.append(f"{what}: all {len(frames)} fireworks frames are identical — the rng must drive the animation (D-159)")
    title = cer.get("title")
    if not isinstance(title, str) or not title.strip():
        errs.append(f"{what}: title_frame({width}) returned no text (EG-08: a block-letter MASTER OF STARBOUR)")
    else:
        lines = [ln for ln in title.splitlines() if ln.strip()]
        if len(lines) < 5:
            errs.append(f"{what}: title_frame({width}) has {len(lines)} non-blank rows, expected ≥ 5 (a 5-row font; D-159)")
        if any(len(ln) > width for ln in title.splitlines()):
            errs.append(f"{what}: title_frame({width}) has a line longer than {width} (C4)")
        if "master of starbour" not in re.sub(r"\s+", " ", title.lower()):
            errs.append(f"{what}: title_frame({width})'s plain text does not carry 'MASTER OF STARBOUR' (OUT-15; D-162: the words must be readable in the frame's text)")
    rend = cer.get("render")
    if not isinstance(rend, str) or not rend.strip():
        errs.append(f"{what}: render_output.render() of the game.mastery payload produced no text (C4: it returns the first frame)")
    return errs


def check_master_render(text: str | None, *, infinity: str | None, what: str) -> list[str]:
    """T4 after mastery: the header carries MASTER OF STARBOUR and coins / food / water read as ∞ (PNL-08, EG-08, C4 C12 additions)."""
    if not isinstance(text, str) or not text.strip():
        return [f"{what}: render_status produced no text (C4)"]
    errs: list[str] = []
    if "master of starbour" not in text.lower():
        errs.append(f"{what}: T4 does not carry 'MASTER OF STARBOUR' for a master captain (EG-08, PNL-08: the header keeps the title permanently)")
    if infinity and infinity not in text:
        errs.append(f"{what}: T4 shows no {infinity!r} for the Master's coins / food / water (C12 C4 additions, SUP-05, D-159)")
    return errs


def drive_resume(pub_port: int, pull_port: int, *, hello_timeout: float = 20.0, watch_s: float = 3.0) -> dict:
    """smoke/54 (resume): an engine started from the Master's save — read state.hello, then listen `watch_s` for anything else."""
    obs: dict = {"hello": None, "master": None, "topics_after": {}, "mastery_seen": False, "notes": []}
    bo = BusObservations()
    client = BusClient(pub_port, pull_port)
    try:
        first = client.wait_for("state.hello", hello_timeout, bo)
        if first is None:
            obs["notes"].append(f"no state.hello within {hello_timeout:.0f} s (topics seen: {dict(bo.topics) or 'none'})")
            return obs
        snap = first.get("snapshot") or {}
        cap = (snap.get("captains") or {}).get(snap.get("player_id")) or {}
        obs["hello"] = {"turn_no": first.get("turn_no"), "seed": first.get("seed")}
        obs["master"] = cap.get("master")
        obs["mastery_seen"] = bo.topics.get("game.mastery", 0) > 0
        t0 = time.perf_counter()
        seen: Counter = Counter()
        while time.perf_counter() - t0 < watch_s:
            got = client.recv(0.2)
            if got is not None:
                seen[got[0]] += 1
        obs["topics_after"] = dict(seen)
        obs["mastery_seen"] = obs["mastery_seen"] or seen.get("game.mastery", 0) > 0
        return obs
    finally:
        client.close()


def check_master_resume(obs: dict, *, what: str) -> list[str]:
    """Loading a Master's save keeps master = true and never re-emits game.mastery (C6 C12, D-159)."""
    if obs.get("hello") is None:
        return [f"{what}: no state.hello from the engine started on the Master's save: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C1 --role engine --save-dir, C3)"]
    errs: list[str] = []
    if obs.get("master") is not True:
        errs.append(f"{what}: state.hello after loading the save shows captains[player].master = {obs.get('master')!r}, expected true (C6: master survives save/load; D-159)")
    if obs.get("mastery_seen"):
        errs.append(f"{what}: the engine published game.mastery again after loading the save — loading never re-emits the event (C6 C12, D-159: the ceremony plays once)")
    return errs


# --------------------------------------------------------------------------- smoke/12 ext. / smoke/44 — the trading AI player (D-161)

def check_trade_player(report: dict, *, turns: int, what: str) -> list[str]:
    """--ai-player trade: at most 2 player mutinies, at least one restock (food or water rising on a turn without a player mutiny), no mastery."""
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    muts = [m for m in (_get(report, "final", "mutinies") or []) if isinstance(m, dict) and str(m.get("captain")) == "player"]
    if len(muts) > 2:
        errs.append(f"{what}: the AI-driven player mutinied {len(muts)} times in {turns} turns (turns {[m.get('turn_no') for m in muts][:5]}) — with --ai-player trade the player "
                    f"restocks like any AI captain; the gate was measuring its own artefact ({TRADE_PLAYER_RULE})")
    mut_turns = {m.get("turn_no") for m in muts}
    rose = False
    prev = None
    for t in _turns(report):
        p = t.get("player") or {}
        if prev is not None and t.get("turn_no") not in mut_turns:
            if isinstance(p.get("food"), int) and isinstance(prev.get("food"), int) and p["food"] > prev["food"]:
                rose = True
            if isinstance(p.get("water"), int) and isinstance(prev.get("water"), int) and p["water"] > prev["water"]:
                rose = True
        prev = p
    if not rose:
        errs.append(f"{what}: the player's food and water never rose in {turns} turns outside a mutiny — the trading AI player buys supplies (D-161, D-144)")
    mt = _get(report, "final", "mastery_turn")
    if mt is not None or report.get("outcome") == "mastery":
        errs.append(f"{what}: final.mastery_turn = {mt!r} / outcome {report.get('outcome')!r} — an AI-driven player never reaches mastery in {turns} turns (D-161, M5 § 4)")
    return errs


# --------------------------------------------------------------------------- the --long tier (M5 § 4 (c), D-161)

def population_curve(report: dict, every: int = 200) -> list[tuple[int, int]]:
    out = []
    for t in _turns(report):
        n = t.get("turn_no")
        if isinstance(n, int) and (n % every == 0 or n == 1):
            c = _int(t.get("captains"))
            if c is not None:
                out.append((n, c))
    return out


def resolve_means(report: dict, n: int = 100) -> tuple[float | None, float | None]:
    ms = [float(t.get("resolve_ms")) for t in _turns(report) if isinstance(t.get("resolve_ms"), (int, float))]
    if len(ms) < 2 * n:
        return None, None
    return statistics.fmean(ms[:n]), statistics.fmean(ms[-n:])


def check_long_tier(report: dict | None, *, turns: int, peak_rss_mb: float | None, rss_limit_mb: float, ratio_max: float = 2.0, what: str) -> list[str]:
    """2,000 turns with --ai-player trade: the run completes, the population stays within 0.7…1.3 × captains_start, no mastery, peak RSS below
    rss_limit_mb, and the mean resolve time of the last 100 turns ≤ ratio_max × the first 100 (nothing grows without bound; D-161, OV-06)."""
    if not isinstance(report, dict):
        return [f"{what}: no report was written (C2)"]
    errs = _prefix(report, what, 0)
    if errs:
        return errs
    if report.get("turns_completed") != turns or report.get("outcome") != "running":
        errs.append(f"{what}: turns_completed = {report.get('turns_completed')!r}, outcome {report.get('outcome')!r}, expected {turns} / 'running' (C2)")
    final = report.get("final") or {}
    start, pmin, pmax = _int(final.get("captains_start")), _int(final.get("population_min")), _int(final.get("population_max"))
    if start is None or pmin is None or pmax is None:
        errs.append(f"{what}: final.captains_start / population_min / population_max = {final.get('captains_start')!r} / {final.get('population_min')!r} / "
                    f"{final.get('population_max')!r}, expected integers (C11)")
    else:
        if pmax > 1.3 * start:
            errs.append(f"{what}: population_max {pmax} > 1.3 × captains_start {start} over {turns} turns (D-144, D-161)")
        if pmin < 0.7 * start:
            errs.append(f"{what}: population_min {pmin} < 0.7 × captains_start {start} over {turns} turns — the AI dies out (D-144)")
    if final.get("mastery_turn") is not None:
        errs.append(f"{what}: final.mastery_turn = {final.get('mastery_turn')!r} — the AI-driven player must not reach mastery (D-161)")
    if peak_rss_mb is None:
        errs.append(f"{what}: the sim's peak RSS could not be measured (psutil; gate logic)")
    elif peak_rss_mb >= rss_limit_mb:
        errs.append(f"{what}: peak RSS {peak_rss_mb:.0f} MB ≥ {rss_limit_mb:.0f} MB over {turns} turns — something grows without bound (M5 § 4 (c) [ASSUMED])")
    first, last = resolve_means(report)
    if first is None:
        errs.append(f"{what}: fewer than 200 turns carry resolve_ms (C2)")
    elif first > 0 and last > ratio_max * first:
        errs.append(f"{what}: mean resolve_ms of the last 100 turns {last:.1f} > {ratio_max:g} × the first 100 ({first:.1f}) — resolution slows as state accumulates (M5 § 4 (c), OV-06)")
    return errs


# --------------------------------------------------------------------------- live/11 — mastery over the bus (I/O driver + pure checker)

def drive_mastery(pub_port: int, pull_port: int, *, warp_factor: float, volley: int, hello_timeout: float = 15.0, reply_timeout: float = 5.0, turn_timeout: float = 10.0,
                  kill_tries: int = 3, after_mastery: Callable[[dict], Any] | None = None) -> dict:
    """live/11: turn 1 (placement) → warp to the lodestone sector → move onto the item cell → scan + a torpedo volley at the first guardian until
    guardians_alive is 0 → `pickup lodestone` → the turn that follows must bring turn.resolved and game.mastery.  `after_mastery(obs)` runs then
    (the live gate watches the T3 ceremony heartbeat); one more turn.end follows so T3 keeps consuming."""
    obs: dict = {"hello": None, "player_id": None, "ship_id": None, "lodestone": None, "warp_reply": None, "arrived": None, "move_reply": None, "at_cell": None,
                 "scan_reply": None, "fire_replies": [], "kill_turn": None, "guardians_alive": None, "pickup_reply": None, "mastery": None, "mastery_wait_s": None,
                 "mastery_turn": None, "resolved_types": None, "master_flag": None, "after_mastery": None, "extra_turn": None, "notes": []}
    bo = BusObservations()
    client = BusClient(pub_port, pull_port)

    def order(sid: str, turn: int, command: str, args: list[str]) -> tuple[str, dict] | None:
        client.send("order.submitted", {"command": command, "args": [str(a) for a in args], "ship_id": sid, "turn_no": turn, "client": "command"})
        rep = client.wait_for_any(("order.accepted", "order.rejected"), reply_timeout, bo)
        return ({"topic": rep[0], "reason": str((rep[1] or {}).get("reason", "")), "text": str((rep[1] or {}).get("text", ""))[:160]} if rep else None)

    def end_turn(turn: int) -> dict | None:
        client.send("turn.end", {"turn_no": turn})
        return client.wait_for("turn.resolved", turn_timeout, bo, pred=lambda p: isinstance(p.get("turn_no"), int) and p["turn_no"] > turn)

    try:
        first = client.wait_for("state.hello", hello_timeout, bo)
        if first is None:
            obs["notes"].append(f"no state.hello within {hello_timeout:.0f} s (topics seen: {dict(bo.topics) or 'none'})")
            return obs
        snapshot = first.get("snapshot") or {}
        obs["hello"] = {"turn_no": first.get("turn_no"), "seed": first.get("seed")}
        pid = snapshot.get("player_id")
        obs["player_id"] = pid
        turn = int(first.get("turn_no") or snapshot.get("turn_no") or 0)
        try:
            sid, _ = player_ship(snapshot)
        except KeyError as e:
            obs["notes"].append(str(e))
            return obs
        obs["ship_id"] = sid
        res = end_turn(turn)
        if res is None:
            obs["notes"].append(f"no turn.resolved within {turn_timeout:.0f} s after the first turn.end")
            return obs
        turn = int(res["turn_no"])
        snap = res.get("snapshot") or {}
        ld = snap.get("lodestone")
        obs["lodestone"] = ld if isinstance(ld, dict) else None
        if obs["lodestone"] is None or parse_sector_key(ld.get("sector")) is None:
            obs["notes"].append(f"turn {turn}: snapshot.lodestone = {_short(ld)} — nothing placed")
            return obs
        gs = parse_sector_key(ld["sector"])
        obs["warp_reply"] = order(sid, turn, "warp", [gs[0][0], gs[0][1], gs[1][0], gs[1][1], f"{warp_factor:g}"])
        res = end_turn(turn)
        if res is None:
            obs["notes"].append(f"no turn.resolved after the warp turn {turn}")
            return obs
        turn = int(res["turn_no"])
        snap = res.get("snapshot") or {}
        ship = (snap.get("ships") or {}).get(sid) or {}
        obs["arrived"] = _norm((ship.get("position") or [[], []])[:2]) == _norm([list(gs[0]), list(gs[1])])
        cell = ld.get("cell") or [0, 0]
        obs["move_reply"] = order(sid, turn, "move", [cell[0], cell[1]])
        res = end_turn(turn)
        if res is None:
            obs["notes"].append(f"no turn.resolved after the move turn {turn}")
            return obs
        turn = int(res["turn_no"])
        snap = res.get("snapshot") or {}
        ship = (snap.get("ships") or {}).get(sid) or {}
        obs["at_cell"] = _norm((ship.get("position") or [[], [], []])[2]) == _norm(list(cell))
        obs["scan_reply"] = order(sid, turn, "scan", [])
        gids = [str(g) for g in (ld.get("guardians") or [])]
        for _ in range(kill_tries):
            alive = [g for g in gids if g in (snap.get("captains") or {})]
            if not alive:
                break
            gship = ((snap.get("captains") or {}).get(alive[0]) or {}).get("active_ship")
            obs["fire_replies"].append(order(sid, turn, "fire", ["torpedo", str(gship), str(volley)]))
            res = end_turn(turn)
            if res is None:
                obs["notes"].append(f"no turn.resolved after the fire turn {turn}")
                return obs
            turn = int(res["turn_no"])
            snap = res.get("snapshot") or {}
        ld2 = snap.get("lodestone") if isinstance(snap.get("lodestone"), dict) else {}
        obs["guardians_alive"] = ld2.get("guardians_alive", "<missing>")
        obs["kill_turn"] = turn
        obs["pickup_reply"] = order(sid, turn, "pickup", ["lodestone"])
        t0 = time.perf_counter()
        client.send("turn.end", {"turn_no": turn})
        deadline = time.perf_counter() + turn_timeout
        resolved = mastery = None
        while time.perf_counter() < deadline and (resolved is None or mastery is None):
            got = client.recv(0.2)
            if got is None:
                continue
            topic, payload = got
            bo.topics[topic] += 1
            if topic == "turn.resolved" and isinstance(payload.get("turn_no"), int) and payload["turn_no"] > turn:
                resolved = payload
            elif topic == "game.mastery":
                mastery = payload
                obs["mastery_wait_s"] = round(time.perf_counter() - t0, 2)
        if resolved is None:
            obs["notes"].append(f"no turn.resolved within {turn_timeout:.0f} s after the pickup turn {turn}")
            return obs
        turn = int(resolved["turn_no"])
        obs["mastery_turn"] = turn
        obs["mastery"] = mastery
        evs = resolved.get("events") or []
        obs["resolved_types"] = [e.get("type") for e in evs if isinstance(e, dict)]
        cap = ((resolved.get("snapshot") or {}).get("captains") or {}).get(pid) or {}
        obs["master_flag"] = cap.get("master", "<missing>")
        if after_mastery is not None:
            obs["after_mastery"] = after_mastery(obs)
        res = end_turn(turn)
        obs["extra_turn"] = int(res["turn_no"]) if res else None
        if res is None:
            obs["notes"].append(f"no turn.resolved after the turn.end pushed during / after the ceremony (turn {turn})")
        return obs
    finally:
        client.close()


def check_mastery_bus(obs: dict, *, what: str = "live/11") -> list[str]:
    """live/11 — placement after turn 1, warp and move accepted, the volley kills the guardian(s), the pickup is accepted, game.mastery (MSG-09)
    arrives with the turn.resolved that carries the mastery event and master = true, and the engine keeps resolving afterwards."""
    errs: list[str] = []
    if obs.get("hello") is None:
        return [f"{what}: no state.hello from the engine: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C3)"]
    if obs.get("ship_id") is None:
        return [f"{what}: the player's ship is missing from state.hello: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C3)"]
    if obs.get("lodestone") is None:
        return [f"{what}: after turn 1 the snapshot carries no lodestone — placement did not happen: the launcher forwards --respect, --fear, --coins and --give to the engine "
                f"(C12 C1, D-148 (13)) and eligibility is evaluated at TP-08 (EG-01…04, D-158); " + "; ".join(obs.get("notes") or ["no notes"])]
    for key, label in (("warp_reply", "warp to the placed sector at full factor"), ("move_reply", "move onto the item cell"), ("pickup_reply", "pickup lodestone")):
        r = obs.get(key)
        if r is None:
            errs.append(f"{what}: no reply to the {label} order (MSG-06 / MSG-07)")
        elif r.get("topic") != "order.accepted":
            errs.append(f"{what}: the {label} order was rejected: {r.get('reason')!r}" + (" (CM-13 applies only while a guardian lives)" if key == "pickup_reply" else "")
                        + " (CMD-07 / CMD-06 / CMD-15, D-158)")
    if obs.get("arrived") is False:
        errs.append(f"{what}: the ship did not arrive in the lodestone sector after the warp (CMD-07, MV-05)")
    if not obs.get("fire_replies"):
        errs.append(f"{what}: no fire order was pushed — no guardian captain of lodestone.guardians was in the snapshot (C12)")
    elif any(r is None or r.get("topic") != "order.accepted" for r in obs["fire_replies"]):
        errs.append(f"{what}: a torpedo volley at the guardian was rejected: {[(r or {}).get('reason') for r in obs['fire_replies']]} (CMD-08, CM-02)")
    if obs.get("guardians_alive") != 0:
        errs.append(f"{what}: lodestone.guardians_alive = {obs.get('guardians_alive')!r} after the volley(s), expected 0 (C12; torpedo_hit_base 1 in the temp ini)")
    if obs.get("mastery_turn") is None:
        return errs + [f"{what}: no turn.resolved after the pickup: " + "; ".join(obs.get("notes") or ["no notes"]) + " (MSG-02 → MSG-03)"]
    if obs.get("mastery") is None:
        errs.append(f"{what}: no game.mastery message after `pickup lodestone` with no guardian alive (MSG-09, EG-07, C12)")
    else:
        m = obs["mastery"]
        if m.get("turn_no") != obs["mastery_turn"]:
            errs.append(f"{what}: game.mastery.turn_no = {m.get('turn_no')!r}, expected the resolved turn {obs['mastery_turn']} (MSG-09)")
        if not isinstance(m.get("item"), str):
            errs.append(f"{what}: game.mastery payload lacks 'item' ({_short(m)}; C12: {{turn_no, captain, item}})")
    if "mastery" not in (obs.get("resolved_types") or []):
        errs.append(f"{what}: turn.resolved {obs['mastery_turn']} events carry no type 'mastery' (types {obs.get('resolved_types')}) — OUT-15 is a T3 event (C3, C12)")
    if obs.get("master_flag") == "<missing>":
        errs.append(f"{what}: turn.resolved snapshot captains[player] has no 'master' (C12 snapshot additions)")
    elif obs.get("master_flag") is not True:
        errs.append(f"{what}: turn.resolved snapshot: captains[player].master = {obs.get('master_flag')!r}, expected true (EG-07)")
    if obs.get("extra_turn") is None:
        errs.append(f"{what}: the engine stopped resolving after mastery: " + "; ".join(obs.get("notes") or ["no notes"]) + " (EG-07: play continues; the engine never waits for the ceremony)")
    return errs


def check_ceremony_heartbeat(seen: dict | None, *, ceremony_seconds: float, grace_s: float = 5.0, what: str = "live/11") -> list[str]:
    """seen (from the live gate's after_mastery hook): {running_after_s, done_after_s, master, problems, turn_no}.  T3's heartbeat shows the ceremony
    start and end within ceremony_seconds + grace, T4's says master true, every window drew the mastery turn (C5 C12, OUT-15, BUS-05)."""
    if not seen:
        return []
    errs: list[str] = []
    if seen.get("running_after_s") is None:
        errs.append(f"{what}: T3's heartbeat never showed ceremony = 'running' after game.mastery (C5 C12: output.json carries ceremony null | running | done; OUT-15)")
    if seen.get("done_after_s") is None:
        errs.append(f"{what}: T3's heartbeat never showed ceremony = 'done' within ceremony_seconds {ceremony_seconds:g} + {grace_s:g} s after game.mastery — the ceremony hangs "
                    f"the terminal or never ends (OUT-15, D-159)")
    elif isinstance(seen.get("done_after_s"), (int, float)) and seen["done_after_s"] > ceremony_seconds + grace_s:
        errs.append(f"{what}: the ceremony ended {seen['done_after_s']:.1f} s after game.mastery, later than ceremony_seconds {ceremony_seconds:g} + {grace_s:g} (CFG-89)")
    if seen.get("master") is not True:
        errs.append(f"{what}: T4's heartbeat master = {seen.get('master')!r}, expected true after mastery (C5 C12)")
    hb = list(seen.get("problems") or [])
    if hb:
        hb[-1] += f" — after the ceremony ended, every window must have drawn turn {seen.get('turn_no')} (T3 keeps consuming turn.resolved; BUS-05, C5)"
    return errs + hb
