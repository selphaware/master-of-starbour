"""Text -> Order | CommandError - CMD-01...21, UX-03, D-73, D-98, D-120, D-122, D-123, D-137, D-142, D-143.  Pure: no state, no I/O.

Syntax is checked here; validity against the game (speed vs engine power, target inside
the sector, warp reach, weapon range, a battle to make peace in, a planet in the cell, an
element held) is checked in ``starbour.engine.orders`` against the state.  Every message
names the CMD id so T1 and T3 can echo it (UX-03, OUT-13).
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from starbour.commands.catalogue import COMMANDS, COMPONENT_WORDS, IMMEDIATE, QUEUED, lookup

VIEW_LEVELS = ("universe", "galaxy", "sector", "follow")
WEAPONS = ("torpedo", "phaser")
BOTH = "both"
WEAPON_WORDS = {"torpedo": "torpedo", "torpedoes": "torpedo", "torpedos": "torpedo", "phaser": "phaser", "phasers": "phaser"}
PEACE_ACTIONS = ("offer", "accept", "reject")
MOVE_SYNTAX_ERROR = "move 5 7 (absolute) or move +2 -1 (relative) — not a mixture (CMD-06, D-127)"
SUPPLY_WORDS = {"food": "food", "water": "water", "torpedo": "torpedo", "torpedoes": "torpedo", "torpedos": "torpedo"}
COIN_WORDS = {"gold": "gold", "silver": "silver"}


class CommandError(Exception):
    """A command that cannot be parsed; ``str(e)`` names the CMD id (UX-03)."""


@dataclass(frozen=True, slots=True)
class Order:
    """One parsed command, player or AI.  ``args`` are the strings that travel on the bus (MSG-01).

    ``persisting`` marks a move the engine re-queued because the ship has not arrived yet
    (CMD-06, D-98); it counts as the ship's queued move (OV-02b) and survives save/load.
    """

    command: str
    args: tuple[str, ...] = ()
    ship_id: str = ""
    kind: str = QUEUED
    persisting: bool = False

    def to_dict(self) -> dict:
        return {"command": self.command, "args": list(self.args), "ship_id": self.ship_id, "persisting": self.persisting}

    @classmethod
    def from_dict(cls, d: dict) -> "Order":
        command = str(d.get("command", "")).lower()
        spec = lookup(command)
        kind = spec.kind if spec else QUEUED
        return cls(spec.name if spec else command, tuple(str(a) for a in d.get("args") or ()), str(d.get("ship_id") or ""), kind,
                   bool(d.get("persisting", False)))

    def text(self) -> str:
        return " ".join((self.command, *self.args))


@dataclass(frozen=True, slots=True)
class MoveSpec:
    """CMD-06 / D-73: ``move x y [speed]`` absolute or ``move +dx +dy [speed]`` relative."""

    dx: int
    dy: int
    relative: bool
    speed: float | None

    def target(self, cell: tuple[int, int]) -> tuple[int, int]:
        if self.relative:
            return cell[0] + self.dx, cell[1] + self.dy
        return self.dx, self.dy


@dataclass(frozen=True, slots=True)
class WarpSpec:
    """CMD-07: ``warp gx gy sx sy [factor]`` - factor None = the ship's full warp core power."""

    galaxy: tuple[int, int]
    sector: tuple[int, int]
    factor: float | None


@dataclass(frozen=True, slots=True)
class FireSpec:
    """CMD-08 / D-120 / D-137: ``fire <target>`` (weapon 'both': one torpedo and the phasers), ``fire torpedo <target> [n]`` or ``fire phaser <target>``."""

    weapon: str
    target: str
    count: int


@dataclass(frozen=True, slots=True)
class PeaceSpec:
    """CMD-10 / D-122: ``peace offer | accept | reject <captain | planet>``."""

    action: str
    captain: str


@dataclass(frozen=True, slots=True)
class BuySpec:
    """CMD-20: ``buy <item> <n>`` or ``buy ship <class>`` (item 'ship', count 1, ``cls`` the class)."""

    item: str
    count: int
    cls: str | None = None


@dataclass(frozen=True, slots=True)
class SellSpec:
    """CMD-20: ``sell <item> <n>``."""

    item: str
    count: int


@dataclass(frozen=True, slots=True)
class TradeSpec:
    """CMD-12: ``trade <counterparty> give <bundle> for <bundle>`` - bundles are ``{item: units}``."""

    counterparty: str
    give: dict[str, int]
    want: dict[str, int]


@dataclass(frozen=True, slots=True)
class UpgradeSpec:
    """CMD-13: ``upgrade <component> with <element>``."""

    component: str
    element: str


def tokenize(text: str) -> list[str]:
    try:
        return shlex.split(text, posix=True)
    except ValueError:
        return text.split()


def parse(text: str, ship_id: str = "") -> Order:
    """Parse one command line.  Raises CommandError."""
    tokens = tokenize(text)
    if not tokens:
        raise CommandError("empty command - type 'help' (CMD-01)")
    return parse_tokens(tokens, ship_id)


def parse_tokens(tokens: list[str], ship_id: str = "") -> Order:
    name, args = tokens[0].lower(), [str(a) for a in tokens[1:]]
    if name == "all" and [a.lower() for a in args] == ["stop"]:   # CMD-21 alias 'all stop'
        name, args = "stop", ["all"]
    spec = lookup(name)
    if spec is None:
        raise CommandError(f"unknown command {name!r} - type 'help' for the list (CMD-01)")
    if not spec.available:
        raise CommandError(f"{spec.name} is not available until {spec.since} ({spec.id})")
    checker = _CHECKERS.get(spec.name)
    if checker is not None:
        checker(args)
    return Order(spec.name, tuple(args), ship_id, spec.kind)


def _int(value: str, what: str, cmd_id: str) -> int:
    try:
        return int(value)
    except ValueError:
        raise CommandError(f"{what} must be a whole number, got {value!r} ({cmd_id})") from None


def _signed(value: str) -> bool:
    return value[:1] in ("+", "-") and value[1:].lstrip("0123456789") == "" and len(value) > 1


def _speed(value: str, cmd_id: str) -> float:
    try:
        speed = float(value)
    except ValueError:
        raise CommandError(f"speed must be a number, got {value!r} ({cmd_id})") from None
    if speed <= 0:
        raise CommandError(f"speed must be greater than 0 ({cmd_id})")
    return speed


def parse_move(args: list[str] | tuple[str, ...]) -> MoveSpec:
    """CMD-06, D-73: both numbers signed (+3 -1) = relative; both unsigned (12 7) = absolute."""
    if len(args) not in (2, 3):
        raise CommandError("move needs <x y> or <+dx +dy> and an optional speed (CMD-06)")
    a, b = args[0], args[1]
    sa, sb = _signed(a), _signed(b)
    if sa != sb:
        raise CommandError(MOVE_SYNTAX_ERROR)
    dx, dy = _int(a, "move x/dx", "CMD-06"), _int(b, "move y/dy", "CMD-06")
    if not sa and (dx < 0 or dy < 0):
        raise CommandError("move: an absolute target cannot be negative (CMD-06)")
    speed: float | None = None
    if len(args) == 3:
        speed = _speed(args[2], "CMD-06")
    return MoveSpec(dx, dy, sa, speed)


def parse_escape(args: list[str] | tuple[str, ...]) -> MoveSpec:
    """CMD-11 / D-123: ``escape +dx +dy [speed]`` - always relative (an unsigned pair is read as dx dy too)."""
    if len(args) not in (2, 3):
        raise CommandError("escape needs <+dx +dy> and an optional speed (CMD-11)")
    dx, dy = _int(args[0], "escape dx", "CMD-11"), _int(args[1], "escape dy", "CMD-11")
    if dx == 0 and dy == 0:
        raise CommandError("escape needs a direction: dx and dy cannot both be 0 (CMD-11)")
    speed: float | None = _speed(args[2], "CMD-11") if len(args) == 3 else None
    return MoveSpec(dx, dy, True, speed)


def parse_warp(args: list[str] | tuple[str, ...]) -> WarpSpec:
    """CMD-07: ``warp gx gy sx sy [factor]``; coordinates are whole numbers >= 0, factor > 0."""
    if len(args) not in (4, 5):
        raise CommandError("warp needs <gx gy> <sx sy> and an optional factor (CMD-07)")
    gx, gy = _int(args[0], "warp gx", "CMD-07"), _int(args[1], "warp gy", "CMD-07")
    sx, sy = _int(args[2], "warp sx", "CMD-07"), _int(args[3], "warp sy", "CMD-07")
    if min(gx, gy, sx, sy) < 0:
        raise CommandError("warp: coordinates cannot be negative (CMD-07)")
    factor: float | None = None
    if len(args) == 5:
        try:
            factor = float(args[4])
        except ValueError:
            raise CommandError(f"warp factor must be a number, got {args[4]!r} (CMD-07)") from None
        if factor <= 0:
            raise CommandError("warp factor must be greater than 0 (CMD-07)")
    return WarpSpec((gx, gy), (sx, sy), factor)


def parse_fire(args: list[str] | tuple[str, ...]) -> FireSpec:
    """CMD-08 / D-120 / D-137: ``fire <target>`` (one torpedo and the phasers), ``fire torpedo <target> [n]`` or ``fire phaser <target>``."""
    if not args:
        raise CommandError("fire needs a target, or a weapon (torpedo | phaser) and a target (CMD-08)")
    weapon = WEAPON_WORDS.get(args[0].lower(), "")
    if not weapon:
        if len(args) != 1:
            raise CommandError("fire <target> takes one target; use 'fire torpedo <target> [n]' for a volley (CMD-08)")
        return FireSpec(BOTH, args[0], 1)
    if len(args) < 2:
        raise CommandError(f"fire {weapon} needs a target ship or planet (CMD-08)")
    if weapon == "phaser":
        if len(args) != 2:
            raise CommandError("fire phaser <target> takes no count (CMD-08)")
        return FireSpec(weapon, args[1], 1)
    if len(args) > 3:
        raise CommandError("fire torpedo <target> [n] (CMD-08)")
    count = _int(args[2], "fire torpedo n", "CMD-08") if len(args) == 3 else 1
    if count < 1:
        raise CommandError("fire torpedo n: n counts from 1 (CMD-08)")
    return FireSpec(weapon, args[1], count)


def parse_peace(args: list[str] | tuple[str, ...]) -> PeaceSpec:
    """CMD-10 / D-122: ``peace offer | accept | reject <captain | planet>``."""
    if len(args) < 2 or args[0].lower() not in PEACE_ACTIONS:
        raise CommandError("peace needs offer | accept | reject and a captain or planet (name or id) (CMD-10)")
    return PeaceSpec(args[0].lower(), " ".join(args[1:]))


def goods_word(text: str) -> str:
    """Normalise an item word: food, water, torpedo, gold, silver, or an element name (lower case, hyphen to underscore)."""
    key = text.strip().lower().replace("-", "_")
    return SUPPLY_WORDS.get(key) or COIN_WORDS.get(key) or key


def _count(value: str, what: str, cmd_id: str) -> int:
    n = _int(value, what, cmd_id)
    if n < 1:
        raise CommandError(f"{what} counts from 1 ({cmd_id})")
    return n


def parse_buy(args: list[str] | tuple[str, ...]) -> BuySpec:
    """CMD-20: ``buy <item> <n>`` or ``buy ship <class>``."""
    if len(args) != 2:
        raise CommandError("buy needs <item> <n>, or 'buy ship <class>' (CMD-20)")
    if args[0].lower() == "ship":
        return BuySpec("ship", 1, args[1].lower())
    return BuySpec(goods_word(args[0]), _count(args[1], "buy n", "CMD-20"))


def parse_sell(args: list[str] | tuple[str, ...]) -> SellSpec:
    """CMD-20: ``sell <item> <n>``."""
    if len(args) != 2:
        raise CommandError("sell needs <item> <n> (CMD-20)")
    return SellSpec(goods_word(args[0]), _count(args[1], "sell n", "CMD-20"))


def parse_bundle(text: str, cmd_id: str = "CMD-12") -> dict[str, int]:
    """``iridium:1,food:5`` -> {'iridium': 1, 'food': 5}; the same item twice adds up."""
    out: dict[str, int] = {}
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, n = part.partition(":")
        if sep != ":" or not name.strip():
            raise CommandError(f"trade items are written item:n, e.g. iridium:1,food:5 - got {part!r} ({cmd_id})")
        item = goods_word(name)
        out[item] = out.get(item, 0) + _count(n.strip(), f"units of {item}", cmd_id)
    if not out:
        raise CommandError(f"trade needs at least one item on each side, written item:n ({cmd_id})")
    return out


def parse_trade(args: list[str] | tuple[str, ...]) -> TradeSpec:
    """CMD-12: ``trade <counterparty> give <bundle> for <bundle>``."""
    low = [a.lower() for a in args]
    if "give" not in low or "for" not in low:
        raise CommandError("trade <planet id | captain> give <item:n[,...]> for <item:n[,...]> (CMD-12)")
    gi, fi = low.index("give"), low.index("for")
    if gi == 0 or fi < gi + 2 or fi != len(args) - 2:
        raise CommandError("trade <planet id | captain> give <item:n[,...]> for <item:n[,...]> (CMD-12)")
    counterparty = " ".join(args[:gi])
    give = parse_bundle(",".join(args[gi + 1:fi]))
    want = parse_bundle(args[fi + 1])
    return TradeSpec(counterparty, give, want)


def parse_upgrade(args: list[str] | tuple[str, ...]) -> UpgradeSpec:
    """CMD-13: ``upgrade <component> with <element>``."""
    if len(args) != 3 or args[1].lower() != "with":
        raise CommandError("upgrade <torpedoes | phasers | shields | engine | warp> with <element> (CMD-13)")
    component = COMPONENT_WORDS.get(args[0].lower())
    if component is None:
        raise CommandError(f"upgrade: unknown component {args[0]!r}; one of torpedoes, phasers, shields, engine, warp (CMD-13)")
    return UpgradeSpec(component, goods_word(args[2]))


def parse_pickup(args: list[str] | tuple[str, ...]) -> str:
    """CMD-15: ``pickup <element>``."""
    if len(args) != 1:
        raise CommandError("pickup <element> (CMD-15)")
    return goods_word(args[0])


def parse_stop(args: list[str] | tuple[str, ...]) -> bool:
    """CMD-21: True for 'stop all' (every owned ship), False for the active ship only."""
    if not args:
        return False
    if len(args) == 1 and args[0].lower() == "all":
        return True
    raise CommandError("stop takes no argument, or 'all' (CMD-21)")


def parse_shields(args: list[str] | tuple[str, ...]) -> None:
    """CMD-09: 'shields' or 'shields status'."""
    if args and not (len(args) == 1 and args[0].lower() == "status"):
        raise CommandError("shields takes no argument, or 'status' (CMD-09)")


def parse_view(args: list[str] | tuple[str, ...]) -> str:
    """CMD-05."""
    if len(args) != 1 or args[0].lower() not in VIEW_LEVELS:
        raise CommandError("view needs one of: universe, galaxy, sector, follow (CMD-05)")
    return args[0].lower()


def parse_ship(args: list[str] | tuple[str, ...]) -> str:
    """CMD-03."""
    if not args:
        raise CommandError("ship needs a ship name, fleet number or id (CMD-03)")
    return " ".join(args)


def parse_orders(args: list[str] | tuple[str, ...]) -> int | None:
    """CMD-16: None = review; n = cancel the n-th queued order (1-based)."""
    if not args:
        return None
    if len(args) == 2 and args[0].lower() == "cancel":
        n = _int(args[1], "orders cancel n", "CMD-16")
        if n < 1:
            raise CommandError("orders cancel n: n counts from 1 (CMD-16)")
        return n
    raise CommandError("orders takes no argument, or 'cancel n' (CMD-16)")


def parse_new(args: list[str] | tuple[str, ...]) -> int | None:
    """CMD-19: optional integer seed."""
    if not args:
        return None
    if len(args) != 1:
        raise CommandError("new takes an optional seed only (CMD-19)")
    return _int(args[0], "new seed", "CMD-19")


def parse_help(args: list[str] | tuple[str, ...]) -> str | None:
    """CMD-01."""
    if len(args) > 1:
        raise CommandError("help takes at most one command name (CMD-01)")
    return args[0].lower() if args else None


def _no_args(name: str, cmd_id: str):
    def check(args: list[str]) -> None:
        if args:
            raise CommandError(f"{name} takes no arguments ({cmd_id})")
    return check


_CHECKERS = {
    "help": lambda a: parse_help(a),
    "status": _no_args("status", "CMD-02"),
    "ship": lambda a: parse_ship(a),
    "fleet": _no_args("fleet", "CMD-04"),
    "view": lambda a: parse_view(a),
    "move": lambda a: parse_move(a),
    "warp": lambda a: parse_warp(a),
    "fire": lambda a: parse_fire(a),
    "shields": lambda a: parse_shields(a),
    "peace": lambda a: parse_peace(a),
    "escape": lambda a: parse_escape(a),
    "trade": lambda a: parse_trade(a),
    "upgrade": lambda a: parse_upgrade(a),
    "scan": _no_args("scan", "CMD-14"),
    "pickup": lambda a: parse_pickup(a),
    "orders": lambda a: parse_orders(a),
    "end": _no_args("end", "CMD-17"),
    "quit": _no_args("quit", "CMD-18"),
    "new": lambda a: parse_new(a),
    "buy": lambda a: parse_buy(a),
    "sell": lambda a: parse_sell(a),
    "stop": lambda a: parse_stop(a),
}

__all__ = ["CommandError", "Order", "MoveSpec", "WarpSpec", "FireSpec", "PeaceSpec", "BuySpec", "SellSpec", "TradeSpec", "UpgradeSpec", "parse",
           "parse_tokens", "parse_move", "parse_escape", "parse_warp", "parse_fire", "parse_peace", "parse_stop", "parse_shields", "parse_view",
           "parse_ship", "parse_orders", "parse_new", "parse_help", "parse_buy", "parse_sell", "parse_trade", "parse_upgrade", "parse_pickup",
           "parse_bundle", "goods_word", "COMMANDS", "IMMEDIATE", "QUEUED", "VIEW_LEVELS", "WEAPONS", "BOTH", "PEACE_ACTIONS", "MOVE_SYNTAX_ERROR"]
