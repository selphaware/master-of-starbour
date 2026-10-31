"""T4 Status renderer - pure: render(snapshot, view, cfg, width) -> renderable (C4, PNL-01...07, BR-18, C11).

Panels: captain (PNL-01), active ship (PNL-02), supplies with the mutiny countdown
(PNL-03), hazard - only when the active ship is inside a pull range (PNL-06), battle -
enemies in range with their shields as a percentage and, once scanned, the shield % at
which they sue for peace (PNL-06, UX-09, D-138), the planet in the player's cell - name,
president, disposition, whether it trades and its prices (C11), orders - the active
ship's queued orders with a persisting move's cells per turn (PNL-07, D-99), cargo - each
element stack with its units and list value (PNL-04), fleet (PNL-05); the header shows
seed, version and turn; a GAME OVER banner tops everything when the snapshot says so
(OUT-16, D-105).  Every number appears as plain digits so the panel can be read (and
checked) as text.
"""
from __future__ import annotations

import math

from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from starbour.config import GameConfig
from starbour.terminals import palette as P

PANEL_WIDTH = 64
BATTLE_PANEL_WIDTH = 80   # the battle table needs more columns than the other panels (UX-09, D-138)


def _kv(table: Table, label: str, value: Text | str, style: str | None = None) -> None:
    table.add_row(Text(label, style=P.STYLES["label"]), value if isinstance(value, Text) else Text(str(value), style=style or P.STYLES["value"]))


def _grid() -> Table:
    t = Table.grid(padding=(0, 1))
    t.add_column(no_wrap=True)
    t.add_column(no_wrap=True)
    return t


def _player(snapshot: dict) -> tuple[dict, dict | None]:
    cap = (snapshot.get("captains") or {}).get(snapshot.get("player_id")) or {}
    ship = (snapshot.get("ships") or {}).get(cap.get("active_ship"))
    return cap, ship


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
        _kv(t, "Ship", "none - your ship was destroyed" if snapshot.get("game_over") else "none", P.STYLES["bad"])
    else:
        _kv(t, "Ship", f"{ship.get('name', '?')} ({str(ship.get('class', '?')).capitalize()}, {cap.get('active_ship')})", P.STYLES["title"])
        _kv(t, "Torpedoes", f"{ship.get('torpedoes', 0)}")
        _kv(t, "Phasers", f"{ship.get('phasers', 0):g} / {cfg.phaser_max:g}")
        sc, sm = float(ship.get("shield_current", 0)), float(ship.get("shield_max", 0))
        shield_style = P.STYLES["good"] if sm and sc / sm > 0.5 else P.STYLES["warn"] if sc > 0 else P.STYLES["bad"]
        _kv(t, "Shields", f"{sc:g} / {sm:g} (cap {cfg.shield_cap:g})", shield_style)
        _kv(t, "Engine", f"{ship.get('engine', 0):g} / {cfg.impulse_max:g}")
        _kv(t, "Warp core", f"{ship.get('warp', 0):g} / {cfg.warp_max:g}")
        pos = ship.get("position") or [[0, 0], [0, 0], [0, 0]]
        _kv(t, "Position", f"galaxy {pos[0][0]},{pos[0][1]}  sector {pos[1][0]},{pos[1][1]}  cell {pos[2][0]},{pos[2][1]}")
        if ship.get("in_battle"):
            _kv(t, "Battle", f"in battle {ship.get('in_battle')} - no shield recharge (CM-05)", P.STYLES["bad"])
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


def hazard_panel(ship: dict, cfg: GameConfig, width: int) -> Panel | None:
    """PNL-06 / GRD-03 / OUT-03: pull, countdown and drain - only when the active ship is in a pull range."""
    hz = ship.get("hazard") if isinstance(ship.get("hazard"), dict) else None
    if hz is None:
        return None
    t = _grid()
    kind = str(hz.get("kind", "hazard")).replace("_", " ")
    cell = hz.get("cell") or ["?", "?"]
    distance = hz.get("distance", 0)
    where = "in your cell" if distance == 0 else f"{distance} cell(s) away"
    _kv(t, "Object", f"{kind} at {cell[0]},{cell[1]} ({where})", P.STYLES["bad"])
    _kv(t, "Pull", f"{P.number(float(hz.get('pull', 0)))} against your engine {float(ship.get('engine', 0)):g}", P.STYLES["warn"])
    left = hz.get("turns_left")
    _kv(t, "Countdown", f"{left} turn(s) until pulled in", P.STYLES["bad"] if isinstance(left, int) and left <= 2 else P.STYLES["countdown"])
    drain = hz.get("drain") or 0
    _kv(t, "Shield drain", f"{drain} per turn" if drain else "none (not a star)", P.STYLES["warn"] if drain else P.STYLES["label"])
    pull = float(hz.get("pull", 0))
    cells = max(0, math.floor(float(ship.get("engine", 0)) - pull + 1e-9))
    _kv(t, "Impulse", f"{cells} cell(s) per turn at full power" + (" - held, warp out" if cells <= 0 else ""),
        P.STYLES["bad"] if cells <= 0 else P.STYLES["value"])
    return Panel(t, title="Hazard", title_align="left", border_style=P.STYLES["hazard_panel"], width=min(width, PANEL_WIDTH))


def peace_threshold(cfg: GameConfig, fear: float) -> float:
    """EF-02 / CFG-30 / CFG-72 / UX-09: the shield % below which a weaker AI sues for peace at the player's fear."""
    lo, hi = float(cfg.peace_shield_pct), float(cfg.peace_shield_pct_at_fear100)
    return lo + (hi - lo) * max(0.0, min(100.0, float(fear))) / 100.0


def enemies_in_battle(snapshot: dict, ship: dict, cfg: GameConfig) -> list[dict]:
    """PNL-06: the other side's ships (or planet, CM-11) in the active ship's sector while it is in a battle, nearest first:
    {id, name, class, distance, shield_current, shield_max, in_range, targeted, scanned}."""
    bid = ship.get("in_battle")
    if not bid:
        return []
    battle = next((b for b in (snapshot.get("battles") or []) if isinstance(b, dict) and b.get("id") == bid), None)
    sides = [str(s) for s in (battle.get("sides") or [])] if battle else []
    enemy = next((s for s in sides if s != ship.get("captain")), None)
    pos = ship.get("position") or [[0, 0], [0, 0], [0, 0]]
    cx, cy = int(pos[2][0]), int(pos[2][1])
    longest = max(int(cfg.torpedo_range) if float(ship.get("torpedoes", 0)) > 0 else 0, int(cfg.phaser_range) if float(ship.get("phasers", 0)) > 0 else 0)
    out = []
    captains = snapshot.get("captains") or {}
    for sid, s in (snapshot.get("ships") or {}).items():
        if not isinstance(s, dict) or s.get("captain") == ship.get("captain"):
            continue
        if enemy is not None and s.get("captain") != enemy:
            continue
        if enemy is None and s.get("in_battle") != bid:
            continue
        p = s.get("position") or []
        if len(p) != 3 or list(p[0]) != list(pos[0]) or list(p[1]) != list(pos[1]):
            continue
        d = max(abs(int(p[2][0]) - cx), abs(int(p[2][1]) - cy))
        theirs = max(int(cfg.torpedo_range) if float(s.get("torpedoes", 0)) > 0 else 0, int(cfg.phaser_range) if float(s.get("phasers", 0)) > 0 else 0)
        cap = captains.get(s.get("captain")) or {}
        out.append({"id": sid, "name": str(s.get("name", sid)), "class": str(s.get("class", "")), "distance": d,
                    "shield_current": float(s.get("shield_current", 0)), "shield_max": float(s.get("shield_max", 0)),
                    "in_range": d <= max(longest, theirs), "targeted": sid == ship.get("target"), "scanned": bool(cap.get("visible_reputation"))})
    if enemy is not None:
        planet = (snapshot.get("planets") or {}).get(enemy)
        if isinstance(planet, dict) and not planet.get("destroyed"):
            cell = planet.get("cell") or [0, 0]
            d = max(abs(int(cell[0]) - cx), abs(int(cell[1]) - cy))
            theirs = max(int(cfg.torpedo_range) if float(planet.get("torpedoes", 0)) > 0 else 0, int(cfg.phaser_range) if float(planet.get("phasers", 0)) > 0 else 0)
            out.append({"id": enemy, "name": str(planet.get("name", enemy)), "class": "planet", "distance": d,
                        "shield_current": float(planet.get("shields", 0)), "shield_max": float(planet.get("shield_max", 0)),
                        "in_range": d <= max(longest, theirs), "targeted": enemy == ship.get("target"), "scanned": False})
    out.sort(key=lambda e: (e["distance"], e["id"]))
    return out


def battle_panel(snapshot: dict, ship: dict | None, cfg: GameConfig, width: int) -> Panel | None:
    """PNL-06 (battle) / UX-09 / D-138: enemies in range with name, distance, shields and shield % - and, once scanned, the
    shield % at which they sue for peace (EF-02) - only while the active ship is in a battle."""
    if not ship or not ship.get("in_battle"):
        return None
    cap, _ = _player(snapshot)
    thr = peace_threshold(cfg, float(cap.get("fear", 0)))
    t = Table(box=None, padding=(0, 1), show_header=True, header_style=P.STYLES["label"])
    for col in ("Enemy", "Dist", "Shields", "Peace", "Range"):
        t.add_column(col, no_wrap=True)
    rows = enemies_in_battle(snapshot, ship, cfg)
    for e in rows:
        mark = " <" if e["targeted"] else ""
        pct = int(round(100.0 * e["shield_current"] / e["shield_max"])) if e["shield_max"] > 0 else 0
        peace = f"sues below {P.number(thr)} %" if e["scanned"] else ("never (planet)" if e["class"] == "planet" else "scan to see")
        t.add_row(Text(f"{e['name']}{mark} ({e['class'].capitalize()})", style=P.STYLES["target"] if e["targeted"] else P.STYLES["ship_ai_battle"]),
                  f"{e['distance']}", f"{pct} % ({e['shield_current']:g}/{e['shield_max']:g})", peace,
                  Text("in range" if e["in_range"] else "out", style=P.STYLES["bad"] if e["in_range"] else P.STYLES["label"]))
    if not rows:
        t.add_row(Text("no enemy in this sector", style=P.STYLES["label"]), "", "", "", "")
    return Panel(t, title=f"Battle {ship.get('in_battle')} - enemies (PNL-06, UX-09)", title_align="left", border_style=P.STYLES["battle_panel"],
                 width=min(width, BATTLE_PANEL_WIDTH))


def docked_planet(snapshot: dict) -> tuple[str, dict] | None:
    """C11 / C5: the planet record standing in the player's active ship's cell, as (id, record), else None."""
    _, ship = _player(snapshot)
    if not ship:
        return None
    pos = ship.get("position") or [[0, 0], [0, 0], [0, 0]]
    key = f"{pos[0][0]},{pos[0][1]}/{pos[1][0]},{pos[1][1]}"
    for pid, p in sorted((snapshot.get("planets") or {}).items()):
        if not isinstance(p, dict) or p.get("destroyed"):
            continue
        if str(p.get("sector")) == key and [int(v) for v in (p.get("cell") or [-1, -1])] == [int(pos[2][0]), int(pos[2][1])]:
            return pid, p
    return None


def docked_planet_id(snapshot: dict) -> str | None:
    got = docked_planet(snapshot)
    return got[0] if got else None


def cargo_stacks(snapshot: dict) -> int:
    """C5: the number of element stacks the player holds."""
    cap, _ = _player(snapshot)
    items = cap.get("items") or {}
    return len([k for k, v in items.items() if int(v) > 0]) if isinstance(items, dict) else len(items)


def price_line(cfg: GameConfig, item: str, respect: float) -> str:
    """PRC-01...03 / EF-04: buy and sell prices for one unit at the player's respect, as the planel shows them."""
    from starbour.engine.economy import planet_unit_price   # a pure formula; no sockets
    buy = planet_unit_price(cfg, item, respect, selling=False) or 0.0
    sell = planet_unit_price(cfg, item, respect, selling=True) or 0.0
    return f"buy {P.number(buy)} / sell {P.number(sell)} silver"


def planet_panel(snapshot: dict, cfg: GameConfig, width: int) -> Panel | None:
    """C11: the planet in the player's cell - name, president, disposition, whether it trades, prices and stock (PL-01...06, PR-05)."""
    got = docked_planet(snapshot)
    if got is None:
        return None
    pid, p = got
    cap, _ = _player(snapshot)
    respect = float(cap.get("respect", 0))
    t = _grid()
    _kv(t, "Planet", f"{p.get('name', pid)} ({pid})", P.STYLES["title"])
    _kv(t, "President", str(p.get("president", "?")))
    disp = int(p.get("disposition_to_player", 0))
    _kv(t, "Disposition", f"{disp} (respect {int(respect)}: 2 x respect - 100)", P.STYLES["good"] if disp >= 0 else P.STYLES["warn"])
    if p.get("trades_with_player"):
        _kv(t, "Trade", f"trades with you - buy / sell here (PR-01)", P.STYLES["good"])
        for item in ("food", "water", "torpedo"):
            _kv(t, item.capitalize(), price_line(cfg, item, respect))
        _kv(t, "Elements", f"sold at {cfg.prices.get('element_sell_pct', 0)} % of value, bought at value (PRC-04)")
    else:
        _kv(t, "Trade", f"refuses to trade: respect {int(respect)} < {cfg.planet_refuse_respect} (EF-04)", P.STYLES["bad"])
    st = p.get("stock") or {}
    els = st.get("elements") or {}
    stock = f"food {st.get('food', 0)}, water {st.get('water', 0)}" + (", " + ", ".join(f"{k} {v}" for k, v in sorted(els.items())) if els else "")
    _kv(t, "Stock", stock)
    _kv(t, "Defence", f"shields {float(p.get('shields', 0)):g} / {float(p.get('shield_max', 0)):g}, torpedoes {p.get('torpedoes', 0)}, phasers {float(p.get('phasers', 0)):g}")
    if p.get("in_battle"):
        _kv(t, "Battle", f"in battle {p.get('in_battle')}", P.STYLES["bad"])
    return Panel(t, title="Planet here", title_align="left", border_style=P.STYLES["planet_panel"] if p.get("trades_with_player") else P.STYLES["bad"],
                 width=min(width, PANEL_WIDTH))


def order_lines(ship: dict, cfg: GameConfig) -> list[str]:
    """PNL-07 / D-99: the queued orders in order; the persisting move shows its cells per turn (C4)."""
    orders = ship.get("orders") or []
    remaining = ship.get("move_remaining")
    hz = ship.get("hazard") if isinstance(ship.get("hazard"), dict) else None
    pull = float(hz.get("pull", 0)) if hz else 0.0
    out: list[str] = []
    for i, o in enumerate(orders, start=1):
        args = [str(a) for a in o.get("args") or []]
        line = f"{i}. {o.get('command', '?')} {' '.join(args)}".rstrip()
        persisting = bool(o.get("persisting"))
        if not persisting and o.get("command") == "move" and isinstance(remaining, list) and len(args) >= 2:
            try:
                persisting = [int(args[0]), int(args[1])] == [int(v) for v in remaining]
            except ValueError:
                persisting = False
        if persisting:
            try:
                speed = float(args[2]) if len(args) > 2 else float(ship.get("engine", 0))
            except ValueError:
                speed = float(ship.get("engine", 0))
            speed = min(speed, float(ship.get("engine", 0)))
            cells = max(0, math.floor(speed - pull + 1e-9))
            line += f" (persisting, {cells} cells/turn)"
        out.append(line)
    return out


def orders_panel(ship: dict | None, cfg: GameConfig, width: int) -> Panel:
    """PNL-07."""
    lines = order_lines(ship, cfg) if ship else []
    if not lines:
        body: RenderableType = Text("no orders queued", style=P.STYLES["label"])
    else:
        body = Text("\n".join(lines), style=P.STYLES["value"], no_wrap=True, overflow="crop")
    return Panel(body, title=f"Orders ({len(lines)})", title_align="left", border_style=P.STYLES["accent"], width=min(width, PANEL_WIDTH))


def cargo_panel(cap: dict, cfg: GameConfig, width: int) -> Panel:
    """PNL-04 / C11: each element stack with its units and list value ([elements]); special items marked."""
    raw = cap.get("items") or {}
    items: dict[str, int] = {}
    if isinstance(raw, dict):
        items = {str(k): int(v) for k, v in raw.items() if int(v) > 0}
    else:
        for name in raw:
            items[str(name)] = items.get(str(name), 0) + 1
    if not items:
        body: RenderableType = Text("empty", style=P.STYLES["label"])
    else:
        t = _grid()
        total = 0
        for name in sorted(items):
            units = items[name]
            spec = cfg.elements.get(name.lower())
            if spec is None:
                _kv(t, name, f"{units} units")
                continue
            value = int(spec.value) * units
            total += value
            special = spec.rarity == "special"
            _kv(t, name, f"{units} unit{'s' if units != 1 else ''}, {value} silver ({int(spec.value)} each), {spec.category.replace('_', ' ')}"
                + (", special" if special else ""), P.STYLES["item_special"] if special else P.STYLES["value"])
        _kv(t, "Total", f"{total} silver at list value (planets pay {cfg.prices.get('element_sell_pct', 0)} %)", P.STYLES["label"])
        body = t
    return Panel(body, title=f"Cargo ({len(items)} stack{'s' if len(items) != 1 else ''})", title_align="left", border_style=P.STYLES["accent"],
                 width=min(width, PANEL_WIDTH))


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
    if not (cap.get("ships") or []):
        t.add_row(Text("no ships", style=P.STYLES["bad"]), "", "", "", "")
    return Panel(t, title="Fleet", title_align="left", border_style=P.STYLES["accent"], width=min(width, PANEL_WIDTH))


def game_over_banner(snapshot: dict, width: int) -> Panel:
    """OUT-16 / D-105: shown while snapshot.game_over is true."""
    t = Text(no_wrap=True, overflow="crop")
    t.append("GAME OVER", style=P.STYLES["game_over"])
    t.append(f" - your ship was destroyed at turn {snapshot.get('game_over_turn', snapshot.get('turn_no', '?'))}.\n", style=P.STYLES["bad"])
    t.append("Type 'new' in the command terminal to start again (CMD-19).", style=P.STYLES["value"])
    return Panel(t, border_style=P.STYLES["game_over"], width=min(width, PANEL_WIDTH))


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
    cap, ship = _player(snapshot)
    parts: list[RenderableType] = [header(snapshot, width)]
    if snapshot.get("game_over"):
        parts.append(game_over_banner(snapshot, width))
    parts += [captain_panel(snapshot, cap, cfg, width), ship_panel(snapshot, cap, ship, cfg, width), supplies_panel(cap, cfg, width)]
    hz = hazard_panel(ship, cfg, width) if ship else None
    if hz is not None:
        parts.append(hz)
    bp = battle_panel(snapshot, ship, cfg, width)
    if bp is not None:
        parts.append(bp)
    pp = planet_panel(snapshot, cfg, width)
    if pp is not None:
        parts.append(pp)
    parts += [orders_panel(ship, cfg, width), cargo_panel(cap, cfg, width), fleet_panel(snapshot, cap, width)]
    return Group(*parts)


def in_battle(snapshot: dict) -> bool:
    """C5 (M3 addition, live/09): whether the player's active ship is in a battle - carried by T4's heartbeat."""
    cap, ship = _player(snapshot)
    return bool((ship or {}).get("in_battle"))
