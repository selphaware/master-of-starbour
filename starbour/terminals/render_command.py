"""T1 Command renderer - pure: the prompt and the immediate command outputs (C4, UX-01, CMD-01/02/04).

``render(snapshot, view, cfg, width)`` returns the coloured prompt ``[T<turn> . <ship> . <n> orders] >``.
The process module turns it into prompt_toolkit input; nothing here touches a socket or stdin.
"""
from __future__ import annotations

from rich.console import RenderableType
from rich.table import Table
from rich.text import Text

from starbour.commands.catalogue import help_lines
from starbour.config import GameConfig
from starbour.terminals import palette as P

SEP = " · "


def _player(snapshot: dict) -> tuple[dict, dict | None]:
    cap = (snapshot.get("captains") or {}).get(snapshot.get("player_id")) or {}
    ship = (snapshot.get("ships") or {}).get(cap.get("active_ship"))
    return cap, ship


def prompt_parts(snapshot: dict, pending: int | None = None) -> tuple[int, str, int]:
    """(turn_no, ship name, queued orders) - UX-01."""
    cap, ship = _player(snapshot)
    name = ship.get("name", "no ship") if ship else "no ship"
    if pending is None:
        pending = int(ship.get("orders_queued", 0)) if ship else 0
    return int(snapshot.get("turn_no", 0)), str(name), pending


def prompt_plain(snapshot: dict, pending: int | None = None) -> str:
    turn_no, name, n = prompt_parts(snapshot, pending)
    return f"[T{turn_no}{SEP}{name}{SEP}{n} order{'' if n == 1 else 's'}] > "


def render(snapshot: dict, view: dict | None, cfg: GameConfig, width: int = 120, pending: int | None = None) -> RenderableType:
    """C4 / UX-01: the prompt as coloured Rich text."""
    turn_no, name, n = prompt_parts(snapshot, pending)
    t = Text(no_wrap=True, overflow="crop")
    t.append("[", style=P.STYLES["label"])
    t.append(f"T{turn_no}", style=P.STYLES["prompt_turn"])
    t.append(SEP, style=P.STYLES["label"])
    t.append(name, style=P.STYLES["prompt_ship"])
    t.append(SEP, style=P.STYLES["label"])
    t.append(f"{n} order{'' if n == 1 else 's'}", style=P.STYLES["prompt_orders"] if n else P.STYLES["label"])
    t.append("] > ", style=P.STYLES["label"])
    return t


def status_lines(snapshot: dict, cfg: GameConfig) -> list[Text]:
    """CMD-02: the active ship's stats and the captain's wealth, fear, respect, food and water."""
    cap, ship = _player(snapshot)
    out: list[Text] = []
    head = Text(no_wrap=True)
    head.append(f"{cap.get('name', '?')}", style=P.STYLES["title"])
    head.append(f"  turn {snapshot.get('turn_no', 0)}  seed {snapshot.get('seed', '?')}", style=P.STYLES["label"])
    out.append(head)
    wealth = Text(no_wrap=True)
    wealth.append("  gold ", style=P.STYLES["label"])
    wealth.append(f"{cap.get('gold', 0)}", style="bold yellow")
    wealth.append("  silver ", style=P.STYLES["label"])
    wealth.append(f"{cap.get('silver', 0)}", style="bright_white")
    wealth.append("  fear ", style=P.STYLES["label"])
    wealth.append(f"{cap.get('fear', 0)}/100", style="red")
    wealth.append("  respect ", style=P.STYLES["label"])
    wealth.append(f"{cap.get('respect', 0)}/100", style="green")
    out.append(wealth)
    sup = Text(no_wrap=True)
    food, water = int(cap.get("food", 0)), int(cap.get("water", 0))
    sup.append("  food ", style=P.STYLES["label"])
    sup.append(f"{food}", style=P.supplies_style(food, cfg.warn_turns))
    sup.append("  water ", style=P.STYLES["label"])
    sup.append(f"{water}", style=P.supplies_style(water, cfg.warn_turns))
    cd = cap.get("mutiny_countdown")
    if cd is not None:
        sup.append(f"  mutiny in {cd} turn(s)", style=P.STYLES["bad"])
    out.append(sup)
    if ship:
        pos = ship.get("position") or [[0, 0], [0, 0], [0, 0]]
        s = Text(no_wrap=True)
        s.append(f"  {ship.get('name')} ", style=P.STYLES["prompt_ship"])
        s.append(f"({str(ship.get('class', '')).capitalize()})  torpedoes {ship.get('torpedoes', 0)}  phasers {ship.get('phasers', 0):g}  "
                 f"shields {ship.get('shield_current', 0):g}/{ship.get('shield_max', 0):g}  engine {ship.get('engine', 0):g}  warp {ship.get('warp', 0):g}",
                 style=P.STYLES["value"])
        out.append(s)
        p = Text(no_wrap=True)
        p.append(f"  galaxy {pos[0][0]},{pos[0][1]}  sector {pos[1][0]},{pos[1][1]}  cell {pos[2][0]},{pos[2][1]}  "
                 f"queued orders {ship.get('orders_queued', 0)}", style=P.STYLES["label"])
        out.append(p)
    return out


def fleet_table(snapshot: dict) -> Table:
    """CMD-04: every owned ship, its location and state."""
    cap, _ = _player(snapshot)
    t = Table(box=None, padding=(0, 1), header_style=P.STYLES["label"])
    for col in ("#", "Ship", "Class", "Location", "Shields", "Engine", "Orders"):
        t.add_column(col, no_wrap=True)
    ships = snapshot.get("ships") or {}
    for i, sid in enumerate(cap.get("ships") or [], start=1):
        s = ships.get(sid)
        if not s:
            continue
        pos = s.get("position") or [[0, 0], [0, 0], [0, 0]]
        active = sid == cap.get("active_ship")
        t.add_row(f"{i}", Text(("* " if active else "  ") + str(s.get("name", sid)), style=P.STYLES["title"] if active else P.STYLES["value"]),
                  str(s.get("class", "")).capitalize(),
                  f"g {pos[0][0]},{pos[0][1]} s {pos[1][0]},{pos[1][1]} c {pos[2][0]},{pos[2][1]}",
                  f"{float(s.get('shield_current', 0)):g}/{float(s.get('shield_max', 0)):g}", f"{float(s.get('engine', 0)):g}",
                  f"{s.get('orders_queued', 0)}")
    return t


def help_text(name: str | None = None) -> list[Text]:
    """CMD-01 from the catalogue."""
    out = []
    for line in help_lines(name):
        style = P.STYLES["title"] if not line.startswith(" ") else P.STYLES["value"]
        out.append(Text(line, style=style, no_wrap=True))
    return out
