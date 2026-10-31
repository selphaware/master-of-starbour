"""T2 Grid renderer - pure: render(snapshot, view, cfg, width) -> Rich renderable (C4, D-65).

Sector view by default (auto-follow, GRD-02); ``view`` may ask for galaxy or universe
(VW-01...03).  Glyphs and colours come from the palette and ``[ui] glyph_set`` (D-68);
coordinates are zero-based with labels (GRD-01); fog of war hides unvisited sectors
(GRD-04); one text line per grid row so a glyph's text position is its cell (GRD-01).
The viewport is clipped to ``width``/``height`` centred on the ship (GRD-05).

M2 (GRD-03): every cell inside a hazard's pull range is tinted - a star's own cell, a
black hole's, wormhole's or neutron star's whole sector - and the player's countdown
(``ships[*].hazard.turns_left``) is written beside the active ship's glyph.  The galaxy
view marks sectors with known hazards (VW-02).

M4 (SYM-08, SYM-09): a planet that refuses trade (the player's respect below
``planet_refuse_respect``, EF-04) or is in battle with the player draws in its own colour; a
destroyed planet is gone from the terrain and draws nothing; an item stack draws only once
revealed by a scan (or with fog of war off).
"""
from __future__ import annotations

from rich.console import Group, RenderableType
from rich.text import Text

from starbour.config import GameConfig
from starbour.terminals import palette as P

CELL_W = 2   # glyph + space: 24 cells fit in 48 columns
SECTOR_WIDE = ("black_hole", "wormhole", "neutron_star")   # HZ-04, HZ-05, HZ-06: the whole sector is in range


def _line(*parts: tuple[str, str] | Text) -> Text:
    t = Text(no_wrap=True, overflow="crop")
    for part in parts:
        if isinstance(part, Text):
            t.append_text(part)
        else:
            t.append(part[0], style=part[1])
    return t


def _player_ship(snapshot: dict) -> tuple[dict | None, dict | None]:
    cap = (snapshot.get("captains") or {}).get(snapshot.get("player_id"))
    if not cap:
        return None, None
    ship = (snapshot.get("ships") or {}).get(cap.get("active_ship"))
    return cap, ship


def resolve_level(view: dict | None, snapshot: dict) -> str:
    """GRD-02: 'follow' (or nothing) means the active ship's sector."""
    level = str((view or snapshot.get("view") or {}).get("level") or "follow").lower()
    return "sector" if level == "follow" else level


def _window(size: int, centre: int, visible: int) -> tuple[int, int]:
    """GRD-05: the [start, end) range of cells shown when the grid is wider than the viewport."""
    if visible >= size:
        return 0, size
    start = min(max(centre - visible // 2, 0), size - visible)
    return start, start + visible


def _column_labels(x0: int, x1: int, indent: int) -> list[Text]:
    """GRD-01: zero-based column labels along the top (tens row only when needed)."""
    rows: list[Text] = []
    if x1 > 10:
        tens = Text(" " * indent, no_wrap=True, overflow="crop")
        for x in range(x0, x1):
            tens.append(f"{x // 10 if x >= 10 else ' '}".ljust(CELL_W), style=P.STYLES["label"])
        rows.append(tens)
    units = Text(" " * indent, no_wrap=True, overflow="crop")
    for x in range(x0, x1):
        units.append(f"{x % 10}".ljust(CELL_W), style=P.STYLES["label"])
    rows.append(units)
    return rows


def hazard_tints(terrain: list[dict]) -> tuple[str | None, dict[tuple[int, int], str]]:
    """GRD-03: (kind tinting the whole sector or None, {cell: kind} for star cells)."""
    sector_kind: str | None = None
    cells: dict[tuple[int, int], str] = {}
    for t in terrain:
        kind = str(t.get("kind", ""))
        cell = tuple(int(v) for v in (t.get("cell") or (-1, -1)))
        if kind in SECTOR_WIDE and sector_kind is None:
            sector_kind = kind
        elif kind == "star":
            cells[cell] = kind
    return sector_kind, cells


def planet_states(snapshot: dict, cfg: GameConfig, skey: str, cap: dict | None) -> tuple[bool, dict[tuple[int, int], dict]]:
    """SYM-08 (M4): whether planets trade with the player by respect (EF-04, TR-10), and the C11 records of this sector by cell."""
    cap = cap or {}
    trades = bool(cap.get("is_master")) or int(cap.get("respect", 0)) >= int(cfg.planet_refuse_respect)
    records: dict[tuple[int, int], dict] = {}
    for pid, p in (snapshot.get("planets") or {}).items():
        if isinstance(p, dict) and str(p.get("sector")) == skey and isinstance(p.get("cell"), list):
            records[(int(p["cell"][0]), int(p["cell"][1]))] = dict(p, id=pid)
    return trades, records


def render_sector(snapshot: dict, cfg: GameConfig, width: int, height: int | None) -> list[Text]:
    """VW-03 / SYM-* / GRD-03: the active ship's sector with the hazard overlay."""
    g = P.glyphs(cfg)
    cap, ship = _player_ship(snapshot)
    sw, sh = snapshot.get("sizes", {}).get("sector", list(cfg.sector_size))
    turn_no = snapshot.get("turn_no", 0)
    fog = bool(snapshot.get("fog_of_war", cfg.fog_of_war))
    lines: list[Text] = []
    if ship is None:
        lines.append(_line(("No active ship to follow", P.STYLES["bad"])))
        if snapshot.get("game_over"):
            lines.append(_line(("GAME OVER - type 'new' in the command terminal to start again (OUT-16)", P.STYLES["game_over"])))
        return lines
    (gx, gy), (sx, sy), (cx, cy) = ship["position"]
    gkey, skey = f"{gx},{gy}", f"{gx},{gy}/{sx},{sy}"
    visited_list = (snapshot.get("galaxies") or {}).get(gkey, {}).get("visited") or []
    visited = [sx, sy] in visited_list or (sx, sy) in {tuple(v) for v in visited_list}
    sector = (snapshot.get("sectors") or {}).get(skey) or {}
    show_terrain = visited or not fog
    cells: dict[tuple[int, int], tuple[str, str]] = {}
    sector_tint: str | None = None
    star_tints: dict[tuple[int, int], str] = {}
    player_id = snapshot.get("player_id")
    my_battles = {s.get("in_battle") for s in (snapshot.get("ships") or {}).values() if s.get("captain") == player_id and s.get("in_battle")}
    planet_cells: dict[tuple[int, int], str] = {}
    planet_names: dict[tuple[int, int], str] = {}
    if show_terrain:
        terrain = sector.get("terrain") or []
        sector_tint, star_tints = hazard_tints(terrain)
        trades, records = planet_states(snapshot, cfg, skey, cap)
        for t in terrain:
            kind = t.get("kind", "")
            cell = tuple(t.get("cell", (-1, -1)))
            if kind == "star":
                cells[cell] = (g["star"], P.star_style(str(t.get("colour", ""))))
            elif kind == "planet":
                rec = records.get(cell) or {}
                if rec.get("destroyed"):
                    continue
                hostile = (rec.get("in_battle") in my_battles and my_battles) or (rec and rec.get("trades_with_player") is False) or not trades
                cells[cell] = (g["planet"], P.STYLES["planet_refuses"] if hostile else P.STYLES["planet"])
                planet_cells[cell] = str(rec.get("id") or t.get("id") or "")
                planet_names[cell] = str(rec.get("name") or t.get("name") or planet_cells[cell])
            elif kind in ("black_hole", "neutron_star", "wormhole"):
                cells[cell] = (g[kind], P.STYLES[kind])
        for item in sector.get("items") or []:
            if item.get("revealed") or not fog:
                cell = tuple(item.get("cell", (-1, -1)))
                special = item.get("rarity") == "special"
                cells[cell] = (g["item_special" if special else "item"], P.STYLES["item_special" if special else "item"])
    # ships: active > owned > AI in battle with you > AI (SYM-11, SYM-12, SYM-14); the last target carries the GRD-08 marker
    target_id = ship.get("target")
    ai_cells: set[tuple[int, int]] = set()
    battle_cells: set[tuple[int, int]] = set()
    owned_cells: set[tuple[int, int]] = set()
    target_cell: tuple[int, int] | None = None
    for cell, pid in planet_cells.items():
        if pid and pid == target_id:
            target_cell = cell
    for sid, s in (snapshot.get("ships") or {}).items():
        pos = s.get("position")
        if not pos or list(pos[0]) != [gx, gy] or list(pos[1]) != [sx, sy]:
            continue
        cell = (int(pos[2][0]), int(pos[2][1]))
        if sid == target_id:
            target_cell = cell
        if sid == cap.get("active_ship"):
            continue
        if s.get("captain") == player_id:
            owned_cells.add(cell)
        elif s.get("in_battle") and s.get("in_battle") in my_battles:
            battle_cells.add(cell)
        else:
            ai_cells.add(cell)
    for cell in ai_cells:
        cells[cell] = (g["ship_ai"], P.STYLES["ship_ai"])
    for cell in battle_cells:
        cells[cell] = (g["ship_ai"], P.STYLES["ship_ai_battle"])
    for cell in owned_cells:
        cells[cell] = (g["ship_owned"], P.STYLES["ship_owned"])
    cells[(cx, cy)] = (g["ship_active"], P.STYLES["ship_active"])
    in_battle = ship.get("in_battle")

    hazard = ship.get("hazard") if isinstance(ship.get("hazard"), dict) else None
    label = str(hazard.get("turns_left")) if hazard is not None and hazard.get("turns_left") is not None else ""

    indent = 3
    visible_cols = max(1, (width - indent) // CELL_W)
    x0, x1 = _window(sw, cx, visible_cols)
    visible_rows = sh if height is None else max(1, height - 7)
    y0, y1 = _window(sh, cy, visible_rows)
    docked = planet_names.get((cx, cy))
    head = _line((f"Sector {sx},{sy}", P.STYLES["title"]), ("  galaxy ", P.STYLES["label"]), (f"{gx},{gy}", P.STYLES["value"]),
                 ("  turn ", P.STYLES["label"]), (f"{turn_no}", P.STYLES["value"]),
                 ("  fog ", P.STYLES["label"]), ("on" if fog else "off", P.STYLES["value"]),
                 (f"  BATTLE {in_battle}" if in_battle else "", P.STYLES["ship_ai_battle"]),
                 (f"  docked at {docked}" if docked else "", P.STYLES["planet"]),
                 ("" if show_terrain else "  (unexplored - terrain hidden)", P.STYLES["warn"]))
    lines.append(head)
    lines.extend(_column_labels(x0, x1, indent))
    void_glyph, void_style = g["void"], P.STYLES["void"]
    for y in range(y0, y1):
        # per-character build so the countdown label can overlay the cell after the ship (GRD-03)
        chars: list[tuple[str, str]] = [(ch, P.STYLES["label"]) for ch in f"{y:>2} "]
        for x in range(x0, x1):
            glyph, style = cells.get((x, y), (void_glyph, void_style))
            tint_kind = sector_tint or star_tints.get((x, y))
            style = P.tinted(style, tint_kind)
            if target_cell == (x, y) and (x, y) != (cx, cy):
                chars.append((glyph, style))
                chars.extend((ch, P.tinted(P.STYLES["target"], tint_kind)) for ch in g["target"].ljust(CELL_W - 1))   # GRD-08
                continue
            text = glyph.ljust(CELL_W)
            chars.extend((ch, style) for ch in text)
        if label and y == cy:
            start = indent + (cx - x0) * CELL_W + 1       # the space after the glyph
            for i, ch in enumerate(label):
                pos = start + i
                if pos < len(chars):
                    chars[pos] = (ch, P.STYLES["countdown"])
                else:
                    chars.append((ch, P.STYLES["countdown"]))
        row = Text(no_wrap=True, overflow="crop")
        for ch, style in chars:
            row.append(ch, style=style)
        lines.append(row)
    if (x0, x1, y0, y1) != (0, sw, 0, sh):
        lines.append(_line((f"viewport x {x0}-{x1 - 1}, y {y0}-{y1 - 1} of {sw}x{sh} (centred on the ship, GRD-05)", P.STYLES["label"])))
    if hazard is not None:
        kind = str(hazard.get("kind", "hazard")).replace("_", " ")
        hc = hazard.get("cell") or ["?", "?"]
        lines.append(_line((f"Hazard: {kind} at {hc[0]},{hc[1]} - pull {P.number(float(hazard.get('pull', 0)))}, ", P.STYLES["warn"]),
                           (f"{label} turn(s) until pulled in", P.STYLES["countdown"]),
                           ((f" - shields drain {hazard.get('drain')} per turn" if hazard.get("drain") else ""), P.STYLES["warn"])))
    if snapshot.get("game_over"):
        lines.append(_line(("GAME OVER - type 'new' in the command terminal to start again (OUT-16)", P.STYLES["game_over"])))
    lines.append(_legend(cfg, "sector"))
    return lines


def _legend(cfg: GameConfig, level: str) -> Text:
    g = P.glyphs(cfg)
    t = Text(no_wrap=True, overflow="crop")
    if level == "sector":
        entries = (("ship_active", "you"), ("ship_owned", "yours"), ("ship_ai", "AI"), ("ship_ai_battle", "in battle"), ("target", "target"),
                   ("star", "star"), ("planet", "planet (trades)"), ("planet_refuses", "planet (refuses)"), ("black_hole", "black hole"),
                   ("neutron_star", "neutron"), ("wormhole", "wormhole"), ("item", "item"), ("item_special", "special"), ("void", "void"))
    elif level == "galaxy":
        entries = (("here", "your sector"), ("sector", "sector"), ("void", "void"))
    else:
        entries = (("here", "your galaxy"), ("galaxy", "galaxy"), ("void", "void"))
    for key, label in entries:
        style = P.STYLES.get(key, P.STYLES["value"]) if key != "star" else P.star_style("yellow")
        if key == "ship_ai_battle":
            glyph = g["ship_ai"]
        elif key == "target":
            glyph = g["ship_ai"] + g["target"]
        elif key == "planet_refuses":
            glyph = g["planet"]
        else:
            glyph = g[key]
        t.append(glyph, style=style)
        t.append(f" {label}  ", style=P.STYLES["label"])
    if level == "sector":
        t.append(" ", style=P.tinted(P.STYLES["void"], "black_hole"))
        t.append(" pull range  ", style=P.STYLES["label"])
    if level == "galaxy":
        t.append(g["sector"], style=P.STYLES["sector_visited"])
        t.append(" visited  ", style=P.STYLES["label"])
        t.append(g["sector"], style=P.STYLES["sector_hazard"])
        t.append(" hazard", style=P.STYLES["label"])
    return t


def render_galaxy(snapshot: dict, cfg: GameConfig, width: int, height: int | None) -> list[Text]:
    """VW-02 / SYM-03: the active ship's galaxy; visited sectors cyan, known hazards red."""
    g = P.glyphs(cfg)
    cap, ship = _player_ship(snapshot)
    gw, gh = snapshot.get("sizes", {}).get("galaxy", list(cfg.galaxy_size))
    fog = bool(snapshot.get("fog_of_war", cfg.fog_of_war))
    lines: list[Text] = []
    if ship is None:
        return [_line(("No active ship to follow", P.STYLES["bad"]))]
    (gx, gy), (sx, sy), _ = ship["position"]
    gal = (snapshot.get("galaxies") or {}).get(f"{gx},{gy}") or {}
    sectors = {tuple(c) for c in gal.get("sectors") or []}
    visited = {tuple(c) for c in gal.get("visited") or []}
    hazards = {tuple(c) for c in gal.get("hazards") or []}
    lines.append(_line((f"Galaxy {gx},{gy}", P.STYLES["title"]), ("  turn ", P.STYLES["label"]), (f"{snapshot.get('turn_no', 0)}", P.STYLES["value"]),
                       ("  sectors ", P.STYLES["label"]), (f"{len(sectors)}", P.STYLES["value"]),
                       ("  visited ", P.STYLES["label"]), (f"{len(visited)}", P.STYLES["value"]),
                       ("  fog ", P.STYLES["label"]), ("on" if fog else "off", P.STYLES["value"])))
    indent = 3
    x0, x1 = _window(gw, sx, max(1, (width - indent) // CELL_W))
    y0, y1 = _window(gh, sy, gh if height is None else max(1, height - 5))
    lines.extend(_column_labels(x0, x1, indent))
    for y in range(y0, y1):
        row = Text(f"{y:>2} ", style=P.STYLES["label"], no_wrap=True, overflow="crop")
        for x in range(x0, x1):
            if (x, y) == (sx, sy):
                row.append(g["here"].ljust(CELL_W), style=P.STYLES["here"])
            elif (x, y) in sectors:
                if (x, y) in hazards and ((x, y) in visited or not fog):
                    row.append(g["sector"].ljust(CELL_W), style=P.STYLES["sector_hazard"])
                elif (x, y) in visited:
                    row.append(g["sector"].ljust(CELL_W), style=P.STYLES["sector_visited"])
                else:
                    row.append(g["sector"].ljust(CELL_W), style=P.STYLES["sector"])
            else:
                row.append(g["void"].ljust(CELL_W), style=P.STYLES["void"])
        lines.append(row)
    lines.append(_legend(cfg, "galaxy"))
    return lines


def render_universe(snapshot: dict, cfg: GameConfig, width: int, height: int | None) -> list[Text]:
    """VW-01 / SYM-02: every galaxy; the one holding the active ship is highlighted."""
    g = P.glyphs(cfg)
    cap, ship = _player_ship(snapshot)
    uw, uh = snapshot.get("sizes", {}).get("universe", list(cfg.universe_size))
    galaxies = {tuple(c) for c in (snapshot.get("universe") or {}).get("galaxies") or []}
    here = tuple(ship["position"][0]) if ship else None
    lines = [_line(("Universe", P.STYLES["title"]), ("  turn ", P.STYLES["label"]), (f"{snapshot.get('turn_no', 0)}", P.STYLES["value"]),
                   ("  galaxies ", P.STYLES["label"]), (f"{len(galaxies)}", P.STYLES["value"]))]
    indent = 3
    x0, x1 = _window(uw, here[0] if here else 0, max(1, (width - indent) // CELL_W))
    y0, y1 = _window(uh, here[1] if here else 0, uh if height is None else max(1, height - 5))
    lines.extend(_column_labels(x0, x1, indent))
    for y in range(y0, y1):
        row = Text(f"{y:>2} ", style=P.STYLES["label"], no_wrap=True, overflow="crop")
        for x in range(x0, x1):
            if (x, y) == here:
                row.append(g["here"].ljust(CELL_W), style=P.STYLES["here"])
            elif (x, y) in galaxies:
                row.append(g["galaxy"].ljust(CELL_W), style=P.STYLES["galaxy"])
            else:
                row.append(g["void"].ljust(CELL_W), style=P.STYLES["void"])
        lines.append(row)
    lines.append(_legend(cfg, "universe"))
    return lines


def render(snapshot: dict, view: dict | None, cfg: GameConfig, width: int = 120, height: int | None = None) -> RenderableType:
    """C4.  ``view`` is the MSG-04 payload (or None = the snapshot's view)."""
    level = resolve_level(view, snapshot)
    if level == "galaxy":
        lines = render_galaxy(snapshot, cfg, width, height)
    elif level == "universe":
        lines = render_universe(snapshot, cfg, width, height)
    else:
        lines = render_sector(snapshot, cfg, width, height)
    return Group(*lines)
