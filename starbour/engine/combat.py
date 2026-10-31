"""Ship and planet combat - battle state, participants, the exchange, outcomes and strength (pure).

CM-01...12, CB-01...04, FR-*, HZ-09, PR-02, EF-05, D-119...D-125, D-130, D-132, D-145, D-148.  ``phase_combat`` is
TP-04: it takes the state (plain dataclasses), this turn's orders and the engine RNG and mutates
the state in place, appending C10 / C11 records to ``stats`` and MSG-03 events to ``events``.
No rich, no zmq, no clocks; every number comes from the config.

Within a turn (D-120, M3 section 6):
  1. accepted ``fire`` orders open battles (CM-01) - FR-06 for an opening captain (planets pay nothing);
  2. peace offers, accepts, rejects and surrenders resolve (CM-09, CB-03, EF-02, D-122, D-124);
  3. a battle with no pair of combatants in range after movement ends by escape (CB-02, D-119);
  4. the exchange: every torpedo of both sides, then every phaser volley, hits computed from the
     state at the start of the phase and applied in attacker id order; a hit landing on 0
     shields destroys (HZ-09), also the next torpedo of the same volley; the rest of a volley at
     a destroyed target is held (ammo kept);
  5. kills: the killer takes the victim's torpedoes and max(own, victim) phasers / engine /
     warp (capped); a side wiped out hands its captain's coins and items - or a planet's stock,
     food, water and elements (CM-11, D-140) - to the captain whose ship landed the last hit
     (CB-01, D-123); both sides wiped out transfers nothing; a destroyed planet leaves the map;
  6. reputation rows for every battle that ended with a victor (TP-07 applies them).

Planets (CM-11, D-145): a planet is a side of its own (its id), fights with the same formulas,
never moves, never offers peace, and accepts a standing offer when it is the weaker side
(D-148 (5)); the victor of a peace is the side that did not offer, X % of the loser's coins
(or a planet's stock) move.  Planet events (``planet_events[]``, C11): attacked, opened_fire,
destroyed, peace.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from starbour.commands.parse import BOTH, CommandError, Order, parse_fire, parse_peace
from starbour.config import GameConfig
from starbour.engine import reputation as R
from starbour.engine.state import GameState as StateView
from starbour.engine.state import is_planet_id
from starbour.entities.battle import Battle
from starbour.entities.captain import Captain
from starbour.entities.planet import Planet
from starbour.entities.ship import Ship
from starbour.world.grid import Coord, chebyshev

TORPEDO, PHASER = "torpedo", "phaser"
SURRENDER = "surrender"     # the AI's internal order (EF-02, FL-02); never typed


# --------------------------------------------------------------------------- formulas (tab 13)

def phaser_damage(phasers: float, per_unit: float, distance: int) -> float:
    """CM-04 / CFG-26: phasers x phaser_damage_per_unit x (1 - distance / 3); never negative."""
    return max(0.0, float(phasers) * float(per_unit) * (1.0 - float(distance) / 3.0))


def torpedo_hit_p(target_engine: float, cfg: GameConfig) -> float:
    """CM-03 / CFG-25: p = torpedo_hit_base - torpedo_evade_per_engine x target engine, clamped 0...1."""
    return max(0.0, min(1.0, float(cfg.torpedo_hit_base) - float(cfg.torpedo_evade_per_engine) * float(target_engine)))


def ship_strength(ship, cfg: GameConfig) -> float:
    """CM-08: shield_current + torpedoes x torpedo_damage + phasers x phaser_damage_per_unit x 3 (ships and planets alike, CM-11)."""
    return float(ship.shield_current) + float(ship.torpedoes) * float(cfg.torpedo_damage) + float(ship.phasers) * float(cfg.phaser_damage_per_unit) * 3.0


def strength(ships: list, cfg: GameConfig) -> float:
    """CM-08 / FL-05: the fleet-in-sector total."""
    return sum(ship_strength(s, cfg) for s in ships)


def weapon_range(ship, cfg: GameConfig) -> int:
    """CM-02: the longest range the combatant can shoot at - torpedoes 5 while it has ammo, phasers 2 while it has any."""
    best = 0
    if ship.torpedoes > 0:
        best = max(best, int(cfg.torpedo_range))
    if ship.phasers > 0:
        best = max(best, int(cfg.phaser_range))
    return best


def range_of(weapon: str, cfg: GameConfig) -> int:
    return int(cfg.torpedo_range) if weapon == TORPEDO else int(cfg.phaser_range)


def pair_in_range(a, b, cfg: GameConfig) -> bool:
    """D-119 / D-128: either combatant inside the other's weapon range (Chebyshev <= range) keeps the pair in battle."""
    if a.galaxy != b.galaxy or a.sector != b.sector:
        return False
    return chebyshev(a.cell, b.cell) <= max(weapon_range(a, cfg), weapon_range(b, cfg))


def peace_pct(victor_shields: float, loser_shields: float, cfg: GameConfig) -> int:
    """CB-03 / D-46 / D-122: X = floor(max(0, (V - L) / V) x peace_loot_max); 0 when the victor's shields are lower."""
    v = float(victor_shields)
    if v <= 0:
        return 0
    return int(math.floor(max(0.0, (v - float(loser_shields)) / v) * float(cfg.peace_loot_max) + 1e-9))


def ai_peace_threshold(enemy_fear: float, cfg: GameConfig) -> float:
    """EF-02 / CFG-30 / CFG-72: the shield % below which a weaker AI offers peace, rising with the enemy's fear."""
    lo, hi = float(cfg.peace_shield_pct), float(cfg.peace_shield_pct_at_fear100)
    return lo + (hi - lo) * max(0.0, min(100.0, float(enemy_fear))) / 100.0


# --------------------------------------------------------------------------- helpers over the state

def ships_of(state: StateView, side_id: str, galaxy: Coord, sector: Coord) -> list:
    """A side's combatants in one sector, by id (CM-08 fleet in sector): a captain's ships, or the planet itself (CM-11)."""
    if is_planet_id(side_id):
        p = state.live_planet(side_id)
        return [p] if p is not None and p.galaxy == galaxy and p.sector == sector else []
    cap = state.captains.get(side_id)
    if cap is None:
        return []
    out = []
    for sid in cap.ships:
        s = state.ships.get(sid)
        if s is not None and s.galaxy == galaxy and s.sector == sector:
            out.append(s)
    out.sort(key=lambda s: s.id)
    return out


def side_strength(state: StateView, side_id: str, galaxy: Coord, sector: Coord, cfg: GameConfig) -> float:
    return strength(ships_of(state, side_id, galaxy, sector), cfg)


def find_ship(state: StateView, text: str, *, galaxy: Coord | None = None, sector: Coord | None = None):
    """A ship or a live planet by id or name (case-insensitive), optionally only in one sector; ids win over names (D-148 (6))."""
    wanted = text.strip().lower()
    direct = state.ships.get(text.strip())
    if direct is not None and (galaxy is None or (direct.galaxy == galaxy and direct.sector == sector)):
        return direct
    planet = state.find_planet(text, galaxy, sector)
    if planet is not None:
        return planet
    for sid in sorted(state.ships):
        s = state.ships[sid]
        if galaxy is not None and (s.galaxy != galaxy or s.sector != sector):
            continue
        if s.name.lower() == wanted or sid.lower() == wanted:
            return s
    return None


def find_side(state: StateView, text: str):
    """A captain or a live planet by id or name (case-insensitive) - the counterparty of a peace order (CMD-10)."""
    wanted = text.strip().lower()
    direct = state.captains.get(text.strip())
    if direct is not None:
        return direct
    planet = state.find_planet(text)
    if planet is not None:
        return planet
    for cid in sorted(state.captains):
        c = state.captains[cid]
        if c.name.lower() == wanted or cid.lower() == wanted:
            return c
    return None


def find_captain(state: StateView, text: str) -> Captain | None:
    """A captain by id or name (case-insensitive)."""
    got = find_side(state, text)
    return got if isinstance(got, Captain) else None


def open_battle_between(state: StateView, a: str, b: str) -> Battle | None:
    for bid in sorted(state.battles):
        bt = state.battles[bid]
        if bt.open and bt.involves(a) and bt.involves(b):
            return bt
    return None


def battle_of_ship(state: StateView, ship) -> Battle | None:
    """The open battle the combatant's side is in within its sector (C10 ``in_battle``)."""
    for bid in sorted(state.battles):
        bt = state.battles[bid]
        if bt.open and bt.involves(ship.captain_id) and bt.galaxy == ship.galaxy and bt.sector == ship.sector:
            return bt
    return None


def enemies_in_sector(state: StateView, ship, enemy_side: str) -> list:
    """The enemy side's combatants in the ship's sector, nearest first (ties by id)."""
    ships = ships_of(state, enemy_side, ship.galaxy, ship.sector)
    ships.sort(key=lambda s: (chebyshev(s.cell, ship.cell), s.id))
    return ships


def exchange_row(attacker, defender, weapon: str, distance: int, hit: bool, damage: float, shields_after: float) -> dict:
    """One C10 ``exchanges[]`` entry."""
    return {"attacker": attacker.id, "defender": defender.id, "weapon": weapon, "distance": int(distance), "hit": bool(hit),
            "damage": round(float(damage), 4), "shields_after": round(float(shields_after), 4)}


def planet_event(stats: dict, planet_id: str, event: str, by: str) -> None:
    """C11 ``planet_events[]``: attacked | opened_fire | destroyed | peace | refused_trade."""
    stats.setdefault("planet_events", []).append({"planet": planet_id, "event": event, "by": by})


# --------------------------------------------------------------------------- the phase

@dataclass
class Turn:
    """Scratch for one TP-04: who died, how, and what each battle recorded."""

    state: StateView
    cfg: GameConfig
    rng: random.Random
    events: list[dict]
    stats: dict
    turn_no: int
    watched: tuple[Coord, Coord] | None
    dead: dict[str, str] = field(default_factory=dict)          # victim id -> killer id, in kill order
    touched: dict[str, Battle] = field(default_factory=dict)    # battles recorded this turn
    exchanges: dict[str, list[dict]] = field(default_factory=dict)

    def visible(self, battle: Battle) -> bool:
        """OB-01: the player's own battles and anything in the player's sector are shown; the rest is counted only."""
        if self.state.player_id in battle.sides:
            return True
        return self.watched is not None and (battle.galaxy, battle.sector) == self.watched

    def event(self, battle: Battle | None, type_: str, severity: str, text: str, ids: tuple[str, ...]) -> None:
        if battle is None or self.visible(battle):
            self.events.append({"phase": "TP-04", "type": type_, "severity": severity, "text": text, "ids": list(ids)})

    def event_for(self, ship, type_: str, severity: str, text: str, ids: tuple[str, ...]) -> None:
        """An event about one combatant: shown for the player's ships and for anything in the player's sector (OB-01)."""
        if ship.captain_id == self.state.player_id or (self.watched is not None and (ship.galaxy, ship.sector) == self.watched):
            self.events.append({"phase": "TP-04", "type": type_, "severity": severity, "text": text, "ids": list(ids)})

    def combatant(self, cid: str):
        return self.state.combatant(cid)

    def alive(self, cid: str) -> bool:
        return self.state.combatant(cid) is not None and cid not in self.dead

    def record(self, battle: Battle) -> None:
        self.touched[battle.id] = battle
        self.exchanges.setdefault(battle.id, [])

    def name(self, side_id: str) -> str:
        return self.state.side_name(side_id)


def phase_combat(state: StateView, cfg: GameConfig, rng: random.Random, by_ship: dict[str, list[Order]], events: list[dict], stats: dict,
                 watched: tuple[Coord, Coord] | None) -> None:
    """TP-04 (see the module docstring).  ``by_ship`` holds this turn's orders for every combatant, AI and planets included."""
    t = Turn(state, cfg, rng, events, stats, state.turn_no, watched)
    fires, peaces, surrenders, escapes = _collect(state, by_ship)
    _open_battles(t, fires)
    for bt in state.battles.values():
        if bt.open:
            t.record(bt)
    _planet_openings(t)
    _peace(t, peaces)
    _surrenders(t, surrenders)
    _escapes(t, escapes)
    _exchange(t, fires)
    _outcomes(t)
    _finish(t)


def _collect(state: StateView, by_ship: dict[str, list[Order]]) -> tuple[list, list, list, set[str]]:
    fires: list = []
    peaces: list = []
    surrenders: list = []
    escapes: set[str] = set()
    for sid in sorted(by_ship):
        ship = state.combatant(sid)
        if ship is None:
            continue
        for o in by_ship[sid]:
            if o.command == "fire":
                fires.append((ship, o))
            elif o.command == "peace":
                peaces.append((ship, o))
            elif o.command == SURRENDER:
                surrenders.append((ship, o))
            elif o.command == "escape":
                escapes.add(sid)
    return fires, peaces, surrenders, escapes


def _target_of(t: Turn, ship, order: Order):
    """(target, weapon, count, problem text) for a fire order, after movement (M3 section 6)."""
    try:
        spec = parse_fire(order.args)
    except CommandError as e:
        return None, "", 0, str(e)
    target = find_ship(t.state, spec.target)
    if target is None or not t.alive(target.id):
        return None, spec.weapon, spec.count, f"{ship.name}: target {spec.target!r} is gone (CMD-08)"
    if target.captain_id == ship.captain_id:
        return None, spec.weapon, spec.count, f"{ship.name}: {target.name} is one of your own ships (CMD-08)"
    if target.galaxy != ship.galaxy or target.sector != ship.sector:
        return None, spec.weapon, spec.count, f"{ship.name}: {target.name} left the sector before the exchange (CM-02)"
    d = chebyshev(ship.cell, target.cell)
    rng_ = range_of(spec.weapon, t.cfg)
    if d > rng_:
        return None, spec.weapon, spec.count, (f"{ship.name}: fire {spec.weapon} rejected - target {target.id} is {d} cells away after movement; "
                                               f"{spec.weapon} range {rng_} (CM-02)")
    return target, spec.weapon, spec.count, None


def open_battle(state: StateView, cfg: GameConfig, attacker, target, turn_no: int, rows: list[dict]) -> tuple[Battle, dict]:
    """CM-01 / CM-08 / D-119 / D-121 / D-145: the first *accepted* fire of either side opens the battle - the player's at
    submission, an AI's or a planet's at TP-01 - so the targeted side holds its cell for the exchange (D-128).  Strength is
    measured now, once; an opening captain's FR-06 row goes to ``rows`` (planets pay no respect).  Returns (battle, event)."""
    sides = (attacker.captain_id, target.captain_id)
    bt = Battle(state.new_battle_id(), sides, attacker.galaxy, attacker.sector, turn_no, attacker.captain_id,
                {cid: side_strength(state, cid, attacker.galaxy, attacker.sector, cfg) for cid in sides})
    state.battles[bt.id] = bt
    attacker.target = target.id
    if is_planet_id(attacker.captain_id):
        state.touch_planet(attacker)
    if is_planet_id(target.captain_id):
        state.touch_planet(target)
    sa, sb = bt.strength_at_open[sides[0]], bt.strength_at_open[sides[1]]
    a_name, t_name = state.side_name(attacker.captain_id), state.side_name(target.captain_id)
    if is_planet_id(attacker.captain_id):
        text = (f"{a_name} opens fire on {t_name}'s {target.name}: battle {bt.id} begins (strength {sa:g} vs {sb:g}); "
                f"a planet attacks a captain of low respect it can beat (EF-05, CM-11)")
        ids = ["EF-05", "CM-11", "CM-08", "OUT-04", "PR-02"]
    else:
        row = R.start_fight(attacker.captain_id, cfg)
        rows.append(row.to_dict())
        what = f"the planet {t_name}" if is_planet_id(target.captain_id) else f"{t_name}'s {target.name}"
        text = (f"{a_name}'s {attacker.name} opens fire on {what}: battle {bt.id} begins (strength {sa:g} vs {sb:g}); "
                f"starting a fight costs {a_name} {abs(row.respect)} respect (CM-01, FR-06)")
        ids = ["CM-01", "FR-06", "CM-08", "OUT-04"] + (["CM-11", "PR-02"] if is_planet_id(target.captain_id) else [])
    ev = {"phase": "TP-04", "type": "combat", "severity": "alert", "text": text, "ids": ids}
    return bt, ev


def _open_battles(t: Turn, fires: list) -> None:
    """Fallback for a fire order whose battle is not open yet (a loaded queue): open it now (CM-01)."""
    st = t.state
    for ship, order in fires:
        if not t.alive(ship.id):
            continue
        target, weapon, _, problem = _target_of(t, ship, order)
        if target is None:
            continue
        if open_battle_between(st, ship.captain_id, target.captain_id) is not None:
            continue
        bt, ev = open_battle(st, t.cfg, ship, target, t.turn_no, t.stats["reputation"])
        t.record(bt)
        t.stats["battles_opened"] = t.stats.get("battles_opened", 0) + 1
        if st.player_id not in bt.sides:
            t.stats["ai_vs_ai_opened"] = t.stats.get("ai_vs_ai_opened", 0) + 1
        if t.visible(bt):
            t.events.append(ev)


def _planet_openings(t: Turn) -> None:
    """C11 / PR-02: a battle opened this turn with a planet on a side records 'attacked' (a captain fired on it) or
    'opened_fire' (EF-05) once."""
    for bt in t.touched.values():
        if bt.opened_turn != t.turn_no:
            continue
        for side in bt.sides:
            if not is_planet_id(side):
                continue
            if bt.opened_by == side:
                planet_event(t.stats, side, "opened_fire", side)
            else:
                planet_event(t.stats, side, "attacked", bt.opened_by)


def _offer_from(t: Turn, ship, order: Order):
    try:
        spec = parse_peace(order.args)
    except CommandError as e:
        return None, None, "", str(e)
    cap = find_side(t.state, spec.captain)
    if cap is None:
        return None, None, spec.action, f"{ship.name}: no captain or planet {spec.captain!r} (CMD-10)"
    bt = open_battle_between(t.state, ship.captain_id, cap.id)
    if bt is None:
        return None, cap, spec.action, f"{ship.name}: no battle with {cap.name} to make peace in (CMD-10)"
    return bt, cap, spec.action, None


def _peace(t: Turn, peaces: list) -> None:
    """CM-09 / CB-03 / D-122: offers first (a mutual offer makes peace with no victor), then accepts, then rejects."""
    st = t.state
    accepts: list = []
    for ship, order in peaces:
        if not t.alive(ship.id):
            continue
        bt, cap, action, problem = _offer_from(t, ship, order)
        if problem or bt is None or cap is None:
            t.event_for(ship, "peace", "warning", problem or f"{ship.name}: peace order dropped", ("CMD-10",))
            continue
        me, other = ship.captain_id, cap.id
        if action == "offer":
            if cap.active_ship:
                ship.target = cap.active_ship
            if bt.offer_stands(other, t.turn_no):
                _close_peace(t, bt, victor=None, loser=None, note=f"Both {t.name(me)} and {t.name(other)} offered peace: no victor, nothing transfers (D-122)")
                continue
            bt.offers[me] = t.turn_no
            if me not in bt.offered:
                bt.offered.append(me)
            t.event(bt, "peace", "highlight", f"{t.name(me)} offers peace to {t.name(other)} - it stands this turn and next (CM-09, OUT-05)", ("CM-09", "OUT-05", "D-122"))
        elif action == "accept":
            accepts.append((ship, bt, cap))
        else:
            if bt.offers.pop(other, None) is not None:
                t.event(bt, "peace", "info", f"{t.name(me)} rejects {t.name(other)}'s peace offer - no cost (CM-09, OUT-05)", ("CM-09", "OUT-05"))
            else:
                t.event(bt, "peace", "warning", f"{t.name(me)}: no peace offer from {t.name(other)} stands to reject (CM-09)", ("CM-09",))
    for ship, bt, cap in accepts:
        if not bt.open:
            continue
        me, other = ship.captain_id, cap.id
        if bt.offer_stands(other, t.turn_no):
            _close_peace(t, bt, victor=me, loser=other, note=None)
        else:
            t.event(bt, "peace", "warning", f"{t.name(me)}: no peace offer from {t.name(other)} stands to accept (CM-09, D-122)", ("CM-09", "D-122"))


def _transfer_pct(t: Turn, loser: Captain, victor: Captain | None, pct: int) -> dict:
    """D-46 / D-122: X % of the loser's coins and of each item stack, rounded down, moved to the victor.  With no victor
    captain (a planet won) the coins are gone and the items go to the planet's stock, D-140 [ASSUMED]."""
    gold = int(loser.gold * pct // 100)
    silver = int(loser.silver * pct // 100)
    moved: dict[str, int] = {}
    for element in sorted(loser.items):
        n = int(loser.items[element] * pct // 100)
        if n > 0:
            moved[element] = n
    loser.gold -= gold
    loser.silver -= silver
    for element, n in moved.items():
        loser.take_item(element, n)
    if victor is not None:
        victor.gold += gold
        victor.silver += silver
        for element, n in moved.items():
            victor.add_item(element, n)
    return {"gold": gold, "silver": silver, "items": moved, "pct": int(pct)}


def _transfer_planet_pct(t: Turn, planet: Planet, victor: Captain | None, pct: int) -> dict:
    """D-145 / PR-02: X % of a planet's food, water and element stacks (planets hold no coins, D-140), rounded down, to the victor."""
    st, cfg = t.state, t.cfg
    prev = t.turn_no - 1
    food, water = planet.stock("food", prev, cfg), planet.stock("water", prev, cfg)
    f, w = int(food * pct // 100), int(water * pct // 100)
    moved: dict[str, int] = {}
    for element in sorted(planet.elements):
        n = int(planet.elements[element] * pct // 100)
        if n > 0:
            moved[element] = n
    planet.rebase(food - f, water - w, prev)
    for element, n in moved.items():
        left = planet.elements.get(element, 0) - n
        if left > 0:
            planet.elements[element] = left
        else:
            planet.elements.pop(element, None)
    st.touch_planet(planet)
    if victor is not None:
        victor.food += f
        victor.water += w
        for element, n in moved.items():
            victor.add_item(element, n)
    return {"gold": 0, "silver": 0, "items": moved, "food": f, "water": w, "pct": int(pct)}


def _side_shields(t: Turn, side_id: str, bt: Battle) -> float:
    return sum(s.shield_current for s in ships_of(t.state, side_id, bt.galaxy, bt.sector))


def _close_peace(t: Turn, bt: Battle, *, victor: str | None, loser: str | None, note: str | None) -> None:
    """CB-03: the side that did not offer is the victor and takes X % (D-46); mutual offers: X = 0, no victor."""
    st = t.state
    bt.ended, bt.ended_turn = "peace", t.turn_no
    if victor is None or loser is None:
        bt.victor, bt.transfer = None, {"gold": 0, "silver": 0, "items": {}, "pct": 0}
        t.event(bt, "peace", "highlight", f"Peace in battle {bt.id}: {note} (CB-03, OUT-05)", ("CB-03", "OUT-05", "D-122"))
        for side in bt.sides:
            if is_planet_id(side):
                planet_event(t.stats, side, "peace", side)
        return
    vside, lside = st.side(victor), st.side(loser)
    v_shields, l_shields = _side_shields(t, victor, bt), _side_shields(t, loser, bt)
    pct = peace_pct(v_shields, l_shields, t.cfg)
    bt.victor = victor
    vcap = vside if isinstance(vside, Captain) else None
    if isinstance(lside, Captain):
        bt.transfer = _transfer_pct(t, lside, vcap, pct)
    elif isinstance(lside, Planet):
        bt.transfer = _transfer_planet_pct(t, lside, vcap, pct)
    else:
        bt.transfer = {"gold": 0, "silver": 0, "items": {}, "pct": pct}
    tr = bt.transfer
    items = ", ".join(f"{n} {e}" for e, n in tr["items"].items()) or "no items"
    supplies = f", {tr['food']} food, {tr['water']} water" if "food" in tr else ""
    t.event(bt, "peace", "highlight",
            f"Peace between {t.name(victor)} and {t.name(loser)}: {t.name(loser)} offered, so {t.name(victor)} is the victor - "
            f"X = max(0, ({v_shields:g} - {l_shields:g}) / {v_shields:g}) x {t.cfg.peace_loot_max} = {pct} % transferred (CB-03, D-46)",
            ("CB-03", "D-46", "OUT-05"))
    t.event(bt, "loot", "highlight", f"Loot from {t.name(loser)}: {tr['gold']} gold, {tr['silver']} silver, {items}{supplies} ({pct} % of each stack, OUT-06)",
            ("OUT-06", "CB-03"))
    for side in bt.sides:
        if is_planet_id(side):
            planet_event(t.stats, side, "peace", loser)
    _reputation_for(t, bt, victor, loser)


def _surrenders(t: Turn, surrenders: list) -> None:
    """EF-02 / FL-02 / D-124: a weaker AI captain hands its ship to the player; the ship joins the fleet in place."""
    st = t.state
    for ship, order in surrenders:
        if not t.alive(ship.id) or not order.args or not isinstance(ship, Ship):
            continue
        to = st.captains.get(order.args[0])
        giver = st.captains.get(ship.captain_id)
        if to is None or giver is None or giver.is_player:
            continue
        bt = open_battle_between(st, giver.id, to.id)
        if bt is None:
            continue
        giver.ships = [s for s in giver.ships if s != ship.id]
        if giver.active_ship == ship.id:
            giver.active_ship = giver.ships[0] if giver.ships else None
        ship.captain_id = to.id
        ship.target = None
        to.ships.append(ship.id)
        st.queues.pop(ship.id, None)
        t.stats["captures"].append({"ship": ship.id, "from": giver.id, "to": to.id, "turn_no": t.turn_no})
        bt.ended, bt.ended_turn, bt.victor, bt.transfer = "peace", t.turn_no, to.id, None
        t.event(bt, "capture", "highlight",
                f"{giver.name} surrenders: the {ship.name} ({ship.cls.capitalize()}, {ship.shield_current:g}/{ship.shield_max:g} shields) joins "
                f"{to.name}'s fleet in place (EF-02, FL-02, OUT-11)", ("EF-02", "FL-02", "OUT-11", "D-124"))
        _reputation_for(t, bt, to.id, giver.id)
        if not giver.ships:
            del st.captains[giver.id]   # D-105: a shipless AI captain is removed the same turn
            t.stats["captains_removed"] = t.stats.get("captains_removed", 0) + 1
            _end_battles_of(t, giver.id, exclude=bt.id)


def _end_battles_of(t: Turn, side_id: str, exclude: str | None = None) -> None:
    """D-105 / M3 section 6: a captain (or planet) that leaves the game ends its other open battles (no victor)."""
    for bt in t.state.battles.values():
        if bt.open and bt.involves(side_id) and bt.id != exclude:
            bt.ended, bt.ended_turn, bt.victor, bt.transfer = "destroyed", t.turn_no, None, None
            t.record(bt)


def _escapes(t: Turn, escapes: set[str]) -> None:
    """CB-02 / D-119 / D-123: a battle with no pair of combatants in range after TP-02 ends by escape, for everyone at once."""
    st = t.state
    for bid in sorted(st.battles):
        bt = st.battles[bid]
        if not bt.open:
            continue
        a, b = bt.sides
        ships_a = [s for s in ships_of(st, a, bt.galaxy, bt.sector) if t.alive(s.id)]
        ships_b = [s for s in ships_of(st, b, bt.galaxy, bt.sector) if t.alive(s.id)]
        if not ships_a or not ships_b:
            gone = a if not ships_a else b
            cap = st.captains.get(gone)
            if cap is not None and any(sid in st.ships for sid in cap.ships):
                bt.ended, bt.ended_turn = "escape", t.turn_no     # warped or moved out of the sector
                t.event(bt, "escape", "info", f"Battle {bt.id} ends: {t.name(gone)} has left sector {bt.galaxy[0]},{bt.galaxy[1]}/{bt.sector[0]},{bt.sector[1]} (CB-02, OUT-05)",
                        ("CB-02", "OUT-05", "D-119"))
            else:
                bt.ended, bt.ended_turn = "destroyed", t.turn_no  # lost to a hazard, nobody won
                t.event(bt, "escape", "info", f"Battle {bt.id} ends: {t.name(gone)} has no ship left here (D-105)", ("D-105", "D-119"))
            continue
        in_range = any(pair_in_range(x, y, t.cfg) for x in ships_a for y in ships_b)
        escapers = [s for s in ships_a + ships_b if s.id in escapes]
        if not in_range:
            bt.ended, bt.ended_turn, bt.victor, bt.transfer = "escape", t.turn_no, None, None
            longest = max(weapon_range(s, t.cfg) for s in ships_a + ships_b)
            d = min(chebyshev(x.cell, y.cell) for x in ships_a for y in ships_b)
            for s in escapers:
                t.event(bt, "escape", "highlight", f"{s.name} escaped: {d} cells from the nearest enemy, beyond every weapon range ({longest}) - battle {bt.id} ends, nothing transfers (CB-02, CMD-11, OUT-05)",
                        ("CB-02", "CMD-11", "OUT-05"))
            if not escapers:
                t.event(bt, "escape", "info", f"Battle {bt.id} ends: nobody is within weapon range after movement ({d} cells, longest range {longest}) (CB-02, OUT-05)",
                        ("CB-02", "OUT-05", "D-119"))
        else:
            for s in escapers:
                near = min((y for y in (ships_b if s in ships_a else ships_a)), key=lambda y: (chebyshev(y.cell, s.cell), y.id))
                t.event(bt, "escape", "warning", f"{s.name}'s escape failed: still {chebyshev(near.cell, s.cell)} cells from {near.name}, within weapon range - the battle goes on (CB-02, CMD-11, OUT-05)",
                        ("CB-02", "CMD-11", "OUT-05"))


def _exchange(t: Turn, fires: list) -> None:
    """CM-06 / D-120: all torpedoes of both sides, then all phasers; attacker id order; HZ-09 after each landing hit."""
    st = t.state
    # only firers are looked up below, so only they are tested (a scan of every planet cost ~30 ms a turn)
    alive_at_start = {ship.id for ship, _ in fires if t.alive(ship.id)}
    plans: list = []
    for ship, order in sorted(fires, key=lambda so: so[0].id):
        if ship.id not in alive_at_start:
            continue
        target, weapon, count, problem = _target_of(t, ship, order)
        bt = open_battle_between(st, ship.captain_id, target.captain_id) if target is not None else None
        if target is None:
            t.event_for(ship, "combat", "warning", problem or f"{ship.name}: fire order dropped", ("CM-02", "CMD-08"))
            continue
        if bt is None:
            t.event_for(ship, "combat", "muted", f"{ship.name} holds fire: no open battle with {t.name(target.captain_id)} (CM-01)", ("CM-01",))
            continue
        ship.target = target.id
        plans.append((0 if weapon == TORPEDO else 1, ship, target, weapon, count, bt))
    plans.sort(key=lambda p: (p[0], p[1].id, p[2].id))
    for _, attacker, target, weapon, count, bt in plans:
        if attacker.id not in alive_at_start:
            continue
        if weapon == TORPEDO:
            _torpedo_volley(t, bt, attacker, target, count)
        else:
            _phaser_volley(t, bt, attacker, target)


def _land(t: Turn, bt: Battle, attacker, defender, weapon: str, distance: int, hit: bool, damage: float) -> None:
    """Apply one shot: shields floor at 0; a hit landing on 0 shields destroys (HZ-09, D-15)."""
    destroyed = False
    if hit:
        if defender.shield_current <= 0:
            destroyed = True
            t.dead[defender.id] = attacker.id
        else:
            defender.shield_current = max(0.0, defender.shield_current - damage)
        if isinstance(defender, Planet):
            t.state.touch_planet(defender)
    row = exchange_row(attacker, defender, weapon, distance, hit, damage if hit else 0.0, defender.shield_current)
    t.exchanges.setdefault(bt.id, []).append(row)
    verb = ("hit" if hit else "miss")
    tail = " - destroyed!" if destroyed else ""
    t.event(bt, "combat", "alert" if destroyed else ("info" if hit else "muted"),
            f"{attacker.name} -> {defender.name}: {weapon} {verb}, {damage if hit else 0:g} damage, shields left {defender.shield_current:g}{tail}",
            ("OUT-04", "CM-03" if weapon == TORPEDO else "CM-04", "HZ-09"))


def _torpedo_volley(t: Turn, bt: Battle, attacker, target, count: int) -> None:
    """CM-03: each torpedo rolls p = base - evade x target engine from the engine RNG (D-67); the rest of a volley at a dead target is held."""
    d = chebyshev(attacker.cell, target.cell)
    p = torpedo_hit_p(target.engine, t.cfg)
    n = min(int(count), int(attacker.torpedoes))
    fired = 0
    for _ in range(n):
        if not t.alive(target.id):
            break
        attacker.torpedoes -= 1
        fired += 1
        hit = t.rng.random() < p
        _land(t, bt, attacker, target, TORPEDO, d, hit, float(t.cfg.torpedo_damage))
    if isinstance(attacker, Planet) and fired:
        t.state.touch_planet(attacker)
    if fired < n:
        t.event(bt, "combat", "muted", f"{attacker.name} holds {n - fired} torpedo(es): {target.name} is already destroyed", ("HZ-09", "D-120"))


def _phaser_volley(t: Turn, bt: Battle, attacker, target) -> None:
    """CM-04: phasers always hit; damage = phasers x per_unit x (1 - d/3)."""
    if not t.alive(target.id):
        t.event(bt, "combat", "muted", f"{attacker.name} holds its phasers: {target.name} is already destroyed", ("HZ-09", "D-120"))
        return
    d = chebyshev(attacker.cell, target.cell)
    _land(t, bt, attacker, target, PHASER, d, True, round(phaser_damage(attacker.phasers, t.cfg.phaser_damage_per_unit, d), 6))


def _loot_planet(t: Turn, planet: Planet, taker: Captain | None) -> dict:
    """CM-11 / PR-02 / D-140: a destroyed planet hands all its food, water and elements (its torpedoes went with the stat loot)."""
    cfg = t.cfg
    prev = t.turn_no - 1
    food, water = planet.stock("food", prev, cfg), planet.stock("water", prev, cfg)
    items = {k: int(v) for k, v in sorted(planet.elements.items()) if int(v) > 0}
    if taker is not None:
        taker.food += food
        taker.water += water
        for element, n in items.items():
            taker.add_item(element, n)
    planet.rebase(0, 0, prev)
    planet.elements = {}
    return {"gold": 0, "silver": 0, "items": items, "food": food, "water": water, "pct": 100}


def _outcomes(t: Turn) -> None:
    """CB-01 / CB-04 / CM-11 / FL-04 / D-105 / D-123: kills, stat loot to the killer, coins (or a planet's stock) to the side that
    wiped the other, game over, planets removed."""
    st, cfg = t.state, t.cfg
    killers: dict[str, str] = dict(t.dead)
    # stat loot per kill (to a surviving killer)
    for victim_id, killer_id in killers.items():
        victim, killer = t.combatant(victim_id), t.combatant(killer_id)
        if victim is None or killer is None or killer_id in t.dead or isinstance(killer, Planet):
            continue
        before = (killer.torpedoes, killer.phasers, killer.engine, killer.warp)
        killer.torpedoes += int(victim.torpedoes)
        killer.phasers = min(float(cfg.phaser_max), max(killer.phasers, victim.phasers))
        killer.engine = min(float(cfg.impulse_max), max(killer.engine, victim.engine))
        killer.warp = min(float(cfg.warp_max), max(killer.warp, victim.warp))
        if isinstance(victim, Planet):
            victim.torpedoes = 0
        bt = open_battle_between(st, killer.captain_id, victim.captain_id)
        t.event(bt, "loot", "highlight",
                f"{killer.name} strips the {victim.name}: torpedoes {before[0]} -> {killer.torpedoes}, phasers {before[1]:g} -> {killer.phasers:g}, "
                f"engine {before[2]:g} -> {killer.engine:g}, warp {before[3]:g} -> {killer.warp:g} (max of own and victim, capped; shields untouched; CB-01, OUT-06)",
                ("CB-01", "OUT-06", "D-130"))
    # which battles ended by destruction, and who takes the coins / the stock
    for bid in sorted(st.battles):
        bt = st.battles[bid]
        if not bt.open:
            continue
        a, b = bt.sides
        left_a = [s for s in ships_of(st, a, bt.galaxy, bt.sector) if s.id not in t.dead]
        left_b = [s for s in ships_of(st, b, bt.galaxy, bt.sector) if s.id not in t.dead]
        if left_a and left_b:
            continue
        bt.ended, bt.ended_turn = "destroyed", t.turn_no
        if not left_a and not left_b:
            bt.victor, bt.transfer = None, None
            t.event(bt, "combat", "alert", f"Battle {bt.id}: both sides destroyed in the same exchange - nothing transfers (CB-01, D-123)", ("CB-01", "D-123"))
            continue
        loser, victor = (a, b) if not left_a else (b, a)
        last_kill = next((vid for vid in reversed(list(killers)) if t.combatant(vid) is not None and t.combatant(vid).captain_id == loser), None)
        killer_ship = t.combatant(killers[last_kill]) if last_kill is not None else None
        taker_id = killer_ship.captain_id if killer_ship is not None else victor
        bt.victor = victor
        taker = st.captains.get(taker_id)
        lside = st.side(loser)
        if isinstance(lside, Captain) and taker is not None and taker_id != loser:
            bt.transfer = _transfer_pct(t, lside, taker, 100)
            items = ", ".join(f"{n} {e}" for e, n in bt.transfer["items"].items()) or "no items"
            t.event(bt, "loot", "highlight", f"Loot from {lside.name}: {bt.transfer['gold']} gold, {bt.transfer['silver']} silver, {items} - all of it (CB-01, OUT-06)",
                    ("CB-01", "OUT-06", "D-123"))
        elif isinstance(lside, Planet):
            bt.transfer = _loot_planet(t, lside, taker)
            tr = bt.transfer
            items = ", ".join(f"{n} {e}" for e, n in tr["items"].items()) or "no elements"
            t.event(bt, "loot", "highlight", f"{lside.name} destroyed - looted {tr['food']} food, {tr['water']} water, {items} and its torpedoes (CM-11, PR-02, OUT-06)",
                    ("CM-11", "PR-02", "OUT-06", "D-140"))
        else:
            bt.transfer = None
        _reputation_for(t, bt, victor, loser)
    # remove the dead, announce, game over
    for victim_id, killer_id in killers.items():
        victim = t.combatant(victim_id)
        if victim is None:
            continue
        killer = t.combatant(killer_id)
        kname = f"the {killer.name} ({t.name(killer.captain_id)})" if killer is not None else "an enemy ship"
        if isinstance(victim, Planet):
            by = killer.captain_id if killer is not None else killer_id
            st.destroy_planet(victim)
            planet_event(t.stats, victim.id, "destroyed", by)
            t.stats["planets_destroyed"] = t.stats.get("planets_destroyed", 0) + 1
            bt = next((x for x in t.touched.values() if x.involves(victim.id)), None)
            t.event(bt, "planet", "alert", f"The planet {victim.name} was destroyed by {kname} and leaves the map - its cell is Void now (CM-11, PR-02)",
                    ("CM-11", "PR-02", "OUT-11", "D-145"))
            _end_battles_of(t, victim.id)
            continue
        cap_name = t.name(victim.captain_id)
        cell = victim.cell
        where = f"sector {victim.galaxy[0]},{victim.galaxy[1]}/{victim.sector[0]},{victim.sector[1]}"
        t.stats["destroyed"].append({"ship": victim.id, "captain": victim.captain_id, "cause": "combat", "by": killer_id, "cell": [cell[0], cell[1]]})
        lost = {"ship_class": victim.cls, "position": [[victim.galaxy[0], victim.galaxy[1]], [victim.sector[0], victim.sector[1]], [cell[0], cell[1]]],
                "shield_current": victim.shield_current, "shield_max": victim.shield_max, "hazard": None, "name": victim.name}
        bt = next((x for x in t.touched.values() if x.involves(victim.captain_id) and killer is not None and x.involves(killer.captain_id)), None)
        cap, was_active = st.remove_ship(victim.id)
        if cap is not None and cap.is_player and was_active:
            st.game_over_turn = t.turn_no
            t.stats["game_over"] = {"cause": f"destroyed in combat by {kname} in {where}", "kind": "combat", "by": killer_id, "cell": [cell[0], cell[1]]}
            t.stats["player_lost"] = lost
            t.events.append({"phase": "TP-04", "type": "destroyed", "severity": "alert",
                             "text": f"{victim.name} destroyed in combat by {kname} - game over at turn {t.turn_no}. Type 'new' to start again.",
                             "ids": ["CB-04", "OV-09b", "OUT-16", "CMD-19"]})
        elif cap is not None and cap.is_player:
            t.events.append({"phase": "TP-04", "type": "destroyed", "severity": "alert",
                             "text": f"{victim.name} destroyed in combat by {kname} - ship lost from your fleet (FL-04, OUT-11)", "ids": ["FL-04", "OUT-11"]})
        else:
            t.event(bt, "destroyed", "alert", f"{cap_name}'s {victim.name} destroyed in combat by {kname} (OUT-11, D-105)", ("OUT-11", "D-105"))
        if cap is not None and not cap.is_player and cap.id not in st.captains:
            t.stats["captains_removed"] = t.stats.get("captains_removed", 0) + 1
            _end_battles_of(t, cap.id)


def _reputation_for(t: Turn, bt: Battle, victor: str, loser: str) -> None:
    """FR-01...05 for a victor captain, FR-02 for an AI loser (D-121), from the strengths at the open; planets have no levels."""
    st = t.state
    sv, sl = float(bt.strength_at_open.get(victor, 0.0)), float(bt.strength_at_open.get(loser, 0.0))
    if victor in st.captains:
        for row in R.battle_won(victor, own_strength=sv, opponent_strength=sl, opponent_offered_peace=loser in bt.offered,
                                started_it=bt.opened_by == victor, cfg=t.cfg):
            t.stats["reputation"].append(row.to_dict())
    lcap = st.captains.get(loser)
    if lcap is not None and not lcap.is_player:
        t.stats["reputation"].append(R.battle_lost(loser, own_strength=sl, opponent_strength=sv, cfg=t.cfg).to_dict())


def _finish(t: Turn) -> None:
    """Record every battle touched this turn (C10) and drop the ended ones from the state."""
    st = t.state
    records = []
    for bid in sorted(t.touched):
        bt = t.touched[bid]
        records.append(bt.to_dict(t.exchanges.get(bid, [])))
    t.stats["battles"] = records
    t.stats["exchanges"] = sum(len(v) for v in t.exchanges.values())
    # CM-05: a combatant that was in a battle this turn (open or ended now) does not recharge this turn
    t.stats["battled"] = sorted({s.id for bt in t.touched.values() for cid in bt.sides for s in ships_of(st, cid, bt.galaxy, bt.sector)})
    for bid in list(st.battles):
        if not st.battles[bid].open:
            del st.battles[bid]
    open_now = len(st.battles)
    kills = len(t.dead)
    t.events.append({"phase": "TP-04", "type": "combat", "severity": "muted",
                     "text": f"Combat: {len(records)} battle(s) this turn, {t.stats['exchanges']} shot(s), {kills} destroyed; {open_now} battle(s) still open",
                     "ids": ["TP-04", "CM-07"]})
