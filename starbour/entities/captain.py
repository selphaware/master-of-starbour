"""Captain - CP-01...10, SUP-02 (mutiny countdown), LV-04 (reputation visible after a scan)."""
from __future__ import annotations

from dataclasses import dataclass, field

from starbour.config import GameConfig


@dataclass(slots=True)
class Captain:
    """A captain, player or AI (CP-09); supplies and items are captain-level (CP-04, CP-07)."""

    id: str
    name: str
    is_player: bool
    gold: int
    silver: int
    fear: int
    respect: int
    food: int
    water: int
    ships: list[str] = field(default_factory=list)
    active_ship: str | None = None
    items: list[str] = field(default_factory=list)
    is_master: bool = False
    zero_turns: int = 0   # consecutive upkeep phases with food or water at 0 - SUP-03, D-76
    rep_seen_turn: int | None = None   # LV-04: the turn a scan revealed this captain's fear and respect (C10 visible_reputation)

    @classmethod
    def new(cls, captain_id: str, name: str, is_player: bool, cfg: GameConfig, *, gold: int | None = None,
            silver: int | None = None, fear: int | None = None, respect: int | None = None) -> "Captain":
        """A fresh captain with the ``[captain]``, ``[fear]``, ``[respect]`` and ``[supplies]`` starts."""
        return cls(captain_id, name, is_player,
                   cfg.gold_start if gold is None else gold, cfg.silver_start if silver is None else silver,
                   clamp_level(cfg.fear_start if fear is None else fear), clamp_level(cfg.respect_start if respect is None else respect),
                   cfg.food_start, cfg.water_start)

    def mutiny_countdown(self, cfg: GameConfig) -> int | None:
        """Turns until the crew mutiny while food or water is at 0, else None - SUP-02, D-76.

        After the upkeep that first hits 0 the counter is 1 and the mutiny fires when it
        exceeds mutiny_turns_at_zero, so the countdown is mutiny_turns_at_zero + 1 - zero_turns.
        """
        if self.zero_turns <= 0:
            return None
        return max(0, cfg.mutiny_turns_at_zero + 1 - self.zero_turns)

    def visible_reputation(self, turn_no: int) -> bool:
        """LV-04 / C10: true for the turn a scan revealed this captain (the scan belongs to the turn being prepared)."""
        return self.rep_seen_turn is not None and self.rep_seen_turn >= turn_no

    def wealth_silver(self, cfg: GameConfig) -> int:
        """EG-01: gold x silver_per_gold + silver."""
        return self.gold * cfg.silver_per_gold + self.silver

    def to_dict(self, cfg: GameConfig, turn_no: int = 0) -> dict:
        """The C3 snapshot entry for ``captains`` (C10 adds ``visible_reputation``)."""
        return {
            "name": self.name, "is_player": self.is_player, "gold": self.gold, "silver": self.silver,
            "fear": self.fear, "respect": self.respect, "food": self.food, "water": self.water,
            "ships": list(self.ships), "active_ship": self.active_ship, "items": list(self.items),
            "is_master": self.is_master, "mutiny_countdown": self.mutiny_countdown(cfg),
            "zero_turns": self.zero_turns, "rep_seen_turn": self.rep_seen_turn,
            "visible_reputation": self.visible_reputation(turn_no),
        }

    @classmethod
    def from_dict(cls, captain_id: str, d: dict) -> "Captain":
        seen = d.get("rep_seen_turn")
        return cls(captain_id, str(d["name"]), bool(d["is_player"]), int(d["gold"]), int(d["silver"]), int(d["fear"]),
                   int(d["respect"]), int(d["food"]), int(d["water"]), [str(s) for s in d.get("ships", [])],
                   d.get("active_ship"), [str(i) for i in d.get("items", [])], bool(d.get("is_master", False)),
                   int(d.get("zero_turns", 0)), int(seen) if seen is not None else None)


def clamp_level(value: float) -> int:
    """LV-01 / LV-05: fear and respect are ints in 0...100."""
    return max(0, min(100, int(round(value))))
