"""Deep space - the lazy, deterministic content of Void sectors (D-153, D-156, WG-00...02, SP-03, IT-04, IT-05, CFG-84, D-67).

A Void cell at galaxy level (and every sector of a Void galaxy) is a *deep-space sector*: a plain
``sector_size`` grid with no star, planet or hazard that may hold one drifting element stack.  Nothing
is generated at world generation and nothing is stored until something changes: the content comes from
``random.Random("starbour-void-<seed>-<gx>-<gy>-<sx>-<sy>")`` the first time a ship enters or scans the
sector - its own stream, never the world RNG and never the in-game RNG, so touching deep space shifts
no other roll (D-67).  A removed stack is remembered by its coordinates (``GameState.void_removed``,
saved as ``snapshot.deep_space.removed``) and never respawns (IT-05).

[ASSUMED] D-163: the stream is keyed by the seed and the four coordinates as a string (Python seeds
``random.Random`` from a str with SHA-512, so the draw is the same in every process and run); the
stack's cell is uniform over the grid; the special share is ``special_items_per_galaxy /
(special + normal)`` (SP-03); the stack id is ``v-<gx>-<gy>-<sx>-<sy>``.
Pure functions of (seed, coordinates, config); no state object, no I/O.
"""
from __future__ import annotations

import random

from starbour.config import GameConfig
from starbour.entities.item import Item
from starbour.world.generate import VOID, Sector, World, sector_key
from starbour.world.grid import Coord, GeneralGrid


def void_rng(seed: int, galaxy: Coord, sector: Coord) -> random.Random:
    """D-156 / D-67: the deep-space stream for one sector - derived from the seed and the coordinates alone."""
    return random.Random(f"starbour-void-{int(seed)}-{int(galaxy[0])}-{int(galaxy[1])}-{int(sector[0])}-{int(sector[1])}")


def item_id(galaxy: Coord, sector: Coord) -> str:
    return f"v-{int(galaxy[0])}-{int(galaxy[1])}-{int(sector[0])}-{int(sector[1])}"


def special_share(cfg: GameConfig) -> float:
    """SP-03: the chance a deep-space stack is special = special_items_per_galaxy / (special + normal) (D-156)."""
    total = int(cfg.special_items_per_galaxy) + int(cfg.normal_items_per_galaxy)
    return float(cfg.special_items_per_galaxy) / total if total > 0 else 0.0


def element_pools(cfg: GameConfig) -> tuple[list[str], list[str]]:
    """(special elements, normal elements), sorted; each falls back to every element when its rarity is empty (as generate does)."""
    specials = sorted(n for n, e in cfg.elements.items() if e.rarity == "special") or sorted(cfg.elements)
    normals = sorted(n for n, e in cfg.elements.items() if e.rarity == "normal") or sorted(cfg.elements)
    return specials, normals


def content(seed: int, galaxy: Coord, sector: Coord, cfg: GameConfig) -> Item | None:
    """The would-be stack of a deep-space sector: None with probability 1 - void_item_pct, else one stack of CFG-80 units at a
    uniform cell, special at the SP-03 share (D-156).  Deterministic for (seed, galaxy, sector, config)."""
    rng = void_rng(seed, galaxy, sector)
    if rng.random() * 100.0 >= float(cfg.void_item_pct):
        return None
    w, h = cfg.sector_size
    cell = (rng.randrange(int(w)), rng.randrange(int(h)))
    specials, normals = element_pools(cfg)
    special = rng.random() < special_share(cfg)
    element = rng.choice(specials if special else normals)
    units = rng.randint(int(cfg.item_units_min), int(cfg.item_units_max))
    return Item(item_id(galaxy, sector), element, "special" if special else "normal", cell, units=units)


def materialise(seed: int, galaxy: Coord, sector: Coord, cfg: GameConfig, *, removed: bool) -> Sector:
    """A deep-space Sector object (kind 'void', empty grid) holding its stack unless ``removed`` says it was picked up (IT-05)."""
    w, h = cfg.sector_size
    sec = Sector((int(galaxy[0]), int(galaxy[1])), (int(sector[0]), int(sector[1])), GeneralGrid(int(w), int(h)), kind=VOID)
    if not removed:
        item = content(seed, galaxy, sector, cfg)
        if item is not None:
            sec.items.append(item)
    return sec


def in_universe(galaxy: Coord, sector: Coord, cfg: GameConfig) -> bool:
    """CMD-07 / VW-01 / VW-02: inside universe_size x galaxy_size - the only thing a warp destination must satisfy (D-153)."""
    uw, uh = cfg.universe_size
    gw, gh = cfg.galaxy_size
    return 0 <= int(galaxy[0]) < int(uw) and 0 <= int(galaxy[1]) < int(uh) and 0 <= int(sector[0]) < int(gw) and 0 <= int(sector[1]) < int(gh)


def is_deep_space(world: World, galaxy: Coord, sector: Coord, cfg: GameConfig) -> bool:
    """A coordinate inside the universe that holds no generated sector (a Void galaxy cell, or any cell of a Void galaxy)."""
    return in_universe(galaxy, sector, cfg) and world.sector((int(galaxy[0]), int(galaxy[1])), (int(sector[0]), int(sector[1]))) is None


def galaxy_voids(world: World, galaxy: Coord, cfg: GameConfig) -> list[Coord]:
    """The deep-space cells of one galaxy coordinate, row-major (every cell when the galaxy itself is Void)."""
    gw, gh = cfg.galaxy_size
    gal = world.galaxy((int(galaxy[0]), int(galaxy[1])))
    listed = set(gal.sector_coords()) if gal is not None else set()
    return [(sx, sy) for sy in range(int(gh)) for sx in range(int(gw)) if (sx, sy) not in listed]


def void_index(seed: int, world: World, galaxy: Coord, cfg: GameConfig) -> list[dict]:
    """C12 ``--world-index voids``: every deep-space sector of ``galaxy`` with its would-be content, fog ignored (debug) -
    ``{"sector": "g,g/s,s", "item": null | {"element", "units", "rarity", "cell"}}``."""
    out: list[dict] = []
    g = (int(galaxy[0]), int(galaxy[1]))
    for s in galaxy_voids(world, g, cfg):
        item = content(seed, g, s, cfg)
        entry = {"sector": sector_key(g, s), "item": None}
        if item is not None:
            entry["item"] = {"element": item.element, "units": int(item.units), "rarity": item.rarity, "cell": [item.cell[0], item.cell[1]]}
        out.append(entry)
    return out
