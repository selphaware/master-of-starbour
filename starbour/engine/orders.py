"""Per-ship order queues and validation against the state - OV-02b, UX-02, UX-07, CMD-03/06/07/08/10/11/12/13/14/15/16/20/21, D-92, D-98, D-120, D-137.

Queued orders wait for ``end``; a second order of the same kind for the same ship
replaces the first (OV-02b) - a persisting move counts as the ship's queued move, so a
new ``move`` replaces it and ``stop`` cancels it (CMD-21, D-98); a torpedo and a phaser
volley are different kinds (D-120); ``escape`` is a move (D-123); ``fire <target>`` queues
both (D-137).  Trade, upgrade and pickup orders are checked for what can be known now (a
planet or counterparty in the cell, the goods held, a scanned stack, the map pairing) and
resolve at TP-05 (M4 section 6).  Immediate commands that change state (ship, scan, orders
cancel, stop) are applied here at once (D-92).  Pure functions of (state, order, cfg); no sockets.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from starbour.commands.catalogue import order_kind
from starbour.commands.parse import (BOTH, QUEUED, CommandError, Order, parse_buy, parse_escape, parse_fire, parse_move, parse_orders, parse_peace,
                                     parse_pickup, parse_sell, parse_ship, parse_stop, parse_trade, parse_upgrade, parse_warp)
from starbour.config import GameConfig
from starbour.engine import combat as C
from starbour.engine import economy as E
from starbour.engine import items as I
from starbour.engine import movement as MV
from starbour.engine.state import GameState
from starbour.entities.planet import Planet
from starbour.world.grid import chebyshev


@dataclass(frozen=True, slots=True)
class Accepted:
    """MSG-07: the order is queued (or applied) - ``queued`` is the ship's queue length now."""

    order: Order
    queue_position: int
    queued: int
    text: str = ""
    events: tuple[dict, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class Rejected:
    """MSG-06: the reason names the CMD id (UX-03)."""

    order: Order
    reason: str


def pending(state: GameState, ship_id: str) -> list[Order]:
    return list(state.queues.get(ship_id, []))


def pending_count(state: GameState, ship_id: str) -> int:
    return len(state.queues.get(ship_id, []))


def _own_ship(state: GameState, order: Order) -> str | None:
    ship = state.ships.get(order.ship_id)
    if ship is None:
        return f"unknown ship {order.ship_id!r} (CMD-03)"
    if ship.captain_id != state.player_id:
        return f"the {ship.name} is not one of your ships (CMD-03)"
    return None


def warp_factor(order_factor: float | None, ship_warp: float) -> float:
    """CMD-07: the factor defaults to the ship's full warp core power."""
    return float(ship_warp) if order_factor is None else float(order_factor)


def _validate_move(state: GameState, ship, spec, cfg: GameConfig, cmd_id: str) -> str | None:
    speed = spec.speed if spec.speed is not None else ship.engine
    if speed > ship.engine + 1e-9:
        return f"speed {speed:g} exceeds engine_power {ship.engine:g} ({cmd_id})"
    if speed > cfg.impulse_max + 1e-9:
        return f"speed {speed:g} exceeds impulse_max {cfg.impulse_max:g} (CFG-22, {cmd_id})"
    tx, ty = spec.target(ship.cell)
    w, h = cfg.sector_size
    if not (0 <= tx < w and 0 <= ty < h):
        return f"target {tx},{ty} is outside the sector {w}x{h} ({cmd_id})"
    return None


def _fire_weapons(state: GameState, ship, spec, cfg: GameConfig) -> tuple[list[str], str | None]:
    """Which weapons a fire order can use at the target now: (weapons, rejection reason)."""
    target = C.find_ship(state, spec.target, galaxy=ship.galaxy, sector=ship.sector)
    if target is None:
        return [], f"no ship or planet {spec.target!r} in sector {ship.galaxy[0]},{ship.galaxy[1]}/{ship.sector[0]},{ship.sector[1]} - 'scan' lists them (CMD-08)"
    if target.captain_id == ship.captain_id:
        return [], f"the {target.name} is one of your own ships (CMD-08)"
    d = chebyshev(ship.cell, target.cell)
    if spec.weapon == BOTH:
        weapons = []
        if ship.torpedoes >= 1 and d <= C.range_of(C.TORPEDO, cfg):
            weapons.append(C.TORPEDO)
        if ship.phasers > 0 and d <= C.range_of(C.PHASER, cfg):
            weapons.append(C.PHASER)
        if not weapons:
            longest = max(C.range_of(C.TORPEDO, cfg) if ship.torpedoes >= 1 else 0, C.range_of(C.PHASER, cfg) if ship.phasers > 0 else 0)
            if longest == 0:
                return [], f"the {ship.name} has no torpedoes and no phasers (CMD-08, SH-01, SH-02)"
            return [], f"target {target.id} is {d} cells away; torpedo range {cfg.torpedo_range}, phaser range {cfg.phaser_range} (CM-02)"
        return weapons, None
    rng_ = C.range_of(spec.weapon, cfg)
    if d > rng_:
        return [], f"target {target.id} is {d} cells away; {spec.weapon} range {rng_} (CM-02)"
    if spec.weapon == C.TORPEDO:
        if ship.torpedoes < 1:
            return [], f"the {ship.name} has no torpedoes left (CMD-08, SH-01)"
        if spec.count > ship.torpedoes:
            return [], f"the {ship.name} has only {ship.torpedoes} torpedo(es), cannot fire {spec.count} (CMD-08, SH-01)"
    elif ship.phasers <= 0:
        return [], f"the {ship.name} has no phasers (CMD-08, SH-02)"
    return [spec.weapon], None


def _counterparty(state: GameState, ship, text: str):
    """CMD-12 / PR-04: the planet in the ship's cell or a captain with a ship in it, by id or name; (holder, their ship, reason)."""
    planet = state.planet_at(ship.galaxy, ship.sector, ship.cell)
    wanted = text.strip().lower()
    if planet is not None and (planet.id.lower() == wanted or planet.name.lower() == wanted):
        return planet, None, None
    cap = C.find_captain(state, text)
    if cap is not None and cap.id != ship.captain_id:
        theirs = [s for s in C.ships_of(state, cap.id, ship.galaxy, ship.sector) if s.cell == ship.cell]
        if theirs:
            return cap, theirs[0], None
        return None, None, f"{cap.name} has no ship in your cell {ship.cell[0]},{ship.cell[1]} - a trade needs the same cell (CMD-12)"
    if planet is not None:
        return None, None, f"no counterparty {text!r} here; the planet in this cell is {planet.name} ({planet.id}) (CMD-12)"
    return None, None, f"no planet or captain {text!r} in your cell {ship.cell[0]},{ship.cell[1]} (CMD-12, PR-04)"


def validate(state: GameState, order: Order, cfg: GameConfig) -> str | None:
    """None when the order can be queued, else the rejection reason (CMD-06 speed and bounds; CMD-07 reach; CMD-08 range;
    CMD-10 a battle to make peace in; CMD-11 a battle to escape from; CMD-12/20 a counterparty in the cell and the goods;
    CMD-13 the map pairing and the element held; CMD-15 a scanned stack)."""
    bad = _own_ship(state, order)
    if bad:
        return bad
    ship = state.ships[order.ship_id]
    if order.command == "move":
        try:
            spec = parse_move(order.args)
        except CommandError as e:
            return str(e)
        return _validate_move(state, ship, spec, cfg, "CMD-06")
    if order.command == "escape":
        try:
            spec = parse_escape(order.args)
        except CommandError as e:
            return str(e)
        if state.battle_of_ship(ship) is None:
            return f"the {ship.name} is not in a battle - 'move' instead (CMD-11)"
        return _validate_move(state, ship, spec, cfg, "CMD-11")
    if order.command == "warp":
        try:
            spec = parse_warp(order.args)
        except CommandError as e:
            return str(e)
        factor = warp_factor(spec.factor, ship.warp)
        if factor > ship.warp + 1e-9:
            return f"factor {factor:g} exceeds warpcore_power {ship.warp:g} (CMD-07, SH-05)"
        if factor > cfg.warp_max + 1e-9:
            return f"factor {factor:g} exceeds warp_max {cfg.warp_max:g} (CFG-22, CMD-07)"
        (gx, gy), (sx, sy) = spec.galaxy, spec.sector
        uw, uh = cfg.universe_size
        gw, gh = cfg.galaxy_size
        if not (0 <= gx < uw and 0 <= gy < uh):
            return f"galaxy {gx},{gy} is outside the universe {uw}x{uh} (CMD-07, VW-01)"
        if not (0 <= sx < gw and 0 <= sy < gh):
            return f"sector {sx},{sy} is outside the galaxy {gw}x{gh} (CMD-07, VW-02)"
        if state.world.sector((gx, gy), (sx, sy)) is None:
            return f"destination {gx},{gy}/{sx},{sy} is Void - there is no sector there (CMD-07, WG-02)"
        if (gx, gy) == ship.galaxy and (sx, sy) == ship.sector:
            return f"the {ship.name} is already in sector {gx},{gy}/{sx},{sy} (CMD-07)"
        pull = state.pull_at(ship)
        distance = MV.warp_distance(ship.galaxy, ship.sector, (gx, gy), (sx, sy), cfg.galaxy_size)
        ok, eff, reach = MV.within_reach(distance, factor, pull, cfg.warp_curve_base, cfg.warp_curve_exponent)
        if eff <= 0:
            return None   # accepted; fails at resolution with a hazard event (HZ-10, MV-03, D-107)
        if not ok:
            netted = f" (effective factor {eff:.2f} after the pull of {pull:.2f} here, HZ-10)" if pull > 0 else ""
            return (f"destination {gx},{gy}/{sx},{sy} is {distance} sectors away, beyond the reach {reach:.2f} for factor {factor:g}"
                    f"{netted} (CMD-07, MV-02)")
        return None
    if order.command == "fire":
        try:
            spec = parse_fire(order.args)
        except CommandError as e:
            return str(e)
        _, reason = _fire_weapons(state, ship, spec, cfg)
        return reason
    if order.command == "peace":
        try:
            spec = parse_peace(order.args)
        except CommandError as e:
            return str(e)
        side = C.find_side(state, spec.captain)
        if side is None:
            return f"no captain or planet {spec.captain!r} (CMD-10)"
        if side.id == ship.captain_id:
            return "you cannot make peace with yourself (CMD-10)"
        if C.open_battle_between(state, ship.captain_id, side.id) is None:
            return f"no battle with {side.name} to make peace in (CMD-10)"
        return None
    if order.command in ("buy", "sell"):
        try:
            spec = parse_buy(order.args) if order.command == "buy" else parse_sell(order.args)
        except CommandError as e:
            return str(e)
        planet = state.planet_at(ship.galaxy, ship.sector, ship.cell)
        if planet is None:
            return f"no planet in cell {ship.cell[0]},{ship.cell[1]} - buy and sell need the planet's cell (CMD-20, PR-04)"
        why = E.refusal(planet, state.player, cfg)
        if why:
            return why
        cap = state.player
        if order.command == "buy" and spec.item == "ship":
            price = cfg.prices.get(f"ship_{spec.cls}")
            if price is None or spec.cls not in cfg.ships:
                return f"{planet.name} sells no ship of class {spec.cls!r} - [prices] lists ship_{{class}} for {', '.join(sorted(k[5:] for k in cfg.prices if k.startswith('ship_')))} (PRC-05)"
            if cap.gold < int(price):
                return f"not enough gold: {price} needed, {cap.gold} held (PRC-05)"
            return None
        unit = E.planet_unit_price(cfg, spec.item, cap.respect, selling=(order.command == "sell"))
        if unit is None:
            return f"{planet.name} {'buys' if order.command == 'sell' else 'sells'} no {spec.item!r} - food, water, torpedo or an element (CMD-20, PRC-01...04)"
        if order.command == "buy":
            cost = E.total_price(unit, spec.count)
            have = E.wealth(cap, cfg)
            available = E.planet_holds(planet, spec.item, state.turn_no + 1, cfg)
            problems = []
            if have < cost:
                problems.append(f"not enough silver: {cost} needed, {have} held (TR-04)")
            if available is not None and available < spec.count:
                problems.append(f"{planet.name} has only {available} {spec.item} in stock (PL-02, PL-03)")
            return "; ".join(problems) if problems else None
        have = E.captain_holds(cap, ship, spec.item)
        if have < spec.count:
            return f"you hold only {have} {spec.item}, not {spec.count} (CMD-20)"
        return None
    if order.command == "trade":
        try:
            spec = parse_trade(order.args)
        except CommandError as e:
            return str(e)
        holder, their_ship, reason = _counterparty(state, ship, spec.counterparty)
        if reason:
            return reason
        bad = E.unknown_items(cfg, spec.give) + E.unknown_items(cfg, spec.want)
        if bad:
            return f"unknown item(s) {', '.join(sorted(set(bad)))} - food, water, torpedo, gold, silver or an element (CMD-12)"
        if isinstance(holder, Planet):
            why = E.refusal(holder, state.player, cfg)
            if why:
                return why
            missing = E.planet_missing(holder, spec.want, state.turn_no + 1, cfg)
        else:
            missing = E.captain_missing(holder, their_ship, spec.want, holder.name)
        if missing:
            return missing
        return E.captain_missing(state.player, ship, spec.give)
    if order.command == "upgrade":
        try:
            spec = parse_upgrade(order.args)
        except CommandError as e:
            return str(e)
        table = cfg.upgrades.get(spec.component) or {}
        if spec.element not in table:
            improves = ", ".join(sorted(table)) or "nothing"
            return f"{spec.element} does not improve {spec.component} - the [upgrades] map lists {improves} (CMD-13, TR-05)"
        if state.player.items.get(spec.element, 0) < 1:
            return f"no {spec.element} held - buy or pick one up first (CMD-13)"
        return None
    if order.command == "pickup":
        try:
            element = parse_pickup(order.args)
        except CommandError as e:
            return str(e)
        sector = state.sector_of(ship)
        if sector is None:
            return "no sector here (CMD-15)"
        stacks = I.items_at(sector, ship.cell)
        if not stacks:
            return f"nothing lies in cell {ship.cell[0]},{ship.cell[1]} (CMD-15)"
        if not any(i.revealed for i in stacks):
            return f"nothing has been scanned in this cell - 'scan' first, items are never picked up unscanned (IT-04, CMD-15)"
        if I.revealed_stack(sector, ship.cell, element) is None:
            seen = ", ".join(sorted({i.element for i in stacks if i.revealed}))
            return f"no scanned {element} here; the scan found {seen} (CMD-15)"
        return None
    return f"{order.command} cannot be queued yet ({order.command} arrives in a later milestone)"


def _queue(state: GameState, order: Order) -> tuple[int, int, str]:
    """Add one order to its ship's queue, replacing a same-kind one (OV-02b); returns (position, length, replacement note)."""
    queue = state.queues.setdefault(order.ship_id, [])
    kind = order_kind(order.command, order.args)
    for i, existing in enumerate(queue):
        if order_kind(existing.command, existing.args) == kind:
            queue[i] = order
            what = "persisting" if existing.persisting else "earlier"
            return i + 1, len(queue), f"replaced the {what} {existing.command} order {existing.text()} (OV-02b)"
    queue.append(order)
    return len(queue), len(queue), ""


def submit(state: GameState, order: Order, cfg: GameConfig) -> Accepted | Rejected:
    """Queue a validated order; same-kind replaces (OV-02b) - including a persisting move (D-98)."""
    if order.kind != QUEUED:
        return Rejected(order, f"{order.command} is an immediate command, not a queued order (OV-02b)")
    reason = validate(state, order, cfg)
    if reason:
        return Rejected(order, reason)
    note = ""
    events: tuple[dict, ...] = ()
    ship = state.ships[order.ship_id]
    if order.command == "warp":
        spec = parse_warp(order.args)
        pull = state.pull_at(ship)
        factor = warp_factor(spec.factor, ship.warp)
        distance = MV.warp_distance(ship.galaxy, ship.sector, spec.galaxy, spec.sector, cfg.galaxy_size)
        eff = MV.effective_factor(factor, pull)
        if eff <= 0:
            note = f"warp queued ({distance} sectors) - warning: the pull of {pull:.2f} here nets factor {factor:g} to {eff:.2f}; the warp will fail unless the pull drops (HZ-10)"
        else:
            note = f"warp queued: {distance} sectors at factor {factor:g}" + (f" (effective {eff:.2f} against pull {pull:.2f})" if pull > 0 else "")
    elif order.command == "fire":
        spec = parse_fire(order.args)
        target = C.find_ship(state, spec.target, galaxy=ship.galaxy, sector=ship.sector)
        weapons, _ = _fire_weapons(state, ship, spec, cfg)
        if target is not None:
            d = chebyshev(ship.cell, target.cell)
            tname = target.name if isinstance(target, Planet) else target.name
            if spec.weapon == BOTH:
                what = " and ".join(("1 torpedo" if w == C.TORPEDO else "the phasers") for w in weapons)
                left_out = "" if len(weapons) == 2 else (" (phasers out of range)" if C.PHASER not in weapons else " (no torpedoes / out of torpedo range)")
                note = f"{what} at the {tname} ({target.id}, {d} cells) queued{left_out}"
            else:
                what = f"{spec.count} torpedo(es)" if spec.weapon == C.TORPEDO else "phasers"
                note = f"{what} at the {tname} ({target.id}, {d} cells) queued"
            if C.open_battle_between(state, ship.captain_id, target.captain_id) is None:
                # CM-01 / D-119: the first accepted fire opens the battle; FR-06 applies at this turn's TP-07
                bt, ev = C.open_battle(state, cfg, ship, target, state.turn_no + 1, state.pending_reputation)
                note += f" - battle {bt.id} with {state.side_name(target.captain_id)} begins; respect -{cfg.respect_start_fight_delta} (CM-01, FR-06)"
                events = (ev,)
            ship.target = target.id
        if spec.weapon == BOTH:
            # D-137: one order per weapon, each its own OV-02b kind (D-120)
            pos, length, replaced = 0, pending_count(state, ship.id), ""
            for w in weapons:
                args = (C.TORPEDO, spec.target, "1") if w == C.TORPEDO else (C.PHASER, spec.target)
                pos, length, rep = _queue(state, Order("fire", args, ship.id, QUEUED))
                replaced = replaced or rep
            text = (replaced + "; " if replaced else "") + note
            return Accepted(order, pos, length, text, events)
    elif order.command == "peace":
        spec = parse_peace(order.args)
        side = C.find_side(state, spec.captain)
        note = f"peace {spec.action} {side.name if side else spec.captain} queued - resolves with the turn (CM-09)"
    elif order.command == "escape":
        note = "escape queued: succeeds if the move ends beyond every enemy's weapon range (CB-02, CMD-11)"
    elif order.command in ("buy", "sell"):
        planet = state.planet_at(ship.galaxy, ship.sector, ship.cell)
        note = f"{order.command} queued at {planet.name if planet else 'the planet'} - resolves at the end of the turn (TP-05, CMD-20)"
    elif order.command == "trade":
        spec = parse_trade(order.args)
        holder, _, _ = _counterparty(state, ship, spec.counterparty)
        note = f"trade proposed to {holder.name if holder else spec.counterparty}: {E.bundle_text(spec.give)} for {E.bundle_text(spec.want)} - answered at the end of the turn (TP-05, CMD-12)"
    elif order.command == "upgrade":
        spec = parse_upgrade(order.args)
        note = f"upgrade queued: {spec.component} with {spec.element} (+{cfg.upgrades[spec.component][spec.element]:g}) - applied at the end of the turn (TP-05, CMD-13)"
    elif order.command == "pickup":
        note = f"pickup queued - the stack is taken at the end of the turn (TP-05, CMD-15)"
    pos, length, replaced = _queue(state, order)
    if replaced:
        return Accepted(order, pos, length, replaced + (f"; {note}" if note else ""), events)
    return Accepted(order, pos, length, note, events)


def cancel(state: GameState, ship_id: str, n: int) -> Order | None:
    """CMD-16 ``orders cancel n`` (1-based)."""
    queue = state.queues.get(ship_id, [])
    if 1 <= n <= len(queue):
        return queue.pop(n - 1)
    return None


def stop(state: GameState, ship_ids: list[str]) -> list[tuple[str, Order]]:
    """CMD-21 / D-98: drop every move or escape (persisting or queued) for the given ships; returns what was cancelled."""
    cancelled: list[tuple[str, Order]] = []
    for sid in ship_ids:
        queue = state.queues.get(sid)
        if not queue:
            continue
        kept = []
        for o in queue:
            if o.command in ("move", "escape"):
                cancelled.append((sid, o))
            else:
                kept.append(o)
        if kept:
            state.queues[sid] = kept
        else:
            state.queues.pop(sid, None)
    return cancelled


def take_all(state: GameState) -> dict[str, list[Order]]:
    """Remove and return every queue, keyed by ship id in sorted order (TP-01)."""
    out = {sid: list(state.queues[sid]) for sid in sorted(state.queues) if state.queues[sid]}
    state.queues.clear()
    return out


def clear(state: GameState) -> None:
    state.queues.clear()


def order_text(state: GameState, o: Order, cfg: GameConfig) -> str:
    """PNL-07 / D-99: the order as typed, a persisting move with its cells per turn."""
    text = o.text()
    if o.command in ("move", "escape") and o.persisting:
        ship = state.ships.get(o.ship_id)
        if ship is not None:
            try:
                speed = float(o.args[2]) if len(o.args) > 2 else ship.engine
            except ValueError:
                speed = ship.engine
            cells = MV.impulse_cells(min(speed, ship.engine), state.pull_at(ship))
            text += f" (persisting, {cells} cells/turn)"
        else:
            text += " (persisting)"
    return text


def _find_ship(state: GameState, text: str) -> str | None:
    """CMD-03: by fleet number (1-based), by name or by id, case-insensitively."""
    cap = state.player
    if text.isdigit():
        n = int(text)
        if 1 <= n <= len(cap.ships):
            return cap.ships[n - 1]
        return None
    wanted = text.strip().lower()
    for sid in cap.ships:
        ship = state.ships.get(sid)
        if ship and (ship.name.lower() == wanted or sid.lower() == wanted):
            return sid
    return None


def scan_text(state: GameState, ship, cfg: GameConfig) -> str:
    """CMD-14 / OUT-10 / LV-04: items in the cell (element, units, rarity), the planet here, terrain, hazard, and every other
    captain in the sector with fear and respect."""
    sector = state.sector_of(ship)
    found: list[str] = []
    if sector is not None:
        state.mark_visited(ship.galaxy, ship.sector)
        found = [I.item_text(item) for item in I.reveal(sector, ship.cell)]
        terrain = sector.terrain_at(ship.cell)
    else:
        terrain = None
    if isinstance(terrain, Planet):
        why = E.refusal(terrain, state.player, cfg)
        here = f"; planet {terrain.name} ({terrain.id}) here, president {terrain.president}: " + (why if why else "trades with you (PR-01)")
    else:
        here = f"; terrain here: {terrain.kind.replace('_', ' ')}" if terrain is not None else ""
    hz = state.hazard_dict(ship)
    warn = (f"; hazard: {hz['kind'].replace('_', ' ')} at {hz['cell'][0]},{hz['cell'][1]}, pull {hz['pull']:g}, {hz['turns_left']} turn(s) left"
            if hz else "")
    others: list[str] = []
    seen_turn = state.turn_no + 1          # the scan belongs to the turn being prepared (C10 visible_reputation)
    for sid in sorted(state.ships):
        s = state.ships[sid]
        if s.captain_id == ship.captain_id or s.galaxy != ship.galaxy or s.sector != ship.sector:
            continue
        cap = state.captains.get(s.captain_id)
        if cap is None:
            continue
        cap.rep_seen_turn = seen_turn
        bt = C.open_battle_between(state, ship.captain_id, cap.id)
        others.append(f"{cap.name} ({cap.id}) in the {s.name} ({sid}, {s.cls.capitalize()}) {chebyshev(s.cell, ship.cell)} cells away: "
                      f"fear {cap.fear}, respect {cap.respect}, shields {s.shield_current:g}/{s.shield_max:g}" + (f", in battle {bt.id} with you" if bt else ""))
    text = (f"Scan: {len(found)} item(s) in this cell: {', '.join(found)} - 'pickup <element>' takes a stack{here}{warn}" if found
            else f"Scan: nothing to pick up in cell {ship.cell[0]},{ship.cell[1]}{here}{warn}")
    if others:
        text += "; captains in this sector (LV-04): " + " | ".join(others)
    else:
        text += "; no other captain in this sector"
    return text


def apply_immediate(state: GameState, order: Order, cfg: GameConfig) -> Accepted | Rejected:
    """Immediate commands the engine applies at once (D-92): ship, scan, orders [cancel n], stop [all], view."""
    cmd = order.command
    if cmd == "ship":
        try:
            text = parse_ship(order.args)
        except CommandError as e:
            return Rejected(order, str(e))
        sid = _find_ship(state, text)
        if sid is None:
            return Rejected(order, f"no owned ship called {text!r} - 'fleet' lists them (CMD-03)")
        state.player.active_ship = sid
        ship = state.ships[sid]
        return Accepted(order, 0, pending_count(state, sid), f"active ship is now the {ship.name} ({sid})",
                        ({"phase": "TP-01", "type": "fleet", "severity": "info",
                          "text": f"Active ship switched to the {ship.name}", "ids": ["CMD-03", "OUT-11"]},))
    if cmd == "scan":
        ship = state.ships.get(order.ship_id) or state.active_ship()
        if ship is None or ship.captain_id != state.player_id:
            return Rejected(order, "no active ship to scan from (CMD-14)")
        text = scan_text(state, ship, cfg)
        return Accepted(order, 0, pending_count(state, ship.id), text,
                        ({"phase": "TP-01", "type": "scan", "severity": "info", "text": text, "ids": ["CMD-14", "OUT-10", "LV-04"]},))
    if cmd == "orders":
        try:
            n = parse_orders(order.args)
        except CommandError as e:
            return Rejected(order, str(e))
        bad = _own_ship(state, order)
        if bad:
            return Rejected(order, bad)
        if n is None:
            queue = pending(state, order.ship_id)
            text = "no orders queued" if not queue else "; ".join(f"{i + 1}. {order_text(state, o, cfg)}" for i, o in enumerate(queue))
            return Accepted(order, 0, len(queue), text)
        removed = cancel(state, order.ship_id, n)
        if removed is None:
            return Rejected(order, f"there is no queued order number {n} (CMD-16)")
        return Accepted(order, 0, pending_count(state, order.ship_id), f"cancelled order {n}: {order_text(state, removed, cfg)}")
    if cmd == "stop":
        try:
            everyone = parse_stop(order.args)
        except CommandError as e:
            return Rejected(order, str(e))
        if everyone:
            ship_ids = [s for s in state.player.ships if s in state.ships]
        else:
            bad = _own_ship(state, order)
            if bad:
                return Rejected(order, bad)
            ship_ids = [order.ship_id]
        cancelled = stop(state, ship_ids)
        queued = pending_count(state, order.ship_id) if order.ship_id in state.ships else 0
        if not cancelled:
            scope = "any of your ships" if everyone else f"the {state.ships[order.ship_id].name}"
            return Accepted(order, 0, queued, f"nothing to stop: no move is queued or persisting for {scope} (CMD-21)")
        parts = [f"{state.ships[s].name}: {o.text()}{' (persisting)' if o.persisting else ''}" for s, o in cancelled if s in state.ships]
        text = "all stop - cancelled " + "; ".join(parts)
        return Accepted(order, 0, queued, text,
                        ({"phase": "TP-01", "type": "stopped", "severity": "info", "text": text, "ids": ["CMD-21", "D-98"]},))
    if cmd == "view":
        level = order.args[0].lower() if order.args else "follow"
        state.view = {"level": level}
        return Accepted(order, 0, 0, f"grid view: {level}")
    return Rejected(order, f"{cmd} is not an engine-side immediate command (OV-02b)")
