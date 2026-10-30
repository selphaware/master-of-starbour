"""``python -m starbour`` - CONTRACTS C1 / C2 / C9 / C10 entry point.

    python -m starbour [--seed N] [--teleport "gx gy sx sy cx cy"] [--spawn "..."]... [--ai passive]   the launcher (TL)
    python -m starbour --role engine|command|grid|output|status
    python -m starbour sim --seed N --turns T --ai-player --report PATH [...]   (D-55)
                       [--teleport "gx gy sx sy cx cy"] [--player-script PATH] [--world-index]   (D-104)
                       [--spawn "<class> gx gy sx sy cx cy [fear] [respect]"]... [--ai passive]    (D-125, D-126)

Exit codes (C7): 0 ok, 1 failure, 4 config validation failed (message names [section] key).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from starbour import paths
from starbour.config import ConfigError, GameConfig, load_config

ROLES = ("engine", "command", "grid", "output", "status")
AI_MODES = ("normal", "passive")
TELEPORT_HELP = 'debug (D-104): place the player\'s active ship at "gx gy sx sy cx cy" before turn 1'
SPAWN_HELP = 'debug (D-126, repeatable): an AI captain with one ship, "<class> gx gy sx sy cx cy [fear] [respect]", before turn 1'
AI_HELP = "debug (D-125): 'passive' = AI captains never open a battle (they still defend, offer peace, escape, surrender)"


def _debug_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--teleport", default=None, help=TELEPORT_HELP)
    p.add_argument("--spawn", action="append", default=None, help=SPAWN_HELP)
    p.add_argument("--ai", choices=AI_MODES, default=None, help=AI_HELP)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="starbour", description="Master of Starbour")
    p.add_argument("--role", choices=ROLES, help="run one process; omit for the launcher")
    p.add_argument("--config", type=Path, default=None, help="controls_config.ini (default: next to the exe / repo root)")
    p.add_argument("--pub-port", type=int, default=None, help="engine PUB port (default: [bus] pub_port)")
    p.add_argument("--pull-port", type=int, default=None, help="engine PULL port (default: [bus] pull_port)")
    p.add_argument("--log-level", default="INFO")
    p.add_argument("--seed", type=int, default=None, help="world seed for a new game (overrides [world] seed)")
    p.add_argument("--new", action="store_true", help="ignore an existing save and start a new game")
    p.add_argument("--save-dir", type=Path, default=None, help="where game.json lives (default: saves/ next to the exe / repo root)")
    _debug_args(p)
    sub = p.add_subparsers(dest="cmd")
    s = sub.add_parser("sim", help="headless simulation (D-55, C2, C9, C10)")
    s.add_argument("--seed", type=int, default=None)
    s.add_argument("--turns", type=int, default=30)
    s.add_argument("--ai-player", action="store_true")
    s.add_argument("--report", type=Path, default=None)
    s.add_argument("--config", type=Path, default=None)
    s.add_argument("--save-dir", type=Path, default=None)
    s.add_argument("--bus", action="store_true")
    s.add_argument("--pub-port", type=int, default=0)
    s.add_argument("--pull-port", type=int, default=0)
    s.add_argument("--log-level", default="INFO")
    _debug_args(s)
    s.add_argument("--player-script", type=Path, default=None, help="one line per turn, blank = none, orders separated by ';' (replaces --ai-player)")
    s.add_argument("--world-index", action="store_true", help="add world_index (stars, holes, neutron stars, planets) to the report")
    return p


def _config(path: Path | None) -> GameConfig:
    return load_config(path if path is not None else paths.config_path())


def _teleport(text: str | None):
    if text is None:
        return None
    from starbour.engine.server import parse_teleport
    return parse_teleport(text)


def _spawns(texts: list[str] | None) -> list[str]:
    """Validate every --spawn string up front so a typo exits 1 with a message, not a traceback in the engine."""
    if not texts:
        return []
    from starbour.engine.server import parse_spawn
    for text in texts:
        parse_spawn(text)
    return list(texts)


def main(argv: list[str] | None = None) -> int:
    from starbour import runtime
    runtime.utf8_stdio()
    args = build_parser().parse_args(argv)
    try:
        cfg = _config(args.config)
    except ConfigError as e:
        sys.stderr.write(f"config error: {e}\n")
        return 4
    try:
        teleport = _teleport(args.teleport)
        spawns = _spawns(args.spawn)
    except ValueError as e:
        sys.stderr.write(f"{e}\n")
        return 1
    ai_passive = args.ai == "passive"
    if args.cmd == "sim":
        from starbour.engine.server import pick_seed
        from starbour.engine.sim import run_sim
        runtime.setup_logging("sim", args.log_level)
        save_dir = args.save_dir if args.save_dir is not None else paths.saves_dir()
        if args.player_script is not None and not args.player_script.is_file():
            sys.stderr.write(f"--player-script {args.player_script} not found\n")
            return 1
        return run_sim(cfg, seed=pick_seed(cfg, args.seed), turns=args.turns, ai_player=args.ai_player, report_path=args.report,
                       save_dir=save_dir, bus=args.bus, pub_port=args.pub_port, pull_port=args.pull_port, teleport=teleport,
                       script_path=args.player_script, with_world_index=args.world_index, spawns=spawns, ai_passive=ai_passive,
                       log_dir=paths.logs_dir() if args.save_dir is None else args.save_dir / "logs")   # a gate's --save-dir keeps its logs with it
    pub_port = args.pub_port if args.pub_port is not None else cfg.pub_port
    pull_port = args.pull_port if args.pull_port is not None else cfg.pull_port
    role = args.role
    save_path = (args.save_dir / "game.json") if args.save_dir is not None else paths.save_path()
    runtime.setup_logging(role or "launcher", args.log_level, to_console=role is None)
    try:
        if role is None:
            from starbour.launcher import main as launcher_main
            return launcher_main(cfg, pub_port=pub_port, pull_port=pull_port, log_level=args.log_level, seed=args.seed,
                                 new_game=args.new, save_dir=args.save_dir, teleport=args.teleport, spawns=spawns, ai=args.ai)
        if role == "engine":
            from starbour.engine.server import run_engine_process
            return run_engine_process(cfg, seed=args.seed, pub_port=pub_port, pull_port=pull_port, save_path=save_path,
                                      resume=not args.new, teleport=teleport, spawns=spawns, ai_passive=ai_passive)
        if role == "command":
            from starbour.terminals.command import main as t1
            return t1(cfg, pub_port, pull_port)
        if role == "grid":
            from starbour.terminals.grid import main as t2
            return t2(cfg, pub_port)
        if role == "output":
            from starbour.terminals.output import main as t3
            return t3(cfg, pub_port)
        if role == "status":
            from starbour.terminals.status import main as t4
            return t4(cfg, pub_port)
    except Exception:   # noqa: BLE001 - the traceback goes to logs/<role>.log and stderr
        logging.getLogger("starbour").exception("%s crashed", role or "launcher")
        raise
    return 2


if __name__ == "__main__":
    sys.exit(main())
