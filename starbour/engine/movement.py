"""Movement rules - impulse against pull, persistent moves, the warp curve and reach (pure).

MV-01...05, CMD-06, CMD-07, HZ-10, CFG-22, CFG-23, D-73, D-98, D-101.  No rich, no zmq, no
state object.  The tab 9 warp calculator rows are the test vectors (tests/test_movement.py):
1 -> 1.00, 2 -> 2.009, 3 -> 3.068, 5 -> 5.946, 8 -> 24.91, 9.9 -> 142; 10 -> anywhere.
"""
from __future__ import annotations

import math

from starbour.world.grid import Coord

EPS = 1e-9
# MV-02: reach = w x B^((w/10)^p); the 10 is the curve's own scale and w >= 10 reaches any sector.
# [movement] warp_max (CFG-22) caps the warp core at the same value; this constant is the formula's.
WARP_ANYWHERE = 10.0


def sign(n: int) -> int:
    return (n > 0) - (n < 0)


def signed(v: int) -> str:
    """A relative move argument: +dx / -dx (D-73: a signed number is relative)."""
    return f"{int(v):+d}"


def impulse_cells(speed: float, pull: float = 0.0) -> int:
    """MV-01 / SH-04 / D-44: cells moved this turn = floor(speed - pull at departure), never negative.

    2.5 -> 2 cells; speed 4 against pull 3.85 -> 0 (held by gravity, HZ-04).
    """
    return max(0, math.floor(float(speed) - float(pull) + EPS))


def step_towards(cell: Coord, target: Coord, cells: int) -> Coord:
    """Walk up to ``cells`` king's-move steps from ``cell`` towards ``target`` (MV-01, D-73)."""
    x, y = cell
    for _ in range(max(0, int(cells))):
        if (x, y) == tuple(target):
            break
        x += sign(target[0] - x)
        y += sign(target[1] - y)
    return x, y


def remaining(cell: Coord, target: Coord) -> Coord:
    """D-98: the vector still to travel after this turn's step; (0, 0) = arrived."""
    return target[0] - cell[0], target[1] - cell[1]


def clamp_target(target: Coord, size: Coord) -> Coord:
    w, h = size
    return min(max(int(target[0]), 0), w - 1), min(max(int(target[1]), 0), h - 1)


# --------------------------------------------------------------------------- warp (MV-02, MV-03, MV-05)

def flat_sector(galaxy: Coord, sector: Coord, galaxy_size: Coord) -> Coord:
    """MV-02: flattened sector coordinates (gx x galaxy_w + sx, gy x galaxy_h + sy)."""
    return int(galaxy[0]) * int(galaxy_size[0]) + int(sector[0]), int(galaxy[1]) * int(galaxy_size[1]) + int(sector[1])


def warp_distance(galaxy_a: Coord, sector_a: Coord, galaxy_b: Coord, sector_b: Coord, galaxy_size: Coord) -> int:
    """MV-02: Chebyshev distance between flattened sector coordinates; Void in between is ignored (M2 section 5)."""
    a, b = flat_sector(galaxy_a, sector_a, galaxy_size), flat_sector(galaxy_b, sector_b, galaxy_size)
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def warp_reach(factor: float, base: float, exponent: float) -> float:
    """MV-02 / CFG-23: reach (sectors) = w x base^((w/10)^exponent); w >= 10 -> anywhere (inf)."""
    w = float(factor)
    if w >= WARP_ANYWHERE - EPS:
        return math.inf
    if w <= 0:
        return 0.0
    return w * float(base) ** ((w / WARP_ANYWHERE) ** float(exponent))


def effective_factor(factor: float, pull: float) -> float:
    """MV-03 / HZ-10: effective = factor - pull at the departure cell; <= 0 means the warp fails."""
    return float(factor) - float(pull)


def within_reach(distance: int, factor: float, pull: float, base: float, exponent: float) -> tuple[bool, float, float]:
    """(ok, effective, reach) for a warp of ``distance`` sectors at ``factor`` under ``pull``.

    ok is False when the effective factor is <= 0 (reach 0) or the distance exceeds the reach.
    """
    eff = effective_factor(factor, pull)
    reach = warp_reach(eff, base, exponent) if eff > 0 else 0.0
    return (eff > 0 and float(distance) <= reach + EPS), eff, reach


def sector_centre(size: Coord) -> tuple[float, float]:
    """MV-05: the geometric centre of a w x h sector, (11.5, 11.5) for 24 x 24."""
    return (int(size[0]) - 1) / 2.0, (int(size[1]) - 1) / 2.0


def universe_diameter(universe_size: Coord, galaxy_size: Coord) -> float:
    """MV-02 note: the flattened grid's diagonal in sectors, about 181 at 8 x 8 x 16 x 16."""
    w, h = int(universe_size[0]) * int(galaxy_size[0]), int(universe_size[1]) * int(galaxy_size[1])
    return math.hypot(w, h)
