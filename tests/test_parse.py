"""Command parsing - every CMD-* syntax; rejections name the id (CMD-01...20, UX-03, D-73)."""
from __future__ import annotations

import pytest

from starbour.commands.catalogue import COMMANDS, available_names, help_lines, lookup
from starbour.commands.parse import (IMMEDIATE, QUEUED, CommandError, Order, parse, parse_help, parse_move, parse_new,
                                     parse_orders, parse_ship, parse_view)


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


@pytest.mark.parametrize("text, cmd_id", [
    ("warp 1 2 3 4", "CMD-07"), ("fire torpedo x", "CMD-08"), ("shields status", "CMD-09"), ("peace offer x", "CMD-10"),
    ("escape 1 1", "CMD-11"), ("trade x give y for z", "CMD-12"), ("upgrade engine with moonstone", "CMD-13"),
    ("pickup i-1", "CMD-15"), ("buy food 3", "CMD-20"), ("sell food 3", "CMD-20"),
])
def test_later_milestone_commands_rejected_by_id(text: str, cmd_id: str) -> None:
    with pytest.raises(CommandError, match=cmd_id):
        parse(text)


@pytest.mark.parametrize("text, cmd_id", [
    ("", "CMD-01"), ("jump 3", "CMD-01"), ("move", "CMD-06"), ("move 3", "CMD-06"), ("move a b", "CMD-06"),
    ("move +3 1", "CMD-06"), ("move 3 1 fast", "CMD-06"), ("move 3 1 0", "CMD-06"), ("move -3 1", "CMD-06"),
    ("view sideways", "CMD-05"), ("view", "CMD-05"), ("ship", "CMD-03"), ("orders cancel", "CMD-16"),
    ("orders cancel 0", "CMD-16"), ("orders drop 1", "CMD-16"), ("new abc", "CMD-19"), ("end now", "CMD-17"),
    ("quit now", "CMD-18"), ("scan deep", "CMD-14"), ("status all", "CMD-02"), ("fleet all", "CMD-04"), ("help a b", "CMD-01"),
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


def test_helpers() -> None:
    assert parse_view(["GALAXY"]) == "galaxy"
    assert parse_ship(["Grey", "Heron"]) == "Grey Heron"
    assert parse_orders([]) is None and parse_orders(["cancel", "3"]) == 3
    assert parse_new([]) is None and parse_new(["77"]) == 77
    assert parse_help([]) is None and parse_help(["Move"]) == "move"


def test_order_round_trip() -> None:
    o = parse("move +1 +2 3", "s-004")
    d = o.to_dict()
    assert d == {"command": "move", "args": ["+1", "+2", "3"], "ship_id": "s-004"}
    back = Order.from_dict(d)
    assert back.command == "move" and back.args == ("+1", "+2", "3") and back.kind == QUEUED
    assert o.text() == "move +1 +2 3"


def test_catalogue_covers_cmd_01_to_20() -> None:
    ids = {c.id for c in COMMANDS.values()}
    assert ids == {f"CMD-{n:02d}" for n in range(1, 21)}
    assert "move" in available_names() and "warp" not in available_names()
    assert lookup("?") is COMMANDS["help"] and lookup("nope") is None
    lines = help_lines()
    assert any("move" in ln for ln in lines) and any("M2+" in ln for ln in lines)
    assert help_lines("warp")[0].endswith("(not available until M2)")
