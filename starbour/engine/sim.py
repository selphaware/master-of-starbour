"""Headless simulation - D-55, D-104, D-125, D-126, CONTRACTS C2, C9, C10 and C11.

    python -m starbour sim --seed 1234 --turns 30 --ai-player --report PATH [--save-dir DIR] [--bus ...]
                           [--teleport "gx gy sx sy cx cy"] [--player-script PATH] [--world-index]
                           [--spawn "<class> gx gy sx sy cx cy [fear] [respect]"]... [--ai passive]
                           [--give "<element>:<units>[,...]"] [--coins G S] [--respect N] [--fear N]

Runs the engine in-process (or, with ``--bus``, the real ZeroMQ server driven from the
sockets) and writes the C2 JSON report with the C9, C10 and C11 additions.  Exit 0 when the
requested turns completed or the game ended legitimately; 1 on any exception; 4 on a
config error (handled in ``__main__``).  ``--player-script`` replaces ``--ai-player``: one
line per turn, blank = no order, several orders separated by ';' (D-104, C10).
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from starbour.config import GameConfig
from starbour.engine.gamelog import turn_row
from starbour.engine.server import Engine
from starbour.engine.state import GameState
from starbour.entities.planet import PLANET
from starbour.entities.ship import Position
from starbour.world.generate import flatten_cell
from starbour.world.grid import chebyshev
from starbour.world.stellar import BLACK_HOLE, NEUTRON_STAR, STAR, WORMHOLE

log = logging.getLogger("starbour.sim")
SCHEMA = 1
INDEX_LIMIT = 200   # C9: at most 200 entries per world_index list, nearest to the player's start first
BATTLE_OUTCOMES = ("destroyed", "peace", "escape", "open")


@dataclass
class Recorder:
    """Collects the per-turn rows of the C2 report (plus the C9, C10 and C11 fields)."""

    seed: int
    config_path: str
    turns_requested: int
    per_turn: list[dict] = field(default_factory=list)
    mutinies: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # script replies, prepended to the next turn's events
    hazard_deaths: int = 0
    warps: int = 0
    roams: int = 0
    combat_deaths: int = 0
    captures: int = 0
    trades: int = 0
    pickups: int = 0
    upgrades: int = 0
    planets_destroyed: int = 0
    ai_purchases: int = 0
    ai_mutinies: int = 0
    captains_start: int = 0
    population_min: int | None = None
    population_max: int | None = None
    battle_records: dict[str, dict] = field(default_factory=dict)   # battle id -> its latest record (C10 final counts)
    game_over_cause: str | None = None
    world_index: dict | None = None
    spawned: list[dict] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def start(self, state: GameState) -> None:
        """C11: the captain count at turn 0 (after any --spawn) and the population bounds from it."""
        n = len(state.captains)
        self.captains_start = n
        self.population_min = n if self.population_min is None else min(self.population_min, n)
        self.population_max = n if self.population_max is None else max(self.population_max, n)

    def record_turn(self, state: GameState, events: list[dict], stats: dict) -> None:
        self.mutinies.extend(stats.get("mutinies") or [])
        destroyed = list(stats.get("destroyed") or [])
        warps = list(stats.get("warps") or [])
        battles = list(stats.get("battles") or [])
        captures = list(stats.get("captures") or [])
        self.hazard_deaths += sum(1 for d in destroyed if d.get("cause") != "combat")
        self.combat_deaths += sum(1 for d in destroyed if d.get("cause") == "combat")
        self.warps += len(warps)
        self.roams += int(stats.get("roams") or 0)
        self.captures += len(captures)
        self.trades += sum(1 for r in (stats.get("trades") or []) if r.get("accepted"))
        self.pickups += len(stats.get("pickups") or [])
        self.upgrades += len(stats.get("upgrades") or [])
        self.planets_destroyed += int(stats.get("planets_destroyed") or 0)
        self.ai_purchases += int((stats.get("ai_economy") or {}).get("purchases", 0))
        self.ai_mutinies += sum(1 for m in (stats.get("mutinies") or []) if str(m.get("captain")) != "player")
        n = len(state.captains)
        self.population_min = n if self.population_min is None else min(self.population_min, n)
        self.population_max = n if self.population_max is None else max(self.population_max, n)
        for b in battles:
            self.battle_records[str(b.get("id"))] = b
        over = stats.get("game_over")
        if over:
            self.game_over_cause = str(over.get("cause"))
        self.per_turn.append(turn_row(state, events, stats, self.notes))   # the row is shared with turns-<seed>.jsonl (gamelog)
        self.notes = []

    def report(self, state: GameState, outcome: str) -> dict:
        by_outcome = {k: 0 for k in BATTLE_OUTCOMES}
        ai_vs_ai = 0
        for b in self.battle_records.values():
            by_outcome[str(b.get("ended") or "open")] += 1
            if state.player_id not in (b.get("sides") or []):
                ai_vs_ai += 1
        cap = state.captains.get(state.player_id)
        doc = {
            "schema": SCHEMA,
            "seed": self.seed,
            "config_path": self.config_path,
            "turns_requested": self.turns_requested,
            "turns_completed": len(self.per_turn),
            "outcome": outcome,
            "elapsed_s": round(time.perf_counter() - self.started, 3),
            "per_turn": self.per_turn,
            "final": {
                "captains": len(state.captains),
                "ships": len(state.ships),
                "mutinies": list(self.mutinies),
                "game_over_turn": state.game_over_turn,
                "mastery_turn": state.mastery_turn,
                "game_over_cause": self.game_over_cause,
                "hazard_deaths": self.hazard_deaths,
                "warps": self.warps,
                "battles": len(self.battle_records),
                "battles_by_outcome": by_outcome,
                "combat_deaths": self.combat_deaths,
                "captures": self.captures,
                "ai_vs_ai_battles": ai_vs_ai,
                "player_fear": cap.fear if cap else None,
                "player_respect": cap.respect if cap else None,
                "roams": self.roams,
                "trades": self.trades,
                "pickups": self.pickups,
                "upgrades": self.upgrades,
                "planets_destroyed": self.planets_destroyed,
                "ai_purchases": self.ai_purchases,
                "ai_mutinies": self.ai_mutinies,
                "population_min": self.population_min if self.population_min is not None else len(state.captains),
                "population_max": self.population_max if self.population_max is not None else len(state.captains),
                "captains_start": self.captains_start,
            },
            "spawned": [dict(s) for s in self.spawned],
        }
        if self.world_index is not None:
            doc["world_index"] = self.world_index
        return doc


def outcome_of(state: GameState) -> str:
    if state.game_over_turn is not None:
        return "game_over"
    if state.mastery_turn is not None:
        return "mastery"
    return "running"


def write_report(path: Path, report: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=1), encoding="utf-8")


def read_script(path: Path) -> list[str]:
    """D-104: one line per turn, blank = no order; trailing whitespace ignored, comments start with '#'."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    out = []
    for raw in lines:
        text = raw.strip()
        out.append("" if text.startswith("#") else text)
    return out


def world_index(state: GameState, cfg: GameConfig) -> dict:
    """C9 / C11 ``--world-index``: stars, black holes (wormholes with their exit), neutron stars, planets (with id, stock and
    defence) and items (element, units, rarity - fog ignored), nearest to the player's start first (Chebyshev on the flattened
    cell grid), at most 200 per list."""
    ship = state.active_ship()
    start: Position = ship.position if ship else ((0, 0), (0, 0), (0, 0))
    origin = flatten_cell(start, cfg)
    stars: list[tuple] = []
    holes: list[tuple] = []
    neutrons: list[tuple] = []
    planets: list[tuple] = []
    items: list[tuple] = []
    for (g, s) in sorted(state.world.sectors):
        sector = state.world.sectors[(g, s)]
        for body in sector.terrain():
            cell = (int(body.cell[0]), int(body.cell[1]))
            d = chebyshev(flatten_cell((g, s, cell), cfg), origin)
            key = (d, g, s, cell)
            entry = {"galaxy": [g[0], g[1]], "sector": [s[0], s[1]], "cell": [cell[0], cell[1]]}
            if body.kind == STAR:
                entry.update({"bucket": body.bucket, "mass": round(float(body.mass), 4), "colour": body.colour})
                stars.append((key, entry))
            elif body.kind in (BLACK_HOLE, WORMHOLE):
                partner = getattr(body, "partner", None) if body.kind == WORMHOLE else None
                entry["wormhole_to"] = ({"galaxy": [partner[0][0], partner[0][1]], "sector": [partner[1][0], partner[1][1]],
                                         "cell": [partner[2][0], partner[2][1]]} if partner else None)
                holes.append((key, entry))
            elif body.kind == NEUTRON_STAR:
                neutrons.append((key, entry))
            elif body.kind == PLANET:
                entry.update({"name": body.name, "id": body.id, "president": body.president,
                              "stock": {"food": body.stock("food", state.turn_no, cfg), "water": body.stock("water", state.turn_no, cfg),
                                        "elements": {k: int(v) for k, v in sorted(body.elements.items())}},
                              "defence": {"shield_max": float(body.shield_max), "torpedoes": int(body.torpedoes), "phasers": float(body.phasers)}})
                planets.append((key, entry))
        for item in sector.items:
            cell = (int(item.cell[0]), int(item.cell[1]))
            d = chebyshev(flatten_cell((g, s, cell), cfg), origin)
            entry = {"galaxy": [g[0], g[1]], "sector": [s[0], s[1]], **item.index_entry()}
            items.append(((d, g, s, cell, item.id), entry))

    def top(entries: list[tuple]) -> list[dict]:
        entries.sort(key=lambda kv: kv[0])
        return [e for _, e in entries[:INDEX_LIMIT]]

    return {"player_start": [[start[0][0], start[0][1]], [start[1][0], start[1][1]], [start[2][0], start[2][1]]],
            "stars": top(stars), "black_holes": top(holes), "neutron_stars": top(neutrons), "planets": top(planets), "items": top(items)}


def run_sim(cfg: GameConfig, *, seed: int, turns: int, ai_player: bool, report_path: Path | None,
            save_dir: Path, bus: bool = False, pub_port: int = 0, pull_port: int = 0, teleport: Position | None = None,
            script_path: Path | None = None, with_world_index: bool = False, spawns: list[str] | None = None,
            ai_passive: bool = False, log_dir: Path | None = None, give: dict[str, int] | None = None, coins: tuple[int, int] | None = None,
            respect: int | None = None, fear: int | None = None) -> int:
    """C2 / C9 / C10 / C11.  Always a new game (never resumes) so the gates compare like with like.
    ``log_dir`` receives the gameplay logs (gamelog.py); None writes none."""
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(seed, str(cfg.path), turns)
    script = read_script(script_path) if script_path is not None else None
    if script is not None and ai_player:
        log.info("--player-script replaces --ai-player for the player (D-104)")
        ai_player = False
    engine: Engine | None = None
    exit_code = 0
    try:
        engine = Engine(cfg, seed=seed, save_path=save_dir / "game.json", pub_port=pub_port, pull_port=pull_port, bus=bus,
                        ai_player=ai_player, max_turns=turns, recorder=recorder, resume=False, teleport=teleport,
                        spawns=spawns, ai_passive=ai_passive, log_dir=log_dir, give=give, coins=coins, respect=respect, fear=fear)
        recorder.spawned = [dict(s) for s in engine.state.spawned]
        recorder.start(engine.state)
        if with_world_index:
            recorder.world_index = world_index(engine.state, cfg)
        engine.start()
        if bus:
            if script:
                log.warning("--player-script is ignored with --bus: orders come from the sockets")
            log.info("sim --bus serving on PUB %d / PULL %d for %d turns", engine.pub_port, engine.pull_port, turns)
            exit_code = engine.run()
        else:
            for t in range(turns):
                line = script[t] if script is not None and t < len(script) else ""
                if line:
                    ok, text = engine.apply_script_line(line)
                    recorder.note(text)
                    (log.info if ok else log.warning)("turn %d: %s", t + 1, text)
                engine.resolve_and_publish()
                if engine.stopping:
                    break
            engine.close()
    except Exception:   # noqa: BLE001 - C2: exit 1 with the traceback on stderr
        traceback.print_exc()
        exit_code = 1
    finally:
        if report_path is not None and engine is not None:
            try:
                write_report(Path(report_path), recorder.report(engine.state, outcome_of(engine.state)))
            except OSError as e:
                log.error("could not write the report: %s", e)
                exit_code = exit_code or 1
    return exit_code
