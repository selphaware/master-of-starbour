"""Captain - CP-01...10, SUP-02 (mutiny countdown), LV-04 (reputation visible after a scan), IT-03 (item stacks)."""
from __future__ import annotations

from dataclasses import dataclass, field

from starbour.config import GameConfig


@dataclass(slots=True)
class Captain:
    """A captain, player or AI (CP-09); supplies and items are captain-level (CP-04, CP-07).

    ``items`` maps an element to the units held (C11); ``restock_target`` is the planet an AI
    captain is heading for to buy supplies (D-144), None when it is not on a trip.
    """

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
    items: dict[str, int] = field(default_factory=dict)
    is_master: bool = False
    zero_turns: int = 0   # consecutive upkeep phases with food or water at 0 - SUP-03, D-76
    rep_seen_turn: int | None = None   # LV-04: the turn a scan revealed this captain's fear and respect (C10 visible_reputation)
    restock_target: str | None = None  # D-144: the planet an AI captain is heading for

    @classmethod
    def new(cls, captain_id: str, name: str, is_player: bool, cfg: GameConfig, *, gold: int | None = None,
            silver: int | None = None, fear: int | None = None, respect: int | None = None) -> "Captain":
        """A fresh captain with the ``[captain]``, ``[fear]``, ``[respect]`` and ``[supplies]`` starts.

        AI captains start at ``[ai] respect_start`` (D-149 [ASSUMED]); the player at ``[respect] start`` (LV-02)."""
        default_respect = cfg.respect_start if is_player else cfg.ai_respect_start
        return cls(captain_id, name, is_player,
                   cfg.gold_start if gold is None else gold, cfg.silver_start if silver is None else silver,
                   clamp_level(cfg.fear_start if fear is None else fear), clamp_level(default_respect if respect is None else respect),
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
        """EG-01 / TR-01: gold x silver_per_gold + silver."""
        return self.gold * cfg.silver_per_gold + self.silver

    # ---- items (IT-03, TR-03)
    def add_item(self, element: str, units: int) -> None:
        if units > 0:
            self.items[element] = self.items.get(element, 0) + int(units)

    def take_item(self, element: str, units: int) -> bool:
        """Remove ``units`` of ``element``; False (and nothing taken) when the captain holds fewer."""
        have = self.items.get(element, 0)
        if units <= 0:
            return True
        if have < units:
            return False
        left = have - int(units)
        if left:
            self.items[element] = left
        else:
            del self.items[element]
        return True

    def item_units(self) -> int:
        return sum(int(v) for v in self.items.values())

    def to_dict(self, cfg: GameConfig, turn_no: int = 0) -> dict:
        """The C3 snapshot entry for ``captains`` (C10 adds ``visible_reputation``, C11 ``items`` as a dict)."""
        return {
            "name": self.name, "is_player": self.is_player, "gold": self.gold, "silver": self.silver,
            "fear": self.fear, "respect": self.respect, "food": self.food, "water": self.water,
            "ships": list(self.ships), "active_ship": self.active_ship,
            "items": {k: int(v) for k, v in sorted(self.items.items()) if int(v) > 0},
            "is_master": self.is_master, "mutiny_countdown": self.mutiny_countdown(cfg),
            "zero_turns": self.zero_turns, "rep_seen_turn": self.rep_seen_turn,
            "visible_reputation": self.visible_reputation(turn_no), "restock_target": self.restock_target,
        }

    @classmethod
    def from_dict(cls, captain_id: str, d: dict) -> "Captain":
        seen = d.get("rep_seen_turn")
        return cls(captain_id, str(d["name"]), bool(d["is_player"]), int(d["gold"]), int(d["silver"]), int(d["fear"]),
                   int(d["respect"]), int(d["food"]), int(d["water"]), [str(s) for s in d.get("ships", [])],
                   d.get("active_ship"), items_from(d.get("items")), bool(d.get("is_master", False)),
                   int(d.get("zero_turns", 0)), int(seen) if seen is not None else None, d.get("restock_target"))


def items_from(raw: object) -> dict[str, int]:
    """``{element: units}`` from a C11 dict or an M3 list of element names (one unit each)."""
    out: dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            n = int(v)
            if n > 0:
                out[str(k)] = out.get(str(k), 0) + n
    elif isinstance(raw, list):
        for k in raw:
            out[str(k)] = out.get(str(k), 0) + 1
    return out


def clamp_level(value: float) -> int:
    """LV-01 / LV-05: fear and respect are ints in 0...100."""
    return max(0, min(100, int(round(value))))
