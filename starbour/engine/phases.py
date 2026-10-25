"""Turn resolution in the fixed phase order TP-01...08 - OV-02, D-12.

``resolve_turn(state, cfg, rng)`` mutates ``state`` in place (the engine is its only
owner, BUS-04) and returns ``(state, events)``; the turn's counters land in
``state.last_stats``.  M2 resolves orders, impulse movement netted by gravity with
persistent moves (TP-02), warp (TP-02), hazards - countdowns, drain, destruction and
wormhole crossings (TP-03), upkeep, mutiny and shield recharge (TP-06); combat, trade and
reputation are named phases that log ``skipped (M3/M4)`` so they are never silently
absent.  No I/O, no clocks.
"""
from __future__ import annotations

import random

from starbour.commands.parse import CommandError, Order, parse_move, parse_warp
from starbour.config import GameConfig
from starbour.engine import ai
from starbour.engine import hazards as H
from starbour.engine import movement as MV
from starbour.engine.orders import take_all, warp_factor
from starbour.engine.state import GameState
from starbour.entities.captain import Captain
from starbour.entities.ship import Ship
from starbour.world import names
from starbour.world.generate import flatten_cell
from starbour.world.grid import Coord, chebyshev, nearest_free_cell
from starbour.world.stellar import WORMHOLE

PHASES: tuple[tuple[str, str], ...] = (
    ("TP-01", "Orders"), ("TP-02", "Movement"), ("TP-03", "Hazards"), ("TP-04", "Combat"),
    ("TP-05", "Trade & pickup"), ("TP-06", "Upkeep"), ("TP-07", "Reputation"), ("TP-08", "Publish & render"),
)
PHASE_ORDER = {code: i for i, (code, _) in enumerate(PHASES)}
PHASE_TITLES = dict(PHASES)

SEVERITIES = ("muted", "info", "warning", "alert", "error", "highlight")

# re-exported for the M1 tests and callers (the rules live in starbour.engine.movement)
impulse_cells = MV.impulse_cells
step_towards = MV.step_towards
sign = MV.sign


def event(phase: str, type_: str, severity: str, text: str, ids: tuple[str, ...] | list[str] = ()) -> dict:
    """One MSG-03 event dict."""
    return {"phase": phase, "type": type_, "severity": severity, "text": text, "ids": list(ids)}


def kind_text(kind: str) -> str:
    return kind.replace("_", " ")


def sector_text(galaxy: Coord, sector: Coord) -> str:
    return f"{galaxy[0]},{galaxy[1]}/{sector[0]},{sector[1]}"


# --------------------------------------------------------------------------- TP-01 orders

def phase_orders(state: GameState, cfg: GameConfig, rng: random.Random, events: list[dict], stats: dict,
                 ai_player: bool) -> dict[str, list[Order]]:
    """TP-01: the player's queued orders (persisting moves included, D-98) plus every AI captain's decisions (OV-10)."""
    by_ship: dict[str, list[Order]] = take_all(state)
    player_count = sum(len(q) for q in by_ship.values())
    if ai_player and not state.game_over:
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

def _watched(state: GameState) -> tuple[Coord, Coord] | None:
    ship = state.active_ship()
    return (ship.galaxy, ship.sector) if ship else None


def resolve_impulse(state: GameState, cfg: GameConfig, ship: Ship, order: Order, events: list[dict], stats: dict,
                    visible: bool) -> bool:
    """MV-01 / CMD-06 / D-98: floor(speed - pull at departure) cells towards the target; the rest persists.

    Returns True when the ship changed cell.  A ship whose cells come to <= 0 is held by
    gravity (HZ-04) and keeps its order for next turn.
    """
    try:
        spec = parse_move(order.args)
    except CommandError as e:
        events.append(event("TP-02", "error", "error", f"{ship.name}: {e}", ["CMD-06"]))
        return False
    speed = min(spec.speed if spec.speed is not None else ship.engine, ship.engine)
    pull = state.pull_at(ship)                     # MV-03 / M2 section 5: the pull at the departure cell
    cells = MV.impulse_cells(speed, pull)
    target = MV.clamp_target(spec.target(ship.cell), cfg.sector_size)
    old = ship.cell
    if cells <= 0:
        new = old
        if visible:
            events.append(event("TP-02", "hazard", "warning",
                                f"{ship.name} is held by gravity at {old[0]},{old[1]}: pull {pull:.2f} against speed {speed:g} - "
                                f"floor({speed:g} - {pull:.2f}) = 0 cells (HZ-04, MV-01)", ["HZ-04", "MV-01", "OUT-03"]))
    else:
        new = MV.step_towards(old, target, cells)
        ship.position = (ship.galaxy, ship.sector, new)
    rx, ry = MV.remaining(new, target)
    player_owned = ship.captain_id == state.player_id
    if (rx, ry) != (0, 0) and player_owned:
        # D-98 / D-111: the move persists for the player's ships; AI moves are planned within reach (D-103)
        args: tuple[str, ...] = (MV.signed(rx), MV.signed(ry))
        if len(order.args) == 3:
            args += (order.args[2],)
        state.queues.setdefault(ship.id, []).insert(0, Order("move", args, ship.id, order.kind, persisting=True))
    if visible and cells > 0:
        netted = f", pull {pull:.2f} netted" if pull > 0 else ""
        text = f"{ship.name} moved {old[0]},{old[1]} -> {new[0]},{new[1]} (speed {speed:g}{netted}, {cells} cells)"
        if new == old:
            text = f"{ship.name} held position at {old[0]},{old[1]}"
        events.append(event("TP-02", "movement", "info", text, ["MV-01", "CMD-06", "OUT-02"]))
        if (rx, ry) != (0, 0) and player_owned:
            events.append(event("TP-02", "movement", "info",
                                f"{ship.name} continues towards {target[0]},{target[1]} next turn ({MV.signed(rx)} {MV.signed(ry)} remaining; "
                                f"'stop' cancels, a new 'move' replaces) (D-98)", ["CMD-06", "CMD-21", "D-98"]))
    return new != old


def resolve_warp(state: GameState, cfg: GameConfig, ship: Ship, order: Order, events: list[dict], stats: dict,
                 visible: bool) -> bool:
    """CMD-07 / MV-02 / MV-03 / MV-05 / HZ-10 / D-101: a single-turn warp netted by the pull at departure."""
    try:
        spec = parse_warp(order.args)
    except CommandError as e:
        events.append(event("TP-02", "error", "error", f"{ship.name}: {e}", ["CMD-07"]))
        return False
    factor = min(warp_factor(spec.factor, ship.warp), ship.warp)
    pull = state.pull_at(ship)
    dest_g, dest_s = spec.galaxy, spec.sector
    sector = state.world.sector(dest_g, dest_s)
    distance = MV.warp_distance(ship.galaxy, ship.sector, dest_g, dest_s, cfg.galaxy_size)
    ok, eff, reach = MV.within_reach(distance, factor, pull, cfg.warp_curve_base, cfg.warp_curve_exponent)
    here = sector_text(ship.galaxy, ship.sector)
    there = sector_text(dest_g, dest_s)
    if sector is None:
        events.append(event("TP-02", "error", "error", f"{ship.name}: warp to {there} impossible - no sector there (CMD-07)", ["CMD-07"]))
        return False
    if eff <= 0:
        if visible:
            events.append(event("TP-02", "hazard", "warning",
                                f"{ship.name}: warp to {there} failed - the pull of {pull:.2f} here nets factor {factor:g} to {eff:.2f} "
                                f"(HZ-10, MV-03)", ["HZ-10", "MV-03", "OUT-03"]))
        return False
    if not ok:
        if visible:
            events.append(event("TP-02", "hazard", "warning",
                                f"{ship.name}: warp to {there} failed - {distance} sectors is beyond the reach {reach:.2f} at effective "
                                f"factor {eff:.2f} (MV-02, MV-03)", ["MV-02", "MV-03"]))
        return False
    cell = nearest_free_cell(sector.grid, MV.sector_centre(cfg.sector_size))
    if cell is None:
        events.append(event("TP-02", "error", "error", f"{ship.name}: warp to {there} failed - the sector has no free cell (MV-05)", ["MV-05"]))
        return False
    old_g, old_s = ship.galaxy, ship.sector
    ship.position = (dest_g, dest_s, cell)
    ship.hazard_turns = 0
    ship.hazard_key = None
    if ship.captain_id == state.player_id:
        state.mark_visited(dest_g, dest_s)
    stats["warps"].append({"ship": ship.id, "from": [[old_g[0], old_g[1]], [old_s[0], old_s[1]]], "to": [[dest_g[0], dest_g[1]], [dest_s[0], dest_s[1]]],
                           "factor": float(factor), "effective": round(eff, 3)})
    if visible:
        netted = f", effective {eff:.2f} after pull {pull:.2f}" if pull > 0 else f", effective {eff:g}"
        events.append(event("TP-02", "warp", "info",
                            f"{ship.name} warped {here} -> {there}: {distance} sectors at factor {factor:g}{netted}; arrived at cell "
                            f"{cell[0]},{cell[1]} nearest the centre (MV-05)", ["CMD-07", "MV-02", "MV-05", "OUT-02"]))
    return True


def phase_movement(state: GameState, cfg: GameConfig, by_ship: dict[str, list[Order]], events: list[dict], stats: dict) -> None:
    """TP-02: impulse (MV-01, netted by pull) and warp (MV-02...05) for every ship with orders."""
    watched = _watched(state)
    moved_ai = 0
    for sid in sorted(by_ship):
        ship = state.ships.get(sid)
        if ship is None:
            continue
        is_player = ship.captain_id == state.player_id
        visible = is_player or (watched is not None and (ship.galaxy, ship.sector) == watched)
        orders = by_ship[sid]
        warp = next((o for o in orders if o.command == "warp"), None)
        moves = [o for o in orders if o.command == "move"]
        for o in orders:
            if o.command not in ("move", "warp"):
                events.append(event("TP-02", "system", "muted", f"{ship.name}: {o.command} orders resolve in a later milestone", ["OV-02b"]))
        moved = False
        if warp is not None:
            if moves and visible:
                events.append(event("TP-02", "movement", "muted",
                                    f"{ship.name}: the warp takes precedence over the queued move, which is dropped (D-109 [ASSUMED])", ["D-109"]))
            moved = resolve_warp(state, cfg, ship, warp, events, stats, visible)
        elif moves:
            moved = resolve_impulse(state, cfg, ship, moves[-1], events, stats, visible)
        if is_player:
            state.mark_visited(ship.galaxy, ship.sector)
            stats["player_moved"] = stats.get("player_moved", False) or moved
        elif moved:
            moved_ai += 1
    stats["ai_ships_moved"] = moved_ai
    # the phase is never silently absent (OB-01 hides AI moves elsewhere; this line says so)
    yours = "you moved" if stats.get("player_moved") else "you held position"
    events.append(event("TP-02", "movement", "muted", f"Movement: {yours}; {moved_ai} AI ship(s) moved across the universe", ["TP-02", "OB-01"]))


# --------------------------------------------------------------------------- TP-03 hazards

def destroy_ship(state: GameState, ship: Ship, hazard: H.Hazard, events: list[dict], stats: dict, visible: bool) -> None:
    """OV-09b / FL-04 / OUT-11 / OUT-16 / D-105: a ship pulled into a star, black hole or neutron star."""
    cause = hazard.cause
    cell = ship.cell
    where = f"{kind_text(cause)} at {hazard.cell[0]},{hazard.cell[1]} in sector {sector_text(ship.galaxy, ship.sector)}"
    stats["destroyed"].append({"ship": ship.id, "captain": ship.captain_id, "cause": cause, "cell": [cell[0], cell[1]]})
    lost = {"ship_class": ship.cls, "position": [[ship.galaxy[0], ship.galaxy[1]], [ship.sector[0], ship.sector[1]], [cell[0], cell[1]]],
            "shield_current": ship.shield_current, "shield_max": ship.shield_max, "hazard": hazard.to_dict(0), "name": ship.name}
    cap_name = state.captains[ship.captain_id].name if ship.captain_id in state.captains else "an unknown captain"
    cap, was_active = state.remove_ship(ship.id)
    if cap is not None and cap.is_player and was_active:
        state.game_over_turn = state.turn_no
        stats["game_over"] = {"cause": f"pulled into the {where}", "kind": cause, "cell": [cell[0], cell[1]]}
        stats["player_lost"] = lost
        events.append(event("TP-03", "destroyed", "alert",
                            f"{ship.name} destroyed: pulled into the {where} - game over at turn {state.turn_no}. Type 'new' to start again.",
                            ["OV-09b", "OUT-16", "CMD-19"]))
    elif cap is not None and cap.is_player:
        events.append(event("TP-03", "destroyed", "alert",
                            f"{ship.name} destroyed: pulled into the {where} - ship lost from your fleet", ["FL-04", "OUT-11"]))
    elif visible:
        events.append(event("TP-03", "destroyed", "alert",
                            f"{cap_name}'s {ship.name} was pulled into the {where} - ship lost", ["OUT-11", "D-105"]))
    else:
        stats["ai_lost_elsewhere"] = stats.get("ai_lost_elsewhere", 0) + 1


def cross_wormhole(state: GameState, ship: Ship, hazard: H.Hazard, events: list[dict], stats: dict, visible: bool) -> None:
    """HZ-05 / ST-04 / D-102: emerge alive at the free cell nearest the partner hole, counter reset."""
    pg, ps, pc = hazard.partner   # type: ignore[misc]
    sector = state.world.sector(pg, ps)
    cell = nearest_free_cell(sector.grid, pc) if sector is not None else None
    if cell is None:
        destroy_ship(state, ship, hazard, events, stats, visible)   # [ASSUMED] a missing exit is a plain black hole
        return
    old = sector_text(ship.galaxy, ship.sector)
    ship.position = (pg, ps, cell)
    ship.hazard_turns = 0
    ship.hazard_key = None
    if ship.captain_id == state.player_id:
        state.mark_visited(pg, ps)
    stats["wormhole_crossings"] = stats.get("wormhole_crossings", 0) + 1
    if visible or ship.captain_id == state.player_id:
        events.append(event("TP-03", "hazard", "alert",
                            f"{ship.name} was pulled into the wormhole at {hazard.cell[0]},{hazard.cell[1]} in {old} and emerged in sector "
                            f"{sector_text(pg, ps)} at cell {cell[0]},{cell[1]} - inside the partner's pull (HZ-05, D-102)",
                            ["HZ-05", "ST-04", "D-102", "OUT-03"]))


def phase_hazards(state: GameState, cfg: GameConfig, events: list[dict], stats: dict) -> None:
    """TP-03 / HZ-02...09 / D-100: counters, shield drain, destruction and wormhole crossings for every ship."""
    watched = _watched(state)
    in_range = 0
    for sid in sorted(state.ships):
        ship = state.ships.get(sid)
        if ship is None:
            continue
        terrain = state.terrain_of(ship)
        hz = H.strongest(terrain, ship.cell, cfg)
        if hz is None:
            ship.hazard_turns = 0
            ship.hazard_key = None
            continue
        in_range += 1
        key = H.hazard_key(ship.galaxy, ship.sector, hz)
        if ship.hazard_key == key:
            ship.hazard_turns += 1          # D-100: incremented at TP-03 each turn still in range after movement
        else:
            ship.hazard_key = key
            ship.hazard_turns = 1           # HZ-08: a new range starts the count afresh
        drain = H.drain_at(terrain, ship.cell, cfg)
        if drain:
            ship.shield_current = H.apply_drain(ship.shield_current, drain)
        left = hz.turns_left(ship.hazard_turns, cfg)
        is_player = ship.captain_id == state.player_id
        visible = is_player or (watched is not None and (ship.galaxy, ship.sector) == watched)
        if left <= 0:
            if hz.kind == WORMHOLE and hz.partner is not None:
                cross_wormhole(state, ship, hz, events, stats, visible)
            else:
                destroy_ship(state, ship, hz, events, stats, visible)
            continue
        if visible:
            drained = f"; shields drained by {drain} to {ship.shield_current:g}" if drain else ""
            events.append(event("TP-03", "hazard", "warning",
                                f"{ship.name} is in the pull of the {kind_text(hz.kind)} at {hz.cell[0]},{hz.cell[1]} ({hz.distance} cells): "
                                f"pull {hz.pull:.2f}, {left} turn(s) until pulled in{drained} (HZ-0{2 if hz.kind == 'star' else 4}, D-100)",
                                ["OUT-03", "HZ-02", "HZ-04", "D-100"]))
    elsewhere = stats.get("ai_lost_elsewhere", 0)
    tail = f"; {elsewhere} AI ship(s) lost to hazards elsewhere" if elsewhere else ""
    events.append(event("TP-03", "hazard", "muted",
                        f"Hazards: {in_range} ship(s) inside a pull range, {len(stats['destroyed'])} destroyed{tail}", ["TP-03", "D-105"]))


# --------------------------------------------------------------------------- TP-06 upkeep, mutiny & recharge

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
    state.queues.pop(old_ship.id, None)
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
    if cap.is_player:
        state.player_mutinies += 1
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
    if cap.zero_turns > cfg.mutiny_turns_at_zero and cap.active_ship in state.ships:
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


def phase_recharge(state: GameState, cfg: GameConfig, events: list[dict], stats: dict) -> None:
    """TP-06 / CM-05 / SH-03 / CFG-27: shields recharge outside any hazard (no battle before M3), capped at shield_max."""
    player_ship = state.active_ship()
    for sid in sorted(state.ships):
        ship = state.ships[sid]
        if ship.hazard_key is not None or ship.shield_current >= ship.shield_max:
            continue
        before = ship.shield_current
        ship.shield_current = H.recharge(ship.shield_current, ship.shield_max, cfg.shield_recharge)
        if player_ship is not None and ship.id == player_ship.id:
            events.append(event("TP-06", "shields", "info",
                                f"Shields recharge {before:g} -> {ship.shield_current:g} / {ship.shield_max:g} (+{cfg.shield_recharge:g} outside hazards, CM-05)",
                                ["CM-05", "SH-03", "CFG-27"]))


# --------------------------------------------------------------------------- the turn

def resolve_turn(state: GameState, cfg: GameConfig, rng: random.Random, *, ai_player: bool = False) -> tuple[GameState, list[dict]]:
    """Resolve one turn in the fixed order TP-01...08 (OV-02); ``state.turn_no`` increments first."""
    state.turn_no += 1
    events: list[dict] = []
    stats: dict = {"turn_no": state.turn_no, "orders_executed": {"player": 0, "ai": 0}, "ai_ships_moved": 0,
                   "mutinies": [], "player_moved": False, "warps": [], "destroyed": [], "game_over": None, "player_lost": None,
                   "wormhole_crossings": 0}
    by_ship = phase_orders(state, cfg, rng, events, stats, ai_player)
    phase_movement(state, cfg, by_ship, events, stats)
    phase_hazards(state, cfg, events, stats)
    events.append(event("TP-04", "system", "muted", "Combat: skipped (M3)", ["TP-04"]))
    events.append(event("TP-05", "system", "muted", "Trade & pickup: skipped (M4)", ["TP-05"]))
    phase_upkeep(state, cfg, rng, events, stats)
    phase_recharge(state, cfg, events, stats)
    events.append(event("TP-07", "system", "muted", "Reputation: skipped (M3)", ["TP-07"]))
    oe = stats["orders_executed"]
    events.append(event("TP-08", "summary", "info",
                        f"Turn {state.turn_no} resolved: {oe['player']} order(s) from you, {oe['ai']} from AI captains", ["OUT-01"]))
    state.last_stats = stats
    return state, events
