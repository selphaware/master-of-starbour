"""Text -> Order | CommandError - CMD-01...21, UX-03, D-73, D-98.  Pure: no state, no I/O.

Syntax is checked here; validity against the game (speed vs engine power, target inside
the sector, warp reach) is checked in ``starbour.engine.orders`` against the state.  Every
message names the CMD id so T1 and T3 can echo it (UX-03, OUT-13).
"""
from __future__ import annotations

import shlex
from dataclasses import dataclass

from starbour.commands.catalogue import COMMANDS, IMMEDIATE, QUEUED, lookup

VIEW_LEVELS = ("universe", "galaxy", "sector", "follow")


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


def parse_move(args: list[str] | tuple[str, ...]) -> MoveSpec:
    """CMD-06, D-73: both numbers signed (+3 -1) = relative; both unsigned (12 7) = absolute."""
    if len(args) not in (2, 3):
        raise CommandError("move needs <x y> or <+dx +dy> and an optional speed (CMD-06)")
    a, b = args[0], args[1]
    sa, sb = _signed(a), _signed(b)
    if sa != sb:
        raise CommandError("move: use either 'x y' (absolute) or '+dx +dy' (relative), not a mixture (CMD-06, D-73)")
    dx, dy = _int(a, "move x/dx", "CMD-06"), _int(b, "move y/dy", "CMD-06")
    if not sa and (dx < 0 or dy < 0):
        raise CommandError("move: an absolute target cannot be negative (CMD-06)")
    speed: float | None = None
    if len(args) == 3:
        try:
            speed = float(args[2])
        except ValueError:
            raise CommandError(f"move speed must be a number, got {args[2]!r} (CMD-06)") from None
        if speed <= 0:
            raise CommandError("move speed must be greater than 0 (CMD-06)")
    return MoveSpec(dx, dy, sa, speed)


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
        raise CommandError("ship needs a ship name or fleet number (CMD-03)")
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
    "shields": lambda a: parse_shields(a),
    "scan": _no_args("scan", "CMD-14"),
    "orders": lambda a: parse_orders(a),
    "end": _no_args("end", "CMD-17"),
    "quit": _no_args("quit", "CMD-18"),
    "new": lambda a: parse_new(a),
    "stop": lambda a: parse_stop(a),
}

__all__ = ["CommandError", "Order", "MoveSpec", "WarpSpec", "parse", "parse_tokens", "parse_move", "parse_warp", "parse_stop",
           "parse_shields", "parse_view", "parse_ship", "parse_orders", "parse_new", "parse_help", "COMMANDS", "IMMEDIATE", "QUEUED",
           "VIEW_LEVELS"]
