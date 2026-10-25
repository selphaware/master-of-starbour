"""Stellar chain - the tab 7 calculator rows as test vectors (DQ-01...04, EB-01...03, SYM-04)."""
from __future__ import annotations

import dataclasses

import pytest

from starbour.config import GameConfig
from starbour.world.stellar import bucket, colour, energy, profile, star_mass, temperature, volume


@pytest.mark.parametrize("radius, base, exp_volume, exp_energy, exp_bucket, exp_temp, exp_colour", [
    (0.3, 2, 0.1131, 0.2262, "low", 1862, "red"),          # dying star
    (1.0, 5, 4.189, 20.94, "medium", 5776, "yellow"),       # Sun-like
    (3.0, 8, 113.1, 904.8, "high", 14810, "blue-white"),    # giant
    (1.5, 6, 14.14, 84.82, "medium", 8194, "white"),        # 'your own' row
])
def test_calculator_vectors(cfg: GameConfig, radius, base, exp_volume, exp_energy, exp_bucket, exp_temp, exp_colour) -> None:
    p = profile(radius, base, cfg)
    assert p.volume == pytest.approx(exp_volume, rel=1e-3)
    assert p.energy == pytest.approx(exp_energy, rel=1e-3)
    assert p.temperature == pytest.approx(exp_temp, rel=2e-3)
    assert p.bucket == exp_bucket
    assert p.colour == exp_colour


def test_formulas() -> None:
    assert volume(1.0) == pytest.approx(4.18879, rel=1e-5)
    assert energy(5, 1.0) == pytest.approx(20.944, rel=1e-4)
    assert temperature(20.944, 2700) == pytest.approx(5776, rel=1e-3)
    assert temperature(0, 2700) == 0.0
    assert star_mass(1.0) == pytest.approx(1.0) and star_mass(2.0) == pytest.approx(8.0)


def test_bucket_thresholds_from_config(cfg: GameConfig) -> None:
    low, med = cfg.energy_low_max, cfg.energy_medium_max
    assert bucket(low - 0.01, low, med) == "low"
    assert bucket(low, low, med) == "medium"
    assert bucket(med - 0.01, low, med) == "medium"
    assert bucket(med, low, med) == "high"
    tweaked = dataclasses.replace(cfg, energy_low_max=30.0)
    assert profile(1.0, 5, tweaked).bucket == "low", "the Sun-like star becomes low when the threshold moves"


def test_colour_bands(cfg: GameConfig) -> None:
    th, names = cfg.colour_thresholds, cfg.colour_names
    assert colour(0, th, names) == "red"
    assert colour(3699, th, names) == "red" and colour(3700, th, names) == "orange"
    assert colour(5999, th, names) == "yellow" and colour(6000, th, names) == "yellow-white"
    assert colour(9999, th, names) == "white" and colour(29999, th, names) == "blue-white"
    assert colour(30000, th, names) == "blue" and colour(1e6, th, names) == "blue"
    with pytest.raises(ValueError):
        colour(100, th, names[:-1])
