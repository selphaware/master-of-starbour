"""T3 Output renderer - pure: render(turn_resolved_payload, view, cfg, width) -> renderable (C4, D-78, OUT-15).

The payload is the whole ``turn.resolved`` message ``{turn_no, snapshot, events}``.  Events
are listed in the order given, grouped under a phase heading whenever the phase changes
(OB-04: the engine emits them in TP order), coloured by severity (OB-03).  One event per
line, never wrapped, so a reader (or the gate) finds every event text verbatim.  A turn
whose snapshot says ``game_over`` ends with the OUT-16 block; ``game_over_lines`` renders
the MSG-09 payload (cause, turn, run summary, 'new').  M5 (EG-08, D-159): ``fireworks_frames``
and ``title_frame`` are the pure pieces of the mastery ceremony the T3 process animates; a turn
carrying the ``mastery`` event renders the first frame and the title so the event is visible in
a single still frame too (C12).
"""
from __future__ import annotations

import math
import random

from rich.console import Group, RenderableType
from rich.text import Text

from starbour.config import GameConfig
from starbour.terminals import palette as P

CEREMONY_FRAME_HEIGHT = 8   # rows of fireworks shown in the still render of a mastery turn (the live ceremony fills the window)


def event_line(ev: dict, width: int = 120) -> Text:
    """One event: ``  [type] text`` in the severity's colour."""
    severity = str(ev.get("severity", "info"))
    t = Text(no_wrap=True, overflow="crop")
    t.append("  ")
    t.append(f"{str(ev.get('type', 'event')):<9} ", style=P.STYLES["label"])
    t.append(str(ev.get("text", "")), style=P.severity_style(severity))
    return t


def turn_lines(payload: dict, width: int = 120, cfg: GameConfig | None = None) -> list[Text]:
    """The lines for one resolved turn (header, phase headings, events, OUT-16 block when the game ended, the OUT-15 still frame on mastery)."""
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
    if any(e.get("type") == "mastery" for e in events):
        # OUT-15 / EG-08 / C12: the still form of the ceremony - the first fireworks frame and the title (the process animates the rest)
        block = P.BLOCK if (cfg is None or cfg.glyph_set == "unicode") else "#"
        glyphs = P.FIREWORK_GLYPHS["unicode" if block == P.BLOCK else "ascii"]
        lines.extend(fireworks_frames(width, CEREMONY_FRAME_HEIGHT, random.Random(f"ceremony-{turn_no}"), 1, glyphs))
        lines.append(title_frame(width, block))
    return lines


def fireworks_frames(width: int, height: int, rng: random.Random, n: int, glyphs: str = P.FIREWORK_GLYPHS["unicode"]) -> list[Text]:
    """EG-08 / D-159: ``n`` frames of a fireworks animation, each exactly ``height`` rows of at most ``width`` columns, drawn from ``rng``
    alone (a pure function of its inputs): a few bursts per frame at random cells, rings growing with the frame number, in the
    FIREWORK_COLOURS.  Consecutive frames differ because the burst centres are drawn afresh."""
    w, h = max(8, int(width)), max(3, int(height))
    out: list[Text] = []
    for i in range(max(0, int(n))):
        cells: dict[tuple[int, int], tuple[str, str]] = {}
        for _ in range(3 + i % 3):
            cx, cy, r = rng.randrange(w), rng.randrange(h), 1 + (i % 4)
            colour = rng.choice(P.FIREWORK_COLOURS)
            glyph = glyphs[min(len(glyphs) - 1, r - 1)]
            for a in range(12):
                angle = 2.0 * math.pi * a / 12.0
                x, y = cx + round(r * math.cos(angle) * 2.0), cy + round(r * math.sin(angle))   # terminal cells are about 2:1
                if 0 <= x < w and 0 <= y < h:
                    cells[(x, y)] = (glyph, colour)
            if 0 <= cx < w and 0 <= cy < h:
                cells[(cx, cy)] = ("*", "bold bright_white")
        t = Text(no_wrap=True, overflow="crop")
        for y in range(h):
            row = "".join(cells.get((x, y), (" ", ""))[0] for x in range(w)).rstrip()
            start = len(t)
            t.append(row)
            for x in range(len(row)):
                if (x, y) in cells:
                    t.stylize(cells[(x, y)][1], start + x, start + x + 1)
            if y < h - 1:
                t.append("\n")
        out.append(t)
    return out


def title_frame(width: int, block: str = P.BLOCK) -> Text:
    """EG-08 / OUT-15 / D-162 (6): MASTER OF STARBOUR in the 5-row palette font (one line, or two when the width is short) with the
    plain words beneath, every row within ``width``."""
    rows = P.title_rows(P.TITLE_WORDS, block)
    if max(len(r) for r in rows) > width:
        rows = P.title_rows("MASTER OF", block) + [""] + P.title_rows("STARBOUR", block)
    if max(len(r) for r in rows) > width:
        rows = []           # too narrow for the font: the plain words still say it
    span = max([len(r) for r in rows] + [len(P.TITLE_WORDS)])
    span = min(int(width), span)
    t = Text(no_wrap=True, overflow="crop")
    for r in rows:
        t.append(r.center(span).rstrip() if r else "", style=P.STYLES["highlight"] if "highlight" in P.STYLES else P.STYLES["title"])
        t.append("\n")
    t.append(P.TITLE_WORDS.center(span).rstrip(), style=P.STYLES["title"])
    return t


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
    return Group(*turn_lines(payload, width, cfg))
