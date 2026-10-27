"""Seeded world generation - WG-01...03, SP-01...07, ST-01...04, D-67, D-116, D-131.

Everything comes from ``random.Random(seed)`` in a fixed order: galaxies row-major,
sectors row-major, then each sector's stars, planets and hole, then the galaxy's items,
then wormhole pairing, then (separately, ``populate``) the captains.  Terrain is static,
so a saved game regenerates it from the seed and overlays the dynamic state (D-87).
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from starbour.config import GameConfig, ShipClass
from starbour.entities.captain import Captain
from starbour.entities.item import Item
from starbour.entities.planet import PLANET, Planet
from starbour.entities.ship import Position, Ship
from starbour.world import names
from starbour.world.grid import Coord, GeneralGrid
from starbour.world.stellar import BLACK_HOLE, HOLE_KINDS, NEUTRON_STAR, WORMHOLE, Hole, StarBody, profile, star_mass

Terrain = StarBody | Hole | Planet
FullCell = tuple[Coord, Coord, Coord]   # (galaxy, sector, cell)

PLAYER_ID = "c-000"
PLAYER_SHIP_ID = "s-000"

# D-131 [ASSUMED]: AI captains start in a mix of classes (CLS-02 is their *usual* ship) so strength ratios
# differ and AI-vs-AI battles happen (CM-10, D-125).  Weights by class name; a class missing from [ships] is skipped.
AI_CLASS_WEIGHTS: tuple[tuple[str, int], ...] = (("corsair", 60), ("kestrel", 25), ("bastion", 15))


@dataclass(slots=True)
class Sector:
    """WG-03: at most one terrain object per cell; any number of items (and ships)."""

    galaxy: Coord
    coord: Coord
    grid: GeneralGrid
    items: list[Item] = field(default_factory=list)

    @property
    def key(self) -> str:
        """The C3 snapshot key 'gx,gy/sx,sy'."""
        return sector_key(self.galaxy, self.coord)

    def terrain(self) -> list[Terrain]:
        return self.grid.values()

    def terrain_at(self, cell: Coord) -> Terrain | None:
        return self.grid.get(cell[0], cell[1])

    def has_hole(self) -> bool:
        return any(t.kind in HOLE_KINDS for t in self.grid.values())

    def has_planet(self) -> bool:
        return any(t.kind == PLANET for t in self.grid.values())

    def to_dict(self) -> dict:
        return {"terrain": [t.to_dict() for t in self.grid.values()], "items": [i.to_dict() for i in self.items]}


def sector_key(galaxy: Coord, coord: Coord) -> str:
    return f"{galaxy[0]},{galaxy[1]}/{coord[0]},{coord[1]}"


def galaxy_key(galaxy: Coord) -> str:
    return f"{galaxy[0]},{galaxy[1]}"


@dataclass(slots=True)
class Galaxy:
    """WG-02: a grid of sectors or Void."""

    coord: Coord
    grid: GeneralGrid

    def sector_coords(self) -> list[Coord]:
        return self.grid.coords()

    def hazard_coords(self) -> list[Coord]:
        """Sectors holding a black hole, neutron star or wormhole (VW-02 highlights)."""
        return [c for c, s in self.grid.items() if s.has_hole()]


@dataclass(slots=True)
class World:
    """WG-01: the universe grid of galaxies, plus flat indexes for speed."""

    seed: int
    universe: GeneralGrid
    sectors: dict[tuple[Coord, Coord], Sector]
    wormholes: list[tuple[FullCell, FullCell]]
    static_galaxies: dict[str, dict] = field(default_factory=dict)   # 'gx,gy' -> {sectors, hazards}
    flat_sectors: dict[Coord, tuple[Coord, Coord]] = field(default_factory=dict)   # MV-02 flattened coords -> (galaxy, sector)

    def galaxy(self, coord: Coord) -> Galaxy | None:
        if not self.universe.in_bounds(coord[0], coord[1]):
            return None
        return self.universe.get(coord[0], coord[1])

    def sector(self, galaxy: Coord, coord: Coord) -> Sector | None:
        return self.sectors.get((galaxy, coord))

    def galaxy_coords(self) -> list[Coord]:
        return self.universe.coords()


def world_rng(seed: int) -> random.Random:
    """The world-generation stream - SP-01, D-67."""
    return random.Random(seed)


def game_rng(seed: int) -> random.Random:
    """The separate in-game stream (combat rolls, AI walks), derived from the seed - D-67."""
    return random.Random(f"starbour-game-{seed}")


def _random_free_cell(grid: GeneralGrid, rng: random.Random) -> Coord:
    """A Void cell chosen uniformly enough: bounded random draws, then the exact list."""
    for _ in range(64):
        x, y = rng.randrange(grid.width), rng.randrange(grid.height)
        if grid.is_void(x, y):
            return x, y
    free = grid.free_cells()
    if not free:
        raise ValueError("sector has no free cell left (WG-03)")
    return rng.choice(free)


def _fill_sector(sector: Sector, rng: random.Random, cfg: GameConfig, used_planet_names: set[str]) -> None:
    """SP-02, SP-04, SP-07: 0-3 stars (systems in adjacent cells), planets in the band around a star (D-116), 0-1 hole."""
    grid = sector.grid
    n_stars = rng.randint(0, 3)
    star_cells: list[Coord] = []
    if n_stars > 0:
        first = _random_free_cell(grid, rng)
        star_cells.append(first)
        grid.set(first[0], first[1], None)   # reserve the cell while the others are chosen
        multi = n_stars >= 2 and rng.random() * 100.0 < cfg.multi_star_pct
        for _ in range(n_stars - 1):
            if multi:
                candidates = [c for c in grid.neighbours(*first) if grid.is_void(*c)]
                cell = rng.choice(candidates) if candidates else _random_free_cell(grid, rng)
            else:
                cell = _random_free_cell(grid, rng)
            star_cells.append(cell)
            grid.set(cell[0], cell[1], None)
        for cell in star_cells:
            radius = rng.uniform(cfg.radius_min, cfg.radius_max)
            base = rng.uniform(cfg.base_energy_min, cfg.base_energy_max)
            prof = profile(radius, base, cfg)
            grid.set(cell[0], cell[1], StarBody(cell, radius, base, star_mass(radius), prof.colour, prof.bucket))
        for cell in star_cells:
            for _ in range(rng.randint(0, cfg.planets_per_system_max)):
                # SP-04 / CFG-61 / D-116: Chebyshev distance planet_distance_min...max from the star, never on another star's cell
                candidates = [c for c in grid.ring(cell[0], cell[1], cfg.planet_distance_min, cfg.planet_distance_max) if grid.is_void(*c)]
                if not candidates:
                    break
                pcell = rng.choice(candidates)
                grid.set(pcell[0], pcell[1], Planet(pcell, names.unique(names.planet_name, rng, used_planet_names),
                                                    names.president_name(rng)))
    if rng.random() * 100.0 < cfg.hole_pct and len(grid) < grid.width * grid.height:
        hcell = _random_free_cell(grid, rng)
        if rng.random() * 100.0 < cfg.neutron_pct:
            grid.set(hcell[0], hcell[1], Hole(NEUTRON_STAR, hcell, cfg.neutron_mass))
        else:
            kind = WORMHOLE if rng.random() * 100.0 < cfg.wormhole_pct else BLACK_HOLE
            grid.set(hcell[0], hcell[1], Hole(kind, hcell, cfg.blackhole_mass))


def _place_items(galaxy: Galaxy, rng: random.Random, cfg: GameConfig, counter: list[int]) -> None:
    """SP-03 / IT-05: special_items_per_galaxy + normal_items_per_galaxy per galaxy."""
    specials = sorted(n for n, e in cfg.elements.items() if e.rarity == "special") or sorted(cfg.elements)
    normals = sorted(n for n, e in cfg.elements.items() if e.rarity == "normal") or sorted(cfg.elements)
    coords = galaxy.sector_coords()
    for rarity, count, pool in (("special", cfg.special_items_per_galaxy, specials),
                                ("normal", cfg.normal_items_per_galaxy, normals)):
        for _ in range(count):
            scoord = rng.choice(coords)
            sector: Sector = galaxy.grid.get(scoord[0], scoord[1])
            cell = (rng.randrange(sector.grid.width), rng.randrange(sector.grid.height))
            counter[0] += 1
            sector.items.append(Item(f"i-{counter[0]:04d}", rng.choice(pool), rarity, cell))


def _pair_wormholes(candidates: list[tuple[Coord, Coord, Hole]], rng: random.Random) -> list[tuple[FullCell, FullCell]]:
    """ST-04 / D-20: fixed pairs at seeding, any galaxy; an odd one becomes a plain black hole."""
    order = list(candidates)
    rng.shuffle(order)
    pairs: list[tuple[FullCell, FullCell]] = []
    for i in range(0, len(order) - 1, 2):
        ga, sa, ha = order[i]
        gb, sb, hb = order[i + 1]
        ha.partner = (gb, sb, hb.cell)
        hb.partner = (ga, sa, ha.cell)
        pairs.append(((ga, sa, ha.cell), (gb, sb, hb.cell)))
    if len(order) % 2 == 1:
        order[-1][2].kind = BLACK_HOLE
    return pairs


def flat_sector(galaxy: Coord, sector: Coord, galaxy_size: Coord) -> Coord:
    """MV-02: flattened sector coordinates (gx x galaxy_w + sx, gy x galaxy_h + sy)."""
    return int(galaxy[0]) * int(galaxy_size[0]) + int(sector[0]), int(galaxy[1]) * int(galaxy_size[1]) + int(sector[1])


def generate_world(seed: int, cfg: GameConfig) -> World:
    """The static universe for ``seed`` - SP-01: same seed, same world (tests/test_generate.py)."""
    rng = world_rng(seed)
    uw, uh = cfg.universe_size
    gw, gh = cfg.galaxy_size
    sw, sh = cfg.sector_size
    universe: GeneralGrid = GeneralGrid(uw, uh)
    for y in range(uh):
        for x in range(uw):
            if rng.random() * 100.0 < cfg.galaxy_fill_pct:
                universe.set(x, y, Galaxy((x, y), GeneralGrid(gw, gh)))
    if len(universe) == 0:
        universe.set(uw // 2, uh // 2, Galaxy((uw // 2, uh // 2), GeneralGrid(gw, gh)))
    sectors: dict[tuple[Coord, Coord], Sector] = {}
    wormhole_candidates: list[tuple[Coord, Coord, Hole]] = []
    used_planet_names: set[str] = set()
    item_counter = [0]
    for gcoord, galaxy in universe.items():
        for sy in range(gh):
            for sx in range(gw):
                if rng.random() * 100.0 < cfg.sector_fill_pct:
                    galaxy.grid.set(sx, sy, Sector(gcoord, (sx, sy), GeneralGrid(sw, sh)))
        if len(galaxy.grid) == 0:
            galaxy.grid.set(gw // 2, gh // 2, Sector(gcoord, (gw // 2, gh // 2), GeneralGrid(sw, sh)))
        for scoord, sector in galaxy.grid.items():
            _fill_sector(sector, rng, cfg, used_planet_names)
            sectors[(gcoord, scoord)] = sector
            for t in sector.grid.values():
                if isinstance(t, Hole) and t.kind == WORMHOLE:
                    wormhole_candidates.append((gcoord, scoord, t))
        _place_items(galaxy, rng, cfg, item_counter)
    pairs = _pair_wormholes(wormhole_candidates, rng)
    world = World(seed, universe, sectors, pairs)
    for gcoord, galaxy in universe.items():
        world.static_galaxies[galaxy_key(gcoord)] = {
            "sectors": [[c[0], c[1]] for c in galaxy.sector_coords()],
            "hazards": [[c[0], c[1]] for c in galaxy.hazard_coords()],
        }
    world.flat_sectors = {flat_sector(g, s, cfg.galaxy_size): (g, s) for (g, s) in sectors}
    return world


@dataclass(slots=True)
class Population:
    captains: dict[str, Captain]
    ships: dict[str, Ship]
    player_id: str
    next_captain_no: int
    next_ship_no: int


def ai_ship_class(cfg: GameConfig) -> ShipClass:
    """[ASSUMED] D-89: the AI's usual ship is the Corsair (CLS-02) when [ships] has one, else the starting class."""
    return cfg.ships.get("corsair", cfg.ships[cfg.starting_class])


def ai_ship_classes(cfg: GameConfig) -> tuple[list[ShipClass], list[int]]:
    """D-131: the class mix AI captains are drawn from (classes present in [ships], with their weights)."""
    classes = [cfg.ships[n] for n, _ in AI_CLASS_WEIGHTS if n in cfg.ships]
    weights = [w for n, w in AI_CLASS_WEIGHTS if n in cfg.ships]
    if not classes:
        return [ai_ship_class(cfg)], [1]
    return classes, weights


def populate(world: World, cfg: GameConfig, rng: random.Random) -> Population:
    """SP-05, SP-06, CP-*: the player (safe start) then ai_captains_per_galaxy AI captains per galaxy."""
    captains: dict[str, Captain] = {}
    ships: dict[str, Ship] = {}
    used_captains: set[str] = set()
    used_ships: set[str] = set()
    all_keys = sorted(world.sectors)
    if cfg.safe_start:
        candidates = [k for k in all_keys if world.sectors[k].has_planet() and not world.sectors[k].has_hole()]
        if not candidates:   # [ASSUMED] tiny worlds: fall back to any hole-free sector, then any sector
            candidates = [k for k in all_keys if not world.sectors[k].has_hole()] or all_keys
    else:
        candidates = all_keys
    gcoord, scoord = rng.choice(candidates)
    start = world.sectors[(gcoord, scoord)]
    cell = _random_free_cell(start.grid, rng)
    player = Captain.new(PLAYER_ID, names.unique(names.captain_name, rng, used_captains), True, cfg)
    pship = Ship.of_class(PLAYER_SHIP_ID, names.unique(names.ship_name, rng, used_ships), cfg.ships[cfg.starting_class],
                          PLAYER_ID, (gcoord, scoord, cell))
    player.ships = [pship.id]
    player.active_ship = pship.id
    captains[player.id] = player
    ships[pship.id] = pship
    captain_no = ship_no = 1
    classes, weights = ai_ship_classes(cfg)
    for gcoord, galaxy in world.universe.items():
        coords = galaxy.sector_coords()
        safe = [c for c in coords if not galaxy.grid.get(c[0], c[1]).has_hole()] or coords
        for _ in range(cfg.ai_captains_per_galaxy):
            scoord = rng.choice(safe)
            sector: Sector = galaxy.grid.get(scoord[0], scoord[1])
            cell = _random_free_cell(sector.grid, rng)
            cid, sid = f"c-{captain_no:03d}", f"s-{ship_no:03d}"
            cap = Captain.new(cid, names.unique(names.captain_name, rng, used_captains), False, cfg)
            spec = rng.choices(classes, weights)[0]
            ship = Ship.of_class(sid, names.unique(names.ship_name, rng, used_ships), spec, cid, (gcoord, scoord, cell))
            cap.ships = [sid]
            cap.active_ship = sid
            captains[cid] = cap
            ships[sid] = ship
            captain_no += 1
            ship_no += 1
    return Population(captains, ships, PLAYER_ID, captain_no, ship_no)


def flatten_cell(pos: Position, cfg: GameConfig) -> Coord:
    """Global cell coordinates on the flattened sector grid (MV-02 note, SUP-04 'nearest')."""
    (gx, gy), (sx, sy), (cx, cy) = pos
    gw, gh = cfg.galaxy_size
    sw, sh = cfg.sector_size
    return (gx * gw + sx) * sw + cx, (gy * gh + sy) * sh + cy
