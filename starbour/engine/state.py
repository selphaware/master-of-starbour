"""GameState, snapshot(), from_snapshot(), snapshot_hash() - BUS-04, BUS-07, C3, C6, C9, D-67, D-87.

The engine is the only owner of this object (BUS-04).  ``snapshot()`` is the full
dynamic state plus the terrain and items of every sector the player has visited; the
static terrain of the rest is regenerated from the seed on load (D-87).  Everything is
emitted in sorted order so the canonical JSON, and therefore the hash, is stable (D-67).
M2 adds per-ship ``hazard`` and ``move_remaining`` (derived, C9) and ``game_over``.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field

from starbour import __version__
from starbour.commands.parse import Order
from starbour.config import GameConfig
from starbour.engine import hazards as H
from starbour.entities.captain import Captain
from starbour.entities.item import Item
from starbour.entities.ship import Position, Ship
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
    player_mutinies: int = 0                         # for the game.over summary (C9)
    last_stats: dict = field(default_factory=dict)   # the last resolved turn's counters (not part of the snapshot)

    # ---- lookups
    @property
    def player(self) -> Captain:
        return self.captains[self.player_id]

    @property
    def game_over(self) -> bool:
        """OV-09b: the player's active ship was destroyed; only 'new' and 'quit' are accepted (CMD-19, D-105)."""
        return self.game_over_turn is not None

    def active_ship(self, captain_id: str | None = None) -> Ship | None:
        cap = self.captains.get(captain_id or self.player_id)
        if cap is None or cap.active_ship is None:
            return None
        return self.ships.get(cap.active_ship)

    def sector_of(self, ship: Ship) -> Sector | None:
        return self.world.sector(ship.galaxy, ship.sector)

    def terrain_of(self, ship: Ship) -> list:
        sector = self.sector_of(ship)
        return sector.terrain() if sector is not None else []

    def is_visited(self, galaxy: Coord, sector: Coord) -> bool:
        return sector in self.visited.get(galaxy, set())

    def mark_visited(self, galaxy: Coord, sector: Coord) -> None:
        self.visited.setdefault(galaxy, set()).add(sector)

    def sectors_visited(self) -> int:
        return sum(len(v) for v in self.visited.values())

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

    # ---- hazards and persisting moves (C9, derived from the world and the queues)
    def hazard_of(self, ship: Ship) -> H.Hazard | None:
        """The hazard governing the ship's cell (HZ-01...06, D-108), or None when clear."""
        return H.strongest(self.terrain_of(ship), ship.cell, self.cfg)

    def pull_at(self, ship: Ship) -> float:
        """DQ-05 pull netted against the ship's impulse and warp at its current cell (MV-01, MV-03)."""
        return H.pull_at(self.terrain_of(ship), ship.cell, self.cfg)

    def hazard_dict(self, ship: Ship) -> dict | None:
        """C9 ``hazard``: kind, cell, distance, pull, turns_left (D-100 from the ship's counter), drain."""
        hz = self.hazard_of(ship)
        if hz is None:
            return None
        drain = H.drain_at(self.terrain_of(ship), ship.cell, self.cfg)
        d = hz.to_dict(hz.turns_left(ship.hazard_turns, self.cfg))
        d["drain"] = int(drain)
        return d

    def persisting_move(self, ship_id: str) -> Order | None:
        """The ship's persisting move, if any (CMD-06, D-98)."""
        for o in self.queues.get(ship_id, []):
            if o.command == "move" and o.persisting:
                return o
        return None

    def move_remaining(self, ship_id: str) -> list[int] | None:
        """C9 ``move_remaining``: [dx, dy] still to travel, or None."""
        o = self.persisting_move(ship_id)
        if o is None:
            return None
        try:
            return [int(o.args[0]), int(o.args[1])]
        except (IndexError, ValueError):
            return None

    # ---- fleet changes (FL-04, D-105)
    def remove_ship(self, ship_id: str) -> tuple[Captain | None, bool]:
        """Remove a destroyed ship; returns (its captain, whether it was that captain's active ship).

        An AI captain left with no ship is removed the same turn (D-105); the player's captain
        stays (game over, OV-09b) with an empty fleet.  Queues for the ship are dropped.
        """
        ship = self.ships.pop(ship_id, None)
        self.queues.pop(ship_id, None)
        if ship is None:
            return None, False
        cap = self.captains.get(ship.captain_id)
        if cap is None:
            return None, False
        was_active = cap.active_ship == ship_id
        cap.ships = [s for s in cap.ships if s != ship_id]
        if was_active:
            cap.active_ship = cap.ships[0] if cap.ships else None
        if not cap.ships and not cap.is_player:
            del self.captains[cap.id]
        return cap, was_active

    def teleport(self, position: Position) -> Ship:
        """D-104 debug: place the player's active ship before turn 1 (C9 ``--teleport``); a terrain cell is allowed."""
        ship = self.active_ship()
        if ship is None:
            raise ValueError("--teleport: the player has no active ship")
        galaxy, sector, cell = position
        target = self.world.sector(galaxy, sector)
        if target is None:
            raise ValueError(f"--teleport: no sector at galaxy {galaxy[0]},{galaxy[1]} sector {sector[0]},{sector[1]} (Void or outside the universe)")
        if not target.grid.in_bounds(cell[0], cell[1]):
            raise ValueError(f"--teleport: cell {cell[0]},{cell[1]} is outside the {target.grid.width}x{target.grid.height} sector")
        ship.position = (galaxy, sector, cell)
        ship.hazard_turns = 0
        ship.hazard_key = None
        self.mark_visited(galaxy, sector)
        return ship

    # ---- snapshot (C3, C9)
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
        ships: dict[str, dict] = {}
        for sid in sorted(self.ships):
            ship = self.ships[sid]
            ships[sid] = ship.to_dict([o.to_dict() for o in self.queues.get(sid, [])], self.hazard_dict(ship), self.move_remaining(sid))
        return {
            "turn_no": self.turn_no,
            "seed": self.seed,
            "version": __version__,
            "sizes": self.sizes(),
            "player_id": self.player_id,
            "captains": {cid: self.captains[cid].to_dict(self.cfg) for cid in sorted(self.captains)},
            "ships": ships,
            "universe": {"galaxies": [[c[0], c[1]] for c in self.world.galaxy_coords()]},
            "galaxies": galaxies,
            "sectors": sectors,
            "fog_of_war": self.cfg.fog_of_war,
            "view": dict(self.view),
            "counters": {"captain": self.next_captain_no, "ship": self.next_ship_no},
            "game_over_turn": self.game_over_turn,
            "mastery_turn": self.mastery_turn,
            "game_over": self.game_over,
            "player_mutinies": self.player_mutinies,
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
            queues[sid] = [Order(o.command, o.args, sid, o.kind, o.persisting) for o in orders]
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
        player_mutinies=int(snap.get("player_mutinies") or 0),
    )
