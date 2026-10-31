"""The endgame - eligibility, placement, guardians and mastery (pure rules the phases apply).

EG-01...08, SP-08, CM-13, EF-06, SUP-05, OUT-15, CFG-49...52, CFG-86...88, D-157, D-158, D-159, C12.

* ``eligibility`` (EG-01...04, D-157): wealth ``gold x silver_per_gold + silver >= wealth_min_silver``, ``fear >= fear_min``,
  ``respect >= respect_min`` and at least ``required_special_distinct`` *distinct* Mythical (special-rarity) elements held - the
  player only, judged at TP-08 every turn until it first holds.
* ``place`` (SP-08, EG-05, EG-06, D-158): the ultimate item lies at the Void cell nearest the centre of a real system (>= 1 star,
  no black hole or neutron star) at flattened Chebyshev distance >= ``placement_distance_pct`` % of the universe diameter from the
  player's active ship - farthest first, ties by the in-game RNG; ``guard_ships`` guardian captains with one Bastion each stand at
  distinct free cells within ``torpedo_range`` of it.  [ASSUMED] D-164: guardians prefer the four corners of the ring two cells
  out (both weapons reach a ship on the item), then any free cell within range, nearest first; when no sector clears the
  threshold the farthest qualifying system is used; a world without a 'bastion' class uses its strongest class.
* guardians (CM-13, D-158): flagged ``Captain.guardian``; never roam, restock, trade, pick up, offer or accept peace, escape or
  surrender; fire both weapons at every non-guardian ship in the sector from the TP-04 of the turn it arrives (``guardian_fire_orders``
  runs at the start of TP-04, after movement) regardless of ratio or fear; excluded from the population counts.
* mastery (EG-07, EF-06, SUP-05, D-159): ``pickup lodestone`` with no guardian alive makes the captain the Master at TP-05 - levels
  100 and locked, every owned ship at the caps every turn (``master_upkeep``), ``infinite_wealth``, supplies frozen; the ``mastery``
  event is the one T3 animates (OUT-15).  Play continues (OV-06 still applies).

Functions of the state, the config and the engine RNG; no I/O, no clocks.
"""
from __future__ import annotations

import random

from starbour.config import GameConfig, ShipClass
from starbour.engine import combat as CB
from starbour.engine.ai import KIND_BATTLE, KIND_HOLD, Decision, fire_orders, fire_text, note
from starbour.engine.movement import sector_centre
from starbour.engine.state import LODESTONE_ID, GameState, lodestone_item
from starbour.entities.captain import Captain, clamp_level
from starbour.entities.ship import Ship
from starbour.world import names
from starbour.world.generate import Sector, flat_sector
from starbour.world.grid import Coord, chebyshev, nearest_free_cell
from starbour.world.stellar import STAR

SPECIAL = "special"
LEVEL_MAX = 100
GUARD_CLASS = "bastion"
GUARD_RING = 2          # [ASSUMED] D-164: guardians stand two cells out, so phasers (range 2) reach a ship on the item as torpedoes do


def guardian_rng(seed: int, n: int) -> random.Random:
    """Names for the n-th guardian come from their own stream, so placement never shifts the game RNG beyond its one tie-break (D-67)."""
    return random.Random(f"starbour-guardian-{int(seed)}-{int(n)}")


# --------------------------------------------------------------------------- eligibility (EG-01...04, D-157)

def special_elements(cfg: GameConfig) -> list[str]:
    return sorted(n for n, e in cfg.elements.items() if e.rarity == SPECIAL)


def distinct_specials(items: dict, cfg: GameConfig) -> int:
    """EG-04 / D-157: how many *distinct* Mythical elements the captain holds (units > 0)."""
    return sum(1 for n in special_elements(cfg) if int((items or {}).get(n, 0)) > 0)


def wealth_silver(gold: int, silver: int, cfg: GameConfig) -> int:
    """EG-01 / TR-01: gold x silver_per_gold + silver."""
    return int(gold) * int(cfg.silver_per_gold) + int(silver)


def eligibility(cap: dict | Captain, cfg: GameConfig) -> dict:
    """The four EG-01...04 checks as {name: {"value", "min", "ok"}} plus "ok" for all four - from a Captain or its snapshot dict,
    so the engine and T4 (PNL-08) read the same rule."""
    if isinstance(cap, Captain):
        gold, silver, fear, respect, items = cap.gold, cap.silver, cap.fear, cap.respect, cap.items
    else:
        gold, silver, fear, respect, items = int(cap.get("gold", 0)), int(cap.get("silver", 0)), int(cap.get("fear", 0)), int(cap.get("respect", 0)), cap.get("items") or {}
    rows = {
        "wealth": {"value": wealth_silver(gold, silver, cfg), "min": int(cfg.wealth_min_silver)},
        "fear": {"value": int(fear), "min": int(cfg.fear_min)},
        "respect": {"value": int(respect), "min": int(cfg.respect_min)},
        "specials": {"value": distinct_specials(items, cfg), "min": int(cfg.required_special_distinct)},
    }
    for r in rows.values():
        r["ok"] = r["value"] >= r["min"]
    rows["ok"] = all(rows[k]["ok"] for k in ("wealth", "fear", "respect", "specials"))
    return rows


# --------------------------------------------------------------------------- placement (SP-08, EG-05, EG-06, D-158)

def placement_threshold(cfg: GameConfig) -> int:
    """SP-08 / CFG-87: placement_distance_pct % of the universe diameter (universe x galaxy, flattened), rounded up."""
    uw, uh = cfg.universe_size
    gw, gh = cfg.galaxy_size
    diameter = max(int(uw) * int(gw), int(uh) * int(gh))
    return int(-(-diameter * int(cfg.placement_distance_pct) // 100))


def is_system(sector: Sector) -> bool:
    """A real sector with at least one star and no black hole, neutron star or wormhole."""
    return sector.kind != "void" and not sector.has_hole() and any(getattr(t, "kind", "") == STAR for t in sector.terrain())


def candidates(state: GameState, origin: tuple[Coord, Coord], cfg: GameConfig) -> list[tuple[int, tuple[Coord, Coord]]]:
    """(flattened Chebyshev distance from origin, (galaxy, sector)) for every qualifying system, sorted by coordinates (D-67)."""
    f0 = flat_sector(origin[0], origin[1], cfg.galaxy_size)
    out: list[tuple[int, tuple[Coord, Coord]]] = []
    for gs in sorted(state.world.sectors):
        if gs == origin or not is_system(state.world.sectors[gs]):
            continue
        out.append((chebyshev(flat_sector(gs[0], gs[1], cfg.galaxy_size), f0), gs))
    return out


def choose_sector(state: GameState, origin: tuple[Coord, Coord], cfg: GameConfig, rng: random.Random) -> tuple[tuple[Coord, Coord], int] | None:
    """SP-08 / D-158: the farthest qualifying system at distance >= the threshold (ties by the in-game RNG); when none clears the
    threshold, the farthest there is ([ASSUMED] D-164); None in a world without a system."""
    rows = candidates(state, origin, cfg)
    if not rows:
        return None
    threshold = placement_threshold(cfg)
    far = [r for r in rows if r[0] >= threshold] or rows
    best = max(d for d, _ in far)
    ties = [gs for d, gs in far if d == best]
    gs = ties[0] if len(ties) == 1 else ties[rng.randrange(len(ties))]
    return gs, best


def item_cell(sector: Sector, cfg: GameConfig) -> Coord | None:
    """EG-05 / D-158: the Void (terrain-free) cell nearest the sector centre."""
    return nearest_free_cell(sector.grid, sector_centre(cfg.sector_size))


def guardian_cells(sector: Sector, cell: Coord, n: int, cfg: GameConfig) -> list[Coord]:
    """[ASSUMED] D-164: distinct terrain-free cells within torpedo_range of the item - the four corners of the ring GUARD_RING out
    first, then the rest of that ring, then every other cell within range, nearest first (ties by x, y)."""
    reach = int(cfg.torpedo_range)
    free = {c for c in sector.grid.free_cells() if c != tuple(cell) and 1 <= chebyshev(c, cell) <= reach}
    ring = min(GUARD_RING, reach)
    corners = [(cell[0] + dx * ring, cell[1] + dy * ring) for dx, dy in ((-1, -1), (1, -1), (-1, 1), (1, 1))]
    ordered = [c for c in corners if c in free]
    ordered += sorted((c for c in free if c not in ordered and chebyshev(c, cell) == ring), key=lambda c: (c[0], c[1]))
    ordered += sorted((c for c in free if c not in ordered), key=lambda c: (chebyshev(c, cell), c[0], c[1]))
    return ordered[:max(0, int(n))]


def guard_class(cfg: GameConfig) -> ShipClass:
    """EG-06: a Bastion each; [ASSUMED] D-164: the strongest class when the ini has no 'bastion'."""
    spec = cfg.ships.get(GUARD_CLASS)
    if spec is not None:
        return spec
    return max(cfg.ships.values(), key=lambda s: (s.shield_max + s.torpedoes * cfg.torpedo_damage + s.phasers * cfg.phaser_damage_per_unit * 3.0, s.name))


def place(state: GameState, cfg: GameConfig, rng: random.Random) -> dict | None:
    """Place the ultimate item and its guardians for the player's active ship; sets ``state.lodestone`` and returns the MSG-03
    ``eligible`` event (None when the world has no qualifying system or the item already lies somewhere)."""
    if state.lodestone is not None:
        return None
    ship = state.active_ship()
    if ship is None:
        return None
    choice = choose_sector(state, (ship.galaxy, ship.sector), cfg, rng)
    if choice is None:
        return None
    (g, s), distance = choice
    sector = state.world.sectors[(g, s)]
    cell = item_cell(sector, cfg)
    if cell is None:
        return None
    item = lodestone_item(cell)
    sector.items.append(item)
    spec = guard_class(cfg)
    guardians: list[str] = []
    for i, gcell in enumerate(guardian_cells(sector, cell, cfg.guard_ships, cfg), start=1):
        grng = guardian_rng(state.seed, i)
        used_c = {c.name for c in state.captains.values()}
        used_s = {x.name for x in state.ships.values()}
        cid, sid = state.new_captain_id(), state.new_ship_id()
        cap = Captain.new(cid, names.unique(names.captain_name, grng, used_c), False, cfg)
        cap.guardian = True
        gship = Ship.of_class(sid, names.unique(names.ship_name, grng, used_s), spec, cid, (g, s, gcell))
        cap.ships = [sid]
        cap.active_ship = sid
        state.captains[cid] = cap
        state.ships[sid] = gship
        guardians.append(cid)
    key = f"{g[0]},{g[1]}/{s[0]},{s[1]}"
    state.lodestone = {"sector": key, "cell": (cell[0], cell[1]), "distance": int(distance), "placed_turn": state.turn_no, "guardians": guardians,
                       "picked_up_turn": None}
    state.eligible_turn = state.turn_no
    name = cfg.ultimate_item_name
    text = (f"You are eligible for the {name}: it lies in sector {key} at cell {cell[0]},{cell[1]}, {distance} sectors away "
            f"(at least {placement_threshold(cfg)}, SP-08), guarded by {len(guardians)} {spec.name.capitalize()} captain(s) who fire on anyone who enters - "
            f"beat them, scan the cell and 'pickup {LODESTONE_ID}' (EG-01...06, CM-13)")
    return {"phase": "TP-08", "type": "eligible", "severity": "highlight", "text": text, "ids": ["EG-01", "EG-02", "EG-03", "EG-04", "EG-05", "EG-06", "SP-08", "D-158"]}


def phase_endgame(state: GameState, cfg: GameConfig, rng: random.Random, events: list[dict], stats: dict) -> None:
    """TP-08 / EG-01...04 / D-158: the player's eligibility, checked every turn until it first holds; placement in the same phase."""
    cap = state.player
    if cap.master or state.lodestone is not None or state.game_over:
        return
    if not eligibility(cap, cfg)["ok"]:
        return
    ev = place(state, cfg, rng)
    if ev is not None:
        events.append(ev)
        stats["eligible"] = True


# --------------------------------------------------------------------------- guardians (CM-13, D-158)

def guardian_targets(state: GameState, ship: Ship) -> list[Ship]:
    """Every non-guardian ship in the guardian's sector, nearest first (ties by id)."""
    out = []
    for sid in sorted(state.ships):
        s = state.ships[sid]
        if s.galaxy != ship.galaxy or s.sector != ship.sector or s.captain_id == ship.captain_id:
            continue
        owner = state.captains.get(s.captain_id)
        if owner is None or owner.guardian:
            continue
        out.append(s)
    out.sort(key=lambda s: (chebyshev(s.cell, ship.cell), s.id))
    return out


def guardian_fire_orders(state: GameState, cfg: GameConfig, by_ship: dict[str, list], trace: list[Decision] | None) -> int:
    """CM-13 / D-158: at the start of TP-04 (after movement) every guardian ship fires both weapons at the nearest non-guardian ship in
    range - the ratio and fear tests of CM-10 do not apply.  The first fire of a turn opens the battle (CM-01, the TP-04 fallback)."""
    fired = 0
    for cid in sorted(state.captains):
        cap = state.captains[cid]
        if not cap.guardian:
            continue
        for sid in cap.ships:
            ship = state.ships.get(sid)
            if ship is None:
                continue
            targets = [t for t in guardian_targets(state, ship) if chebyshev(t.cell, ship.cell) <= CB.weapon_range(ship, cfg)]
            if not targets:
                note(trace, cap, ship, "guardian of the ultimate item: nothing in range -> holds the sector (CM-13)", "CM-13", "D-158", kind=KIND_HOLD)
                continue
            shots = fire_orders(ship, targets[0], cfg)
            if not shots:
                continue
            queue = [o for o in by_ship.get(sid, []) if o.command != "fire"]
            by_ship[sid] = queue + shots
            fired += len(shots)
            note(trace, cap, ship, f"guardian of the ultimate item: {targets[0].id} is {chebyshev(targets[0].cell, ship.cell)} cell(s) away -> "
                 f"{fire_text(shots)} regardless of ratio or fear (CM-13, CM-10 waived)", "CM-13", "D-158", kind=KIND_BATTLE)
    return fired


# --------------------------------------------------------------------------- mastery (EG-07, EF-06, SUP-05, D-159)

def master_ship(ship: Ship, cfg: GameConfig) -> None:
    """D-159: a Master's ship at the caps - shields shield_cap and full, phasers phaser_max, engine impulse_max, warp warp_max,
    torpedoes master_torpedoes."""
    ship.shield_max = float(cfg.shield_cap)
    ship.shield_current = float(cfg.shield_cap)
    ship.phasers = float(cfg.phaser_max)
    ship.engine = float(cfg.impulse_max)
    ship.warp = float(cfg.warp_max)
    ship.torpedoes = int(cfg.master_torpedoes)


def master_upkeep(state: GameState, cfg: GameConfig) -> None:
    """Every turn (TP-06, after hazards, combat and recharge): levels locked at 100, every owned ship back at the caps (D-159)."""
    for cap in state.captains.values():
        if not cap.master:
            continue
        cap.fear = cap.respect = LEVEL_MAX
        for sid in cap.ships:
            ship = state.ships.get(sid)
            if ship is not None:
                master_ship(ship, cfg)


def become_master(state: GameState, cfg: GameConfig, cap: Captain) -> dict:
    """EG-07 / OUT-15 / D-159: the pickup of the ultimate item with no guardian alive; returns the MSG-03 ``mastery`` event."""
    cap.master = True
    cap.infinite_wealth = True
    cap.fear = cap.respect = clamp_level(LEVEL_MAX)
    cap.zero_turns = 0
    if state.lodestone is not None:
        state.lodestone["picked_up_turn"] = state.turn_no
    state.mastery_turn = state.turn_no
    master_upkeep(state, cfg)
    return {"phase": "TP-05", "type": "mastery", "severity": "highlight",
            "text": f"MASTER OF STARBOUR - {cap.name} holds the {cfg.ultimate_item_name}: fear and respect 100 for good, every ship at its best, "
                    f"wealth and supplies without end; every planet trades and every ship yields (EG-07, EF-06, SUP-05)",
            "ids": ["EG-07", "EG-08", "EF-06", "SUP-05", "OUT-15", "D-159"]}
