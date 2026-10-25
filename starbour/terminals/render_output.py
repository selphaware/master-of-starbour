"""T3 Output renderer - pure: render(turn_resolved_payload, view, cfg, width) -> renderable (C4, D-78).

The payload is the whole ``turn.resolved`` message ``{turn_no, snapshot, events}``.  Events
are listed in the order given, grouped under a phase heading whenever the phase changes
(OB-04: the engine emits them in TP order), coloured by severity (OB-03).  One event per
line, never wrapped, so a reader (or the gate) finds every event text verbatim.  A turn
whose snapshot says ``game_over`` ends with the OUT-16 block; ``game_over_lines`` renders
the MSG-09 payload (cause, turn, run summary, 'new').
"""
from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text

from starbour.config import GameConfig
from starbour.terminals import palette as P


def event_line(ev: dict, width: int = 120) -> Text:
    """One event: ``  [type] text`` in the severity's colour."""
    severity = str(ev.get("severity", "info"))
    t = Text(no_wrap=True, overflow="crop")
    t.append("  ")
    t.append(f"{str(ev.get('type', 'event')):<9} ", style=P.STYLES["label"])
    t.append(str(ev.get("text", "")), style=P.severity_style(severity))
    return t


def turn_lines(payload: dict, width: int = 120) -> list[Text]:
    """The lines for one resolved turn (header, phase headings, events, OUT-16 block when the game ended)."""
    turn_no = payload.get("turn_no", 0)
    events = payload.get("events") or []
    header = Text(no_wrap=True, overflow="crop")
    rule = "─" * 8
    header.append(f"{rule} Turn {turn_no} {rule}", style=P.STYLES["title"])
    lines = [header]
    current_phase: str | None = None
    for ev in events:
        phase = str(ev.get("phase", ""))
        if phase != current_phase:
            current_phase = phase
            title = P.PHASE_TITLES.get(phase, phase or "Events")
            h = Text(no_wrap=True, overflow="crop")
            h.append(f"{title} ", style=P.STYLES["subtitle"])
            h.append(f"({phase})" if phase else "", style=P.STYLES["rule"])
            lines.append(h)
        lines.append(event_line(ev, width))
    if not events:
        lines.append(Text("  (no events)", style=P.STYLES["label"]))
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    if snapshot.get("game_over"):
        cause = next((str(e.get("text", "")) for e in events if e.get("type") == "destroyed"), "")
        lines.extend(game_over_lines({"turn_no": turn_no, "cause": cause, "summary": {}}, width))
    return lines


def game_over_lines(payload: dict, width: int = 120) -> list[Text]:
    """OUT-16: cause, turn reached, run summary, and how to start again (MSG-09 payload)."""
    out: list[Text] = []
    bar = Text(no_wrap=True, overflow="crop")
    bar.append("═" * 12 + "  GAME OVER  " + "═" * 12, style=P.STYLES["game_over"])
    out.append(bar)
    cause = str(payload.get("cause") or "your ship was destroyed")
    t = Text(no_wrap=True, overflow="crop")
    t.append(f"  {cause}", style=P.STYLES["bad"])
    out.append(t)
    t = Text(no_wrap=True, overflow="crop")
    t.append(f"  Turn reached: {payload.get('turn_no', '?')}", style=P.STYLES["value"])
    out.append(t)
    summary = payload.get("summary") or {}
    if summary:
        t = Text(no_wrap=True, overflow="crop")
        t.append("  Run: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in summary.items() if k != "turns"), style=P.STYLES["value"])
        out.append(t)
    t = Text(no_wrap=True, overflow="crop")
    t.append("  Type 'new' in the command terminal to start again with a new seed (CMD-19, OV-09c).", style=P.STYLES["highlight"] if "highlight" in P.STYLES else P.STYLES["title"])
    out.append(t)
    return out


def log_line(text: str, severity: str = "info", kind: str = "system") -> Text:
    """A single out-of-turn line (MSG-05 event.log, MSG-06 rejections echoed - OUT-13, OUT-14)."""
    t = Text(no_wrap=True, overflow="crop")
    t.append(f"  {kind:<9} ", style=P.STYLES["label"])
    t.append(text, style=P.severity_style(severity))
    return t


def render(payload: dict, view: dict | None, cfg: GameConfig, width: int = 120) -> RenderableType:
    """C4 / D-78: the turn.resolved payload rendered as a grouped, coloured block."""
    return Group(*turn_lines(payload, width))
