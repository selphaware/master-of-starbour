"""Hazard rules - gravity pull, countdowns, shield drain, wormhole crossing (pure).

HZ-01...10, DQ-05, ST-01...04, CFG-16...21, D-89, D-100, D-102, D-108.  Functions of a
sector's terrain, a cell and the config; no rich, no zmq, no state object.  The tab 8
pull calculator rows are the test vectors (tests/test_hazards.py): black hole d=0 -> 20,
d=1 -> 7.07, d=2 -> 3.85, d=3 -> 2.5, d=4 -> 1.79; neutron d=1 -> 2.83, d=3 -> 1.0;
turns to pulled in: hole d=2 -> 2, d=4 -> 4; neutron d=1 -> 3; Sun-like star -> 7.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from starbour.config import GameConfig
from starbour.world.grid import Coord, chebyshev
from starbour.world.stellar import BLACK_HOLE, HOLE_KINDS, NEUTRON_STAR, STAR, WORMHOLE

EPS = 1e-9
FullCell = tuple[Coord, Coord, Coord]

# C9 `destroyed[].cause` values: a wormhole never destroys, a plain black hole does (D-102)
CAUSES = {STAR: "star", BLACK_HOLE: "black_hole", WORMHOLE: "black_hole", NEUTRON_STAR: "neutron_star"}


def pull(mass: float, distance: int, k_g: float, falloff: float) -> float:
    """DQ-05 / HZ-04 / HZ-06: pull = k_g x mass / (1 + distance)^falloff (CFG-16...19)."""
    return float(k_g) * float(mass) / (1.0 + float(distance)) ** float(falloff)


def countdown_turns(pull_value: float, scale: float) -> int:
    """HZ-02 / D-100: N = ceil(pull_turns_scale / pull) turns until the ship is pulled in (CFG-20).

    The 1e-9 guard keeps 7 / 0.7 at 10, not 11 (floating point).  A zero pull never
    counts down (returns a very large N) - callers only ask about hazards in range.
    """
    if pull_value <= 0:
        return 10 ** 9
    return int(math.ceil(float(scale) / float(pull_value) - EPS))


@dataclass(frozen=True, slots=True)
class Hazard:
    """One hazard acting on a cell: the object, its distance and pull there, the drain it causes."""

    kind: str                     # star | black_hole | neutron_star | wormhole
    cell: Coord                   # the object's cell
    distance: int                 # Chebyshev distance from the ship's cell (HZ-04)
    pull: float                   # DQ-05 at that distance
    drain: int                    # shield drain per turn - stars only (HZ-03, HZ-06)
    partner: FullCell | None = None   # a wormhole's fixed exit (ST-04)

    @property
    def cause(self) -> str:
        """The C9 `destroyed[].cause` word for being pulled into this object."""
        return CAUSES[self.kind]

    def turns_left(self, counter: int, cfg: GameConfig) -> int:
        """D-100: ceil(pull_turns_scale / pull_at_current_position) - consecutive_turns_in_range."""
        return countdown_turns(self.pull, cfg.pull_turns_scale) - int(counter)

    def to_dict(self, turns_left: int) -> dict:
        """C9: {kind, cell, distance, pull, turns_left, drain}."""
        return {"kind": self.kind, "cell": [self.cell[0], self.cell[1]], "distance": self.distance,
                "pull": round(self.pull, 3), "turns_left": int(turns_left), "drain": int(self.drain)}


def in_range(kind: str, source_cell: Coord, cell: Coord) -> bool:
    """HZ-01 / HZ-02: a star acts only in its own cell; HZ-04 / HZ-06: a hole acts across its sector."""
    if kind in HOLE_KINDS:
        return True
    return tuple(source_cell) == tuple(cell)


def drain_for(kind: str, bucket: str | None, cfg: GameConfig) -> int:
    """HZ-03 / CFG-21: drain per turn by the star's energy bucket; holes drain nothing (HZ-06)."""
    if kind != STAR:
        return 0
    table = {"low": cfg.drain_low, "medium": cfg.drain_medium, "high": cfg.drain_high}
    return int(table.get(str(bucket or "").lower(), cfg.drain_medium))


def hazards_at(terrain: list, cell: Coord, cfg: GameConfig) -> list[Hazard]:
    """Every hazard whose range covers ``cell``, strongest pull first (ties: kind, cell - D-67)."""
    out: list[Hazard] = []
    for body in terrain:
        kind = getattr(body, "kind", "")
        if kind != STAR and kind not in HOLE_KINDS:
            continue
        if not in_range(kind, body.cell, cell):
            continue
        d = chebyshev(tuple(body.cell), tuple(cell))
        p = pull(float(body.mass), d, cfg.k_g, cfg.falloff)
        partner = getattr(body, "partner", None) if kind == WORMHOLE else None
        out.append(Hazard(kind, (int(body.cell[0]), int(body.cell[1])), d, p, drain_for(kind, getattr(body, "bucket", None), cfg), partner))
    out.sort(key=lambda h: (-h.pull, h.kind, h.cell))
    return out


def strongest(terrain: list, cell: Coord, cfg: GameConfig) -> Hazard | None:
    """The hazard that governs a cell.  [ASSUMED] D-108: when several hazards cover a cell
    (a star inside a hole's sector) the strongest pull drives the netting and the countdown."""
    hs = hazards_at(terrain, cell, cfg)
    return hs[0] if hs else None


def pull_at(terrain: list, cell: Coord, cfg: GameConfig) -> float:
    """The pull netted against impulse and warp at ``cell`` (MV-01, MV-03, HZ-10); 0 when clear."""
    h = strongest(terrain, cell, cfg)
    return h.pull if h is not None else 0.0


def drain_at(terrain: list, cell: Coord, cfg: GameConfig) -> int:
    """HZ-03: the shield drain this turn - every star in the ship's cell drains (D-108)."""
    return sum(h.drain for h in hazards_at(terrain, cell, cfg))


def hazard_key(galaxy: Coord, sector: Coord, hazard: Hazard) -> str:
    """Identity of a hazard's range for the consecutive-turns counter (HZ-08, D-100).

    A star's range is its cell, a hole's its whole sector: moving within the sector keeps the
    same key (the counter continues); leaving it, or entering another object's range, resets.
    """
    return f"{galaxy[0]},{galaxy[1]}/{sector[0]},{sector[1]}:{hazard.kind}:{hazard.cell[0]},{hazard.cell[1]}"


def apply_drain(shield_current: float, drain: int) -> float:
    """HZ-03 with the HZ-09 floor: shields never drop below 0."""
    return max(0.0, float(shield_current) - float(drain))


def recharge(shield_current: float, shield_max: float, amount: float) -> float:
    """CM-05 / SH-03 / CFG-27: outside any hazard and battle shields recharge, capped at shield_max."""
    return min(float(shield_max), float(shield_current) + float(amount))
