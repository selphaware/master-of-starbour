"""Items in space - scan reveal and pickup (pure): SP-03, IT-03...05, CMD-14, CMD-15, OUT-10, FR-08, SYM-09, D-141.

A stack is hidden until a ``scan`` in its cell (IT-04); ``pickup <element>`` moves a revealed stack
to the captain's items at TP-05 (never automatic); a special (mythical) stack earns FR-08 once,
when it is picked up; nothing respawns (IT-05).  AI captains pick up what they land on after an
automatic scan (D-144 [ASSUMED]).  Functions of entities and the config; no I/O.
"""
from __future__ import annotations

from starbour.config import GameConfig
from starbour.engine import reputation as R
from starbour.entities.captain import Captain
from starbour.entities.item import Item

FR_08 = "FR-08"


def items_at(sector, cell) -> list[Item]:
    return [i for i in sector.items if i.cell == tuple(cell)]


def reveal(sector, cell) -> list[Item]:
    """CMD-14 / SYM-09: every stack in the cell becomes visible."""
    found = items_at(sector, cell)
    for item in found:
        item.revealed = True
    return found


def item_text(item: Item) -> str:
    """OUT-10: element, units and rarity, e.g. 'iridium (2 units, normal)'."""
    return f"{item.element} ({item.units} unit{'s' if item.units != 1 else ''}, {item.rarity})"


def revealed_stack(sector, cell, element: str) -> Item | None:
    """The revealed stack of ``element`` in the cell, else None (IT-04: unscanned stacks cannot be picked up)."""
    for item in items_at(sector, cell):
        if item.revealed and item.element == element:
            return item
    return None


def pickup_row(cap: Captain, item: Item) -> dict:
    return {"captain": cap.id, "element": item.element, "units": int(item.units), "rarity": item.rarity, "cell": [item.cell[0], item.cell[1]]}


def pickup(sector, cap: Captain, item: Item, cfg: GameConfig) -> tuple[dict, dict | None]:
    """Move the stack to the captain (CMD-15); returns (the C11 pickups row, the FR-08 row for a special stack or None)."""
    sector.items = [i for i in sector.items if i.id != item.id]
    cap.add_item(item.element, int(item.units))
    rep = None
    if item.rarity == "special":
        rep = R.Delta(cap.id, FR_08, 0, int(cfg.respect_special_item_delta), 1.0).to_dict()
    return pickup_row(cap, item), rep
