"""T3 Output renderer - pure: render(turn_resolved_payload, view, cfg, width) -> renderable (C4, D-78).

The payload is the whole ``turn.resolved`` message ``{turn_no, snapshot, events}``.  Events
are listed in the order given, grouped under a phase heading whenever the phase changes
(OB-04: the engine emits them in TP order), coloured by severity (OB-03).  One event per
line, never wrapped, so a reader (or the gate) finds every event text verbatim.
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
    """The lines for one resolved turn (header, phase headings, events)."""
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
    return lines


def log_line(text: str, severity: str = "info", kind: str = "system") -> Text:
    """A single out-of-turn line (MSG-05 event.log, MSG-06 rejections echoed - OUT-13, OUT-14)."""
    t = Text(no_wrap=True, overflow="crop")
    t.append(f"  {kind:<9} ", style=P.STYLES["label"])
    t.append(text, style=P.severity_style(severity))
    return t


def render(payload: dict, view: dict | None, cfg: GameConfig, width: int = 120) -> RenderableType:
    """C4 / D-78: the turn.resolved payload rendered as a grouped, coloured block."""
    return Group(*turn_lines(payload, width))
