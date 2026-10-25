"""Movement rules - the tab 9 warp curve rows as test vectors (MV-01...05, CFG-22, CFG-23, HZ-10, D-98)."""
from __future__ import annotations

import math

import pytest

from starbour.config import GameConfig
from starbour.engine import movement as MV


@pytest.mark.parametrize("factor, reach", [
    (1, 1.0), (2, 2.009), (3, 3.068), (4, 4.294), (5, 5.946), (6, 8.594), (7, 13.62), (8, 24.91), (9, 55.5), (9.5, 90.88), (9.9, 142.0),
])
def test_tab_9_warp_curve(cfg: GameConfig, factor, reach) -> None:
    """MV-02 / CFG-23: reach = w x 16^((w/10)^4) to the calculator's precision."""
    got = MV.warp_reach(factor, cfg.warp_curve_base, cfg.warp_curve_exponent)
    assert got == pytest.approx(reach, rel=2e-3)


def test_warp_ten_is_anywhere(cfg: GameConfig) -> None:
    """MV-02 / SH-05: w = 10 reaches any sector; the universe diameter is about 181 sectors at 8 x 8 x 16 x 16."""
    assert MV.warp_reach(10, cfg.warp_curve_base, cfg.warp_curve_exponent) == math.inf
    assert MV.warp_reach(9.99999, cfg.warp_curve_base, cfg.warp_curve_exponent) == pytest.approx(160, rel=1e-2)
    assert MV.universe_diameter(cfg.universe_size, cfg.galaxy_size) == pytest.approx(181.0, abs=0.1)
    assert MV.warp_reach(0, 16, 4) == 0.0 and MV.warp_reach(-1, 16, 4) == 0.0


def test_flattened_distance(cfg: GameConfig) -> None:
    """MV-02: Chebyshev distance on the flattened sector grid, across galaxies, ignoring Void."""
    assert MV.flat_sector((3, 4), (7, 9), cfg.galaxy_size) == (55, 73)
    assert MV.warp_distance((3, 4), (7, 9), (3, 4), (10, 9), cfg.galaxy_size) == 3
    assert MV.warp_distance((0, 0), (15, 0), (1, 0), (0, 0), cfg.galaxy_size) == 1, "adjacent sectors across a galaxy border"
    assert MV.warp_distance((0, 0), (0, 0), (7, 7), (15, 15), cfg.galaxy_size) == 127


def test_effective_factor_and_reach(cfg: GameConfig) -> None:
    """MV-03 / HZ-10: effective = factor - pull; <= 0 fails; the distance must be within reach(effective)."""
    ok, eff, reach = MV.within_reach(3, 3.0, 0.0, cfg.warp_curve_base, cfg.warp_curve_exponent)
    assert ok and eff == 3.0 and reach == pytest.approx(3.068, abs=0.005)
    ok, eff, reach = MV.within_reach(4, 3.0, 0.0, cfg.warp_curve_base, cfg.warp_curve_exponent)
    assert not ok
    ok, eff, reach = MV.within_reach(1, 3.0, 3.849, cfg.warp_curve_base, cfg.warp_curve_exponent)
    assert not ok and eff < 0 and reach == 0.0
    ok, eff, reach = MV.within_reach(1, 3.0, 2.5, cfg.warp_curve_base, cfg.warp_curve_exponent)
    assert not ok and eff == pytest.approx(0.5) and reach == pytest.approx(0.5, abs=0.01), "reach(0.5) is 0.50: 1 sector is too far"


def test_impulse_against_pull() -> None:
    """MV-01 / SH-04 / HZ-04: cells = floor(speed - pull); 4 against 3.849 is held."""
    assert MV.impulse_cells(4.0) == 4 and MV.impulse_cells(2.5) == 2 and MV.impulse_cells(0.4) == 0
    assert MV.impulse_cells(4.0, 3.849) == 0 and MV.impulse_cells(4.0, 1.789) == 2 and MV.impulse_cells(4.0, 1.08) == 2
    assert MV.impulse_cells(4.0, 0.741) == 3 and MV.impulse_cells(6.0, 2.5) == 3
    assert MV.step_towards((0, 0), (5, 2), 3) == (3, 2)
    assert MV.step_towards((4, 4), (0, 0), 10) == (0, 0)
    assert MV.remaining((0, 4), (0, 10)) == (0, 6) and MV.signed(6) == "+6" and MV.signed(-2) == "-2" and MV.signed(0) == "+0"
    assert MV.clamp_target((30, -3), (24, 24)) == (23, 0)
    assert MV.sector_centre((24, 24)) == (11.5, 11.5) and MV.sector_centre((12, 12)) == (5.5, 5.5)
