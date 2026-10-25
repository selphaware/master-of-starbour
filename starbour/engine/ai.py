"""AI captains' decisions - OV-10, SP-06, D-103.  M2: a hazard-aware random walk.

``decide`` is a pure function of (captain, state, rng, cfg); it draws only from the
engine's game RNG (D-67) and never from the global ``random``.  An AI move never ends on
a star, hole or planet cell; inside a hole's pull range the ship moves directly away at
full engine power; every move is planned within one turn's reach so it completes this
turn and never persists (D-103, D-111).  No AI warp yet.
"""
from __future__ import annotations

import random

from starbour.commands.parse import QUEUED, Order
from starbour.config import GameConfig
from starbour.engine import hazards as H
from starbour.engine import movement as MV
from starbour.engine.state import GameState
from starbour.entities.captain import Captain
from starbour.world.grid import Coord
from starbour.world.stellar import HOLE_KINDS

TRIES = 8


def away_target(cell: Coord, hole: Coord, cells: int, size: Coord, blocked: set[Coord]) -> Coord | None:
    """D-103: the free cell up to ``cells`` steps directly away from ``hole`` (sign per axis, clipped
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


def decide(captain: Captain, state: GameState, rng: random.Random, cfg: GameConfig) -> list[Order]:
    """One ``move`` per owned ship at full engine power, hazard-aware (D-103).

    A ship held by gravity (floor(engine - pull) <= 0, HZ-04) gets no order: nothing it
    can do by impulse would move it."""
    orders: list[Order] = []
    size = cfg.sector_size
    for sid in captain.ships:
        ship = state.ships.get(sid)
        if ship is None:
            continue
        terrain = state.terrain_of(ship)
        blocked = {(int(t.cell[0]), int(t.cell[1])) for t in terrain}
        hz = H.strongest(terrain, ship.cell, cfg)
        cells = MV.impulse_cells(ship.engine, hz.pull if hz is not None else 0.0)
        if cells <= 0:
            continue
        if hz is not None and hz.kind in HOLE_KINDS:
            target = away_target(ship.cell, hz.cell, cells, size, blocked)
        else:
            target = random_target(ship.cell, cells, size, blocked, rng)
        if target is None:
            continue
        orders.append(Order("move", (str(target[0]), str(target[1]), f"{ship.engine:g}"), sid, QUEUED))
    return orders
