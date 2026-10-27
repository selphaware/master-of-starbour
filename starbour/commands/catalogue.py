"""The command table: name, syntax, immediate/queued, help text - CMD-01...21, OV-02b.

Immediate commands act at once; queued ones wait for ``end`` (OV-02b).  Commands whose
rules arrive in a later milestone are listed so ``help`` can say so and the parser can
reject them by name.  M2 adds warp (CMD-07), shields (CMD-09) and stop (CMD-21, D-98);
M3 adds fire (CMD-08), peace (CMD-10) and escape (CMD-11), and the help footer names
the shield recharge and that planet cells are safe (CM-05, HZ-07, D-127).
"""
from __future__ import annotations

from dataclasses import dataclass

IMMEDIATE = "immediate"
QUEUED = "queued"
CURRENT_MILESTONE = "M3"
MILESTONE_ORDER = ("M1", "M2", "M3", "M4", "M5")


@dataclass(frozen=True, slots=True)
class CommandSpec:
    name: str
    id: str
    syntax: str
    kind: str
    summary: str
    since: str = "M1"
    aliases: tuple[str, ...] = ()

    @property
    def available(self) -> bool:
        return MILESTONE_ORDER.index(self.since) <= MILESTONE_ORDER.index(CURRENT_MILESTONE)


COMMANDS: dict[str, CommandSpec] = {c.name: c for c in (
    CommandSpec("help", "CMD-01", "help [command]", IMMEDIATE, "List commands, or show one command's syntax.", aliases=("?",)),
    CommandSpec("status", "CMD-02", "status", IMMEDIATE, "Show the active ship's stats and the captain's wealth, fear, respect, food and water."),
    CommandSpec("ship", "CMD-03", "ship <name | n | id>", IMMEDIATE, "Switch the active ship (by name, fleet number or id); each ship keeps its own order queue (UX-07)."),
    CommandSpec("fleet", "CMD-04", "fleet", IMMEDIATE, "List every owned ship, its location and state."),
    CommandSpec("view", "CMD-05", "view universe | galaxy | sector | follow", IMMEDIATE,
                "Change the Grid terminal level; 'follow' returns to auto-follow."),
    CommandSpec("move", "CMD-06", "move <x y | +dx +dy> [speed]", QUEUED,
                "Impulse move within the sector: absolute cell x y, or signed +dx +dy relative; speed <= engine power. "
                "Cells per turn = floor(speed - gravity pull). A far target is not clipped: the move persists turn after turn "
                "until the ship arrives, a new move replaces it, or 'stop' cancels it (D-98). Owned ships in your cell move with you (MV-06)."),
    CommandSpec("warp", "CMD-07", "warp <gx gy> <sx sy> [factor]", QUEUED,
                "Warp to a sector in any galaxy; factor <= warp core (default: full power). Reach follows the warp curve; "
                "gravity pull nets the factor at departure (HZ-10). One turn per warp.", since="M2"),
    CommandSpec("fire", "CMD-08", "fire torpedo <ship> [n] | fire phaser <ship>", QUEUED,
                "Attack a ship in range after movement: torpedoes 5 cells (n <= ammo, each rolls to hit, 25 damage), phasers 2 cells "
                "(always hit, damage falls with distance). One of each per ship per turn. Firing on a captain you are not in battle "
                "with starts a fight: respect -5, asks first (CM-01, FR-06).", since="M3"),
    CommandSpec("shields", "CMD-09", "shields [status]", IMMEDIATE, "Show current / max shields (they drain in a star's cell and recharge outside hazards and battle).", since="M2"),
    CommandSpec("peace", "CMD-10", "peace offer | accept | reject <captain>", QUEUED,
                "In a battle: offer peace (stands this turn and next), accept the other side's offer, or reject it (free). "
                "The side that did not offer is the victor and takes up to peace_loot_max % of the loser's coins and items (CB-03).", since="M3"),
    CommandSpec("escape", "CMD-11", "escape <+dx +dy> [speed]", QUEUED,
                "A move that reports whether it left every enemy's weapon range; any move that does ends the battle (CB-02).", since="M3"),
    CommandSpec("trade", "CMD-12", "trade <captain | planet> give <items> for <items>", QUEUED, "Propose a trade.", since="M4"),
    CommandSpec("upgrade", "CMD-13", "upgrade <component> with <element>", QUEUED, "Spend an element on a component.", since="M4"),
    CommandSpec("scan", "CMD-14", "scan", IMMEDIATE,
                "Reveal what is in the current cell (items), the sector with fog of war on, and the captains here with their fear and respect (LV-04)."),
    CommandSpec("pickup", "CMD-15", "pickup <item>", QUEUED, "Pick up an item in the current cell after a scan.", since="M4"),
    CommandSpec("orders", "CMD-16", "orders [cancel n]", IMMEDIATE, "Review or cancel the active ship's queued orders (a persisting move counts)."),
    CommandSpec("end", "CMD-17", "end", IMMEDIATE, "End the turn; every captain's queued orders resolve. Asks first when a hazard countdown is running and no move or warp is queued."),
    CommandSpec("quit", "CMD-18", "quit", IMMEDIATE, "Confirm, autosave, then close every window."),
    CommandSpec("new", "CMD-19", "new [seed]", IMMEDIATE, "Start a new game with a new (or given) seed; asks for confirmation (not after game over)."),
    CommandSpec("buy", "CMD-20", "buy <item> <qty>", QUEUED, "Buy from the planet in the current cell.", since="M4"),
    CommandSpec("sell", "CMD-20", "sell <item> <qty>", QUEUED, "Sell to the planet in the current cell.", since="M4"),
    CommandSpec("stop", "CMD-21", "stop [all]", IMMEDIATE,
                "All stop: cancel the active ship's persisting move and its queued move. 'stop all' (or 'all stop') does it for every owned ship.",
                since="M2"),
)}

ALIASES: dict[str, str] = {alias: c.name for c in COMMANDS.values() for alias in c.aliases}

# OV-02b: a second order of the same *kind* for the same ship replaces the first.  A torpedo and a phaser
# volley are different kinds (D-120); escape is a move (D-123).
MOVEMENT_COMMANDS = ("move", "escape")


def order_kind(command: str, args: tuple[str, ...] | list[str]) -> str:
    """The OV-02b replacement key of an order: 'move' for move/escape, 'fire:torpedo' / 'fire:phaser', else the command."""
    if command in MOVEMENT_COMMANDS:
        return "move"
    if command == "fire" and args:
        return f"fire:{str(args[0]).lower()}"
    return command


def lookup(name: str) -> CommandSpec | None:
    key = name.strip().lower()
    key = ALIASES.get(key, key)
    return COMMANDS.get(key)


def available_names() -> list[str]:
    """Commands the current milestone supports (for tab completion and help)."""
    return [c.name for c in COMMANDS.values() if c.available]


def shields_footer(shield_recharge: float | None = None) -> str:
    """CM-05 / HZ-07 / D-127: the line both ``help`` and ``help shields`` end with."""
    amount = f"{shield_recharge:g}" if shield_recharge is not None else "shield_recharge"
    return f"Shields recharge {amount} a turn outside hazards and battle (CM-05); planet cells are safe (HZ-07)."


def help_lines(name: str | None = None, shield_recharge: float | None = None) -> list[str]:
    """CMD-01: the help text, one line per entry (plain text; the terminal colours it)."""
    if name:
        spec = lookup(name)
        if spec is None:
            return [f"unknown command {name!r} - type 'help' for the list (CMD-01)"]
        note = "" if spec.available else f"  (not available until {spec.since})"
        lines = [f"{spec.syntax}{note}", f"  {spec.summary}", f"  {spec.kind} - {spec.id}"]
        if spec.name == "shields":
            lines.append(f"  {shields_footer(shield_recharge)}")
        return lines
    width = max(len(c.syntax) for c in COMMANDS.values())
    lines = ["Commands (immediate ones act at once; queued ones wait for 'end'):"]
    for c in COMMANDS.values():
        tag = c.kind if c.available else f"{c.since}+"
        lines.append(f"  {c.syntax:<{width}}  {tag:<9} {c.summary}")
    lines.append("Coordinates are zero-based x y as shown on the Grid (GRD-01). Type 'help <command>' for one entry.")
    lines.append(shields_footer(shield_recharge))
    return lines
