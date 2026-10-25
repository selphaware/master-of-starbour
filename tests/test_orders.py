"""Order queues - immediate vs queued; same-kind replaces; per-ship queues; cancel (OV-02b, UX-07, CMD-06/16, D-92)."""
from __future__ import annotations

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import orders as O
from starbour.engine.state import GameState
from starbour.entities.ship import Ship


def give_second_ship(state: GameState, cfg: GameConfig) -> Ship:
    cap = state.player
    first = state.ships[cap.active_ship]
    sid = state.new_ship_id()
    ship = Ship.of_class(sid, "Second Tern", cfg.ships["corsair"], cap.id, first.position)
    state.ships[sid] = ship
    cap.ships.append(sid)
    return ship


def test_queued_move_accepted_and_counted(small_state: GameState, small_cfg: GameConfig) -> None:
    sid = small_state.player.active_ship
    res = O.submit(small_state, parse("move 1 0", sid), small_cfg)
    assert isinstance(res, O.Accepted) and res.queue_position == 1 and res.queued == 1
    assert O.pending_count(small_state, sid) == 1


def test_immediate_cannot_be_queued(small_state: GameState, small_cfg: GameConfig) -> None:
    sid = small_state.player.active_ship
    res = O.submit(small_state, parse("status", sid), small_cfg)
    assert isinstance(res, O.Rejected) and "OV-02b" in res.reason


def test_same_kind_replaces(small_state: GameState, small_cfg: GameConfig) -> None:
    sid = small_state.player.active_ship
    O.submit(small_state, parse("move 1 0", sid), small_cfg)
    res = O.submit(small_state, parse("move 2 2", sid), small_cfg)
    assert isinstance(res, O.Accepted) and res.queue_position == 1 and res.queued == 1 and "OV-02b" in res.text
    assert [o.args for o in O.pending(small_state, sid)] == [("2", "2")]


def test_per_ship_queues(small_state: GameState, small_cfg: GameConfig) -> None:
    second = give_second_ship(small_state, small_cfg)
    first = small_state.player.active_ship
    O.submit(small_state, parse("move 1 0", first), small_cfg)
    O.submit(small_state, parse("move 2 0", second.id), small_cfg)
    assert O.pending_count(small_state, first) == 1 and O.pending_count(small_state, second.id) == 1
    taken = O.take_all(small_state)
    assert set(taken) == {first, second.id} and O.pending_count(small_state, first) == 0


def test_speed_and_bounds_validation_name_cmd_06(small_state: GameState, small_cfg: GameConfig) -> None:
    sid = small_state.player.active_ship
    ship = small_state.ships[sid]
    fast = O.submit(small_state, parse(f"move 1 0 {ship.engine + 1}", sid), small_cfg)
    assert isinstance(fast, O.Rejected) and "exceeds engine_power" in fast.reason and "CMD-06" in fast.reason
    out = O.submit(small_state, parse("move 99 0", sid), small_cfg)
    assert isinstance(out, O.Rejected) and "outside the sector" in out.reason and "CMD-06" in out.reason
    w, h = small_cfg.sector_size
    rel = O.submit(small_state, parse(f"move +{w} +0", sid), small_cfg)
    assert isinstance(rel, O.Rejected) and "CMD-06" in rel.reason


def test_not_your_ship(small_state: GameState, small_cfg: GameConfig) -> None:
    other = next(s for s in small_state.ships.values() if s.captain_id != small_state.player_id)
    res = O.submit(small_state, parse("move 1 0", other.id), small_cfg)
    assert isinstance(res, O.Rejected) and "not one of your ships" in res.reason


def test_orders_cancel_n(small_state: GameState, small_cfg: GameConfig) -> None:
    sid = small_state.player.active_ship
    O.submit(small_state, parse("move 1 0", sid), small_cfg)
    review = O.apply_immediate(small_state, parse("orders", sid), small_cfg)
    assert isinstance(review, O.Accepted) and "move 1 0" in review.text
    bad = O.apply_immediate(small_state, parse("orders cancel 2", sid), small_cfg)
    assert isinstance(bad, O.Rejected) and "CMD-16" in bad.reason
    ok = O.apply_immediate(small_state, parse("orders cancel 1", sid), small_cfg)
    assert isinstance(ok, O.Accepted) and ok.queued == 0 and O.pending_count(small_state, sid) == 0


def test_ship_switch_by_name_and_number(small_state: GameState, small_cfg: GameConfig) -> None:
    second = give_second_ship(small_state, small_cfg)
    first = small_state.player.active_ship
    res = O.apply_immediate(small_state, parse("ship second tern", first), small_cfg)
    assert isinstance(res, O.Accepted) and small_state.player.active_ship == second.id
    res = O.apply_immediate(small_state, parse("ship 1", second.id), small_cfg)
    assert isinstance(res, O.Accepted) and small_state.player.active_ship == first
    res = O.apply_immediate(small_state, parse("ship Nobody", first), small_cfg)
    assert isinstance(res, O.Rejected) and "CMD-03" in res.reason


def test_scan_reveals_items_in_the_cell(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = small_state.active_ship()
    sector = small_state.sector_of(ship)
    from starbour.entities.item import Item
    sector.items.append(Item("i-9999", "iridium", "normal", ship.cell))
    res = O.apply_immediate(small_state, parse("scan", ship.id), small_cfg)
    assert isinstance(res, O.Accepted) and "iridium" in res.text and res.events
    assert all(i.revealed for i in sector.items if i.cell == ship.cell)


def test_warp_validation_and_queue(small_state: GameState, small_cfg: GameConfig) -> None:
    """CMD-07: a reachable sector queues; a second warp replaces the first (OV-02b); factor defaults to the core."""
    sid = small_state.player.active_ship
    ship = small_state.ships[sid]
    here = (ship.galaxy, ship.sector)
    dests = [k for k in sorted(small_state.world.sectors) if k[0] == here[0] and k != here]
    (gx, gy), (sx, sy) = dests[0]
    res = O.submit(small_state, parse(f"warp {gx} {gy} {sx} {sy}", sid), small_cfg)
    assert isinstance(res, O.Accepted) and res.queue_position == 1 and "warp queued" in res.text
    if len(dests) > 1:
        (gx2, gy2), (sx2, sy2) = dests[1]
        res2 = O.submit(small_state, parse(f"warp {gx2} {gy2} {sx2} {sy2} 2", sid), small_cfg)
        assert isinstance(res2, O.Accepted) and res2.queued == 1 and "OV-02b" in res2.text
    O.submit(small_state, parse("move 1 0", sid), small_cfg)
    assert O.pending_count(small_state, sid) == 2, "a move and a warp are different kinds"


def test_stop_immediate(small_state: GameState, small_cfg: GameConfig) -> None:
    sid = small_state.player.active_ship
    nothing = O.apply_immediate(small_state, parse("stop", sid), small_cfg)
    assert isinstance(nothing, O.Accepted) and "nothing to stop" in nothing.text and "CMD-21" in nothing.text
    O.submit(small_state, parse("move 1 0", sid), small_cfg)
    res = O.apply_immediate(small_state, parse("all stop", sid), small_cfg)
    assert isinstance(res, O.Accepted) and "cancelled" in res.text and O.pending_count(small_state, sid) == 0
    assert res.events and res.events[0]["type"] == "stopped"
