"""verify/smoke_test.py — gate 1: headless, no windows, random ports.

    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\smoke_test.py .            full  (≤ 90 s)
    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\smoke_test.py . --quick    hook  (≤ 15 s)
    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\smoke_test.py --selftest   prove the checkers discriminate

Checks smoke/00…12 per docs/BOOTSTRAP.md § Gate 1 and smoke/13…22 per specs/M2.md § 3,
driving only the interfaces in docs/CONTRACTS.md (C2 sim + report, C3 bus, C4 render, C6
save, C9 M2 additions).  Exit 0 pass, 1 fail, 3 HARNESS ERROR (environment, not code).
Checks a milestone has not reached print SKIP; checks whose prerequisite failed print
BLOCKED.  Output goes to verify/artifacts/smoke/.

M2 rows build their scenarios from `world_index` (C9) and the ini — never from hard-coded
coordinates — trying the seeds in SEEDS in order when a seed lacks a feature (M2 § 5).
"""
from __future__ import annotations

import argparse
import configparser
import importlib
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _checks as C  # noqa: E402

QUICK_BUDGET_S = 15.0
FULL_BUDGET_S = 90.0
SIM_BOUND_S = 20.0            # C2: 30 turns at default sizes
LONG_RUN_BOUND_S = 120.0      # smoke/12
AI_RUN_TURNS = 60             # smoke/20
QUICK_CODES = {"smoke/00", "smoke/01", "smoke/02", "smoke/03", "smoke/04", "smoke/10"}
M2_CODES = {f"smoke/{n}" for n in range(13, 23)}
SEEDS = (1234, 7, 42, 99, 2026)   # scenario seeds, tried in order (gate logic, M2 § 5)
RENDER_ROLES = ("grid", "output", "status", "command")
CONFIG_LOADERS = ("load_config", "load", "GameConfig.load", "GameConfig.from_ini", "GameConfig.from_file", "GameConfig.from_path")


def utf8_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


class ScenarioError(Exception):
    """An M2 scenario could not be built from world_index or its sim did not run — the row's FAIL."""


@dataclass
class World:
    """One probed seed: its world_index (C9), the turn-1 snapshot and every existing sector."""
    seed: int
    index: dict
    snapshot: dict
    start: tuple                 # ((gx, gy), (sx, sy)) of the player's start
    sectors: list                # [((gx, gy), (sx, sy)), …] from the snapshot's galaxy lists


@dataclass
class BusRun:
    result: Any
    log: Path
    returncode: int | None
    exited_during_drive: bool


@dataclass
class Ctx:
    root: Path
    quick: bool
    py: str
    art: Path
    milestone: str = "M1"
    cfg: configparser.ConfigParser | None = None
    values: dict = field(default_factory=dict)
    report_main: dict | None = None
    save_dir_main: Path | None = None
    turns_main: int = 30
    pkg_missing: str | None = None
    worlds: dict = field(default_factory=dict)      # seed → World | str (a cached probe failure)


# --------------------------------------------------------------------------- helpers

def pkg_missing_message(root: Path) -> str | None:
    init, main = root / "starbour" / "__init__.py", root / "starbour" / "__main__.py"
    if not init.exists():
        return f"starbour package not found at {init} — M1 not built yet (specs/M1.md)"
    if not main.exists():
        return f"starbour package has no __main__.py at {main} — `python -m starbour` cannot run (CONTRACTS C1)"
    return None


def _size(text: str) -> tuple[int, int]:
    w, h = text.lower().strip().split("x")
    return int(w), int(h)


def ini_values(cp: configparser.ConfigParser) -> dict:
    starting = cp.get("ships", "starting_class").strip().lower()
    torp, phas, shield, engine, warp = [float(x) for x in cp.get("ships", starting).split(",")]
    return {
        "food_start": cp.getint("supplies", "food_start"),
        "water_start": cp.getint("supplies", "water_start"),
        "warn_turns": cp.getint("supplies", "warn_turns"),
        "mutiny_turns_at_zero": cp.getint("supplies", "mutiny_turns_at_zero"),
        "mutiny_ship_class": cp.get("supplies", "mutiny_ship_class").strip(),
        "mutiny_supplies": cp.getint("supplies", "mutiny_supplies"),
        "mutiny_fear_delta": cp.getint("supplies", "mutiny_fear_delta"),
        "glyph_set": cp.get("ui", "glyph_set").strip().lower(),
        "pub_port": cp.getint("bus", "pub_port"),
        "pull_port": cp.getint("bus", "pull_port"),
        # M2 (specs/M2.md § 1, CFG-16…23, CFG-27, D-89)
        "m2": {
            "k_g": cp.getfloat("hazards", "k_g"), "falloff": cp.getfloat("hazards", "falloff"),
            "pull_turns_scale": cp.getfloat("hazards", "pull_turns_scale"),
            "drain": {"low": cp.getfloat("hazards", "drain_low"), "medium": cp.getfloat("hazards", "drain_medium"),
                      "high": cp.getfloat("hazards", "drain_high")},
            "shield_recharge": cp.getfloat("battle", "shield_recharge"),
            "warp_base": cp.getfloat("movement", "warp_curve_base"), "warp_exp": cp.getfloat("movement", "warp_curve_exponent"),
            "blackhole_mass": cp.getfloat("stellar", "blackhole_mass"), "neutron_mass": cp.getfloat("stellar", "neutron_mass"),
            "universe_size": _size(cp.get("world", "universe_size")), "galaxy_size": _size(cp.get("world", "galaxy_size")),
            "sector_size": _size(cp.get("world", "sector_size")),
            "starting_class": starting,
            "ship": {"torpedoes": torp, "phasers": phas, "shield_max": shield, "engine": engine, "warp": warp},
        },
    }


def ensure_values(ctx: Ctx) -> dict:
    """ctx.values is filled by smoke/00; a --select run without it reads the ini here."""
    if not ctx.values:
        cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
        cp.read(ctx.root / "controls_config.ini", encoding="utf-8")
        ctx.cfg = cp
        ctx.values = ini_values(cp)
    return ctx.values


def require_pkg(ctx: Ctx) -> None:
    msg = pkg_missing_message(ctx.root)
    if msg:
        raise C.Blocked(msg)


def at_least(ctx: Ctx, milestone: str) -> bool:
    return C.milestone_index(ctx.milestone) >= C.milestone_index(milestone)


def run_sim(ctx: Ctx, *, seed: int, turns: int, report: Path, save_dir: Path, ai_player: bool = True,
            config: Path | None = None, timeout: float, script: Path | None = None, teleport: str | None = None,
            world_index: bool = False) -> C.CmdResult:
    if save_dir.exists():
        shutil.rmtree(save_dir, ignore_errors=True)
    save_dir.mkdir(parents=True, exist_ok=True)
    if report.exists():
        report.unlink()
    args = [ctx.py, "-m", "starbour", "sim", "--seed", str(seed), "--turns", str(turns),
            "--report", str(report), "--save-dir", str(save_dir)]
    if ai_player:
        args.append("--ai-player")
    if config is not None:
        args += ["--config", str(config)]
    if script is not None:
        args += ["--player-script", str(script)]
    if teleport is not None:
        args += ["--teleport", teleport]
    if world_index:
        args.append("--world-index")
    return C.run_cmd(args, ctx.root, timeout)


def sim_problems(res: C.CmdResult, what: str, timeout: float) -> list[str]:
    if res.timed_out:
        return [f"{what} did not finish within {timeout:.0f} s and was killed — the turn loop may never end"]
    rc = res.returncode
    if rc == 4:
        return [f"{what}: config validation failed (exit 4): {res.tail(3)} (CFG-*, D-53 — fix controls_config.ini or starbour/config.py)"]
    if rc != 0:
        return [f"{what} exited {rc}: {res.tail(12)}"]
    if "Traceback" in res.stderr:
        return [f"{what} printed a traceback despite exit 0: {res.tail(8)}"]
    return []


def need_main_report(ctx: Ctx) -> dict:
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    if ctx.report_main is None:
        raise C.Blocked("smoke/02 produced no usable report")
    return ctx.report_main


def snapshot_from_save(doc: dict) -> dict:
    return doc["snapshot"] if isinstance(doc.get("snapshot"), dict) else {k: v for k, v in doc.items() if k != "rng_state"}


def write_lines(path: Path, lines: list[str]) -> Path:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_ini(ctx: Ctx, name: str, changes: dict[tuple[str, str], str]) -> Path:
    """A temp copy of controls_config.ini with some keys changed (smoke/08, 15, 19)."""
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cp.read(ctx.root / "controls_config.ini", encoding="utf-8")
    for (section, key), value in changes.items():
        cp.set(section, key, value)
    path = ctx.art / name
    with open(path, "w", encoding="utf-8") as f:
        cp.write(f)
    return path


def bus_session(ctx: Ctx, label: str, *, seed: int, turns: int, driver: Callable[[int, int], Any], teleport: str | None = None,
                config: Path | None = None, save_dir: Path | None = None, report: Path | None = None) -> BusRun:
    """Start `sim --bus` on free ports, run `driver(pub, pull)`, then shut the engine down."""
    pub, pull = C.free_ports(2)
    save_dir = save_dir or ctx.art / f"saves_{label}"
    report = report or ctx.art / f"report_{label}.json"
    log = ctx.art / f"{label}_engine.log"
    if save_dir.exists():
        shutil.rmtree(save_dir, ignore_errors=True)
    save_dir.mkdir(parents=True, exist_ok=True)
    if report.exists():
        report.unlink()
    args = [ctx.py, "-m", "starbour", "sim", "--seed", str(seed), "--turns", str(turns), "--bus", "--pub-port", str(pub),
            "--pull-port", str(pull), "--report", str(report), "--save-dir", str(save_dir)]
    if teleport is not None:
        args += ["--teleport", teleport]
    if config is not None:
        args += ["--config", str(config)]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(args, cwd=str(ctx.root), env=env, stdout=lf, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            result = driver(pub, pull)
            exited = proc.poll() is not None
            C.wait_until(lambda: proc.poll() is not None, 3.0)
            if proc.poll() is None:
                try:
                    cl = C.BusClient(pub, pull)
                    cl.send("system.shutdown", {"reason": "gate finished"})
                    cl.close()
                except Exception:  # noqa: BLE001
                    pass
                C.wait_until(lambda: proc.poll() is not None, 5.0)
        finally:
            if proc.poll() is None:
                C.kill_tree(proc.pid)
    return BusRun(result, log, proc.returncode, exited)


def log_tail(path: Path, n: int = 12) -> str:
    if not path.exists():
        return ""
    return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:])


def with_log(problems: list[str], run: BusRun) -> list[str]:
    if problems:
        tail = log_tail(run.log)
        if tail.strip():
            problems.append("engine log tail:\n" + textwrap.indent(tail, " " * 10))
    return problems


# --------------------------------------------------------------------------- M2 world probing (C9 --world-index)

def probe_world(ctx: Ctx, seed: int) -> World:
    cached = ctx.worlds.get(seed)
    if isinstance(cached, World):
        return cached
    if isinstance(cached, str):
        raise ScenarioError(cached)

    def fail(msg: str) -> ScenarioError:
        ctx.worlds[seed] = msg
        return ScenarioError(msg)

    report, save_dir = ctx.art / f"world_{seed}.json", ctx.art / f"saves_world_{seed}"
    what = f"sim --seed {seed} --turns 1 --ai-player --world-index"
    res = run_sim(ctx, seed=seed, turns=1, report=report, save_dir=save_dir, world_index=True, timeout=SIM_BOUND_S * 2)
    problems = sim_problems(res, what, SIM_BOUND_S * 2)
    if problems:
        raise fail(problems[0] + " — every M2 row needs --world-index to pick its cells (C9, D-104)")
    doc, err = C.load_json(report)
    if doc is None:
        raise fail(f"{what}: no report written: {err} (C2)")
    index = doc.get("world_index")
    errs = C.world_index_shape_errors(index)
    if errs:
        raise fail(f"{what}: " + "; ".join(errs[:4]))
    sdoc, err = C.load_json(save_dir / "game.json")
    if sdoc is None:
        raise fail(f"{what}: no save file to read the galaxy layout from ({err}; C6)")
    snap = snapshot_from_save(sdoc)
    se = C.snapshot_shape_errors(snap)
    if se:
        raise fail(f"{what}: saved snapshot: " + "; ".join(se[:3]))
    sectors = C.existing_sectors(snap)
    if not sectors:
        raise fail(f"{what}: the snapshot lists no sectors (C3 galaxies[*].sectors) — warp destinations cannot be picked")
    start = (tuple(int(v) for v in index["player_start"][0]), tuple(int(v) for v in index["player_start"][1]))
    world = World(seed, index, snap, start, sectors)
    ctx.worlds[seed] = world
    return world


def find_world(ctx: Ctx, what: str, select: Callable[[World], Any]) -> tuple[World, Any]:
    """The first seed (SEEDS order) whose world offers `what`; `select` returns the pick or None."""
    tried: list[int] = []
    for seed in SEEDS:
        world = probe_world(ctx, seed)
        got = select(world)
        if got is not None:
            return world, got
        tried.append(seed)
    raise ScenarioError(f"none of the gate's seeds {tried} offers {what} within world_index (≤ 200 nearest entries per list) — "
                        f"the seed list is exhausted; this is gate logic, report it (M2 § 5)")


def run_scenario(ctx: Ctx, name: str, scn: dict, *, seed: int, config: Path | None = None) -> tuple[dict, bool]:
    """Run one plan's script (and teleport) through the sim; return (report, save_present)."""
    script = write_lines(ctx.art / f"script_{name}.txt", scn["script"])
    report, save_dir = ctx.art / f"report_{name}.json", ctx.art / f"saves_{name}"
    what = f"sim --seed {seed} --turns {scn['turns']} --player-script {script.name}"
    if scn.get("teleport"):
        what += f" --teleport \"{scn['teleport']}\""
    if config is not None:
        what += f" --config {config.name}"
    res = run_sim(ctx, seed=seed, turns=scn["turns"], report=report, save_dir=save_dir, ai_player=False, config=config,
                  script=script, teleport=scn.get("teleport"), timeout=SIM_BOUND_S * 2)
    problems = sim_problems(res, what, SIM_BOUND_S * 2)
    doc, err = C.load_json(report)
    if doc is None:
        problems.append(f"no report written: {err} (C2)")
    if problems:
        raise ScenarioError(f"{scn['plan']['what']}: " + "; ".join(problems))
    return doc, (save_dir / "game.json").exists()


def m2(ctx: Ctx) -> dict:
    return ensure_values(ctx)["m2"]


def star_pick(ctx: Ctx) -> Callable[[World], Any]:
    v = m2(ctx)
    return lambda w: C.pick_star(w.index, k_g=v["k_g"], falloff=v["falloff"], scale=v["pull_turns_scale"], sector_size=v["sector_size"])


def hole_pick(ctx: Ctx, *, wormhole: bool | None, distance: int, path: tuple[int, ...] = (), diagonal_first: bool = False) -> Callable[[World], Any]:
    """A black hole (plain / wormhole / either) with a free cell at `distance` (and a free path)."""
    v = m2(ctx)

    def select(w: World) -> Any:
        for h in C.pick_holes(w.index, wormhole=wormhole):
            if wormhole:
                wt = h.get("wormhole_to")
                if not (isinstance(wt, dict) and isinstance(wt.get("galaxy"), list) and isinstance(wt.get("sector"), list)):
                    continue
            orders = (True, False) if diagonal_first else (False, True)
            for diag in orders:
                got = C.cell_at_distance(w.index, h, distance, v["sector_size"], diagonal=diag, path=path)
                if got:
                    return (h, got[0], got[1])
        return None
    return select


def render_probe_run(ctx: Ctx, name: str, inputs: dict[str, list], *, styled: bool = False, view: dict | None = None) -> dict:
    spec = {"root": str(ctx.root), "config_path": str(ctx.root / "controls_config.ini"), "width": 120,
            "view": view or {"level": "follow"}, "inputs": inputs, "styled": styled}
    in_path, out_path = ctx.art / f"render_in_{name}.json", ctx.art / f"render_out_{name}.json"
    in_path.write_text(json.dumps(spec), encoding="utf-8")
    if out_path.exists():
        out_path.unlink()
    res = C.run_cmd([ctx.py, str(HERE / "smoke_test.py"), "--_render-probe", str(in_path), str(out_path)], ctx.root, 60.0)
    if res.returncode != 0 or not out_path.exists():
        raise ScenarioError(f"render probe subprocess failed (exit {res.returncode}): {res.tail(10)}")
    return json.loads(out_path.read_text(encoding="utf-8"))


def probe_errors(probe: dict) -> list[str]:
    errs = []
    if probe.get("config_loader") is None:
        errs.append("could not load GameConfig: none of " + ", ".join(probe.get("loader_candidates", [])) + " exists in starbour.config (C4)")
    for mod, err in (probe.get("import_errors") or {}).items():
        errs.append(f"import {mod} failed: {err.strip().splitlines()[-1] if err.strip() else err} (C4)")
    for role, err in (probe.get("errors") or {}).items():
        errs.append(f"render_{role}.render() raised: {err.strip().splitlines()[-1] if err.strip() else err}")
    return errs


# --------------------------------------------------------------------------- checks smoke/00…12

def c00(ctx: Ctx) -> list[str]:
    """smoke/00 — harness sanity: interpreter, deps, docs current, ini parses (CFG-*, D-53)."""
    exe = Path(sys.executable).resolve()
    if "windowsapps" in str(exe).lower():
        raise C.HarnessError(f"the gate is running under the Microsoft Store python stub ({exe}); use {C.EXPECTED_PY}")
    allowed = {os.path.normcase(str(p.resolve())) for p in (C.EXPECTED_PY, ctx.root / "MOS" / "Scripts" / "python.exe") if p.exists()}
    if os.path.normcase(str(exe)) not in allowed:
        raise C.HarnessError(f"interpreter is {exe}, expected the MOS venv {C.EXPECTED_PY} — run the gate with the absolute path")
    missing = []
    for mod in ("rich", "prompt_toolkit", "zmq", "pytest", "psutil", "openpyxl"):
        try:
            importlib.import_module(mod)
        except Exception:  # noqa: BLE001
            missing.append(mod)
    try:
        importlib.metadata.version("pytest-timeout")
    except importlib.metadata.PackageNotFoundError:
        missing.append("pytest-timeout")
    if missing:
        raise C.HarnessError(f"missing in the MOS venv: {missing} — pip install -e .[dev] with {C.EXPECTED_PY} (docs/SETUP.md § 1)")
    ctx.milestone = C.read_milestone(ctx.root)
    C.milestone_index(ctx.milestone)
    tool = ctx.root / "tools" / "blueprint_to_design.py"
    if not tool.exists():
        raise C.HarnessError(f"{tool} not found")
    res = C.run_cmd([ctx.py, str(tool), "--check"], ctx.root, 90)
    if res.returncode != 0:
        raise C.HarnessError(f"generated docs are not current with the workbook (exit {res.returncode}): {res.tail(4)} — "
                             f"run tools/blueprint_to_design.py and commit (D-74)")
    problems: list[str] = []
    ini = ctx.root / "controls_config.ini"
    if not ini.exists():
        return [f"{ini} not found — every tunable lives there (CFG-*, D-49)"]
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    try:
        cp.read(ini, encoding="utf-8")
    except configparser.Error as e:
        return [f"controls_config.ini does not parse: {e} (CFG-*)"]
    ctx.cfg = cp
    manifest = ctx.root / "docs" / "config_keys.json"
    if not manifest.exists():
        raise C.HarnessError(f"{manifest} not found — run tools/blueprint_to_design.py (D-74)")
    rows = json.loads(manifest.read_text(encoding="utf-8"))["keys"]
    for row in rows:
        sec = row["section"]
        if not cp.has_section(sec):
            problems.append(f"[{sec}] section missing from controls_config.ini ({row['id']})")
            continue
        if sec == "elements":
            continue
        for key in row["keys"]:
            if not cp.has_option(sec, key):
                problems.append(f"[{sec}] {key} missing from controls_config.ini ({row['id']}) — a missing key must fail loudly, never default (D-53)")
    try:
        ctx.values = ini_values(cp)
    except (configparser.Error, ValueError) as e:
        problems.append(f"the gate cannot read the [supplies]/[ui]/[bus]/[hazards]/[battle]/[movement]/[stellar]/[world]/[ships] keys it needs: {e}")
    return problems


def c01(ctx: Ctx) -> list[str]:
    """smoke/01 — the unit tests pass."""
    args = [ctx.py, "-m", "pytest", "tests", "-q", "-x", "--timeout", "10", "-p", "no:cacheprovider"]
    if ctx.quick:
        args += ["-m", "not slow"]
    limit = 40.0 if ctx.quick else 150.0
    res = C.run_cmd(args, ctx.root, limit)
    (ctx.art / "pytest.log").write_text(res.stdout + res.stderr, encoding="utf-8")
    if res.timed_out:
        return [f"pytest did not finish within {limit:.0f} s — a test hangs (pytest-timeout is on; mark socket/process tests @pytest.mark.slow)"]
    if res.returncode == 5:
        return ["pytest collected no tests (exit 5) — tests/ must hold the milestone's tests (specs/M1.md § 3)"]
    if res.returncode != 0:
        return [f"pytest exited {res.returncode}:\n{textwrap.indent(res.tail(30), ' ' * 10)}"]
    return []


def c02(ctx: Ctx) -> list[str]:
    """smoke/02 — turns advance (OV-02, TP-01…08); from M2 an ended game passes (D-106)."""
    msg = pkg_missing_message(ctx.root)
    if msg:
        ctx.pkg_missing = msg
        return [msg]
    turns = 10 if ctx.quick else 30
    ctx.turns_main = turns
    report, save_dir = ctx.art / f"report{turns}.json", ctx.art / f"saves{turns}"
    res = run_sim(ctx, seed=1234, turns=turns, report=report, save_dir=save_dir, timeout=SIM_BOUND_S * 2)
    problems = sim_problems(res, f"sim --seed 1234 --turns {turns} --ai-player", SIM_BOUND_S * 2)
    if not res.timed_out and res.seconds > SIM_BOUND_S:
        problems.append(f"sim --turns {turns} took {res.seconds:.1f} s, bound {SIM_BOUND_S:.0f} s (C2) — profile before shrinking any size")
    doc, err = C.load_json(report)
    if doc is None:
        return problems + [f"no report written: {err} (C2 --report)"]
    shape = C.report_shape_errors(doc)
    if shape:
        return problems + shape[:8]
    problems += C.check_turns_advance(doc, turns, allow_end=at_least(ctx, "M2"))
    if not problems:
        ctx.report_main, ctx.save_dir_main = doc, save_dir
        if doc.get("outcome") != "running":
            print(f"         note: the main run ended by {doc.get('outcome')} at turn {doc.get('turns_completed')} (allowed from M2, D-106)")
    return problems


def c03(ctx: Ctx) -> list[str]:
    """smoke/03 — the AI acts (OV-10, SP-06)."""
    return C.check_ai_acts(need_main_report(ctx))


def c04(ctx: Ctx) -> list[str]:
    """smoke/04 — the snapshot changes every turn (BUS-07)."""
    return C.check_hashes_differ(need_main_report(ctx))


def c05(ctx: Ctx) -> list[str]:
    """smoke/05 — upkeep wired to config (SUP-01, SUP-02, CFG-45/46); active while MILESTONE < M4 (D-106)."""
    v = ctx.values
    return C.check_upkeep(need_main_report(ctx), v["food_start"], v["water_start"], v["warn_turns"])


def c06(ctx: Ctx) -> list[str]:
    """smoke/06 — the mutiny fires (SUP-03, SUP-04b, SUP-04c, CFG-47/48)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    v = ctx.values
    report, save_dir = ctx.art / "report40.json", ctx.art / "saves40"
    res = run_sim(ctx, seed=1234, turns=40, report=report, save_dir=save_dir, timeout=SIM_BOUND_S * 2)
    problems = sim_problems(res, "sim --seed 1234 --turns 40 --ai-player", SIM_BOUND_S * 2)
    doc, err = C.load_json(report)
    if doc is None:
        return problems + [f"no report written: {err}"]
    shape = C.report_shape_errors(doc)
    if shape:
        return problems + shape[:6]
    return problems + C.check_mutiny(doc, v["food_start"], v["water_start"], v["mutiny_turns_at_zero"],
                                      v["mutiny_ship_class"], v["mutiny_supplies"], v["mutiny_fear_delta"])


def c07(ctx: Ctx) -> list[str]:
    """smoke/07 — determinism (D-67)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    problems: list[str] = []
    seqs: list[list[str]] = []
    for tag, seed in (("a", 1234), ("b", 1234), ("c", 1235)):
        rep = ctx.art / f"det_{tag}.json"
        res = run_sim(ctx, seed=seed, turns=10, report=rep, save_dir=ctx.art / f"saves_det_{tag}", timeout=SIM_BOUND_S * 2)
        problems += sim_problems(res, f"sim --seed {seed} --turns 10 (run {tag})", SIM_BOUND_S * 2)
        doc, err = C.load_json(rep)
        seqs.append([t.get("snapshot_hash") for t in ((doc or {}).get("per_turn") or [])])
    if problems:
        return problems
    return C.check_determinism(*seqs)


def c08(ctx: Ctx) -> list[str]:
    """smoke/08 — a changed ini key changes the game (CFG-45, D-49)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    tmp_ini = write_ini(ctx, "cfg12.ini", {("supplies", "food_start"): "12"})
    rep = ctx.art / "report_cfg12.json"
    res = run_sim(ctx, seed=1234, turns=2, report=rep, save_dir=ctx.art / "saves_cfg12", config=tmp_ini, timeout=SIM_BOUND_S * 2)
    problems = sim_problems(res, f"sim --config {tmp_ini.name} --turns 2", SIM_BOUND_S * 2)
    doc, err = C.load_json(rep)
    if doc is None:
        return problems + [f"no report written: {err}"]
    return problems + C.check_config_honoured(doc, 11, 12, tmp_ini)


def c09(ctx: Ctx) -> list[str]:
    """smoke/09 — bus wiring on random ports (BUS-03/04, MSG-01…07)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    pub, pull = C.free_ports(2)
    report, save_dir, log = ctx.art / "report_bus.json", ctx.art / "saves_bus", ctx.art / "bus_engine.log"
    if save_dir.exists():
        shutil.rmtree(save_dir, ignore_errors=True)
    save_dir.mkdir(parents=True, exist_ok=True)
    args = [ctx.py, "-m", "starbour", "sim", "--seed", "1234", "--turns", "3", "--bus", "--pub-port", str(pub), "--pull-port", str(pull),
            "--report", str(report), "--save-dir", str(save_dir)]
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    problems: list[str] = []
    with open(log, "w", encoding="utf-8") as lf:
        proc = subprocess.Popen(args, cwd=str(ctx.root), env=env, stdout=lf, stderr=subprocess.STDOUT,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            obs = C.drive_engine(pub, pull, turns=3, hello_timeout=15.0, turn_timeout=10.0, reply_timeout=5.0, orders=True)
            problems += C.check_bus_observations(obs, turns_expected=3, need_orders=True)
            C.wait_until(lambda: proc.poll() is not None, 10.0)
            if proc.poll() is None:
                try:
                    cl = C.BusClient(pub, pull)
                    cl.send("system.shutdown", {"reason": "gate finished"})
                    cl.close()
                except Exception:  # noqa: BLE001
                    pass
                C.wait_until(lambda: proc.poll() is not None, 5.0)
            if proc.poll() is None:
                C.kill_tree(proc.pid)
                problems.append("the --bus engine did not exit after its 3 turns (C2: exit 0 when turns_completed == turns_requested) "
                                "nor within 5 s of system.shutdown (MSG-08)")
            elif proc.returncode != 0:
                problems.append(f"the --bus engine exited {proc.returncode}")
            if obs.hello is None and proc.poll() is not None:
                problems.insert(0, f"the engine process exited {proc.returncode} before the gate saw state.hello")
        finally:
            if proc.poll() is None:
                C.kill_tree(proc.pid)
    if problems:
        tail = "\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-12:])
        if tail.strip():
            problems.append("engine log tail:\n" + textwrap.indent(tail, " " * 10))
    return problems


def c10(ctx: Ctx) -> list[str]:
    """smoke/10 — render() of each terminal is fresh and faithful (C4, BUS-05, GRD-06)."""
    report = need_main_report(ctx)
    terminals = ctx.root / "starbour" / "terminals"
    sources: dict[str, str] = {}
    problems: list[str] = []
    for role in RENDER_ROLES:
        p = terminals / f"render_{role}.py"
        if p.exists():
            sources[p.name] = p.read_text(encoding="utf-8", errors="replace")
        else:
            problems.append(f"{p.relative_to(ctx.root)} not found — each terminal needs a pure render(snapshot, view, cfg, width) module (C4)")
    problems += C.check_render_sources(sources)
    if not sources:
        return problems
    save = ctx.save_dir_main / "game.json"
    doc, err = C.load_json(save)
    if doc is None:
        hint = (f" — the main run ended by game over at turn {report.get('turns_completed')}, which deletes the save (OV-09c); "
                f"an AI player dying from the safe start within {ctx.turns_main} turns means the AI is not hazard-aware (D-103)"
                if report.get("outcome") == "game_over" else "")
        raise C.Blocked(f"no save file to take a snapshot from ({err}){hint}; smoke/11 reports the autosave")
    snap = snapshot_from_save(doc)
    se = C.snapshot_shape_errors(snap)
    if se:
        return problems + ["saved snapshot: " + "; ".join(se[:4])]
    bx, mx = C.make_successor(snap, "x")
    by, my = C.make_successor(snap, "y")
    events_a = [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "gate probe alpha: a ship moved", "ids": ["MV-01"]},
                {"phase": "TP-06", "type": "upkeep", "severity": "info", "text": "gate probe beta: upkeep applied", "ids": ["SUP-01"]}]
    events_b = [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "gate probe gamma: another move", "ids": ["MV-01"]},
                {"phase": "TP-06", "type": "upkeep", "severity": "warning", "text": "gate probe delta: supplies low", "ids": ["SUP-02"]}]
    spec = {
        "root": str(ctx.root), "config_path": str(ctx.root / "controls_config.ini"), "width": 120,
        "view": snap.get("view") or {"level": "follow"},
        "inputs": {
            "grid": [snap, bx, by],
            "status": [snap, bx],
            "command": [snap, bx],
            "output": [{"turn_no": snap["turn_no"], "snapshot": snap, "events": events_a},
                       {"turn_no": bx["turn_no"], "snapshot": bx, "events": events_b}],
        },
    }
    in_path, out_path = ctx.art / "render_in.json", ctx.art / "render_out.json"
    in_path.write_text(json.dumps(spec), encoding="utf-8")
    if out_path.exists():
        out_path.unlink()
    res = C.run_cmd([ctx.py, str(HERE / "smoke_test.py"), "--_render-probe", str(in_path), str(out_path)], ctx.root, 60.0)
    if res.returncode != 0 or not out_path.exists():
        return problems + [f"render probe subprocess failed (exit {res.returncode}): {res.tail(10)}"]
    probe = json.loads(out_path.read_text(encoding="utf-8"))
    cap = snap["captains"][snap["player_id"]]
    base = {"turn_no": snap["turn_no"], "food": cap.get("food"), "water": cap.get("water"), "fear": cap.get("fear"), "respect": cap.get("respect")}
    glyph = C.ACTIVE_SHIP_GLYPH.get(ctx.values.get("glyph_set", "unicode"), "▲")
    problems += C.check_render_probe(probe, glyph, mx, my, base, events_a, events_b)
    if problems and probe.get("renders", {}).get("grid"):
        head = "\n".join(probe["renders"]["grid"][0].splitlines()[:6])
        problems.append("first lines of the grid render:\n" + textwrap.indent(head, " " * 10))
    return problems


def c11(ctx: Ctx) -> list[str]:
    """smoke/11 — autosave exists and round-trips (OV-12, C6)."""
    report = need_main_report(ctx)
    save = ctx.save_dir_main / "game.json"
    if not save.exists():
        hint = (f" — the run ended by game over at turn {report.get('turns_completed')}, which deletes the save (OV-09c); an AI player dying "
                f"from the safe start within {ctx.turns_main} turns means the AI is not hazard-aware (D-103)"
                if report.get("outcome") == "game_over" else "")
        return [f"{save} does not exist after the run — autosave at TP-08 every turn into --save-dir (OV-12, C6){hint}"]
    doc, err = C.load_json(save)
    if doc is None:
        return [f"{err} (C6)"]
    final_hash = report["per_turn"][-1]["snapshot_hash"]
    problems = C.check_save_document(doc, final_hash)
    code = textwrap.dedent("""
        import sys, json, hashlib
        sys.path.insert(0, sys.argv[1])
        from starbour.engine import save as S
        st = S.load(sys.argv[2])
        h = None
        fn = getattr(st, "snapshot_hash", None)
        if callable(fn):
            h = fn()
        if h is None:
            try:
                from starbour.engine import state as ST
                if callable(getattr(ST, "snapshot_hash", None)):
                    h = ST.snapshot_hash(st)
            except Exception:
                pass
        if h is None:
            snap = st.snapshot() if callable(getattr(st, "snapshot", None)) else None
            if snap is not None:
                h = hashlib.sha1(json.dumps(snap, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        print("HASH", h or "NO_ACCESSOR")
    """)
    res = C.run_cmd([ctx.py, "-c", code, str(ctx.root), str(save)], ctx.root, 30.0)
    if res.returncode != 0:
        problems.append(f"starbour.engine.save.load({save.name}) raised: {res.tail(6)} (C6)")
    else:
        m = re.search(r"HASH (\S+)", res.stdout)
        h = m.group(1) if m else "NO_ACCESSOR"
        if h == "NO_ACCESSOR":
            problems.append("the gate cannot read the loaded state's hash: expose state.snapshot_hash(), "
                            "starbour.engine.state.snapshot_hash(state) or state.snapshot() (C6: load() returns a state whose snapshot hash equals the saved one)")
        elif h != final_hash:
            problems.append(f"save.load() round trip hash {h[:8]}… ≠ the report's final snapshot_hash {final_hash[:8]}… (C6)")
    return problems


def c12(ctx: Ctx) -> list[str]:
    """smoke/12 — a long AI-only run is robust (D-69, Chat A15); from M2 final.hazard_deaths is reported (C9)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    report = ctx.art / "report500.json"
    res = run_sim(ctx, seed=7, turns=500, report=report, save_dir=ctx.art / "saves500", timeout=LONG_RUN_BOUND_S + 15)
    if res.timed_out:
        return [f"sim --seed 7 --turns 500 did not finish within {LONG_RUN_BOUND_S + 15:.0f} s and was killed (bound {LONG_RUN_BOUND_S:.0f} s)"]
    doc, _ = C.load_json(report)
    m2_ = at_least(ctx, "M2")
    problems = C.check_long_run(doc, res.returncode, res.seconds, LONG_RUN_BOUND_S, res.stderr, 500, expect_hazard_deaths=m2_)
    if not problems and m2_ and isinstance(doc, dict):
        print(f"         500 turns, outcome {doc.get('outcome')}, turns_completed {doc.get('turns_completed')}, "
              f"final.hazard_deaths {C._get(doc, 'final', 'hazard_deaths')}, final.ships {C._get(doc, 'final', 'ships')}")
    return problems


# --------------------------------------------------------------------------- checks smoke/13…22 (specs/M2.md § 3)

def c13(ctx: Ctx) -> list[str]:
    """smoke/13 — persistent moves, stop, replace (CMD-06, CMD-21, OV-02b, D-98)."""
    require_pkg(ctx)
    v = m2(ctx)
    step = int(math.floor(v["ship"]["engine"]))
    distance = 10

    def select(w: World) -> Any:
        (gx, gy), (sx, sy), (cx, cy) = w.index["player_start"]
        blocked = C.terrain_cells(w.index, (gx, gy), (sx, sy))
        for d in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            landings = [(cx + d[0] * min(distance, t * step), cy + d[1] * min(distance, t * step)) for t in range(1, math.ceil(distance / step) + 1)]
            if not all(C.in_sector(c, v["sector_size"]) and c not in blocked for c in landings):
                continue
            for perp in (((1, 0), (-1, 0)) if d[0] == 0 else ((0, 1), (0, -1))):
                c2 = (cx + d[0] * step + perp[0] * 2, cy + d[1] * step + perp[1] * 2)
                if C.in_sector(c2, v["sector_size"]) and c2 not in blocked:
                    return d, perp
        return None
    try:
        w, (direction, perp) = find_world(ctx, f"a clear {distance}-cell path from the player's start", select)
        problems: list[str] = []
        for variant in ("persist", "stop", "replace"):
            scn = C.plan_persistent_move(w.index["player_start"], direction, v["ship"]["engine"], variant=variant,
                                         distance=distance, perpendicular=perp)
            report, _ = run_scenario(ctx, f"move_{variant}", scn, seed=w.seed)
            problems += C.check_scenario(report, scn["plan"])
        return problems
    except ScenarioError as e:
        return [str(e)]


def c14(ctx: Ctx) -> list[str]:
    """smoke/14 — orders are visible: PNL-07 on T4 and T1 'orders' (PNL-07, D-99)."""
    require_pkg(ctx)
    v = m2(ctx)
    step = int(math.floor(v["ship"]["engine"]))
    distance = 10
    try:
        w, _ = find_world(ctx, "a world", lambda w: True)
        variants = C.hazard_render_snapshots(w.snapshot)
        orders = variants["orders"]["ships"][variants["ship_id"]]["orders"]
        probe = render_probe_run(ctx, "orders", {"status": [variants["orders"]]}, view=w.snapshot.get("view") or {"level": "follow"})
        problems = probe_errors(probe)
        texts = (probe.get("renders") or {}).get("status") or []
        if texts:
            problems += C.check_orders_render(texts[0], orders, step)
            if problems:
                problems.append("status render head:\n" + textwrap.indent("\n".join(texts[0].splitlines()[:30]), " " * 10))
        elif not problems:
            problems.append("render_status: no rendered text captured (C4)")
        # T1 'orders' over the bus, before and after a turn (D-92: immediate commands are answered with order.accepted + text)
        (gx, gy), (sx, sy), (cx, cy) = w.index["player_start"]
        blocked = C.terrain_cells(w.index, (gx, gy), (sx, sy))
        direction = next((d for d in ((0, 1), (0, -1), (1, 0), (-1, 0))
                          if all(C.in_sector((cx + d[0] * min(distance, t * step), cy + d[1] * min(distance, t * step)), v["sector_size"])
                                 and (cx + d[0] * min(distance, t * step), cy + d[1] * min(distance, t * step)) not in blocked
                                 for t in range(1, math.ceil(distance / step) + 1))), None)
        if direction is None:
            raise ScenarioError(f"no clear {distance}-cell path from the player's start {cx},{cy} for seed {w.seed} (gate logic, M2 § 5)")
        move_args = [C.signed(direction[0] * distance), C.signed(direction[1] * distance)]
        remaining = [direction[0] * (distance - step), direction[1] * (distance - step)]
        run = bus_session(ctx, "orders", seed=w.seed, turns=3,
                          driver=lambda pub, pull: C.drive_orders_listing(pub, pull, move_args=move_args))
        problems += with_log(C.check_orders_listing(run.result, move_args, remaining, step), run)
        return problems
    except ScenarioError as e:
        return [str(e)]


def c15(ctx: Ctx) -> list[str]:
    """smoke/15 — star fatal: countdown, drain, game over, save deleted (HZ-02, HZ-03, HZ-09, OV-09b, OV-09c, D-100)."""
    require_pkg(ctx)
    v = m2(ctx)
    try:
        w, (star, _) = find_world(ctx, "a medium-bucket star with a free neighbouring cell", star_pick(ctx))
        bucket = str(star.get("bucket", "")).lower()
        if bucket not in v["drain"]:
            raise ScenarioError(f"star bucket {bucket!r} has no [hazards] drain_{bucket} key (CFG-21)")
        common = dict(k_g=v["k_g"], falloff=v["falloff"], drain=v["drain"][bucket], shield_max=v["ship"]["shield_max"])
        scn = C.plan_star_fatal(star, scale=v["pull_turns_scale"], **common)
        report, save_present = run_scenario(ctx, "star_fatal", scn, seed=w.seed)
        problems = C.check_scenario(report, scn["plan"], save_present=save_present)
        scale2 = 10.0 if abs(v["pull_turns_scale"] - 10.0) > 1e-9 else 14.0
        ini2 = write_ini(ctx, "scale_alt.ini", {("hazards", "pull_turns_scale"): f"{scale2:g}"})
        scn2 = C.plan_star_fatal(star, scale=scale2, label=f" with pull_turns_scale = {scale2:g}", **common)
        report2, sp2 = run_scenario(ctx, "star_fatal_scale", scn2, seed=w.seed, config=ini2)
        problems += C.check_scenario(report2, scn2["plan"], save_present=sp2)
        if not problems:
            n1, n2 = scn["plan"]["end"]["turn"], scn2["plan"]["end"]["turn"]
            print(f"         seed {w.seed}: star at {star['cell']} in {star['galaxy']}/{star['sector']} mass {float(star['mass']):.3f}; "
                  f"destroyed at turn {n1} (scale {v['pull_turns_scale']:g}) and {n2} (scale {scale2:g}); save deleted")
        return problems
    except ScenarioError as e:
        return [str(e)]


def c16(ctx: Ctx) -> list[str]:
    """smoke/16 — countdown reset on leaving, shield recharge outside (HZ-08, CM-05)."""
    require_pkg(ctx)
    v = m2(ctx)
    try:
        w, (star, exit_) = find_world(ctx, "a medium-bucket star with a free neighbouring cell", star_pick(ctx))
        bucket = str(star.get("bucket", "")).lower()
        if bucket not in v["drain"]:
            raise ScenarioError(f"star bucket {bucket!r} has no [hazards] drain_{bucket} key (CFG-21)")
        scn = C.plan_countdown_reset(star, exit_, k_g=v["k_g"], falloff=v["falloff"], scale=v["pull_turns_scale"], drain=v["drain"][bucket],
                                     recharge=v["shield_recharge"], shield_max=v["ship"]["shield_max"])
        report, _ = run_scenario(ctx, "countdown_reset", scn, seed=w.seed)
        problems = C.check_scenario(report, scn["plan"])
        if not problems:
            print(f"         seed {w.seed}: {scn['plan']['what']}; alive at turn {scn['turns']}, shields back at {v['ship']['shield_max']:g}")
        return problems
    except ScenarioError as e:
        return [str(e)]


def c17(ctx: Ctx) -> list[str]:
    """smoke/17 — hole physics: held, escape, neutron star (HZ-04, HZ-06, CFG-16, CFG-17, D-100)."""
    require_pkg(ctx)
    v = m2(ctx)
    k, f, scale, engine = v["k_g"], v["falloff"], v["pull_turns_scale"], v["ship"]["engine"]
    bh, ns = v["blackhole_mass"], v["neutron_mass"]
    try:
        problems: list[str] = []
        # (a) held: the largest distance at which floor(engine − pull) ≤ 0
        d_held = max((d for d in range(1, 12) if math.floor(engine - C.pull_at(bh, d, k, f)) <= 0), default=None)
        if d_held is None:
            raise ScenarioError(f"with blackhole_mass {bh:g}, k_g {k:g}, falloff {f:g} and engine {engine:g} no distance ≥ 1 holds the ship "
                                f"(HZ-04 'engine ≤ pull cannot move away') — the held scenario cannot be built from this ini")
        w, (hole, cell, sign) = find_world(ctx, f"a plain black hole with a free cell at distance {d_held}",
                                           hole_pick(ctx, wormhole=False, distance=d_held))
        scn = C.plan_hole_held(hole, cell, sign, kind="black_hole", mass=bh, k_g=k, falloff=f, scale=scale, engine=engine,
                               shield_max=v["ship"]["shield_max"])
        report, sp = run_scenario(ctx, "hole_held", scn, seed=w.seed)
        problems += C.check_scenario(report, scn["plan"], save_present=sp)
        # (b) escape from distance 4 moving directly away at full engine power
        d0, steps = 4, 3
        path = tuple(C.escape_path(d0, steps, engine=engine, mass=bh, k_g=k, falloff=f))
        w2, (hole2, cell2, sign2) = find_world(ctx, f"a black hole with a free diagonal escape path from distance {d0} ({path})",
                                               hole_pick(ctx, wormhole=None, distance=d0, path=path, diagonal_first=True))
        scn2 = C.plan_hole_escape(hole2, cell2, sign2, kind="black_hole", mass=bh, k_g=k, falloff=f, scale=scale, engine=engine, steps=steps)
        report2, _ = run_scenario(ctx, "hole_escape", scn2, seed=w2.seed)
        problems += C.check_scenario(report2, scn2["plan"])
        # (c) neutron star at distance 1: destroyed at N, no drain
        def pick_ns(w: World) -> Any:
            for s in w.index.get("neutron_stars") or []:
                got = C.cell_at_distance(w.index, s, 1, v["sector_size"], diagonal=False) or C.cell_at_distance(w.index, s, 1, v["sector_size"])
                if got:
                    return (s, got[0])
            return None
        w3, (star3, cell3) = find_world(ctx, "a neutron star with a free cell at distance 1", pick_ns)
        scn3 = C.plan_neutron_fatal(star3, cell3, mass=ns, k_g=k, falloff=f, scale=scale, shield_max=v["ship"]["shield_max"])
        report3, sp3 = run_scenario(ctx, "neutron", scn3, seed=w3.seed)
        problems += C.check_scenario(report3, scn3["plan"], save_present=sp3)
        if not problems:
            print(f"         held at distance {d_held} (seed {w.seed}, destroyed turn {scn['plan']['end']['turn']}); escape from {d0} via {path} "
                  f"(seed {w2.seed}); neutron star destroyed turn {scn3['plan']['end']['turn']} (seed {w3.seed})")
        return problems
    except ScenarioError as e:
        return [str(e)]


def c18(ctx: Ctx) -> list[str]:
    """smoke/18 — wormhole crossing vs a plain black hole (HZ-05, D-102)."""
    require_pkg(ctx)
    v = m2(ctx)
    k, f, scale, bh = v["k_g"], v["falloff"], v["pull_turns_scale"], v["blackhole_mass"]
    try:
        w, (hole, cell, _) = find_world(ctx, "a wormhole black hole with a free cell at distance 1", hole_pick(ctx, wormhole=True, distance=1))
        scn = C.plan_wormhole(hole, cell, mass=bh, k_g=k, falloff=f, scale=scale)
        report, _ = run_scenario(ctx, "wormhole", scn, seed=w.seed)
        problems = C.check_scenario(report, scn["plan"])
        w2, (hole2, cell2, _) = find_world(ctx, "a plain black hole with a free cell at distance 1", hole_pick(ctx, wormhole=False, distance=1))
        scn2 = C.plan_plain_hole_kills(hole2, cell2, mass=bh, k_g=k, falloff=f, scale=scale)
        report2, sp2 = run_scenario(ctx, "plain_hole", scn2, seed=w2.seed)
        problems += C.check_scenario(report2, scn2["plan"], save_present=sp2)
        if not problems:
            wt = hole["wormhole_to"]
            print(f"         seed {w.seed}: crossed from {hole['galaxy']}/{hole['sector']} to {wt['galaxy']}/{wt['sector']} at turn "
                  f"{scn['turns']}; plain hole (seed {w2.seed}) destroyed at turn {scn2['plan']['end']['turn']}")
        return problems
    except ScenarioError as e:
        return [str(e)]


def c19(ctx: Ctx) -> list[str]:
    """smoke/19 — warp reach, rejections, factor 10, pull on warp (CMD-07, MV-02, MV-03, MV-05, HZ-10, D-101)."""
    require_pkg(ctx)
    v = m2(ctx)
    size, gsize, usize = v["sector_size"], v["galaxy_size"], v["universe_size"]
    k, f, scale, bh = v["k_g"], v["falloff"], v["pull_turns_scale"], v["blackhole_mass"]
    warp = v["ship"]["warp"]
    reach_w = C.warp_reach(warp, v["warp_base"], v["warp_exp"])
    d_in, d_out = int(math.floor(reach_w)), int(math.floor(reach_w)) + 1
    try:
        def pick_sectors(w: World) -> Any:
            near = [gs for gs in C.sectors_at_distance(w.sectors, w.start, d_in, gsize) if C.centre_block_free(w.index, gs[0], gs[1], size, 1.5)]
            far = C.sectors_at_distance(w.sectors, w.start, d_out, gsize)
            void = C.void_sector(w.snapshot, usize, gsize)
            farthest = C.farthest_sector(w.sectors, w.start, gsize)
            if near and far and void and farthest and C.centre_block_free(w.index, farthest[0], farthest[1], size, 1.5):
                return near[0], far[0], void, farthest
            return None
        w, (dest_in, dest_out, void, farthest) = find_world(
            ctx, f"sectors at flattened distance {d_in} and {d_out} from the start, a Void sector and a farthest sector", pick_sectors)
        start = w.index["player_start"]
        start_cell = (int(start[2][0]), int(start[2][1]))
        pull_start = 0.0
        for s in w.index.get("stars") or []:
            if C._gs(s) == w.start and (int(s["cell"][0]), int(s["cell"][1])) == start_cell:
                pull_start = C.pull_at(float(s["mass"]), 0, k, f)
        problems: list[str] = []
        # (a) within reach: arrives at the free cell nearest the centre, warps[0] recorded
        scn = C.plan_warp_arrival(start, dest_in, warp, pull_start=pull_start, sector_size=size, label=f" (distance {d_in}, within reach {reach_w:.2f})")
        report, _ = run_scenario(ctx, "warp_arrival", scn, seed=w.seed)
        problems += C.check_scenario(report, scn["plan"])
        # (d) factor 10 on a temp ini: the farthest sector in one turn
        sh = v["ship"]
        ini10 = write_ini(ctx, "warp10.ini", {("ships", v["starting_class"]): f"{sh['torpedoes']:g}, {sh['phasers']:g}, {sh['shield_max']:g}, {sh['engine']:g}, 10"})
        far_d = C.chebyshev(C.flat_sector(farthest[0], farthest[1], gsize), C.flat_sector(w.start[0], w.start[1], gsize))
        scn_far = C.plan_warp_arrival(start, farthest, 10.0, pull_start=pull_start, sector_size=size,
                                      label=f" factor 10 to the farthest sector (distance {far_d}; w ≥ 10 → anywhere)")
        report_far, _ = run_scenario(ctx, "warp_far", scn_far, seed=w.seed, config=ini10)
        problems += C.check_scenario(report_far, scn_far["plan"])
        # (b)(c)(e) rejections at submission, over the bus from the safe start
        def args(dest: tuple, factor: float) -> list[str]:
            return [str(dest[0][0]), str(dest[0][1]), str(dest[1][0]), str(dest[1][1]), f"{factor:g}"]
        cases = [
            {"label": "beyond reach", "command": "warp", "args": args(dest_out, warp), "expect": "rejected", "reach": reach_w,
             "why": f"flattened Chebyshev distance {d_out} exceeds reach {reach_w:.2f} for factor {warp:g} (CMD-07, MV-02); the message states the reach"},
            {"label": "factor above warpcore_power", "command": "warp", "args": args(dest_in, warp + 1), "expect": "rejected",
             "reason_all": [r"warp[ _]?core[ _]?power"], "why": f"factor {warp + 1:g} > warpcore_power {warp:g} (CMD-07, SH-05)"},
            {"label": "Void destination", "command": "warp", "args": args(void, warp), "expect": "rejected",
             "why": "the destination holds no sector (Void) — a rejection, not a failed warp (M2 § 1, § 5)"},
            {"label": "outside the universe", "command": "warp", "args": [str(usize[0]), str(usize[1]), "0", "0", f"{warp:g}"], "expect": "rejected",
             "why": f"galaxy {usize[0]},{usize[1]} is outside universe_size {usize[0]}x{usize[1]} (M2 § 5)"},
        ]
        run = bus_session(ctx, "warp_cases", seed=w.seed, turns=2, driver=lambda pub, pull: C.drive_order_cases(pub, pull, cases))
        problems += with_log(C.check_order_cases(run.result, cases), run)
        # (f) pull on warp: effective ≤ 0 fails at resolution with a hazard event; 0 < effective < 1 never arrives
        d_neg = max((d for d in range(1, 12) if C.pull_at(bh, d, k, f) >= warp), default=None)
        if d_neg is None:
            raise ScenarioError(f"with blackhole_mass {bh:g} and warp {warp:g} no distance ≥ 1 gives effective ≤ 0 — the failed-warp scenario cannot be built from this ini (HZ-10)")
        for label, dist, require_event in (("neg", d_neg, True), ("pos", d_neg + 1, False)):
            def pick_pull(w_: World, dist_: int = dist) -> Any:
                for h in C.pick_holes(w_.index, wormhole=None):
                    got = C.cell_at_distance(w_.index, h, dist_, size, diagonal=False) or C.cell_at_distance(w_.index, h, dist_, size)
                    if not got:
                        continue
                    hs = (tuple(int(x) for x in h["galaxy"]), tuple(int(x) for x in h["sector"]))
                    dests = [gs for dd in range(1, d_in + 1) for gs in C.sectors_at_distance(w_.sectors, hs, dd, gsize)]
                    if dests:
                        return (h, got[0], dests[0])
                return None
            w_p, (hole, cell, dest) = find_world(ctx, f"a black hole with a free cell at distance {dist} and a sector within reach", pick_pull)
            scn_p = C.plan_warp_pull(hole, cell, dest, warp, mass=bh, k_g=k, falloff=f, scale=scale, require_event=require_event)
            report_p, _ = run_scenario(ctx, f"warp_pull_{label}", scn_p, seed=w_p.seed)
            problems += C.check_scenario(report_p, scn_p["plan"])
        if not problems:
            print(f"         seed {w.seed}: warp {warp:g} reach {reach_w:.2f}; arrived at {list(dest_in[0])}/{list(dest_in[1])}; factor 10 reached "
                  f"{list(farthest[0])}/{list(farthest[1])} (distance {far_d}); 4 rejections; pull-netted warps at distance {d_neg} and {d_neg + 1} failed")
        return problems
    except ScenarioError as e:
        return [str(e)]


def c20(ctx: Ctx) -> list[str]:
    """smoke/20 — AI hazard avoidance over a long run (D-103, D-105)."""
    require_pkg(ctx)
    m2(ctx)
    try:
        problems: list[str] = []
        notes: list[str] = []
        tried = 0
        for seed in SEEDS:
            w = probe_world(ctx, seed)
            if not (w.index.get("black_holes") or w.index.get("neutron_stars")):
                continue
            tried += 1
            snapshots: list[dict] = []
            run = bus_session(ctx, f"ai{AI_RUN_TURNS}_{seed}", seed=seed, turns=AI_RUN_TURNS,
                              driver=lambda pub, pull: C.drive_engine(pub, pull, turns=AI_RUN_TURNS, hello_timeout=15.0, turn_timeout=10.0, orders=False,
                                                                      on_resolved=lambda p: snapshots.append(p.get("snapshot") or {})))
            obs = run.result
            problems += with_log(C.check_bus_observations(obs, turns_expected=AI_RUN_TURNS, need_orders=False), run)
            if problems:
                return problems
            if obs.hello is not None and isinstance(obs.hello.get("snapshot"), dict):
                snapshots.insert(0, obs.hello["snapshot"])
            report, _ = C.load_json(ctx.art / f"report_ai{AI_RUN_TURNS}_{seed}.json")
            if not isinstance(report, dict):
                notes.append(f"seed {seed}: the --bus run wrote no report, so destroyed[] was not checked (C2 --report)")
                report = None
            errs, stats = C.check_ai_hazards(report, snapshots, w.index)
            problems += errs
            notes.append(f"seed {seed}: {stats['ai_in_hole_sectors']} AI ship(s) started in an indexed hole sector, {stats['ai_moves_checked']} "
                         f"AI turn-pairs checked, {stats['ai_star_deaths']} AI star death(s)")
            if problems or stats["ai_in_hole_sectors"] > 0 or tried >= 2:
                break
        if tried == 0:
            raise ScenarioError(f"none of the gate's seeds {list(SEEDS)} indexes a black hole or neutron star (C9 world_index) — report it (M2 § 5)")
        for n in notes:
            print(f"         {n}")
        return problems
    except ScenarioError as e:
        return [str(e)]


def c21(ctx: Ctx) -> list[str]:
    """smoke/21 — the game-over state machine over the bus (OV-09b, OV-09c, CMD-19, D-105)."""
    require_pkg(ctx)
    v = m2(ctx)
    try:
        w, (star, _) = find_world(ctx, "a medium-bucket star with a free neighbouring cell", star_pick(ctx))
        n = C.countdown_turns(C.pull_at(float(star["mass"]), 0, v["k_g"], v["falloff"]), v["pull_turns_scale"])
        save_dir = ctx.art / "saves_game_over"
        save = save_dir / "game.json"
        run = bus_session(ctx, "game_over", seed=w.seed, turns=n + 5, teleport=C.teleport_arg(star["galaxy"], star["sector"], star["cell"]),
                          save_dir=save_dir,
                          driver=lambda pub, pull: C.drive_game_over(pub, pull, turns=n, save_path=save, order_after_over=True, new_game=True, turn_after_new=True))
        problems = C.check_game_over(run.result, n, reject_id="CMD-19")
        if problems and run.exited_during_drive:
            problems.insert(0, f"the --bus engine process exited (rc {run.returncode}) while the gate was still driving it — after game.over the engine must "
                               f"stay up and answer orders with order.rejected until 'new' or 'quit' (CMD-19, D-105); C9's 'the sim stops there' is the in-process run")
        if not problems:
            obs = run.result
            print(f"         seed {w.seed}: game.over at turn {n} after {obs.get('game_over_wait_s')} s; move rejected citing CMD-19; "
                  f"new → seed {obs.get('hello_after_new', {}).get('seed')} at turn 0; save back after one turn")
        return with_log(problems, run)
    except ScenarioError as e:
        return [str(e)]


def c22(ctx: Ctx) -> list[str]:
    """smoke/22 — hazard UI: T2 overlay and countdown, T4 PNL-06 and GAME OVER banner, T3 cause (GRD-03, PNL-06, OUT-03, OUT-16)."""
    require_pkg(ctx)
    v = ensure_values(ctx)
    try:
        w, _ = find_world(ctx, "a world", lambda w: True)
        variants = C.hazard_render_snapshots(w.snapshot)
        sid = variants["ship_id"]
        name = variants["over"]["ships"][sid].get("name") or "the ship"
        event = {"phase": "TP-03", "type": "destroyed", "severity": "alert",
                 "text": f"{name} was pulled into the black hole at 3,3 — ship lost", "ids": ["OUT-11", "OUT-16"]}
        payload = {"turn_no": variants["over"]["turn_no"], "snapshot": variants["over"], "events": [event]}
        probe = render_probe_run(ctx, "hazard", {"grid": [variants["hole"], variants["without"]],
                                                 "status": [variants["star"], variants["without"], variants["over"]],
                                                 "output": [payload]}, styled=True, view=w.snapshot.get("view") or {"level": "follow"})
        glyph = C.ACTIVE_SHIP_GLYPH.get(v.get("glyph_set", "unicode"), "▲")
        hole_hz = variants["hole"]["ships"][sid]["hazard"]
        star_hz = variants["star"]["ships"][sid]["hazard"]
        problems = probe_errors(probe) + C.check_hazard_render(probe, glyph, hole_hz, star_hz, "black hole")
        if problems and (probe.get("renders") or {}).get("grid"):
            head = "\n".join(probe["renders"]["grid"][0].splitlines()[:6])
            problems.append("first lines of the hazard grid render:\n" + textwrap.indent(head, " " * 10))
        return problems
    except ScenarioError as e:
        return [str(e)]


CHECKS = [
    C.Check("smoke/00", "harness: interpreter, deps, docs current, ini parses", "CFG-*, D-53, D-74", c00),
    C.Check("smoke/01", "unit tests", "specs/M1.md § 3", c01),
    C.Check("smoke/02", "turns advance", "OV-02, TP-01…08, C2, D-106", c02),
    C.Check("smoke/03", "AI acts", "OV-10, SP-06", c03),
    C.Check("smoke/04", "snapshot changes every turn", "BUS-07", c04),
    C.Check("smoke/05", "upkeep wired to config", "SUP-01, SUP-02, CFG-45/46, D-106", c05, until="M3"),
    C.Check("smoke/06", "mutiny fires", "SUP-03, SUP-04b, SUP-04c, CFG-47/48, D-76", c06),
    C.Check("smoke/07", "determinism", "D-67, SP-01", c07),
    C.Check("smoke/08", "config key honoured", "CFG-45, D-49", c08),
    C.Check("smoke/09", "bus wiring", "BUS-03/04, MSG-01…07, C3", c09),
    C.Check("smoke/10", "render freshness", "C4, BUS-05, GRD-06", c10),
    C.Check("smoke/11", "autosave round trip", "OV-12, C6", c11),
    C.Check("smoke/12", "long run robustness", "D-69, Chat A15, C9", c12),
    C.Check("smoke/13", "persistent move, stop, replace", "CMD-06, CMD-21, OV-02b, D-98, C9", c13, since="M2"),
    C.Check("smoke/14", "orders visible on T4 (PNL-07) and via T1 'orders'", "PNL-07, D-99, C4, C9", c14, since="M2"),
    C.Check("smoke/15", "star fatal: countdown, drain, game over, save deleted", "HZ-02, HZ-03, HZ-09, OV-09b, OV-09c, D-100, C9", c15, since="M2"),
    C.Check("smoke/16", "countdown reset on leaving; shields recharge outside", "HZ-08, CM-05, C9", c16, since="M2"),
    C.Check("smoke/17", "hole physics: held, escape, neutron star", "HZ-04, HZ-06, CFG-16, CFG-17, D-100, C9", c17, since="M2"),
    C.Check("smoke/18", "wormhole crossing; plain hole destroys", "HZ-05, D-102, C9", c18, since="M2"),
    C.Check("smoke/19", "warp reach, rejections, factor 10, pull on warp", "CMD-07, MV-02, MV-03, MV-05, HZ-10, D-101, C9", c19, since="M2"),
    C.Check("smoke/20", "AI hazard avoidance", "D-103, D-105, OV-10", c20, since="M2"),
    C.Check("smoke/21", "game-over state machine over the bus", "OV-09b, OV-09c, CMD-19, D-105, MSG-09, C9", c21, since="M2"),
    C.Check("smoke/22", "hazard UI: T2 overlay, T4 PNL-06 and banner, T3 cause", "GRD-03, PNL-06, OUT-03, OUT-16, C4", c22, since="M2"),
]


def run_gate(ctx: Ctx, select: set[str] | None = None) -> int:
    ctx.art.mkdir(parents=True, exist_ok=True)
    label = f"smoke gate ({'quick' if ctx.quick else 'full'}) — root {ctx.root}"
    if ctx.quick:
        print("quick mode: smoke/05–09, 11–22 are not run (full gate: smoke_test.py . without --quick)")
    if select is None:
        select = QUICK_CODES if ctx.quick else None
    return C.run_checks(CHECKS, ctx, milestone=lambda: ctx.milestone, select=select,
                        budget_s=QUICK_BUDGET_S if ctx.quick else FULL_BUDGET_S, label=label,
                        result_path=ctx.art / "last_run.json")


# --------------------------------------------------------------------------- render probe (subprocess of smoke/10, 14, 22)

def render_probe(in_path: Path, out_path: Path) -> int:
    """Import starbour.terminals.render_<role>, load GameConfig, render the given inputs to plain
    text with Rich (and, with "styled": true, to ANSI so cell tints are observable).  Runs in its
    own interpreter so 'does the render module import zmq' is observable and a hung render cannot
    stall the gate."""
    spec = json.loads(in_path.read_text(encoding="utf-8"))
    sys.path.insert(0, spec["root"])
    zmq_before = "zmq" in sys.modules
    out: dict = {"renders": {}, "styled": {}, "errors": {}, "import_errors": {}, "config_loader": None,
                 "loader_candidates": [f"starbour.config.{n}" for n in CONFIG_LOADERS], "zmq_imported": False}
    mods = {}
    for role in RENDER_ROLES:
        if role not in spec["inputs"]:
            continue
        try:
            mods[role] = importlib.import_module(f"starbour.terminals.render_{role}")
        except Exception:  # noqa: BLE001
            out["import_errors"][f"starbour.terminals.render_{role}"] = traceback.format_exc()
    out["zmq_imported"] = ("zmq" in sys.modules) and not zmq_before
    cfg = None
    try:
        mc = importlib.import_module("starbour.config")
        for name in CONFIG_LOADERS:
            obj: object = mc
            for part in name.split("."):
                obj = getattr(obj, part, None)
                if obj is None:
                    break
            if callable(obj):
                cfg = obj(Path(spec["config_path"]))
                out["config_loader"] = f"starbour.config.{name}"
                break
    except Exception:  # noqa: BLE001
        out["import_errors"]["starbour.config"] = traceback.format_exc()
    if cfg is not None:
        from rich.console import Console
        for role, inputs in spec["inputs"].items():
            mod = mods.get(role)
            if mod is None:
                continue
            texts, styled = [], []
            try:
                for inp in inputs:
                    renderable = mod.render(inp, spec["view"], cfg, spec["width"])
                    con = Console(file=io.StringIO(), width=spec["width"], record=True, force_terminal=False,
                                  color_system=None, legacy_windows=False, highlight=False)
                    con.print(renderable)
                    texts.append(con.export_text(styles=False))
                    if spec.get("styled"):
                        renderable = mod.render(inp, spec["view"], cfg, spec["width"])
                        con = Console(file=io.StringIO(), width=spec["width"], record=True, force_terminal=True,
                                      color_system="truecolor", legacy_windows=False, highlight=False)
                        con.print(renderable)
                        styled.append(con.export_text(styles=True))
                out["renders"][role] = texts
                if spec.get("styled"):
                    out["styled"][role] = styled
            except Exception:  # noqa: BLE001
                out["errors"][role] = traceback.format_exc()
    out_path.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    return 0


# --------------------------------------------------------------------------- selftest

def evaluate_report(report: dict, v: dict, turns: int, save_doc: dict | None = None) -> dict[str, list[str] | str]:
    """The report checks as the gate sequences them: smoke/02 first; when it fails the
    dependants are BLOCKED (the gate's report_main is only set after smoke/02 passes).
    The selftest feeds one 40-turn fixture to both the 30-turn and the 40-turn checks."""
    out: dict[str, list[str] | str] = {}
    p02 = C.report_shape_errors(report) or C.check_turns_advance(report, turns)
    out["smoke/02"] = p02
    if p02:
        for code in ("smoke/03", "smoke/04", "smoke/05", "smoke/06", "smoke/11"):
            out[code] = C.BLOCKED
        return out
    out["smoke/03"] = C.check_ai_acts(report)
    out["smoke/04"] = C.check_hashes_differ(report)
    out["smoke/05"] = C.check_upkeep(report, v["food_start"], v["water_start"], v["warn_turns"])
    out["smoke/06"] = C.check_mutiny(report, v["food_start"], v["water_start"], v["mutiny_turns_at_zero"],
                                     v["mutiny_ship_class"], v["mutiny_supplies"], v["mutiny_fear_delta"])
    if save_doc is not None:
        out["smoke/11"] = C.check_save_document(save_doc, report["per_turn"][-1]["snapshot_hash"])
    return out


def _fake_probe(snap_a: dict, snap_bx: dict, snap_by: dict, pay_a: dict, pay_b: dict, *, stale: bool = False,
                glyph_fixed: bool = False, drop_event: bool = False, no_numbers: bool = False) -> dict:
    """What a correct (or deliberately broken) render probe would return, built from the fixtures."""
    def grid(s: dict) -> str:
        w, h = s["sizes"]["sector"]
        _, ship = C.player_ship(s)
        cx, cy = ship["position"][2]
        rows = [f"sector 7,9 · turn {s['turn_no']} · fog on   legend: ▲ you ▼ AI ○ planet"]
        for y in range(h):
            rows.append(f"{y:>2} " + "".join("▲" if (x, y) == (cx, cy) else "·" for x in range(w)))
        return "\n".join(rows)

    def status(s: dict) -> str:
        cap = s["captains"][s["player_id"]]
        if no_numbers:
            return "Status panel"
        return f"Turn {s['turn_no']}\nfood {cap['food']}  water {cap['water']}\nfear {cap['fear']}  respect {cap['respect']}"

    def output(p: dict) -> str:
        evs = p["events"][1:] if drop_event else p["events"]
        return "\n".join(f"[{e['phase']}] {e['text']}" for e in evs)

    def command(s: dict) -> str:
        return f"[T{s['turn_no']} · Lantern · 0 orders] >"

    frozen = stale or glyph_fixed
    g = [grid(snap_a), grid(snap_a if frozen else snap_bx), grid(snap_a if frozen else snap_by)]
    if glyph_fixed and not stale:
        g[1] += "\n(redrawn)"
        g[2] += "\n(redrawn)"
    return {"renders": {"grid": g,
                        "status": [status(snap_a), status(snap_a if stale else snap_bx)],
                        "output": [output(pay_a), output(pay_a if stale else pay_b)],
                        "command": [command(snap_a), command(snap_a if stale else snap_bx)]},
            "errors": {}, "import_errors": {}, "config_loader": "starbour.config.load_config", "zmq_imported": False,
            "loader_candidates": [f"starbour.config.{n}" for n in CONFIG_LOADERS]}


def _fake_hazard_probe(variants: dict, glyph: str = "▲", *, no_label: bool = False, no_tint: bool = False, no_panel: bool = False,
                       no_banner: bool = False, no_cause: bool = False) -> dict:
    """A correct (or deliberately broken) smoke/22 probe built from hazard_render_snapshots()."""
    sid = variants["ship_id"]

    def grid(s: dict, styled: bool) -> str:
        w, h = s["sizes"]["sector"]
        ship = s["ships"][sid]
        cx, cy = ship["position"][2]
        hz = ship.get("hazard")
        key = f"{ship['position'][0][0]},{ship['position'][0][1]}/{ship['position'][1][0]},{ship['position'][1][1]}"
        holes = {tuple(t["cell"]) for t in s["sectors"][key]["terrain"] if t["kind"] == "black_hole"}
        rows = [f"sector · turn {s['turn_no']} · fog on"]
        for y in range(h):
            cells = []
            for x in range(w):
                if (x, y) == (cx, cy):
                    glyph_txt = glyph + (str(hz["turns_left"]) if hz and not no_label else " ")
                    cells.append(f"\x1b[36m{glyph_txt}\x1b[0m" if styled else glyph_txt)
                elif (x, y) in holes:
                    cells.append("\x1b[31m● \x1b[0m" if styled else "● ")
                elif holes and styled and not no_tint:
                    cells.append("\x1b[48;2;40;0;0m· \x1b[0m")
                else:
                    cells.append("· ")
            rows.append(f"{y:>2} " + "".join(cells))
        return "\n".join(rows)

    def status(s: dict) -> str:
        ship = s["ships"][sid]
        lines = [f"Turn {s['turn_no']}", f"shields {ship['shield_current']} / {ship['shield_max']}"]
        hz = ship.get("hazard")
        if hz and not no_panel:
            lines.append(f"HAZARD {hz['kind']}: pull {hz['pull']:.2f} · {hz['turns_left']} turns until pulled in · drain {hz['drain']}/turn")
        if s.get("game_over") and not no_banner:
            lines.append("*** GAME OVER — type new to start again ***")
        return "\n".join(lines)

    def output(p: dict) -> str:
        return "\n".join(f"[{e['phase']}] " + ("ship lost" if no_cause else e["text"]) for e in p["events"])

    hole, without, star, over = variants["hole"], variants["without"], variants["star"], variants["over"]
    name = over["ships"][sid].get("name", "Lantern")
    payload = {"turn_no": over["turn_no"], "snapshot": over,
               "events": [{"phase": "TP-03", "type": "destroyed", "severity": "alert", "text": f"{name} was pulled into the black hole at 3,3 — ship lost", "ids": []}]}
    return {"renders": {"grid": [grid(hole, False), grid(without, False)], "status": [status(star), status(without), status(over)],
                        "output": [output(payload)]},
            "styled": {"grid": [grid(hole, True), grid(without, True)]},
            "errors": {}, "import_errors": {}, "config_loader": "starbour.config.load_config", "zmq_imported": False}


def _good_game_over_obs(n: int = 7) -> dict:
    return {"hello": {"turn_no": 0}, "seed": 1234, "ship_id": "s-194", "resolved": list(range(1, n + 1)), "last_snapshot_game_over": True,
            "game_over": {"turn_no": n, "cause": "pulled into the star at 5,5 in sector 3,4/7,9",
                          "summary": {"turns": n, "sectors_visited": 1, "mutinies": 0, "fear": 10, "respect": 10, "gold": 5, "silver": 50}},
            "game_over_wait_s": 0.01, "save_present_after_over": False,
            "after_over_reply": {"topic": "order.rejected", "reason": "game over — type new to start again (CMD-19)"},
            "hello_after_over": {"turn_no": n, "game_over": True}, "new_reply": {"topic": "order.accepted", "reason": ""},
            "hello_after_new": {"turn_no": 0, "seed": 1235, "game_over": False}, "resolved_after_new": 1,
            "save_present_after_new_turn": True, "notes": []}


def selftest(root: Path) -> int:
    fx = HERE / "fixtures"
    sys.path.insert(0, str(fx))
    from make_fixtures import FIXTURE_CFG, FIXTURE_M2, M2_INDEX  # noqa: E402
    from stub_engine import StubEngine  # noqa: E402
    results: list[tuple[bool, str]] = []

    def t(name: str, ok: bool, detail: object = "") -> None:
        results.append((ok, name))
        print(("ok    " if ok else "FAIL  ") + name + ("" if ok or not detail else f"\n      -> {detail}"))

    def load(name: str) -> dict:
        p = fx / f"{name}.json"
        if not p.exists():
            raise C.HarnessError(f"fixture {p} missing — run verify/fixtures/make_fixtures.py")
        return json.loads(p.read_text(encoding="utf-8"))

    print(f"== smoke selftest — cwd {Path.cwd()} — fixtures {fx} ==")
    good, save_good = load("sim_good"), load("save_good")

    # 1. shape
    t("shape: sim_good has the C2 shape", not C.report_shape_errors(good))
    t("shape: a malformed report is rejected", bool(C.report_shape_errors({"per_turn": "x"})))

    # 2. the good fixture passes every report checker
    ev = evaluate_report(good, FIXTURE_CFG, 40, save_good)
    t("sim_good passes smoke/02, 03, 04, 05, 06, 11", all(not v for v in ev.values()), {k: v for k, v in ev.items() if v})

    # 3. each broken fixture fails only its own check
    expected = {"sim_turns_stuck": "smoke/02", "sim_ai_idle": "smoke/03", "sim_ai_frozen": "smoke/03",
                "sim_hash_frozen": "smoke/04", "sim_upkeep_wrong": "smoke/05", "sim_no_warning": "smoke/05",
                "sim_warning_always": "smoke/05", "sim_no_mutiny": "smoke/06", "sim_mutiny_early": "smoke/06",
                "sim_mutiny_no_effect": "smoke/06"}
    for name, code in expected.items():
        ev = evaluate_report(load(name), FIXTURE_CFG, 40, save_good)
        failing = [k for k, v in ev.items() if v and v != C.BLOCKED]
        blocked = [k for k, v in ev.items() if v == C.BLOCKED]
        msg = ev[code][0] if isinstance(ev.get(code), list) and ev[code] else ""
        t(f"{name} fails only {code}" + (f" (blocking {len(blocked)})" if blocked else ""), failing == [code] and bool(msg),
          f"failing={failing}; {msg or ev}")
        if failing == [code]:
            print(f"      message: {msg[:150]}")

    # 4. determinism
    seq = [x["snapshot_hash"] for x in good["per_turn"]]
    other = seq[:-1] + ["0" * 40]
    t("determinism: identical runs pass", not C.check_determinism(seq, seq, other))
    t("determinism: diverging same-seed runs fail", any("diverge" in m for m in C.check_determinism(seq, other, other)))
    t("determinism: an ignored seed fails", any("seed is ignored" in m for m in C.check_determinism(seq, seq, seq)))

    # 5. config honoured
    with tempfile.TemporaryDirectory() as td:
        tmp_ini = Path(td) / "cfg12.ini"
        tmp_ini.write_text("[supplies]\nfood_start = 12\n", encoding="utf-8")
        ok_doc, bad_doc = load("sim_config_ok"), load("sim_config_ignored")
        ok_doc["config_path"] = bad_doc["config_path"] = str(tmp_ini)
        t("config honoured: food 11 at turn 1 passes", not C.check_config_honoured(ok_doc, 11, 12, tmp_ini))
        t("config honoured: literal 30 fails", any("literal" in m for m in C.check_config_honoured(bad_doc, 11, 12, tmp_ini)))
        wrong_path = dict(ok_doc, config_path="E:\\somewhere\\else.ini")
        t("config honoured: wrong config_path fails", any("config_path" in m for m in C.check_config_honoured(wrong_path, 11, 12, tmp_ini)))

    # 6. save
    final_hash = good["per_turn"][-1]["snapshot_hash"]
    t("save: good save round-trips to the final hash", not C.check_save_document(save_good, final_hash))
    t("save: a stale save fails", any("stale" in m for m in C.check_save_document(load("save_stale"), final_hash)))
    flat = dict(save_good["snapshot"], rng_state=[1])
    t("save: flat layout (snapshot keys + rng_state) accepted", not C.check_save_document(flat, final_hash))

    # 7. render
    snap = load("snapshot_good")
    bx, mx = C.make_successor(snap, "x")
    by, my = C.make_successor(snap, "y")
    ev_a = [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "alpha moved", "ids": []},
            {"phase": "TP-06", "type": "upkeep", "severity": "info", "text": "beta upkeep", "ids": []}]
    ev_b = [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "gamma moved", "ids": []},
            {"phase": "TP-06", "type": "upkeep", "severity": "warning", "text": "delta low", "ids": []}]
    pa = {"turn_no": snap["turn_no"], "snapshot": snap, "events": ev_a}
    pb = {"turn_no": bx["turn_no"], "snapshot": bx, "events": ev_b}
    cap = snap["captains"][snap["player_id"]]
    base = {"turn_no": snap["turn_no"], "food": cap["food"], "water": cap["water"], "fear": cap["fear"], "respect": cap["respect"]}
    glyph = C.ACTIVE_SHIP_GLYPH["unicode"]
    r = C.check_render_probe(_fake_probe(snap, bx, by, pa, pb), glyph, mx, my, base, ev_a, ev_b)
    t("render: a faithful renderer passes", not r, r)
    r = C.check_render_probe(_fake_probe(snap, bx, by, pa, pb, stale=True), glyph, mx, my, base, ev_a, ev_b)
    t("render: a stale renderer fails for grid, status, output and command",
      sum(1 for m in r if "stale" in m or "identical" in m) >= 4, r)
    r = C.check_render_probe(_fake_probe(snap, bx, by, pa, pb, glyph_fixed=True), glyph, mx, my, base, ev_a, ev_b)
    t("render: a glyph that ignores position fails", any("should move exactly one" in m for m in r), r)
    r = C.check_render_probe(_fake_probe(snap, bx, by, pa, pb, drop_event=True), glyph, mx, my, base, ev_a, ev_b)
    t("render: a dropped event fails", any("missing from" in m for m in r), r)
    r = C.check_render_probe(_fake_probe(snap, bx, by, pa, pb, no_numbers=True), glyph, mx, my, base, ev_a, ev_b)
    t("render: a status panel without the numbers fails", any("does not appear" in m for m in r), r)
    probe = _fake_probe(snap, bx, by, pa, pb)
    probe["zmq_imported"] = True
    t("render: zmq imported by a render module fails", any("zmq" in m for m in C.check_render_probe(probe, glyph, mx, my, base, ev_a, ev_b)))
    probe = _fake_probe(snap, bx, by, pa, pb)
    probe["config_loader"] = None
    t("render: no GameConfig loader fails naming the candidates",
      any("GameConfig" in m and "starbour.config.load_config" in m for m in C.check_render_probe(probe, glyph, mx, my, base, ev_a, ev_b)))
    t("render sources: clean module passes", not C.check_render_sources({"render_grid.py": "from rich.table import Table\n"}))
    t("render sources: import zmq fails", any("zmq" in m for m in C.check_render_sources({"render_grid.py": "import zmq\n"})))
    t("render sources: time.sleep fails", any("time" in m for m in C.check_render_sources({"render_grid.py": "import time\ntime.sleep(1)\n"})))
    t("render sources: input() fails", any("input" in m for m in C.check_render_sources({"render_command.py": "x = input('> ')\n"})))

    # 8. bus client against the stub engine
    for mode, expect in (("good", None), ("stuck_turn", "not strictly increasing"), ("same_snapshot", "byte-identical"),
                         ("reject_no_id", "CMD-06"), ("no_reply", "order.accepted"), ("no_hello", "state.hello")):
        eng = StubEngine(mode)
        eng.start()
        eng.ready.wait(5)
        t0 = time.perf_counter()
        try:
            obs = C.drive_engine(eng.pub_port, eng.pull_port, turns=3, hello_timeout=2.0 if mode == "no_hello" else 10.0,
                                 turn_timeout=1.5, reply_timeout=1.5, orders=True)
        finally:
            eng.stop()
        problems = C.check_bus_observations(obs, turns_expected=3, need_orders=True)
        if expect is None:
            t(f"bus: stub engine '{mode}' passes ({time.perf_counter() - t0:.1f} s)", not problems, problems)
        else:
            t(f"bus: stub engine '{mode}' fails naming it", any(expect in m for m in problems), problems)

    # 9. the 'starbour package not found' path, end to end, from a temp copy of the kit (no starbour/)
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td) / "repo"
        for rel in ("tools", "tests", "specs"):
            shutil.copytree(root / rel, tmp / rel, ignore=shutil.ignore_patterns("__pycache__"))
        (tmp / "docs").mkdir()
        for rel in ("docs/master_of_starbour_blueprint.xlsx", "docs/DESIGN.md", "docs/DECISIONS.md", "docs/config_keys.json",
                    "controls_config.ini", "pyproject.toml"):
            shutil.copy2(root / rel, tmp / rel)
        res = C.run_cmd([sys.executable, str(HERE / "smoke_test.py"), str(tmp), "--quick"], tmp, 120.0)
        out = res.stdout + res.stderr
        line = next((ln for ln in out.splitlines() if "smoke/02" in ln), "")
        t("no starbour package: smoke_test.py exits 1 with the specific message and no traceback",
          res.returncode == 1 and "starbour package not found at" in out and "M1 not built yet" in out and "Traceback" not in out,
          f"exit {res.returncode}; tail:\n{res.tail(15)}")
        if line:
            print(f"      gate line: {line.strip()[:160]}")
        t("no starbour package: dependants are BLOCKED, not FAIL", out.count("BLOCKED") >= 3 and "FAIL    smoke/03" not in out, res.tail(12))

    # ---------------------------------------------------------------- M2 (specs/M2.md § 3)
    print("-- M2 --")
    # 10. D-106: smoke/02 relaxed from M2, smoke/05 scoped to ≤ M3
    ended = copy_report = json.loads(json.dumps(good))
    ended["per_turn"] = ended["per_turn"][:20]
    ended.update(turns_requested=30, turns_completed=20, outcome="game_over")
    ended["final"].update(game_over_turn=20, game_over_cause="pulled into a black hole", hazard_deaths=1)
    t("D-106: an AI player that dies at turn 20 of 30 passes smoke/02 with allow_end", not C.check_turns_advance(ended, 30, allow_end=True))
    r = C.check_turns_advance(ended, 30, allow_end=False)
    t("D-106: the same report fails smoke/02 before M2, naming D-106", any("D-106" in m for m in r), r)
    mismatch = json.loads(json.dumps(ended))
    mismatch["final"]["game_over_turn"] = 19
    t("D-106: game_over_turn ≠ turns_completed fails even with allow_end", any("turns_completed" in m for m in C.check_turns_advance(mismatch, 30, allow_end=True)))
    t("D-106: a short run that is still 'running' still fails", any("expected 30" in m for m in C.check_turns_advance(dict(good, turns_completed=20, per_turn=good["per_turn"][:20]), 30, allow_end=True)))
    long_ok = dict(good, final=dict(good["final"], hazard_deaths=3))
    t("smoke/12: final.hazard_deaths reported from M2 passes", not C.check_long_run(long_ok, 0, 10.0, 120.0, "", 40, expect_hazard_deaths=True))
    t("smoke/12: a missing final.hazard_deaths fails from M2", any("hazard_deaths" in m for m in C.check_long_run(good, 0, 10.0, 120.0, "", 40, expect_hazard_deaths=True)))
    t("smoke/12: hazard_deaths is not required at M1", not C.check_long_run(good, 0, 10.0, 120.0, "", 40, expect_hazard_deaths=False))

    # 11. milestone gating: every new row SKIPs at M1 and runs (here: fails) at M2; smoke/05 is SKIP from M4
    def gate_status(code: str, milestone: str) -> str:
        chk = next(c for c in CHECKS if c.code == code)
        canned = C.Check(chk.code, chk.title, chk.ids, lambda ctx: ["canned failure"], since=chk.since, until=chk.until)
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            with tempfile.TemporaryDirectory() as td:
                C.run_checks([canned], None, milestone=lambda: milestone, select=None, budget_s=10.0, label="gating", result_path=Path(td) / "r.json")
        finally:
            sys.stdout = real
        line = next((ln for ln in buf.getvalue().splitlines() if ln.split()[0] in (C.PASS, C.FAIL, C.SKIP, C.BLOCKED) and code in ln), "")
        return line.split()[0] if line else "?"
    rows = sorted(M2_CODES)
    skips = {code: gate_status(code, "M1") for code in rows}
    fails = {code: gate_status(code, "M2") for code in rows}
    t(f"gating: smoke/13–22 SKIP at MILESTONE M1 ({len(rows)} rows)", all(s == C.SKIP for s in skips.values()), skips)
    t("gating: smoke/13–22 run (and here FAIL) at MILESTONE M2", all(s == C.FAIL for s in fails.values()), fails)
    t("gating: smoke/05 is active at M3 and SKIP at M4 (D-106)", gate_status("smoke/05", "M3") == C.FAIL and gate_status("smoke/05", "M4") == C.SKIP,
      (gate_status("smoke/05", "M3"), gate_status("smoke/05", "M4")))

    # 12. plan-based rows: every good scenario fixture passes its plan; each broken fixture fails its row with the right IDs
    scen = load("sim_m2_scenarios")
    bad_scn = {k: v for k, v in ((name, C.check_scenario(s["report"], s["scenario"]["plan"], save_present=s.get("save_present")))
                                 for name, s in scen.items()) if v}
    t(f"M2 scenarios: all {len(scen)} good fixtures pass their plans ({', '.join(sorted(scen))})", not bad_scn, bad_scn)
    broken = {"sim_move_clipped": ("smoke/13", "D-98", "move_remaining"), "sim_star_not_fatal": ("smoke/15", "OV-09b", "game_over"),
              "sim_countdown_no_reset": ("smoke/16", "HZ-08", "turns_left"), "sim_hole_escapable": ("smoke/17", "HZ-04", "held"),
              "sim_wormhole_kills": ("smoke/18", "HZ-05", "running"), "sim_warp_reach_wrong": ("smoke/19", "MV-02", "reach")}
    for name, (code, id_, word) in broken.items():
        doc = load(name)
        if name == "sim_warp_reach_wrong":
            r = C.check_scenario(doc["arrival"]["report"], doc["arrival"]["scenario"]["plan"]) + C.check_order_cases(doc["bus"]["obs"], doc["bus"]["cases"])
        else:
            r = C.check_scenario(doc["report"], doc["scenario"]["plan"], save_present=doc.get("save_present"))
        t(f"{name} fails {code} citing {id_} and naming '{word}'", bool(r) and any(id_ in m for m in r) and any(word in m for m in r), r[:3])
        if r:
            print(f"      message: {r[0][:170]}")
    good_warp = scen["warp_arrival"]
    t("smoke/19: the good warp fixture also passes the bus cases",
      not C.check_order_cases(load("sim_m2_warp_bus")["obs"], load("sim_m2_warp_bus")["cases"]), C.check_order_cases(load("sim_m2_warp_bus")["obs"], load("sim_m2_warp_bus")["cases"]))
    del good_warp

    # 13. plan builders reproduce the spec's numbers (M2 § 4) from the fixture ini
    m = FIXTURE_M2
    star = next(s for s in M2_INDEX["stars"] if s["mass"] == 1.0)
    p15 = C.plan_star_fatal(star, k_g=m["k_g"], falloff=m["falloff"], scale=m["pull_turns_scale"], drain=10, shield_max=60)["plan"]
    t("plan: Sun-like star → destroyed at turn 7, turns_left 6..1, shields 50..0 floor",
      p15["end"]["turn"] == 7 and [x["hazard"]["turns_left"] for x in p15["turns"]] == [6, 5, 4, 3, 2, 1]
      and [x["shield"] for x in p15["turns"]] == [50, 40, 30, 20, 10, 0], p15["turns"])
    hole = next(h for h in M2_INDEX["black_holes"] if not h["wormhole_to"])
    p17 = C.plan_hole_held(hole, (hole["cell"][0] + 2, hole["cell"][1]), (1, 0), kind="black_hole", mass=20, k_g=1, falloff=1.5, scale=7, engine=4, shield_max=60)["plan"]
    t("plan: black hole d=2 → pull 3.85, destroyed at turn 2", p17["end"]["turn"] == 2 and abs(p17["turns"][0]["hazard"]["pull"] - 3.849) < 0.001, p17)
    path = C.escape_path(4, 3, engine=4, mass=20, k_g=1, falloff=1.5)
    p17b = C.plan_hole_escape(hole, (hole["cell"][0] + 4, hole["cell"][1] + 4), (1, 1), kind="black_hole", mass=20, k_g=1, falloff=1.5, scale=7, engine=4)["plan"]
    tl = [x["hazard"]["turns_left"] for x in p17b["turns"]]
    t(f"plan: escape path from d=4 is {path}; turns_left grows while moving away ({tl[:3]}), alive at 10",
      path == [6, 8, 11] and tl[0] < tl[1] < tl[2] and p17b["end"] == {"outcome": "running", "turns": 10}, tl)
    ns = M2_INDEX["neutron_stars"][0]
    p17c = C.plan_neutron_fatal(ns, (ns["cell"][0] + 1, ns["cell"][1]), mass=8, k_g=1, falloff=1.5, scale=7, shield_max=60)["plan"]
    t("plan: neutron star d=1 → pull 2.83, destroyed at turn 3, no drain", p17c["end"]["turn"] == 3 and all(x["shield"] == 60 for x in p17c["turns"]), p17c)
    worm = next(h for h in M2_INDEX["black_holes"] if h["wormhole_to"])
    p18 = C.plan_wormhole(worm, (worm["cell"][0] + 1, worm["cell"][1]), mass=20, k_g=1, falloff=1.5, scale=7)["plan"]
    t("plan: wormhole d=1 → crossing at turn 1 into the partner's sector", p18["turns"][0]["sector"] == [worm["wormhole_to"]["galaxy"], worm["wormhole_to"]["sector"]], p18)
    t("plan: warp reach 3 → 3.07, 5 → 5.95, 9.9 → 142, 10 → anywhere",
      abs(C.warp_reach(3, 16, 4) - 3.07) < 0.005 and abs(C.warp_reach(5, 16, 4) - 5.95) < 0.005 and abs(C.warp_reach(9.9, 16, 4) - 142) < 0.5
      and C.warp_reach(10, 16, 4) == math.inf)
    pick = C.pick_star(M2_INDEX, k_g=1, falloff=1.5, scale=7, sector_size=(24, 24))
    t("pick_star prefers the Sun-like star and finds a free neighbour", pick is not None and pick[0]["mass"] == 1.0 and pick[1] in C.NEIGHBOURS, pick)
    t("void_sector / sectors_at_distance / farthest_sector work on the fixture snapshot",
      C.void_sector(snap, (8, 8), (16, 16)) is not None and C.farthest_sector(C.existing_sectors(snap), ((3, 4), (7, 9)), (16, 16)) is not None)

    # 14. game-over state machine: stub engine modes, the collector and the checker
    with tempfile.TemporaryDirectory() as td:
        for mode, expect in (("game_over", None), ("game_over_leaky", ("CMD-19", "save", "game_over"))):
            save = Path(td) / f"{mode}.json"
            eng = StubEngine(mode, save_path=save, die_at=7)
            eng.start()
            eng.ready.wait(5)
            try:
                obs = C.drive_game_over(eng.pub_port, eng.pull_port, turns=7, save_path=save, hello_timeout=10.0, turn_timeout=2.0,
                                        reply_timeout=2.0, over_timeout=2.0, file_wait=1.0)
            finally:
                eng.stop()
            problems = C.check_game_over(obs, 7)
            if expect is None:
                t(f"game over: stub engine '{mode}' passes smoke/21 (game.over after {obs.get('game_over_wait_s')} s; new → seed {C._get(obs, 'hello_after_new', 'seed')})", not problems, problems)
            else:
                t(f"game over: stub engine '{mode}' fails naming {expect}", all(any(w in m for m in problems) for w in expect), problems)
    t("game over: the canned good observations pass", not C.check_game_over(_good_game_over_obs(), 7), C.check_game_over(_good_game_over_obs(), 7))
    t("game over: the good JSON fixture passes", not C.check_game_over(load("sim_m2_game_over"), 7), C.check_game_over(load("sim_m2_game_over"), 7))
    r = C.check_game_over(load("sim_game_over_accepts_orders"), 7)
    t("sim_game_over_accepts_orders fails smoke/21 citing CMD-19 and the save", any("CMD-19" in m for m in r) and any("save" in m for m in r), r)
    if r:
        print(f"      message: {r[0][:170]}")
    short = _good_game_over_obs()
    short["resolved"] = [1, 2, 3]
    short["notes"] = ["no turn.resolved within 2 s after turn.end for turn 3"]
    t("game over: an engine that stops resolving before the fatal turn fails", any("1..7" in m for m in C.check_game_over(short, 7)))
    no_over = _good_game_over_obs()
    no_over["game_over"] = None
    t("game over: no game.over message fails naming MSG-09", any("MSG-09" in m for m in C.check_game_over(no_over, 7)))

    # 15. orders listing (smoke/14) and warp rejections (smoke/19) against the stub's good mode
    eng = StubEngine("good")
    eng.start()
    eng.ready.wait(5)
    try:
        obs14 = C.drive_orders_listing(eng.pub_port, eng.pull_port, move_args=["+0", "-10"], hello_timeout=10.0, reply_timeout=2.0, turn_timeout=2.0)
        cases = [{"label": "beyond reach", "command": "warp", "args": ["3", "4", "11", "9", "3"], "expect": "rejected", "reach": C.warp_reach(3, 16, 4)},
                 {"label": "factor above warpcore_power", "command": "warp", "args": ["3", "4", "10", "9", "4"], "expect": "rejected", "reason_all": [r"warp[ _]?core[ _]?power"]},
                 {"label": "Void destination", "command": "warp", "args": ["3", "4", "5", "9", "3"], "expect": "rejected"},
                 {"label": "outside the universe", "command": "warp", "args": ["8", "8", "0", "0", "3"], "expect": "rejected"},
                 {"label": "within reach", "command": "warp", "args": ["3", "4", "10", "9", "3"], "expect": "accepted"}]
        obs19 = C.drive_order_cases(eng.pub_port, eng.pull_port, cases, hello_timeout=10.0, reply_timeout=2.0)
    finally:
        eng.stop()
    r = C.check_orders_listing(obs14, ["+0", "-10"], [0, -6], 4)
    t("orders listing: the stub's T1 'orders' and snapshot orders/move_remaining pass smoke/14", not r, r)
    bad14 = json.loads(json.dumps(obs14))
    bad14["orders_after"]["payload"]["text"] = "no orders queued"
    bad14["ship_after"]["move_remaining"] = None
    r = C.check_orders_listing(bad14, ["+0", "-10"], [0, -6], 4)
    t("orders listing: a forgotten persisting move fails naming PNL-07 and D-98", any("PNL-07" in m for m in r) and any("D-98" in m for m in r), r)
    r = C.check_order_cases(obs19, cases)
    t("warp cases: the stub's rejections (reach 3.07, warpcore_power, Void, outside) pass smoke/19", not r, r)
    bad19 = json.loads(json.dumps(obs19))
    bad19["replies"][0]["reason"] = "destination beyond reach 4.00 for factor 3 (CMD-07)"
    bad19["replies"][4]["topic"] = "order.rejected"
    r = C.check_order_cases(bad19, cases)
    t("warp cases: a wrong reach and a rejected in-reach warp fail", any("3.07" in m for m in r) and any("within reach" in m for m in r), r)

    # 16. AI hazard awareness (smoke/20)
    ai_good, ai_bad = load("sim_m2_ai"), load("sim_ai_suicide")
    r, stats = C.check_ai_hazards(ai_good["report"], ai_good["snapshots"], ai_good["index"])
    t(f"sim_m2_ai passes smoke/20 ({stats['ai_in_hole_sectors']} AI ship(s) in hole sectors, {stats['ai_moves_checked']} pairs)", not r, r)
    r, _ = C.check_ai_hazards(ai_bad["report"], ai_bad["snapshots"], ai_bad["index"])
    t("sim_ai_suicide fails smoke/20 citing D-103 for a star death, a move toward the hole and D-105 for an orphan captain",
      sum(1 for m in r if "D-103" in m) >= 2 and any("D-105" in m for m in r), r)
    if r:
        print(f"      message: {r[0][:170]}")

    # 17. render additions (smoke/14 render part, smoke/22)
    variants = C.hazard_render_snapshots(snap)
    orders = variants["orders"]["ships"][variants["ship_id"]]["orders"]
    good_panel = "Orders\n 1. move +0 +6 (4 cells/turn)\n 2. warp 3 4 7 9 3\n"
    t("orders render: a PNL-07 panel listing both orders passes", not C.check_orders_render(good_panel, orders, 4))
    r = C.check_orders_render("Orders\n (2 queued)\n", orders, 4)
    t("orders render: a count-only panel fails naming PNL-07", any("PNL-07" in m for m in r), r)
    r = C.check_orders_render("Orders\n 1. warp 3 4 7 9 3\n 2. move +0 +6 (4 cells/turn)\n", orders, 4)
    t("orders render: orders out of order fail", any("in order" in m for m in r), r)
    hole_hz, star_hz = variants["hole"]["ships"][variants["ship_id"]]["hazard"], variants["star"]["ships"][variants["ship_id"]]["hazard"]
    r = C.check_hazard_render(_fake_hazard_probe(variants), glyph, hole_hz, star_hz, "black hole")
    t("hazard render: a faithful T2/T4/T3 passes smoke/22", not r, r)
    for kw, word in (("no_label", "GRD-03"), ("no_tint", "tinted"), ("no_panel", "PNL-06"), ("no_banner", "GAME OVER"), ("no_cause", "cause")):
        r = C.check_hazard_render(_fake_hazard_probe(variants, **{kw: True}), glyph, hole_hz, star_hz, "black hole")
        t(f"hazard render: {kw} fails naming {word}", any(word in m for m in r), r)
    t("ansi_cells: styles are tracked per character and reset",
      C.ansi_cells("a\x1b[31mb\x1b[0mc")[0] == [("a", ""), ("b", "31"), ("c", "")])

    # 18. gate level, end to end: the new rows SKIP at MILESTONE M1 and FAIL (not crash) at M2 against the M1 game
    if (root / "starbour" / "__init__.py").exists():
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td) / "repo"
            shutil.copytree(root / "starbour", tmp / "starbour", ignore=shutil.ignore_patterns("__pycache__"))
            (tmp / "specs").mkdir()
            shutil.copy2(root / "controls_config.ini", tmp / "controls_config.ini")
            sel = ",".join(sorted(M2_CODES))
            for milestone, want in (("M1", C.SKIP), ("M2", C.FAIL)):
                (tmp / "specs" / "MILESTONE").write_text(milestone + "\n", encoding="utf-8")
                res = C.run_cmd([sys.executable, str(HERE / "smoke_test.py"), str(tmp), "--select", sel], tmp, 300.0)
                out = res.stdout + res.stderr
                statuses = {}
                for ln in out.splitlines():
                    parts = ln.split()
                    if len(parts) >= 2 and parts[1] in M2_CODES and parts[0] in (C.PASS, C.FAIL, C.SKIP, C.BLOCKED):
                        statuses[parts[1]] = parts[0]
                ok = len(statuses) == len(M2_CODES) and all(s == want for s in statuses.values()) and "unexpected exception inside the gate" not in out
                ok = ok and (res.returncode == (0 if want == C.SKIP else 1))
                t(f"gate: with specs/MILESTONE = {milestone} the M1 game makes smoke/13–22 all {want} (exit {res.returncode}, {res.seconds:.1f} s)", ok,
                  f"statuses {statuses}; tail:\n{res.tail(20)}")
                if want == C.FAIL:
                    line = next((ln for ln in out.splitlines() if "smoke/15" in ln), "")
                    if line:
                        print(f"      gate line: {line.strip()[:200]}")
    else:
        print("note: no starbour package here — the gate-level M1/M2 run is skipped")

    bad = [n for ok, n in results if not ok]
    print(f"== smoke selftest: {len(results) - len(bad)} ok, {len(bad)} failed ==")
    return 1 if bad else 0


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".", help="repo root (default: current directory)")
    ap.add_argument("--quick", action="store_true", help="hook tier: smoke/00, 01 (not slow), 02–04 with --turns 10, 10")
    ap.add_argument("--selftest", action="store_true", help="prove the checkers discriminate good from broken fixtures")
    ap.add_argument("--select", default=None, help=argparse.SUPPRESS)        # comma-separated codes (selftest use)
    ap.add_argument("--_render-probe", nargs=2, metavar=("IN", "OUT"), help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if a._render_probe:
        return render_probe(Path(a._render_probe[0]), Path(a._render_probe[1]))
    root = Path(a.root).resolve()
    if a.selftest:
        try:
            return selftest(HERE.parent)   # the selftest always exercises the kit this file lives in, whatever the cwd
        except C.HarnessError as e:
            print(f"HARNESS ERROR: {e}")
            return 3
    if not root.is_dir():
        print(f"HARNESS ERROR: repo root {root} is not a directory")
        return 3
    ctx = Ctx(root=root, quick=a.quick, py=sys.executable, art=root / "verify" / "artifacts" / "smoke")
    try:
        ctx.milestone = C.read_milestone(root)
    except C.HarnessError:
        pass                                    # smoke/00 reports it
    select = {s.strip() for s in a.select.split(",") if s.strip()} if a.select else None
    return run_gate(ctx, select)


if __name__ == "__main__":
    sys.exit(main())
