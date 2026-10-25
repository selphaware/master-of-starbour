"""GameState, snapshot(), from_snapshot(), snapshot_hash() - BUS-04, BUS-07, C3, C6, D-67, D-87.

The engine is the only owner of this object (BUS-04).  ``snapshot()`` is the full
dynamic state plus the terrain and items of every sector the player has visited; the
static terrain of the rest is regenerated from the seed on load (D-87).  Everything is
emitted in sorted order so the canonical JSON, and therefore the hash, is stable (D-67).
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field

from starbour import __version__
from starbour.commands.parse import Order
from starbour.config import GameConfig
from starbour.entities.captain import Captain
from starbour.entities.item import Item
from starbour.entities.ship import Ship
from starbour.world.generate import Sector, World, galaxy_key, generate_world, populate, sector_key
from starbour.world.grid import Coord


def canonical_json(obj: object) -> bytes:
    """C2: sorted keys, no whitespace, ASCII-escaped."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def hash_snapshot(snapshot: dict) -> str:
    """SHA-1 of the canonical JSON (C2 ``snapshot_hash``)."""
    return hashlib.sha1(canonical_json(snapshot)).hexdigest()


def populate_rng(seed: int) -> random.Random:
    """The captain-placement stream, separate from terrain so from_snapshot() can rebuild terrain alone (D-87)."""
    return random.Random(f"starbour-populate-{seed}")


@dataclass(slots=True)
class GameState:
    """Everything the engine knows.  Only ``starbour.engine`` mutates it."""

    cfg: GameConfig
    seed: int
    world: World
    captains: dict[str, Captain]
    ships: dict[str, Ship]
    player_id: str
    turn_no: int = 0
    visited: dict[Coord, set[Coord]] = field(default_factory=dict)
    view: dict = field(default_factory=lambda: {"level": "follow"})
    next_captain_no: int = 1
    next_ship_no: int = 1
    queues: dict[str, list[Order]] = field(default_factory=dict)
    game_over_turn: int | None = None
    mastery_turn: int | None = None
    last_stats: dict = field(default_factory=dict)   # the last resolved turn's counters (not part of the snapshot)

    # ---- lookups
    @property
    def player(self) -> Captain:
        return self.captains[self.player_id]

    def active_ship(self, captain_id: str | None = None) -> Ship | None:
        cap = self.captains.get(captain_id or self.player_id)
        if cap is None or cap.active_ship is None:
            return None
        return self.ships.get(cap.active_ship)

    def sector_of(self, ship: Ship) -> Sector | None:
        return self.world.sector(ship.galaxy, ship.sector)

    def is_visited(self, galaxy: Coord, sector: Coord) -> bool:
        return sector in self.visited.get(galaxy, set())

    def mark_visited(self, galaxy: Coord, sector: Coord) -> None:
        self.visited.setdefault(galaxy, set()).add(sector)

    def new_captain_id(self) -> str:
        cid = f"c-{self.next_captain_no:03d}"
        self.next_captain_no += 1
        return cid

    def new_ship_id(self) -> str:
        sid = f"s-{self.next_ship_no:03d}"
        self.next_ship_no += 1
        return sid

    def sizes(self) -> dict:
        return {"universe": list(self.cfg.universe_size), "galaxy": list(self.cfg.galaxy_size), "sector": list(self.cfg.sector_size)}

    # ---- snapshot (C3)
    def snapshot(self) -> dict:
        """The C3 snapshot: full dynamic state plus visited sectors' terrain and items (D-87)."""
        galaxies: dict[str, dict] = {}
        for gcoord in self.world.galaxy_coords():
            static = self.world.static_galaxies[galaxy_key(gcoord)]
            galaxies[galaxy_key(gcoord)] = {
                "sectors": static["sectors"],
                "hazards": static["hazards"],
                "visited": [[c[0], c[1]] for c in sorted(self.visited.get(gcoord, ()))],
            }
        sectors: dict[str, dict] = {}
        for gcoord in sorted(self.visited):
            for scoord in sorted(self.visited[gcoord]):
                sector = self.world.sector(gcoord, scoord)
                if sector is not None:
                    sectors[sector_key(gcoord, scoord)] = sector.to_dict()
        return {
            "turn_no": self.turn_no,
            "seed": self.seed,
            "version": __version__,
            "sizes": self.sizes(),
            "player_id": self.player_id,
            "captains": {cid: self.captains[cid].to_dict(self.cfg) for cid in sorted(self.captains)},
            "ships": {sid: self.ships[sid].to_dict([o.to_dict() for o in self.queues.get(sid, [])]) for sid in sorted(self.ships)},
            "universe": {"galaxies": [[c[0], c[1]] for c in self.world.galaxy_coords()]},
            "galaxies": galaxies,
            "sectors": sectors,
            "fog_of_war": self.cfg.fog_of_war,
            "view": dict(self.view),
            "counters": {"captain": self.next_captain_no, "ship": self.next_ship_no},
            "game_over_turn": self.game_over_turn,
            "mastery_turn": self.mastery_turn,
        }

    def snapshot_hash(self) -> str:
        """D-79: the hash the gates compare (C2, C6)."""
        return hash_snapshot(self.snapshot())


def snapshot_hash(state: GameState) -> str:
    """Module-level alias (D-79)."""
    return state.snapshot_hash()


def new_game(seed: int, cfg: GameConfig) -> GameState:
    """Generate a fresh universe and its captains for ``seed`` - SP-01, SP-05, SP-06."""
    world = generate_world(seed, cfg)
    pop = populate(world, cfg, populate_rng(seed))
    state = GameState(cfg, seed, world, pop.captains, pop.ships, pop.player_id,
                      next_captain_no=pop.next_captain_no, next_ship_no=pop.next_ship_no)
    ship = state.active_ship()
    if ship is not None:
        state.mark_visited(ship.galaxy, ship.sector)
    return state


def _parse_coord(text: str) -> Coord:
    a, b = text.split(",")
    return int(a), int(b)


def from_snapshot(snap: dict, cfg: GameConfig) -> GameState:
    """Rebuild a state whose ``snapshot()`` equals ``snap`` - C6 round trip (tests/test_save.py)."""
    seed = int(snap["seed"])
    world = generate_world(seed, cfg)
    captains = {cid: Captain.from_dict(cid, d) for cid, d in sorted(snap["captains"].items())}
    ships = {sid: Ship.from_dict(sid, d) for sid, d in sorted(snap["ships"].items())}
    queues: dict[str, list[Order]] = {}
    for sid, d in sorted(snap["ships"].items()):
        orders = [Order.from_dict(o) for o in d.get("orders") or []]
        if orders:
            queues[sid] = [Order(o.command, o.args, sid, o.kind) for o in orders]
    visited: dict[Coord, set[Coord]] = {}
    for gkey, g in snap.get("galaxies", {}).items():
        cells = {(int(c[0]), int(c[1])) for c in g.get("visited") or []}
        if cells:
            visited[_parse_coord(gkey)] = cells
    for key, sec in snap.get("sectors", {}).items():
        gtext, stext = key.split("/")
        sector = world.sector(_parse_coord(gtext), _parse_coord(stext))
        if sector is not None:
            sector.items = [Item.from_dict(i) for i in sec.get("items") or []]
    counters = snap.get("counters") or {}
    return GameState(
        cfg, seed, world, captains, ships, str(snap["player_id"]),
        turn_no=int(snap["turn_no"]), visited=visited, view=dict(snap.get("view") or {"level": "follow"}),
        next_captain_no=int(counters.get("captain") or (len(captains) + 1)),
        next_ship_no=int(counters.get("ship") or (len(ships) + 1)),
        queues=queues, game_over_turn=snap.get("game_over_turn"), mastery_turn=snap.get("mastery_turn"),
    )
