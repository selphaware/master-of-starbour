"""The command table: name, syntax, immediate/queued, help text - CMD-01...21, OV-02b, UX-09.

Immediate commands act at once; queued ones wait for ``end`` (OV-02b).  M2 adds warp
(CMD-07), shields (CMD-09) and stop (CMD-21, D-98); M3 adds fire (CMD-08), peace
(CMD-10) and escape (CMD-11); M4 adds trade (CMD-12), upgrade (CMD-13), pickup (CMD-15),
buy and sell (CMD-20), and ``fire <target>`` for both weapons (D-137).  ``help fire`` prints
the live numbers the caller computes from the ini (UX-09, D-138): nothing here is a literal.
"""
from __future__ import annotations

from dataclasses import dataclass

IMMEDIATE = "immediate"
QUEUED = "queued"
CURRENT_MILESTONE = "M4"
MILESTONE_ORDER = ("M1", "M2", "M3", "M4", "M5")
COMPONENTS = ("torpedoes", "phasers", "shields", "engine", "warp")
COMPONENT_WORDS = {"torpedoes": "torpedoes", "torpedo": "torpedoes", "phasers": "phasers", "phaser": "phasers", "shields": "shields",
                   "shield": "shields", "engine": "engine", "warp": "warp", "warpcore": "warp"}


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
    CommandSpec("fire", "CMD-08", "fire <target> | fire torpedo <target> [n] | fire phaser <target>", QUEUED,
                "Attack a ship or planet in range after movement. 'fire <target>' fires one torpedo and the phasers (D-137); the long forms fire one weapon "
                "(n torpedoes, default 1). Torpedoes reach 5 cells and roll to hit; phasers reach 2 and always hit. One torpedo order and one phaser "
                "order per ship per turn; a fire order lasts one turn only (D-136). Firing on a target you are not in battle with starts a fight: "
                "respect falls, asks first (CM-01, FR-06).", since="M3"),
    CommandSpec("shields", "CMD-09", "shields [status]", IMMEDIATE, "Show current / max shields (they drain in a star's cell and recharge outside hazards and battle).", since="M2"),
    CommandSpec("peace", "CMD-10", "peace offer | accept | reject <captain | planet id>", QUEUED,
                "In a battle: offer peace (stands this turn and next), accept the other side's offer, or reject it (free). "
                "The side that did not offer is the victor and takes up to peace_loot_max % of the loser's coins and items (CB-03).", since="M3"),
    CommandSpec("escape", "CMD-11", "escape <+dx +dy> [speed]", QUEUED,
                "A move that reports whether it left every enemy's weapon range; any move that does ends the battle (CB-02).", since="M3"),
    CommandSpec("trade", "CMD-12", "trade <planet id | captain> give <item:n[,...]> for <item:n[,...]>", QUEUED,
                "Propose a barter with the planet or the captain in your cell: items are food, water, torpedo, gold, silver or an element. "
                "The counterparty accepts when what it receives is worth at least what it gives at list prices, adjusted by its disposition "
                "(TR-06). Resolves at the end of the turn; an accepted trade earns respect (FR-07).", since="M4"),
    CommandSpec("upgrade", "CMD-13", "upgrade <component> with <element>", QUEUED,
                "Spend one unit of an element on the active ship's torpedoes, phasers, shields, engine or warp per the [upgrades] map; "
                "stats stop at their caps (UP-01...05). Resolves at the end of the turn.", since="M4"),
    CommandSpec("scan", "CMD-14", "scan", IMMEDIATE,
                "Reveal what is in the current cell (items, a planet), the sector with fog of war on, and the captains here with their fear and respect (LV-04)."),
    CommandSpec("pickup", "CMD-15", "pickup <element>", QUEUED, "Pick up an element stack lying in the current cell after a scan; never automatic (IT-04).", since="M4"),
    CommandSpec("orders", "CMD-16", "orders [cancel n]", IMMEDIATE, "Review or cancel the active ship's queued orders (a persisting move counts)."),
    CommandSpec("end", "CMD-17", "end", IMMEDIATE, "End the turn; every captain's queued orders resolve. Asks first when a hazard countdown is running with no move or warp queued, "
                "or when the ship is in a battle with no fire order queued (UX-04)."),
    CommandSpec("quit", "CMD-18", "quit", IMMEDIATE, "Confirm, autosave, then close every window."),
    CommandSpec("new", "CMD-19", "new [seed]", IMMEDIATE, "Start a new game with a new (or given) seed; asks for confirmation (not after game over)."),
    CommandSpec("buy", "CMD-20", "buy <food | water | torpedo | element> <n> | buy ship <class>", QUEUED,
                "Buy from the planet in your cell at its prices (silver; gold converts when silver runs short); 'buy ship <class>' pays gold and adds the ship "
                "to your fleet here (PRC-05). Refused below planet_refuse_respect (EF-04). Resolves at the end of the turn.", since="M4"),
    CommandSpec("sell", "CMD-20", "sell <food | water | torpedo | element> <n>", QUEUED,
                "Sell to the planet in your cell at its prices (elements at element_sell_pct of their value). Resolves at the end of the turn.", since="M4"),
    CommandSpec("stop", "CMD-21", "stop [all]", IMMEDIATE,
                "All stop: cancel the active ship's persisting move and its queued move. 'stop all' (or 'all stop') does it for every owned ship.",
                since="M2"),
)}

ALIASES: dict[str, str] = {alias: c.name for c in COMMANDS.values() for alias in c.aliases}

# OV-02b: a second order of the same *kind* for the same ship replaces the first.  A torpedo and a phaser
# volley are different kinds (D-120); escape is a move (D-123); a buy of food and a buy of water, a trade with
# two counterparties, an upgrade of two components are different kinds too (M4 [ASSUMED], D-150).
MOVEMENT_COMMANDS = ("move", "escape")


def order_kind(command: str, args: tuple[str, ...] | list[str]) -> str:
    """The OV-02b replacement key of an order."""
    if command in MOVEMENT_COMMANDS:
        return "move"
    if command == "fire" and args:
        return f"fire:{str(args[0]).lower()}"
    if command in ("buy", "sell", "pickup") and args:
        return f"{command}:{str(args[0]).lower()}"
    if command == "trade" and args:
        return f"trade:{str(args[0]).lower()}"
    if command == "upgrade" and args:
        return f"upgrade:{COMPONENT_WORDS.get(str(args[0]).lower(), str(args[0]).lower())}"
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


def fire_numbers_line(numbers: dict) -> str:
    """UX-09 / D-138: 'torpedo: 15 damage, hits 58 % vs engine 4; phasers: 15 / 10 / 5 damage at distance 0 / 1 / 2' from the caller's numbers."""
    dmgs = " / ".join(f"{d:g}" for d in numbers.get("phaser_damages", ()))
    dists = " / ".join(str(i) for i in range(len(numbers.get("phaser_damages", ()))))
    return (f"torpedo: {numbers['torpedo_damage']:g} damage, hits {numbers['hit_pct']} % vs engine {numbers['engine']:g}"
            f" ({numbers.get('target', 'the starting class')}); phasers: {dmgs} damage at distance {dists} (one torpedo + phasers with 'fire <target>')")


def help_lines(name: str | None = None, shield_recharge: float | None = None, fire_numbers: dict | None = None) -> list[str]:
    """CMD-01: the help text, one line per entry (plain text; the terminal colours it).  ``fire_numbers`` carries the
    UX-09 numbers the terminal computed from the ini for ``help fire``."""
    if name:
        spec = lookup(name)
        if spec is None:
            return [f"unknown command {name!r} - type 'help' for the list (CMD-01)"]
        note = "" if spec.available else f"  (not available until {spec.since})"
        lines = [f"{spec.syntax}{note}", f"  {spec.summary}"]
        if spec.name == "fire" and fire_numbers:
            lines.append(f"  {fire_numbers_line(fire_numbers)}")
        lines.append(f"  {spec.kind} - {spec.id}")
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
