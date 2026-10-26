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
import math
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
    until: str | None = None      # last milestone the check applies to (D-106 scopes smoke/05 to ≤ M3)


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
            if chk.until is not None and milestone_index(milestone()) > milestone_index(chk.until):
                raise Skip(f"applies only while specs/MILESTONE ≤ {chk.until} (D-106); it is {milestone()}")
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

def check_turns_advance(report: dict, turns_requested: int, allow_end: bool = False) -> list[str]:
    """smoke/02 — turns advance (OV-02, TP-01…08).

    D-106 (M2 and after, `allow_end=True`): a run that ends early by `game_over` / `mastery`
    passes when the matching `final.*_turn` is set and `turns_completed` equals it."""
    errs: list[str] = []
    tr, tc = report.get("turns_requested"), report.get("turns_completed")
    oc = report.get("outcome")
    if tr != turns_requested:
        errs.append(f"turns_requested in the report is {tr}, the gate asked for {turns_requested}")
    if allow_end and oc in ("game_over", "mastery"):
        key = "game_over_turn" if oc == "game_over" else "mastery_turn"
        et = _get(report, "final", key)
        if not isinstance(et, int):
            errs.append(f"outcome {oc!r} but final.{key} = {et!r} — the ending turn must be recorded (D-106, C2)")
        elif tc != et:
            errs.append(f"outcome {oc!r} with final.{key} = {et}, but turns_completed = {tc} — the sim stops the turn the game ends (D-106, C9)")
        elif tc > turns_requested:
            errs.append(f"turns_completed = {tc} exceeds the {turns_requested} requested (outcome {oc!r})")
    elif tc != turns_requested:
        hint = " — a run ended by game_over/mastery passes only from M2 (D-106)" if oc in ("game_over", "mastery") else ""
        errs.append(f"turns_completed = {tc}, expected {turns_requested} (outcome {oc!r}){hint}")
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
                   turns: int, expect_hazard_deaths: bool = False) -> list[str]:
    """smoke/12 — robustness over a long AI-only run (D-69).  From M2 `final.hazard_deaths`
    must be reported as a count ≥ 0 (C9)."""
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
    if expect_hazard_deaths:
        hd = _get(report, "final", "hazard_deaths")
        if not isinstance(hd, int) or isinstance(hd, bool) or hd < 0:
            errs.append(f"final.hazard_deaths = {hd!r}, expected an integer ≥ 0 (C9 final additions; D-69)")
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
        got = self.wait_for_any((topic,), timeout_s, obs, pred)
        return got[1] if got else None

    def wait_for_any(self, topics: tuple[str, ...], timeout_s: float, obs: BusObservations,
                     pred: Callable[[dict], bool] | None = None) -> tuple[str, dict] | None:
        """The first message whose topic is one of `topics` (and passes `pred`), or None on timeout.
        Every message received meanwhile is counted in obs.topics and kept in self.log."""
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
            if t in topics and (pred is None or pred(p)):
                return t, p


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


# =========================================================================== M2 — specs/M2.md § 3, CONTRACTS C9
#
# Everything below drives the C9 fields only: `sim --teleport / --player-script / --world-index`,
# the per-turn report additions (player.hazard, shield_current, move_remaining, warps, destroyed),
# the snapshot additions (move_remaining, hazard, orders, game_over), the game.over message and
# the T2 / T4 / T3 render additions.  Scenarios are *plans* built from `world_index` and the ini
# (never hard-coded coordinates, M2 § 3); `check_scenario` judges a C2 report against a plan, so
# --selftest can prove every plan-based row with a canned report.  Prose conventions the gate
# relies on (stated in every message that uses them):
#   * a ship held by gravity produces an event whose text contains "held" (M2 § 1 "held by gravity");
#   * a warp that fails at resolution produces an event whose text contains "warp" (HZ-10, MV-03).
# Countdown arithmetic follows D-100 and the C9 example (pull 1.0 → turns_left 6 at turn 1):
# the counter is incremented at TP-03 before the comparison, so turn t in range reads N − t and the
# ship is destroyed at turn N = ceil(pull_turns_scale / pull).

INDEX_LISTS = ("stars", "black_holes", "neutron_stars", "planets")
NEIGHBOURS = ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1))
HELD_RULE = "a held ship is an event string containing 'held' (M2 § 1: hazard event 'held by gravity')"
WARP_FAIL_RULE = "a failed warp is an event string containing 'warp' (HZ-10, MV-03: effective factor ≤ 0 → the warp fails with a hazard event)"


# --------------------------------------------------------------------------- formulas (tab 7 / 8 / 9)

def flat_sector(galaxy: Any, sector: Any, galaxy_size: Any) -> tuple[int, int]:
    """MV-02: flattened sector coordinates (gx × galaxy_w + sx, gy × galaxy_h + sy)."""
    return (int(galaxy[0]) * int(galaxy_size[0]) + int(sector[0]), int(galaxy[1]) * int(galaxy_size[1]) + int(sector[1]))


def chebyshev(a: Any, b: Any) -> int:
    return max(abs(int(a[0]) - int(b[0])), abs(int(a[1]) - int(b[1])))


def pull_at(mass: float, distance: int, k_g: float, falloff: float) -> float:
    """DQ-05: pull = k_g × mass / (1 + d)^falloff."""
    return float(k_g) * float(mass) / (1.0 + float(distance)) ** float(falloff)


def countdown_turns(pull: float, scale: float) -> int:
    """HZ-02 / D-100: N = ceil(pull_turns_scale / pull).  A 1e-9 guard keeps 7/0.7 at 10, not 11."""
    return int(math.ceil(float(scale) / float(pull) - 1e-9))


def warp_reach(factor: float, base: float, exponent: float, anywhere_at: float = 10.0) -> float:
    """MV-02: reach = w × base^((w/10)^exponent); w ≥ 10 → anywhere (inf)."""
    w = float(factor)
    if w >= anywhere_at:
        return math.inf
    return w * float(base) ** ((w / 10.0) ** float(exponent))


def signed(v: int) -> str:
    """Relative move argument: +dx / -dx (D-73: a signed number is relative)."""
    return f"{int(v):+d}"


def _close(a: Any, b: Any, tol: float) -> bool:
    return isinstance(a, (int, float)) and not isinstance(a, bool) and abs(float(a) - float(b)) <= tol


def _norm(x: Any) -> Any:
    if isinstance(x, (list, tuple)):
        return [_norm(v) for v in x]
    return x


def _short(x: Any, n: int = 160) -> str:
    s = json.dumps(x, ensure_ascii=False) if isinstance(x, (dict, list)) else repr(x)
    return s if len(s) <= n else s[:n] + "…"


# --------------------------------------------------------------------------- world index (C9 --world-index)

def world_index_shape_errors(index: Any) -> list[str]:
    if not isinstance(index, dict):
        return [f"world_index is {type(index).__name__}, expected an object (C9 --world-index)"]
    errs: list[str] = []
    ps = index.get("player_start")
    if not (isinstance(ps, list) and len(ps) == 3 and all(isinstance(p, list) and len(p) == 2 for p in ps)):
        errs.append(f"world_index.player_start = {_short(ps)}, expected [[gx,gy],[sx,sy],[cx,cy]] (C9)")
    for name in INDEX_LISTS:
        lst = index.get(name)
        if not isinstance(lst, list):
            errs.append(f"world_index.{name} is missing or not a list (C9)")
            continue
        for i, e in enumerate(lst[:3]):
            if not isinstance(e, dict):
                errs.append(f"world_index.{name}[{i}] is not an object (C9)")
                continue
            for k in ("galaxy", "sector", "cell"):
                v = e.get(k)
                if not (isinstance(v, list) and len(v) == 2):
                    errs.append(f"world_index.{name}[{i}].{k} = {v!r}, expected [x, y] (C9)")
            if name == "stars":
                if not isinstance(e.get("mass"), (int, float)):
                    errs.append(f"world_index.stars[{i}].mass = {e.get('mass')!r}, expected a number (C9; D-89 mass = volume relative to radius 1)")
                if not isinstance(e.get("bucket"), str):
                    errs.append(f"world_index.stars[{i}].bucket = {e.get('bucket')!r}, expected low|medium|high (C9, EB-01…03)")
            if name == "black_holes" and "wormhole_to" not in e:
                errs.append(f"world_index.black_holes[{i}] lacks 'wormhole_to' (null or {{galaxy, sector, cell}}; C9)")
    return errs


def _gs(entry: dict) -> tuple[tuple[int, int], tuple[int, int]]:
    return (tuple(int(v) for v in entry["galaxy"]), tuple(int(v) for v in entry["sector"]))


def terrain_cells(index: dict, galaxy: Any, sector: Any) -> set[tuple[int, int]]:
    """Every indexed terrain cell (star, hole, neutron star, planet) of one sector."""
    key = (tuple(int(v) for v in galaxy), tuple(int(v) for v in sector))
    out: set[tuple[int, int]] = set()
    for name in INDEX_LISTS:
        for e in index.get(name) or []:
            if isinstance(e, dict) and _gs(e) == key:
                out.add((int(e["cell"][0]), int(e["cell"][1])))
    return out


def in_sector(cell: Any, sector_size: Any) -> bool:
    return 0 <= int(cell[0]) < int(sector_size[0]) and 0 <= int(cell[1]) < int(sector_size[1])


def pick_star(index: dict, *, k_g: float, falloff: float, scale: float, sector_size: Any,
              bucket: str = "medium") -> tuple[dict, tuple[int, int]] | None:
    """A `bucket` star with a free neighbouring cell (for the countdown-reset exit).  Prefers a
    star whose countdown equals a Sun-like star's (mass 1 → N = ceil(scale / k_g), 7 with the
    defaults), then the mass nearest 1.0, then the nearest to the player's start (list order).
    Returns (star, exit_offset)."""
    target_n = countdown_turns(pull_at(1.0, 0, k_g, falloff), scale)
    best: tuple | None = None
    for i, s in enumerate(index.get("stars") or []):
        if not isinstance(s, dict) or str(s.get("bucket", "")).lower() != bucket:
            continue
        mass = s.get("mass")
        if not isinstance(mass, (int, float)) or mass <= 0:
            continue
        blocked = terrain_cells(index, s["galaxy"], s["sector"])
        cx, cy = int(s["cell"][0]), int(s["cell"][1])
        exit_ = next(((dx, dy) for dx, dy in NEIGHBOURS
                      if in_sector((cx + dx, cy + dy), sector_size) and (cx + dx, cy + dy) not in blocked), None)
        if exit_ is None:
            continue
        n = countdown_turns(pull_at(mass, 0, k_g, falloff), scale)
        key = (0 if n == target_n else 1, abs(float(mass) - 1.0), i)
        if best is None or key < best[0]:
            best = (key, s, exit_)
    return (best[1], best[2]) if best else None


def pick_holes(index: dict, *, wormhole: bool | None) -> list[dict]:
    out = []
    for h in index.get("black_holes") or []:
        if not isinstance(h, dict):
            continue
        if wormhole is None or bool(h.get("wormhole_to")) == wormhole:
            out.append(h)
    return out


def _sign(v: int) -> int:
    return (v > 0) - (v < 0)


def cell_at_distance(index: dict, obj: dict, d: int, sector_size: Any, *, diagonal: bool = True,
                     path: tuple[int, ...] = ()) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """A free cell at Chebyshev distance `d` from `obj`'s cell such that obj + sign × k is also
    free and inside the sector for every k in `path` (the escape route).  Returns (cell, sign)."""
    blocked = terrain_cells(index, obj["galaxy"], obj["sector"])
    hx, hy = int(obj["cell"][0]), int(obj["cell"][1])
    signs = ((1, 1), (-1, -1), (1, -1), (-1, 1)) if diagonal else ((1, 0), (-1, 0), (0, 1), (0, -1))
    for sx, sy in signs:
        cells = [(hx + sx * k, hy + sy * k) for k in (d, *path)]
        if all(in_sector(c, sector_size) and c not in blocked for c in cells):
            return cells[0], (sx, sy)
    if not path:
        for dx in range(-d, d + 1):
            for dy in range(-d, d + 1):
                if max(abs(dx), abs(dy)) != d:
                    continue
                c = (hx + dx, hy + dy)
                if in_sector(c, sector_size) and c not in blocked:
                    return c, (_sign(dx), _sign(dy))
    return None


def existing_sectors(snapshot: dict) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Every (galaxy, sector) the snapshot lists (galaxy layout is always visible, GRD-04)."""
    out = []
    for key, g in (snapshot.get("galaxies") or {}).items():
        try:
            gx, gy = (int(v) for v in str(key).split(","))
        except ValueError:
            continue
        for s in (g or {}).get("sectors") or []:
            out.append(((gx, gy), (int(s[0]), int(s[1]))))
    return out


def sectors_at_distance(existing: list, start: tuple, d: int, galaxy_size: Any) -> list[tuple]:
    f0 = flat_sector(start[0], start[1], galaxy_size)
    return [gs for gs in existing if chebyshev(flat_sector(gs[0], gs[1], galaxy_size), f0) == d]


def farthest_sector(existing: list, start: tuple, galaxy_size: Any) -> tuple | None:
    f0 = flat_sector(start[0], start[1], galaxy_size)
    return max(existing, key=lambda gs: chebyshev(flat_sector(gs[0], gs[1], galaxy_size), f0), default=None)


def void_sector(snapshot: dict, universe_size: Any, galaxy_size: Any) -> tuple | None:
    """A sector coordinate inside the universe that holds no sector (Void), from the snapshot."""
    galaxies = {tuple(int(v) for v in g) for g in (snapshot.get("universe") or {}).get("galaxies") or []}
    listed: dict[tuple[int, int], set[tuple[int, int]]] = {}
    for key, g in (snapshot.get("galaxies") or {}).items():
        try:
            gx, gy = (int(v) for v in str(key).split(","))
        except ValueError:
            continue
        listed[(gx, gy)] = {(int(s[0]), int(s[1])) for s in (g or {}).get("sectors") or []}
    for gxy, secs in sorted(listed.items()):
        for sx in range(int(galaxy_size[0])):
            for sy in range(int(galaxy_size[1])):
                if (sx, sy) not in secs:
                    return (gxy, (sx, sy))
    for gx in range(int(universe_size[0])):
        for gy in range(int(universe_size[1])):
            if (gx, gy) not in galaxies:
                return ((gx, gy), (0, 0))
    return None


def centre_block_free(index: dict, galaxy: Any, sector: Any, sector_size: Any, tol: float) -> bool:
    cx, cy = (int(sector_size[0]) - 1) / 2.0, (int(sector_size[1]) - 1) / 2.0
    return not any(max(abs(c[0] - cx), abs(c[1] - cy)) <= tol for c in terrain_cells(index, galaxy, sector))


def teleport_arg(galaxy: Any, sector: Any, cell: Any) -> str:
    """C9: --teleport "gx gy sx sy cx cy"."""
    return f"{int(galaxy[0])} {int(galaxy[1])} {int(sector[0])} {int(sector[1])} {int(cell[0])} {int(cell[1])}"


# --------------------------------------------------------------------------- scenario plans (pure)
#
# A scenario is {"script": [line per turn], "teleport": str | None, "turns": T, "plan": {...}}.
# plan = {"what", "ids", "turns": [{"turn": n, <expectations>, "note"}], "end": {...}, "final": {...},
#         "save_present": bool}.  Per-turn expectations: cell, sector, cell_near {centre, tol},
# hazard (None | "any" | {kind, distance, pull, turns_left}), shield, move_remaining (None | [dx, dy]),
# events_contain [substr], warps [{to, from, factor, effective}], no_warps.

def plan_persistent_move(start: Any, direction: tuple[int, int], engine: float, *, variant: str = "persist",
                         distance: int = 10, perpendicular: tuple[int, int] = (1, 0)) -> dict:
    """smoke/13 — CMD-06, CMD-21, OV-02b, D-98.  `start` = [[gx,gy],[sx,sy],[cx,cy]]; the path
    must be free of terrain (the caller chose `direction` from world_index)."""
    (gx, gy), (sx, sy), (cx, cy) = start
    dx, dy = direction
    step = int(math.floor(float(engine)))          # MV-01: cells = floor(speed − pull), pull 0 here
    order = f"move {signed(dx * distance)} {signed(dy * distance)}"
    ids = "CMD-06, CMD-21, OV-02b, D-98, C9"

    def cell(moved: int, px: int = 0, py: int = 0) -> list[int]:
        return [cx + dx * moved + px, cy + dy * moved + py]

    def rem(moved: int) -> list[int] | None:
        left = distance - moved
        return [dx * left, dy * left] if left > 0 else None

    turns: list[dict] = []
    if variant == "persist":
        script = [order, "", "", "", ""]
        for t in range(1, 6):
            moved = min(distance, t * step)
            turns.append({"turn": t, "cell": cell(moved), "move_remaining": rem(moved), "hazard": None,
                          "note": f"{order}: floor(engine {engine:g}) = {step} cells per turn, not clipped; persists until it arrives (D-98)"})
        what = f"smoke/13 persistent move ({order} from {cx},{cy})"
    elif variant == "stop":
        script = [order, "stop", "", ""]
        turns.append({"turn": 1, "cell": cell(step), "move_remaining": rem(step), "hazard": None})
        for t in (2, 3, 4):
            turns.append({"turn": t, "cell": cell(step), "move_remaining": None, "hazard": None,
                          "note": "'stop' at turn 2 cancels the persisting move (CMD-21)"})
        what = f"smoke/13 stop ({order}, then stop)"
    elif variant == "replace":
        px, py = perpendicular
        second = f"move {signed(px * 2)} {signed(py * 2)}"
        script = [order, second, ""]
        turns.append({"turn": 1, "cell": cell(step), "move_remaining": rem(step), "hazard": None})
        for t in (2, 3):
            turns.append({"turn": t, "cell": cell(step, px * 2, py * 2), "move_remaining": None, "hazard": None,
                          "note": f"a second move ({second}) at turn 2 replaces the persisting one (OV-02b same-kind rule)"})
        what = f"smoke/13 replace ({order}, then {second})"
    else:
        raise ValueError(variant)
    return {"script": script, "teleport": None, "turns": len(script),
            "plan": {"what": what, "ids": ids, "turns": turns, "end": {"outcome": "running", "turns": len(script)}}}


def plan_star_fatal(star: dict, *, k_g: float, falloff: float, scale: float, drain: float, shield_max: float,
                    extra_turns: int = 2, label: str = "") -> dict:
    """smoke/15 — HZ-02, HZ-03, HZ-09 (0 floor), OV-09b, OV-09c, D-100."""
    pull = pull_at(float(star["mass"]), 0, k_g, falloff)
    n = countdown_turns(pull, scale)
    cell = [int(star["cell"][0]), int(star["cell"][1])]
    turns = []
    for t in range(1, n):
        turns.append({"turn": t, "cell": cell, "shield": max(0.0, float(shield_max) - t * float(drain)),
                      "hazard": {"kind": "star", "distance": 0, "pull": pull, "turns_left": n - t},
                      "note": f"star mass {float(star['mass']):.3f} → pull {pull:.3f}, N = ceil({scale:g} / {pull:.3f}) = {n}; "
                              f"shields drain {drain:g} per turn (bucket {star.get('bucket')}) and floor at 0"})
    what = f"smoke/15 star fatal{label} (star at {cell[0]},{cell[1]} in {star['galaxy']}/{star['sector']}, N = {n})"
    return {"script": [""] * (n + extra_turns), "teleport": teleport_arg(star["galaxy"], star["sector"], cell), "turns": n + extra_turns,
            "plan": {"what": what, "ids": "HZ-02, HZ-03, HZ-09, OV-09b, OV-09c, D-100, C9", "turns": turns,
                     "end": {"outcome": "game_over", "turn": n, "cause": "star", "cell": cell},
                     "final": {"hazard_deaths_min": 1}, "save_present": False}}


def plan_countdown_reset(star: dict, exit_offset: tuple[int, int], *, k_g: float, falloff: float, scale: float,
                         drain: float, recharge: float, shield_max: float, total_turns: int = 14) -> dict:
    """smoke/16 — HZ-08, CM-05: in the cell, out, back in (counter reset), out again (recharge to the cap)."""
    pull = pull_at(float(star["mass"]), 0, k_g, falloff)
    n = countdown_turns(pull, scale)
    cx, cy = int(star["cell"][0]), int(star["cell"][1])
    ex, ey = exit_offset
    out_cell = [cx + ex, cy + ey]
    in_cell = [cx, cy]
    stay = max(1, min(3, n - 2))                        # turns inside before leaving (never reach 0)
    script: list[str] = []
    turns: list[dict] = []
    shield = float(shield_max)
    counter = 0
    inside = True
    t = 0
    # phase 1: inside for `stay` turns; phase 2: out (3 turns); phase 3: back in (1 turn); phase 4: out for the rest
    plan_moves = {stay + 1: f"move {signed(ex)} {signed(ey)}", stay + 4: f"move {signed(-ex)} {signed(-ey)}",
                  stay + 5: f"move {signed(ex)} {signed(ey)}"}
    for t in range(1, total_turns + 1):
        script.append(plan_moves.get(t, ""))
        if t == stay + 1 or t == stay + 5:
            inside = False
        elif t == stay + 4:
            inside = True
        if inside:
            counter += 1
            shield = max(0.0, shield - float(drain))
            exp = {"turn": t, "cell": in_cell, "shield": shield,
                   "hazard": {"kind": "star", "distance": 0, "pull": pull, "turns_left": n - counter}}
            if t == stay + 4:
                exp["note"] = f"re-entry: the counter must reset on leaving the cell, so turns_left reads N − 1 = {n - 1} again (HZ-08)"
        else:
            counter = 0
            shield = min(float(shield_max), shield + float(recharge))
            exp = {"turn": t, "cell": out_cell, "shield": shield, "hazard": None,
                   "note": f"outside any hazard shields recharge {recharge:g} per turn at TP-06, capped at shield_max {shield_max:g} (CM-05)"}
        turns.append(exp)
    what = f"smoke/16 countdown reset (star at {cx},{cy}, exit {signed(ex)} {signed(ey)}, N = {n})"
    return {"script": script, "teleport": teleport_arg(star["galaxy"], star["sector"], in_cell), "turns": total_turns,
            "plan": {"what": what, "ids": "HZ-08, CM-05, HZ-03, D-100, C9", "turns": turns,
                     "end": {"outcome": "running", "turns": total_turns}}}


def plan_hole_held(hole: dict, cell: tuple[int, int], away: tuple[int, int], *, kind: str, mass: float, k_g: float,
                   falloff: float, scale: float, engine: float, shield_max: float) -> dict:
    """smoke/17 (a) — HZ-04, CFG-16: engine ≤ pull → held every turn; destroyed at N."""
    d = chebyshev(cell, hole["cell"])
    pull = pull_at(mass, d, k_g, falloff)
    n = countdown_turns(pull, scale)
    cells = int(math.floor(float(engine) - pull))
    order = f"move {signed(away[0])} {signed(away[1])}"
    turns = []
    for t in range(1, n):
        turns.append({"turn": t, "cell": list(cell), "shield": float(shield_max), "events_contain": ["held"],
                      "hazard": {"kind": kind, "distance": d, "pull": pull, "turns_left": n - t},
                      "note": f"{order} with floor(engine {engine:g} − pull {pull:.3f}) = {cells} ≤ 0 cells: held ({HELD_RULE})"})
    what = f"smoke/17 held by a {kind.replace('_', ' ')} (distance {d}, pull {pull:.3f}, N = {n})"
    return {"script": [order] * (n + 1), "teleport": teleport_arg(hole["galaxy"], hole["sector"], cell), "turns": n + 1,
            "plan": {"what": what, "ids": "HZ-04, HZ-06, CFG-16, CFG-17, D-100, C9", "turns": turns,
                     "end": {"outcome": "game_over", "turn": n, "cause": kind, "cell": list(cell)},
                     "final": {"hazard_deaths_min": 1}, "save_present": False}}


def escape_path(d0: int, steps: int, *, engine: float, mass: float, k_g: float, falloff: float) -> list[int]:
    """Distances after each of `steps` moves directly away at full engine power from distance d0
    (MV-01: floor(engine − pull at departure) cells per turn, at least 1)."""
    out, d = [], int(d0)
    for _ in range(steps):
        d += max(1, int(math.floor(float(engine) - pull_at(mass, d, k_g, falloff))))
        out.append(d)
    return out


def plan_hole_escape(hole: dict, cell: tuple[int, int], sign: tuple[int, int], *, kind: str, mass: float, k_g: float,
                     falloff: float, scale: float, engine: float, steps: int = 3, total_turns: int = 10) -> dict:
    """smoke/17 (b) — HZ-04, D-100: moving directly away at full engine power lengthens the countdown."""
    turns = []
    cur = (int(cell[0]), int(cell[1]))
    script: list[str] = []
    counter = 0
    for t in range(1, total_turns + 1):
        d0 = chebyshev(cur, hole["cell"])
        pull0 = pull_at(mass, d0, k_g, falloff)
        if t <= steps:
            step = max(1, int(math.floor(float(engine) - pull0)))
            cur = (cur[0] + sign[0] * step, cur[1] + sign[1] * step)
            script.append(f"move {signed(sign[0] * step)} {signed(sign[1] * step)}")
        else:
            script.append("")
        counter += 1
        d1 = chebyshev(cur, hole["cell"])
        pull1 = pull_at(mass, d1, k_g, falloff)
        n1 = countdown_turns(pull1, scale)
        turns.append({"turn": t, "cell": list(cur), "hazard": {"kind": kind, "distance": d1, "pull": pull1, "turns_left": n1 - counter},
                      "note": (f"after moving away at full engine power the pull at distance {d1} is {pull1:.3f} → "
                               f"N = {n1}, counter {counter} (D-100: moving outward buys time)") if t <= steps else
                              f"holding at distance {d1}: N = {n1}, counter {counter}"})
    what = f"smoke/17 escape from a {kind.replace('_', ' ')} (from distance {chebyshev(cell, hole['cell'])}, {steps} moves away)"
    return {"script": script, "teleport": teleport_arg(hole["galaxy"], hole["sector"], cell), "turns": total_turns,
            "plan": {"what": what, "ids": "HZ-04, CFG-16, D-100, C9", "turns": turns,
                     "end": {"outcome": "running", "turns": total_turns}}}


def plan_neutron_fatal(star: dict, cell: tuple[int, int], *, mass: float, k_g: float, falloff: float, scale: float,
                       shield_max: float) -> dict:
    """smoke/17 (c) — HZ-06, CFG-17: destroyed at N with no shield drain."""
    d = chebyshev(cell, star["cell"])
    pull = pull_at(mass, d, k_g, falloff)
    n = countdown_turns(pull, scale)
    turns = [{"turn": t, "cell": list(cell), "shield": float(shield_max),
              "hazard": {"kind": "neutron_star", "distance": d, "pull": pull, "turns_left": n - t},
              "note": "a neutron star pulls but never drains shields (HZ-06)"} for t in range(1, n)]
    what = f"smoke/17 neutron star (distance {d}, pull {pull:.3f}, N = {n})"
    return {"script": [""] * (n + 1), "teleport": teleport_arg(star["galaxy"], star["sector"], cell), "turns": n + 1,
            "plan": {"what": what, "ids": "HZ-06, CFG-17, D-100, C9", "turns": turns,
                     "end": {"outcome": "game_over", "turn": n, "cause": "neutron_star", "cell": list(cell)},
                     "final": {"hazard_deaths_min": 1}, "save_present": False}}


def plan_wormhole(hole: dict, cell: tuple[int, int], *, mass: float, k_g: float, falloff: float, scale: float) -> dict:
    """smoke/18 (a) — HZ-05, D-102: pulled in at N → alive in the partner's sector, inside its range."""
    d = chebyshev(cell, hole["cell"])
    pull = pull_at(mass, d, k_g, falloff)
    n = countdown_turns(pull, scale)
    partner = hole["wormhole_to"]
    turns = [{"turn": t, "cell": list(cell), "hazard": {"kind": "black_hole", "distance": d, "turns_left": n - t}} for t in range(1, n)]
    turns.append({"turn": n, "sector": [list(partner["galaxy"]), list(partner["sector"])], "hazard": "any",
                  "note": f"pulled into a wormhole at turn N = {n}: the ship emerges alive in the partner's sector "
                          f"{partner['galaxy']}/{partner['sector']} inside the partner's pull range (HZ-05, D-102)"})
    what = f"smoke/18 wormhole crossing (distance {d}, N = {n})"
    return {"script": [""] * n, "teleport": teleport_arg(hole["galaxy"], hole["sector"], cell), "turns": n,
            "plan": {"what": what, "ids": "HZ-05, ST-04, D-102, C9", "turns": turns,
                     "end": {"outcome": "running", "turns": n}, "final": {"warps_min": 0}}}


def plan_plain_hole_kills(hole: dict, cell: tuple[int, int], *, mass: float, k_g: float, falloff: float, scale: float) -> dict:
    """smoke/18 (b) — a plain (unpaired) black hole at the same distance destroys (D-102)."""
    d = chebyshev(cell, hole["cell"])
    pull = pull_at(mass, d, k_g, falloff)
    n = countdown_turns(pull, scale)
    turns = [{"turn": t, "cell": list(cell), "hazard": {"kind": "black_hole", "distance": d, "turns_left": n - t}} for t in range(1, n)]
    what = f"smoke/18 plain black hole (distance {d}, N = {n})"
    return {"script": [""] * (n + 1), "teleport": teleport_arg(hole["galaxy"], hole["sector"], cell), "turns": n + 1,
            "plan": {"what": what, "ids": "HZ-04, HZ-05, D-102, C9", "turns": turns,
                     "end": {"outcome": "game_over", "turn": n, "cause": "black_hole", "cell": list(cell)},
                     "final": {"hazard_deaths_min": 1}, "save_present": False}}


def plan_warp_arrival(start: Any, dest: tuple, factor: float, *, pull_start: float, sector_size: Any, teleport: str | None = None,
                      label: str = "", turns: int = 2) -> dict:
    """smoke/19 (a)/(d) — CMD-07, MV-02, MV-05: arrives, warps[0] recorded, cell near the sector centre."""
    (gx, gy), (sx, sy) = start[0], start[1]
    dgal, dsec = dest
    centre = [(int(sector_size[0]) - 1) / 2.0, (int(sector_size[1]) - 1) / 2.0]
    turns_ = [{"turn": 1, "sector": [list(dgal), list(dsec)], "cell_near": {"centre": centre, "tol": 1.5},
               "warps": [{"from": [[gx, gy], [sx, sy]], "to": [list(dgal), list(dsec)], "factor": float(factor),
                          "effective": float(factor) - float(pull_start)}],
               "note": f"warp factor {factor:g} (effective {float(factor) - float(pull_start):g}); arrival at the free cell nearest the sector centre (MV-05)"}]
    what = f"smoke/19 warp{label} to {list(dgal)}/{list(dsec)} at factor {factor:g}"
    script = [f"warp {dgal[0]} {dgal[1]} {dsec[0]} {dsec[1]} {factor:g}"] + [""] * (turns - 1)
    return {"script": script, "teleport": teleport, "turns": turns,
            "plan": {"what": what, "ids": "CMD-07, MV-02, MV-05, D-101, C9", "turns": turns_,
                     "end": {"outcome": "running", "turns": turns}, "final": {"warps_min": 1}}}


def plan_warp_pull(hole: dict, cell: tuple[int, int], dest: tuple, factor: float, *, mass: float, k_g: float, falloff: float,
                   scale: float, require_event: bool) -> dict:
    """smoke/19 (f) — HZ-10, MV-03: effective = factor − pull at departure; the ship stays put."""
    d = chebyshev(cell, hole["cell"])
    pull = pull_at(mass, d, k_g, falloff)
    eff = float(factor) - pull
    n = countdown_turns(pull, scale)
    dgal, dsec = dest
    exp = {"turn": 1, "sector": [list(hole["galaxy"]), list(hole["sector"])], "no_warps": True,
           "note": f"warp factor {factor:g} from distance {d} of a black hole: effective = {factor:g} − {pull:.3f} = {eff:.2f}"
                   + (f" ≤ 0 → the warp fails at resolution with a hazard event ({WARP_FAIL_RULE})" if require_event else
                      " → the destination is beyond reach(effective), the ship must not arrive (HZ-10, MV-03)")}
    if require_event:
        exp["events_contain"] = ["warp"]
    what = f"smoke/19 warp under pull (distance {d}, effective {eff:.2f})"
    return {"script": [f"warp {dgal[0]} {dgal[1]} {dsec[0]} {dsec[1]} {factor:g}"], "teleport": teleport_arg(hole["galaxy"], hole["sector"], cell),
            "turns": 1, "plan": {"what": what, "ids": "HZ-10, MV-03, CMD-07, C9", "turns": [exp],
                                 "end": {"outcome": "running", "turns": 1} if n > 1 else {}}}


# --------------------------------------------------------------------------- the scenario checker (pure)

def check_scenario(report: Any, plan: dict, *, save_present: bool | None = None) -> list[str]:
    """Judge a C2+C9 report against a plan from the builders above (smoke/13, 15–19)."""
    what, ids = plan.get("what", "scenario"), plan.get("ids", "")
    errs: list[str] = []

    def E(msg: str) -> None:
        errs.append(f"{what}: {msg}" + (f" ({ids})" if ids else ""))

    shape = report_shape_errors(report)
    if shape:
        E("report shape: " + "; ".join(shape[:4]))
        return errs
    per_turn = report.get("per_turn") or []
    by_turn = {t.get("turn_no"): t for t in per_turn if isinstance(t, dict)}
    final = report.get("final") or {}
    for exp in plan.get("turns") or []:
        n = exp["turn"]
        note = f" — {exp['note']}" if exp.get("note") else ""
        t = by_turn.get(n)
        if t is None:
            E(f"turn {n} is missing from per_turn (the run has {len(per_turn)} turn(s), outcome {report.get('outcome')!r}, "
              f"game_over_turn {final.get('game_over_turn')}){note}")
            continue
        pl = t.get("player") or {}
        pos = pl.get("position")
        if any(k in exp for k in ("cell", "sector", "cell_near")):
            if not (isinstance(pos, list) and len(pos) == 3 and all(isinstance(p, list) and len(p) == 2 for p in pos)):
                E(f"turn {n}: player.position = {_short(pos)}, expected [[gx,gy],[sx,sy],[cx,cy]] (SH-09){note}")
            else:
                if "cell" in exp and _norm(pos[2]) != _norm(exp["cell"]):
                    E(f"turn {n}: player cell {pos[2]}, expected {list(exp['cell'])}{note}")
                if "sector" in exp and _norm(pos[:2]) != _norm(exp["sector"]):
                    E(f"turn {n}: player in galaxy {pos[0]} sector {pos[1]}, expected galaxy {list(exp['sector'][0])} sector {list(exp['sector'][1])}{note}")
                if "cell_near" in exp:
                    (cx, cy), tol = exp["cell_near"]["centre"], exp["cell_near"]["tol"]
                    if max(abs(pos[2][0] - cx), abs(pos[2][1] - cy)) > tol + 1e-9:
                        E(f"turn {n}: arrival cell {pos[2]} is not within {tol} of the sector centre ({cx}, {cy}) (MV-05){note}")
        if "hazard" in exp:
            if "hazard" not in pl:
                E(f"turn {n}: per_turn.player has no 'hazard' field (C9: null or {{kind, cell, distance, pull, turns_left, drain}}){note}")
            else:
                hz, want = pl.get("hazard"), exp["hazard"]
                if want is None:
                    if hz is not None:
                        E(f"turn {n}: player.hazard = {_short(hz)}, expected null — no hazard in range here{note}")
                elif want == "any":
                    if not isinstance(hz, dict):
                        E(f"turn {n}: player.hazard = {hz!r}, expected a hazard object{note}")
                elif not isinstance(hz, dict):
                    E(f"turn {n}: player.hazard = {hz!r}, expected {_short(want)}{note}")
                else:
                    if "kind" in want and hz.get("kind") != want["kind"]:
                        E(f"turn {n}: hazard.kind = {hz.get('kind')!r}, expected {want['kind']!r}{note}")
                    if "distance" in want and hz.get("distance") != want["distance"]:
                        E(f"turn {n}: hazard.distance = {hz.get('distance')!r}, expected {want['distance']} (Chebyshev, HZ-04){note}")
                    if "pull" in want and not _close(hz.get("pull"), want["pull"], 0.01):
                        E(f"turn {n}: hazard.pull = {hz.get('pull')!r}, expected {want['pull']:.3f} ± 0.01 (DQ-05: k_g × mass / (1 + d)^falloff){note}")
                    if "turns_left" in want and hz.get("turns_left") != want["turns_left"]:
                        E(f"turn {n}: hazard.turns_left = {hz.get('turns_left')!r}, expected {want['turns_left']} (D-100: ceil(pull_turns_scale / pull) − "
                          f"consecutive turns in range, the counter incremented at TP-03 before the comparison; C9 example pull 1.0 → 6 at turn 1){note}")
        if "shield" in exp:
            if "shield_current" not in pl:
                E(f"turn {n}: per_turn.player has no 'shield_current' (C9){note}")
            elif not _close(pl.get("shield_current"), exp["shield"], 1e-6):
                E(f"turn {n}: player.shield_current = {pl.get('shield_current')!r}, expected {exp['shield']:g}{note}")
        if "move_remaining" in exp:
            if "move_remaining" not in pl:
                E(f"turn {n}: per_turn.player has no 'move_remaining' (C9: null or [dx, dy]){note}")
            else:
                mr, want = pl.get("move_remaining"), exp["move_remaining"]
                if want is None and mr is not None:
                    E(f"turn {n}: player.move_remaining = {mr!r}, expected null{note}")
                elif want is not None and not (isinstance(mr, list) and len(mr) == 2 and _norm([int(v) for v in mr]) == _norm(want)):
                    E(f"turn {n}: player.move_remaining = {mr!r}, expected {list(want)}{note}")
        for sub in exp.get("events_contain") or []:
            texts = [event_text(e) for e in (t.get("events") or [])]
            if not any(sub.lower() in s.lower() for s in texts):
                E(f"turn {n}: no event mentions {sub!r}; events: {[s[:60] for s in texts[:6]]}{note}")
        if "warps" in exp:
            ws = t.get("warps")
            if not isinstance(ws, list):
                E(f"turn {n}: per_turn.warps is missing or not a list (C9: [{{ship, from, to, factor, effective}}]){note}")
            else:
                for i, w in enumerate(exp["warps"]):
                    if i >= len(ws) or not isinstance(ws[i], dict):
                        E(f"turn {n}: warps has {len(ws)} entr{'y' if len(ws) == 1 else 'ies'}, expected entry {i} = {_short(w)}{note}")
                        break
                    got = ws[i]
                    for k in ("to", "from"):
                        if k in w and _norm(got.get(k)) != _norm(w[k]):
                            E(f"turn {n}: warps[{i}].{k} = {got.get(k)!r}, expected {w[k]}{note}")
                    for k in ("factor", "effective"):
                        if k in w and not _close(got.get(k), w[k], 0.01):
                            E(f"turn {n}: warps[{i}].{k} = {got.get(k)!r}, expected {w[k]:.2f} ± 0.01{note}")
        if exp.get("no_warps"):
            ws = t.get("warps")
            if isinstance(ws, list):
                ok = [w for w in ws if isinstance(w, dict) and not (isinstance(w.get("effective"), (int, float)) and w["effective"] <= 0)]
                if ok:
                    E(f"turn {n}: a warp was recorded ({_short(ok[0])}), expected none{note}")
    end = plan.get("end") or {}
    oc, tc = report.get("outcome"), report.get("turns_completed")
    if end.get("outcome") == "game_over":
        n, cause = end["turn"], end.get("cause")
        if oc != "game_over":
            E(f"outcome = {oc!r}, expected 'game_over' at turn {n} — the player's active ship must be destroyed and the game end there (OV-09b)")
        got = final.get("game_over_turn")
        if got != n:
            E(f"final.game_over_turn = {got!r}, expected {n}")
        if tc != n or len(per_turn) != n:
            E(f"turns_completed = {tc} with {len(per_turn)} per_turn entries, expected {n} — the sim stops the turn the game ends (C9, D-106)")
        gc = final.get("game_over_cause")
        if not (isinstance(gc, str) and gc.strip()):
            E(f"final.game_over_cause = {gc!r}, expected a non-empty string (C9)")
        t = by_turn.get(n)
        if t is not None:
            ds = t.get("destroyed")
            if not isinstance(ds, list):
                E(f"turn {n}: per_turn.destroyed is missing or not a list (C9: [{{ship, captain, cause, cell}}])")
            else:
                match = [d for d in ds if isinstance(d, dict) and d.get("cause") == cause
                         and ("cell" not in end or _norm(d.get("cell")) == _norm(end["cell"]))]
                if not match:
                    E(f"turn {n}: destroyed = {_short(ds)}, expected an entry with cause {cause!r}"
                      + (f" at cell {list(end['cell'])}" if "cell" in end else "") + " (OV-09b, OUT-16)")
    elif end.get("outcome") == "running":
        T = end["turns"]
        if oc != "running":
            E(f"outcome = {oc!r} (game_over_turn {final.get('game_over_turn')!r}, cause {final.get('game_over_cause')!r}), "
              f"expected 'running' — the ship must survive through turn {T}")
        elif tc != T:
            E(f"turns_completed = {tc}, expected {T}")
    fin = plan.get("final") or {}
    if "hazard_deaths_min" in fin:
        hd = final.get("hazard_deaths")
        if not isinstance(hd, int) or isinstance(hd, bool):
            E(f"final.hazard_deaths = {hd!r}, expected an integer (C9)")
        elif hd < fin["hazard_deaths_min"]:
            E(f"final.hazard_deaths = {hd}, expected ≥ {fin['hazard_deaths_min']}")
    if "warps_min" in fin:
        wc = final.get("warps")
        if not isinstance(wc, int) or isinstance(wc, bool):
            E(f"final.warps = {wc!r}, expected an integer count (C9)")
        elif wc < fin["warps_min"]:
            E(f"final.warps = {wc}, expected ≥ {fin['warps_min']}")
    if save_present is not None and "save_present" in plan and bool(save_present) != bool(plan["save_present"]):
        if plan["save_present"]:
            E("the save file is missing after the run (OV-12, C6)")
        else:
            E("the save file still exists after game over — delete it before publishing game.over (OV-09c, C6, M2 § 5)")
    return errs


# --------------------------------------------------------------------------- bus collectors (I/O) and checkers (pure)

def _order(sid: str | None, turn_no: int, command: str, args: list[str]) -> dict:
    return {"command": command, "args": [str(a) for a in args], "ship_id": sid, "turn_no": turn_no, "client": "command"}


def drive_orders_listing(pub_port: int, pull_port: int, *, move_args: list[str], hello_timeout: float = 15.0,
                         reply_timeout: float = 5.0, turn_timeout: float = 10.0) -> dict:
    """smoke/14 (bus part): queue a persisting move, list orders, end the turn, list again."""
    obs: dict = {"hello": None, "ship_id": None, "move_reply": None, "orders_before": None, "resolved_turn": None,
                 "ship_after": None, "orders_after": None, "notes": []}
    bo = BusObservations()
    client = BusClient(pub_port, pull_port)
    try:
        first = client.wait_for("state.hello", hello_timeout, bo)
        if first is None:
            obs["notes"].append(f"no state.hello within {hello_timeout:.0f} s (topics seen: {dict(bo.topics) or 'none'})")
            return obs
        obs["hello"] = {"turn_no": first.get("turn_no"), "seed": first.get("seed")}
        snapshot = first.get("snapshot") or {}
        turn_no = int(first.get("turn_no") or snapshot.get("turn_no") or 0)
        try:
            sid, _ = player_ship(snapshot)
        except KeyError as e:
            obs["notes"].append(str(e))
            return obs
        obs["ship_id"] = sid

        def submit(command: str, args: list[str]) -> dict | None:
            client.send("order.submitted", _order(sid, turn_no, command, args))
            got = client.wait_for_any(("order.accepted", "order.rejected"), reply_timeout, bo)
            return {"topic": got[0], "payload": got[1]} if got else None

        obs["move_reply"] = submit("move", move_args)
        obs["orders_before"] = submit("orders", [])
        client.send("turn.end", {"turn_no": turn_no})
        res = client.wait_for("turn.resolved", turn_timeout, bo, pred=lambda p: isinstance(p.get("turn_no"), int) and p["turn_no"] > turn_no)
        if res is None:
            obs["notes"].append(f"no turn.resolved within {turn_timeout:.0f} s of turn.end")
            return obs
        obs["resolved_turn"] = res.get("turn_no")
        turn_no = int(res["turn_no"])
        obs["ship_after"] = ((res.get("snapshot") or {}).get("ships") or {}).get(sid)
        obs["orders_after"] = submit("orders", [])
        return obs
    finally:
        client.close()


def _args_pattern(values: list) -> str:
    return r"\s+".join(r"\+?" + re.escape(str(int(v))) if str(v).lstrip("+-").isdigit() and int(v) >= 0 else re.escape(str(v)) for v in values)


def check_orders_listing(obs: dict, move_args: list[str], remaining: list[int], cells_per_turn: int) -> list[str]:
    """smoke/14 (bus part) — PNL-07, CMD-16, D-99: T1 `orders` lists the queued move and, after the
    turn, the persisting move with its remaining vector; the snapshot carries `orders` and
    `move_remaining` (C9)."""
    errs: list[str] = []
    if obs.get("hello") is None:
        return ["no state.hello from the --bus engine: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C3)"]
    mv = obs.get("move_reply")
    if mv is None:
        errs.append(f"no reply to order.submitted move {move_args} (MSG-07)")
    elif mv.get("topic") != "order.accepted":
        errs.append(f"move {move_args} was rejected: {(mv.get('payload') or {}).get('reason')!r} — a relative move inside the sector is valid (CMD-06, D-73)")
    pat_before = re.compile(r"move\s+" + _args_pattern(move_args), re.I)
    ob = obs.get("orders_before")
    if ob is None:
        errs.append("no reply to the immediate command 'orders' before the turn (CMD-16; D-92: answered with order.accepted carrying text)")
    elif ob.get("topic") != "order.accepted":
        errs.append(f"'orders' was rejected before the turn: {(ob.get('payload') or {}).get('reason')!r} (CMD-16, D-92)")
    elif not pat_before.search(str((ob.get("payload") or {}).get("text", ""))):
        errs.append(f"'orders' before the turn does not list 'move {' '.join(move_args)}'; text was {str((ob.get('payload') or {}).get('text', ''))[:120]!r} (PNL-07, D-99: T1 'orders' lists the same)")
    if obs.get("resolved_turn") is None:
        errs.append("no turn.resolved after turn.end: " + "; ".join(obs.get("notes") or ["no notes"]) + " (MSG-02 → MSG-03)")
        return errs
    ship = obs.get("ship_after")
    if not isinstance(ship, dict):
        errs.append(f"the player's ship {obs.get('ship_id')!r} is missing from the turn.resolved snapshot")
    else:
        if "move_remaining" not in ship:
            errs.append("snapshot.ships[*] has no 'move_remaining' (C9 snapshot additions)")
        elif not (isinstance(ship.get("move_remaining"), list) and _norm([int(v) for v in ship["move_remaining"]]) == _norm(remaining)):
            errs.append(f"snapshot.ships[*].move_remaining = {ship.get('move_remaining')!r} after the turn, expected {remaining} "
                        f"(a {' '.join(move_args)} move at {cells_per_turn} cells per turn persists; D-98)")
        orders = ship.get("orders")
        if not isinstance(orders, list):
            errs.append("snapshot.ships[*].orders is missing or not a list (C9: [{command, args}] for PNL-07)")
        else:
            def is_persist(o: Any) -> bool:
                try:
                    return isinstance(o, dict) and o.get("command") == "move" and [int(a) for a in o.get("args") or []] == list(remaining)
                except (TypeError, ValueError):
                    return False
            if not any(is_persist(o) for o in orders):
                errs.append(f"snapshot.ships[*].orders = {_short(orders)} after the turn, expected the persisting move as "
                            f"{{'command': 'move', 'args': {[signed(v) for v in remaining]}}} (C9, PNL-07, OV-02b)")
    oa = obs.get("orders_after")
    pat_after = re.compile(r"move\s+" + _args_pattern(remaining), re.I)
    if oa is None:
        errs.append("no reply to 'orders' after the turn (CMD-16)")
    elif oa.get("topic") != "order.accepted":
        errs.append(f"'orders' was rejected after the turn: {(oa.get('payload') or {}).get('reason')!r} (CMD-16)")
    elif not pat_after.search(str((oa.get("payload") or {}).get("text", ""))):
        errs.append(f"'orders' after the turn does not list the persisting move with its remaining vector 'move {' '.join(signed(v) for v in remaining)}'; "
                    f"text was {str((oa.get('payload') or {}).get('text', ''))[:120]!r} (PNL-07, D-99)")
    return errs


def drive_order_cases(pub_port: int, pull_port: int, cases: list[dict], *, hello_timeout: float = 15.0,
                      reply_timeout: float = 5.0) -> dict:
    """smoke/19 (bus part): submit each case's order and record the reply."""
    obs: dict = {"hello": None, "replies": [], "notes": []}
    bo = BusObservations()
    client = BusClient(pub_port, pull_port)
    try:
        first = client.wait_for("state.hello", hello_timeout, bo)
        if first is None:
            obs["notes"].append(f"no state.hello within {hello_timeout:.0f} s (topics seen: {dict(bo.topics) or 'none'})")
            return obs
        snapshot = first.get("snapshot") or {}
        obs["hello"] = {"turn_no": first.get("turn_no"), "seed": first.get("seed")}
        turn_no = int(first.get("turn_no") or snapshot.get("turn_no") or 0)
        try:
            sid, _ = player_ship(snapshot)
        except KeyError as e:
            obs["notes"].append(str(e))
            return obs
        for case in cases:
            client.send("order.submitted", _order(sid, turn_no, case["command"], case["args"]))
            got = client.wait_for_any(("order.accepted", "order.rejected"), reply_timeout, bo)
            obs["replies"].append({"label": case["label"], "topic": got[0] if got else None,
                                   "reason": str((got[1] or {}).get("reason", "")) if got else None})
        return obs
    finally:
        client.close()


_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


def check_order_cases(obs: dict, cases: list[dict]) -> list[str]:
    """Each case: {label, command, args, expect: accepted|rejected, reason_all: [regex], reach: float|None}.
    A `reach` must appear in the rejection reason as a number within 0.0051 (2 d.p., CMD-07)."""
    errs: list[str] = []
    if obs.get("hello") is None:
        return ["no state.hello from the --bus engine: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C3)"]
    replies = {r.get("label"): r for r in obs.get("replies") or []}
    for case in cases:
        r = replies.get(case["label"])
        order = f"{case['command']} {' '.join(str(a) for a in case['args'])}"
        if r is None or r.get("topic") is None:
            errs.append(f"{case['label']}: no reply to order.submitted '{order}' (MSG-06 / MSG-07)")
            continue
        topic, reason = r.get("topic"), r.get("reason") or ""
        want = f"order.{case['expect']}"
        if topic != want:
            errs.append(f"{case['label']}: '{order}' → {topic} (reason {reason[:100]!r}), expected {want} — {case.get('why', '')}".rstrip(" —"))
            continue
        for rx in case.get("reason_all") or []:
            if not re.search(rx, reason, re.I):
                errs.append(f"{case['label']}: rejection reason {reason[:140]!r} does not mention /{rx}/ — {case.get('why', '')}".rstrip(" —"))
        reach = case.get("reach")
        if reach is not None:
            nums = [float(m) for m in _NUMBER.findall(reason)]
            if not any(abs(v - reach) <= 0.0051 for v in nums):
                errs.append(f"{case['label']}: rejection reason {reason[:140]!r} does not state the reach {reach:.2f} (2 d.p.) — "
                            f"CMD-07: the message states the reach for that factor (MV-02: w × base^((w/10)^exponent))")
    return errs


def drive_game_over(pub_port: int, pull_port: int, *, turns: int, save_path: Path | None, order_after_over: bool = True,
                    new_game: bool = True, turn_after_new: bool = True, hello_timeout: float = 15.0, turn_timeout: float = 10.0,
                    reply_timeout: float = 5.0, over_timeout: float = 5.0, file_wait: float = 3.0,
                    after_over: Callable[[dict], Any] | None = None) -> dict:
    """smoke/21 and live/08: end `turns` turns with the player parked where it dies at turn `turns`,
    then exercise the game-over state machine (OV-09b, OV-09c, CMD-19, D-105, C9).

    `after_over(obs)` runs the moment game.over has arrived — before the save check and before anything
    else is pushed; its return value is kept as obs["after_over"].  live/08 reads the windows' heartbeats
    there: once 'new' is out the engine publishes state.hello at turn 0 and the windows redraw it within
    milliseconds, so a heartbeat check made after this driver returns races them (specs/M2.md § 3 orders
    "heartbeats reach turn N" before "push new"; § 7 gate note)."""
    obs: dict = {"hello": None, "seed": None, "ship_id": None, "resolved": [], "last_snapshot_game_over": None,
                 "game_over": None, "game_over_wait_s": None, "after_over": None, "save_present_after_over": None,
                 "after_over_reply": None, "hello_after_over": None, "new_reply": None, "hello_after_new": None,
                 "resolved_after_new": None, "save_present_after_new_turn": None, "notes": []}
    bo = BusObservations()
    client = BusClient(pub_port, pull_port)
    try:
        first = client.wait_for("state.hello", hello_timeout, bo)
        if first is None:
            obs["notes"].append(f"no state.hello within {hello_timeout:.0f} s (topics seen: {dict(bo.topics) or 'none'})")
            return obs
        snapshot = first.get("snapshot") or {}
        obs["hello"] = {"turn_no": first.get("turn_no")}
        obs["seed"] = first.get("seed", snapshot.get("seed"))
        turn_no = int(first.get("turn_no") or snapshot.get("turn_no") or 0)
        try:
            sid, _ = player_ship(snapshot)
        except KeyError as e:
            obs["notes"].append(str(e))
            sid = None
        obs["ship_id"] = sid
        for _ in range(turns):
            client.send("turn.end", {"turn_no": turn_no})
            res = client.wait_for("turn.resolved", turn_timeout, bo, pred=lambda p: isinstance(p.get("turn_no"), int) and p["turn_no"] > turn_no)
            if res is None:
                obs["notes"].append(f"no turn.resolved within {turn_timeout:.0f} s after turn.end for turn {turn_no}")
                return obs
            turn_no = int(res["turn_no"])
            obs["resolved"].append(turn_no)
            obs["last_snapshot_game_over"] = (res.get("snapshot") or {}).get("game_over")
        t0 = time.perf_counter()
        over = next((p for ts, t, p in client.log if t == "game.over"), None)
        if over is None:
            over = client.wait_for("game.over", over_timeout, bo)
        obs["game_over"] = over
        obs["game_over_wait_s"] = round(time.perf_counter() - t0, 2)
        if over is None:
            obs["notes"].append(f"no game.over within {over_timeout:.0f} s of the turn.resolved of turn {turn_no} (topics seen: {dict(bo.topics)})")
            return obs
        if after_over is not None:
            obs["after_over"] = after_over(obs)          # live/08: the windows at turn N, read before 'new' goes out
        if save_path is not None:
            wait_until(lambda: not save_path.exists(), file_wait, 0.1)
            obs["save_present_after_over"] = save_path.exists()
        if order_after_over:
            client.send("order.submitted", _order(sid, turn_no, "move", ["1", "0"]))
            got = client.wait_for_any(("order.accepted", "order.rejected"), reply_timeout, bo)
            obs["after_over_reply"] = {"topic": got[0], "reason": str(got[1].get("reason", ""))} if got else None
            hello = client.wait_for("state.hello", reply_timeout, bo, pred=lambda p: (p.get("snapshot") or {}).get("game_over") is True)
            obs["hello_after_over"] = {"turn_no": hello.get("turn_no"), "game_over": True} if hello else None
        if new_game:
            old_seed = obs["seed"]
            client.send("order.submitted", _order(sid, turn_no, "new", []))
            got = client.wait_for_any(("order.accepted", "order.rejected"), reply_timeout, bo)
            obs["new_reply"] = {"topic": got[0], "reason": str(got[1].get("reason", ""))} if got else None
            hello = client.wait_for("state.hello", reply_timeout, bo,
                                    pred=lambda p: p.get("turn_no") == 0 and (p.get("seed", (p.get("snapshot") or {}).get("seed")) != old_seed))
            if hello is None:
                obs["notes"].append(f"no state.hello with turn_no 0 and a seed ≠ {old_seed} within {reply_timeout:.0f} s of 'new'")
            else:
                snap = hello.get("snapshot") or {}
                obs["hello_after_new"] = {"turn_no": hello.get("turn_no"), "seed": hello.get("seed", snap.get("seed")),
                                          "game_over": snap.get("game_over", "<missing>")}
                if turn_after_new:
                    client.send("turn.end", {"turn_no": 0})
                    res = client.wait_for("turn.resolved", turn_timeout, bo, pred=lambda p: p.get("turn_no") == 1)
                    obs["resolved_after_new"] = res.get("turn_no") if res else None
                    if save_path is not None:
                        wait_until(save_path.exists, file_wait, 0.1)
                        obs["save_present_after_new_turn"] = save_path.exists()
        return obs
    finally:
        client.close()


GAME_OVER_SUMMARY_KEYS = ("turns", "sectors_visited", "mutinies", "fear", "respect", "gold", "silver")


def check_game_over(obs: dict, expected_turn: int, *, reject_id: str = "CMD-19", order_after_over: bool = True,
                    new_game: bool = True, turn_after_new: bool = True, save_checked: bool = True) -> list[str]:
    """smoke/21, live/08 — OV-09b, OV-09c, CMD-19, MSG-09, D-105, C9."""
    errs: list[str] = []
    if obs.get("hello") is None:
        return ["no state.hello from the engine: " + "; ".join(obs.get("notes") or ["no notes"]) + " (C3)"]
    got = obs.get("resolved") or []
    if got != list(range(1, expected_turn + 1)):
        errs.append(f"turn.resolved turn numbers {got[:12]}, expected 1..{expected_turn}: " + "; ".join(obs.get("notes") or ["no notes"])
                    + " (MSG-02 → MSG-03)")
        if len(got) < expected_turn:
            return errs
    over = obs.get("game_over")
    if not isinstance(over, dict):
        errs.append(f"no game.over message after the player's ship was destroyed at turn {expected_turn}: "
                    + "; ".join(obs.get("notes") or ["no notes"]) + " — publish game.over (MSG-09, OV-09b); in --bus mode the engine must stay up "
                    "after it so T1 can send 'new' (CMD-19, D-105)")
        return errs
    if over.get("turn_no") != expected_turn:
        errs.append(f"game.over.turn_no = {over.get('turn_no')!r}, expected {expected_turn} (MSG-09)")
    if not (isinstance(over.get("cause"), str) and over["cause"].strip()):
        errs.append(f"game.over.cause = {over.get('cause')!r}, expected a sentence naming the hazard (C9, OUT-16)")
    summary = over.get("summary")
    if not isinstance(summary, dict):
        errs.append(f"game.over.summary = {_short(summary)}, expected the run summary object (C9)")
    else:
        missing = [k for k in GAME_OVER_SUMMARY_KEYS if k not in summary]
        if missing:
            errs.append(f"game.over.summary lacks {missing} (C9: {list(GAME_OVER_SUMMARY_KEYS)})")
    if obs.get("last_snapshot_game_over") is not True:
        errs.append(f"turn.resolved snapshot.game_over = {obs.get('last_snapshot_game_over')!r} at turn {expected_turn}, expected true (C9 snapshot additions)")
    if save_checked and obs.get("save_present_after_over") is True:
        errs.append("saves\\game.json still exists after game.over — delete the save before publishing it (OV-09c, C6, M2 § 5)")
    if order_after_over:
        rep = obs.get("after_over_reply")
        if rep is None:
            errs.append("no reply to order.submitted 'move 1 0' after game over — answer every order except new/quit with order.rejected (CMD-19, D-105)")
        elif rep.get("topic") != "order.rejected":
            errs.append(f"'move 1 0' after game over was accepted ({rep.get('topic')}) — after game.over the engine accepts only 'new' and 'quit' (CMD-19, D-105)")
        elif reject_id not in str(rep.get("reason", "")):
            errs.append(f"order.rejected after game over does not cite {reject_id}: {str(rep.get('reason'))[:120]!r} (C9: 'game over — type new to start again (CMD-19)')")
        if obs.get("hello_after_over") is None:
            errs.append("no state.hello with snapshot.game_over = true after game over — the banner needs it (C9, D-105)")
    if new_game:
        nr = obs.get("new_reply")
        if nr is not None and nr.get("topic") == "order.rejected":
            errs.append(f"'new' was rejected after game over: {str(nr.get('reason'))[:120]!r} (CMD-19, OV-09c)")
        hn = obs.get("hello_after_new")
        if hn is None:
            errs.append("no state.hello at turn 0 with a new seed after 'new': " + "; ".join(obs.get("notes") or ["no notes"])
                        + " (CMD-19, OV-09c: new game = new seed; C9)")
        else:
            if hn.get("game_over") is not False:
                errs.append(f"state.hello after 'new' has snapshot.game_over = {hn.get('game_over')!r}, expected false (C9)")
            if hn.get("seed") is None:
                errs.append("state.hello after 'new' carries no seed (C3: {turn_no, snapshot, seed, version})")
            elif hn.get("seed") == obs.get("seed"):
                errs.append(f"state.hello after 'new' carries the old seed {hn.get('seed')!r} (OV-09c: new game = new seed)")
        if turn_after_new and hn is not None:
            if obs.get("resolved_after_new") != 1:
                errs.append(f"turn.resolved after 'new' + turn.end carried turn_no {obs.get('resolved_after_new')!r}, expected 1")
            elif save_checked and obs.get("save_present_after_new_turn") is False:
                errs.append("saves\\game.json does not exist after the first turn of the new game (OV-12, C6)")
    return errs


# --------------------------------------------------------------------------- AI hazard awareness (pure)

def _hazard_sectors(index: dict) -> dict[tuple, list[dict]]:
    out: dict[tuple, list[dict]] = {}
    for name, kind in (("black_holes", "black_hole"), ("neutron_stars", "neutron_star")):
        for h in index.get(name) or []:
            if isinstance(h, dict):
                out.setdefault(_gs(h), []).append({"kind": kind, "cell": (int(h["cell"][0]), int(h["cell"][1]))})
    return out


def check_ai_hazards(report: dict | None, snapshots: list[dict], index: dict) -> tuple[list[str], dict]:
    """smoke/20 — D-103, D-105: no AI ship dies by a star; an AI ship in a hole's sector never moves
    closer to the hole; an AI move never ends on an indexed terrain cell; every AI captain owns a
    ship.  Returns (problems, stats)."""
    errs: list[str] = []
    stats = {"ai_in_hole_sectors": 0, "ai_moves_checked": 0, "ai_star_deaths": 0, "turns": len(snapshots)}
    if report is not None:
        pid = (snapshots[0].get("player_id") if snapshots else None) or report.get("player_id")
        deaths = []
        for t in report.get("per_turn") or []:
            for d in t.get("destroyed") or []:
                if isinstance(d, dict) and d.get("cause") == "star" and d.get("captain") != pid:
                    deaths.append((t.get("turn_no"), d.get("ship"), d.get("captain")))
        stats["ai_star_deaths"] = len(deaths)
        if deaths:
            errs.append(f"{len(deaths)} AI ship(s) destroyed by a star, e.g. turn {deaths[0][0]} ship {deaths[0][1]} of {deaths[0][2]} — "
                        f"an AI move never ends on a star cell (D-103, OV-10)")
    hazards = _hazard_sectors(index)
    terrain_cache: dict[tuple, set] = {}
    prev: dict | None = None
    closer: list[str] = []
    onto: list[str] = []
    orphans: list[str] = []
    for snap in snapshots:
        pid = snap.get("player_id")
        ships = snap.get("ships") or {}
        owners = {s.get("captain") for s in ships.values() if isinstance(s, dict)}
        for cid, cap in (snap.get("captains") or {}).items():
            if cid != pid and not (cap or {}).get("is_player") and cid not in owners:
                orphans.append(f"turn {snap.get('turn_no')}: captain {cid} owns no ship")
        for sid, s in ships.items():
            if not isinstance(s, dict) or s.get("captain") == pid:
                continue
            pos = s.get("position")
            if not (isinstance(pos, list) and len(pos) == 3):
                continue
            key = (tuple(int(v) for v in pos[0]), tuple(int(v) for v in pos[1]))
            cell = (int(pos[2][0]), int(pos[2][1]))
            if key in hazards and prev is None:
                stats["ai_in_hole_sectors"] += 1
            if prev is None:
                continue
            p = (prev.get("ships") or {}).get(sid)
            ppos = (p or {}).get("position") if isinstance(p, dict) else None
            if not (isinstance(ppos, list) and len(ppos) == 3):
                continue
            pkey = (tuple(int(v) for v in ppos[0]), tuple(int(v) for v in ppos[1]))
            pcell = (int(ppos[2][0]), int(ppos[2][1]))
            if pkey != key:
                continue
            stats["ai_moves_checked"] += 1
            for hz in hazards.get(key, []):
                d0, d1 = chebyshev(pcell, hz["cell"]), chebyshev(cell, hz["cell"])
                if d1 < d0:
                    closer.append(f"turn {snap.get('turn_no')}: ship {sid} in {key[0]}/{key[1]} moved from distance {d0} to {d1} of the "
                                  f"{hz['kind'].replace('_', ' ')} at {hz['cell']}")
            if cell != pcell:
                if key not in terrain_cache:
                    terrain_cache[key] = terrain_cells(index, key[0], key[1])
                if cell in terrain_cache[key]:
                    onto.append(f"turn {snap.get('turn_no')}: ship {sid} ended its move on terrain cell {cell} in {key[0]}/{key[1]}")
        prev = snap
    if closer:
        errs.append(f"{len(closer)} AI move(s) reduced the distance to a hole, e.g. {closer[0]} — inside a hole's range the AI moves "
                    f"directly away at full engine power (D-103)")
    if onto:
        errs.append(f"{len(onto)} AI move(s) ended on a terrain cell, e.g. {onto[0]} (D-103)")
    if orphans:
        errs.append(f"{len(orphans)} captain(s) without a ship in the snapshot, e.g. {orphans[0]} — an AI captain with no ships is removed the same turn (D-105, M2 § 5)")
    return errs, stats


# --------------------------------------------------------------------------- render additions (C4, pure)

_SGR = re.compile(r"\x1b\[([0-9;]*)m")


def ansi_cells(text: str) -> list[list[tuple[str, str]]]:
    """Per line, (char, style-state) pairs from ANSI SGR output; a reset ends the state."""
    lines = []
    for raw in text.split("\n"):
        state = ""
        cells: list[tuple[str, str]] = []
        pos = 0
        for m in _SGR.finditer(raw):
            cells.extend((ch, state) for ch in raw[pos:m.start()])
            params = m.group(1)
            state = "" if params in ("", "0") else (state + ";" + params if state else params)
            pos = m.end()
        cells.extend((ch, state) for ch in raw[pos:])
        lines.append(cells)
    return lines


def hazard_render_snapshots(snap: dict) -> dict[str, dict]:
    """From a real C3 snapshot, the inputs for smoke/22: 'without' (no hazard), 'hole' (a black hole
    in the sector, player.hazard set), 'star' (in a star's cell, drain), 'over' (game_over true).
    Also an 'orders' variant for smoke/14 (a persisting move plus a queued warp)."""
    base = copy.deepcopy(snap)
    sid, ship = player_ship(base)
    w, h = base["sizes"]["sector"]
    (gx, gy), (sx, sy), _ = ship["position"]
    cx, cy = (10, 8) if (w > 12 and h > 10) else (min(w - 1, 2), min(h - 1, 2))
    ship["position"] = [[gx, gy], [sx, sy], [cx, cy]]
    ship["hazard"] = None
    ship["move_remaining"] = None
    ship["orders"] = []
    base["game_over"] = False
    key = f"{gx},{gy}/{sx},{sy}"
    sector = base.setdefault("sectors", {}).setdefault(key, {"terrain": [], "items": []})
    terrain = [t for t in sector.get("terrain") or [] if t.get("kind") not in ("black_hole", "neutron_star", "wormhole")
               and list(t.get("cell") or []) != [cx, cy]]
    for other in base["ships"].values():                      # no other ship on the probe cells
        if other is not ship and list(other.get("position", [[], [], []])[2]) in ([cx, cy], [3, 3]):
            other["position"][2] = [cx + 1, cy + 1]
    sector["terrain"] = terrain
    without = base
    hole = copy.deepcopy(base)
    hole["sectors"][key]["terrain"] = terrain + [{"kind": "black_hole", "cell": [3, 3], "wormhole": False}]
    hole["ships"][sid]["hazard"] = {"kind": "black_hole", "cell": [3, 3], "distance": max(abs(cx - 3), abs(cy - 3)),
                                    "pull": 0.88, "turns_left": 4, "drain": 0}
    star = copy.deepcopy(base)
    star["sectors"][key]["terrain"] = terrain + [{"kind": "star", "cell": [cx, cy], "colour": "yellow", "bucket": "medium", "mass": 1.0}]
    star["ships"][sid]["hazard"] = {"kind": "star", "cell": [cx, cy], "distance": 0, "pull": 1.0, "turns_left": 4, "drain": 10}
    star["ships"][sid]["shield_current"] = 30.0
    over = copy.deepcopy(star)
    over["game_over"] = True
    orders = copy.deepcopy(base)
    orders["ships"][sid]["move_remaining"] = [0, 6]
    orders["ships"][sid]["orders"] = [{"command": "move", "args": ["+0", "+6"]}, {"command": "warp", "args": ["3", "4", "7", "9", "3"]}]
    orders["ships"][sid]["orders_queued"] = 2
    return {"without": without, "hole": hole, "star": star, "over": over, "orders": orders, "ship_id": sid}


def check_orders_render(text: str, orders: list[dict], cells_per_turn: int) -> list[str]:
    """smoke/14 (render part) — PNL-07, D-99: the T4 panel lists the queued orders in order and shows
    the persisting move as 'move +0 +6 (4 cells/turn)' (C4)."""
    errs: list[str] = []
    last = -1
    for o in orders:
        pat = re.compile(re.escape(o["command"]) + r"\s+" + _args_pattern(o["args"]), re.I)
        m = pat.search(text)
        if m is None:
            errs.append(f"render_status: queued order '{o['command']} {' '.join(o['args'])}' does not appear in the panel (PNL-07, D-99, C4)")
        elif m.start() < last:
            errs.append(f"render_status: '{o['command']} {' '.join(o['args'])}' appears before an earlier queued order — list them in order (PNL-07)")
        else:
            last = m.start()
    if not re.search(rf"\b{int(cells_per_turn)}\s*cells?\s*/\s*turn", text, re.I):
        errs.append(f"render_status: the persisting move does not show its cells per turn '({cells_per_turn} cells/turn)' (C4, PNL-07)")
    return errs


def check_hazard_render(probe: dict, glyph: str, hole_hazard: dict, star_hazard: dict, cause_phrase: str) -> list[str]:
    """smoke/22 — GRD-03, PNL-06, OUT-03, OUT-16 (C4 additions).

    probe = {"renders": {"grid": [hole_text, without_text], "status": [star_text, without_text, over_text],
             "output": [destroyed_text]}, "styled": {"grid": [hole_ansi, without_ansi]}, "errors": {…}}."""
    errs: list[str] = []
    for role, err in (probe.get("errors") or {}).items():
        errs.append(f"render_{role}.render() raised: {err.strip().splitlines()[-1] if err.strip() else err}")
    for mod, err in (probe.get("import_errors") or {}).items():
        errs.append(f"import {mod} failed: {err.strip().splitlines()[-1] if err.strip() else err}")
    renders, styled = probe.get("renders") or {}, probe.get("styled") or {}
    g = renders.get("grid") or []
    gs = styled.get("grid") or []
    if len(g) < 2 or len(gs) < 2:
        errs.append("render_grid: no rendered text captured for the hazard and no-hazard snapshots")
    else:
        with_, without = g[0], g[1]
        pw, pwo = glyph_positions(with_, glyph), glyph_positions(without, glyph)
        tl = str(hole_hazard["turns_left"])
        if not pw:
            errs.append(f"render_grid: active-ship glyph {glyph!r} not found in the hazard render (SYM-10)")
        else:
            li, col = sorted(pw)[0]
            lines_w, lines_wo = with_.splitlines(), without.splitlines()
            win_w = lines_w[li][max(0, col - 4): col + 6] if li < len(lines_w) else ""
            win_wo = lines_wo[li][max(0, col - 4): col + 6] if li < len(lines_wo) else ""
            if tl not in win_w or win_w == win_wo:
                errs.append(f"render_grid: turns_left {tl} is not shown next to the ship glyph (row {li}, cols {max(0, col - 4)}..{col + 5}: "
                            f"{win_w!r} vs {win_wo!r} without the hazard) — the countdown is shown beside the ship (GRD-03, C4)")
        a, b = ansi_cells(gs[0]), ansi_cells(gs[1])
        pa, pb = with_.splitlines(), without.splitlines()
        tinted = 0
        for i in range(min(len(a), len(b), len(pa), len(pb))):
            if pa[i] != pb[i]:
                continue
            tinted += sum(1 for (ca, sa), (cb, sb) in zip(a[i], b[i]) if ca == cb and sa != sb and not ca.isspace())
        if tinted < 1:
            errs.append("render_grid: no cell is styled differently with a black hole in the sector — cells inside a hazard's pull range "
                        "must be tinted (GRD-03, C4; compared the ANSI styles of rows whose text is identical)")
    st = renders.get("status") or []
    if len(st) < 3:
        errs.append("render_status: no rendered text captured for the hazard / no-hazard / game-over snapshots")
    else:
        star_t, wo_t, over_t = st[0], st[1], st[2]
        new_lines = [ln for ln in star_t.splitlines() if ln not in set(wo_t.splitlines())]
        joined = "\n".join(new_lines)
        pull, tl, drain = star_hazard["pull"], star_hazard["turns_left"], star_hazard["drain"]
        pull_rx = rf"\b{int(pull)}(\.0+)?\b" if float(pull).is_integer() else rf"\b{re.escape(f'{pull:g}')}\d*\b"
        want = ((pull_rx, f"pull {pull:g}"), (rf"\b{tl}\b", f"turns_left {tl}"), (rf"\b{drain}\b", f"drain {drain}"))
        for rx, label in want:
            if not re.search(rx, joined):
                errs.append(f"render_status: {label} does not appear in the lines added by player.hazard (PNL-06: pull, countdown, drain; C4)"
                            + (f"; added lines: {new_lines[:4]!r}" if new_lines else "; no line differs from the no-hazard panel"))
        if not re.search(r"game\s*over", over_t, re.I):
            errs.append("render_status: no GAME OVER banner when snapshot.game_over is true (C4, OUT-16, D-105)")
        if re.search(r"game\s*over", wo_t, re.I):
            errs.append("render_status: a GAME OVER banner is shown although snapshot.game_over is false")
    out = renders.get("output") or []
    if not out:
        errs.append("render_output: no rendered text captured for the destroyed event")
    elif cause_phrase.lower() not in out[0].lower():
        errs.append(f"render_output: the destroyed event's cause {cause_phrase!r} is missing from the render (OUT-11, OUT-16, C4)")
    return errs
