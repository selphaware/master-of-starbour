"""AI captains' and planets' decisions - OV-10, SP-06, CM-10, EF-01, EF-02, EF-05, CFG-29/30/31/44/72/73/81, D-103, D-125, D-144, D-145.

``decide`` is a pure function of (captain, state, rng, cfg); it draws only from the
engine's game RNG (D-67) and never from the global ``random``.  Per ship, in this order:

* in a battle (D-128: it holds its cell unless escaping): when weaker, surrender > escape >
  accept a standing offer > offer peace (EF-02); then fire both weapons at the nearest enemy
  in range (D-120); outgunned by a planet it leaves the planet's range instead (planets cannot
  pursue, CM-11; D-150);
* the active ship on a restock trip (D-144): with food or water below ``buy_supplies_below``
  it heads for the nearest planet that will trade with it (its own respect vs
  ``planet_refuse_respect``) and holds no hole - in-sector by impulse, else by a warp to the
  nearest sector with one - docks in orbit (the free cell next to the planet, D-150) and stays
  until both supplies are back at their starts;
* not passive and an enemy in weapon range with ratio >= attack_ratio_min and fear <
  attack_fear_max: open fire (CM-10, EF-01) - unless the FR-06 cost would drop its respect
  below ``planet_refuse_respect`` (an AI that could no longer eat does not pick fights, D-150);
* every roam_every_turns turns the active ship warps to a random reachable sector that holds
  no black hole or neutron star (CFG-73; 0 disables);
* otherwise the M2 hazard-aware random walk: never ends on a star, hole or planet cell, never
  inside the opening range of a planet that would fire on it (EF-05); inside a hole's pull
  range the ship moves directly away at full power; every move is planned within one turn's
  reach so it never persists (D-103, D-111).

``--ai passive`` (D-125, D-132) removes every initiative - no opening, no roaming, no wandering,
no restock trips, and planets never open either - so a spawned opponent stays where the gate put
it; it still fires back, offers peace, escapes, surrenders and flees a hole.

Planets (``planet_orders``, D-145, D-150): in a battle a planet fires both weapons at the nearest
enemy in range and accepts a standing peace offer when it is the weaker side; otherwise (EF-05,
not passive) it opens on the nearest ship within its phaser range (point-blank, CFG-24) whose
captain's respect is below ``planet_attack_respect_max`` when it is at least ``attack_ratio_min``
stronger.  Planets never move, offer peace or surrender.

Every choice can be traced: pass a list as ``trace`` and the functions append one ``Decision`` per
combatant with the reason, the rule IDs, its sector and a ``kind`` the gameplay log filters on
(``[logging] ai_trace``, CFG-74).  Tracing reads nothing new and draws no random number (D-67).
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from starbour.commands.parse import QUEUED, Order
from starbour.config import GameConfig
from starbour.engine import combat as C
from starbour.engine import economy as E
from starbour.engine import hazards as H
from starbour.engine import movement as MV
from starbour.engine.state import GameState
from starbour.entities.battle import Battle
from starbour.entities.captain import Captain
from starbour.entities.planet import Planet
from starbour.entities.ship import Ship
from starbour.world.generate import flat_sector
from starbour.world.grid import Coord, chebyshev, manhattan
from starbour.world.stellar import HOLE_KINDS

TRIES = 8
KIND_BATTLE, KIND_ROAM, KIND_WALK, KIND_HOLD, KIND_RESTOCK, KIND_ATTACK = "battle", "roam", "walk", "hold", "restock", "attack"


@dataclass(slots=True)
class Decision:
    """One combatant's choice this turn, with the reason and the rule IDs behind it (gameplay log, M3 step 2; CFG-74 filter)."""

    captain_id: str
    captain_name: str
    ship_id: str
    text: str
    ids: tuple[str, ...]
    kind: str = KIND_WALK
    sector: tuple[Coord, Coord] | None = None

    def to_dict(self) -> dict:
        return {"captain": self.captain_id, "name": self.captain_name, "ship": self.ship_id, "text": self.text, "ids": list(self.ids), "kind": self.kind,
                "sector": [[self.sector[0][0], self.sector[0][1]], [self.sector[1][0], self.sector[1][1]]] if self.sector else None}


def note(trace: list[Decision] | None, captain, ship, text: str, *ids: str, kind: str = KIND_WALK) -> None:
    if trace is not None:
        trace.append(Decision(captain.id, captain.name, ship.id, text, ids, kind, (ship.galaxy, ship.sector)))


def fire_text(orders: list[Order]) -> str:
    """'fires torpedo+phaser at s-000' for the fire orders of one ship."""
    weapons = "+".join(str(o.args[0]) for o in orders)
    return f"fires {weapons} at {orders[0].args[1]}"


def at(cell: Coord) -> str:
    return f"{cell[0]},{cell[1]}"


def away_target(cell: Coord, hole: Coord, cells: int, size: Coord, blocked: set[Coord]) -> Coord | None:
    """D-103 / EF-02: the free cell up to ``cells`` steps directly away from ``hole`` (sign per axis, clipped
    to the sector).  Shortening the step never brings the ship closer (Chebyshev), so the
    distance to the hole is never reduced; None when every candidate is blocked."""
    sx, sy = MV.sign(cell[0] - hole[0]), MV.sign(cell[1] - hole[1])
    if sx == 0 and sy == 0:
        sx = 1
    for n in range(int(cells), 0, -1):
        target = MV.clamp_target((cell[0] + sx * n, cell[1] + sy * n), size)
        if target != tuple(cell) and target not in blocked:
            return target
    return None


def random_target(cell: Coord, cells: int, size: Coord, blocked: set[Coord], rng: random.Random) -> Coord | None:
    """OV-10: a random free cell within this turn's reach (Chebyshev <= cells), never the current cell."""
    w, h = size
    x0, x1 = max(0, cell[0] - cells), min(w - 1, cell[0] + cells)
    y0, y1 = max(0, cell[1] - cells), min(h - 1, cell[1] + cells)
    for _ in range(TRIES):
        target = (rng.randint(x0, x1), rng.randint(y0, y1))
        if target != tuple(cell) and target not in blocked:
            return target
    return None


def captain_index(captain_id: str) -> int:
    try:
        return int(captain_id.split("-")[-1])
    except ValueError:
        return 0


def roam_due(captain: Captain, turn_no: int, cfg: GameConfig) -> bool:
    """CFG-73 / D-132: every roam_every_turns turns from turn roam_every_turns on, staggered by captain so the universe does
    not jump all at once (captain c-NNN roams at the turns t >= every with (t + NNN) % every == 0); 0 = never."""
    every = int(cfg.roam_every_turns)
    return every > 0 and int(turn_no) >= every and (int(turn_no) + captain_index(captain.id)) % every == 0


def _reach_box(ship: Ship, state: GameState, cfg: GameConfig, pull: float) -> tuple[int, Coord] | None:
    """(reach in sectors, the flattened sector coordinates) of a warp from here, or None when the pull nets the factor away."""
    eff = MV.effective_factor(ship.warp, pull)
    if eff <= 0:
        return None
    reach = MV.warp_reach(eff, cfg.warp_curve_base, cfg.warp_curve_exponent)
    uw, uh = cfg.universe_size
    gw, gh = cfg.galaxy_size
    cap = max(uw * gw, uh * gh)
    return int(min(reach + MV.EPS, cap)), flat_sector(ship.galaxy, ship.sector, cfg.galaxy_size)


def roam_destination(ship: Ship, state: GameState, rng: random.Random, cfg: GameConfig, pull: float) -> tuple[Coord, Coord] | None:
    """D-125: a random sector within the warp reach (MV-02, netted by the pull here, HZ-10) that holds no hole.

    Candidates are read from the world's flattened index inside the reach box, in a fixed order, so the
    draw is deterministic (D-67).  None when the pull nets the factor to nothing or nothing is reachable."""
    box = _reach_box(ship, state, cfg, pull)
    if box is None:
        return None
    r, (fx, fy) = box
    here = (ship.galaxy, ship.sector)
    candidates: list[tuple[Coord, Coord]] = []
    for y in range(fy - r, fy + r + 1):
        for x in range(fx - r, fx + r + 1):
            gs = state.world.flat_sectors.get((x, y))
            if gs is None or gs == here:
                continue
            sector = state.world.sectors.get(gs)
            if sector is None or sector.has_hole():
                continue
            candidates.append(gs)
    if not candidates:
        return None
    return candidates[rng.randrange(len(candidates))]


def fire_orders(ship, target, cfg: GameConfig) -> list[Order]:
    """D-120: both weapons at one target - one torpedo while there is ammo and it is in range, one phaser volley in range."""
    d = chebyshev(ship.cell, target.cell)
    out: list[Order] = []
    if ship.torpedoes > 0 and d <= cfg.torpedo_range:
        out.append(Order("fire", ("torpedo", target.id, "1"), ship.id, QUEUED))
    if ship.phasers > 0 and d <= cfg.phaser_range:
        out.append(Order("fire", ("phaser", target.id), ship.id, QUEUED))
    return out


def battle_orders(captain: Captain, ship: Ship, battle: Battle, here: list[Ship], state: GameState, cfg: GameConfig,
                  cells: int, blocked: set[Coord], trace: list[Decision] | None = None) -> list[Order]:
    """EF-02 / CM-10 / D-125 / D-128: an AI ship in battle holds its cell and fires; when weaker it yields in the order
    surrender > escape > accept a standing offer > offer peace.  The enemy may be a captain or a planet (CM-11)."""
    enemy_id = battle.other(captain.id)
    enemy = state.side(enemy_id)
    enemies = C.enemies_in_sector(state, ship, enemy_id)
    if enemy is None or not enemies:
        note(trace, captain, ship, f"in battle {battle.id}, enemy {enemy_id} is not in this sector -> no orders; the battle ends by escape at TP-04", "D-119",
             kind=KIND_BATTLE)
        return []          # the battle ends by escape at TP-04 (D-119)
    nearest = enemies[0]
    own = C.strength([s for s in here if s.captain_id == captain.id], cfg)
    theirs = C.strength(enemies, cfg)
    weaker = own < theirs
    pct = ship.shield_pct
    enemy_fear = int(getattr(enemy, "fear", 0))
    where = f"in battle {battle.id}, {'weaker' if weaker else 'not weaker'} (strength {own:.0f} v {theirs:.0f}, shields {pct:.0f}%)"
    out: list[Order] = []
    if weaker and isinstance(enemy, Planet) and cells > 0:
        # D-150: a planet cannot pursue (CM-11), so a ship it outguns simply leaves its range (CB-02) instead of trading fire
        target = away_target(ship.cell, nearest.cell, cells, cfg.sector_size, blocked)
        if target is not None:
            note(trace, captain, ship, f"{where} against the planet {nearest.id} -> leaves its range: moves {at(ship.cell)} -> {at(target)}", "CM-11", "CB-02", "D-150",
                 kind=KIND_BATTLE)
            return [Order("move", (str(target[0]), str(target[1]), f"{ship.engine:g}"), ship.id, QUEUED)]
    if weaker:
        if getattr(enemy, "is_player", False) and enemy_fear >= cfg.yield_fear_min and pct < cfg.surrender_shield_pct:
            note(trace, captain, ship, f"{where}, {enemy_id} fear {enemy_fear} >= {cfg.yield_fear_min} and shields < {cfg.surrender_shield_pct:g}% -> surrenders to {enemy_id}",
                 "EF-02", "D-128", kind=KIND_BATTLE)
            return [Order(C.SURRENDER, (enemy_id,), ship.id, QUEUED)]
        if pct < cfg.escape_shield_pct and cells > 0:
            target = away_target(ship.cell, nearest.cell, cells, cfg.sector_size, blocked)
            if target is not None:
                note(trace, captain, ship, f"{where}, shields < {cfg.escape_shield_pct:g}% -> escapes: moves from {nearest.id} at {at(nearest.cell)} to {at(target)}",
                     "EF-02", "D-128", kind=KIND_BATTLE)
                return [Order("move", (str(target[0]), str(target[1]), f"{ship.engine:g}"), ship.id, QUEUED)]
        # a ship that cannot flee (held, immobile or boxed in) falls through to peace (D-128: surrender > escape > peace)
        if battle.offer_stands(enemy_id, state.turn_no):
            note(trace, captain, ship, f"{where}, {enemy_id} has a peace offer standing -> accepts it", "EF-02", "D-128", kind=KIND_BATTLE)
            return [Order("peace", ("accept", enemy_id), ship.id, QUEUED)]
        if pct < C.ai_peace_threshold(enemy_fear, cfg) and not battle.offer_stands(captain.id, state.turn_no):
            out.append(Order("peace", ("offer", enemy_id), ship.id, QUEUED))
    shots = fire_orders(ship, nearest, cfg)
    out += shots
    if not weaker:
        stance = "no peace"
    elif out and out[0].command == "peace":
        stance = f"shields {pct:.0f}% < {C.ai_peace_threshold(enemy_fear, cfg):.0f}% (enemy fear {enemy_fear}) -> offers peace"
    else:
        stance = "no peace (shields above the peace threshold or an offer of its own already stands)"
    act = fire_text(shots) if shots else f"cannot fire at {nearest.id} (distance {chebyshev(ship.cell, nearest.cell)}, no weapon in range)"
    note(trace, captain, ship, f"{where} -> {stance}; {act}", *(("EF-02", "CM-10", "D-125", "D-128") if weaker else ("CM-10", "D-125")), kind=KIND_BATTLE)
    return out


# --------------------------------------------------------------------------- planets as a danger (EF-05)

def planet_threatens(planet: Planet, captain: Captain, own_strength: float, cfg: GameConfig) -> bool:
    """EF-05 / CFG-29 / CFG-31: the planet would open on this captain - respect below planet_attack_respect_max and the
    planet at least attack_ratio_min stronger than the captain's ships in the sector."""
    if planet.destroyed or int(captain.respect) >= int(cfg.planet_attack_respect_max):
        return False
    reach = C.weapon_range(planet, cfg)
    if reach <= 0:
        return False
    ratio = C.ship_strength(planet, cfg) / own_strength if own_strength > 0 else float("inf")
    return ratio >= float(cfg.attack_ratio_min)


def hostile_planet_cells(captain: Captain, ship: Ship, here: list[Ship], state: GameState, cfg: GameConfig) -> set[Coord]:
    """EF-05 avoidance (D-150): every cell inside the opening range (phaser_range, CFG-24) of a live planet in the sector that
    would open on this captain."""
    if int(captain.respect) >= int(cfg.planet_attack_respect_max):
        return set()
    own = C.strength([s for s in here if s.captain_id == captain.id], cfg)
    cells: set[Coord] = set()
    w, h = cfg.sector_size
    reach = int(cfg.phaser_range)
    for p in state.planets_in(ship.galaxy, ship.sector):
        if not planet_threatens(p, captain, own, cfg):
            continue
        for y in range(max(0, p.cell[1] - reach), min(h, p.cell[1] + reach + 1)):
            for x in range(max(0, p.cell[0] - reach), min(w, p.cell[0] + reach + 1)):
                cells.add((x, y))
    return cells


def can_afford_a_fight(captain: Captain, cfg: GameConfig) -> bool:
    """D-150: an AI captain opens a battle only while the FR-06 cost leaves it at or above planet_refuse_respect."""
    return int(captain.respect) - int(cfg.respect_start_fight_delta) >= int(cfg.planet_refuse_respect)


def attack_orders(captain: Captain, ship: Ship, here: list[Ship], state: GameState, cfg: GameConfig,
                  trace: list[Decision] | None = None) -> list[Order]:
    """CM-10 / EF-01: open fire on the nearest enemy in weapon range whose captain's fear is below attack_fear_max and
    against whom the strength ratio (fleet in sector) is at least attack_ratio_min."""
    reach = C.weapon_range(ship, cfg)
    if reach <= 0:
        return []
    if not can_afford_a_fight(captain, cfg):
        return []
    own = C.strength([s for s in here if s.captain_id == captain.id], cfg)
    best: tuple | None = None
    for s in here:
        if s.captain_id == captain.id:
            continue
        d = chebyshev(s.cell, ship.cell)
        if d > reach:
            continue
        tcap = state.captains.get(s.captain_id)
        if tcap is None or tcap.fear >= cfg.attack_fear_max:
            continue
        theirs = C.strength([x for x in here if x.captain_id == s.captain_id], cfg)
        ratio = own / theirs if theirs > 0 else float("inf")
        if ratio < cfg.attack_ratio_min:
            continue
        key = (d, s.id, s, tcap, ratio)
        if best is None or key[:2] < best[:2]:
            best = key
    if best is None:
        return []
    shots = fire_orders(ship, best[2], cfg)
    if shots:
        note(trace, captain, ship, f"{best[2].id} of {best[3].name} ({best[3].id}) is {best[0]} cell(s) away (weapon reach {reach}), fear {best[3].fear} < {cfg.attack_fear_max:g}, "
             f"strength ratio {best[4]:.2f} >= {cfg.attack_ratio_min:g} -> opens a battle; {fire_text(shots)}", "CM-10", "EF-01", kind=KIND_ATTACK)
    return shots


# --------------------------------------------------------------------------- restock trips (D-144, CFG-81)

def planet_trades_with(planet: Planet, captain: Captain, cfg: GameConfig) -> bool:
    return E.trades_with(planet, captain, cfg)


def needs_supplies(captain: Captain, cfg: GameConfig) -> bool:
    """D-144 / CFG-81: a trip starts when food or water is below buy_supplies_below."""
    below = int(cfg.buy_supplies_below)
    return int(captain.food) < below or int(captain.water) < below


def docked_at(ship: Ship, captain: Captain, state: GameState, cfg: GameConfig) -> Planet | None:
    """D-150: an AI ship is docked when a planet that trades with its captain lies in its cell or next to it (orbit)."""
    best: Planet | None = None
    for p in state.planets_in(ship.galaxy, ship.sector):
        if chebyshev(p.cell, ship.cell) <= 1 and planet_trades_with(p, captain, cfg):
            if best is None or (chebyshev(p.cell, ship.cell), p.id) < (chebyshev(best.cell, ship.cell), best.id):
                best = p
    return best


def orbit_cell(planet: Planet, ship: Ship, state: GameState) -> Coord | None:
    """The free cell next to the planet nearest the ship (Chebyshev, then Manhattan, then x, y - D-113 ordering)."""
    sector = state.world.sector(planet.galaxy, planet.sector)
    if sector is None:
        return None
    free = [c for c in sector.grid.neighbours(*planet.cell) if sector.grid.is_void(*c)]
    if not free:
        return None
    return min(free, key=lambda c: (chebyshev(c, ship.cell), manhattan(c, ship.cell), c[0], c[1]))


def nearest_trading_planet(captain: Captain, ship: Ship, state: GameState, cfg: GameConfig) -> Planet | None:
    """The nearest live planet in the ship's sector that trades with the captain and has a free orbit cell; None in a hole
    sector (D-103: docking inside a pull range would be fatal) or when none trades."""
    sector = state.sector_of(ship)
    if sector is None or sector.has_hole():
        return None
    best: Planet | None = None
    for p in sector.planets():
        if not planet_trades_with(p, captain, cfg) or orbit_cell(p, ship, state) is None:
            continue
        if best is None or (chebyshev(p.cell, ship.cell), p.id) < (chebyshev(best.cell, ship.cell), best.id):
            best = p
    return best


def nearest_planet_sector(captain: Captain, ship: Ship, state: GameState, cfg: GameConfig, pull: float) -> tuple[Coord, Coord] | None:
    """The nearest sector (flattened Chebyshev, then fixed order) within warp reach that holds no hole and a planet trading with the captain."""
    box = _reach_box(ship, state, cfg, pull)
    if box is None:
        return None
    r, (fx, fy) = box
    here = (ship.galaxy, ship.sector)
    for ring in range(1, r + 1):
        for y in range(fy - ring, fy + ring + 1):
            for x in range(fx - ring, fx + ring + 1):
                if max(abs(x - fx), abs(y - fy)) != ring:
                    continue
                gs = state.world.flat_sectors.get((x, y))
                if gs is None or gs == here:
                    continue
                sector = state.world.sectors.get(gs)
                if sector is None or sector.has_hole():
                    continue
                if any(planet_trades_with(p, captain, cfg) for p in sector.planets()):
                    return gs
    return None


def restock_step(ship: Ship, dest: Coord, cells: int, blocked: set[Coord]) -> Coord | None:
    """This turn's step towards the orbit cell (D-103: within reach, never persisting); the step is shortened when it would
    end on terrain, and None means the ship cannot move."""
    if cells <= 0 or dest == ship.cell:
        return None
    for n in range(int(cells), 0, -1):
        target = MV.step_towards(ship.cell, dest, n)
        if target != ship.cell and target not in blocked:
            return target
    return None


def restock_orders(captain: Captain, ship: Ship, state: GameState, cfg: GameConfig, cells: int, pull: float, blocked: set[Coord],
                   stats: dict | None, trace: list[Decision] | None) -> list[Order] | None:
    """D-144: the active ship's restock trip.  Returns the orders (an empty list = hold here, docked) or None when no trip applies
    and the ordinary rules decide."""
    docked = docked_at(ship, captain, state, cfg)
    if docked is not None:
        if not E.restocked(captain, cfg) or captain.item_units() > 0:
            captain.restock_target = docked.id
            note(trace, captain, ship, f"docked at {docked.name} ({docked.id}): food {captain.food}, water {captain.water}, {captain.silver} silver -> stays to restock",
                 "D-144", "CFG-81", kind=KIND_RESTOCK)
            return []
        captain.restock_target = None
        return None
    if captain.restock_target is None and not needs_supplies(captain, cfg):
        return None
    target = state.live_planet(captain.restock_target) if captain.restock_target else None
    if target is not None and (not planet_trades_with(target, captain, cfg) or state.world.sector(target.galaxy, target.sector) is None
                               or state.world.sector(target.galaxy, target.sector).has_hole()):
        target = None
    if target is None and not needs_supplies(captain, cfg):
        captain.restock_target = None
        return None
    if target is None or (target.galaxy, target.sector) != (ship.galaxy, ship.sector):
        local = nearest_trading_planet(captain, ship, state, cfg)
        if local is not None:
            target = local
    if target is not None and (target.galaxy, target.sector) == (ship.galaxy, ship.sector):
        started = captain.restock_target != target.id
        captain.restock_target = target.id
        if started and stats is not None:
            stats["restock_trips"] = stats.get("restock_trips", 0) + 1
        dest = orbit_cell(target, ship, state)
        step = restock_step(ship, dest, cells, blocked) if dest is not None else None
        if step is None:
            note(trace, captain, ship, f"restock trip to {target.name} ({target.id}) at {at(target.cell)}: held (pull {pull:g}, {cells} cell(s)) -> waits",
                 "D-144", "MV-01", kind=KIND_RESTOCK)
            return []
        note(trace, captain, ship, f"food {captain.food}, water {captain.water} < buy_supplies_below {cfg.buy_supplies_below} -> restock trip to {target.name} ({target.id}) "
             f"in orbit at {at(dest)}: {at(ship.cell)} -> {at(step)}", "D-144", "CFG-81", kind=KIND_RESTOCK)
        return [Order("move", (str(step[0]), str(step[1]), f"{ship.engine:g}"), ship.id, QUEUED)]
    dest_sector = nearest_planet_sector(captain, ship, state, cfg, pull)
    if dest_sector is None:
        captain.restock_target = None
        note(trace, captain, ship, f"food {captain.food}, water {captain.water} low but no trading planet within warp reach from {at(ship.cell)} -> wanders", "D-144", "D-103",
             kind=KIND_RESTOCK)
        return None
    (gx, gy), (sx, sy) = dest_sector
    if captain.restock_target is None and stats is not None:
        stats["restock_trips"] = stats.get("restock_trips", 0) + 1
    captain.restock_target = next(p.id for p in state.world.sectors[dest_sector].planets() if planet_trades_with(p, captain, cfg))
    note(trace, captain, ship, f"food {captain.food}, water {captain.water} < buy_supplies_below {cfg.buy_supplies_below}, no trading planet here -> restock trip: warps to "
         f"galaxy {gx},{gy} sector {sx},{sy}", "D-144", "CFG-81", "D-103", kind=KIND_RESTOCK)
    return [Order("warp", (str(gx), str(gy), str(sx), str(sy)), ship.id, QUEUED)]


# --------------------------------------------------------------------------- the captain's turn

def decide(captain: Captain, state: GameState, rng: random.Random, cfg: GameConfig, *, passive: bool = False,
           index: dict[tuple[Coord, Coord], list[Ship]] | None = None, trace: list[Decision] | None = None,
           roamers: set[str] | None = None, stats: dict | None = None) -> list[Order]:
    """The captain's orders for this turn (see the module docstring).  ``index`` is ``state.ships_by_sector()``,
    built once per turn by the caller; ``passive`` is the ``--ai passive`` debug mode (never opens a battle);
    ``trace`` collects a ``Decision`` per ship (reason + rule IDs) for the gameplay log; ``roamers`` receives the ids
    of ships that warp because a roam is due (CFG-73 counts those, not restock warps); ``stats`` counts restock trips."""
    if index is None:
        index = state.ships_by_sector()
    orders: list[Order] = []
    size = cfg.sector_size
    for sid in list(captain.ships):
        ship = state.ships.get(sid)
        if ship is None:
            continue
        terrain = state.terrain_of(ship)
        blocked = {(int(t.cell[0]), int(t.cell[1])) for t in terrain}
        hz = H.strongest(terrain, ship.cell, cfg)
        pull = hz.pull if hz is not None else 0.0
        cells = MV.impulse_cells(ship.engine, pull)
        here = index.get((ship.galaxy, ship.sector), [])
        battle = state.battle_of_ship(ship)
        if battle is not None:
            orders += battle_orders(captain, ship, battle, here, state, cfg, cells, blocked, trace)
            continue
        if not passive and not captain.is_player and sid == captain.active_ship and not (hz is not None and hz.kind in HOLE_KINDS):
            # D-150 (17): the AI driving the player (--ai-player) never trades - the economy is the human's to play
            trip = restock_orders(captain, ship, state, cfg, cells, pull, blocked, stats, trace)
            if trip is not None:
                orders += trip
                continue
        if not passive:
            attack = attack_orders(captain, ship, here, state, cfg, trace)
            if attack:
                orders += attack
                continue
            if sid == captain.active_ship and roam_due(captain, state.turn_no, cfg):
                dest = roam_destination(ship, state, rng, cfg, pull)
                if dest is not None:
                    (gx, gy), (sx, sy) = dest
                    orders.append(Order("warp", (str(gx), str(gy), str(sx), str(sy)), sid, QUEUED))
                    if roamers is not None:
                        roamers.add(sid)
                    note(trace, captain, ship, f"no battle, roam due (every {cfg.roam_every_turns} turns) -> warps to galaxy {gx},{gy} sector {sx},{sy}",
                         "CFG-73", "D-125", "D-132", kind=KIND_ROAM)
                    continue
        if cells <= 0:
            note(trace, captain, ship, f"impulse gives {cells} cell(s) here (engine {ship.engine:g}, pull {pull:g}) -> holds", "MV-01", "OV-10", kind=KIND_HOLD)
            continue
        if hz is not None and hz.kind in HOLE_KINDS:
            target = away_target(ship.cell, hz.cell, cells, size, blocked)
            why = f"inside the pull of the {hz.kind.replace('_', ' ')} at {at(hz.cell)} -> moves away"
            ids = ("OV-10", "D-103")
        elif passive:
            note(trace, captain, ship, "--ai passive: no initiative -> holds", "D-125", kind=KIND_HOLD)
            continue       # --ai passive (D-125): no initiative at all - no opening, no roaming, no wandering; it still defends and flees a hole
        else:
            avoid = blocked | hostile_planet_cells(captain, ship, here, state, cfg)
            target = random_target(ship.cell, cells, size, avoid, rng)
            why = "no enemy to fight, no roam due -> random walk"
            ids = ("OV-10",)
        if target is None:
            note(trace, captain, ship, f"{why}, but no free cell within {cells} -> holds", *ids, kind=KIND_HOLD)
            continue
        orders.append(Order("move", (str(target[0]), str(target[1]), f"{ship.engine:g}"), sid, QUEUED))
        note(trace, captain, ship, f"{why} {at(ship.cell)} -> {at(target)} (up to {cells} cell(s))", *ids, kind=KIND_WALK)
    return orders


# --------------------------------------------------------------------------- the planet's turn (CM-11, EF-05, D-145)

def planet_orders(planet: Planet, state: GameState, cfg: GameConfig, here: list[Ship], *, passive: bool = False,
                  trace: list[Decision] | None = None) -> tuple[list[Order], Ship | None]:
    """A live planet's orders: in a battle it fires both weapons at the nearest enemy in range (accepting a standing peace
    offer when weaker); otherwise, not passive, it opens on the nearest low-respect weaker ship within its phaser range
    (EF-05, D-150).  Returns (orders, the ship it opens on or None)."""
    if planet.destroyed or not here:
        return [], None
    battle = state.battle_of_ship(planet)
    if battle is not None:
        enemy_id = battle.other(planet.id)
        enemies = C.enemies_in_sector(state, planet, enemy_id)
        if not enemies:
            return [], None
        own = C.ship_strength(planet, cfg)
        theirs = C.strength(enemies, cfg)
        if own < theirs and battle.offer_stands(enemy_id, state.turn_no):
            note(trace, planet, planet, f"in battle {battle.id}, weaker (strength {own:.0f} v {theirs:.0f}) and {enemy_id} offers peace -> accepts", "CB-03", "D-145", "D-148",
                 kind=KIND_BATTLE)
            return [Order("peace", ("accept", enemy_id), planet.id, QUEUED)], None
        shots = fire_orders(planet, enemies[0], cfg)
        note(trace, planet, planet, f"in battle {battle.id} ({'weaker' if own < theirs else 'not weaker'}, strength {own:.0f} v {theirs:.0f}) -> "
             f"{fire_text(shots) if shots else 'no weapon in range'}", "CM-11", "D-145", kind=KIND_BATTLE)
        return shots, None
    if passive:
        return [], None
    reach = min(C.weapon_range(planet, cfg), int(cfg.phaser_range))     # D-150: planets open point-blank, then use their full range
    if reach <= 0:
        return [], None
    own = C.ship_strength(planet, cfg)
    best: tuple | None = None
    for s in here:
        d = chebyshev(s.cell, planet.cell)
        if d > reach:
            continue
        cap = state.captains.get(s.captain_id)
        if cap is None or int(cap.respect) >= int(cfg.planet_attack_respect_max):
            continue
        if C.open_battle_between(state, planet.id, cap.id) is not None:
            continue
        theirs = C.strength([x for x in here if x.captain_id == cap.id], cfg)
        ratio = own / theirs if theirs > 0 else float("inf")
        if ratio < cfg.attack_ratio_min:
            continue
        key = (d, s.id)
        if best is None or key < best[:2]:
            best = (d, s.id, s, cap, ratio)
    if best is None:
        return [], None
    shots = fire_orders(planet, best[2], cfg)
    if shots:
        note(trace, planet, planet, f"{best[2].id} of {best[3].name} ({best[3].id}) is {best[0]} cell(s) away, respect {best[3].respect} < planet_attack_respect_max "
             f"{cfg.planet_attack_respect_max}, strength ratio {best[4]:.2f} >= {cfg.attack_ratio_min:g} -> opens fire; {fire_text(shots)}", "EF-05", "CFG-31", "D-145",
             kind=KIND_BATTLE)
    return shots, (best[2] if shots else None)
