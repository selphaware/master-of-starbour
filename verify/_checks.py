"""verify/_checks.py — checker functions shared by smoke_test.py and live_test.py.

Two kinds of thing live here, kept apart:

* **Pure checkers** — functions of plain data (a sim report, bus observations, rendered
  text, heartbeat dicts) that return a list of problem strings; an empty list means
  pass.  They touch no sockets, files, processes or clocks, so --selftest can feed
  them canned fixtures and prove each one fails for its own reason and no other.
* **Thin I/O adapters** — the ZeroMQ client, process-tree helpers and file readers
  that *collect* the data the checkers judge.  They contain no pass/fail logic.

Every message names the check code, says what was seen versus what was expected, and
cites the blueprint ID (docs/DESIGN.md) or contract (docs/CONTRACTS.md) it enforces.
Interfaces are those in docs/CONTRACTS.md; nothing else is assumed about the game.

Exit codes (CONTRACTS C7): 0 pass · 1 deliverable failure · 3 HARNESS ERROR.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import traceback
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

# --------------------------------------------------------------------------- constants

EXPECTED_PY = Path(r"E:\CODE\master-of-starbour\MOS\Scripts\python.exe")
REPO_ROOT_PINNED = Path(r"E:\CODE\master-of-starbour")
MILESTONES = ("M1", "M2", "M3", "M4", "M5")
ROLES = ("engine", "command", "grid", "output", "status")
VIEW_ROLES = ("grid", "output", "status")

# Glyph the gate looks for as the player's active ship (SYM-10).  The unicode glyph is
# the blueprint's; the ascii fallback is [ASSUMED] from the controls_config.ini comment
# for [ui] glyph_set ("ascii (* @ ^ …)") — D-68.
ACTIVE_SHIP_GLYPH = {"unicode": "▲", "ascii": "^"}

PASS, FAIL, SKIP, BLOCKED = "PASS", "FAIL", "SKIP", "BLOCKED"


class HarnessError(Exception):
    """The environment, not the deliverable, is wrong (exit 3)."""


@dataclass(slots=True)
class Outcome:
    code: str
    title: str
    status: str
    detail: str = ""
    ids: str = ""
    seconds: float = 0.0

    def line(self) -> str:
        s = f"{self.status:<7} {self.code} {self.title}"
        if self.detail:
            s += f": {self.detail}"
        if self.ids and self.status != PASS:
            s += f" — {self.ids}"
        return s + f"  ({self.seconds:.1f} s)"

    def as_dict(self) -> dict:
        return {"code": self.code, "title": self.title, "status": self.status,
                "detail": self.detail, "ids": self.ids, "seconds": round(self.seconds, 3)}


class Skip(Exception):
    """The check does not apply yet (milestone not reached) — reported as a visible SKIP."""


class Blocked(Exception):
    """A prerequisite check failed, so this one cannot run — reported as BLOCKED."""


@dataclass(slots=True)
class Check:
    code: str
    title: str
    ids: str
    fn: Callable[[Any], list[str]]
    since: str = "M1"


def run_checks(checks: list[Check], ctx: Any, *, milestone: Callable[[], str], select: set[str] | None,
               budget_s: float, label: str, result_path: Path) -> int:
    """Run checks in order; print one line per check plus timings; return the exit code
    (0 pass, 1 any FAIL, 3 at the first HARNESS ERROR — which stops the run)."""
    outcomes: list[Outcome] = []
    t_all = time.perf_counter()
    print(f"== {label} ==")
    rc = 0
    for chk in checks:
        if select is not None and chk.code not in select:
            continue
        t0 = time.perf_counter()
        details: list[str] = []
        try:
            if milestone_index(milestone()) < milestone_index(chk.since):
                raise Skip(f"needs {chk.since}; specs/MILESTONE is {milestone()}")
            problems = chk.fn(ctx)
            details = list(problems)
            o = Outcome(chk.code, chk.title, PASS if not details else FAIL, "", chk.ids)
        except Skip as e:
            o = Outcome(chk.code, chk.title, SKIP, str(e), chk.ids)
        except Blocked as e:
            o = Outcome(chk.code, chk.title, BLOCKED, str(e), chk.ids)
        except HarnessError as e:
            o = Outcome(chk.code, chk.title, "HARNESS", str(e), chk.ids, time.perf_counter() - t0)
            outcomes.append(o)
            print(f"HARNESS ERROR {chk.code} {chk.title}: {e}  ({o.seconds:.1f} s)")
            print("The environment is wrong, not the code. Fix it and re-run; do not change the game for this.")
            rc = 3
            break
        except Exception:
            o = Outcome(chk.code, chk.title, FAIL, "", chk.ids)
            details = ["unexpected exception inside the gate — if the deliverable's output is malformed, fix that; "
                       "if the gate itself is wrong, say so in the report (never edit verify/): "
                       + traceback.format_exc(limit=4).strip().replace("\n", "\n" + " " * 10)]
        o.seconds = time.perf_counter() - t0
        if details:
            o.detail = details[0]
        outcomes.append(o)
        print(o.line())
        for extra in details[1:]:
            print(f"{'':8}+ {extra}")
    total = time.perf_counter() - t_all
    counts = Counter(o.status for o in outcomes)
    if rc != 3 and counts.get(FAIL, 0):
        rc = 1
    print("-- timings --")
    for o in outcomes:
        print(f"  {o.seconds:6.1f} s  {o.code}  {o.status}")
    print(f"== {label}: {counts.get(PASS, 0)} pass, {counts.get(FAIL, 0)} fail, {counts.get(SKIP, 0)} skip, "
          f"{counts.get(BLOCKED, 0)} blocked — {total:.1f} s (budget {budget_s:.0f} s) — exit {rc} ==")
    if total > budget_s:
        print(f"WARN tier budget exceeded: {total:.1f} s > {budget_s:.0f} s (BOOTSTRAP § gates) — not a failure; report it")
    try:
        result_path.parent.mkdir(parents=True, exist_ok=True)
        result_path.write_text(json.dumps({"label": label, "exit": rc, "total_s": round(total, 2),
                                           "outcomes": [o.as_dict() for o in outcomes]}, indent=1), encoding="utf-8")
    except OSError:
        pass
    return rc


def milestone_index(name: str) -> int:
    name = (name or "").strip().upper()
    if name not in MILESTONES:
        raise HarnessError(f"specs/MILESTONE must be one of {MILESTONES}, got {name!r}")
    return MILESTONES.index(name)


def read_milestone(root: Path) -> str:
    p = root / "specs" / "MILESTONE"
    if not p.exists():
        raise HarnessError(f"specs/MILESTONE not found at {p} — the gate cannot tell which checks apply")
    return p.read_text(encoding="utf-8").strip()


# --------------------------------------------------------------------------- small helpers

def load_json(path: Path) -> tuple[Any, str]:
    """Return (object, '') or (None, reason)."""
    if not path.exists():
        return None, f"{path} does not exist"
    try:
        return json.loads(path.read_text(encoding="utf-8")), ""
    except (OSError, ValueError) as e:
        return None, f"{path} is not valid JSON ({e})"


def canonical_json(obj: Any, ensure_ascii: bool = True) -> bytes:
    """Canonical JSON per CONTRACTS C2: sorted keys, no whitespace."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=ensure_ascii).encode("utf-8")


def canonical_hash(obj: Any, ensure_ascii: bool = True) -> str:
    return hashlib.sha1(canonical_json(obj, ensure_ascii)).hexdigest()


def hash_variants(obj: Any) -> set[str]:
    """Both canonical forms (C2 does not say whether non-ASCII is escaped)."""
    return {canonical_hash(obj, True), canonical_hash(obj, False)}


def event_text(e: Any) -> str:
    """Report events are strings (C2); accept MSG-03 dicts too."""
    if isinstance(e, dict):
        return str(e.get("text", json.dumps(e, ensure_ascii=False)))
    return str(e)


def is_supplies_warning(text: str) -> bool:
    """Gate convention for OUT-17 in the sim report's string events: mentions supplies
    (or 'supply') AND one of warn / low / mutiny.  Stated in every message that uses it."""
    t = text.lower()
    return "suppl" in t and any(w in t for w in ("warn", "low", "mutiny"))


SUPPLIES_WARNING_RULE = ("a supplies warning is an event string containing 'suppl' and one of "
                         "'warn', 'low', 'mutiny' (case-insensitive)")


def is_mutiny_event(text: str) -> bool:
    return "mutin" in text.lower()


def is_hex_sha1(s: Any) -> bool:
    return isinstance(s, str) and re.fullmatch(r"[0-9a-f]{40}", s) is not None


def _get(d: Any, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if not isinstance(d, dict) or k not in d:
            return default
        d = d[k]
    return d


# --------------------------------------------------------------------------- report shape (C2)

REQUIRED_TOP = ("schema", "seed", "config_path", "turns_requested", "turns_completed", "outcome",
                "elapsed_s", "per_turn", "final")
REQUIRED_TURN = ("turn_no", "orders_executed", "ai_ships_moved", "player", "captains", "events",
                 "snapshot_hash", "snapshot_bytes", "resolve_ms")
REQUIRED_PLAYER = ("food", "water", "fear", "respect", "gold", "silver", "ship_class", "position")
OUTCOMES = ("running", "game_over", "mastery")


def report_shape_errors(report: Any) -> list[str]:
    """Does the JSON have the C2 shape? Checked before any checker trusts a field."""
    if not isinstance(report, dict):
        return [f"report is {type(report).__name__}, expected a JSON object (C2)"]
    errs = [f"report lacks key {k!r} (C2)" for k in REQUIRED_TOP if k not in report]
    if report.get("outcome") not in OUTCOMES:
        errs.append(f"report.outcome = {report.get('outcome')!r}, expected one of {OUTCOMES} (C2)")
    per_turn = report.get("per_turn")
    if not isinstance(per_turn, list):
        errs.append("report.per_turn is not a list (C2)")
        return errs
    for i, t in enumerate(per_turn[:3] + per_turn[-1:]):   # first three and last — enough to catch shape drift
        if not isinstance(t, dict):
            errs.append(f"per_turn[{i}] is not an object (C2)")
            continue
        for k in REQUIRED_TURN:
            if k not in t:
                errs.append(f"per_turn[{i}] lacks {k!r} (C2)")
        oe = t.get("orders_executed")
        if not isinstance(oe, dict) or "player" not in oe or "ai" not in oe:
            errs.append(f"per_turn[{i}].orders_executed must be {{player, ai}} (C2)")
        pl = t.get("player")
        if not isinstance(pl, dict):
            errs.append(f"per_turn[{i}].player is not an object (C2)")
        else:
            for k in REQUIRED_PLAYER:
                if k not in pl:
                    errs.append(f"per_turn[{i}].player lacks {k!r} (C2)")
    return sorted(set(errs))


# --------------------------------------------------------------------------- pure checkers: sim report

def check_turns_advance(report: dict, turns_requested: int) -> list[str]:
    """smoke/02 — turns advance (OV-02, TP-01…08)."""
    errs: list[str] = []
    tr, tc = report.get("turns_requested"), report.get("turns_completed")
    if tr != turns_requested:
        errs.append(f"turns_requested in the report is {tr}, the gate asked for {turns_requested}")
    if tc != turns_requested:
        errs.append(f"turns_completed = {tc}, expected {turns_requested} (outcome {report.get('outcome')!r})")
    per_turn = report.get("per_turn") or []
    if len(per_turn) != tc:
        errs.append(f"per_turn has {len(per_turn)} entries, turns_completed says {tc}")
    seen = [t.get("turn_no") for t in per_turn]
    expected = list(range(1, len(per_turn) + 1))
    if seen != expected:
        bad = next((i for i, (a, b) in enumerate(zip(seen, expected)) if a != b), None)
        where = f"per_turn[{bad}].turn_no = {seen[bad]}, expected {expected[bad]}" if bad is not None else f"{seen[:5]}…"
        errs.append(f"turn_no is not strictly 1..{len(per_turn)}: {where} — the phase loop may be returning the old state "
                    f"(see starbour/engine/phases.py resolve_turn)")
    return errs


def check_ai_acts(report: dict, min_moved_fraction: float = 0.5) -> list[str]:
    """smoke/03 — the AI acts every turn (OV-10, SP-06).

    C2 gives per-turn `ai_ships_moved` (count) but no per-ship positions, so "≥ 50 % of
    AI ships changed position at least once" is checked as a lower bound: the best single
    turn must already show ≥ 50 % of AI ships moving.  AI ship count = final.ships − 1
    ([ASSUMED] the player owns one ship in an AI-driven run; see the report)."""
    errs: list[str] = []
    per_turn = report.get("per_turn") or []
    idle = [t.get("turn_no") for t in per_turn if (_get(t, "orders_executed", "ai", default=0) or 0) < 1]
    if idle:
        errs.append(f"0 AI orders executed in turn(s) {idle[:10]}{'…' if len(idle) > 10 else ''} "
                    f"(need ≥ 1 every turn) — see starbour/engine/ai.py decide()")
    ships = _get(report, "final", "ships", default=0) or 0
    ai_total = ships - 1
    if ai_total <= 0:
        errs.append(f"final.ships = {ships}: no AI ships exist to move (SP-06 ai_captains_per_galaxy)")
        return errs
    moved = [t.get("ai_ships_moved", 0) or 0 for t in per_turn]
    best = max(moved) if moved else 0
    frac = best / ai_total
    if frac < min_moved_fraction:
        errs.append(f"at most {best} of {ai_total} AI ships ({frac:.0%}) moved in any one turn; need ≥ "
                    f"{min_moved_fraction:.0%} — AI ships are frozen scenery (ai_ships_moved per turn; "
                    f"AI count taken as final.ships − 1)")
    return errs


def check_hashes_differ(report: dict) -> list[str]:
    """smoke/04 — consecutive snapshot hashes differ (BUS-07)."""
    errs: list[str] = []
    per_turn = report.get("per_turn") or []
    hashes = [t.get("snapshot_hash") for t in per_turn]
    bad_fmt = [h for h in hashes if not is_hex_sha1(h)]
    if bad_fmt:
        errs.append(f"snapshot_hash must be a 40-char hex SHA-1 of the canonical snapshot JSON (C2); "
                    f"got e.g. {bad_fmt[0]!r}")
    same = [(per_turn[i - 1].get("turn_no"), per_turn[i].get("turn_no"))
            for i in range(1, len(hashes)) if hashes[i] == hashes[i - 1]]
    if same:
        errs.append(f"snapshot_hash identical between turns {same[0][0]} and {same[0][1]}"
                    f"{' (and %d more pairs)' % (len(same) - 1) if len(same) > 1 else ''} — the published state is frozen")
    return errs


def _player_mutiny_turns(report: dict) -> list[int]:
    out = []
    for m in _get(report, "final", "mutinies", default=[]) or []:
        if isinstance(m, dict) and str(m.get("captain", "")).lower() in ("player", _get(report, "player_id", default="player")):
            if isinstance(m.get("turn_no"), int):
                out.append(m["turn_no"])
    return sorted(out)


def check_upkeep(report: dict, food_start: int, water_start: int, warn_turns: int) -> list[str]:
    """smoke/05 — upkeep is wired to config (SUP-01, SUP-02, CFG-45/46).

    For every turn t up to the first mutiny and ≤ *_start: food == food_start − t and
    water == water_start − t (no trade in M1).  A supplies warning (OUT-17) must be
    present whenever min(food, water) ≤ warn_turns and absent otherwise, except on a
    mutiny turn."""
    errs: list[str] = []
    per_turn = report.get("per_turn") or []
    mutiny_turns = set(_player_mutiny_turns(report))
    first_mutiny = min(mutiny_turns) if mutiny_turns else None
    wrong_food = wrong_water = None
    missing_warn: list[int] = []
    spurious_warn: list[int] = []
    for t in per_turn:
        n = t.get("turn_no")
        pl = t.get("player") or {}
        food, water = pl.get("food"), pl.get("water")
        if first_mutiny is not None and n >= first_mutiny:
            break
        if n <= food_start and food != food_start - n and wrong_food is None:
            wrong_food = (n, food, food_start - n)
        if n <= water_start and water != water_start - n and wrong_water is None:
            wrong_water = (n, water, water_start - n)
        texts = [event_text(e) for e in (t.get("events") or [])]
        warned = any(is_supplies_warning(s) for s in texts)
        low = min(food if isinstance(food, int) else 10**9, water if isinstance(water, int) else 10**9) <= warn_turns
        if low and not warned:
            missing_warn.append(n)
        if warned and not low:
            spurious_warn.append(n)
    if wrong_food:
        errs.append(f"turn {wrong_food[0]}: player.food = {wrong_food[1]}, expected food_start − t = {wrong_food[2]} "
                    f"([supplies] food_start = {food_start}; SUP-01 −1 per turn)")
    if wrong_water:
        errs.append(f"turn {wrong_water[0]}: player.water = {wrong_water[1]}, expected water_start − t = {wrong_water[2]} "
                    f"([supplies] water_start = {water_start}; SUP-01)")
    if missing_warn:
        errs.append(f"no supplies warning event in turn(s) {missing_warn[:8]} although food or water ≤ warn_turns = {warn_turns} "
                    f"(OUT-17; {SUPPLIES_WARNING_RULE})")
    if spurious_warn:
        errs.append(f"a supplies warning appears in turn(s) {spurious_warn[:8]} although food and water > warn_turns = {warn_turns} "
                    f"— stale or always-on warning (OUT-17; {SUPPLIES_WARNING_RULE})")
    return errs


def check_mutiny(report: dict, food_start: int, water_start: int, mutiny_turns_at_zero: int,
                 mutiny_ship_class: str, mutiny_supplies: int, mutiny_fear_delta: int) -> list[str]:
    """smoke/06 — the mutiny fires and has its consequences (SUP-03, SUP-04b, SUP-04c, CFG-47/48, D-76).

    Expected turn T = min(food_start, water_start) + mutiny_turns_at_zero (33 with defaults)."""
    errs: list[str] = []
    per_turn = report.get("per_turn") or []
    by_turn = {t.get("turn_no"): t for t in per_turn}
    T = min(food_start, water_start) + mutiny_turns_at_zero
    if T not in by_turn or (T - 1) not in by_turn:
        errs.append(f"the run has {len(per_turn)} turns; the mutiny check needs turn {T} (and {T - 1}) present")
        return errs
    mut_turns = _player_mutiny_turns(report)
    early = [m for m in mut_turns if m < T]
    if early:
        errs.append(f"final.mutinies records a player mutiny at turn {early[0]}, before the expected turn {T} "
                    f"— timing rule: zero_turns > mutiny_turns_at_zero after consumption (D-76, SUP-03)")
    at_T = by_turn[T]
    texts = [event_text(e) for e in (at_T.get("events") or [])]
    if not any(is_mutiny_event(s) for s in texts):
        errs.append(f"no mutiny event in turn {T} (food_start {min(food_start, water_start)} + mutiny_turns_at_zero "
                    f"{mutiny_turns_at_zero}); events were {texts[:6]} — see TP-06 in starbour/engine/phases.py (OUT-18)")
    if T not in mut_turns:
        errs.append(f"final.mutinies has no entry with turn_no {T} and captain 'player' (got {mut_turns}) (C2)")
    pl, prev = at_T.get("player") or {}, by_turn[T - 1].get("player") or {}
    cls = str(pl.get("ship_class", ""))
    if cls.lower() != str(mutiny_ship_class).lower():
        errs.append(f"turn {T}: player.ship_class = {cls!r}, expected {mutiny_ship_class!r} (case-insensitive; "
                    f"[supplies] mutiny_ship_class; SUP-04b)")
    if pl.get("food") != mutiny_supplies or pl.get("water") != mutiny_supplies:
        errs.append(f"turn {T}: player food/water = {pl.get('food')}/{pl.get('water')}, expected {mutiny_supplies}/{mutiny_supplies} "
                    f"([supplies] mutiny_supplies; SUP-04b, D-72)")
    c0, c1 = by_turn[T - 1].get("captains"), at_T.get("captains")
    if not (isinstance(c0, int) and isinstance(c1, int) and c1 == c0 + 1):
        errs.append(f"captains count {c0} → {c1} across turn {T}, expected +1 (the mutineers become an AI captain; SUP-04c)")
    f0, f1 = prev.get("fear"), pl.get("fear")
    if not (isinstance(f0, int) and isinstance(f1, int) and f1 == max(0, f0 - mutiny_fear_delta)):
        errs.append(f"fear {f0} → {f1} across turn {T}, expected max(0, {f0} − {mutiny_fear_delta}) "
                    f"([supplies] mutiny_fear_delta; SUP-04b)")
    return errs


def check_determinism(seq_a: list[str], seq_b: list[str], seq_other: list[str]) -> list[str]:
    """smoke/07 — same seed + same orders = identical hashes; a different seed differs (D-67)."""
    errs: list[str] = []
    if not seq_a or not seq_b:
        errs.append("a run produced no snapshot hashes")
        return errs
    if seq_a != seq_b:
        first = next((i for i, (x, y) in enumerate(zip(seq_a, seq_b)) if x != y), min(len(seq_a), len(seq_b)))
        errs.append(f"two runs with the same seed diverge at turn {first + 1} ({seq_a[first][:8] if first < len(seq_a) else '∅'}… vs "
                    f"{seq_b[first][:8] if first < len(seq_b) else '∅'}…) — something draws from the global random or iterates a set")
    if seq_other and seq_a == seq_other:
        errs.append("a run with a different seed produced identical hashes — the seed is ignored (SP-01)")
    return errs


def check_config_honoured(report: dict, expected_food_turn1: int, food_start_in_file: int,
                          config_path: Path) -> list[str]:
    """smoke/08 — a changed ini value changes the game (CFG-45, D-49)."""
    errs: list[str] = []
    per_turn = report.get("per_turn") or []
    if not per_turn:
        errs.append("the run produced no turns")
        return errs
    food = _get(per_turn[0], "player", "food")
    if food != expected_food_turn1:
        errs.append(f"with [supplies] food_start = {food_start_in_file} in {config_path.name}, player.food at turn 1 = {food}, "
                    f"expected {expected_food_turn1} — the key is read from a literal somewhere, not from the ini")
    rp = str(report.get("config_path", ""))
    try:
        same = Path(rp).resolve() == config_path.resolve()
    except OSError:
        same = False
    if not same:
        errs.append(f"report.config_path = {rp!r}, expected the --config file {config_path} (C2)")
    return errs


def check_long_run(report: dict | None, exit_code: int, elapsed: float, limit_s: float, stderr_tail: str,
                   turns: int) -> list[str]:
    """smoke/12 — robustness over a long AI-only run (D-69)."""
    errs: list[str] = []
    if exit_code != 0:
        errs.append(f"sim exited {exit_code}" + (f"; stderr tail: {stderr_tail.strip()[-400:]}" if stderr_tail.strip() else ""))
    if "Traceback" in (stderr_tail or ""):
        errs.append("a traceback was printed")
    if elapsed > limit_s:
        errs.append(f"took {elapsed:.1f} s, bound {limit_s:.0f} s")
    if report is None:
        errs.append("no report was written")
        return errs
    oc = report.get("outcome")
    if oc not in OUTCOMES:
        errs.append(f"outcome {oc!r} not in {OUTCOMES}")
    tc = report.get("turns_completed")
    if oc == "running" and tc != turns:
        errs.append(f"outcome 'running' but turns_completed = {tc}, expected {turns}")
    return errs


# --------------------------------------------------------------------------- pure checkers: snapshot & save

def player_ship(snapshot: dict) -> tuple[str, dict]:
    """(ship_id, ship) of the player's active ship, per the C3 snapshot shape."""
    pid = snapshot.get("player_id")
    cap = _get(snapshot, "captains", pid) if pid else None
    if not isinstance(cap, dict):
        raise KeyError(f"snapshot.captains[{pid!r}] missing (C3)")
    sid = cap.get("active_ship")
    ship = _get(snapshot, "ships", sid)
    if not isinstance(ship, dict):
        raise KeyError(f"snapshot.ships[{sid!r}] missing (C3)")
    return sid, ship


def snapshot_shape_errors(snapshot: Any) -> list[str]:
    if not isinstance(snapshot, dict):
        return [f"snapshot is {type(snapshot).__name__}, expected an object (C3)"]
    errs = [f"snapshot lacks {k!r} (C3)" for k in ("turn_no", "seed", "sizes", "player_id", "captains", "ships") if k not in snapshot]
    if errs:
        return errs
    try:
        sid, ship = player_ship(snapshot)
        pos = ship.get("position")
        if not (isinstance(pos, list) and len(pos) == 3 and all(isinstance(p, list) and len(p) == 2 for p in pos)):
            errs.append(f"ships[{sid}].position = {pos!r}, expected [[gx,gy],[sx,sy],[cx,cy]] (SH-09, C3)")
    except KeyError as e:
        errs.append(str(e))
    sector = _get(snapshot, "sizes", "sector")
    if not (isinstance(sector, list) and len(sector) == 2):
        errs.append(f"sizes.sector = {sector!r}, expected [w, h] (C3)")
    return errs


def make_successor(snapshot: dict, shift: str = "x") -> tuple[dict, dict]:
    """A plausible next-turn snapshot derived from `snapshot`: turn_no + 1, player food and
    water − 1, the active ship moved one cell along `shift` ('x' or 'y') inside the sector.
    Returns (successor, meta) where meta has dx, dy and the new food/water."""
    s = copy.deepcopy(snapshot)
    s["turn_no"] = int(snapshot.get("turn_no", 0)) + 1
    pid = s["player_id"]
    cap = s["captains"][pid]
    cap["food"] = max(0, int(cap.get("food", 0)) - 1)
    cap["water"] = max(0, int(cap.get("water", 0)) - 1)
    sid, ship = player_ship(s)
    w, h = s["sizes"]["sector"]
    (gx, gy), (sx, sy), (cx, cy) = ship["position"]
    dx = dy = 0
    if shift == "x":
        dx = 1 if cx + 1 < w else -1
    else:
        dy = 1 if cy + 1 < h else -1
    ship["position"] = [[gx, gy], [sx, sy], [cx + dx, cy + dy]]
    return s, {"dx": dx, "dy": dy, "food": cap["food"], "water": cap["water"], "turn_no": s["turn_no"], "ship_id": sid}


def check_save_document(save_doc: Any, final_hash: str) -> list[str]:
    """smoke/11 — saves/game.json holds the snapshot whose hash the report ended on (C6, OV-12).

    C6 says 'the snapshot plus rng_state'; both layouts are accepted: {"snapshot": …,
    "rng_state": …} or the snapshot's keys at top level beside "rng_state"."""
    errs: list[str] = []
    if not isinstance(save_doc, dict):
        return [f"save file is {type(save_doc).__name__}, expected an object (C6)"]
    if "rng_state" not in save_doc:
        errs.append("save file lacks 'rng_state' (C6)")
    snap = save_doc.get("snapshot") if isinstance(save_doc.get("snapshot"), dict) else {k: v for k, v in save_doc.items() if k != "rng_state"}
    if not snap:
        errs.append("save file holds no snapshot (C6)")
        return errs
    if final_hash not in hash_variants(snap):
        errs.append(f"SHA-1 of the saved snapshot ({canonical_hash(snap)[:8]}…) ≠ the report's final snapshot_hash "
                    f"({str(final_hash)[:8]}…) — the save is stale or the hash is not SHA-1 of sorted-key compact JSON (C2, C6)")
    return errs


# --------------------------------------------------------------------------- pure checkers: render (C4)

def glyph_positions(text: str, glyph: str) -> set[tuple[int, int]]:
    out = set()
    for li, line in enumerate(text.splitlines()):
        start = 0
        while True:
            i = line.find(glyph, start)
            if i < 0:
                break
            out.add((li, i))
            start = i + 1
    return out


def check_render_probe(probe: dict, glyph: str, meta_x: dict, meta_y: dict, base: dict, events_a: list[dict],
                       events_b: list[dict]) -> list[str]:
    """smoke/10 — render() of each terminal is fresh and faithful (C4, BUS-05, GRD-06).

    probe = output of the render subprocess: {"renders": {role: [text, …]}, "errors": {role: str},
    "config_loader": str|None, "zmq_imported": bool, "import_errors": {module: str}}.
    base = {turn_no, food, water, fear, respect}; meta_x/meta_y from make_successor."""
    errs: list[str] = []
    if probe.get("config_loader") is None:
        errs.append("could not load GameConfig: none of " + ", ".join(probe.get("loader_candidates", [])) +
                    " exists in starbour.config — expose one of them (C4 render() takes cfg: GameConfig)")
    for mod, err in (probe.get("import_errors") or {}).items():
        errs.append(f"import {mod} failed: {err.strip().splitlines()[-1] if err.strip() else err} (C4: starbour/terminals/render_<role>.py)")
    if probe.get("zmq_imported"):
        errs.append("importing the render modules pulled zmq into sys.modules — render modules must not import zmq (C4)")
    for role, err in (probe.get("errors") or {}).items():
        errs.append(f"render_{role}.render() raised: {err.strip().splitlines()[-1] if err.strip() else err}")
    renders = probe.get("renders") or {}
    if errs and not renders:
        return errs

    def texts(role: str, n: int) -> list[str] | None:
        t = renders.get(role)
        if not isinstance(t, list) or len(t) < n or any(not isinstance(x, str) for x in t[:n]):
            errs.append(f"render_{role}: no rendered text captured")
            return None
        return t

    # T2 grid: differs; glyph tracks position
    g = texts("grid", 3)
    if g:
        a, bx, by = g
        if a == bx:
            errs.append("render_grid: identical text for two snapshots whose active ship moved one cell — stale viewport (GRD-06)")
        pa, pbx, pby = glyph_positions(a, glyph), glyph_positions(bx, glyph), glyph_positions(by, glyph)
        if not pa:
            errs.append(f"render_grid: active-ship glyph {glyph!r} (SYM-10, [ui] glyph_set) not found in the sector render")
        else:
            old, new = pa - pbx, pbx - pa
            if len(old) != 1 or len(new) != 1:
                errs.append(f"render_grid: moving the ship by dx={meta_x['dx']} should move exactly one {glyph!r}; "
                            f"positions changed {sorted(old)} → {sorted(new)}")
            else:
                (l0, c0), (l1, c1) = next(iter(old)), next(iter(new))
                if l1 != l0 or (c1 - c0) * meta_x["dx"] <= 0:
                    errs.append(f"render_grid: ship moved dx={meta_x['dx']} but the glyph went from line {l0} col {c0} to line {l1} col {c1} "
                                f"(expected same line, column {'right' if meta_x['dx'] > 0 else 'left'}) — glyph not placed at position (GRD-01)")
            old, new = pa - pby, pby - pa
            if len(old) == 1 and len(new) == 1:
                (l0, c0), (l1, c1) = next(iter(old)), next(iter(new))
                if c1 != c0 or (l1 - l0) != meta_y["dy"]:
                    errs.append(f"render_grid: ship moved dy={meta_y['dy']} but the glyph went from line {l0} col {c0} to line {l1} col {c1} "
                                f"(expected same column, {meta_y['dy']:+d} line) — one text line per grid row expected (GRD-01)")
            else:
                errs.append(f"render_grid: moving the ship by dy={meta_y['dy']} should move exactly one {glyph!r}; "
                            f"positions changed {sorted(old)} → {sorted(new)}")
    # T4 status: differs; shows turn_no, food, water (and fear, respect)
    st = texts("status", 2)
    if st:
        a, b = st
        if a == b:
            errs.append("render_status: identical text for two consecutive snapshots — stale panel (PNL-01…03)")
        for label, val in (("turn_no", base["turn_no"]), ("food", base["food"]), ("water", base["water"]),
                           ("fear", base["fear"]), ("respect", base["respect"])):
            if str(val) not in a:
                errs.append(f"render_status: {label} = {val} does not appear in the panel text (PNL-01, PNL-03, C4)")
        if str(meta_x["turn_no"]) not in b or str(meta_x["food"]) not in b:
            errs.append(f"render_status: successor turn_no {meta_x['turn_no']} / food {meta_x['food']} not shown — panel not redrawn from the new snapshot")
    # T3 output: lists every event, in order; differs
    out = texts("output", 2)
    if out:
        a, b = out
        if a == b:
            errs.append("render_output: identical text for two turn.resolved payloads with different events — stale log (OB-02)")
        for name, text, evs in (("first", a, events_a), ("second", b, events_b)):
            last = -1
            for ev in evs:
                i = text.find(ev["text"])
                if i < 0:
                    errs.append(f"render_output: event text {ev['text']!r} missing from the {name} render (OB-04 lists every event)")
                elif i < last:
                    errs.append(f"render_output: events are not in phase order in the {name} render ({ev['text']!r} appears before an earlier phase; OB-04)")
                last = max(last, i)
    # T1 command: differs; shows the turn number
    cm = texts("command", 2)
    if cm:
        a, b = cm
        if a == b:
            errs.append("render_command: identical prompt text for two consecutive snapshots (UX-01 shows the turn number)")
        if f"T{base['turn_no']}" not in a:
            errs.append(f"render_command: prompt lacks 'T{base['turn_no']}' (UX-01 format [T<turn> · <ship> · <n> orders] >)")
    return errs


RENDER_FORBIDDEN = (
    (re.compile(r"^\s*(import\s+zmq|from\s+zmq\b)", re.M), "imports zmq"),
    (re.compile(r"^\s*(import\s+time\b|from\s+time\b)", re.M), "imports time"),
    (re.compile(r"\btime\.(sleep|time|monotonic|perf_counter)\s*\(", re.M), "calls time.*()"),
    (re.compile(r"(?<![\w.])input\s*\(", re.M), "calls input()"),
)


def check_render_sources(sources: dict[str, str]) -> list[str]:
    """smoke/10 static part: render modules import no zmq and call no time/input (C4)."""
    errs = []
    for name, src in sources.items():
        for rx, what in RENDER_FORBIDDEN:
            if rx.search(src):
                errs.append(f"{name} {what} — render functions never touch sockets, time or input() (C4)")
    return errs


# --------------------------------------------------------------------------- pure checkers: bus (C3)

@dataclass(slots=True)
class BusObservations:
    hello: dict | None = None
    hello_count: int = 0
    hello_wait_s: float | None = None
    resolved: list[dict] = field(default_factory=list)          # turn.resolved payloads in arrival order
    resolved_wait_s: list[float] = field(default_factory=list)  # seconds from the turn.end push to each
    accepted: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    topics: Counter = field(default_factory=Counter)
    notes: list[str] = field(default_factory=list)
    ship_id: str | None = None
    start_turn_no: int | None = None

    def hashes(self) -> list[str]:
        return [canonical_hash(r.get("snapshot", {})) for r in self.resolved]


def check_bus_observations(obs: BusObservations, turns_expected: int = 3, need_orders: bool = True,
                           reject_id: str = "CMD-06") -> list[str]:
    """smoke/09 and live/03 — bus wiring (BUS-03/04, MSG-01…07, C3)."""
    errs: list[str] = []
    if obs.hello is None:
        errs.append(f"no state.hello received within the wait (topics seen: {dict(obs.topics) or 'none'}) — the engine must "
                    f"publish state.hello every 2 s (C3, D-70)")
    else:
        se = snapshot_shape_errors(obs.hello.get("snapshot"))
        if se:
            errs.append("state.hello.snapshot: " + "; ".join(se[:3]))
    tn = [r.get("turn_no") for r in obs.resolved]
    seq = ([obs.start_turn_no] if isinstance(obs.start_turn_no, int) else []) + tn
    if len(seq) >= 2 and any(not (isinstance(a, int) and isinstance(b, int) and b > a) for a, b in zip(seq, seq[1:])):
        errs.append(f"turn_no sequence after turn.end is not strictly increasing: started at {obs.start_turn_no}, "
                    f"turn.resolved carried {tn} — the engine republishes a stale turn (BUS-04, MSG-03)")
    fresh = [t for t in tn if isinstance(t, int) and (obs.start_turn_no is None or t > obs.start_turn_no)]
    if len(fresh) < turns_expected:
        errs.append(f"{len(fresh)} new turn.resolved received for {turns_expected} turn.end pushes "
                    f"(waits {[round(w, 1) for w in obs.resolved_wait_s]} s; {'; '.join(obs.notes) or 'no notes'}) — MSG-02 → MSG-03")
    for r in obs.resolved:
        snap_tn = _get(r, "snapshot", "turn_no")
        if snap_tn != r.get("turn_no"):
            errs.append(f"turn.resolved.turn_no {r.get('turn_no')} ≠ snapshot.turn_no {snap_tn} — payload and snapshot disagree (MSG-03)")
            break
    hs = obs.hashes()
    if len(hs) >= 2 and any(a == b for a, b in zip(hs, hs[1:])):
        errs.append("consecutive turn.resolved snapshots are byte-identical — the state does not change between turns (BUS-07)")
    if need_orders:
        if not obs.accepted:
            errs.append("no order.accepted after pushing a valid order.submitted (move 1 0) (MSG-01, MSG-07)")
        if not obs.rejected:
            errs.append("no order.rejected after pushing an invalid order.submitted (move 99 0) (MSG-06)")
        else:
            reasons = [str(r.get("reason", "")) for r in obs.rejected]
            if not any(reject_id in s for s in reasons):
                errs.append(f"order.rejected.reason does not name {reject_id}: {reasons[:2]} (CMD-06 'target outside the sector'; MSG-06)")
    return errs


# --------------------------------------------------------------------------- pure checkers: live (processes, heartbeats)

def cmdline_matches_role(cmdline: list[str] | None, root: Path, role: str | None = None) -> str | None:
    """Return the role if `cmdline` is one of our processes: has `--role <r>` and names the
    repo path (dev) or starbour.exe (frozen). Never match on python.exe alone (M1 § Known traps)."""
    if not cmdline:
        return None
    joined = " ".join(cmdline).lower()
    root_s = str(root).lower().replace("/", "\\")
    if root_s not in joined.replace("/", "\\") and "starbour.exe" not in joined:
        return None
    for i, tok in enumerate(cmdline):
        if tok == "--role" and i + 1 < len(cmdline):
            r = cmdline[i + 1].lower()
            if role is None or r == role:
                return r
        if tok.startswith("--role=") and (role is None or tok[7:].lower() == role):
            return tok[7:].lower()
    return None


def check_roles_present(found: dict[str, list[int]], required: tuple[str, ...] = ROLES) -> list[str]:
    """live/02 — the launcher has one child per role (TL, BR-15)."""
    errs = []
    missing = [r for r in required if not found.get(r)]
    if missing:
        errs.append(f"no child with --role {missing} (found {{{', '.join(f'{k}: {v}' for k, v in found.items() if v)}}}) — the launcher must spawn all of {list(required)}")
    dup = {r: pids for r, pids in found.items() if len(pids) > 1}
    if dup:
        errs.append(f"more than one process per role: {dup}")
    return errs


def check_heartbeats(hbs: dict[str, dict | None], turn_no: int, roles: tuple[str, ...] = VIEW_ROLES,
                     pids: dict[str, int] | None = None) -> list[str]:
    """live/03, live/04 — every view terminal drew the current turn (C5, D-66, BUS-05)."""
    errs = []
    for r in roles:
        hb = hbs.get(r)
        if not isinstance(hb, dict):
            errs.append(f"{r}: no heartbeat file <MOS_HEARTBEAT_DIR>/{r}.json (C5)")
            continue
        if hb.get("turn_no") != turn_no:
            errs.append(f"{r}: heartbeat turn_no {hb.get('turn_no')} ≠ current turn {turn_no} — the window shows a stale turn (BUS-05)")
        if pids and r in pids and hb.get("pid") != pids[r]:
            errs.append(f"{r}: heartbeat pid {hb.get('pid')} is not the live {r} process {pids[r]} — written by an old process")
        rm = hb.get("render_ms")
        if isinstance(rm, (int, float)) and rm > 500:
            errs.append(f"{r}: render_ms {rm} > 500 — redraw target is under 0.5 s (BUS-05)")
    return errs


def check_all_gone(remaining: list[tuple[int, str]], what: str) -> list[str]:
    """live/05, live/06 — no process survives (BUS-06, D-59)."""
    if remaining:
        return [f"{len(remaining)} process(es) still alive {what}: " + ", ".join(f"pid {p} ({r})" for p, r in remaining[:8])]
    return []


# --------------------------------------------------------------------------- I/O adapters (no pass/fail logic)

def free_ports(n: int) -> list[int]:
    """n distinct free TCP ports on 127.0.0.1 (bind-and-release)."""
    socks, ports = [], []
    try:
        for _ in range(n):
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.bind(("127.0.0.1", 0))
            socks.append(s)
            ports.append(s.getsockname()[1])
    finally:
        for s in socks:
            s.close()
    return ports


def port_holder(port: int) -> int | None:
    """PID of the process listening on 127.0.0.1:<port>, or None when free."""
    import psutil
    try:
        conns = psutil.net_connections(kind="tcp")
    except psutil.AccessDenied:
        conns = []
    for c in conns:
        if c.laddr and c.laddr.port == port and c.status == psutil.CONN_LISTEN:
            return c.pid or -1
    # fall back to a bind attempt (AccessDenied or pid hidden)
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", port))
        return None
    except OSError:
        return -1
    finally:
        s.close()


def role_processes(root: Path, role: str | None = None) -> list[tuple[int, str, list[str]]]:
    """All live processes whose cmdline has --role and the repo path (or starbour.exe)."""
    import psutil
    out = []
    for p in psutil.process_iter(["pid", "cmdline"]):
        try:
            r = cmdline_matches_role(p.info.get("cmdline"), root, role)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if r:
            out.append((p.pid, r, p.info.get("cmdline") or []))
    return out


def kill_tree(pid: int, timeout: float = 5.0) -> None:
    import psutil
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=timeout)


def read_heartbeats(hb_dir: Path, roles: tuple[str, ...] = VIEW_ROLES) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for r in roles:
        doc, _ = load_json(hb_dir / f"{r}.json")
        out[r] = doc if isinstance(doc, dict) else None
    return out


def wait_until(pred: Callable[[], bool], timeout: float, interval: float = 0.1) -> float:
    """Poll pred until true or timeout; return seconds waited (≥ timeout means it never became true)."""
    t0 = time.perf_counter()
    while True:
        if pred():
            return time.perf_counter() - t0
        if time.perf_counter() - t0 >= timeout:
            return time.perf_counter() - t0
        time.sleep(interval)


@dataclass(slots=True)
class CmdResult:
    args: list[str]
    returncode: int | None
    stdout: str
    stderr: str
    seconds: float
    timed_out: bool

    def tail(self, n: int = 25) -> str:
        lines = (self.stdout + "\n" + self.stderr).strip().splitlines()
        return "\n".join(lines[-n:])


def run_cmd(args: list[str], cwd: Path, timeout: float, env: dict | None = None) -> CmdResult:
    e = dict(os.environ)
    e["PYTHONUTF8"] = "1"
    e["PYTHONIOENCODING"] = "utf-8"
    if env:
        e.update(env)
    t0 = time.perf_counter()
    try:
        cp = subprocess.run(args, cwd=str(cwd), env=e, capture_output=True, text=True, encoding="utf-8",
                            errors="replace", timeout=timeout, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return CmdResult(args, cp.returncode, cp.stdout, cp.stderr, time.perf_counter() - t0, False)
    except subprocess.TimeoutExpired as ex:
        out = ex.stdout.decode("utf-8", "replace") if isinstance(ex.stdout, bytes) else (ex.stdout or "")
        err = ex.stderr.decode("utf-8", "replace") if isinstance(ex.stderr, bytes) else (ex.stderr or "")
        return CmdResult(args, None, out, err, time.perf_counter() - t0, True)


# --------------------------------------------------------------------------- the bus client (C3)

class BusClient:
    """SUB + PUSH client speaking CONTRACTS C3. Collects BusObservations; judges nothing."""

    def __init__(self, pub_port: int, pull_port: int, host: str = "127.0.0.1") -> None:
        import zmq
        self._zmq = zmq
        self.ctx = zmq.Context()
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.setsockopt(zmq.SUBSCRIBE, b"")
        self.sub.setsockopt(zmq.LINGER, 0)
        self.sub.connect(f"tcp://{host}:{pub_port}")
        self.push = self.ctx.socket(zmq.PUSH)
        self.push.setsockopt(zmq.LINGER, 0)
        self.push.connect(f"tcp://{host}:{pull_port}")
        self.poller = zmq.Poller()
        self.poller.register(self.sub, zmq.POLLIN)
        self.log: list[tuple[float, str, dict]] = []

    def close(self) -> None:
        for s in (self.sub, self.push):
            try:
                s.close(0)
            except Exception:
                pass
        self.ctx.term()

    def send(self, topic: str, payload: dict) -> None:
        self.push.send_multipart([topic.encode("ascii"), json.dumps(payload).encode("utf-8")])

    def recv(self, timeout_s: float) -> tuple[str, dict] | None:
        if not self.poller.poll(int(timeout_s * 1000)):
            return None
        frames = self.sub.recv_multipart()
        if len(frames) != 2:
            return ("<malformed:%d frames>" % len(frames), {})
        topic = frames[0].decode("ascii", "replace")
        try:
            payload = json.loads(frames[1].decode("utf-8"))
        except ValueError:
            payload = {"<raw>": frames[1][:80].decode("utf-8", "replace")}
        self.log.append((time.perf_counter(), topic, payload))
        return topic, payload

    def wait_for(self, topic: str, timeout_s: float, obs: BusObservations, pred: Callable[[dict], bool] | None = None) -> dict | None:
        deadline = time.perf_counter() + timeout_s
        while True:
            left = deadline - time.perf_counter()
            if left <= 0:
                return None
            got = self.recv(min(left, 0.5))
            if got is None:
                continue
            t, p = got
            obs.topics[t] += 1
            if t == "state.hello":
                obs.hello_count += 1
            if t == topic and (pred is None or pred(p)):
                return p


def drive_engine(pub_port: int, pull_port: int, *, turns: int = 3, hello_timeout: float = 15.0, turn_timeout: float = 10.0,
                 reply_timeout: float = 5.0, orders: bool = True,
                 on_resolved: Callable[[dict], None] | None = None) -> BusObservations:
    """Connect, wait for state.hello (or a turn.resolved), optionally submit one valid and one
    invalid order, then push turn.end `turns` times, recording every turn.resolved."""
    obs = BusObservations()
    client = BusClient(pub_port, pull_port)
    try:
        t0 = time.perf_counter()
        first = client.wait_for("state.hello", hello_timeout, obs,
                                pred=None)
        # accept a turn.resolved as the state source too (live gate may join mid-game)
        if first is None:
            for ts, topic, payload in client.log:
                if topic == "turn.resolved" and isinstance(payload.get("snapshot"), dict):
                    first = {"turn_no": payload.get("turn_no"), "snapshot": payload["snapshot"]}
                    obs.notes.append("no state.hello; used a turn.resolved snapshot as the starting state")
                    break
        if first is None:
            return obs
        obs.hello = first
        obs.hello_wait_s = time.perf_counter() - t0
        snapshot = first.get("snapshot") or {}
        turn_no = int(first.get("turn_no") or snapshot.get("turn_no") or 0)
        try:
            sid, _ship = player_ship(snapshot)
            obs.ship_id = sid
        except KeyError as e:
            obs.notes.append(str(e))
            sid = None
        if orders and sid is not None:
            client.send("order.submitted", {"command": "move", "args": ["1", "0"], "ship_id": sid, "turn_no": turn_no, "client": "command"})
            acc = client.wait_for("order.accepted", reply_timeout, obs)
            if acc is not None:
                obs.accepted.append(acc)
            client.send("order.submitted", {"command": "move", "args": ["99", "0"], "ship_id": sid, "turn_no": turn_no, "client": "command"})
            rej = client.wait_for("order.rejected", reply_timeout, obs)
            if rej is not None:
                obs.rejected.append(rej)
        obs.start_turn_no = turn_no
        for _ in range(turns):
            t1 = time.perf_counter()
            client.send("turn.end", {"turn_no": turn_no})
            res = client.wait_for("turn.resolved", turn_timeout, obs,
                                  pred=lambda p: isinstance(p.get("turn_no"), int) and p["turn_no"] > turn_no)
            obs.resolved_wait_s.append(time.perf_counter() - t1)
            if res is None:
                # record stale republishes (turn_no not newer) so the checker can name them
                stale = [p for ts, t, p in client.log if t == "turn.resolved" and ts >= t1]
                if stale:
                    obs.notes.append(f"after turn.end for turn {turn_no}: {len(stale)} turn.resolved with turn_no "
                                     f"{sorted({p.get('turn_no') for p in stale})} — none newer")
                    obs.resolved.extend(stale[:1])
                break
            obs.resolved.append(res)
            if on_resolved:
                on_resolved(res)
            turn_no = res["turn_no"]
        return obs
    finally:
        client.close()
