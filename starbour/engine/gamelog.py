"""Gameplay logs - M3 step 2 (no rule changes; D-135), with the CFG-74 filter (D-139, OB-05).

``logs/game-<seed>.log``    plain text: one line per event in phase order (TP-01...08) with the turn
                            number and the T3 text untruncated, plus one line per AI decision with its
                            reason and rule IDs - filtered by ``[logging] ai_trace``: ``sector`` (AI in the
                            player's sector, plus every battle and roam), ``battle`` (battles and roams only),
                            ``all``, ``off``.
``logs/turns-<seed>.jsonl`` one JSON line per resolved turn in the C2 ``per_turn`` shape (CONTRACTS C2, C9,
                            C10, C11), plus ``event_records`` (the full MSG-03 event dicts) and ``ai_decisions``
                            (every decision, unfiltered).

Both are appended to each turn, started afresh on ``new`` and never read by the game.  The formatters are
pure functions of their inputs; ``GameLogs`` is the thin file shell around them and never raises - a log
that cannot be written is reported once and then dropped, so it cannot stop a turn.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from starbour.engine.phases import PHASE_ORDER
from starbour.engine.state import GameState

log = logging.getLogger("starbour.gamelog")

UNKNOWN_PHASE = len(PHASE_ORDER)
BATTLE_KINDS = ("battle", "roam", "attack")


def log_names(seed: int) -> tuple[str, str]:
    """File names (text log, JSON-lines log) for a game with this seed."""
    return f"game-{seed}.log", f"turns-{seed}.jsonl"


def one_line(text: object) -> str:
    """Events are one line each; a newline inside a text becomes ' / '."""
    return " / ".join(str(text).splitlines()) if text else ""


def header_line(seed: int) -> str:
    return (f"# Master of Starbour gameplay log - seed {seed}. 'T<n> <phase> <severity>/<type>: text [ids]' is a T3 event; "
            f"'T<n> <captain> <name>: reason (ids)' is an AI decision. Never read by the game.")


def event_line(turn_no: int, event: dict) -> str:
    """One T3 event: ``T3 TP-02 info/movement: Kestrel moved to 12,7 [MV-01, CMD-06]`` (the text untruncated)."""
    ids = event.get("ids") or []
    tail = f" [{', '.join(str(i) for i in ids)}]" if ids else ""
    return f"T{turn_no} {event.get('phase', '?')} {event.get('severity', '?')}/{event.get('type', '?')}: {one_line(event.get('text', ''))}{tail}"


def decision_line(turn_no: int, decision: dict) -> str:
    """One AI decision: ``T3 c-163 Elin Kestle: in battle b-001, not weaker -> no peace; fires torpedo+phaser at s-000 (CM-10, D-125)``."""
    ids = decision.get("ids") or []
    tail = f" ({', '.join(str(i) for i in ids)})" if ids else ""
    return f"T{turn_no} {decision.get('captain', '?')} {decision.get('name', '?')}: {one_line(decision.get('text', ''))}{tail}"


def keep_decision(decision: dict, mode: str, watched: list | None) -> bool:
    """CFG-74 / D-139: which decision lines the text log keeps."""
    mode = str(mode or "all").lower()
    if mode == "off":
        return False
    if mode == "all":
        return True
    kind = str(decision.get("kind") or "")
    if kind in BATTLE_KINDS:
        return True
    if mode == "sector" and watched is not None:
        sector = decision.get("sector")
        return sector is not None and [list(sector[0]), list(sector[1])] == [list(watched[0]), list(watched[1])]
    return False


def turn_lines(turn_no: int, events: list[dict], decisions: list[dict], mode: str = "all", watched: list | None = None) -> list[str]:
    """The turn's lines in phase order.  AI decisions are made in TP-01, so they lead that phase; events keep
    their order within a phase (the sort is stable)."""
    ordered = sorted(events, key=lambda e: PHASE_ORDER.get(str(e.get("phase")), UNKNOWN_PHASE))
    kept = [d for d in decisions if keep_decision(d, mode, watched)]
    return [decision_line(turn_no, d) for d in kept] + [event_line(turn_no, e) for e in ordered]


def turn_row(state: GameState, events: list[dict], stats: dict, notes: list[str] | tuple[str, ...] = ()) -> dict:
    """The C2 ``per_turn`` row (with the C9, C10 and C11 fields) for the turn just resolved.

    ``events`` holds the strings of the T3 texts, preceded by ``notes`` (the sim's script replies).
    """
    cap = state.player
    ship = state.active_ship()
    warps = list(stats.get("warps") or [])
    destroyed = list(stats.get("destroyed") or [])
    if ship is not None:
        player = {"ship_class": ship.cls,
                  "position": [[ship.galaxy[0], ship.galaxy[1]], [ship.sector[0], ship.sector[1]], [ship.cell[0], ship.cell[1]]],
                  "shield_current": ship.shield_current, "shield_max": ship.shield_max,
                  "hazard": state.hazard_dict(ship), "move_remaining": state.move_remaining(ship.id)}
    else:
        lost = stats.get("player_lost") or {}
        player = {"ship_class": lost.get("ship_class"), "position": lost.get("position"),
                  "shield_current": lost.get("shield_current"), "shield_max": lost.get("shield_max"),
                  "hazard": lost.get("hazard"), "move_remaining": None}
    player.update({"food": cap.food, "water": cap.water, "fear": cap.fear, "respect": cap.respect, "gold": cap.gold, "silver": cap.silver,
                   "in_battle": state.player_in_battle(), "ships": len([s for s in cap.ships if s in state.ships]),
                   "items": {k: int(v) for k, v in sorted(cap.items.items()) if int(v) > 0}})
    econ = dict(stats.get("ai_economy") or {})
    return {
        "turn_no": state.turn_no,
        "orders_executed": dict(stats.get("orders_executed") or {"player": 0, "ai": 0}),
        "ai_ships_moved": int(stats.get("ai_ships_moved") or 0),
        "player": player,
        "captains": len(state.captains),
        "events": list(notes) + [str(e.get("text", "")) for e in events],
        "snapshot_hash": stats.get("snapshot_hash"),
        "snapshot_bytes": int(stats.get("snapshot_bytes") or 0),
        "resolve_ms": round(float(stats.get("resolve_ms") or 0.0), 1),
        "warps": warps,
        "destroyed": destroyed,
        "battles": list(stats.get("battles") or []),
        "reputation": list(stats.get("reputation") or []),
        "captures": list(stats.get("captures") or []),
        "trades": list(stats.get("trades") or []),
        "pickups": list(stats.get("pickups") or []),
        "upgrades": list(stats.get("upgrades") or []),
        "planet_events": list(stats.get("planet_events") or []),
        "ai_economy": {"purchases": int(econ.get("purchases", 0)), "sales": int(econ.get("sales", 0)), "restock_trips": int(econ.get("restock_trips", 0)),
                       "docked": int(econ.get("docked", 0))},
    }


def decisions_of(stats: dict) -> list[dict]:
    """The turn's AI decisions as plain dicts (``stats['ai_decisions']`` holds ``ai.Decision`` objects)."""
    return [d.to_dict() if hasattr(d, "to_dict") else dict(d) for d in stats.get("ai_decisions") or []]


def jsonl_line(row: dict, events: list[dict], decisions: list[dict]) -> str:
    """One line of ``turns-<seed>.jsonl``: the C2 row plus the full event records and the AI decisions."""
    doc = dict(row)
    doc["event_records"] = events
    doc["ai_decisions"] = decisions
    return json.dumps(doc, ensure_ascii=False, separators=(",", ":"))


class GameLogs:
    """The two files of one game.  ``fresh=True`` starts them empty (new game); ``fresh=False`` appends (resumed game)."""

    def __init__(self, log_dir: Path, seed: int, *, fresh: bool = True) -> None:
        self.seed = int(seed)
        text_name, jsonl_name = log_names(self.seed)
        self.text_path = Path(log_dir) / text_name
        self.jsonl_path = Path(log_dir) / jsonl_name
        self.broken = False
        try:
            self.text_path.parent.mkdir(parents=True, exist_ok=True)
            if fresh or not self.text_path.exists():
                self.text_path.write_text(header_line(self.seed) + "\n", encoding="utf-8", newline="\n")
            if fresh or not self.jsonl_path.exists():
                self.jsonl_path.write_text("", encoding="utf-8")
        except OSError as e:
            self._fail(e)

    def _fail(self, e: OSError) -> None:
        if not self.broken:
            log.error("gameplay logs in %s disabled: %s", self.text_path.parent, e)
        self.broken = True

    def append_turn(self, state: GameState, events: list[dict], stats: dict) -> None:
        """Append the turn just resolved to both files (after ``stats`` carries the snapshot hash and size)."""
        if self.broken:
            return
        decisions = decisions_of(stats)
        ship = state.active_ship()
        watched = [list(ship.galaxy), list(ship.sector)] if ship is not None else None
        try:
            lines = turn_lines(state.turn_no, events, decisions, getattr(state.cfg, "ai_trace", "all"), watched)
            with self.text_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(lines) + "\n")
            with self.jsonl_path.open("a", encoding="utf-8", newline="\n") as f:
                f.write(jsonl_line(turn_row(state, events, stats), events, decisions) + "\n")
        except OSError as e:
            self._fail(e)
