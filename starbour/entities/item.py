"""Items lying in sector cells - IT-03...05, SP-03, SYM-09."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class Item:
    """An element compound in a cell; ``revealed`` is the fog-of-war state after a scan (CMD-14)."""

    id: str
    element: str
    rarity: str          # normal | special
    cell: tuple[int, int]
    revealed: bool = False

    def to_dict(self) -> dict:
        return {"id": self.id, "cell": [self.cell[0], self.cell[1]], "element": self.element,
                "rarity": self.rarity, "revealed": self.revealed}

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(str(d["id"]), str(d["element"]), str(d["rarity"]), (int(d["cell"][0]), int(d["cell"][1])),
                   bool(d.get("revealed", False)))
