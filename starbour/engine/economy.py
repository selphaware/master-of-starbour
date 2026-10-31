"""Trade, prices, disposition and upgrades - the pure rules TP-05 applies.

TR-01...10, PRC-01...05, PR-01, PR-04, PR-05, PL-06, EF-03, EF-04, UP-01...05, CMD-12, CMD-13,
CMD-20, FL-02, FR-07, CFG-32...43, CFG-76, CFG-81, CFG-82, D-142, D-143, D-144, D-148, D-150.

Every number comes from the config.  Prices (D-148 (1)): a price is floored to whole silver
after the respect factor, on the total; gold converts to silver only when silver runs
short (TR-01), never upward.  Barter (D-148 (2)): food, water and torpedoes are valued at
the planet's ``*_buy`` price, elements at their ``[elements]`` value, gold at
``silver_per_gold``, silver at 1; the counterparty accepts when what it receives is worth
at least what it gives times ``1 - band/100 x disposition/100`` (TR-06), the band being
``planet_price_band`` for a planet and ``respect_price_band`` for an AI captain, the
disposition the proposer's (PL-06).  Planets hold no coins (D-140): a planet gives food,
water and elements from its stock and torpedoes without limit [ASSUMED D-150]; what it
receives joins its stock (food and water up to ``stock_cap``).  No state object is
mutated except the entities handed in; no I/O, no clocks.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from starbour.config import GameConfig
from starbour.engine import reputation as R
from starbour.entities.captain import Captain
from starbour.entities.planet import Planet
from starbour.entities.ship import Ship

SUPPLIES = ("food", "water")
TORPEDO = "torpedo"
GOLD, SILVER = "gold", "silver"
COINS = (GOLD, SILVER)
EPS = 1e-9
FR_07, FR_08 = "FR-07", "FR-08"
COMPONENT_STAT = {"torpedoes": "torpedoes", "phasers": "phasers", "shields": "shield_max", "engine": "engine", "warp": "warp"}


# --------------------------------------------------------------------------- formulas (tabs 11, 12, 14; D-142)

def disposition(respect: float) -> int:
    """PL-06 / D-142: disposition = 2 x respect - 100 (-100...100)."""
    return int(round(2 * float(respect) - 100))


def price_factor(respect: float, band: float, *, selling: bool) -> float:
    """D-142 / EF-04: buying from a planet 1 - band/100 x respect/100; selling to it 1 + ... (prices improve as respect rises)."""
    f = float(band) / 100.0 * float(respect) / 100.0
    return 1.0 + f if selling else 1.0 - f


def barter_factor(disp: float, band: float) -> float:
    """TR-06 / D-142: the counterparty accepts when received >= given x (1 - band/100 x disposition/100)."""
    return 1.0 - float(band) / 100.0 * float(disp) / 100.0


def floor_silver(amount: float) -> int:
    """D-148 (1): whole silver, floored after the factor."""
    return int(math.floor(float(amount) + EPS))


def trades_with(planet: Planet, cap: Captain, cfg: GameConfig) -> bool:
    """EF-04 / PR-05 / TR-10: a live planet trades at respect >= planet_refuse_respect; after mastery every planet trades."""
    return not planet.destroyed and (bool(cap.is_master) or int(cap.respect) >= int(cfg.planet_refuse_respect))


def refusal(planet: Planet, cap: Captain, cfg: GameConfig) -> str | None:
    """Why the planet will not trade with the captain, or None (C11: the reason names the rule)."""
    if planet.destroyed:
        return f"{planet.name} has been destroyed (CM-11)"
    if not trades_with(planet, cap, cfg):
        return f"{planet.name} refuses to trade: respect {cap.respect} < {cfg.planet_refuse_respect} (EF-04)"
    return None


def list_unit_value(cfg: GameConfig, item: str) -> float | None:
    """D-148 (2): the list value of one unit in a barter; None for an unknown item."""
    if item in SUPPLIES:
        return float(cfg.prices[f"{item}_buy"])
    if item == TORPEDO:
        return float(cfg.prices["torpedo_buy"])
    if item == GOLD:
        return float(cfg.silver_per_gold)
    if item == SILVER:
        return 1.0
    spec = cfg.elements.get(item)
    return float(spec.value) if spec is not None else None


def planet_unit_price(cfg: GameConfig, item: str, respect: float, *, selling: bool) -> float | None:
    """PRC-01...04 / EF-04: one unit at the planet, factored by the captain's respect; None when the planet has no price for it.
    ``selling`` = the captain sells to the planet (the planet pays the sell price)."""
    factor = price_factor(respect, cfg.planet_price_band, selling=selling)
    if item in SUPPLIES:
        base = float(cfg.prices[f"{item}_sell" if selling else f"{item}_buy"])
    elif item == TORPEDO:
        base = float(cfg.prices["torpedo_sell" if selling else "torpedo_buy"])
    elif item in cfg.elements:
        value = float(cfg.elements[item].value)
        base = value * float(cfg.prices["element_sell_pct"]) / 100.0 if selling else value
    else:
        return None
    return base * factor


def total_price(unit: float, n: int) -> int:
    """The floored total for n units (D-148 (1))."""
    return floor_silver(float(unit) * int(n))


def affordable(unit: float, want: int, wealth: int) -> int:
    """The largest n <= want whose floored total fits ``wealth`` silver-equivalent."""
    if want <= 0 or unit <= 0:
        return max(0, want) if unit <= 0 else 0
    n = min(int(want), int((float(wealth) + 1.0 - EPS) / float(unit)))
    while n > 0 and total_price(unit, n) > wealth:
        n -= 1
    return max(0, n)


def wealth(cap: Captain, cfg: GameConfig) -> int:
    return int(cap.gold) * int(cfg.silver_per_gold) + int(cap.silver)


def pay(cap: Captain, cost: int, cfg: GameConfig) -> bool:
    """TR-01: pay ``cost`` silver; gold converts at silver_per_gold only when silver runs short.  False when the captain cannot."""
    cost = int(cost)
    if wealth(cap, cfg) < cost:
        return False
    if cap.silver < cost:
        spg = int(cfg.silver_per_gold)
        need = cost - cap.silver
        gold = int(math.ceil(need / spg)) if spg > 0 else cap.gold
        cap.gold -= gold
        cap.silver += gold * spg
    cap.silver -= cost
    return True


def bundle_text(bundle: dict[str, int]) -> str:
    return ", ".join(f"{n} {item}" for item, n in bundle.items())


def bundle_value(cfg: GameConfig, bundle: dict[str, int]) -> float:
    total = 0.0
    for item, n in bundle.items():
        v = list_unit_value(cfg, item)
        total += (v or 0.0) * int(n)
    return total


def unknown_items(cfg: GameConfig, bundle: dict[str, int]) -> list[str]:
    return [item for item in bundle if list_unit_value(cfg, item) is None]


# --------------------------------------------------------------------------- holders: who has what, give and receive

def captain_holds(cap: Captain, ship: Ship | None, item: str) -> int:
    if item == "food":
        return int(cap.food)
    if item == "water":
        return int(cap.water)
    if item == TORPEDO:
        return int(ship.torpedoes) if ship is not None else 0
    if item == GOLD:
        return int(cap.gold)
    if item == SILVER:
        return int(cap.silver)
    return int(cap.items.get(item, 0))


def captain_missing(cap: Captain, ship: Ship | None, bundle: dict[str, int], who: str = "you") -> str | None:
    for item, n in bundle.items():
        have = captain_holds(cap, ship, item)
        if have < int(n):
            return f"{who} hold{'' if who == 'you' else 's'} only {have} {item}, not {n} (TR-03)"
    return None


def captain_give(cap: Captain, ship: Ship | None, bundle: dict[str, int]) -> None:
    for item, n in bundle.items():
        n = int(n)
        if item == "food":
            cap.food -= n
        elif item == "water":
            cap.water -= n
        elif item == TORPEDO:
            if ship is not None:
                ship.torpedoes -= n
        elif item == GOLD:
            cap.gold -= n
        elif item == SILVER:
            cap.silver -= n
        else:
            cap.take_item(item, n)


def captain_receive(cap: Captain, ship: Ship | None, bundle: dict[str, int]) -> None:
    for item, n in bundle.items():
        n = int(n)
        if item == "food":
            cap.food += n
        elif item == "water":
            cap.water += n
        elif item == TORPEDO:
            if ship is not None:
                ship.torpedoes += n
        elif item == GOLD:
            cap.gold += n
        elif item == SILVER:
            cap.silver += n
        else:
            cap.add_item(item, n)


def planet_holds(planet: Planet, item: str, turn_no: int, cfg: GameConfig) -> int | None:
    """What the planet can give: food / water from the stock as it stood at the end of the previous turn, elements
    from the stacks, torpedoes without limit (None), coins never (0)."""
    if item in SUPPLIES:
        return planet.stock(item, turn_no - 1, cfg)
    if item == TORPEDO:
        return None
    if item in COINS:
        return 0
    return int(planet.elements.get(item, 0))


def planet_missing(planet: Planet, bundle: dict[str, int], turn_no: int, cfg: GameConfig) -> str | None:
    for item, n in bundle.items():
        have = planet_holds(planet, item, turn_no, cfg)
        if have is None:
            continue
        if item in COINS:
            return f"{planet.name} holds no coins (D-140)"
        if have < int(n):
            return f"{planet.name} has only {have} {item} in stock, not {n} (PL-02, PL-03)"
    return None


def planet_give(planet: Planet, bundle: dict[str, int], turn_no: int, cfg: GameConfig) -> None:
    for item, n in bundle.items():
        n = int(n)
        if item in SUPPLIES:
            planet.set_stock(item, planet.stock(item, turn_no - 1, cfg) - n, turn_no - 1, cfg)
        elif item in cfg.elements:
            left = int(planet.elements.get(item, 0)) - n
            if left > 0:
                planet.elements[item] = left
            else:
                planet.elements.pop(item, None)


def planet_receive(planet: Planet, bundle: dict[str, int], turn_no: int, cfg: GameConfig) -> None:
    for item, n in bundle.items():
        n = int(n)
        if item in SUPPLIES:
            planet.set_stock(item, min(int(cfg.stock_cap), planet.stock(item, turn_no - 1, cfg) + n), turn_no - 1, cfg)
        elif item in cfg.elements:
            planet.elements[item] = int(planet.elements.get(item, 0)) + n
        # torpedoes and coins vanish into the planet (D-140 [ASSUMED])


# --------------------------------------------------------------------------- results and rows (C11)

@dataclass(slots=True)
class TradeResult:
    """One resolved order: the C11 row (``trades[]`` / ``upgrades[]``), the OUT-07/08 text, the reputation rows it earned."""

    ok: bool
    row: dict
    text: str
    rep: list[dict] = field(default_factory=list)
    ids: tuple[str, ...] = ()
    new_ship: Ship | None = None


def trade_row(captain_id: str, with_: str | None, kind: str, gave: dict, received: dict, *, accepted: bool, reason: str | None = None,
              factor: float | None = None) -> dict:
    return {"captain": captain_id, "with": with_, "kind": kind, "gave": dict(gave), "received": dict(received), "accepted": bool(accepted),
            "reason": reason, "price_factor": round(float(factor), 4) if factor is not None else None}


def fr07(cap: Captain, cfg: GameConfig) -> dict:
    """FR-07 / CFG-08 / TR-09: +trade_accepted_delta respect for any accepted trade, unscaled (D-121)."""
    return R.Delta(cap.id, FR_07, 0, int(cfg.respect_trade_accepted_delta), 1.0).to_dict()


def _refused(cap: Captain, with_: str | None, kind: str, gave: dict, received: dict, reason: str, factor: float | None = None) -> TradeResult:
    return TradeResult(False, trade_row(cap.id, with_, kind, gave, received, accepted=False, reason=reason, factor=factor), f"trade rejected: {reason}",
                       ids=("OUT-07", "CMD-20" if kind in ("buy", "sell", "ship") else "CMD-12"))


# --------------------------------------------------------------------------- buy / sell (CMD-20, PRC-01...05, TR-04)

def resolve_buy(cap: Captain, ship: Ship, planet: Planet | None, item: str, n: int, cfg: GameConfig, turn_no: int, *,
                cls: str | None = None, new_ship_id: str | None = None, ship_name: str | None = None) -> TradeResult:
    """``buy <item> <n>`` / ``buy ship <class>`` at the planet in the ship's cell, resolved at TP-05.  For a ship purchase the
    caller hands in the id and the name of the ship to create (FL-02, PRC-05)."""
    kind = "ship" if item == "ship" else "buy"
    with_ = planet.id if planet is not None else None
    if planet is None:
        return _refused(cap, None, kind, {}, {}, f"no planet in cell {ship.cell[0]},{ship.cell[1]} - a trade needs the planet's cell (PR-04, CMD-20)")
    why = refusal(planet, cap, cfg)
    if why:
        return _refused(cap, with_, kind, {}, {item: n} if kind == "buy" else {"ship": cls}, why)
    if kind == "ship":
        assert cls is not None
        price = cfg.prices.get(f"ship_{cls}")
        spec = cfg.ships.get(cls)
        if price is None or spec is None:
            return _refused(cap, with_, kind, {}, {"ship": cls}, f"{planet.name} sells no ship of class {cls!r} (PRC-05, [prices] ship_<class>)")
        if cap.gold < int(price):
            return _refused(cap, with_, kind, {GOLD: int(price)}, {"ship": cls}, f"not enough gold: {price} needed, {cap.gold} held (PRC-05)")
        cap.gold -= int(price)
        new = Ship.of_class(str(new_ship_id), str(ship_name), spec, cap.id, ship.position)
        cap.ships.append(new.id)
        row = trade_row(cap.id, with_, kind, {GOLD: int(price)}, {"ship": cls, "id": new.id}, accepted=True)
        return TradeResult(True, row, f"Bought a {spec.name.capitalize()}, the {new.name} ({new.id}), from {planet.name} for {price} gold; it joins your fleet here (OUT-07, PRC-05, FL-02)",
                           [fr07(cap, cfg)], ("OUT-07", "PRC-05", "FL-02", "FR-07"), new_ship=new)
    unit = planet_unit_price(cfg, item, cap.respect, selling=False)
    if unit is None:
        return _refused(cap, with_, kind, {}, {item: n}, f"{planet.name} sells no {item!r} - food, water, torpedo or an element (CMD-20, PRC-01...04)")
    factor = price_factor(cap.respect, cfg.planet_price_band, selling=False)
    available = planet_holds(planet, item, turn_no, cfg)
    cost = total_price(unit, n)
    have = wealth(cap, cfg)
    problems: list[str] = []
    if have < cost:
        problems.append(f"not enough silver: {cost} needed, {have} held (TR-04)")
    if available is not None and available < n:
        problems.append(f"{planet.name} has only {available} {item} in stock (PL-02, PL-03)")
    if problems:
        return _refused(cap, with_, kind, {SILVER: cost}, {item: n}, "; ".join(problems), factor)
    pay(cap, cost, cfg)
    captain_receive(cap, ship, {item: n})
    planet_give(planet, {item: n}, turn_no, cfg)
    row = trade_row(cap.id, with_, kind, {SILVER: cost}, {item: n}, accepted=True, factor=factor)
    return TradeResult(True, row, f"Bought {n} {item} from {planet.name} for {cost} silver (factor {factor:.2f}, OUT-07)", [fr07(cap, cfg)],
                       ("OUT-07", "CMD-20", "TR-04", "FR-07"))


def resolve_sell(cap: Captain, ship: Ship, planet: Planet | None, item: str, n: int, cfg: GameConfig, turn_no: int) -> TradeResult:
    """``sell <item> <n>`` to the planet in the ship's cell (PRC-01...04), resolved at TP-05."""
    with_ = planet.id if planet is not None else None
    if planet is None:
        return _refused(cap, None, "sell", {item: n}, {}, f"no planet in cell {ship.cell[0]},{ship.cell[1]} - a trade needs the planet's cell (PR-04, CMD-20)")
    why = refusal(planet, cap, cfg)
    if why:
        return _refused(cap, with_, "sell", {item: n}, {}, why)
    unit = planet_unit_price(cfg, item, cap.respect, selling=True)
    if unit is None:
        return _refused(cap, with_, "sell", {item: n}, {}, f"{planet.name} buys no {item!r} - food, water, torpedo or an element (CMD-20, PRC-01...04)")
    factor = price_factor(cap.respect, cfg.planet_price_band, selling=True)
    have = captain_holds(cap, ship, item)
    if have < n:
        return _refused(cap, with_, "sell", {item: n}, {}, f"you hold only {have} {item}, not {n} (CMD-20)", factor)
    proceeds = total_price(unit, n)
    captain_give(cap, ship, {item: n})
    cap.silver += proceeds
    planet_receive(planet, {item: n}, turn_no, cfg)
    row = trade_row(cap.id, with_, "sell", {item: n}, {SILVER: proceeds}, accepted=True, factor=factor)
    return TradeResult(True, row, f"Sold {n} {item} to {planet.name} for {proceeds} silver (factor {factor:.2f}, OUT-07)", [fr07(cap, cfg)],
                       ("OUT-07", "CMD-20", "PRC-04", "FR-07"))


# --------------------------------------------------------------------------- barter (CMD-12, TR-02, TR-03, TR-06, EF-03, EF-04)

def resolve_barter(cap: Captain, ship: Ship, counterparty: Any, their_ship: Ship | None, give: dict[str, int], want: dict[str, int],
                   cfg: GameConfig, turn_no: int) -> TradeResult:
    """``trade <counterparty> give <give> for <want>``: ``counterparty`` is a Planet or a Captain (with ``their_ship`` for torpedoes);
    the valuation and the acceptance follow D-142 / D-148 (2)."""
    is_planet = isinstance(counterparty, Planet)
    with_ = counterparty.id
    gave_text, want_text = bundle_text(give), bundle_text(want)
    bad = unknown_items(cfg, give) + unknown_items(cfg, want)
    if bad:
        return _refused(cap, with_, "barter", give, want, f"unknown item(s) {', '.join(sorted(set(bad)))} - food, water, torpedo, gold, silver or an element (CMD-12)")
    if is_planet:
        why = refusal(counterparty, cap, cfg)
        if why:
            return _refused(cap, with_, "barter", give, want, why)
        band, factor_base = float(cfg.planet_price_band), None
    else:
        band = float(cfg.respect_price_band)
    disp = disposition(cap.respect)
    factor = barter_factor(disp, band)
    missing = captain_missing(cap, ship, give)
    if missing:
        return _refused(cap, with_, "barter", give, want, missing, factor)
    missing = planet_missing(counterparty, want, turn_no, cfg) if is_planet else captain_missing(counterparty, their_ship, want, counterparty.name)
    if missing:
        return _refused(cap, with_, "barter", give, want, missing, factor)
    received_value = bundle_value(cfg, give)      # what the counterparty receives
    given_value = bundle_value(cfg, want)         # what it gives
    if received_value + EPS < given_value * factor:
        reason = (f"{counterparty.name} declines: {want_text} worth {given_value:g} x {factor:.2f} = {given_value * factor:.2f} > {gave_text} worth "
                  f"{received_value:g} at list prices (TR-06, {'EF-04' if is_planet else 'EF-03'}, D-142)")
        return _refused(cap, with_, "barter", give, want, reason, factor)
    captain_give(cap, ship, give)
    if is_planet:
        planet_give(counterparty, want, turn_no, cfg)
        planet_receive(counterparty, give, turn_no, cfg)
    else:
        captain_give(counterparty, their_ship, want)
        captain_receive(counterparty, their_ship, give)
    captain_receive(cap, ship, want)
    rep = [fr07(cap, cfg)]
    if not is_planet:
        rep.append(fr07(counterparty, cfg))     # D-121: every FR rule applies to AI captains too
    row = trade_row(cap.id, with_, "barter", give, want, accepted=True, factor=factor)
    return TradeResult(True, row, f"Traded {gave_text} to {counterparty.name} for {want_text} (disposition {disp:+d}, OUT-07)", rep,
                       ("OUT-07", "CMD-12", "TR-06", "FR-07"))


# --------------------------------------------------------------------------- upgrades (CMD-13, UP-01...05, D-143)

def cap_for(component: str, cfg: GameConfig) -> float | None:
    """UP-01...05 / D-143 caps: torpedoes none, phasers phaser_max, shields shield_cap, engine impulse_max, warp warp_max."""
    return {"torpedoes": None, "phasers": float(cfg.phaser_max), "shields": float(cfg.shield_cap), "engine": float(cfg.impulse_max),
            "warp": float(cfg.warp_max)}[component]


def apply_upgrade(ship: Ship, component: str, amount: float, cfg: GameConfig) -> tuple[float, float, bool]:
    """Raise the component by ``amount`` up to its cap; returns (before, after, capped).  Shields raise shield_max and the
    current shields by the same delta (an upgrade installs fresh capacity, [ASSUMED] D-150)."""
    stat = COMPONENT_STAT[component]
    before = float(getattr(ship, stat))
    limit = cap_for(component, cfg)
    after = before + float(amount)
    capped = limit is not None and after > limit + EPS
    if capped:
        after = max(before, float(limit))
    if component == "torpedoes":
        ship.torpedoes = int(round(after))
        after = float(ship.torpedoes)
    else:
        setattr(ship, stat, after)
        if component == "shields":
            ship.shield_current = min(after, float(ship.shield_current) + (after - before))
    return before, after, capped


def resolve_upgrade(cap: Captain, ship: Ship, component: str, element: str, cfg: GameConfig) -> TradeResult:
    """``upgrade <component> with <element>`` at TP-05: one unit consumed, the [upgrades] amount applied, capped (D-143)."""
    table = cfg.upgrades.get(component) or {}
    amount = table.get(element)
    row = {"captain": cap.id, "component": component, "element": element, "before": None, "after": None, "capped": False}
    if amount is None:
        improves = ", ".join(sorted(table)) or "nothing"
        return TradeResult(False, row, f"upgrade rejected: {element} does not improve {component} - the [upgrades] map lists {improves} (CMD-13, TR-05)",
                           ids=("OUT-08", "CMD-13"))
    if not cap.take_item(element, 1):
        return TradeResult(False, row, f"upgrade rejected: no {element} held (CMD-13)", ids=("OUT-08", "CMD-13"))
    before, after, capped = apply_upgrade(ship, component, float(amount), cfg)
    row.update(before=before, after=after, capped=bool(capped))
    limit = cap_for(component, cfg)
    tail = f" (capped at {limit:g})" if capped and limit is not None else ""
    return TradeResult(True, row, f"{component.capitalize()} upgraded with {element}: {before:g} -> {after:g}{tail} (OUT-08, UP-01...05)", ids=("OUT-08", "CMD-13", "D-143"))


# --------------------------------------------------------------------------- the AI at a planet (D-144, CFG-81)

def ai_dock(cap: Captain, ship: Ship, planet: Planet, cfg: GameConfig, turn_no: int, stats: dict) -> list[dict]:
    """A docked AI captain's turn at a planet that trades with it (D-144): the placeholder income, then it sells every element
    stack, restocks food and water to their starts and buys torpedoes when below half its class ammo, as its coins allow.
    Counts land in ``stats['ai_economy']``; the rows in ``stats['trades']``; returns the FR-07 rows earned."""
    econ = stats["ai_economy"]
    econ["docked"] = econ.get("docked", 0) + 1
    if int(cfg.docking_income_silver) > 0:
        cap.silver += int(cfg.docking_income_silver)
    rep: list[dict] = []
    for element in sorted(cap.items):
        n = int(cap.items[element])
        if n <= 0 or element not in cfg.elements:
            continue
        res = resolve_sell(cap, ship, planet, element, n, cfg, turn_no)
        if res.ok:
            econ["sales"] = econ.get("sales", 0) + 1
            stats["trades"].append(res.row)
            rep += res.rep
    for item, start in (("food", int(cfg.food_start)), ("water", int(cfg.water_start))):
        want = start - captain_holds(cap, ship, item)
        if want <= 0:
            continue
        unit = planet_unit_price(cfg, item, cap.respect, selling=False)
        available = planet_holds(planet, item, turn_no, cfg)
        n = affordable(unit or 0.0, min(want, available if available is not None else want), wealth(cap, cfg))
        if n > 0:
            res = resolve_buy(cap, ship, planet, item, n, cfg, turn_no)
            if res.ok:
                econ["purchases"] = econ.get("purchases", 0) + 1
                stats["trades"].append(res.row)
                rep += res.rep
    spec = cfg.ships.get(ship.cls)
    if spec is not None and ship.torpedoes < spec.torpedoes / 2.0:
        unit = planet_unit_price(cfg, TORPEDO, cap.respect, selling=False)
        n = affordable(unit or 0.0, int(spec.torpedoes) - int(ship.torpedoes), wealth(cap, cfg))
        if n > 0:
            res = resolve_buy(cap, ship, planet, TORPEDO, n, cfg, turn_no)
            if res.ok:
                econ["purchases"] = econ.get("purchases", 0) + 1
                stats["trades"].append(res.row)
                rep += res.rep
    return rep


def restocked(cap: Captain, cfg: GameConfig) -> bool:
    """D-144: an AI captain leaves the planet once both supplies are back at their starts - judged at TP-01, one upkeep after
    the purchase that filled them (TP-05 buys to the start, TP-06 takes one)."""
    return int(cap.food) >= int(cfg.food_start) - 1 and int(cap.water) >= int(cfg.water_start) - 1
