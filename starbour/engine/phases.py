"""Turn resolution in the fixed phase order TP-01...08 - OV-02, D-12.

``resolve_turn(state, cfg, rng)`` mutates ``state`` in place (the engine is its only
owner, BUS-04) and returns ``(state, events)``; the turn's counters land in
``state.last_stats``.  M1 resolves orders, impulse movement (pull = 0), upkeep and
mutiny; hazards, combat, trade and reputation are named phases that log
``skipped (M2/M3/M4)`` so they are never silently absent.  No I/O, no clocks.
"""
from __future__ import annotations

import math
import random

from starbour.commands.parse import CommandError, Order, parse_move
from starbour.config import GameConfig
from starbour.engine import ai
from starbour.engine.orders import take_all
from starbour.engine.state import GameState
from starbour.entities.captain import Captain
from starbour.entities.ship import Ship
from starbour.world import names
from starbour.world.generate import flatten_cell
from starbour.world.grid import Coord, chebyshev

PHASES: tuple[tuple[str, str], ...] = (
    ("TP-01", "Orders"), ("TP-02", "Movement"), ("TP-03", "Hazards"), ("TP-04", "Combat"),
    ("TP-05", "Trade & pickup"), ("TP-06", "Upkeep"), ("TP-07", "Reputation"), ("TP-08", "Publish & render"),
)
PHASE_ORDER = {code: i for i, (code, _) in enumerate(PHASES)}
PHASE_TITLES = dict(PHASES)

SEVERITIES = ("muted", "info", "warning", "alert", "error", "highlight")


def event(phase: str, type_: str, severity: str, text: str, ids: tuple[str, ...] | list[str] = ()) -> dict:
    """One MSG-03 event dict."""
    return {"phase": phase, "type": type_, "severity": severity, "text": text, "ids": list(ids)}


def sign(n: int) -> int:
    return (n > 0) - (n < 0)


def step_towards(cell: Coord, target: Coord, cells: int) -> Coord:
    """Walk up to ``cells`` king's-move steps from ``cell`` towards ``target`` (MV-01, D-73)."""
    x, y = cell
    for _ in range(max(0, cells)):
        if (x, y) == target:
            break
        x += sign(target[0] - x)
        y += sign(target[1] - y)
    return x, y


def impulse_cells(speed: float, pull: float = 0.0) -> int:
    """MV-01 / D-44: cells moved this turn = floor(speed - pull), never negative."""
    return max(0, math.floor(speed - pull + 1e-9))


# --------------------------------------------------------------------------- TP-01 orders

def phase_orders(state: GameState, cfg: GameConfig, rng: random.Random, events: list[dict], stats: dict,
                 ai_player: bool) -> dict[str, list[Order]]:
    """TP-01: the player's queued orders plus every AI captain's decisions (OV-10)."""
    by_ship: dict[str, list[Order]] = take_all(state)
    player_count = sum(len(q) for q in by_ship.values())
    if ai_player:
        for order in ai.decide(state.player, state, rng, cfg):
            if order.ship_id not in by_ship:
                by_ship[order.ship_id] = [order]
                player_count += 1
    ai_count = 0
    for cid in sorted(state.captains):
        cap = state.captains[cid]
        if cap.is_player:
            continue
        for order in ai.decide(cap, state, rng, cfg):
            by_ship.setdefault(order.ship_id, []).append(order)
            ai_count += 1
    stats["orders_executed"] = {"player": player_count, "ai": ai_count}
    events.append(event("TP-01", "orders", "muted", f"Orders: {player_count} from you, {ai_count} from AI captains", ["TP-01"]))
    return by_ship


# --------------------------------------------------------------------------- TP-02 movement

def phase_movement(state: GameState, cfg: GameConfig, by_ship: dict[str, list[Order]], events: list[dict], stats: dict) -> None:
    """TP-02 / MV-01 / CMD-06 / D-73: impulse moves, clipped to floor(speed) cells along the Chebyshev path."""
    player_ship = state.active_ship()
    watched = (player_ship.galaxy, player_ship.sector) if player_ship else None
    moved_ai = 0
    w, h = cfg.sector_size
    for sid in sorted(by_ship):
        ship = state.ships.get(sid)
        if ship is None:
            continue
        for order in by_ship[sid]:
            if order.command != "move":
                events.append(event("TP-02", "system", "muted", f"{ship.name}: {order.command} orders resolve in a later milestone", ["OV-02b"]))
                continue
            try:
                spec = parse_move(order.args)
            except CommandError as e:
                events.append(event("TP-02", "error", "error", f"{ship.name}: {e}", ["CMD-06"]))
                continue
            speed = min(spec.speed if spec.speed is not None else ship.engine, ship.engine)
            pull = 0.0   # gravity arrives in M2 (MV-01, DQ-05)
            cells = impulse_cells(speed, pull)
            tx, ty = spec.target(ship.cell)
            target = (min(max(tx, 0), w - 1), min(max(ty, 0), h - 1))
            old = ship.cell
            new = step_towards(old, target, cells)
            ship.position = (ship.galaxy, ship.sector, new)
            is_player = ship.captain_id == state.player_id
            moved = new != old
            if is_player:
                state.mark_visited(ship.galaxy, ship.sector)
                stats["player_moved"] = stats.get("player_moved", False) or moved
            elif moved:
                moved_ai += 1
            if is_player or (watched is not None and (ship.galaxy, ship.sector) == watched):
                text = f"{ship.name} moved {old[0]},{old[1]} -> {new[0]},{new[1]} (speed {speed:g}, {cells} cells)"
                if not moved:
                    text = f"{ship.name} held position at {old[0]},{old[1]}"
                events.append(event("TP-02", "movement", "info", text, ["MV-01", "CMD-06", "OUT-02"]))
                if new != target and is_player:
                    events.append(event("TP-02", "movement", "info",
                                        f"{ship.name} is short of {target[0]},{target[1]} - re-issue the move next turn (D-73)", ["D-73"]))
    stats["ai_ships_moved"] = moved_ai
    # the phase is never silently absent (OB-01 hides AI moves elsewhere; this line says so)
    yours = "you moved" if stats.get("player_moved") else "you held position"
    events.append(event("TP-02", "movement", "muted", f"Movement: {yours}; {moved_ai} AI ship(s) moved across the universe", ["TP-02", "OB-01"]))


# --------------------------------------------------------------------------- TP-06 upkeep & mutiny

def mutiny(state: GameState, cap: Captain, cfg: GameConfig, rng: random.Random, stats: dict) -> list[dict]:
    """SUP-03, SUP-04, SUP-04b, SUP-04c, D-47, D-51, D-52, D-72: the crew take the active ship."""
    out: list[dict] = []
    old_ship = state.ships[cap.active_ship]
    others = [s for s in cap.ships if s != old_ship.id and s in state.ships]
    cap.ships = [s for s in cap.ships if s != old_ship.id]
    # the mutineers keep the ship and become a new AI captain (SUP-04c, D-52)
    mid = state.new_captain_id()
    mutineers = Captain.new(mid, names.captain_name(rng), False, cfg, gold=0, silver=0)   # [ASSUMED] D-93: no coins
    mutineers.ships = [old_ship.id]
    mutineers.active_ship = old_ship.id
    old_ship.captain_id = mid
    state.captains[mid] = mutineers
    if others:
        here = flatten_cell(old_ship.position, cfg)
        nearest = min(others, key=lambda s: (chebyshev(flatten_cell(state.ships[s].position, cfg), here), s))
        cap.active_ship = nearest
        result, where = "moved", f"moves to the {state.ships[nearest].name}"
    else:
        spec = cfg.ship_class(cfg.mutiny_ship_class)
        sid = state.new_ship_id()
        new_ship = Ship.of_class(sid, names.ship_name(rng), spec, cap.id, old_ship.position)
        state.ships[sid] = new_ship
        cap.ships.append(sid)
        cap.active_ship = sid
        result, where = "demoted", f"is demoted to a {spec.name.capitalize()}-class ship, the {new_ship.name}"
    fear_before = cap.fear
    cap.food = cap.water = cfg.mutiny_supplies
    cap.zero_turns = 0
    cap.fear = max(0, cap.fear - cfg.mutiny_fear_delta)
    out.append(event("TP-06", "mutiny", "alert",
                     f"Mutiny! The crew seize the {old_ship.name}; {cap.name} {where} with {cfg.mutiny_supplies} food and "
                     f"{cfg.mutiny_supplies} water; fear {fear_before} -> {cap.fear}",
                     ["SUP-03", "SUP-04" if others else "SUP-04b", "OUT-18"]))
    out.append(event("TP-06", "mutiny", "info", f"The mutineers under {mutineers.name} now captain the {old_ship.name}", ["SUP-04c"]))
    stats.setdefault("mutinies", []).append({"turn_no": state.turn_no, "captain": "player" if cap.is_player else cap.id, "result": result})
    return out


def phase_upkeep(state: GameState, cfg: GameConfig, rng: random.Random, events: list[dict], stats: dict) -> None:
    """TP-06 / SUP-01 / SUP-02 / D-60 / D-76: player-only consumption, warning, zero counter, mutiny."""
    cap = state.player
    cap.food = max(0, cap.food - 1)
    cap.water = max(0, cap.water - 1)
    events.append(event("TP-06", "upkeep", "info", f"Upkeep: food {cap.food}, water {cap.water}", ["SUP-01", "CP-07"]))
    if cap.food == 0 or cap.water == 0:
        cap.zero_turns += 1
    else:
        cap.zero_turns = 0
    if cap.zero_turns > cfg.mutiny_turns_at_zero:
        events.extend(mutiny(state, cap, cfg, rng, stats))
        return
    low = min(cap.food, cap.water)
    if low <= cfg.warn_turns:
        if low > 0:
            text = f"Supplies warning: food {cap.food}, water {cap.water} - {low} turn(s) before they run out"
        else:
            text = (f"Supplies warning: food {cap.food}, water {cap.water} - the crew mutiny in "
                    f"{cap.mutiny_countdown(cfg)} turn(s) unless you buy supplies")
        events.append(event("TP-06", "supplies", "warning", text, ["SUP-02", "OUT-17"]))


# --------------------------------------------------------------------------- the turn

def resolve_turn(state: GameState, cfg: GameConfig, rng: random.Random, *, ai_player: bool = False) -> tuple[GameState, list[dict]]:
    """Resolve one turn in the fixed order TP-01...08 (OV-02); ``state.turn_no`` increments first."""
    state.turn_no += 1
    events: list[dict] = []
    stats: dict = {"turn_no": state.turn_no, "orders_executed": {"player": 0, "ai": 0}, "ai_ships_moved": 0,
                   "mutinies": [], "player_moved": False}
    by_ship = phase_orders(state, cfg, rng, events, stats, ai_player)
    phase_movement(state, cfg, by_ship, events, stats)
    events.append(event("TP-03", "system", "muted", "Hazards: skipped (M2)", ["TP-03"]))
    events.append(event("TP-04", "system", "muted", "Combat: skipped (M3)", ["TP-04"]))
    events.append(event("TP-05", "system", "muted", "Trade & pickup: skipped (M4)", ["TP-05"]))
    phase_upkeep(state, cfg, rng, events, stats)
    events.append(event("TP-07", "system", "muted", "Reputation: skipped (M3)", ["TP-07"]))
    oe = stats["orders_executed"]
    events.append(event("TP-08", "summary", "info",
                        f"Turn {state.turn_no} resolved: {oe['player']} order(s) from you, {oe['ai']} from AI captains", ["OUT-01"]))
    state.last_stats = stats
    return state, events
