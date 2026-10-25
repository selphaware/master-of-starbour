"""One palette and the glyph sets shared by all four terminals - UX-08, OB-03, SYM-01...14, D-68, D-81.

Glyphs are written as escapes so the source stays ASCII; ``[ui] glyph_set`` picks the
set at run time.  No game rules here: display metadata only.
"""
from __future__ import annotations

from rich.text import Text

from starbour.config import GameConfig

GLYPHS: dict[str, dict[str, str]] = {
    "unicode": {
        "void": "·",          # SYM-01 middle dot
        "galaxy": "◎",        # SYM-02 bullseye
        "sector": "▪",        # SYM-03 small square
        "star": "★",          # SYM-04 star
        "neutron_star": "✦",  # SYM-05 four-pointed star
        "black_hole": "●",    # SYM-06 black circle
        "wormhole": "◉",      # SYM-07 fisheye
        "planet": "○",        # SYM-08 white circle
        "item": "?",               # SYM-09 normal item
        "item_special": "✧",  # SYM-09 special item
        "ship_active": "▲",   # SYM-10 up triangle
        "ship_owned": "△",    # SYM-11 white up triangle
        "ship_ai": "▼",       # SYM-12 down triangle
        "here": "◆",          # galaxy/universe view: the cell holding the active ship
    },
    "ascii": {
        "void": ".", "galaxy": "@", "sector": "#", "star": "*", "neutron_star": "+", "black_hole": "O",
        "wormhole": "0", "planet": "o", "item": "?", "item_special": "$", "ship_active": "^", "ship_owned": "A",
        "ship_ai": "v", "here": "X",
    },
}

STYLES: dict[str, str] = {
    "void": "grey37",
    "galaxy": "white",
    "sector": "grey62",
    "sector_visited": "cyan",
    "sector_hazard": "red",
    "here": "bold bright_cyan",
    "neutron_star": "magenta",
    "black_hole": "bold bright_red",
    "wormhole": "medium_purple",
    "planet": "green",
    "item": "yellow",
    "item_special": "bold bright_yellow",
    "ship_active": "bold bright_cyan",
    "ship_owned": "cyan",
    "ship_ai": "dark_orange",
    "ship_ai_battle": "bold red",
    "title": "bold bright_cyan",
    "subtitle": "bright_cyan",
    "label": "grey70",
    "value": "white",
    "accent": "cyan",
    "good": "green",
    "warn": "yellow",
    "bad": "bold red",
    "rule": "grey50",
    "prompt_turn": "bold bright_cyan",
    "prompt_ship": "bold white",
    "prompt_orders": "yellow",
    "command_ok": "green",
    "command_error": "bold red",
    "command_info": "grey70",
    "confirm": "bold yellow",
    "countdown": "bold bright_yellow",   # GRD-03: turns until pulled in, beside the ship
    "hazard_panel": "bold yellow",       # PNL-06 border
    "game_over": "bold bright_red",      # OUT-16 banner
}

# GRD-03 hazard overlay: background tints for cells inside a pull range (display metadata, SYM-05...07)
HAZARD_BG: dict[str, str] = {
    "black_hole": "#3a0e0e",    # the whole sector, dark red
    "wormhole": "#2a1040",      # the whole sector, dark purple
    "neutron_star": "#2a0a2a",  # the whole sector, dark magenta
    "star": "#3a2a00",          # the star's own cell, dark amber
}

# SYM-04: the stellar sequence colour names from [stellar] colour_names -> Rich styles
STAR_STYLES: dict[str, str] = {
    "red": "red", "orange": "dark_orange", "yellow": "yellow", "yellow-white": "khaki1", "white": "bright_white",
    "blue-white": "light_sky_blue1", "blue": "dodger_blue1",
}

# OB-03: colour per severity
SEVERITY_STYLES: dict[str, str] = {
    "muted": "grey50", "info": "white", "warning": "yellow", "alert": "bold red", "error": "bold bright_red",
    "highlight": "bold magenta",
}

# OB-04: phase titles for the T3 grouping (display metadata, mirrors TP-01...08)
PHASE_TITLES: dict[str, str] = {
    "TP-01": "Orders", "TP-02": "Movement", "TP-03": "Hazards", "TP-04": "Combat", "TP-05": "Trade & pickup",
    "TP-06": "Upkeep", "TP-07": "Reputation", "TP-08": "Publish & render",
}


def glyphs(cfg: GameConfig) -> dict[str, str]:
    """The glyph set selected by [ui] glyph_set (D-68)."""
    return GLYPHS[cfg.glyph_set]


def star_style(colour_name: str) -> str:
    return STAR_STYLES.get(colour_name, "white")


def tinted(style: str, kind: str | None) -> str:
    """GRD-03: the cell style with the hazard's background tint added; unchanged when kind is None."""
    bg = HAZARD_BG.get(kind or "")
    return f"{style} on {bg}" if bg else style


def number(value: float) -> str:
    """Numbers for the panels: 2 d.p. with trailing zeros dropped (1 -> '1', 0.88 -> '0.88', 3.849 -> '3.85')."""
    text = f"{float(value):.2f}".rstrip("0").rstrip(".")
    return text or "0"


def severity_style(severity: str) -> str:
    return SEVERITY_STYLES.get(severity, "white")


def bar(value: float, maximum: float, width: int = 20, style: str = "cyan", empty_style: str = "grey37") -> Text:
    """A 0-100 style bar for fear / respect (PNL-01)."""
    maximum = max(1.0, float(maximum))
    filled = int(round(max(0.0, min(value, maximum)) / maximum * width))
    t = Text()
    t.append("█" * filled if True else "#" * filled, style=style)
    t.append("░" * (width - filled), style=empty_style)
    return t


def ascii_bar(value: float, maximum: float, width: int = 20, style: str = "cyan", empty_style: str = "grey37") -> Text:
    maximum = max(1.0, float(maximum))
    filled = int(round(max(0.0, min(value, maximum)) / maximum * width))
    t = Text()
    t.append("#" * filled, style=style)
    t.append("-" * (width - filled), style=empty_style)
    return t


def level_bar(cfg: GameConfig, value: float, maximum: float = 100.0, width: int = 20, style: str = "cyan") -> Text:
    return (bar if cfg.glyph_set == "unicode" else ascii_bar)(value, maximum, width, style)


def supplies_style(value: int, warn_turns: int) -> str:
    """PNL-03 colour coding: red at 0, yellow at or below warn_turns, else green."""
    if value <= 0:
        return STYLES["bad"]
    if value <= warn_turns:
        return STYLES["warn"]
    return STYLES["good"]
