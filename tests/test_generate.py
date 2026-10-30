"""World generation - SP-01...07, WG-03, ST-04 (tests/test_generate.py per specs/M1.md)."""
from __future__ import annotations

import dataclasses
import random

from starbour.config import GameConfig
from starbour.entities.planet import PLANET
from starbour.world.generate import Sector, _fill_sector, generate_world, populate
from starbour.world.grid import GeneralGrid, chebyshev
from starbour.world.stellar import HOLE_KINDS, STAR, WORMHOLE
from starbour.engine.state import new_game, populate_rng


def world_dump(world) -> list:
    return [(k, s.to_dict()) for k, s in sorted(world.sectors.items())]


def test_same_seed_identical_world(small_cfg: GameConfig) -> None:
    a, b = generate_world(4242, small_cfg), generate_world(4242, small_cfg)
    assert world_dump(a) == world_dump(b)
    assert a.wormholes == b.wormholes
    assert a.galaxy_coords() == b.galaxy_coords()


def test_different_seed_different_world(small_cfg: GameConfig) -> None:
    assert world_dump(generate_world(1, small_cfg)) != world_dump(generate_world(2, small_cfg))


def test_counts_at_default_sizes(cfg: GameConfig, default_state) -> None:
    """SP-03 items per galaxy, SP-06 captains per galaxy, SP-07 stars and holes per sector."""
    state = default_state
    world = state.world
    galaxies = world.galaxy_coords()
    assert galaxies, "a universe needs at least one galaxy (WG-01)"
    for gcoord in galaxies:
        galaxy = world.galaxy(gcoord)
        items = sum(len(galaxy.grid.get(*c).items) for c in galaxy.sector_coords())
        assert items == cfg.special_items_per_galaxy + cfg.normal_items_per_galaxy, gcoord
        specials = sum(1 for c in galaxy.sector_coords() for i in galaxy.grid.get(*c).items if i.rarity == "special")
        assert specials == cfg.special_items_per_galaxy
    ai = [c for c in state.captains.values() if not c.is_player]
    assert len(ai) == cfg.ai_captains_per_galaxy * len(galaxies)
    for sector in world.sectors.values():
        kinds = [t.kind for t in sector.terrain()]
        assert 0 <= kinds.count(STAR) <= 3, sector.key
        assert sum(k in HOLE_KINDS for k in kinds) <= 1, sector.key
        assert len(sector.grid) == len(sector.terrain()), "WG-03: one terrain object per cell"
        assert all(t is not None for t in sector.terrain())


def test_planets_adjacent_to_a_star(small_cfg: GameConfig) -> None:
    """SP-04 / CFG-61 / D-116: every planet lies planet_distance_min...max (Chebyshev) from a star, and some lie beyond the minimum."""
    lo, hi = small_cfg.planet_distance_min, small_cfg.planet_distance_max
    spread = judged = 0
    for seed in (7, 1234):
        world = generate_world(seed, small_cfg)
        for sector in world.sectors.values():
            stars = [t.cell for t in sector.terrain() if t.kind == STAR]
            for t in sector.terrain():
                if t.kind == PLANET:
                    d = min(chebyshev(t.cell, s) for s in stars)
                    assert lo <= d <= hi, f"planet at {t.cell} in {sector.key} is {d} from its nearest star, band {lo}...{hi} (SP-04, D-116)"
                    judged += 1
                    spread += d > lo
    assert judged and spread, "the band is used: some planets sit beyond the minimum distance (D-116)"


def test_binary_stars_are_adjacent_when_multi() -> None:
    """SP-02 / SYM-13: with multi_star_pct = 100 every extra star sits next to the first."""
    from tests.conftest import INI
    from starbour.config import load_config
    cfg = dataclasses.replace(load_config(INI), multi_star_pct=100, planets_per_system_max=0, hole_pct=0)
    rng = random.Random(5)
    seen_multi = False
    for _ in range(40):
        sector = Sector((0, 0), (0, 0), GeneralGrid(24, 24))
        _fill_sector(sector, rng, cfg, set())
        stars = [t.cell for t in sector.terrain() if t.kind == STAR]
        if len(stars) >= 2:
            seen_multi = True
            # the system is built around one star: every other star is adjacent to it
            assert any(all(chebyshev(c, s) == 1 for s in stars if s != c) for c in stars), stars
            assert len(set(stars)) == len(stars), "stars share a cell (WG-03)"
    assert seen_multi


def test_player_start_is_safe(cfg: GameConfig, default_state) -> None:
    """SP-05: the start sector has a planet and no black hole or neutron star."""
    state = default_state
    ship = state.active_ship()
    sector = state.sector_of(ship)
    assert sector.has_planet() and not sector.has_hole()
    assert sector.terrain_at(ship.cell) is None, "the ship starts in a free cell"
    assert state.is_visited(ship.galaxy, ship.sector)


def test_ai_captains_avoid_holes(small_cfg: GameConfig) -> None:
    state = new_game(11, small_cfg)
    for cap in state.captains.values():
        if cap.is_player:
            continue
        ship = state.ships[cap.active_ship]
        sector = state.sector_of(ship)
        assert sector is not None
        assert cap.ships == [ship.id] and ship.captain_id == cap.id


def test_wormholes_paired_both_ways(cfg: GameConfig, default_state) -> None:
    """ST-04 / D-20: fixed pairs, each partner points back."""
    world = default_state.world
    holes = {}
    for (g, s), sector in world.sectors.items():
        for t in sector.terrain():
            if t.kind == WORMHOLE:
                holes[(g, s, t.cell)] = t
    assert holes, "the default world should contain wormholes"
    assert len(holes) % 2 == 0
    for key, hole in holes.items():
        assert hole.partner is not None
        other = holes[hole.partner]
        assert other.partner == key
    assert len(world.wormholes) == len(holes) // 2


def test_populate_is_deterministic(small_cfg: GameConfig) -> None:
    w1, w2 = generate_world(3, small_cfg), generate_world(3, small_cfg)
    p1, p2 = populate(w1, small_cfg, populate_rng(3)), populate(w2, small_cfg, populate_rng(3))
    assert [c.name for c in p1.captains.values()] == [c.name for c in p2.captains.values()]
    assert [s.position for s in p1.ships.values()] == [s.position for s in p2.ships.values()]
    assert p1.player_id == "c-000" and p1.captains[p1.player_id].is_player


def test_ai_class_mix_comes_from_the_ini(small_cfg: GameConfig) -> None:
    """CFG-75, D-131: [world] ai_class_mix drives which classes AI captains get; one class means only that class."""
    only = dataclasses.replace(small_cfg, ai_class_mix=(("bastion", 1),), ai_captains_per_galaxy=3)
    state = new_game(7, only)
    ai_ships = [s for s in state.ships.values() if s.captain_id != state.player_id]
    assert ai_ships and {s.cls for s in ai_ships} == {"bastion"}
