"""Stellar objects and the volume -> energy -> temperature -> colour chain.

DQ-01...04, EB-01...03, SYM-04, ST-01...04.  Pure functions of their inputs; the
tab 7 calculator rows are the test vectors (tests/test_stellar.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from starbour.config import GameConfig

BUCKETS = ("low", "medium", "high")

STAR = "star"
BLACK_HOLE = "black_hole"
NEUTRON_STAR = "neutron_star"
WORMHOLE = "wormhole"
HOLE_KINDS = (BLACK_HOLE, NEUTRON_STAR, WORMHOLE)


def volume(radius: float) -> float:
    """DQ-01: volume = 4/3 * pi * radius^3."""
    return 4.0 / 3.0 * math.pi * radius ** 3


def energy(base_energy: float, radius: float) -> float:
    """DQ-02: energy = base_energy * volume."""
    return base_energy * volume(radius)


def temperature(energy_value: float, temp_scale: float) -> float:
    """DQ-03: temperature (K) = temp_scale * energy^0.25."""
    if energy_value <= 0:
        return 0.0
    return temp_scale * energy_value ** 0.25


def bucket(energy_value: float, energy_low_max: float, energy_medium_max: float) -> str:
    """EB-01...03: low below energy_low_max, medium below energy_medium_max, else high."""
    if energy_value < energy_low_max:
        return "low"
    if energy_value < energy_medium_max:
        return "medium"
    return "high"


def colour(temperature_k: float, thresholds: tuple[float, ...], names: tuple[str, ...]) -> str:
    """DQ-04 / SYM-04: the colour name whose band holds the temperature.

    ``names`` has one more entry than ``thresholds``: below the first threshold,
    between each pair, at or above the last.
    """
    if len(names) != len(thresholds) + 1:
        raise ValueError("colour_names must have one more entry than colour_thresholds (CFG-15)")
    for i, limit in enumerate(thresholds):
        if temperature_k < limit:
            return names[i]
    return names[-1]


@dataclass(frozen=True, slots=True)
class StarProfile:
    """The derived quantities of a star for one config."""

    volume: float
    energy: float
    temperature: float
    bucket: str
    colour: str


def profile(radius: float, base_energy: float, cfg: GameConfig) -> StarProfile:
    """Run the full DQ-01...04 chain for a star."""
    v = volume(radius)
    e = base_energy * v
    t = temperature(e, cfg.temp_scale)
    return StarProfile(v, e, t, bucket(e, cfg.energy_low_max, cfg.energy_medium_max),
                       colour(t, cfg.colour_thresholds, cfg.colour_names))


def star_mass(radius: float) -> float:
    """[ASSUMED] D-89: a star's mass is its volume relative to a radius-1 star, so the
    Sun-like example (radius 1) has mass 1 and the pull calculator's 7 turns (HZ-02)."""
    return volume(radius) / volume(1.0)


@dataclass(slots=True)
class StarBody:
    """A star in a sector cell - ST-01."""

    cell: tuple[int, int]
    radius: float
    base_energy: float
    mass: float
    colour: str
    bucket: str
    kind: str = STAR

    def to_dict(self) -> dict:
        return {"kind": self.kind, "cell": [self.cell[0], self.cell[1]], "radius": round(self.radius, 4),
                "base_energy": round(self.base_energy, 4), "mass": round(self.mass, 4),
                "colour": self.colour, "bucket": self.bucket}


@dataclass(slots=True)
class Hole:
    """A black hole, neutron star or wormhole - ST-02...04, HZ-04...06.

    ``partner`` is the wormhole's fixed exit as ((gx, gy), (sx, sy), (cx, cy)), set at
    seeding (SP-01, D-20); None for a plain black hole or a neutron star.
    """

    kind: str
    cell: tuple[int, int]
    mass: float
    partner: tuple[tuple[int, int], tuple[int, int], tuple[int, int]] | None = None

    def to_dict(self) -> dict:
        d: dict = {"kind": self.kind, "cell": [self.cell[0], self.cell[1]], "mass": self.mass}
        if self.partner is not None:
            d["partner"] = [list(p) for p in self.partner]
        return d
