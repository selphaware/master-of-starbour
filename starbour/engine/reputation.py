"""Fear and respect - FR-01...10, LV-01...05, CFG-01...11, D-121 (pure).

Deltas are config; the battle-outcome rules FR-01...05 are scaled by
clamp(opponent strength / your strength at the open, scale_min, scale_max) (LV-03);
FR-06 (starting a fight) is unscaled.  Levels are ints clamped 0...100 (LV-01).  Nothing
else moves them and they never decay (FR-10).  Functions of plain values and the
config; no state object, no rich, no zmq.
"""
from __future__ import annotations

from dataclasses import dataclass

from starbour.config import GameConfig
from starbour.entities.captain import clamp_level

FR_01, FR_02, FR_03, FR_04, FR_05, FR_06 = "FR-01", "FR-02", "FR-03", "FR-04", "FR-05", "FR-06"


@dataclass(frozen=True, slots=True)
class Delta:
    """One C10 ``reputation`` row: the rule, the fear and respect deltas (ints) and the LV-03 scale used."""

    captain: str
    rule: str
    fear: int
    respect: int
    scale: float

    def to_dict(self) -> dict:
        return {"captain": self.captain, "rule": self.rule, "fear": self.fear, "respect": self.respect, "scale": round(self.scale, 4)}


def scale_for(opponent_strength: float, own_strength: float, cfg: GameConfig) -> float:
    """LV-03 / CFG-11: clamp(opponent / you, scale_min, scale_max); a zero own strength counts as the maximum."""
    if own_strength <= 0:
        ratio = float("inf")
    else:
        ratio = float(opponent_strength) / float(own_strength)
    return max(float(cfg.scale_min), min(float(cfg.scale_max), ratio))


def scaled(delta: int, scale: float) -> int:
    """delta x scale, rounded to the nearest int (Python's round, as the gates compute it)."""
    return int(round(int(delta) * float(scale)))


def start_fight(captain_id: str, cfg: GameConfig) -> Delta:
    """FR-06 / CFG-07: -start_fight_delta respect for the side that opens a battle, unscaled (D-121); AI openers too."""
    return Delta(captain_id, FR_06, 0, -int(cfg.respect_start_fight_delta), 1.0)


def battle_won(captain_id: str, *, own_strength: float, opponent_strength: float, opponent_offered_peace: bool,
               started_it: bool, cfg: GameConfig) -> list[Delta]:
    """FR-01, FR-03, FR-04, FR-05 (+ FR-09 stacking) for the victor, each scaled by LV-03.

    stronger opponent = ratio > 1 (FR-03); weaker = ratio < 1 (FR-04 when the loser offered peace,
    FR-05 when the victor started the fight; both stack with each other and with FR-06).
    """
    scale = scale_for(opponent_strength, own_strength, cfg)
    ratio = (opponent_strength / own_strength) if own_strength > 0 else float("inf")
    rows = [Delta(captain_id, FR_01, scaled(cfg.fear_battle_won_delta, scale), 0, scale)]
    if ratio > 1.0:
        rows.append(Delta(captain_id, FR_03, 0, scaled(cfg.respect_win_vs_stronger_delta, scale), scale))
    elif ratio < 1.0:
        if opponent_offered_peace:
            rows.append(Delta(captain_id, FR_04, 0, -scaled(cfg.respect_win_vs_weaker_peace_delta, scale), scale))
        if started_it:
            rows.append(Delta(captain_id, FR_05, 0, -scaled(cfg.respect_win_vs_weaker_started_delta, scale), scale))
    return rows


def battle_lost(captain_id: str, *, own_strength: float, opponent_strength: float, cfg: GameConfig) -> Delta:
    """FR-02 / CFG-02: -battle_lost_delta fear for the loser (AI captains - a player loss is game over), scaled by LV-03."""
    scale = scale_for(opponent_strength, own_strength, cfg)
    return Delta(captain_id, FR_02, -scaled(cfg.fear_battle_lost_delta, scale), 0, scale)


def apply(fear: int, respect: int, delta: Delta) -> tuple[int, int]:
    """LV-01 / LV-05: the new (fear, respect), ints clamped 0...100."""
    return clamp_level(fear + delta.fear), clamp_level(respect + delta.respect)
