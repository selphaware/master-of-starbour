"""Planet - PL-01...06, HZ-07.  Terrain in M1; trade, stocks and disposition arrive in M4."""
from __future__ import annotations

from dataclasses import dataclass

PLANET = "planet"


@dataclass(slots=True)
class Planet:
    """A planet in a sector cell adjacent to a star (SP-04)."""

    cell: tuple[int, int]
    name: str
    president: str
    kind: str = PLANET

    def to_dict(self) -> dict:
        return {"kind": self.kind, "cell": [self.cell[0], self.cell[1]], "name": self.name, "president": self.president}
