"""Hazard rules - the tab 8 pull calculator rows as test vectors (DQ-05, HZ-02...09, CFG-16...21, D-100, D-108)."""
from __future__ import annotations

import pytest

from starbour.config import GameConfig
from starbour.engine import hazards as H
from starbour.world.grid import GeneralGrid, nearest_free_cell
from starbour.world.stellar import BLACK_HOLE, NEUTRON_STAR, WORMHOLE, Hole, StarBody


def star(cell=(5, 5), mass=1.0, bucket="medium") -> StarBody:
    return StarBody(cell, mass ** (1 / 3), 5.0, mass, "yellow", bucket)


@pytest.mark.parametrize("distance, hole_pull, neutron_pull, hole_turns, neutron_turns", [
    (0, 20.0, 8.0, 1, 1),
    (1, 7.071, 2.828, 1, 3),
    (2, 3.849, 1.54, 2, 5),
    (3, 2.5, 1.0, 3, 7),
    (4, 1.789, 0.7155, 4, 10),
    (5, 1.361, 0.5443, 6, 13),
    (6, 1.08, 0.432, 7, 17),
    (12, 0.4267, 0.1707, 17, 42),
])
def test_tab_8_pull_calculator(cfg: GameConfig, distance, hole_pull, neutron_pull, hole_turns, neutron_turns) -> None:
    """DQ-05 / HZ-04 / HZ-06 / HZ-02: pull and turns-to-pulled-in rows of the blueprint calculator (2 d.p.)."""
    bh = H.pull(cfg.blackhole_mass, distance, cfg.k_g, cfg.falloff)
    ns = H.pull(cfg.neutron_mass, distance, cfg.k_g, cfg.falloff)
    assert bh == pytest.approx(hole_pull, abs=0.005) and ns == pytest.approx(neutron_pull, abs=0.005)
    assert H.countdown_turns(bh, cfg.pull_turns_scale) == hole_turns
    assert H.countdown_turns(ns, cfg.pull_turns_scale) == neutron_turns


def test_sun_like_star_is_seven_turns(cfg: GameConfig) -> None:
    """HZ-02 / D-89: a mass-1 star in its own cell pulls 1.0 and is fatal after 7 turns with the defaults."""
    assert H.pull(1.0, 0, cfg.k_g, cfg.falloff) == pytest.approx(1.0)
    assert H.countdown_turns(1.0, cfg.pull_turns_scale) == 7
    assert H.countdown_turns(0.7, 7) == 10, "the 1e-9 guard keeps 7 / 0.7 at 10"
    assert H.countdown_turns(1.0, 10) == 10, "pull_turns_scale is config (CFG-20)"


def test_range_rules(cfg: GameConfig) -> None:
    """HZ-01: a star acts only in its own cell; HZ-04 / HZ-06: holes act across the sector."""
    terrain = [star((11, 11)), Hole(BLACK_HOLE, (0, 0), cfg.blackhole_mass)]
    assert H.in_range("star", (11, 11), (11, 11)) and not H.in_range("star", (11, 11), (11, 12))
    assert H.in_range(BLACK_HOLE, (0, 0), (11, 11))
    far = H.hazards_at([star((5, 5))], (6, 5), cfg)
    assert far == []
    # D-108: the strongest pull governs when several hazards cover a cell - the star (1.0) over the hole 11 cells away (0.48)
    both = H.hazards_at(terrain, (11, 11), cfg)
    assert [h.kind for h in both] == ["star", "black_hole"] and both[0].pull >= both[1].pull
    at_star = H.strongest(terrain, (11, 11), cfg)
    assert at_star is not None and at_star.kind == "star" and at_star.distance == 0 and at_star.drain == cfg.drain_medium
    # ... and the hole over a star when the ship is 2 cells from the hole and in the star's cell (3.85 > 1.0)
    close = [star((2, 2)), Hole(BLACK_HOLE, (0, 0), cfg.blackhole_mass)]
    assert H.strongest(close, (2, 2), cfg).kind == "black_hole"
    assert H.drain_at(close, (2, 2), cfg) == cfg.drain_medium, "the star still drains in its cell (HZ-03)"
    assert H.drain_at(terrain, (3, 3), cfg) == 0
    assert H.pull_at([], (1, 1), cfg) == 0.0


def test_drain_by_bucket_and_floor(cfg: GameConfig) -> None:
    """HZ-03 / CFG-21 / HZ-09: drain by bucket; shields floor at 0; holes drain nothing (HZ-06)."""
    assert H.drain_for("star", "low", cfg) == cfg.drain_low
    assert H.drain_for("star", "medium", cfg) == cfg.drain_medium
    assert H.drain_for("star", "high", cfg) == cfg.drain_high
    assert H.drain_for(NEUTRON_STAR, None, cfg) == 0 and H.drain_for(BLACK_HOLE, None, cfg) == 0
    assert H.apply_drain(15.0, 10) == 5.0 and H.apply_drain(5.0, 10) == 0.0
    assert H.recharge(57.0, 60.0, 5) == 60.0 and H.recharge(40.0, 60.0, 5) == 45.0


def test_turns_left_and_key(cfg: GameConfig) -> None:
    """D-100: turns_left = ceil(scale / pull) - counter; the key names the range (HZ-08)."""
    hz = H.strongest([Hole(BLACK_HOLE, (0, 0), cfg.blackhole_mass)], (4, 4), cfg)
    assert hz is not None and hz.distance == 4 and hz.pull == pytest.approx(1.789, abs=0.005)
    assert hz.turns_left(1, cfg) == 3 and hz.turns_left(4, cfg) == 0
    assert H.hazard_key((2, 7), (12, 9), hz) == "2,7/12,9:black_hole:0,0"
    d = hz.to_dict(hz.turns_left(1, cfg))
    assert d == {"kind": "black_hole", "cell": [0, 0], "distance": 4, "pull": 1.789, "turns_left": 3, "drain": 0}
    assert hz.cause == "black_hole"
    worm = H.strongest([Hole(WORMHOLE, (3, 3), cfg.blackhole_mass, ((1, 1), (2, 2), (4, 4)))], (3, 4), cfg)
    assert worm is not None and worm.kind == "wormhole" and worm.partner == ((1, 1), (2, 2), (4, 4)) and worm.cause == "black_hole"


def test_nearest_free_cell() -> None:
    """MV-05 / D-102: nearest the centre (or a hole) by Chebyshev, then Manhattan, then x, y - deterministic."""
    g: GeneralGrid = GeneralGrid(24, 24)
    assert nearest_free_cell(g, (11.5, 11.5)) == (11, 11)
    g.set(11, 11, "star")
    assert nearest_free_cell(g, (11.5, 11.5)) == (11, 12), "the three remaining centre cells tie; smallest x then y"
    h: GeneralGrid = GeneralGrid(12, 12)
    h.set(8, 8, "hole")
    assert nearest_free_cell(h, (8, 8)) == (7, 8), "a neighbour of the hole, Manhattan 1, smallest x first"
    full: GeneralGrid = GeneralGrid(1, 1)
    full.set(0, 0, "x")
    assert nearest_free_cell(full, (0, 0)) is None
