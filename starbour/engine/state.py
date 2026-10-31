"""GameState, snapshot(), from_snapshot(), snapshot_hash() - BUS-04, BUS-07, C3, C6, C9, C10, C11, D-67, D-87.

The engine is the only owner of this object (BUS-04).  ``snapshot()`` is the full
dynamic state plus the terrain and items of every sector the player has visited; the
static terrain of the rest is regenerated from the seed on load (D-87).  Everything is
emitted in sorted order so the canonical JSON, and therefore the hash, is stable (D-67).
M2 adds per-ship ``hazard`` and ``move_remaining`` (derived, C9) and ``game_over``; M3 adds
open ``battles``, per-ship ``in_battle`` / ``target`` and per-captain ``visible_reputation``
(C10), the debug ``--spawn`` placement (D-126) and the mutiny placement rule (D-115, D-129).
M4 adds ``planets`` (C11): the records of every planet whose dynamic state no longer
follows the seed (``touched``), every planet in an open battle and the planet in the
player's active ship's cell - the rest regenerate from the seed (D-150 [ASSUMED]) - plus the
debug ``--give`` / ``--coins`` / ``--respect`` / ``--fear`` setters.
"""
from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field

from starbour import __version__
from starbour.commands.parse import Order
from starbour.config import GameConfig
from starbour.engine import hazards as H
from starbour.entities.battle import Battle
from starbour.entities.captain import Captain, clamp_level
from starbour.entities.item import Item
from starbour.entities.planet import Planet
from starbour.entities.ship import Position, Ship
from starbour.world import names
from starbour.world.generate import Sector, World, galaxy_key, generate_world, populate, sector_key
from starbour.world.grid import Coord, chebyshev, manhattan


def canonical_json(obj: object) -> bytes:
    """C2: sorted keys, no whitespace, ASCII-escaped."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def hash_snapshot(snapshot: dict) -> str:
    """SHA-1 of the canonical JSON (C2 ``snapshot_hash``)."""
    return hashlib.sha1(canonical_json(snapshot)).hexdigest()


def populate_rng(seed: int) -> random.Random:
    """The captain-placement stream, separate from terrain so from_snapshot() can rebuild terrain alone (D-87)."""
    return random.Random(f"starbour-populate-{seed}")


def spawn_rng(seed: int, n: int) -> random.Random:
    """D-126: names for a spawned captain come from their own stream so a spawn never shifts the game RNG (D-67)."""
    return random.Random(f"starbour-spawn-{seed}-{n}")


def is_planet_id(text: str) -> bool:
    return isinstance(text, str) and text.startswith("p-")


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
    next_battle_no: int = 1
    queues: dict[str, list[Order]] = field(default_factory=dict)
    battles: dict[str, Battle] = field(default_factory=dict)
    game_over_turn: int | None = None
    mastery_turn: int | None = None
    player_mutinies: int = 0                         # for the game.over summary (C9)
    spawned: list[dict] = field(default_factory=list)   # C10: the ships --spawn created (D-126)
    pending_reputation: list[dict] = field(default_factory=list)   # FR-06 rows from fires accepted between turns, applied at the next TP-07
    touched_planets: set[str] = field(default_factory=set)         # C11: planets whose dynamic state left the seed (D-150)
    hazard_note_sector: Coord | None = None          # D-127: 'planet cells are safe' once per sector entry (not saved)
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

    def sector_of(self, ship) -> Sector | None:
        return self.world.sector(ship.galaxy, ship.sector)

    def terrain_of(self, ship) -> list:
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

    def new_battle_id(self) -> str:
        bid = f"b-{self.next_battle_no:03d}"
        self.next_battle_no += 1
        return bid

    def sizes(self) -> dict:
        return {"universe": list(self.cfg.universe_size), "galaxy": list(self.cfg.galaxy_size), "sector": list(self.cfg.sector_size)}

    def ships_by_sector(self) -> dict[tuple[Coord, Coord], list[Ship]]:
        """Every ship grouped by (galaxy, sector), ids sorted - built once per phase so the AI never scans every pair."""
        out: dict[tuple[Coord, Coord], list[Ship]] = {}
        for sid in sorted(self.ships):
            s = self.ships[sid]
            out.setdefault((s.galaxy, s.sector), []).append(s)
        return out

    # ---- planets (C11, CM-11, D-140, D-145)
    def planet(self, planet_id: str) -> Planet | None:
        return self.world.planets.get(planet_id)

    def live_planet(self, planet_id: str) -> Planet | None:
        p = self.world.planets.get(planet_id)
        return p if p is not None and not p.destroyed else None

    def planet_at(self, galaxy: Coord, sector: Coord, cell: Coord) -> Planet | None:
        s = self.world.sector(galaxy, sector)
        return s.planet_at(cell) if s is not None else None

    def planets_in(self, galaxy: Coord, sector: Coord) -> list[Planet]:
        s = self.world.sector(galaxy, sector)
        return s.planets() if s is not None else []

    def find_planet(self, text: str, galaxy: Coord | None = None, sector: Coord | None = None) -> Planet | None:
        """A live planet by id (D-148 (6)) or exact name, optionally only in one sector."""
        wanted = text.strip().lower()
        p = self.world.planets.get(text.strip())
        if p is None:
            pool = self.planets_in(galaxy, sector) if galaxy is not None and sector is not None else self.world.planets.values()
            p = next((x for x in pool if not x.destroyed and x.name.lower() == wanted), None)
        if p is None or p.destroyed:
            return None
        if galaxy is not None and (p.galaxy != galaxy or p.sector != sector):
            return None
        return p

    def touch_planet(self, planet: Planet) -> None:
        self.touched_planets.add(planet.id)

    def destroy_planet(self, planet: Planet) -> None:
        """CM-11 / D-145: the planet leaves the map (its cell becomes Void) and every index; the record stays with destroyed = true."""
        planet.destroyed = True
        planet.shield_current = 0.0
        sector = self.world.sector(planet.galaxy, planet.sector)
        if sector is not None and sector.grid.get(planet.cell[0], planet.cell[1]) is planet:
            sector.grid.remove(planet.cell[0], planet.cell[1])
        self.touched_planets.add(planet.id)
        for cap in self.captains.values():
            if cap.restock_target == planet.id:
                cap.restock_target = None

    def combatant(self, text: str):
        """A ship or a live planet by id (CM-11: planets fight with the ship rules)."""
        s = self.ships.get(text)
        if s is not None:
            return s
        return self.live_planet(text)

    def side(self, side_id: str):
        """The holder behind a battle side: a Captain or a Planet (None when gone)."""
        cap = self.captains.get(side_id)
        if cap is not None:
            return cap
        return self.world.planets.get(side_id)

    def side_name(self, side_id: str) -> str:
        holder = self.side(side_id)
        return holder.name if holder is not None else side_id

    def trades_with_player(self, planet: Planet) -> bool:
        """EF-04 / PR-05 / TR-10 for the player's current respect."""
        cap = self.player
        return not planet.destroyed and (bool(cap.is_master) or int(cap.respect) >= int(self.cfg.planet_refuse_respect))

    def docked_planet(self) -> Planet | None:
        """The live planet in the player's active ship's cell (PR-04; the T4 panel and heartbeat)."""
        ship = self.active_ship()
        if ship is None:
            return None
        return self.planet_at(ship.galaxy, ship.sector, ship.cell)

    # ---- battles (C10)
    def battle_of_ship(self, ship) -> Battle | None:
        """The open battle the ship's (or planet's) side is in within its sector (``ships[*].in_battle``)."""
        for bid in sorted(self.battles):
            bt = self.battles[bid]
            if bt.open and bt.involves(ship.captain_id) and bt.galaxy == ship.galaxy and bt.sector == ship.sector:
                return bt
        return None

    def player_in_battle(self) -> bool:
        return any(bt.open and bt.involves(self.player_id) for bt in self.battles.values())

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
            if o.command in ("move", "escape") and o.persisting:
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

    # ---- debug affordances before turn 1 (C9, C10, C11)
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

    def spawn(self, class_name: str, position: Position, fear: int | None = None, respect: int | None = None) -> tuple[Captain, Ship]:
        """D-126 debug ``--spawn``: an AI captain with one ship of ``class_name`` at ``position`` before turn 1 (C10 ``spawned``)."""
        spec = self.cfg.ship_class(class_name)
        galaxy, sector, cell = position
        target = self.world.sector(galaxy, sector)
        if target is None:
            raise ValueError(f"--spawn: no sector at galaxy {galaxy[0]},{galaxy[1]} sector {sector[0]},{sector[1]} (Void or outside the universe)")
        if not target.grid.in_bounds(cell[0], cell[1]):
            raise ValueError(f"--spawn: cell {cell[0]},{cell[1]} is outside the {target.grid.width}x{target.grid.height} sector")
        rng = spawn_rng(self.seed, len(self.spawned) + 1)
        used_c = {c.name for c in self.captains.values()}
        used_s = {s.name for s in self.ships.values()}
        cid, sid = self.new_captain_id(), self.new_ship_id()
        cap = Captain.new(cid, names.unique(names.captain_name, rng, used_c), False, self.cfg, fear=fear, respect=respect)
        ship = Ship.of_class(sid, names.unique(names.ship_name, rng, used_s), spec, cid, (galaxy, sector, cell))
        cap.ships = [sid]
        cap.active_ship = sid
        self.captains[cid] = cap
        self.ships[sid] = ship
        self.spawned.append({"ship": sid, "captain": cid, "class": spec.name, "name": ship.name, "galaxy": [galaxy[0], galaxy[1]],
                             "sector": [sector[0], sector[1]], "cell": [cell[0], cell[1]], "fear": cap.fear, "respect": cap.respect})
        return cap, ship

    def give(self, items: dict[str, int]) -> None:
        """C11 debug ``--give``: set the player's element stacks before turn 1 (adds to what is held)."""
        for element, units in items.items():
            if element not in self.cfg.elements:
                raise ValueError(f"--give: unknown element {element!r}; known: {sorted(self.cfg.elements)} (CFG-42)")
            self.player.add_item(element, int(units))

    def set_coins(self, gold: int, silver: int) -> None:
        """C11 debug ``--coins``."""
        if gold < 0 or silver < 0:
            raise ValueError("--coins: gold and silver cannot be negative (TR-01)")
        self.player.gold, self.player.silver = int(gold), int(silver)

    def set_levels(self, respect: int | None = None, fear: int | None = None) -> None:
        """C11 debug ``--respect`` / ``--fear`` (clamped 0...100, LV-01)."""
        if respect is not None:
            self.player.respect = clamp_level(respect)
        if fear is not None:
            self.player.fear = clamp_level(fear)

    def mutiny_cell(self, ship: Ship, new_engine: float) -> Coord:
        """SUP-04b / D-115 / D-129: where a demoted captain's new ship appears.

        The nearest free cell outside every pull range (the same cell when it is safe); when the whole
        sector is in a hole's range, the nearest cell where the new ship can move (floor(engine - pull)
        >= 1); failing that, the cell farthest from the strongest hazard.  Nearest = Chebyshev, then
        Manhattan, then x, then y (D-113 ordering, D-67).
        """
        sector = self.sector_of(ship)
        if sector is None:
            return ship.cell
        terrain = sector.terrain()
        here = ship.cell
        free = [c for c in sector.grid.free_cells()] or [here]

        def key(c: Coord) -> tuple[int, int, int, int]:
            return (chebyshev(c, here), manhattan(c, here), c[0], c[1])

        safe = [c for c in free if H.strongest(terrain, c, self.cfg) is None]
        if safe:
            return min(safe, key=key)
        movable = [c for c in free if math.floor(float(new_engine) - H.pull_at(terrain, c, self.cfg) + 1e-9) >= 1]
        if movable:
            return min(movable, key=key)
        hz = H.strongest(terrain, here, self.cfg)
        anchor = hz.cell if hz is not None else here
        return max(free, key=lambda c: (chebyshev(c, anchor), manhattan(c, anchor), -c[0], -c[1]))

    # ---- snapshot (C3, C9, C10, C11)
    def planet_records(self) -> dict[str, dict]:
        """C11 ``planets``: the touched planets, every planet in an open battle, and the planet under the player's active ship (D-150)."""
        ids = set(self.touched_planets)
        for bt in self.battles.values():
            if bt.open:
                ids.update(s for s in bt.sides if is_planet_id(s))
        docked = self.docked_planet()
        if docked is not None:
            ids.add(docked.id)
        player = self.player
        disp = int(round(2 * player.respect - 100))
        out: dict[str, dict] = {}
        for pid in sorted(ids):
            p = self.world.planets.get(pid)
            if p is None:
                continue
            bt = self.battle_of_ship(p) if not p.destroyed else None
            out[pid] = p.record(self.cfg, self.turn_no, disposition=disp, trades=self.trades_with_player(p), in_battle=bt.id if bt else None)
        return out

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
            bt = self.battle_of_ship(ship)
            ships[sid] = ship.to_dict([o.to_dict() for o in self.queues.get(sid, [])], self.hazard_dict(ship), self.move_remaining(sid),
                                      bt.id if bt is not None else None)
        return {
            "turn_no": self.turn_no,
            "seed": self.seed,
            "version": __version__,
            "sizes": self.sizes(),
            "player_id": self.player_id,
            "captains": {cid: self.captains[cid].to_dict(self.cfg, self.turn_no) for cid in sorted(self.captains)},
            "ships": ships,
            "universe": {"galaxies": [[c[0], c[1]] for c in self.world.galaxy_coords()]},
            "galaxies": galaxies,
            "sectors": sectors,
            "planets": self.planet_records(),
            "fog_of_war": self.cfg.fog_of_war,
            "view": dict(self.view),
            "counters": {"captain": self.next_captain_no, "ship": self.next_ship_no, "battle": self.next_battle_no},
            "battles": [self.battles[bid].to_dict() for bid in sorted(self.battles) if self.battles[bid].open],
            "game_over_turn": self.game_over_turn,
            "mastery_turn": self.mastery_turn,
            "game_over": self.game_over,
            "player_mutinies": self.player_mutinies,
            "spawned": [dict(s) for s in self.spawned],
            "pending_reputation": [dict(r) for r in self.pending_reputation],
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
    turn_no = int(snap["turn_no"])
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
            # C11 / D-141: the saved list says which stacks remain and which are revealed; element and units come from the seed
            saved = {str(i.get("id")): i for i in (sec.get("items") or []) if isinstance(i, dict)}
            by_id = {i.id: i for i in sector.items}
            merged: list[Item] = []
            for iid, raw in saved.items():
                item = by_id.get(iid)
                if item is None:
                    item = Item.from_dict(raw)
                item.revealed = bool(raw.get("revealed", False))
                merged.append(item)
            sector.items = merged
    touched: set[str] = set()
    for pid, rec in sorted((snap.get("planets") or {}).items()):
        planet = world.planets.get(pid)
        if planet is None or not isinstance(rec, dict):
            continue
        planet.apply_record(rec, turn_no)
        touched.add(pid)
        if planet.destroyed:
            sector = world.sector(planet.galaxy, planet.sector)
            if sector is not None and sector.grid.get(planet.cell[0], planet.cell[1]) is planet:
                sector.grid.remove(planet.cell[0], planet.cell[1])
    counters = snap.get("counters") or {}
    battles = {}
    for b in snap.get("battles") or []:
        bt = Battle.from_dict(b)
        battles[bt.id] = bt
    return GameState(
        cfg, seed, world, captains, ships, str(snap["player_id"]),
        turn_no=turn_no, visited=visited, view=dict(snap.get("view") or {"level": "follow"}),
        next_captain_no=int(counters.get("captain") or (len(captains) + 1)),
        next_ship_no=int(counters.get("ship") or (len(ships) + 1)),
        next_battle_no=int(counters.get("battle") or (len(battles) + 1)),
        queues=queues, battles=battles, game_over_turn=snap.get("game_over_turn"), mastery_turn=snap.get("mastery_turn"),
        player_mutinies=int(snap.get("player_mutinies") or 0), spawned=[dict(s) for s in snap.get("spawned") or []],
        pending_reputation=[dict(r) for r in snap.get("pending_reputation") or []], touched_planets=touched,
    )
