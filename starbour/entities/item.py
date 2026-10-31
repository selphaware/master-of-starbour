"""Items lying in sector cells - IT-03...05, SP-03, SYM-09, CFG-80, D-141.

An item is an element stack of ``units`` units.  ``revealed`` is the fog-of-war state after
a scan in the cell (CMD-14, IT-04): the snapshot carries the element and the units only once
the stack is revealed (C11), so a terminal cannot show what the captain has not scanned.
"""
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
    units: int = 1

    def to_dict(self) -> dict:
        d: dict = {"id": self.id, "cell": [self.cell[0], self.cell[1]], "rarity": self.rarity, "revealed": self.revealed}
        if self.revealed:
            d["element"] = self.element
            d["units"] = int(self.units)
        return d

    def index_entry(self) -> dict:
        """The debug ``world_index.items`` entry (C11): everything, fog ignored."""
        return {"id": self.id, "cell": [self.cell[0], self.cell[1]], "element": self.element, "units": int(self.units), "rarity": self.rarity}

    @classmethod
    def from_dict(cls, d: dict) -> "Item":
        return cls(str(d["id"]), str(d.get("element") or ""), str(d.get("rarity") or "normal"), (int(d["cell"][0]), int(d["cell"][1])),
                   bool(d.get("revealed", False)), int(d.get("units") or 1))
