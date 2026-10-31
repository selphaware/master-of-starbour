"""The endgame (M5 § 2b): eligibility, placement, guardians, CM-13, mastery and its after-effects, save/load (EG-01...08, SP-08, CM-13,
EF-06, SUP-05, OUT-15, D-157, D-158, D-159, C12)."""
from __future__ import annotations

import dataclasses

import pytest

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import endgame as EG
from starbour.engine import orders as O
from starbour.engine.phases import resolve_turn
from starbour.engine.server import Engine
from starbour.engine.state import LODESTONE_ID, GameState, from_snapshot, new_game
from starbour.world.generate import flat_sector, game_rng
from starbour.world.grid import chebyshev


def make_eligible(state: GameState, cfg: GameConfig, *, respect: int | None = None, specials: int | None = None) -> None:
    """--respect / --fear / --coins / --give as the gate drives them (D-162 (4))."""
    names = EG.special_elements(cfg)
    need = cfg.required_special_distinct if specials is None else specials
    state.set_levels(respect=cfg.respect_min + 5 if respect is None else respect, fear=cfg.fear_min + 5)
    state.set_coins(-(-cfg.wealth_min_silver // cfg.silver_per_gold), 0)
    state.give({n: 1 for n in names[:need]})


def strong(state: GameState) -> None:
    ship = state.active_ship()
    ship.torpedoes, ship.phasers, ship.shield_max, ship.shield_current, ship.engine, ship.warp = 999, 10.0, 5000.0, 5000.0, 10.0, 10.0


def deterministic_hits(cfg: GameConfig) -> GameConfig:
    return dataclasses.replace(cfg, torpedo_hit_base=1.0, torpedo_evade_per_engine=0.0)


# ---------------------------------------------------------------- eligibility (EG-01...04, D-157)

def test_eligibility_reads_the_four_thresholds(cfg: GameConfig) -> None:
    cap = {"gold": 100, "silver": 0, "fear": 95, "respect": 85, "items": {"orichalcum": 1, "adamantine": 2, "iridium": 9}}
    rows = EG.eligibility(cap, cfg)
    assert rows["wealth"]["ok"] and rows["wealth"]["value"] == 100 * cfg.silver_per_gold
    assert rows["fear"]["ok"] and not rows["respect"]["ok"] and rows["respect"] == {"value": 85, "min": cfg.respect_min, "ok": False}
    assert rows["specials"] == {"value": 2, "min": cfg.required_special_distinct, "ok": False}, "distinct Mythical elements, units do not count (D-157)"
    assert not rows["ok"]
    cap.update(respect=90, items={"orichalcum": 1, "adamantine": 1, "aetherite": 1})
    assert EG.eligibility(cap, cfg)["ok"]
    assert EG.placement_threshold(cfg) == 64, "50 % of the 128-sector diameter (M5 § 5)"


# ---------------------------------------------------------------- placement (SP-08, EG-05, EG-06, D-158)

def test_placement_happens_once_at_tp08_far_away_in_a_guarded_system(cfg: GameConfig) -> None:
    state = new_game(1234, cfg)
    make_eligible(state, cfg)
    rng = game_rng(1234)
    before = len(state.captains)
    _, events = resolve_turn(state, cfg, rng, ai_passive=True)
    ev = [e for e in events if e["type"] == "eligible"]
    assert len(ev) == 1 and ev[0]["severity"] == "highlight" and state.eligible_turn == 1 and state.lodestone is not None
    ld = state.lodestone
    assert ld["sector"] in ev[0]["text"] and ld["placed_turn"] == 1 and ld["picked_up_turn"] is None
    g, s = (int(v) for v in ld["sector"].split("/")[0].split(",")), (int(v) for v in ld["sector"].split("/")[1].split(","))
    g, s = tuple(g), tuple(s)
    ship = state.active_ship()
    dist = chebyshev(flat_sector(g, s, cfg.galaxy_size), flat_sector(ship.galaxy, ship.sector, cfg.galaxy_size))
    assert dist == ld["distance"] >= EG.placement_threshold(cfg)
    sector = state.world.sectors[(g, s)]
    assert EG.is_system(sector) and sector.terrain_at(ld["cell"]) is None
    item = next(i for i in sector.items if i.id == LODESTONE_ID)
    assert item.ultimate and item.cell == tuple(ld["cell"]) and not item.revealed
    assert len(ld["guardians"]) == cfg.guard_ships == len(state.guardians()) == state.guardians_alive()
    assert len(state.captains) == before + cfg.guard_ships and state.population() == before, "guardians are not population (D-158)"
    cells = set()
    for cid in ld["guardians"]:
        cap = state.captains[cid]
        assert cap.guardian and len(cap.ships) == 1
        gship = state.ships[cap.ships[0]]
        assert gship.cls == "bastion" and (gship.galaxy, gship.sector) == (g, s) and 1 <= chebyshev(gship.cell, ld["cell"]) <= cfg.torpedo_range
        cells.add(gship.cell)
    assert len(cells) == cfg.guard_ships, "distinct cells"
    row = state.lodestone_row()
    assert set(row) == {"sector", "cell", "distance", "placed_turn", "guardians", "guardians_alive", "picked_up_turn"}
    # checked once: raising nothing, lowering the levels afterwards changes nothing
    state.player.respect = 0
    resolve_turn(state, cfg, rng, ai_passive=True)
    assert state.lodestone is ld and not [e for e in state.last_stats.get("events", [])]
    snap = state.snapshot()
    assert snap["lodestone"] == row and snap["eligible_turn"] == 1
    # the save carries the item even though the placed sector was never visited (C6)
    assert ld["sector"] not in snap["sectors"]
    back = from_snapshot(snap, cfg)
    assert any(i.id == LODESTONE_ID for i in back.world.sectors[(g, s)].items) and back.snapshot() == snap


def test_not_eligible_when_one_threshold_is_short(small_state: GameState, small_cfg: GameConfig) -> None:
    for kw in ({"respect": small_cfg.respect_min - 1}, {"specials": small_cfg.required_special_distinct - 1}):
        state = new_game(99, small_cfg)
        make_eligible(state, small_cfg, **kw)
        rng = game_rng(99)
        for _ in range(3):
            _, events = resolve_turn(state, small_cfg, rng, ai_passive=True)
            assert not [e for e in events if e["type"] == "eligible"] and state.lodestone is None and state.eligible_turn is None


def test_placement_is_reproducible(small_cfg: GameConfig) -> None:
    rows = []
    for _ in range(2):
        state = new_game(99, small_cfg)
        make_eligible(state, small_cfg)
        resolve_turn(state, small_cfg, game_rng(99), ai_passive=True)
        rows.append(state.lodestone_row())
    assert rows[0] == rows[1] and rows[0] is not None, "same seed, same orders, same placement (D-67)"


# ---------------------------------------------------------------- guardians (CM-13, D-158)

def placed_small_state(cfg: GameConfig) -> tuple[GameState, GameConfig, dict]:
    cfg = deterministic_hits(cfg)
    state = new_game(99, cfg)
    make_eligible(state, cfg)
    resolve_turn(state, cfg, game_rng(99), ai_passive=True)
    assert state.lodestone is not None
    return state, cfg, state.lodestone


def test_guardians_fire_on_arrival_regardless_of_ratio_and_never_yield(small_cfg: GameConfig) -> None:
    state, cfg, ld = placed_small_state(small_cfg)
    g = tuple(int(v) for v in ld["sector"].split("/")[0].split(","))
    s = tuple(int(v) for v in ld["sector"].split("/")[1].split(","))
    strong(state)
    ship = state.teleport((g, s, tuple(ld["cell"])))
    state.player.fear = 95          # >= attack_fear_max and a ratio far below attack_ratio_min: CM-10 would refuse both ways
    rng = game_rng(99)
    _, events = resolve_turn(state, cfg, rng)
    stats = state.last_stats
    attackers = {ex["attacker"] for b in stats["battles"] for ex in b.get("exchanges", [])}
    gships = {state.captains[c].ships[0] for c in ld["guardians"]}
    assert gships & attackers, "guardians fire the turn the ship is in the sector (TP-04 after movement)"
    assert any(b["opened_by"] in ld["guardians"] for b in stats["battles"])
    assert any("guardian" in d.text for d in stats["ai_decisions"])
    # wear one guardian down below the peace threshold: still no peace, escape or surrender, nobody leaves
    target = state.ships[state.captains[ld["guardians"][0]].ships[0]]
    target.shield_current = 1.0
    for _ in range(3):
        _, events = resolve_turn(state, cfg, rng)
        texts = " ".join(e["text"] for e in events).lower()
        assert "offers peace" not in texts and "surrenders" not in texts and "escaped" not in texts
        for cid in ld["guardians"]:
            if cid in state.captains:
                gs_ = state.ships[state.captains[cid].ships[0]]
                assert (gs_.galaxy, gs_.sector) == (g, s) and state.captains[cid].guardian
    assert ship.id in state.ships and state.game_over is False


def test_pickup_lodestone_is_refused_while_a_guardian_lives(small_cfg: GameConfig) -> None:
    state, cfg, ld = placed_small_state(small_cfg)
    g = tuple(int(v) for v in ld["sector"].split("/")[0].split(","))
    s = tuple(int(v) for v in ld["sector"].split("/")[1].split(","))
    strong(state)
    ship = state.teleport((g, s, tuple(ld["cell"])))
    scan = O.apply_immediate(state, parse("scan", ship.id), cfg)
    assert cfg.ultimate_item_name in scan.text and LODESTONE_ID in scan.text
    rej = O.submit(state, parse("pickup lodestone", ship.id), cfg)
    assert isinstance(rej, O.Rejected) and rej.reason == "the guardians still hold the sector (CM-13)"
    snap = state.snapshot()
    it = next(i for i in snap["sectors"][ld["sector"]]["items"] if i["id"] == LODESTONE_ID)
    assert it["revealed"] is True and it["ultimate"] is True and it["element"] == LODESTONE_ID


# ---------------------------------------------------------------- mastery (EG-07, EF-06, SUP-05, D-159)

def kill_guardians(state: GameState) -> None:
    for cid in list(state.lodestone["guardians"]):
        cap = state.captains.get(cid)
        if cap is not None:
            for sid in list(cap.ships):
                state.remove_ship(sid)
    assert state.guardians_alive() == 0


def test_mastery_fires_once_and_locks_everything(small_cfg: GameConfig) -> None:
    state, cfg, ld = placed_small_state(small_cfg)
    g = tuple(int(v) for v in ld["sector"].split("/")[0].split(","))
    s = tuple(int(v) for v in ld["sector"].split("/")[1].split(","))
    kill_guardians(state)
    ship = state.teleport((g, s, tuple(ld["cell"])))
    rng = game_rng(99)
    O.apply_immediate(state, parse("scan", ship.id), cfg)
    assert isinstance(O.submit(state, parse("pickup lodestone", ship.id), cfg), O.Accepted)
    food0, water0, gold0, silver0 = state.player.food, state.player.water, state.player.gold, state.player.silver
    _, events = resolve_turn(state, cfg, rng)
    ev = [e for e in events if e["type"] == "mastery"]
    assert len(ev) == 1 and "MASTER OF STARBOUR" in ev[0]["text"] and state.mastery_turn == state.turn_no and state.last_stats["mastery"]["captain"] == state.player_id
    cap = state.player
    assert cap.master and cap.infinite_wealth and cap.fear == cap.respect == 100 and state.lodestone["picked_up_turn"] == state.turn_no
    assert (ship.shield_max, ship.shield_current, ship.phasers, ship.engine, ship.warp, ship.torpedoes) == \
        (cfg.shield_cap, cfg.shield_cap, cfg.phaser_max, cfg.impulse_max, cfg.warp_max, cfg.master_torpedoes)
    assert (cap.food, cap.water) == (food0, water0), "a Master's supplies never fall (SUP-05)"
    assert (cap.gold, cap.silver) == (gold0, silver0)
    # a second turn: still master, nothing moves, a started fight's FR-06 is ignored (levels locked)
    state.pending_reputation.append({"captain": cap.id, "rule": "FR-06", "fear": 0, "respect": -5, "scale": 1.0})
    ship.torpedoes = 3
    ship.shield_current = 10.0
    resolve_turn(state, cfg, rng)
    assert cap.fear == cap.respect == 100 and ship.torpedoes == cfg.master_torpedoes and ship.shield_current == cfg.shield_cap
    assert (cap.food, cap.water) == (food0, water0) and state.last_stats.get("mastery") is None, "the event fires once"
    snap = state.snapshot()
    assert snap["captains"][cap.id]["master"] is True and snap["captains"][cap.id]["infinite_wealth"] is True and snap["mastery_turn"] == state.mastery_turn
    back = from_snapshot(snap, cfg)
    assert back.player.master and back.snapshot() == snap, "master survives save/load (C6)"
    # the Engine publishes the event only in the turn's stats, so a resumed engine never re-emits it (D-159)
    engine = Engine(cfg, seed=99, bus=False, resume=False)
    engine.state = back
    assert engine.state.last_stats.get("mastery") is None
    payload = engine.mastery_payload({"captain": cap.id, "item": cfg.ultimate_item_name})
    assert payload["captain"] == cap.id and payload["item"] == cfg.ultimate_item_name and "MASTER OF STARBOUR" in payload["cause"]


def test_master_trades_for_nothing_and_every_ship_yields(small_cfg: GameConfig) -> None:
    from starbour.engine import economy as E
    from starbour.engine import ai
    state, cfg, ld = placed_small_state(small_cfg)
    kill_guardians(state)
    cap = state.player
    ship = state.active_ship()
    cap.master = cap.infinite_wealth = True
    cap.fear = cap.respect = 100
    cap.gold, cap.silver = 0, 0
    planet = next(p for p in state.world.planets.values() if not p.destroyed)
    assert E.wealth(cap, cfg) == E.INFINITE_WEALTH and E.pay(cap, 10**6, cfg) and (cap.gold, cap.silver) == (0, 0)
    food_before = cap.food
    res = E.resolve_buy(cap, ship, planet, "food", 5, cfg, 1)
    assert res.ok and res.row["gave"] == {} and res.row["price_factor"] == pytest.approx(E.price_factor(100, cfg.planet_price_band, selling=False))
    assert cap.food == food_before + 5 and (cap.gold, cap.silver) == (0, 0)
    assert E.trades_with(planet, cap, cfg) and E.refusal(planet, cap, cfg) is None
    # an AI next to the Master never opens; fired upon, it surrenders that turn (EF-06, FL-02)
    sector = state.sector_of(ship)
    free = [c for c in sector.grid.free_cells() if chebyshev(c, ship.cell) == 1]
    foe_cap, foe = state.spawn("corsair", (ship.galaxy, ship.sector, free[0]), fear=0, respect=40)
    foe.engine = foe.warp = 0.0        # parked beside the Master, as the gate does (D-162 (6))
    rng = game_rng(5)
    for _ in range(3):
        resolve_turn(state, cfg, rng)
        assert not any(b["opened_by"] == foe_cap.id for b in state.last_stats["battles"]), "no AI opens fire on a Master (EF-06)"
    assert isinstance(O.submit(state, parse(f"fire torpedo {foe.id} 1", ship.id), cfg), O.Accepted)
    resolve_turn(state, cfg, rng)
    caps = state.last_stats["captures"]
    assert caps and caps[0]["ship"] == foe.id and caps[0]["to"] == cap.id and foe.captain_id == cap.id
    assert cap.fear == cap.respect == 100
    assert not ai.attack_orders(foe_cap, foe, [foe, ship], state, cfg) if foe_cap.id in state.captains else True
