"""Ship - SH-01...10, CLS-01...03."""
from __future__ import annotations

from dataclasses import dataclass

from starbour.config import ShipClass

Cell = tuple[int, int]
Position = tuple[Cell, Cell, Cell]   # (galaxy xy, sector xy, cell xy) - SH-09


def position_to_list(pos: Position) -> list[list[int]]:
    return [[pos[0][0], pos[0][1]], [pos[1][0], pos[1][1]], [pos[2][0], pos[2][1]]]


def position_from_list(raw: list) -> Position:
    if len(raw) != 3 or any(len(p) != 2 for p in raw):
        raise ValueError(f"position must be [[gx,gy],[sx,sy],[cx,cy]], got {raw!r} (SH-09)")
    return (int(raw[0][0]), int(raw[0][1])), (int(raw[1][0]), int(raw[1][1])), (int(raw[2][0]), int(raw[2][1]))


@dataclass(slots=True)
class Ship:
    """One starship.  Items are captain-level (SH-10); shields are the ship's health (SH-07)."""

    id: str
    name: str
    cls: str
    captain_id: str
    torpedoes: int
    phasers: float
    shield_max: float
    shield_current: float
    engine: float
    warp: float
    position: Position

    @classmethod
    def of_class(cls, ship_id: str, name: str, spec: ShipClass, captain_id: str, position: Position) -> "Ship":
        """Build a ship with its class's base stats - CLS-01...03."""
        return cls(ship_id, name, spec.name, captain_id, spec.torpedoes, spec.phasers, spec.shield_max,
                   spec.shield_max, spec.engine, spec.warp, position)

    @property
    def galaxy(self) -> Cell:
        return self.position[0]

    @property
    def sector(self) -> Cell:
        return self.position[1]

    @property
    def cell(self) -> Cell:
        return self.position[2]

    def to_dict(self, orders: list[dict] | None = None) -> dict:
        """The C3 snapshot entry for ``ships``."""
        return {
            "name": self.name, "class": self.cls, "captain": self.captain_id,
            "torpedoes": self.torpedoes, "phasers": self.phasers, "shield_max": self.shield_max,
            "shield_current": self.shield_current, "engine": self.engine, "warp": self.warp,
            "position": position_to_list(self.position),
            "orders_queued": len(orders or []), "orders": list(orders or []),
        }

    @classmethod
    def from_dict(cls, ship_id: str, d: dict) -> "Ship":
        return cls(ship_id, str(d["name"]), str(d["class"]), str(d["captain"]), int(d["torpedoes"]), float(d["phasers"]),
                   float(d["shield_max"]), float(d["shield_current"]), float(d["engine"]), float(d["warp"]),
                   position_from_list(d["position"]))
