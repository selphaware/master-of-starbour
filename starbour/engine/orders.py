"""Per-ship order queues and validation against the state - OV-02b, UX-02, UX-07, CMD-03/06/07/08/10/11/14/16/21, D-92, D-98, D-120.

Queued orders wait for ``end``; a second order of the same kind for the same ship
replaces the first (OV-02b) - a persisting move counts as the ship's queued move, so a
new ``move`` replaces it and ``stop`` cancels it (CMD-21, D-98); a torpedo and a phaser
volley are different kinds (D-120); ``escape`` is a move (D-123).  Immediate commands that
change state (ship, scan, orders cancel, stop) are applied here at once (D-92).  Pure
functions of (state, order, cfg); no sockets.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from starbour.commands.catalogue import order_kind
from starbour.commands.parse import (QUEUED, CommandError, Order, parse_escape, parse_fire, parse_move, parse_orders, parse_peace,
                                     parse_ship, parse_stop, parse_warp)
from starbour.config import GameConfig
from starbour.engine import combat as C
from starbour.engine import movement as MV
from starbour.engine.state import GameState
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


def validate(state: GameState, order: Order, cfg: GameConfig) -> str | None:
    """None when the order can be queued, else the rejection reason (CMD-06 speed and bounds; CMD-07 reach; CMD-08 range;
    CMD-10 a battle to make peace in; CMD-11 a battle to escape from)."""
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
        target = C.find_ship(state, spec.target, galaxy=ship.galaxy, sector=ship.sector)
        if target is None:
            return f"no ship {spec.target!r} in sector {ship.galaxy[0]},{ship.galaxy[1]}/{ship.sector[0]},{ship.sector[1]} - 'scan' lists them (CMD-08)"
        if target.captain_id == ship.captain_id:
            return f"the {target.name} is one of your own ships (CMD-08)"
        d = chebyshev(ship.cell, target.cell)
        rng_ = C.range_of(spec.weapon, cfg)
        if d > rng_:
            return f"target {target.id} is {d} cells away; {spec.weapon} range {rng_} (CM-02)"
        if spec.weapon == C.TORPEDO:
            if ship.torpedoes < 1:
                return f"the {ship.name} has no torpedoes left (CMD-08, SH-01)"
            if spec.count > ship.torpedoes:
                return f"the {ship.name} has only {ship.torpedoes} torpedo(es), cannot fire {spec.count} (CMD-08, SH-01)"
        elif ship.phasers <= 0:
            return f"the {ship.name} has no phasers (CMD-08, SH-02)"
        return None
    if order.command == "peace":
        try:
            spec = parse_peace(order.args)
        except CommandError as e:
            return str(e)
        cap = C.find_captain(state, spec.captain)
        if cap is None:
            return f"no captain {spec.captain!r} (CMD-10)"
        if cap.id == ship.captain_id:
            return "you cannot make peace with yourself (CMD-10)"
        if C.open_battle_between(state, ship.captain_id, cap.id) is None:
            return f"no battle with {cap.name} to make peace in (CMD-10)"
        return None
    return f"{order.command} cannot be queued yet ({order.command} arrives in a later milestone)"


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
        if target is not None:
            d = chebyshev(ship.cell, target.cell)
            what = f"{spec.count} torpedo(es)" if spec.weapon == C.TORPEDO else "phasers"
            note = f"{what} at the {target.name} ({target.id}, {d} cells) queued"
            if C.open_battle_between(state, ship.captain_id, target.captain_id) is None:
                # CM-01 / D-119: the first accepted fire opens the battle; FR-06 applies at this turn's TP-07
                bt, ev = C.open_battle(state, cfg, ship, target, state.turn_no + 1, state.pending_reputation)
                note += f" - battle {bt.id} with {state.captains[target.captain_id].name} begins; respect -{cfg.respect_start_fight_delta} (CM-01, FR-06)"
                events = (ev,)
            ship.target = target.id
    elif order.command == "peace":
        spec = parse_peace(order.args)
        cap = C.find_captain(state, spec.captain)
        note = f"peace {spec.action} {cap.name if cap else spec.captain} queued - resolves with the turn (CM-09)"
    elif order.command == "escape":
        note = "escape queued: succeeds if the move ends beyond every enemy's weapon range (CB-02, CMD-11)"
    queue = state.queues.setdefault(order.ship_id, [])
    kind = order_kind(order.command, order.args)
    for i, existing in enumerate(queue):
        if order_kind(existing.command, existing.args) == kind:
            queue[i] = order
            what = "persisting" if existing.persisting else "earlier"
            return Accepted(order, i + 1, len(queue), f"replaced the {what} {existing.command} order {existing.text()} (OV-02b)" + (f"; {note}" if note else ""), events)
    queue.append(order)
    return Accepted(order, len(queue), len(queue), note, events)


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
    """CMD-14 / OUT-10 / LV-04: items in the cell, terrain, hazard, and every other captain in the sector with fear and respect."""
    sector = state.sector_of(ship)
    found: list[str] = []
    if sector is not None:
        state.mark_visited(ship.galaxy, ship.sector)
        for item in sector.items:
            if item.cell == ship.cell:
                item.revealed = True
                found.append(f"{item.element} ({item.rarity})")
        terrain = sector.terrain_at(ship.cell)
    else:
        terrain = None
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
    text = (f"Scan: {len(found)} item(s) in this cell: {', '.join(found)}{here}{warn}" if found
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
