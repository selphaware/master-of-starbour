"""Typed configuration read from controls_config.ini - CFG-01...73, D-49, D-53, D-71.

Every tunable number the game uses is a field here and nowhere else.  A missing or
unparsable key raises :class:`ConfigError`, which ``python -m starbour`` turns into
exit 4 naming ``[section] key`` (CONTRACTS C7).  Nothing defaults silently.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass
from pathlib import Path

GLYPH_SETS = ("unicode", "ascii")


class ConfigError(Exception):
    """controls_config.ini is missing a key or a value does not parse (exit 4, C7)."""


@dataclass(frozen=True, slots=True)
class ShipClass:
    """A ship class row from ``[ships]`` - CLS-01...03."""

    name: str
    torpedoes: int
    phasers: float
    shield_max: float
    engine: float
    warp: float


@dataclass(frozen=True, slots=True)
class ElementSpec:
    """One ``[elements]`` line: name = category, value, rarity - CFG-42, EL-01...12, D-62."""

    name: str
    category: str
    value: int
    rarity: str


@dataclass(frozen=True, slots=True)
class GameConfig:
    """Every key on tab 15 (CFG-01...73), typed as the tab says."""

    path: Path
    # [world]
    seed: int | None
    universe_size: tuple[int, int]
    galaxy_size: tuple[int, int]
    sector_size: tuple[int, int]
    fog_of_war: bool
    special_items_per_galaxy: int
    normal_items_per_galaxy: int
    ai_captains_per_galaxy: int
    safe_start: bool
    galaxy_fill_pct: int
    sector_fill_pct: int
    multi_star_pct: int
    planets_per_system_max: int
    planet_distance_min: int
    planet_distance_max: int
    hole_pct: int
    neutron_pct: int
    wormhole_pct: int
    # [bus]
    pub_port: int
    pull_port: int
    # [ui]
    log_lines: int
    glyph_set: str
    # [movement]
    impulse_max: float
    warp_max: float
    warp_curve_base: float
    warp_curve_exponent: float
    # [stellar]
    temp_scale: float
    energy_low_max: float
    energy_medium_max: float
    colour_thresholds: tuple[float, ...]
    colour_names: tuple[str, ...]
    blackhole_mass: float
    neutron_mass: float
    radius_min: float
    radius_max: float
    base_energy_min: float
    base_energy_max: float
    # [hazards]
    k_g: float
    falloff: float
    pull_turns_scale: int
    drain_low: int
    drain_medium: int
    drain_high: int
    wormhole_exit_distance: int
    # [battle]
    phaser_range: int
    torpedo_range: int
    torpedo_hit_base: float
    torpedo_evade_per_engine: float
    torpedo_damage: float
    phaser_damage_per_unit: float
    shield_recharge: int
    peace_loot_max: int
    # [ai]
    attack_ratio_min: float
    attack_fear_max: float
    peace_shield_pct: int
    escape_shield_pct: int
    planet_attack_respect_max: int
    yield_fear_min: int
    peace_shield_pct_at_fear100: int
    surrender_shield_pct: int
    roam_every_turns: int
    # [fear]
    fear_battle_won_delta: int
    fear_battle_lost_delta: int
    fear_start: int
    # [respect]
    respect_win_vs_stronger_delta: int
    respect_win_vs_weaker_peace_delta: int
    respect_win_vs_weaker_started_delta: int
    respect_start_fight_delta: int
    respect_trade_accepted_delta: int
    respect_special_item_delta: int
    respect_start: int
    # [reputation]
    scale_min: float
    scale_max: float
    # [economy]
    silver_per_gold: int
    planet_refuse_respect: int
    planet_price_band: int
    respect_price_band: int
    # [supplies]
    food_start: int
    water_start: int
    warn_turns: int
    mutiny_turns_at_zero: int
    mutiny_ship_class: str
    mutiny_supplies: int
    mutiny_fear_delta: int
    # [endgame]
    respect_min: int
    fear_min: int
    wealth_min_silver: int
    required_special_categories: int
    guard_ships: int
    # data sections (D-53)
    upgrades: dict[str, dict[str, float]]
    prices: dict[str, int]
    elements: dict[str, ElementSpec]
    ships: dict[str, ShipClass]
    starting_class: str
    # [captain]
    gold_start: int
    silver_start: int

    def ship_class(self, name: str) -> ShipClass:
        """Look a class up case-insensitively (``[supplies] mutiny_ship_class = Kestrel``)."""
        key = name.strip().lower()
        if key not in self.ships:
            raise ConfigError(f"[ships] has no class {name!r}; known: {sorted(self.ships)}")
        return self.ships[key]


class _Reader:
    """Typed accessors that name ``[section] key`` in every error."""

    def __init__(self, cp: configparser.ConfigParser, path: Path) -> None:
        self.cp = cp
        self.path = path

    def raw(self, section: str, key: str) -> str:
        if not self.cp.has_section(section):
            raise ConfigError(f"[{section}] section missing from {self.path} (D-53)")
        if not self.cp.has_option(section, key):
            raise ConfigError(f"[{section}] {key} missing from {self.path} - a missing key must fail loudly, never default (D-53)")
        value = self.cp.get(section, key).strip()
        if value == "":
            raise ConfigError(f"[{section}] {key} is empty in {self.path}")
        return value

    def text(self, section: str, key: str) -> str:
        return self.raw(section, key)

    def integer(self, section: str, key: str) -> int:
        v = self.raw(section, key)
        try:
            return int(v)
        except ValueError:
            try:
                f = float(v)
            except ValueError:
                raise ConfigError(f"[{section}] {key} = {v!r} is not an integer") from None
            if f != int(f):
                raise ConfigError(f"[{section}] {key} = {v!r} is not an integer") from None
            return int(f)

    def number(self, section: str, key: str) -> float:
        v = self.raw(section, key)
        try:
            return float(v)
        except ValueError:
            raise ConfigError(f"[{section}] {key} = {v!r} is not a number") from None

    def boolean(self, section: str, key: str) -> bool:
        v = self.raw(section, key).lower()
        if v in ("on", "true", "yes", "1"):
            return True
        if v in ("off", "false", "no", "0"):
            return False
        raise ConfigError(f"[{section}] {key} = {v!r} must be on/off or true/false")

    def size(self, section: str, key: str) -> tuple[int, int]:
        v = self.raw(section, key).lower()
        m, x, n = v.partition("x")
        if x != "x" or not m.strip().isdigit() or not n.strip().isdigit():
            raise ConfigError(f"[{section}] {key} = {v!r} must be MxN, e.g. 24x24 (CFG-54)")
        w, h = int(m), int(n)
        if w < 1 or h < 1:
            raise ConfigError(f"[{section}] {key} = {v!r} must be at least 1x1 (CFG-54)")
        return w, h

    def numbers(self, section: str, key: str) -> tuple[float, ...]:
        v = self.raw(section, key)
        out = []
        for part in v.split(","):
            part = part.strip()
            try:
                out.append(float(part))
            except ValueError:
                raise ConfigError(f"[{section}] {key}: {part!r} is not a number") from None
        return tuple(out)

    def words(self, section: str, key: str) -> tuple[str, ...]:
        return tuple(p.strip() for p in self.raw(section, key).split(",") if p.strip())

    def pct(self, section: str, key: str) -> int:
        n = self.integer(section, key)
        if not 0 <= n <= 100:
            raise ConfigError(f"[{section}] {key} = {n} must be a percentage 0..100")
        return n

    def at_least(self, section: str, key: str, lowest: int) -> int:
        n = self.integer(section, key)
        if n < lowest:
            raise ConfigError(f"[{section}] {key} = {n} must be at least {lowest}")
        return n

    def section_items(self, section: str) -> list[tuple[str, str]]:
        if not self.cp.has_section(section):
            raise ConfigError(f"[{section}] section missing from {self.path} (D-53)")
        return list(self.cp.items(section))


def _parse_seed(value: str) -> int | None:
    """CFG-53: 'random' -> None (a seed is drawn per new game), else an integer."""
    if value.strip().lower() == "random":
        return None
    try:
        return int(value)
    except ValueError:
        raise ConfigError(f"[world] seed = {value!r} must be an integer or 'random' (CFG-53)") from None


def _parse_ships(r: _Reader) -> tuple[dict[str, ShipClass], str]:
    ships: dict[str, ShipClass] = {}
    starting = ""
    for name, value in r.section_items("ships"):
        if name == "starting_class":
            starting = value.strip().lower()
            continue
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 5:
            raise ConfigError(f"[ships] {name} must be 'torpedoes, phasers, shield_max, engine, warp', got {value!r} (CLS-01...03)")
        try:
            ships[name.lower()] = ShipClass(name.lower(), int(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]), float(parts[4]))
        except ValueError:
            raise ConfigError(f"[ships] {name} = {value!r} has a non-numeric field (CLS-01...03)") from None
    if not ships:
        raise ConfigError("[ships] defines no ship class (CLS-01...03)")
    if not starting:
        raise ConfigError("[ships] starting_class missing (D-71)")
    if starting not in ships:
        raise ConfigError(f"[ships] starting_class = {starting!r} is not a class in [ships]")
    return ships, starting


def _parse_elements(r: _Reader) -> dict[str, ElementSpec]:
    out: dict[str, ElementSpec] = {}
    for name, value in r.section_items("elements"):
        parts = [p.strip() for p in value.split(",")]
        if len(parts) != 3:
            raise ConfigError(f"[elements] {name} must be 'category, value, rarity', got {value!r} (CFG-42)")
        if not parts[1].isdigit():
            raise ConfigError(f"[elements] {name} value {parts[1]!r} must be an integer number of silver (CFG-42)")
        if parts[2] not in ("normal", "special"):
            raise ConfigError(f"[elements] {name} rarity {parts[2]!r} must be normal or special (CFG-42)")
        out[name.lower()] = ElementSpec(name.lower(), parts[0], int(parts[1]), parts[2])
    if not out:
        raise ConfigError("[elements] is empty (EL-01...12)")
    return out


def _parse_upgrades(r: _Reader, elements: dict[str, ElementSpec]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for component in ("torpedoes", "phasers", "shields", "engine", "warp"):
        value = r.raw("upgrades", component)
        table: dict[str, float] = {}
        for pair in value.split(","):
            name, sep, amount = pair.strip().partition(":")
            if sep != ":":
                raise ConfigError(f"[upgrades] {component}: {pair.strip()!r} must be element:amount (CFG-33...37)")
            name = name.strip().lower()
            if name not in elements:
                raise ConfigError(f"[upgrades] {component} names unknown element {name!r} (D-53)")
            try:
                table[name] = float(amount)
            except ValueError:
                raise ConfigError(f"[upgrades] {component}: amount {amount!r} for {name} is not a number") from None
        out[component] = table
    return out


def _parse_prices(r: _Reader) -> dict[str, int]:
    out: dict[str, int] = {}
    for key, _ in r.section_items("prices"):
        out[key] = r.integer("prices", key)
    for required in ("food_buy", "food_sell", "water_buy", "water_sell", "torpedo_buy", "torpedo_sell", "element_sell_pct"):
        if required not in out:
            raise ConfigError(f"[prices] {required} missing (CFG-38...40)")
    return out


def load_config(path: Path | str) -> GameConfig:
    """Read and validate controls_config.ini - D-79 names this loader.  Raises ConfigError."""
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"controls_config.ini not found at {path} (BR-20, D-61)")
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    try:
        cp.read(path, encoding="utf-8")
    except configparser.Error as e:
        raise ConfigError(f"{path} does not parse: {e}") from None
    r = _Reader(cp, path)

    elements = _parse_elements(r)
    ships, starting_class = _parse_ships(r)
    thresholds = r.numbers("stellar", "colour_thresholds")
    names = r.words("stellar", "colour_names")
    if len(names) != len(thresholds) + 1:
        raise ConfigError(f"[stellar] colour_names has {len(names)} names but colour_thresholds has {len(thresholds)} thresholds; "
                          f"need one more name than thresholds (CFG-15, DQ-04)")
    if list(thresholds) != sorted(thresholds):
        raise ConfigError("[stellar] colour_thresholds must be ascending (CFG-15)")
    glyph_set = r.text("ui", "glyph_set").lower()
    if glyph_set not in GLYPH_SETS:
        raise ConfigError(f"[ui] glyph_set = {glyph_set!r} must be one of {GLYPH_SETS} (D-68)")
    mutiny_class = r.text("supplies", "mutiny_ship_class")
    if mutiny_class.lower() not in ships:
        raise ConfigError(f"[supplies] mutiny_ship_class = {mutiny_class!r} is not a class in [ships] (CFG-48)")
    radius_min, radius_max = r.number("stellar", "radius_min"), r.number("stellar", "radius_max")
    if not 0 < radius_min <= radius_max:
        raise ConfigError("[stellar] radius_min must be > 0 and <= radius_max (D-89)")
    base_min, base_max = r.number("stellar", "base_energy_min"), r.number("stellar", "base_energy_max")
    if not 0 < base_min <= base_max:
        raise ConfigError("[stellar] base_energy_min must be > 0 and <= base_energy_max (D-89)")
    planet_min, planet_max = r.at_least("world", "planet_distance_min", 1), r.integer("world", "planet_distance_max")
    if planet_min > planet_max:
        raise ConfigError("[world] planet_distance_min must be <= planet_distance_max (CFG-61, SP-04)")

    return GameConfig(
        path=path.resolve(),
        seed=_parse_seed(r.text("world", "seed")),
        universe_size=r.size("world", "universe_size"),
        galaxy_size=r.size("world", "galaxy_size"),
        sector_size=r.size("world", "sector_size"),
        fog_of_war=r.boolean("world", "fog_of_war"),
        special_items_per_galaxy=r.integer("world", "special_items_per_galaxy"),
        normal_items_per_galaxy=r.integer("world", "normal_items_per_galaxy"),
        ai_captains_per_galaxy=r.integer("world", "ai_captains_per_galaxy"),
        safe_start=r.boolean("world", "safe_start"),
        galaxy_fill_pct=r.pct("world", "galaxy_fill_pct"),
        sector_fill_pct=r.pct("world", "sector_fill_pct"),
        multi_star_pct=r.pct("world", "multi_star_pct"),
        planets_per_system_max=r.at_least("world", "planets_per_system_max", 0),
        planet_distance_min=planet_min,
        planet_distance_max=planet_max,
        hole_pct=r.pct("world", "hole_pct"),
        neutron_pct=r.pct("world", "neutron_pct"),
        wormhole_pct=r.pct("world", "wormhole_pct"),
        pub_port=r.integer("bus", "pub_port"),
        pull_port=r.integer("bus", "pull_port"),
        log_lines=r.integer("ui", "log_lines"),
        glyph_set=glyph_set,
        impulse_max=r.number("movement", "impulse_max"),
        warp_max=r.number("movement", "warp_max"),
        warp_curve_base=r.number("movement", "warp_curve_base"),
        warp_curve_exponent=r.number("movement", "warp_curve_exponent"),
        temp_scale=r.number("stellar", "temp_scale"),
        energy_low_max=r.number("stellar", "energy_low_max"),
        energy_medium_max=r.number("stellar", "energy_medium_max"),
        colour_thresholds=thresholds,
        colour_names=names,
        blackhole_mass=r.number("stellar", "blackhole_mass"),
        neutron_mass=r.number("stellar", "neutron_mass"),
        radius_min=radius_min,
        radius_max=radius_max,
        base_energy_min=base_min,
        base_energy_max=base_max,
        k_g=r.number("hazards", "k_g"),
        falloff=r.number("hazards", "falloff"),
        pull_turns_scale=r.integer("hazards", "pull_turns_scale"),
        drain_low=r.integer("hazards", "drain_low"),
        drain_medium=r.integer("hazards", "drain_medium"),
        drain_high=r.integer("hazards", "drain_high"),
        wormhole_exit_distance=r.at_least("hazards", "wormhole_exit_distance", 1),
        phaser_range=r.integer("battle", "phaser_range"),
        torpedo_range=r.integer("battle", "torpedo_range"),
        torpedo_hit_base=r.number("battle", "torpedo_hit_base"),
        torpedo_evade_per_engine=r.number("battle", "torpedo_evade_per_engine"),
        torpedo_damage=r.number("battle", "torpedo_damage"),
        phaser_damage_per_unit=r.number("battle", "phaser_damage_per_unit"),
        shield_recharge=r.integer("battle", "shield_recharge"),
        peace_loot_max=r.integer("battle", "peace_loot_max"),
        attack_ratio_min=r.number("ai", "attack_ratio_min"),
        attack_fear_max=r.number("ai", "attack_fear_max"),
        peace_shield_pct=r.integer("ai", "peace_shield_pct"),
        escape_shield_pct=r.integer("ai", "escape_shield_pct"),
        planet_attack_respect_max=r.integer("ai", "planet_attack_respect_max"),
        yield_fear_min=r.integer("ai", "yield_fear_min"),
        peace_shield_pct_at_fear100=r.pct("ai", "peace_shield_pct_at_fear100"),
        surrender_shield_pct=r.pct("ai", "surrender_shield_pct"),
        roam_every_turns=r.at_least("ai", "roam_every_turns", 0),
        fear_battle_won_delta=r.integer("fear", "battle_won_delta"),
        fear_battle_lost_delta=r.integer("fear", "battle_lost_delta"),
        fear_start=r.integer("fear", "start"),
        respect_win_vs_stronger_delta=r.integer("respect", "win_vs_stronger_delta"),
        respect_win_vs_weaker_peace_delta=r.integer("respect", "win_vs_weaker_peace_delta"),
        respect_win_vs_weaker_started_delta=r.integer("respect", "win_vs_weaker_started_delta"),
        respect_start_fight_delta=r.integer("respect", "start_fight_delta"),
        respect_trade_accepted_delta=r.integer("respect", "trade_accepted_delta"),
        respect_special_item_delta=r.integer("respect", "special_item_delta"),
        respect_start=r.integer("respect", "start"),
        scale_min=r.number("reputation", "scale_min"),
        scale_max=r.number("reputation", "scale_max"),
        silver_per_gold=r.integer("economy", "silver_per_gold"),
        planet_refuse_respect=r.integer("economy", "planet_refuse_respect"),
        planet_price_band=r.integer("economy", "planet_price_band"),
        respect_price_band=r.integer("economy", "respect_price_band"),
        food_start=r.integer("supplies", "food_start"),
        water_start=r.integer("supplies", "water_start"),
        warn_turns=r.integer("supplies", "warn_turns"),
        mutiny_turns_at_zero=r.integer("supplies", "mutiny_turns_at_zero"),
        mutiny_ship_class=mutiny_class.lower(),
        mutiny_supplies=r.integer("supplies", "mutiny_supplies"),
        mutiny_fear_delta=r.integer("supplies", "mutiny_fear_delta"),
        respect_min=r.integer("endgame", "respect_min"),
        fear_min=r.integer("endgame", "fear_min"),
        wealth_min_silver=r.integer("endgame", "wealth_min_silver"),
        required_special_categories=r.integer("endgame", "required_special_categories"),
        guard_ships=r.integer("endgame", "guard_ships"),
        upgrades=_parse_upgrades(r, elements),
        prices=_parse_prices(r),
        elements=elements,
        ships=ships,
        starting_class=starting_class,
        gold_start=r.integer("captain", "gold_start"),
        silver_start=r.integer("captain", "silver_start"),
    )
