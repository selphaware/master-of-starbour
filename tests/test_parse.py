"""Command parsing - every CMD-* syntax; rejections name the id (CMD-01...21, UX-03, D-73, D-120, D-122, D-127)."""
from __future__ import annotations

import pytest

from starbour.commands.catalogue import COMMANDS, available_names, help_lines, lookup, order_kind
from starbour.commands.parse import (IMMEDIATE, QUEUED, CommandError, Order, parse, parse_escape, parse_fire, parse_help, parse_move,
                                     parse_new, parse_orders, parse_peace, parse_shields, parse_ship, parse_stop, parse_view, parse_warp)


@pytest.mark.parametrize("text, name, kind", [
    ("help", "help", IMMEDIATE), ("help move", "help", IMMEDIATE), ("?", "help", IMMEDIATE),
    ("status", "status", IMMEDIATE), ("ship Lantern", "ship", IMMEDIATE), ("ship 2", "ship", IMMEDIATE),
    ("fleet", "fleet", IMMEDIATE), ("view galaxy", "view", IMMEDIATE), ("view follow", "view", IMMEDIATE),
    ("move 12 7", "move", QUEUED), ("move +3 -1 2.5", "move", QUEUED), ("MOVE 3 0", "move", QUEUED),
    ("scan", "scan", IMMEDIATE), ("orders", "orders", IMMEDIATE), ("orders cancel 2", "orders", IMMEDIATE),
    ("end", "end", IMMEDIATE), ("quit", "quit", IMMEDIATE), ("new", "new", IMMEDIATE), ("new 1234", "new", IMMEDIATE),
])
def test_every_m1_syntax_parses(text: str, name: str, kind: str) -> None:
    order = parse(text, "s-000")
    assert isinstance(order, Order) and order.command == name and order.kind == kind and order.ship_id == "s-000"


@pytest.mark.parametrize("text, name, kind", [
    ("warp 1 2 3 4", "warp", QUEUED), ("warp 1 2 3 4 2.5", "warp", QUEUED), ("shields", "shields", IMMEDIATE),
    ("shields status", "shields", IMMEDIATE), ("stop", "stop", IMMEDIATE), ("stop all", "stop", IMMEDIATE), ("all stop", "stop", IMMEDIATE),
])
def test_every_m2_syntax_parses(text: str, name: str, kind: str) -> None:
    """CMD-07, CMD-09, CMD-21 (D-98): warp, shields and stop are available from M2."""
    order = parse(text, "s-000")
    assert order.command == name and order.kind == kind
    if text == "all stop":
        assert order.args == ("all",)


@pytest.mark.parametrize("text, name", [
    ("fire torpedo s-017", "fire"), ("fire torpedo s-017 3", "fire"), ("fire phaser s-017", "fire"), ("fire torpedoes Grey", "fire"),
    ("peace offer c-004", "peace"), ("peace accept c-004", "peace"), ("peace reject Harrow Vey", "peace"),
    ("escape +4 +0", "escape"), ("escape -1 +2 3", "escape"), ("escape 2 0", "escape"),
])
def test_every_m3_syntax_parses(text: str, name: str) -> None:
    """CMD-08, CMD-10, CMD-11 (D-120, D-122, D-123): fire, peace and escape are queued orders from M3."""
    order = parse(text, "s-000")
    assert order.command == name and order.kind == QUEUED


@pytest.mark.parametrize("text, name", [
    ("fire s-017", "fire"), ("fire p-00012", "fire"),
    ("trade p-00012 give iridium:1 for food:5", "trade"), ("trade c-004 give iridium:1,food:2 for silver:20", "trade"),
    ("upgrade engine with moonstone", "upgrade"), ("upgrade shields with star-iron", "upgrade"),
    ("pickup iridium", "pickup"), ("buy food 3", "buy"), ("buy ship kestrel", "buy"), ("sell torpedoes 2", "sell"),
])
def test_every_m4_syntax_parses(text: str, name: str) -> None:
    """CMD-08 (D-137), CMD-12, CMD-13, CMD-15, CMD-20: the M4 orders are queued orders from M4."""
    order = parse(text, "s-000")
    assert order.command == name and order.kind == QUEUED


def test_m4_helpers() -> None:
    from starbour.commands.parse import BOTH, parse_buy, parse_bundle, parse_pickup, parse_sell, parse_trade, parse_upgrade
    assert parse_fire(["s-017"]).weapon == BOTH and parse_fire(["torpedo", "s-017", "2"]).count == 2
    assert parse_buy(["ship", "Kestrel"]).cls == "kestrel" and parse_buy(["torpedoes", "4"]).item == "torpedo"
    assert parse_sell(["Iridium", "1"]).item == "iridium"
    assert parse_bundle("iridium:1,food:5,iridium:2") == {"iridium": 3, "food": 5}
    t = parse_trade(["p-00012", "give", "iridium:1", "for", "food:5"])
    assert t.counterparty == "p-00012" and t.give == {"iridium": 1} and t.want == {"food": 5}
    assert parse_upgrade(["warpcore", "with", "aetherite"]).component == "warp" and parse_pickup(["Star-Iron"]) == "star_iron"
    for bad, cmd_id in (("fire", "CMD-08"), ("fire s-017 2", "CMD-08"), ("trade p-1 give for food:5", "CMD-12"), ("trade p-1 give iridium for food:5", "CMD-12"),
                        ("upgrade hull with iridium", "CMD-13"), ("pickup", "CMD-15"), ("buy food", "CMD-20"), ("buy food 0", "CMD-20"), ("sell food x", "CMD-20")):
        with pytest.raises(CommandError, match=cmd_id):
            parse(bad)


@pytest.mark.parametrize("text, cmd_id", [
    ("", "CMD-01"), ("jump 3", "CMD-01"), ("move", "CMD-06"), ("move 3", "CMD-06"), ("move a b", "CMD-06"),
    ("move +3 1", "CMD-06"), ("move 3 1 fast", "CMD-06"), ("move 3 1 0", "CMD-06"), ("move -3 1", "CMD-06"),
    ("view sideways", "CMD-05"), ("view", "CMD-05"), ("ship", "CMD-03"), ("orders cancel", "CMD-16"),
    ("orders cancel 0", "CMD-16"), ("orders drop 1", "CMD-16"), ("new abc", "CMD-19"), ("end now", "CMD-17"),
    ("quit now", "CMD-18"), ("scan deep", "CMD-14"), ("status all", "CMD-02"), ("fleet all", "CMD-04"), ("help a b", "CMD-01"),
    ("warp 1 2 3", "CMD-07"), ("warp a 2 3 4", "CMD-07"), ("warp 1 2 3 4 0", "CMD-07"), ("warp -1 2 3 4", "CMD-07"),
    ("stop now", "CMD-21"), ("shields up", "CMD-09"),
    ("fire", "CMD-08"), ("fire laser s-1", "CMD-08"), ("fire torpedo s-1 0", "CMD-08"), ("fire torpedo s-1 x", "CMD-08"),
    ("fire phaser s-1 2", "CMD-08"), ("peace", "CMD-10"), ("peace offer", "CMD-10"), ("peace surrender c-1", "CMD-10"),
    ("escape", "CMD-11"), ("escape +0 +0", "CMD-11"), ("escape a b", "CMD-11"), ("escape +1 +1 0", "CMD-11"),
])
def test_bad_syntax_names_the_id(text: str, cmd_id: str) -> None:
    with pytest.raises(CommandError, match=cmd_id):
        parse(text)


def test_move_absolute_vs_relative() -> None:
    a = parse_move(["12", "7"])
    assert (a.dx, a.dy, a.relative, a.speed) == (12, 7, False, None) and a.target((0, 0)) == (12, 7)
    r = parse_move(["+3", "-1", "2.5"])
    assert (r.dx, r.dy, r.relative, r.speed) == (3, -1, True, 2.5) and r.target((10, 10)) == (13, 9)
    assert parse_move(["+0", "+0"]).relative
    with pytest.raises(CommandError, match=r"move 5 7 \(absolute\) or move \+2 -1 \(relative\)"):   # D-127 wording
        parse_move(["+3", "1"])


def test_m3_helpers() -> None:
    f = parse_fire(["torpedo", "s-017", "3"])
    assert (f.weapon, f.target, f.count) == ("torpedo", "s-017", 3)
    assert parse_fire(["phaser", "Grey Heron"]).count == 1 and parse_fire(["torpedoes", "s-1"]).weapon == "torpedo"
    p = parse_peace(["offer", "Harrow", "Vey"])
    assert (p.action, p.captain) == ("offer", "Harrow Vey")
    e = parse_escape(["+4", "+0"])
    assert (e.dx, e.dy, e.relative, e.speed) == (4, 0, True, None) and e.target((3, 3)) == (7, 3)
    assert parse_escape(["2", "-1", "3"]).relative and parse_escape(["2", "-1", "3"]).speed == 3.0
    assert order_kind("move", ()) == order_kind("escape", ("+1", "+0")) == "move", "escape is a move (OV-02b, D-123)"
    assert order_kind("fire", ("torpedo", "s-1")) != order_kind("fire", ("phaser", "s-1")), "a torpedo and a phaser volley are different kinds (D-120)"
    assert order_kind("fire", ("Torpedo", "s-1")) == "fire:torpedo" and order_kind("peace", ("offer", "c-1")) == "peace"


def test_helpers() -> None:
    assert parse_view(["GALAXY"]) == "galaxy"
    assert parse_ship(["Grey", "Heron"]) == "Grey Heron"
    assert parse_orders([]) is None and parse_orders(["cancel", "3"]) == 3
    assert parse_new([]) is None and parse_new(["77"]) == 77
    assert parse_help([]) is None and parse_help(["Move"]) == "move"


def test_order_round_trip() -> None:
    o = parse("move +1 +2 3", "s-004")
    d = o.to_dict()
    assert d == {"command": "move", "args": ["+1", "+2", "3"], "ship_id": "s-004", "persisting": False}
    back = Order.from_dict(d)
    assert back.command == "move" and back.args == ("+1", "+2", "3") and back.kind == QUEUED and not back.persisting
    assert o.text() == "move +1 +2 3"
    persisting = Order("move", ("+0", "+6"), "s-004", QUEUED, persisting=True)
    assert Order.from_dict(persisting.to_dict()) == persisting, "D-98: a persisting move survives the snapshot round trip"


def test_warp_and_stop_helpers() -> None:
    w = parse_warp(["3", "4", "7", "9"])
    assert (w.galaxy, w.sector, w.factor) == ((3, 4), (7, 9), None)
    assert parse_warp(["3", "4", "7", "9", "2.5"]).factor == 2.5
    assert parse_stop([]) is False and parse_stop(["all"]) is True
    assert parse_shields([]) is None and parse_shields(["status"]) is None


def test_catalogue_covers_cmd_01_to_21() -> None:
    ids = {c.id for c in COMMANDS.values()}
    assert ids == {f"CMD-{n:02d}" for n in range(1, 22)}
    for name in ("move", "warp", "stop", "shields", "fire", "peace", "escape", "trade", "upgrade", "pickup", "buy", "sell"):
        assert name in available_names(), name
    assert lookup("?") is COMMANDS["help"] and lookup("nope") is None
    lines = help_lines(shield_recharge=5)
    assert any("move" in ln for ln in lines) and not any("M4+" in ln for ln in lines), "every M4 command is available (CMD-12/13/15/20)"
    assert lines[-1] == "Shields recharge 5 a turn outside hazards and battle (CM-05); planet cells are safe (HZ-07).", "D-127 footer"
    assert not help_lines("trade")[0].endswith("(not available until M4)"), "trade is available at M4 (CMD-12)"
    assert not help_lines("fire")[0].endswith("(not available until M3)")
    assert "persists" in help_lines("move")[1], "CMD-06 help text says the move persists (D-98)"
    assert any("recharge 5 a turn outside hazards and battle" in ln for ln in help_lines("shields", 5)), "help shields names the recharge (CM-05, D-127)"
