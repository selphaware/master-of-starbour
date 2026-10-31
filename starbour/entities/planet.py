"""Planet - PL-01...06, PR-01...05, CM-11, HZ-07, CFG-77...79, D-140, D-145.

Terrain in M1 (a cell next to a star, SP-04); M4 gives every planet defence (CFG-77), a
supplies stock that regenerates lazily (CFG-78, PR-03) and element stacks for sale
(CFG-79, PL-03).  A planet is also a combat participant (CM-11, D-145): it carries the
same attributes a ship does for the strength formula and the exchange - ``captain_id``
is its own id (a planet is its own side), engine and warp are 0 (it cannot move or
escape).  No rules here; ``starbour.engine.economy`` and ``starbour.engine.combat`` hold them.

Stock timing (D-150 [ASSUMED]): ``food_base`` / ``water_base`` hold the stock as it stood at
the end of turn ``stock_turn``; the effective stock at the end of turn T is
``min(stock_cap, base + stock_regen x (T - stock_turn))`` (PL-02: +stock_regen a turn to
the cap).  TP-05 of turn T trades against the end-of-turn-(T-1) value, so the turn's
regeneration lands at TP-06 as the spec says; an untouched planet needs no entry in the
snapshot because its stock follows the seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field

Coord = tuple[int, int]
PLANET = "planet"


@dataclass(slots=True)
class Planet:
    """A planet in a sector cell adjacent to a star (SP-04); dynamic state from M4 (PL-02...04)."""

    cell: Coord
    name: str
    president: str
    kind: str = PLANET
    id: str = ""
    galaxy: Coord = (0, 0)
    sector: Coord = (0, 0)
    # CFG-77 defence (PL-04, D-140)
    shield_max: float = 0.0
    shield_current: float = 0.0
    torpedoes: int = 0
    phasers: float = 0.0
    torpedoes_gen: int = 0
    # CFG-78 stock (PL-02, PR-03): lazily regenerating supplies
    food_base: int = 0
    water_base: int = 0
    stock_turn: int = 0
    # CFG-79 element stacks (PL-03): never regenerate
    elements: dict[str, int] = field(default_factory=dict)
    elements_gen: dict[str, int] = field(default_factory=dict)
    destroyed: bool = False
    target: str | None = None       # the ship it last fired at (GRD-08)

    # ---- the combatant protocol shared with Ship (CM-08, CM-11)
    @property
    def captain_id(self) -> str:
        return self.id

    @property
    def cls(self) -> str:
        return PLANET

    @property
    def engine(self) -> float:
        return 0.0

    @property
    def warp(self) -> float:
        return 0.0

    @property
    def position(self) -> tuple[Coord, Coord, Coord]:
        return self.galaxy, self.sector, self.cell

    @property
    def is_player(self) -> bool:
        return False

    @property
    def active_ship(self) -> str:
        """A planet stands in for its own ship (peace targets name a ship, GRD-08)."""
        return self.id

    @property
    def shield_pct(self) -> float:
        return 100.0 * self.shield_current / self.shield_max if self.shield_max > 0 else 0.0

    @property
    def alive(self) -> bool:
        return not self.destroyed

    # ---- stock (PL-02, PR-03, D-150)
    def stock(self, item: str, turn_no: int, cfg) -> int:
        """The effective food / water stock at the end of ``turn_no`` (CFG-78: +stock_regen a turn to stock_cap)."""
        base = self.food_base if item == "food" else self.water_base
        turns = max(0, int(turn_no) - int(self.stock_turn))
        return int(min(int(cfg.stock_cap), base + int(cfg.stock_regen) * turns))

    def set_stock(self, item: str, value: int, turn_no: int, cfg) -> None:
        """Rebase one stock after a trade or a loot as it stands at the end of ``turn_no`` (the other keeps its value)."""
        food = value if item == "food" else self.stock("food", turn_no, cfg)
        water = value if item == "water" else self.stock("water", turn_no, cfg)
        self.rebase(food, water, turn_no)

    def rebase(self, food: int, water: int, turn_no: int) -> None:
        """Set both stocks as they stand at the end of ``turn_no``."""
        self.food_base, self.water_base, self.stock_turn = int(food), int(water), int(turn_no)

    def untouched(self, cfg, turn_no: int) -> bool:
        """True when every dynamic field still follows the seed (the snapshot may omit the record)."""
        if self.destroyed or self.shield_current < self.shield_max or self.torpedoes != self.torpedoes_gen:
            return False
        if self.elements != self.elements_gen:
            return False
        default_food = min(int(cfg.stock_cap), int(cfg.food_stock_start) + int(cfg.stock_regen) * int(turn_no))
        default_water = min(int(cfg.stock_cap), int(cfg.water_stock_start) + int(cfg.stock_regen) * int(turn_no))
        return self.stock("food", turn_no, cfg) == default_food and self.stock("water", turn_no, cfg) == default_water

    # ---- dictionaries
    def to_dict(self) -> dict:
        """The terrain entry (C3 ``sectors[*].terrain``)."""
        return {"kind": self.kind, "cell": [self.cell[0], self.cell[1]], "name": self.name, "president": self.president, "id": self.id}

    def sector_key(self) -> str:
        return f"{self.galaxy[0]},{self.galaxy[1]}/{self.sector[0]},{self.sector[1]}"

    def record(self, cfg, turn_no: int, *, disposition: int, trades: bool, in_battle: str | None) -> dict:
        """The C11 ``snapshot.planets[id]`` record."""
        return {
            "name": self.name, "president": self.president, "cell": [self.cell[0], self.cell[1]], "sector": self.sector_key(),
            "shields": round(float(self.shield_current), 4), "shield_max": float(self.shield_max), "torpedoes": int(self.torpedoes),
            "phasers": float(self.phasers),
            "stock": {"food": self.stock("food", turn_no, cfg), "water": self.stock("water", turn_no, cfg),
                      "elements": {k: int(v) for k, v in sorted(self.elements.items()) if int(v) > 0}},
            "disposition_to_player": int(disposition), "trades_with_player": bool(trades) and not self.destroyed,
            "in_battle": in_battle, "destroyed": bool(self.destroyed), "target": self.target,
        }

    def apply_record(self, d: dict, turn_no: int) -> None:
        """Overlay a saved record on the regenerated planet (C6: stock, defence, destroyed survive)."""
        self.shield_max = float(d.get("shield_max", self.shield_max))
        self.shield_current = float(d.get("shields", self.shield_current))
        self.torpedoes = int(d.get("torpedoes", self.torpedoes))
        self.phasers = float(d.get("phasers", self.phasers))
        st = d.get("stock") or {}
        self.rebase(int(st.get("food", self.food_base)), int(st.get("water", self.water_base)), turn_no)
        els = st.get("elements")
        if isinstance(els, dict):
            self.elements = {str(k): int(v) for k, v in els.items() if int(v) > 0}
        self.destroyed = bool(d.get("destroyed", False))
        self.target = d.get("target")
