"""Headless simulation - D-55, CONTRACTS C2.

    python -m starbour sim --seed 1234 --turns 30 --ai-player --report PATH [--save-dir DIR] [--bus ...]

Runs the engine in-process (or, with ``--bus``, the real ZeroMQ server driven from the
sockets) and writes the C2 JSON report.  Exit 0 when the requested turns completed or
the game ended legitimately; 1 on any exception; 4 on a config error (handled in
``__main__``).
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

log = logging.getLogger("starbour.sim")
SCHEMA = 1


@dataclass
class Recorder:
    """Collects the per-turn rows of the C2 report."""

    seed: int
    config_path: str
    turns_requested: int
    per_turn: list[dict] = field(default_factory=list)
    mutinies: list[dict] = field(default_factory=list)
    started: float = field(default_factory=time.perf_counter)

    def record_turn(self, state: GameState, events: list[dict], stats: dict) -> None:
        cap = state.player
        ship = state.active_ship()
        self.mutinies.extend(stats.get("mutinies") or [])
        self.per_turn.append({
            "turn_no": state.turn_no,
            "orders_executed": dict(stats.get("orders_executed") or {"player": 0, "ai": 0}),
            "ai_ships_moved": int(stats.get("ai_ships_moved") or 0),
            "player": {
                "food": cap.food, "water": cap.water, "fear": cap.fear, "respect": cap.respect,
                "gold": cap.gold, "silver": cap.silver,
                "ship_class": ship.cls if ship else None,
                "position": [[ship.galaxy[0], ship.galaxy[1]], [ship.sector[0], ship.sector[1]], [ship.cell[0], ship.cell[1]]] if ship else None,
            },
            "captains": len(state.captains),
            "events": [str(e.get("text", "")) for e in events],
            "snapshot_hash": stats.get("snapshot_hash"),
            "snapshot_bytes": int(stats.get("snapshot_bytes") or 0),
            "resolve_ms": round(float(stats.get("resolve_ms") or 0.0), 1),
        })

    def report(self, state: GameState, outcome: str) -> dict:
        return {
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
            },
        }


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


def run_sim(cfg: GameConfig, *, seed: int, turns: int, ai_player: bool, report_path: Path | None,
            save_dir: Path, bus: bool = False, pub_port: int = 0, pull_port: int = 0) -> int:
    """C2.  Always a new game (never resumes) so the gates compare like with like."""
    save_dir = Path(save_dir)
    save_dir.mkdir(parents=True, exist_ok=True)
    recorder = Recorder(seed, str(cfg.path), turns)
    engine = Engine(cfg, seed=seed, save_path=save_dir / "game.json", pub_port=pub_port, pull_port=pull_port, bus=bus,
                    ai_player=ai_player, max_turns=turns, recorder=recorder, resume=False)
    exit_code = 0
    try:
        engine.start()
        if bus:
            log.info("sim --bus serving on PUB %d / PULL %d for %d turns", engine.pub_port, engine.pull_port, turns)
            exit_code = engine.run()
        else:
            for _ in range(turns):
                engine.resolve_and_publish()
                if engine.stopping:
                    break
            engine.close()
    except Exception:   # noqa: BLE001 - C2: exit 1 with the traceback on stderr
        traceback.print_exc()
        exit_code = 1
    finally:
        if report_path is not None:
            try:
                write_report(Path(report_path), recorder.report(engine.state, outcome_of(engine.state)))
            except OSError as e:
                log.error("could not write the report: %s", e)
                exit_code = exit_code or 1
    return exit_code
