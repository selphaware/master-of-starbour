"""verify/smoke_test.py — gate 1: headless, no windows, random ports.

    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\smoke_test.py .            full  (≤ 90 s)
    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\smoke_test.py . --quick    hook  (≤ 15 s)
    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\smoke_test.py --selftest   prove the checkers discriminate

Checks smoke/00…12 per docs/BOOTSTRAP.md § Gate 1, driving only the interfaces in
docs/CONTRACTS.md (C2 sim + report, C3 bus, C4 render, C6 save).  Exit 0 pass, 1 fail,
3 HARNESS ERROR (environment, not code).  Checks a milestone has not reached print SKIP;
checks whose prerequisite failed print BLOCKED.  Output goes to verify/artifacts/smoke/.
"""
from __future__ import annotations

import argparse
import configparser
import importlib
import io
import json
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

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _checks as C  # noqa: E402

QUICK_BUDGET_S = 15.0
FULL_BUDGET_S = 90.0
SIM_BOUND_S = 20.0            # C2: 30 turns at default sizes
LONG_RUN_BOUND_S = 120.0      # smoke/12
QUICK_CODES = {"smoke/00", "smoke/01", "smoke/02", "smoke/03", "smoke/04", "smoke/10"}
RENDER_ROLES = ("grid", "output", "status", "command")
CONFIG_LOADERS = ("load_config", "load", "GameConfig.load", "GameConfig.from_ini", "GameConfig.from_file", "GameConfig.from_path")


def utf8_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


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


# --------------------------------------------------------------------------- helpers

def pkg_missing_message(root: Path) -> str | None:
    init, main = root / "starbour" / "__init__.py", root / "starbour" / "__main__.py"
    if not init.exists():
        return f"starbour package not found at {init} — M1 not built yet (specs/M1.md)"
    if not main.exists():
        return f"starbour package has no __main__.py at {main} — `python -m starbour` cannot run (CONTRACTS C1)"
    return None


def ini_values(cp: configparser.ConfigParser) -> dict:
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
    }


def run_sim(ctx: Ctx, *, seed: int, turns: int, report: Path, save_dir: Path, ai_player: bool = True,
            config: Path | None = None, timeout: float) -> C.CmdResult:
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


# --------------------------------------------------------------------------- checks

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
        problems.append(f"the gate cannot read the [supplies]/[ui]/[bus] keys it needs: {e}")
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
    """smoke/02 — turns advance (OV-02, TP-01…08)."""
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
    problems += C.check_turns_advance(doc, turns)
    if not problems:
        ctx.report_main, ctx.save_dir_main = doc, save_dir
    return problems


def c03(ctx: Ctx) -> list[str]:
    """smoke/03 — the AI acts (OV-10, SP-06)."""
    return C.check_ai_acts(need_main_report(ctx))


def c04(ctx: Ctx) -> list[str]:
    """smoke/04 — the snapshot changes every turn (BUS-07)."""
    return C.check_hashes_differ(need_main_report(ctx))


def c05(ctx: Ctx) -> list[str]:
    """smoke/05 — upkeep wired to config (SUP-01, SUP-02, CFG-45/46)."""
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
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cp.read(ctx.root / "controls_config.ini", encoding="utf-8")
    cp.set("supplies", "food_start", "12")
    tmp_ini = ctx.art / "cfg12.ini"
    with open(tmp_ini, "w", encoding="utf-8") as f:
        cp.write(f)
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
    need_main_report(ctx)
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
        raise C.Blocked(f"no save file to take a snapshot from ({err}); smoke/11 reports the autosave")
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
        return [f"{save} does not exist after the run — autosave at TP-08 every turn into --save-dir (OV-12, C6)"]
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
    """smoke/12 — a long AI-only run is robust (D-69, Chat A15)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    report = ctx.art / "report500.json"
    res = run_sim(ctx, seed=7, turns=500, report=report, save_dir=ctx.art / "saves500", timeout=LONG_RUN_BOUND_S + 15)
    if res.timed_out:
        return [f"sim --seed 7 --turns 500 did not finish within {LONG_RUN_BOUND_S + 15:.0f} s and was killed (bound {LONG_RUN_BOUND_S:.0f} s)"]
    doc, _ = C.load_json(report)
    return C.check_long_run(doc, res.returncode, res.seconds, LONG_RUN_BOUND_S, res.stderr, 500)


CHECKS = [
    C.Check("smoke/00", "harness: interpreter, deps, docs current, ini parses", "CFG-*, D-53, D-74", c00),
    C.Check("smoke/01", "unit tests", "specs/M1.md § 3", c01),
    C.Check("smoke/02", "turns advance", "OV-02, TP-01…08, C2", c02),
    C.Check("smoke/03", "AI acts", "OV-10, SP-06", c03),
    C.Check("smoke/04", "snapshot changes every turn", "BUS-07", c04),
    C.Check("smoke/05", "upkeep wired to config", "SUP-01, SUP-02, CFG-45/46", c05),
    C.Check("smoke/06", "mutiny fires", "SUP-03, SUP-04b, SUP-04c, CFG-47/48, D-76", c06),
    C.Check("smoke/07", "determinism", "D-67, SP-01", c07),
    C.Check("smoke/08", "config key honoured", "CFG-45, D-49", c08),
    C.Check("smoke/09", "bus wiring", "BUS-03/04, MSG-01…07, C3", c09),
    C.Check("smoke/10", "render freshness", "C4, BUS-05, GRD-06", c10),
    C.Check("smoke/11", "autosave round trip", "OV-12, C6", c11),
    C.Check("smoke/12", "long run robustness", "D-69, Chat A15", c12),
]


def run_gate(ctx: Ctx) -> int:
    ctx.art.mkdir(parents=True, exist_ok=True)
    label = f"smoke gate ({'quick' if ctx.quick else 'full'}) — root {ctx.root}"
    if ctx.quick:
        print("quick mode: smoke/05–09, 11, 12 are not run (full gate: smoke_test.py . without --quick)")
    return C.run_checks(CHECKS, ctx, milestone=lambda: ctx.milestone, select=QUICK_CODES if ctx.quick else None,
                        budget_s=QUICK_BUDGET_S if ctx.quick else FULL_BUDGET_S, label=label,
                        result_path=ctx.art / "last_run.json")


# --------------------------------------------------------------------------- render probe (subprocess of smoke/10)

def render_probe(in_path: Path, out_path: Path) -> int:
    """Import starbour.terminals.render_<role>, load GameConfig, render the given inputs to plain
    text with Rich.  Runs in its own interpreter so 'does the render module import zmq'
    is observable and a hung render cannot stall the gate."""
    spec = json.loads(in_path.read_text(encoding="utf-8"))
    sys.path.insert(0, spec["root"])
    zmq_before = "zmq" in sys.modules
    out: dict = {"renders": {}, "errors": {}, "import_errors": {}, "config_loader": None,
                 "loader_candidates": [f"starbour.config.{n}" for n in CONFIG_LOADERS], "zmq_imported": False}
    mods = {}
    for role in RENDER_ROLES:
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
            texts = []
            try:
                for inp in inputs:
                    renderable = mod.render(inp, spec["view"], cfg, spec["width"])
                    con = Console(file=io.StringIO(), width=spec["width"], record=True, force_terminal=False,
                                  color_system=None, legacy_windows=False, highlight=False)
                    con.print(renderable)
                    texts.append(con.export_text(styles=False))
                out["renders"][role] = texts
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


def selftest(root: Path) -> int:
    fx = HERE / "fixtures"
    sys.path.insert(0, str(fx))
    from make_fixtures import FIXTURE_CFG  # noqa: E402
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
    return run_gate(ctx)


if __name__ == "__main__":
    sys.exit(main())
