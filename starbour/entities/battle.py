"""Battle - a relation between two captains in one sector (CM-01, CM-02, CM-12, CB-01...03, D-119, D-122).

Opened by the first accepted ``fire`` of either side; ends on destruction of a side's last
ship in the sector, an accepted peace offer, or nobody in range after movement (escape).
Strength is measured once, at the open (CM-08, D-121).  Peace offers stand for the turn
they are made and the next (D-122).  A plain dataclass: no rules here, the rules live in
``starbour.engine.combat``.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Coord = tuple[int, int]
OUTCOMES = ("destroyed", "peace", "escape")


@dataclass(slots=True)
class Battle:
    id: str
    sides: tuple[str, str]            # captain ids, the opener first
    galaxy: Coord
    sector: Coord
    opened_turn: int
    opened_by: str
    strength_at_open: dict[str, float]
    ended: str | None = None          # None | destroyed | peace | escape
    victor: str | None = None
    transfer: dict | None = None      # {gold, silver, items: {element: n}, pct}
    ended_turn: int | None = None
    offers: dict[str, int] = field(default_factory=dict)    # captain -> turn of the latest standing offer (CM-09, D-122)
    offered: list[str] = field(default_factory=list)        # every captain that ever offered peace in this battle (FR-04)

    def other(self, captain_id: str) -> str:
        """The opposing side of ``captain_id``."""
        return self.sides[1] if captain_id == self.sides[0] else self.sides[0]

    def involves(self, captain_id: str) -> bool:
        return captain_id in self.sides

    @property
    def open(self) -> bool:
        return self.ended is None

    def offer_stands(self, captain_id: str, turn_no: int) -> bool:
        """D-122: an offer made at turn T stands at T and T + 1."""
        t = self.offers.get(captain_id)
        return t is not None and turn_no - 1 <= t <= turn_no

    def to_dict(self, exchanges: list[dict] | None = None) -> dict:
        """C10: the report record (with ``exchanges``) or the snapshot record (without)."""
        d: dict = {
            "id": self.id, "sides": list(self.sides), "galaxy": [self.galaxy[0], self.galaxy[1]], "sector": [self.sector[0], self.sector[1]],
            "opened_turn": self.opened_turn, "opened_by": self.opened_by,
            "strength_at_open": {k: round(float(v), 3) for k, v in sorted(self.strength_at_open.items())},
            "ended": self.ended, "victor": self.victor, "transfer": dict(self.transfer) if self.transfer is not None else None,
            "ended_turn": self.ended_turn, "offers": {k: int(v) for k, v in sorted(self.offers.items())}, "offered": list(self.offered),
        }
        if exchanges is not None:
            d["exchanges"] = list(exchanges)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Battle":
        sides = d.get("sides") or []
        return cls(str(d["id"]), (str(sides[0]), str(sides[1])), (int(d["galaxy"][0]), int(d["galaxy"][1])),
                   (int(d["sector"][0]), int(d["sector"][1])), int(d.get("opened_turn") or 0), str(d.get("opened_by") or sides[0]),
                   {str(k): float(v) for k, v in (d.get("strength_at_open") or {}).items()}, d.get("ended"), d.get("victor"),
                   dict(d["transfer"]) if isinstance(d.get("transfer"), dict) else None, d.get("ended_turn"),
                   {str(k): int(v) for k, v in (d.get("offers") or {}).items()}, [str(c) for c in d.get("offered") or []])
