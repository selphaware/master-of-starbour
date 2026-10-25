"""AI captains' decisions - OV-10, SP-06.  M1: a random impulse walk every turn.

``decide`` is a pure function of (captain, state, rng, cfg); it draws only from the
engine's game RNG (D-67) and never from the global ``random``.
"""
from __future__ import annotations

import random

from starbour.commands.parse import QUEUED, Order
from starbour.config import GameConfig
from starbour.entities.captain import Captain
from starbour.engine.state import GameState


def decide(captain: Captain, state: GameState, rng: random.Random, cfg: GameConfig) -> list[Order]:
    """One ``move`` per owned ship to a random other cell of its sector at full engine power.

    M1 subset of OV-10: no combat, trade or warp.  A ship alone in a 1x1 sector gets no
    order (there is nowhere to go)."""
    orders: list[Order] = []
    w, h = cfg.sector_size
    for sid in captain.ships:
        ship = state.ships.get(sid)
        if ship is None:
            continue
        cx, cy = ship.cell
        tx, ty = cx, cy
        for _ in range(8):
            tx, ty = rng.randrange(w), rng.randrange(h)
            if (tx, ty) != (cx, cy):
                break
        if (tx, ty) == (cx, cy):
            continue
        orders.append(Order("move", (str(tx), str(ty), f"{ship.engine:g}"), sid, QUEUED))
    return orders
