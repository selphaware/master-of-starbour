"""AI captains' decisions - OV-10, SP-06, CM-10, EF-01, EF-02, CFG-29/30/44/72/73, D-103, D-125.

``decide`` is a pure function of (captain, state, rng, cfg); it draws only from the
engine's game RNG (D-67) and never from the global ``random``.  Per ship, in this order:

* in a battle (D-128: it holds its cell unless escaping): when weaker, surrender > escape >
  accept a standing offer > offer peace (EF-02); then fire both weapons at the nearest enemy
  in range (D-120);
* not passive and an enemy in weapon range with ratio >= attack_ratio_min and fear <
  attack_fear_max: open fire (CM-10, EF-01);
* every roam_every_turns turns the active ship warps to a random reachable sector that holds
  no black hole or neutron star (CFG-73; 0 disables);
* otherwise the M2 hazard-aware random walk: never ends on a star, hole or planet cell; inside a
  hole's pull range the ship moves directly away at full power; every move is planned within one
  turn's reach so it never persists (D-103, D-111).

``--ai passive`` (D-125, D-132) removes every initiative - no opening, no roaming, no wandering -
so a spawned opponent stays where the gate put it; it still fires back, offers peace, escapes,
surrenders and flees a hole.
"""
from __future__ import annotations

import random

from starbour.commands.parse import QUEUED, Order
from starbour.config import GameConfig
from starbour.engine import combat as C
from starbour.engine import hazards as H
from starbour.engine import movement as MV
from starbour.engine.state import GameState
from starbour.entities.battle import Battle
from starbour.entities.captain import Captain
from starbour.entities.ship import Ship
from starbour.world.generate import flat_sector
from starbour.world.grid import Coord, chebyshev
from starbour.world.stellar import HOLE_KINDS

TRIES = 8


def away_target(cell: Coord, hole: Coord, cells: int, size: Coord, blocked: set[Coord]) -> Coord | None:
    """D-103 / EF-02: the free cell up to ``cells`` steps directly away from ``hole`` (sign per axis, clipped
    to the sector).  Shortening the step never brings the ship closer (Chebyshev), so the
    distance to the hole is never reduced; None when every candidate is blocked."""
    sx, sy = MV.sign(cell[0] - hole[0]), MV.sign(cell[1] - hole[1])
    if sx == 0 and sy == 0:
        sx = 1
    for n in range(int(cells), 0, -1):
        target = MV.clamp_target((cell[0] + sx * n, cell[1] + sy * n), size)
        if target != tuple(cell) and target not in blocked:
            return target
    return None


def random_target(cell: Coord, cells: int, size: Coord, blocked: set[Coord], rng: random.Random) -> Coord | None:
    """OV-10: a random free cell within this turn's reach (Chebyshev <= cells), never the current cell."""
    w, h = size
    x0, x1 = max(0, cell[0] - cells), min(w - 1, cell[0] + cells)
    y0, y1 = max(0, cell[1] - cells), min(h - 1, cell[1] + cells)
    for _ in range(TRIES):
        target = (rng.randint(x0, x1), rng.randint(y0, y1))
        if target != tuple(cell) and target not in blocked:
            return target
    return None


def captain_index(captain_id: str) -> int:
    try:
        return int(captain_id.split("-")[-1])
    except ValueError:
        return 0


def roam_due(captain: Captain, turn_no: int, cfg: GameConfig) -> bool:
    """CFG-73 / D-132: every roam_every_turns turns from turn roam_every_turns on, staggered by captain so the universe does
    not jump all at once (captain c-NNN roams at the turns t >= every with (t + NNN) % every == 0); 0 = never."""
    every = int(cfg.roam_every_turns)
    return every > 0 and int(turn_no) >= every and (int(turn_no) + captain_index(captain.id)) % every == 0


def roam_destination(ship: Ship, state: GameState, rng: random.Random, cfg: GameConfig, pull: float) -> tuple[Coord, Coord] | None:
    """D-125: a random sector within the warp reach (MV-02, netted by the pull here, HZ-10) that holds no hole.

    Candidates are read from the world's flattened index inside the reach box, in a fixed order, so the
    draw is deterministic (D-67).  None when the pull nets the factor to nothing or nothing is reachable."""
    eff = MV.effective_factor(ship.warp, pull)
    if eff <= 0:
        return None
    reach = MV.warp_reach(eff, cfg.warp_curve_base, cfg.warp_curve_exponent)
    uw, uh = cfg.universe_size
    gw, gh = cfg.galaxy_size
    cap = max(uw * gw, uh * gh)
    r = int(min(reach + MV.EPS, cap))
    fx, fy = flat_sector(ship.galaxy, ship.sector, cfg.galaxy_size)
    here = (ship.galaxy, ship.sector)
    candidates: list[tuple[Coord, Coord]] = []
    for y in range(fy - r, fy + r + 1):
        for x in range(fx - r, fx + r + 1):
            gs = state.world.flat_sectors.get((x, y))
            if gs is None or gs == here:
                continue
            sector = state.world.sectors.get(gs)
            if sector is None or sector.has_hole():
                continue
            candidates.append(gs)
    if not candidates:
        return None
    return candidates[rng.randrange(len(candidates))]


def fire_orders(ship: Ship, target: Ship, cfg: GameConfig) -> list[Order]:
    """D-120: both weapons at one target - one torpedo while there is ammo and it is in range, one phaser volley in range."""
    d = chebyshev(ship.cell, target.cell)
    out: list[Order] = []
    if ship.torpedoes > 0 and d <= cfg.torpedo_range:
        out.append(Order("fire", ("torpedo", target.id, "1"), ship.id, QUEUED))
    if ship.phasers > 0 and d <= cfg.phaser_range:
        out.append(Order("fire", ("phaser", target.id), ship.id, QUEUED))
    return out


def battle_orders(captain: Captain, ship: Ship, battle: Battle, here: list[Ship], state: GameState, cfg: GameConfig,
                  cells: int, blocked: set[Coord]) -> list[Order]:
    """EF-02 / CM-10 / D-125 / D-128: an AI ship in battle holds its cell and fires; when weaker it yields in the order
    surrender > escape > accept a standing offer > offer peace."""
    enemy_id = battle.other(captain.id)
    enemy = state.captains.get(enemy_id)
    enemies = sorted((s for s in here if s.captain_id == enemy_id), key=lambda s: (chebyshev(s.cell, ship.cell), s.id))
    if enemy is None or not enemies:
        return []          # the battle ends by escape at TP-04 (D-119)
    nearest = enemies[0]
    own = C.strength([s for s in here if s.captain_id == captain.id], cfg)
    theirs = C.strength(enemies, cfg)
    weaker = own < theirs
    pct = ship.shield_pct
    out: list[Order] = []
    if weaker:
        if enemy.is_player and enemy.fear >= cfg.yield_fear_min and pct < cfg.surrender_shield_pct:
            return [Order(C.SURRENDER, (enemy_id,), ship.id, QUEUED)]
        if pct < cfg.escape_shield_pct and cells > 0:
            target = away_target(ship.cell, nearest.cell, cells, cfg.sector_size, blocked)
            if target is not None:
                return [Order("move", (str(target[0]), str(target[1]), f"{ship.engine:g}"), ship.id, QUEUED)]
        # a ship that cannot flee (held, immobile or boxed in) falls through to peace (D-128: surrender > escape > peace)
        if battle.offer_stands(enemy_id, state.turn_no):
            return [Order("peace", ("accept", enemy_id), ship.id, QUEUED)]
        if pct < C.ai_peace_threshold(enemy.fear, cfg) and not battle.offer_stands(captain.id, state.turn_no):
            out.append(Order("peace", ("offer", enemy_id), ship.id, QUEUED))
    out += fire_orders(ship, nearest, cfg)
    return out


def attack_orders(captain: Captain, ship: Ship, here: list[Ship], state: GameState, cfg: GameConfig) -> list[Order]:
    """CM-10 / EF-01: open fire on the nearest enemy in weapon range whose captain's fear is below attack_fear_max and
    against whom the strength ratio (fleet in sector) is at least attack_ratio_min."""
    reach = C.weapon_range(ship, cfg)
    if reach <= 0:
        return []
    own = C.strength([s for s in here if s.captain_id == captain.id], cfg)
    best: tuple[int, str, Ship] | None = None
    for s in here:
        if s.captain_id == captain.id:
            continue
        d = chebyshev(s.cell, ship.cell)
        if d > reach:
            continue
        tcap = state.captains.get(s.captain_id)
        if tcap is None or tcap.fear >= cfg.attack_fear_max:
            continue
        theirs = C.strength([x for x in here if x.captain_id == s.captain_id], cfg)
        ratio = own / theirs if theirs > 0 else float("inf")
        if ratio < cfg.attack_ratio_min:
            continue
        key = (d, s.id, s)
        if best is None or key[:2] < best[:2]:
            best = key
    if best is None:
        return []
    return fire_orders(ship, best[2], cfg)


def decide(captain: Captain, state: GameState, rng: random.Random, cfg: GameConfig, *, passive: bool = False,
           index: dict[tuple[Coord, Coord], list[Ship]] | None = None) -> list[Order]:
    """The captain's orders for this turn (see the module docstring).  ``index`` is ``state.ships_by_sector()``,
    built once per turn by the caller; ``passive`` is the ``--ai passive`` debug mode (never opens a battle)."""
    if index is None:
        index = state.ships_by_sector()
    orders: list[Order] = []
    size = cfg.sector_size
    for sid in list(captain.ships):
        ship = state.ships.get(sid)
        if ship is None:
            continue
        terrain = state.terrain_of(ship)
        blocked = {(int(t.cell[0]), int(t.cell[1])) for t in terrain}
        hz = H.strongest(terrain, ship.cell, cfg)
        pull = hz.pull if hz is not None else 0.0
        cells = MV.impulse_cells(ship.engine, pull)
        here = index.get((ship.galaxy, ship.sector), [])
        battle = state.battle_of_ship(ship)
        if battle is not None:
            orders += battle_orders(captain, ship, battle, here, state, cfg, cells, blocked)
            continue
        if not passive:
            attack = attack_orders(captain, ship, here, state, cfg)
            if attack:
                orders += attack
                continue
            if sid == captain.active_ship and roam_due(captain, state.turn_no, cfg):
                dest = roam_destination(ship, state, rng, cfg, pull)
                if dest is not None:
                    (gx, gy), (sx, sy) = dest
                    orders.append(Order("warp", (str(gx), str(gy), str(sx), str(sy)), sid, QUEUED))
                    continue
        if cells <= 0:
            continue
        if hz is not None and hz.kind in HOLE_KINDS:
            target = away_target(ship.cell, hz.cell, cells, size, blocked)
        elif passive:
            continue       # --ai passive (D-125): no initiative at all - no opening, no roaming, no wandering; it still defends and flees a hole
        else:
            target = random_target(ship.cell, cells, size, blocked, rng)
        if target is None:
            continue
        orders.append(Order("move", (str(target[0]), str(target[1]), f"{ship.engine:g}"), sid, QUEUED))
    return orders
