"""AI captains in M3 - CM-10, EF-01, EF-02, CFG-29/30/44/72/73, D-125, D-132: attack, yield, roam, passive mode."""
from __future__ import annotations

import dataclasses

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import ai
from starbour.engine import combat as C
from starbour.engine import orders as O
from starbour.engine.phases import resolve_turn
from starbour.engine.state import GameState
from starbour.world.generate import game_rng
from tests.test_combat import arena, spawn


def orders_for(state: GameState, cfg: GameConfig, cap, **kw) -> list[tuple[str, tuple[str, ...]]]:
    return [(o.command, o.args) for o in ai.decide(cap, state, game_rng(1), cfg, **kw)]


def test_ai_opens_on_a_weaker_low_fear_target_in_range(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "corsair", (6, 5))
    got = orders_for(small_state, small_cfg, cap)
    assert got == [("fire", ("torpedo", ship.id, "1")), ("fire", ("phaser", ship.id))], "ratio 1.78 >= 1.3, fear 10 < 50 (CM-10, EF-01)"
    assert orders_for(small_state, small_cfg, cap, passive=True) == [], "--ai passive never opens and never wanders (D-125, D-132)"
    small_state.player.fear = small_cfg.attack_fear_max
    assert all(cmd != "fire" for cmd, _ in orders_for(small_state, small_cfg, cap)), "fear >= attack_fear_max: no attack"
    small_state.player.fear = 10
    foe.position = (foe.galaxy, foe.sector, (11, 5))
    assert all(cmd != "fire" for cmd, _ in orders_for(small_state, small_cfg, cap)), "out of weapon range: no attack"
    weak_cap, weak = spawn(small_state, "kestrel", (4, 5))
    assert all(cmd != "fire" for cmd, _ in orders_for(small_state, small_cfg, weak_cap)), "ratio 1.0 < 1.3: a Kestrel does not open on a Kestrel"


def test_ai_fires_back_in_battle_and_yields_by_the_ef02_rules(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "bastion", (6, 5), torpedoes=0, phasers=1)
    O.submit(small_state, parse(f"fire phaser {foe.id}", ship.id), small_cfg)
    small_state.turn_no = 1
    bt = small_state.battles["b-001"]
    assert orders_for(small_state, small_cfg, cap, passive=True) == [("fire", ("phaser", ship.id))], "in battle: fires back, holds its cell"
    # weaker (Bastion at 0 torpedoes and 1 phaser: 100 + 15 = 115, under the Kestrel's strength): below the fear-scaled threshold it offers peace and keeps firing
    foe.shield_current = 30.0
    thr = C.ai_peace_threshold(small_state.player.fear, small_cfg)
    assert 30.0 < thr / 100 * foe.shield_max
    assert orders_for(small_state, small_cfg, cap) == [("peace", ("offer", small_state.player_id)), ("fire", ("phaser", ship.id))]
    bt.offers[cap.id] = 1
    assert orders_for(small_state, small_cfg, cap) == [("fire", ("phaser", ship.id))], "no second offer while one stands"
    # the player's standing offer is accepted when weaker
    bt.offers.clear()
    bt.offers[small_state.player_id] = 1
    assert orders_for(small_state, small_cfg, cap) == [("peace", ("accept", small_state.player_id))]
    bt.offers.clear()
    # below escape_shield_pct a mobile ship moves directly away at full power; an immobile one offers peace instead
    foe.shield_current = 10.0
    got = orders_for(small_state, small_cfg, cap)
    assert got == [("move", ("9", "5", "3"))], got
    foe.engine = 0.0
    assert orders_for(small_state, small_cfg, cap)[0] == ("peace", ("offer", small_state.player_id))
    # surrender beats escape and peace when the player's fear is high enough (D-128)
    foe.engine = 3.0
    small_state.player.fear = small_cfg.yield_fear_min
    assert orders_for(small_state, small_cfg, cap) == [(C.SURRENDER, (small_state.player_id,))]
    foe.shield_current = 25.0     # 25 % >= surrender_shield_pct 20: escape no longer applies either (>= 15), so peace (< 60 % at fear 80)
    assert orders_for(small_state, small_cfg, cap)[0] == ("peace", ("offer", small_state.player_id))


def test_roaming_schedule_and_destinations(small_state: GameState, small_cfg: GameConfig) -> None:
    cap = next(c for c in small_state.captains.values() if not c.is_player)
    every = small_cfg.roam_every_turns
    due = [t for t in range(1, 4 * every) if ai.roam_due(cap, t, small_cfg)]
    assert due and all(t >= every and (t + ai.captain_index(cap.id)) % every == 0 for t in due) and len(due) == 3
    assert not ai.roam_due(cap, every, dataclasses.replace(small_cfg, roam_every_turns=0)), "0 disables (CFG-73)"
    ship = small_state.ships[cap.active_ship]
    for _ in range(20):
        dest = ai.roam_destination(ship, small_state, game_rng(_), small_cfg, 0.0)
        if dest is None:
            continue
        assert dest != (ship.galaxy, ship.sector) and not small_state.world.sectors[dest].has_hole(), "never a sector holding a hole (D-125)"
    assert ai.roam_destination(ship, small_state, game_rng(1), small_cfg, ship.warp) is None, "effective factor <= 0: no roam (HZ-10)"
    state, _ = resolve_turn(small_state, small_cfg, game_rng(7), ai_player=False)
    while state.turn_no < every + 1:
        state, _ = resolve_turn(state, small_cfg, game_rng(7), ai_player=False)
    assert state.last_stats["roams"] >= 0 and all(w.get("captain") for w in state.last_stats["warps"])


def test_ai_opening_at_tp01_holds_the_target_for_the_exchange(small_state: GameState, small_cfg: GameConfig) -> None:
    """D-132 (1): the targeted captain decided its random walk before the attacker's fire opened the battle; that move is
    dropped so the exchange happens at the start-of-turn distance (D-128)."""
    arena(small_state, cell=(0, 0))
    target_cap, target = spawn(small_state, "kestrel", (8, 8))        # lower captain id: decides first
    attacker_cap, attacker = spawn(small_state, "corsair", (9, 8))    # ratio 1.78, fear 10: opens
    assert ai.captain_index(target_cap.id) < ai.captain_index(attacker_cap.id)
    state, events = resolve_turn(small_state, small_cfg, game_rng(3), ai_passive=False)
    st = state.last_stats
    assert st["battles_opened"] == 1 and st["ai_vs_ai_opened"] == 1 and st["held_for_battle"] == 1
    assert target.cell == (8, 8) and attacker.cell == (9, 8)
    rows = st["battles"][0]["exchanges"]
    assert any(r["attacker"] == attacker.id and r["defender"] == target.id for r in rows) and st["battles"][0]["opened_by"] == attacker_cap.id
    assert any(r["captain"] == attacker_cap.id and r["rule"] == "FR-06" for r in st["reputation"])


def test_captured_ship_gets_no_ai_orders(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "kestrel", (6, 5))
    foe.captain_id = small_state.player_id
    small_state.player.ships.append(foe.id)
    cap.ships.remove(foe.id)
    cap.active_ship = None
    assert ai.decide(cap, small_state, game_rng(1), small_cfg) == []
