"""Per-ship order queues and validation against the state - OV-02b, UX-02, UX-07, CMD-03/06/14/16, D-92.

Queued orders wait for ``end``; a second order of the same kind for the same ship
replaces the first (OV-02b).  Immediate commands that change state (ship, scan,
orders cancel) are applied here at once (D-92).  Pure functions of (state, order, cfg);
no sockets.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from starbour.commands.parse import QUEUED, CommandError, Order, parse_move, parse_orders, parse_ship
from starbour.config import GameConfig
from starbour.engine.state import GameState


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


def validate(state: GameState, order: Order, cfg: GameConfig) -> str | None:
    """None when the order can be queued, else the rejection reason (CMD-06: speed and bounds)."""
    bad = _own_ship(state, order)
    if bad:
        return bad
    ship = state.ships[order.ship_id]
    if order.command == "move":
        try:
            spec = parse_move(order.args)
        except CommandError as e:
            return str(e)
        speed = spec.speed if spec.speed is not None else ship.engine
        if speed > ship.engine + 1e-9:
            return f"speed {speed:g} exceeds engine_power {ship.engine:g} (CMD-06)"
        if speed > cfg.impulse_max + 1e-9:
            return f"speed {speed:g} exceeds impulse_max {cfg.impulse_max:g} (CFG-22, CMD-06)"
        tx, ty = spec.target(ship.cell)
        w, h = cfg.sector_size
        if not (0 <= tx < w and 0 <= ty < h):
            return f"target {tx},{ty} is outside the sector {w}x{h} (CMD-06)"
        return None
    return f"{order.command} cannot be queued yet ({order.command} arrives in a later milestone)"


def submit(state: GameState, order: Order, cfg: GameConfig) -> Accepted | Rejected:
    """Queue a validated order; same-kind replaces (OV-02b)."""
    if order.kind != QUEUED:
        return Rejected(order, f"{order.command} is an immediate command, not a queued order (OV-02b)")
    reason = validate(state, order, cfg)
    if reason:
        return Rejected(order, reason)
    queue = state.queues.setdefault(order.ship_id, [])
    for i, existing in enumerate(queue):
        if existing.command == order.command:
            queue[i] = order
            return Accepted(order, i + 1, len(queue), f"replaced the earlier {order.command} order (OV-02b)")
    queue.append(order)
    return Accepted(order, len(queue), len(queue))


def cancel(state: GameState, ship_id: str, n: int) -> Order | None:
    """CMD-16 ``orders cancel n`` (1-based)."""
    queue = state.queues.get(ship_id, [])
    if 1 <= n <= len(queue):
        return queue.pop(n - 1)
    return None


def take_all(state: GameState) -> dict[str, list[Order]]:
    """Remove and return every queue, keyed by ship id in sorted order (TP-01)."""
    out = {sid: list(state.queues[sid]) for sid in sorted(state.queues) if state.queues[sid]}
    state.queues.clear()
    return out


def clear(state: GameState) -> None:
    state.queues.clear()


def _find_ship(state: GameState, text: str) -> str | None:
    """CMD-03: by fleet number (1-based) or by name, case-insensitively."""
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


def apply_immediate(state: GameState, order: Order, cfg: GameConfig) -> Accepted | Rejected:
    """Immediate commands the engine applies at once (D-92): ship, scan, orders [cancel n], view."""
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
        text = (f"Scan: {len(found)} item(s) in this cell: {', '.join(found)}{here}" if found
                else f"Scan: nothing to pick up in cell {ship.cell[0]},{ship.cell[1]}{here}")
        return Accepted(order, 0, pending_count(state, ship.id), text,
                        ({"phase": "TP-01", "type": "scan", "severity": "info", "text": text, "ids": ["CMD-14", "OUT-10"]},))
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
            text = "no orders queued" if not queue else "; ".join(f"{i + 1}. {o.text()}" for i, o in enumerate(queue))
            return Accepted(order, 0, len(queue), text)
        removed = cancel(state, order.ship_id, n)
        if removed is None:
            return Rejected(order, f"there is no queued order number {n} (CMD-16)")
        return Accepted(order, 0, pending_count(state, order.ship_id), f"cancelled order {n}: {removed.text()}")
    if cmd == "view":
        level = order.args[0].lower() if order.args else "follow"
        state.view = {"level": level}
        return Accepted(order, 0, 0, f"grid view: {level}")
    return Rejected(order, f"{cmd} is not an engine-side immediate command (OV-02b)")
