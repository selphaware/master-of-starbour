"""T1 Command renderer - pure: the prompt and the immediate command outputs (C4, UX-01, UX-09, CMD-01/02/04).

``render(snapshot, view, cfg, width)`` returns the coloured prompt ``[T<turn> . <ship> . <n> orders] >``.
``help_text('fire', cfg)`` carries the live combat numbers from the ini (UX-09, D-138, D-148 (9)).
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
    items = cap.get("items") or {}
    if items:
        cargo = Text(no_wrap=True)
        cargo.append("  cargo ", style=P.STYLES["label"])
        if isinstance(items, dict):
            cargo.append(", ".join(f"{k} x{v}" for k, v in sorted(items.items())), style=P.STYLES["value"])
        else:
            cargo.append(", ".join(str(i) for i in items), style=P.STYLES["value"])
        out.append(cargo)
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


def shields_lines(snapshot: dict) -> list[Text]:
    """CMD-09: current / max shields of the active ship, with the hazard drain when one applies (HZ-03, CM-05)."""
    _, ship = _player(snapshot)
    t = Text(no_wrap=True)
    if not ship:
        t.append("  no active ship", style=P.STYLES["bad"])
        return [t]
    sc, sm = float(ship.get("shield_current", 0)), float(ship.get("shield_max", 0))
    style = P.STYLES["good"] if sm and sc / sm > 0.5 else P.STYLES["warn"] if sc > 0 else P.STYLES["bad"]
    t.append(f"  shields {sc:g} / {sm:g}", style=style)
    hz = ship.get("hazard") if isinstance(ship.get("hazard"), dict) else None
    if hz and hz.get("drain"):
        t.append(f"  draining {hz['drain']} per turn in the {str(hz.get('kind', '')).replace('_', ' ')}'s cell (HZ-03)", style=P.STYLES["warn"])
    elif hz:
        t.append("  no recharge inside a pull range (CM-05)", style=P.STYLES["label"])
    elif sc < sm:
        t.append("  recharging outside hazards (CM-05)", style=P.STYLES["label"])
    return [t]


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


def fire_numbers(cfg: GameConfig, target_engine: float | None = None, phasers: float | None = None) -> dict:
    """UX-09 / D-138 / D-148 (9): the live combat numbers from the ini - torpedo damage, the hit chance against ``target_engine``
    (the starting class's engine when None) as a whole percentage, the phaser damage of ``phasers`` units (the starting class's
    when None) at distance 0 ... phaser_range."""
    from starbour.engine.combat import phaser_damage, torpedo_hit_p   # pure formulas; no sockets
    start = cfg.ships[cfg.starting_class]
    engine = float(start.engine) if target_engine is None else float(target_engine)
    power = float(start.phasers) if phasers is None else float(phasers)
    return {"torpedo_damage": float(cfg.torpedo_damage), "hit_pct": int(round(torpedo_hit_p(engine, cfg) * 100)), "engine": engine,
            "phaser_damages": [phaser_damage(power, cfg.phaser_damage_per_unit, d) for d in range(0, int(cfg.phaser_range) + 1)],
            "target": "the target" if target_engine is not None else f"a {start.name.capitalize()}"}


def help_text(name: str | None = None, cfg: GameConfig | None = None, snapshot: dict | None = None) -> list[Text]:
    """CMD-01 from the catalogue; the footer names the shield recharge from the config (CM-05, D-127); ``help fire`` prints the
    live numbers (UX-09, D-138): against the starting class, or against the ship's last target when the snapshot shows one."""
    numbers = None
    if cfg is not None:
        engine = phasers = None
        if snapshot:
            cap, ship = _player(snapshot)
            if ship:
                phasers = float(ship.get("phasers", 0) or 0)
                target = (snapshot.get("ships") or {}).get(ship.get("target"))
                if isinstance(target, dict):
                    engine = float(target.get("engine", 0) or 0)
        numbers = fire_numbers(cfg, engine, phasers)
    out = []
    for line in help_lines(name, cfg.shield_recharge if cfg is not None else None, numbers):
        style = P.STYLES["title"] if not line.startswith(" ") else P.STYLES["value"]
        out.append(Text(line, style=style, no_wrap=True))
    return out


def battle_with(snapshot: dict, target_text: str) -> tuple[dict | None, dict | None, bool]:
    """CM-01 / UX-04: (target ship or planet, its holder, already in battle with the player) for a fire target by id or name
    in the player's sector; (None, None, False) when the target is unknown (the engine will say why)."""
    cap, ship = _player(snapshot)
    if not ship:
        return None, None, False
    wanted = target_text.strip().lower()
    pos = ship.get("position") or [[0, 0], [0, 0], [0, 0]]
    pid = snapshot.get("player_id")
    target = None
    for sid, s in (snapshot.get("ships") or {}).items():
        p = s.get("position") or []
        if len(p) != 3 or list(p[0]) != list(pos[0]) or list(p[1]) != list(pos[1]):
            continue
        if sid.lower() == wanted or str(s.get("name", "")).lower() == wanted:
            target = dict(s, id=sid)
            break
    if target is None:
        key = f"{pos[0][0]},{pos[0][1]}/{pos[1][0]},{pos[1][1]}"
        for ppid, p in (snapshot.get("planets") or {}).items():
            if isinstance(p, dict) and not p.get("destroyed") and str(p.get("sector")) == key and (ppid.lower() == wanted or str(p.get("name", "")).lower() == wanted):
                target = dict(p, id=ppid, captain=ppid)
                holder = {"id": ppid, "name": p.get("name", ppid)}
                fighting = any(isinstance(b, dict) and b.get("ended") is None and {str(x) for x in (b.get("sides") or [])} == {pid, ppid}
                               for b in (snapshot.get("battles") or []))
                return target, holder, fighting
        return None, None, False
    if target.get("captain") == pid:
        return target, None, False
    tcap = (snapshot.get("captains") or {}).get(target.get("captain")) or {}
    fighting = any(isinstance(b, dict) and b.get("ended") is None and {str(x) for x in (b.get("sides") or [])} == {pid, target.get("captain")}
                   for b in (snapshot.get("battles") or []))
    return target, dict(tcap, id=target.get("captain")), fighting
