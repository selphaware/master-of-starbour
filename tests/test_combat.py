"""Ship combat - CM-01...12, CB-01...04, HZ-09, FL-02/03, MV-06, D-119...D-124, D-130 (specs/M3.md section 5 vectors).

Scenarios spawn an opponent with the D-126 debug placement in the small world, clear the sector so no
hazard interferes, and resolve turns with a passive AI (D-125) so only the scripted side takes the
initiative.  The pure formulas are checked against the numbers in specs/M3.md section 5."""
from __future__ import annotations

import random

import pytest

from starbour.commands.parse import QUEUED, Order, parse
from starbour.config import GameConfig
from starbour.engine import combat as C
from starbour.engine import orders as O
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState, from_snapshot, new_game
from starbour.entities.ship import Ship
from starbour.world.generate import game_rng


def run(state: GameState, cfg: GameConfig, n: int = 1, rng: random.Random | None = None, passive: bool = True):
    rng = rng or game_rng(state.seed)
    events: list[dict] = []
    for _ in range(n):
        state, events = resolve_turn(state, cfg, rng, ai_passive=passive)
    return state, events


def texts(events: list[dict], phase: str | None = None) -> list[str]:
    return [e["text"] for e in events if phase is None or e["phase"] == phase]


def arena(state: GameState, cell=(5, 5)) -> Ship:
    """The player's ship alone in a terrain-free sector at ``cell``."""
    ship = state.active_ship()
    sector = state.sector_of(ship)
    for c in list(sector.grid.coords()):
        sector.grid.remove(*c)
    for sid in list(state.ships):
        s = state.ships[sid]
        if s.captain_id != state.player_id and s.galaxy == ship.galaxy and s.sector == ship.sector:
            state.remove_ship(sid)
    state.teleport((ship.galaxy, ship.sector, cell))
    return ship


def spawn(state: GameState, cls: str, cell, *, fear=None, respect=None, torpedoes=None, phasers=None, shield_max=None, engine=None):
    ship = state.active_ship()
    cap, s = state.spawn(cls, (ship.galaxy, ship.sector, cell), fear, respect)
    if torpedoes is not None:
        s.torpedoes = torpedoes
    if phasers is not None:
        s.phasers = phasers
    if shield_max is not None:
        s.shield_max = s.shield_current = float(shield_max)
    if engine is not None:
        s.engine = float(engine)
    return cap, s


def submit(state: GameState, cfg: GameConfig, text: str) -> O.Accepted:
    res = O.submit(state, parse(text, state.player.active_ship), cfg)
    assert isinstance(res, O.Accepted), getattr(res, "reason", res)
    return res


def class_strength(cfg: GameConfig, name: str) -> float:
    """CM-08 from the ini: shield_max + torpedoes x torpedo_damage + phasers x phaser_damage_per_unit x 3 (never a literal)."""
    spec = cfg.ships[name]
    return spec.shield_max + spec.torpedoes * cfg.torpedo_damage + spec.phasers * cfg.phaser_damage_per_unit * 3.0


# --------------------------------------------------------------------------- formulas (M3 section 5)

def test_section_5_vectors(cfg: GameConfig) -> None:
    assert [C.phaser_damage(3, cfg.phaser_damage_per_unit, d) for d in (0, 1, 2)] == pytest.approx([15.0, 10.0, 5.0])
    assert [round(C.torpedo_hit_p(e, cfg), 2) for e in (4, 6, 10, 0)] == [0.58, 0.52, 0.40, 0.70]
    k, c, b = (cfg.ships[n] for n in ("kestrel", "corsair", "bastion"))
    strengths = [C.ship_strength(Ship.of_class("s", "x", spec, "c", ((0, 0), (0, 0), (0, 0))), cfg) for spec in (k, c, b)]
    assert strengths == [class_strength(cfg, n) for n in ("kestrel", "corsair", "bastion")]
    assert round(strengths[1] / strengths[0], 2) >= cfg.attack_ratio_min, "a Corsair may open on a Kestrel (EF-01)"
    assert strengths[2] > strengths[1] > strengths[0]
    assert C.peace_pct(40, 10, cfg) == 37 and C.peace_pct(10, 40, cfg) == 0 and C.peace_pct(0, 0, cfg) == 0
    assert C.ai_peace_threshold(0, cfg) == 30 and C.ai_peace_threshold(100, cfg) == 60 and C.ai_peace_threshold(10, cfg) == pytest.approx(33.0)
    assert C.weapon_range(Ship.of_class("s", "x", k, "c", ((0, 0), (0, 0), (0, 0))), cfg) == cfg.torpedo_range
    unarmed = Ship.of_class("s", "x", k, "c", ((0, 0), (0, 0), (0, 0)))
    unarmed.torpedoes, unarmed.phasers = 0, 0.0
    assert C.weapon_range(unarmed, cfg) == 0


# --------------------------------------------------------------------------- opening and the exchange (CM-01...06)

def test_fire_opens_the_battle_at_acceptance_and_the_exchange_lands(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "kestrel", (6, 5))
    res = submit(small_state, small_cfg, f"fire phaser {foe.id}")
    assert "battle b-001" in res.text and "FR-06" in res.text
    bt = small_state.battles["b-001"]
    assert bt.open and bt.opened_by == small_state.player_id and set(bt.sides) == {small_state.player_id, cap.id}
    kestrel = class_strength(small_cfg, "kestrel")
    assert bt.strength_at_open == {small_state.player_id: kestrel, cap.id: kestrel}
    assert small_state.pending_reputation == [{"captain": small_state.player_id, "rule": "FR-06", "fear": 0, "respect": -5, "scale": 1.0}]
    snap = small_state.snapshot()
    assert snap["ships"][ship.id]["in_battle"] == "b-001" and snap["ships"][ship.id]["target"] == foe.id and snap["battles"][0]["id"] == "b-001"
    state, events = run(small_state, small_cfg)
    rows = state.last_stats["battles"][0]["exchanges"]
    mine = [r for r in rows if r["attacker"] == ship.id]
    assert mine == [{"attacker": ship.id, "defender": foe.id, "weapon": "phaser", "distance": 1, "hit": True, "damage": 10.0, "shields_after": 50.0}]
    assert [r["weapon"] for r in rows] == sorted([r["weapon"] for r in rows], key=lambda w: w != "torpedo"), "all torpedoes before all phasers (CM-06)"
    assert foe.shield_current == 50.0 and foe.cell == (6, 5), "a passive AI in battle fires back and holds its cell (D-125, D-128)"
    assert state.player.respect == small_cfg.respect_start - small_cfg.respect_start_fight_delta
    assert state.last_stats["reputation"][0]["rule"] == "FR-06" and any("FR-06" in t for t in texts(events, "TP-07"))
    assert any("-> " in t and "phaser hit" in t for t in texts(events, "TP-04")), "OUT-04 line"
    assert "no recharge in battle" in " ".join(texts(events, "TP-06"))


def test_fire_rejections_name_range_and_distance(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    _, foe = spawn(small_state, "kestrel", (8, 5))
    rej = O.submit(small_state, parse(f"fire phaser {foe.id}", ship.id), small_cfg)
    assert isinstance(rej, O.Rejected) and rej.reason == f"target {foe.id} is 3 cells away; phaser range 2 (CM-02)"
    assert not small_state.battles
    assert isinstance(O.submit(small_state, parse(f"fire torpedo {foe.id} 7", ship.id), small_cfg), O.Rejected)
    assert isinstance(O.submit(small_state, parse(f"fire torpedo {ship.id}", ship.id), small_cfg), O.Rejected)
    assert isinstance(O.submit(small_state, parse("fire torpedo nobody", ship.id), small_cfg), O.Rejected)
    ok = submit(small_state, small_cfg, f"fire torpedo '{foe.name}' 3")
    assert ok.queued == 1 and small_state.battles
    foe.position = (foe.galaxy, foe.sector, (6, 5))
    assert submit(small_state, small_cfg, f"fire phaser {foe.id}").queued == 2, "a torpedo and a phaser volley are different kinds (D-120)"
    assert "replaced" in submit(small_state, small_cfg, f"fire torpedo {foe.id} 2").text
    rej2 = O.submit(small_state, parse("peace offer nobody", ship.id), small_cfg)
    assert isinstance(rej2, O.Rejected) and "CMD-10" in rej2.reason


def test_torpedo_volley_is_seeded_and_spends_ammo(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    _, foe = spawn(small_state, "bastion", (6, 5), torpedoes=0, phasers=0, engine=0)
    submit(small_state, small_cfg, f"fire torpedo {foe.id} 3")
    state, _ = run(small_state, small_cfg)
    rows = [r for r in state.last_stats["battles"][0]["exchanges"] if r["attacker"] == ship.id]
    assert len(rows) == 3 and ship.torpedoes == 3
    assert all(r["damage"] == (small_cfg.torpedo_damage if r["hit"] else 0.0) for r in rows)
    # same seed, same orders -> the same hits (D-67)
    again = new_game(small_state.seed, small_cfg)
    arena(again)
    _, foe2 = spawn(again, "bastion", (6, 5), torpedoes=0, phasers=0, engine=0)
    submit(again, small_cfg, f"fire torpedo {foe2.id} 3")
    again, _ = run(again, small_cfg)
    assert [r["hit"] for r in again.last_stats["battles"][0]["exchanges"]] == [r["hit"] for r in rows]


# --------------------------------------------------------------------------- outcomes (CB-01...04)

def test_destruction_loots_the_victim_and_removes_the_captain(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "corsair", (5, 5), torpedoes=0, engine=0)   # immobile: at 0 shields a mobile AI would flee (EF-02)
    foe.shield_current = 0.0
    cap.items = {"iridium": 2}
    captains = len(small_state.captains)
    submit(small_state, small_cfg, f"fire phaser {foe.id}")
    state, events = run(small_state, small_cfg)
    assert foe.id not in state.ships and cap.id not in state.captains and len(state.captains) == captains - 1
    d = state.last_stats["destroyed"]
    assert d == [{"ship": foe.id, "captain": cap.id, "cause": "combat", "by": ship.id, "cell": [5, 5]}]
    b = state.last_stats["battles"][0]
    assert b["ended"] == "destroyed" and b["victor"] == state.player_id and b["transfer"] == {"gold": 5, "silver": 50, "items": {"iridium": 2}, "pct": 100}
    p = state.player
    assert (p.gold, p.silver, p.items) == (10, 100, {"iridium": 2})
    assert (ship.torpedoes, ship.phasers, ship.engine, ship.warp, ship.shield_max) == (6, 5.0, 4.0, 5.0, 60.0), "max(own, victim) capped (CB-01)"
    assert ship.shield_current == 60.0 - 25.0, "the Corsair's phaser landed; loot never restores shields and no recharge on a battle turn"
    rows = {r["rule"] for r in state.last_stats["reputation"] if r["captain"] == state.player_id}
    assert rows == {"FR-06", "FR-01", "FR-04", "FR-05"}, ("at the open the Corsair had 0 shields and no torpedoes (strength 75 vs the Kestrel's): weaker, "
                                                           "it offered peace (immobile, EF-02) and the player started it - FR-04 and FR-05 stack (FR-09)")
    assert state.last_stats["battles"][0]["strength_at_open"] == {state.player_id: class_strength(small_cfg, "kestrel"), cap.id: 75.0}
    assert not state.battles and not state.game_over
    assert any("Loot from" in t for t in texts(events, "TP-04"))


def test_mutual_annihilation_transfers_nothing(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "kestrel", (5, 5), torpedoes=0, engine=0)
    ship.shield_current = foe.shield_current = 0.0
    submit(small_state, small_cfg, f"fire phaser {foe.id}")
    state, _ = run(small_state, small_cfg)
    assert {d["ship"] for d in state.last_stats["destroyed"]} == {ship.id, foe.id}
    b = state.last_stats["battles"][0]
    assert b["ended"] == "destroyed" and b["victor"] is None and b["transfer"] is None
    assert state.game_over and state.last_stats["game_over"]["kind"] == "combat" and "combat" in state.last_stats["game_over"]["cause"]
    assert state.last_stats["game_over"]["by"] == foe.id


def test_escape_ends_the_battle_only_beyond_every_range(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    _, foe = spawn(small_state, "bastion", (7, 5), torpedoes=0, phasers=1, engine=0)
    assert isinstance(O.submit(small_state, parse("escape -1 +0", ship.id), small_cfg), O.Rejected), "escape needs a battle (CMD-11)"
    submit(small_state, small_cfg, f"fire phaser {foe.id}")
    state, _ = run(small_state, small_cfg)
    submit(state, small_cfg, "escape -1 +0")
    state, events = run(state, small_cfg)
    assert state.battles and "escape failed" in " ".join(texts(events, "TP-04"))
    submit(state, small_cfg, "escape -3 +0")        # distance 6 > torpedo range 5
    state, events = run(state, small_cfg)
    assert ship.cell == (1, 5) and not state.battles
    b = state.last_stats["battles"][0]
    assert b["ended"] == "escape" and b["victor"] is None and b["transfer"] is None
    assert state.last_stats["reputation"] == [] and any("escaped" in t for t in texts(events, "TP-04"))


def test_peace_victor_is_the_side_that_did_not_offer(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "bastion", (6, 5), torpedoes=0, phasers=0, engine=0)
    submit(small_state, small_cfg, f"fire phaser {foe.id}")
    state, _ = run(small_state, small_cfg)                      # foe at 90
    submit(state, small_cfg, f"peace offer {cap.name}")
    state, events = run(state, small_cfg)
    assert any("offers peace" in t for t in texts(events, "TP-04")) and state.battles["b-001"].offer_stands(state.player_id, 2)
    state, events = run(state, small_cfg)                       # the weaker AI accepts the standing offer (D-125)
    b = state.last_stats["battles"][0]
    assert b["ended"] == "peace" and b["victor"] == cap.id
    pct = C.peace_pct(90.0, 60.0, small_cfg)
    assert b["transfer"] == {"gold": 5 * pct // 100, "silver": 50 * pct // 100, "items": {}, "pct": pct}
    assert state.player.silver == 50 - 50 * pct // 100 and cap.silver == 50 + 50 * pct // 100
    assert [r["rule"] for r in state.last_stats["reputation"] if r["captain"] == cap.id] == ["FR-01", "FR-03"]
    assert not state.battles and state.player.respect == small_cfg.respect_start - small_cfg.respect_start_fight_delta


def test_mutual_peace_offers_make_peace_with_no_victor(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "kestrel", (6, 5))
    submit(small_state, small_cfg, f"fire phaser {foe.id}")
    state, _ = run(small_state, small_cfg)
    stats = {"battles": [], "reputation": [], "captures": [], "destroyed": []}
    by_ship = {ship.id: [Order("peace", ("offer", cap.id), ship.id, QUEUED)], foe.id: [Order("peace", ("offer", state.player_id), foe.id, QUEUED)]}
    events: list[dict] = []
    state.turn_no += 1
    C.phase_combat(state, small_cfg, game_rng(1), by_ship, events, stats, None)
    b = stats["battles"][0]
    assert b["ended"] == "peace" and b["victor"] is None and b["transfer"]["pct"] == 0 and stats["reputation"] == []
    assert any("no victor" in e["text"] for e in events)


def test_surrender_captures_the_ship_in_place(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    small_state.player.fear = small_cfg.yield_fear_min
    cap, foe = spawn(small_state, "bastion", (6, 5), torpedoes=0, phasers=0, engine=0)
    foe.shield_current = 10.0
    submit(small_state, small_cfg, f"fire phaser {foe.id}")
    state, events = run(small_state, small_cfg)
    assert state.last_stats["captures"] == [{"ship": foe.id, "from": cap.id, "to": state.player_id, "turn_no": 1}]
    assert foe.captain_id == state.player_id and foe.id in state.player.ships and cap.id not in state.captains
    assert foe.cell == (6, 5) and foe.shield_current == 10.0 and foe.id not in state.queues
    assert state.last_stats["battles"][0]["ended"] == "peace" and any("surrenders" in t for t in texts(events, "TP-04"))
    snap = state.snapshot()
    assert snap["captains"][state.player_id]["ships"] == [ship.id, foe.id]


def test_fleet_moves_with_the_active_ship(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state, cell=(2, 5))
    cap = small_state.player
    same = Ship.of_class(small_state.new_ship_id(), "Second Tern", small_cfg.ships["corsair"], cap.id, ship.position)
    far = Ship.of_class(small_state.new_ship_id(), "Far Plover", small_cfg.ships["corsair"], cap.id, (ship.galaxy, ship.sector, (9, 9)))
    small_state.ships[same.id] = same
    small_state.ships[far.id] = far
    cap.ships += [same.id, far.id]
    res = O.apply_immediate(small_state, parse(f"ship {same.id}", ship.id), small_cfg)
    assert isinstance(res, O.Accepted) and cap.active_ship == same.id, "ship <id> switches (CMD-03, D-124)"
    submit(small_state, small_cfg, "move +2 +0")
    state, events = run(small_state, small_cfg)
    assert same.cell == ship.cell == (4, 5) and far.cell == (9, 9), "owned ships in the active ship's cell move with it; others hold (MV-06, FL-03)"
    assert any("moves with" in t for t in texts(events, "TP-02"))
    submit(state, small_cfg, "move +7 +0")                     # the active Corsair makes 6 cells a turn: 1 persists
    state, _ = run(state, small_cfg)
    assert same.cell == ship.cell == (10, 5) and state.move_remaining(same.id) == [1, 0] and state.move_remaining(ship.id) is None, "queues are per ship (UX-07)"


def test_open_battle_survives_the_save_round_trip(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    _, foe = spawn(small_state, "kestrel", (6, 5))
    submit(small_state, small_cfg, f"fire torpedo {foe.id} 2")
    submit(small_state, small_cfg, f"fire phaser {foe.id}")
    state, _ = run(small_state, small_cfg)
    submit(state, small_cfg, f"peace offer {foe.captain_id}")
    snap = state.snapshot()
    back = from_snapshot(snap, small_cfg)
    skip = {"sectors", "planets"}      # the arena cleared terrain that the seed regenerates (D-87): a regenerated planet beside the ship reads as docked
    same = {k: v for k, v in back.snapshot().items() if k not in skip}
    assert same == {k: v for k, v in snap.items() if k not in skip}
    assert back.battles["b-001"].strength_at_open == state.battles["b-001"].strength_at_open
    assert O.pending_count(back, ship.id) == 1 and back.ships[ship.id].target == foe.id and back.spawned == state.spawned


def test_scan_lists_captains_and_reveals_reputation(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "kestrel", (8, 7), fear=37, respect=43)
    assert not small_state.snapshot()["captains"][cap.id]["visible_reputation"]
    res = O.apply_immediate(small_state, parse("scan", ship.id), small_cfg)
    assert isinstance(res, O.Accepted) and "fear 37, respect 43" in res.text and cap.name in res.text and "3 cells away" in res.text
    assert small_state.snapshot()["captains"][cap.id]["visible_reputation"]
    state, _ = run(small_state, small_cfg)
    assert state.snapshot()["captains"][cap.id]["visible_reputation"], "true for the turn the scan belongs to (LV-04, C10)"
    state, _ = run(state, small_cfg)
    assert not state.snapshot()["captains"][cap.id]["visible_reputation"]


def test_spawn_is_reported_and_never_shifts_the_game_rng(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "corsair", (9, 9), fear=0, respect=10)
    assert small_state.spawned == [{"ship": foe.id, "captain": cap.id, "class": "corsair", "name": foe.name, "galaxy": list(ship.galaxy),
                                    "sector": list(ship.sector), "cell": [9, 9], "fear": 0, "respect": 10}]
    assert cap.fear == 0 and cap.respect == 10 and foe.cls == "corsair" and small_state.snapshot()["spawned"] == small_state.spawned
    with pytest.raises(ValueError, match="--spawn"):
        small_state.spawn("kestrel", ((99, 99), (0, 0), (0, 0)))
