"""Fear and respect - FR-01...10, LV-01...05, CFG-01...11, D-121 (specs/M3.md section 5 vectors)."""
from __future__ import annotations

from starbour.config import GameConfig
from starbour.engine import reputation as R
from starbour.entities.captain import clamp_level


def rows(deltas: list[R.Delta]) -> dict[str, tuple[int, int, float]]:
    return {d.rule: (d.fear, d.respect, round(d.scale, 2)) for d in deltas}


def test_scale_clamps_to_cfg_11(cfg: GameConfig) -> None:
    assert R.scale_for(720, 255, cfg) == cfg.scale_max            # Bastion vs Kestrel 2.82 -> 2.0
    assert R.scale_for(255, 455, cfg) == 0.5603 or abs(R.scale_for(255, 455, cfg) - 255 / 455) < 1e-9
    assert R.scale_for(1, 1000, cfg) == cfg.scale_min
    assert R.scale_for(1, 0, cfg) == cfg.scale_max


def test_kestrel_beats_bastion_scaled_twice(cfg: GameConfig) -> None:
    got = rows(R.battle_won("c-000", own_strength=255, opponent_strength=720, opponent_offered_peace=False, started_it=True, cfg=cfg))
    assert got == {"FR-01": (16, 0, 2.0), "FR-03": (0, 20, 2.0)}, "FR-05 never applies against a stronger opponent"


def test_weaker_opponent_who_offered_peace_stacks_fr04_fr05(cfg: GameConfig) -> None:
    got = rows(R.battle_won("c-000", own_strength=255, opponent_strength=127.5, opponent_offered_peace=True, started_it=True, cfg=cfg))
    assert got == {"FR-01": (4, 0, 0.5), "FR-04": (0, -4, 0.5), "FR-05": (0, -4, 0.5)}, "FR-09: FR-04, FR-05 (and FR-06 at the open) stack"
    only_started = rows(R.battle_won("c-000", own_strength=255, opponent_strength=127.5, opponent_offered_peace=False, started_it=True, cfg=cfg))
    assert set(only_started) == {"FR-01", "FR-05"}
    not_started = rows(R.battle_won("c-000", own_strength=255, opponent_strength=127.5, opponent_offered_peace=False, started_it=False, cfg=cfg))
    assert set(not_started) == {"FR-01"}
    equal = rows(R.battle_won("c-000", own_strength=255, opponent_strength=255, opponent_offered_peace=True, started_it=True, cfg=cfg))
    assert equal == {"FR-01": (8, 0, 1.0)}, "ratio exactly 1: neither stronger nor weaker"


def test_fr02_fr06_and_the_clamp(cfg: GameConfig) -> None:
    lost = R.battle_lost("c-004", own_strength=255, opponent_strength=720, cfg=cfg)
    assert (lost.rule, lost.fear, lost.respect, lost.scale) == ("FR-02", -16, 0, 2.0)
    start = R.start_fight("c-000", cfg)
    assert (start.rule, start.fear, start.respect, start.scale) == ("FR-06", 0, -5, 1.0), "FR-06 is unscaled (D-121)"
    assert R.apply(95, 2, R.Delta("c", "FR-01", 16, -5, 2.0)) == (100, 0), "ints clamped 0...100 (LV-01, LV-05)"
    assert clamp_level(-3) == 0 and clamp_level(100.4) == 100 and clamp_level(7.5) == 8
    assert start.to_dict() == {"captain": "c-000", "rule": "FR-06", "fear": 0, "respect": -5, "scale": 1.0}
