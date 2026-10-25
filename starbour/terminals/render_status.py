"""T4 Status renderer - pure: render(snapshot, view, cfg, width) -> renderable (C4, PNL-01...05, BR-18).

Panels: captain (PNL-01), active ship (PNL-02), supplies with the mutiny countdown
(PNL-03), cargo (PNL-04), fleet (PNL-05); the header shows seed, version and turn.
Every number appears as plain digits so the panel can be read (and checked) as text.
"""
from __future__ import annotations

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from starbour.config import GameConfig
from starbour.terminals import palette as P

PANEL_WIDTH = 64


def _kv(table: Table, label: str, value: Text | str, style: str | None = None) -> None:
    table.add_row(Text(label, style=P.STYLES["label"]), value if isinstance(value, Text) else Text(str(value), style=style or P.STYLES["value"]))


def _grid() -> Table:
    t = Table.grid(padding=(0, 1))
    t.add_column(no_wrap=True)
    t.add_column(no_wrap=True)
    return t


def captain_panel(snapshot: dict, cap: dict, cfg: GameConfig, width: int) -> Panel:
    """PNL-01."""
    t = _grid()
    _kv(t, "Captain", cap.get("name", "?"), P.STYLES["title"])
    _kv(t, "Gold", f"{cap.get('gold', 0)}", "bold yellow")
    _kv(t, "Silver", f"{cap.get('silver', 0)}", "bright_white")
    fear, respect = int(cap.get("fear", 0)), int(cap.get("respect", 0))
    fb = P.level_bar(cfg, fear, 100, 20, "red")
    fb.append(f" {fear}/100", style=P.STYLES["value"])
    _kv(t, "Fear", fb)
    rb = P.level_bar(cfg, respect, 100, 20, "green")
    rb.append(f" {respect}/100", style=P.STYLES["value"])
    _kv(t, "Respect", rb)
    eligible = fear >= cfg.fear_min and respect >= cfg.respect_min
    _kv(t, "Ultimate item", "eligible" if eligible else f"not yet (needs fear {cfg.fear_min}, respect {cfg.respect_min}, wealth {cfg.wealth_min_silver} silver)",
        P.STYLES["good"] if eligible else P.STYLES["label"])
    if cap.get("is_master"):
        _kv(t, "Title", "Master of Starbour", P.STYLES["title"])
    return Panel(t, title="Captain", title_align="left", border_style=P.STYLES["accent"], width=min(width, PANEL_WIDTH))


def ship_panel(snapshot: dict, cap: dict, ship: dict | None, cfg: GameConfig, width: int) -> Panel:
    """PNL-02."""
    t = _grid()
    if ship is None:
        _kv(t, "Ship", "none", P.STYLES["bad"])
    else:
        _kv(t, "Ship", f"{ship.get('name', '?')} ({str(ship.get('class', '?')).capitalize()}, {cap.get('active_ship')})", P.STYLES["title"])
        _kv(t, "Torpedoes", f"{ship.get('torpedoes', 0)}")
        _kv(t, "Phasers", f"{ship.get('phasers', 0):g} / {cfg.impulse_max:g}")
        sc, sm = float(ship.get("shield_current", 0)), float(ship.get("shield_max", 0))
        shield_style = P.STYLES["good"] if sm and sc / sm > 0.5 else P.STYLES["warn"] if sc > 0 else P.STYLES["bad"]
        _kv(t, "Shields", f"{sc:g} / {sm:g}", shield_style)
        _kv(t, "Engine", f"{ship.get('engine', 0):g} / {cfg.impulse_max:g}")
        _kv(t, "Warp core", f"{ship.get('warp', 0):g} / {cfg.warp_max:g}")
        pos = ship.get("position") or [[0, 0], [0, 0], [0, 0]]
        _kv(t, "Position", f"galaxy {pos[0][0]},{pos[0][1]}  sector {pos[1][0]},{pos[1][1]}  cell {pos[2][0]},{pos[2][1]}")
    return Panel(t, title="Active ship", title_align="left", border_style=P.STYLES["accent"], width=min(width, PANEL_WIDTH))


def supplies_panel(cap: dict, cfg: GameConfig, width: int) -> Panel:
    """PNL-03 / SUP-02: food and water as turns remaining, colour-coded; countdown at 0."""
    t = _grid()
    food, water = int(cap.get("food", 0)), int(cap.get("water", 0))
    _kv(t, "Food", f"{food} (turns)", P.supplies_style(food, cfg.warn_turns))
    _kv(t, "Water", f"{water} (turns)", P.supplies_style(water, cfg.warn_turns))
    countdown = cap.get("mutiny_countdown")
    if countdown is not None:
        _kv(t, "Mutiny", f"in {countdown} turn(s) unless you buy supplies", P.STYLES["bad"])
    elif min(food, water) <= cfg.warn_turns:
        _kv(t, "Warning", f"supplies at or below {cfg.warn_turns} turns - buy food and water at a planet", P.STYLES["warn"])
    else:
        _kv(t, "Status", "supplies adequate", P.STYLES["good"])
    style = P.STYLES["bad"] if countdown is not None else P.STYLES["warn"] if min(food, water) <= cfg.warn_turns else P.STYLES["accent"]
    return Panel(t, title="Supplies", title_align="left", border_style=style, width=min(width, PANEL_WIDTH))


def cargo_panel(cap: dict, cfg: GameConfig, width: int) -> Panel:
    """PNL-04."""
    items = cap.get("items") or []
    if not items:
        body: RenderableType = Text("empty", style=P.STYLES["label"])
    else:
        t = _grid()
        for name in items:
            spec = cfg.elements.get(str(name).lower())
            _kv(t, str(name), f"{spec.category}, {spec.rarity}" if spec else "")
        body = t
    return Panel(body, title="Cargo", title_align="left", border_style=P.STYLES["accent"], width=min(width, PANEL_WIDTH))


def fleet_panel(snapshot: dict, cap: dict, width: int) -> Panel:
    """PNL-05."""
    t = Table(box=None, padding=(0, 1), show_header=True, header_style=P.STYLES["label"])
    for col in ("Ship", "Class", "Location", "Shields", "Orders"):
        t.add_column(col, no_wrap=True)
    ships = snapshot.get("ships") or {}
    for sid in cap.get("ships") or []:
        s = ships.get(sid)
        if not s:
            continue
        pos = s.get("position") or [[0, 0], [0, 0], [0, 0]]
        active = sid == cap.get("active_ship")
        t.add_row(Text(("* " if active else "  ") + str(s.get("name", sid)), style=P.STYLES["title"] if active else P.STYLES["value"]),
                  str(s.get("class", "")).capitalize(),
                  f"g {pos[0][0]},{pos[0][1]} s {pos[1][0]},{pos[1][1]} c {pos[2][0]},{pos[2][1]}",
                  f"{float(s.get('shield_current', 0)):g}/{float(s.get('shield_max', 0)):g}",
                  f"{s.get('orders_queued', 0)}")
    return Panel(t, title="Fleet", title_align="left", border_style=P.STYLES["accent"], width=min(width, PANEL_WIDTH))


def header(snapshot: dict, width: int) -> Text:
    t = Text(no_wrap=True, overflow="crop")
    t.append("Master of Starbour", style=P.STYLES["title"])
    t.append(f"  v{snapshot.get('version', '?')}", style=P.STYLES["label"])
    t.append(f"  seed {snapshot.get('seed', '?')}", style=P.STYLES["label"])
    t.append("  Turn ", style=P.STYLES["label"])
    t.append(f"{snapshot.get('turn_no', 0)}", style=P.STYLES["title"])
    return t


def render(snapshot: dict, view: dict | None, cfg: GameConfig, width: int = 120) -> RenderableType:
    """C4: the whole status column."""
    cap = (snapshot.get("captains") or {}).get(snapshot.get("player_id")) or {}
    ship = (snapshot.get("ships") or {}).get(cap.get("active_ship"))
    return Group(
        header(snapshot, width),
        captain_panel(snapshot, cap, cfg, width),
        ship_panel(snapshot, cap, ship, cfg, width),
        supplies_panel(cap, cfg, width),
        cargo_panel(cap, cfg, width),
        fleet_panel(snapshot, cap, width),
    )
