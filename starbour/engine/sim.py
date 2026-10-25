"""Headless simulation - D-55, D-104, CONTRACTS C2 and C9.

    python -m starbour sim --seed 1234 --turns 30 --ai-player --report PATH [--save-dir DIR] [--bus ...]
                           [--teleport "gx gy sx sy cx cy"] [--player-script PATH] [--world-index]

Runs the engine in-process (or, with ``--bus``, the real ZeroMQ server driven from the
sockets) and writes the C2 JSON report with the C9 additions.  Exit 0 when the requested
turns completed or the game ended legitimately; 1 on any exception; 4 on a config error
(handled in ``__main__``).  ``--player-script`` replaces ``--ai-player``: one order per
line, one line per turn, blank = no order (D-104).
"""
from __future__ import annotations

import json
import logging
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

from starbour.config import GameConfig
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


@dataclass
class Recorder:
    """Collects the per-turn rows of the C2 report (plus the C9 fields)."""

    seed: int
    config_path: str
    turns_requested: int
    per_turn: list[dict] = field(default_factory=list)
    mutinies: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)       # script replies, prepended to the next turn's events
    hazard_deaths: int = 0
    warps: int = 0
    game_over_cause: str | None = None
    world_index: dict | None = None
    started: float = field(default_factory=time.perf_counter)

    def note(self, text: str) -> None:
        self.notes.append(text)

    def record_turn(self, state: GameState, events: list[dict], stats: dict) -> None:
        cap = state.player
        ship = state.active_ship()
        self.mutinies.extend(stats.get("mutinies") or [])
        destroyed = list(stats.get("destroyed") or [])
        warps = list(stats.get("warps") or [])
        self.hazard_deaths += len(destroyed)
        self.warps += len(warps)
        over = stats.get("game_over")
        if over:
            self.game_over_cause = str(over.get("cause"))
        if ship is not None:
            player = {"ship_class": ship.cls,
                      "position": [[ship.galaxy[0], ship.galaxy[1]], [ship.sector[0], ship.sector[1]], [ship.cell[0], ship.cell[1]]],
                      "shield_current": ship.shield_current, "shield_max": ship.shield_max,
                      "hazard": state.hazard_dict(ship), "move_remaining": state.move_remaining(ship.id)}
        else:
            lost = stats.get("player_lost") or {}
            player = {"ship_class": lost.get("ship_class"), "position": lost.get("position"),
                      "shield_current": lost.get("shield_current"), "shield_max": lost.get("shield_max"),
                      "hazard": lost.get("hazard"), "move_remaining": None}
        player.update({"food": cap.food, "water": cap.water, "fear": cap.fear, "respect": cap.respect, "gold": cap.gold, "silver": cap.silver})
        self.per_turn.append({
            "turn_no": state.turn_no,
            "orders_executed": dict(stats.get("orders_executed") or {"player": 0, "ai": 0}),
            "ai_ships_moved": int(stats.get("ai_ships_moved") or 0),
            "player": player,
            "captains": len(state.captains),
            "events": self.notes + [str(e.get("text", "")) for e in events],
            "snapshot_hash": stats.get("snapshot_hash"),
            "snapshot_bytes": int(stats.get("snapshot_bytes") or 0),
            "resolve_ms": round(float(stats.get("resolve_ms") or 0.0), 1),
            "warps": warps,
            "destroyed": destroyed,
        })
        self.notes = []

    def report(self, state: GameState, outcome: str) -> dict:
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
            },
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
    """C9 ``--world-index``: stars, black holes (wormholes with their exit), neutron stars and planets,
    nearest to the player's start first (Chebyshev on the flattened cell grid), at most 200 per list."""
    ship = state.active_ship()
    start: Position = ship.position if ship else ((0, 0), (0, 0), (0, 0))
    origin = flatten_cell(start, cfg)
    stars: list[tuple] = []
    holes: list[tuple] = []
    neutrons: list[tuple] = []
    planets: list[tuple] = []
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
                entry["name"] = body.name
                planets.append((key, entry))

    def top(items: list[tuple]) -> list[dict]:
        items.sort(key=lambda kv: kv[0])
        return [e for _, e in items[:INDEX_LIMIT]]

    return {"player_start": [[start[0][0], start[0][1]], [start[1][0], start[1][1]], [start[2][0], start[2][1]]],
            "stars": top(stars), "black_holes": top(holes), "neutron_stars": top(neutrons), "planets": top(planets)}


def run_sim(cfg: GameConfig, *, seed: int, turns: int, ai_player: bool, report_path: Path | None,
            save_dir: Path, bus: bool = False, pub_port: int = 0, pull_port: int = 0, teleport: Position | None = None,
            script_path: Path | None = None, with_world_index: bool = False) -> int:
    """C2 / C9.  Always a new game (never resumes) so the gates compare like with like."""
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
                        ai_player=ai_player, max_turns=turns, recorder=recorder, resume=False, teleport=teleport)
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
