"""verify/live_test.py — gate 2: the real launcher, real windows, default ports.

    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\live_test.py .            milestone gate (≤ 3 min)
    E:\\CODE\\master-of-starbour\\MOS\\Scripts\\python.exe verify\\live_test.py --selftest   stub launcher; opens no window

Checks live/01…07 per docs/BOOTSTRAP.md § Gate 2, live/08 per specs/M2.md § 3 and live/09 per
specs/M3.md § 4 against docs/CONTRACTS.md (C1 command lines, C3 bus, C5 heartbeats, C6 save,
C9 M2 additions, C10 M3 additions).
Windows open and close on the desktop.  Not run by the hook.  Any existing saves\\game.json
is moved aside first and restored afterwards; the gate's own save is deleted.  On any exit
every process the gate started is killed.  Exit 0 pass, 1 fail, 3 HARNESS ERROR.
"""
from __future__ import annotations

import argparse
import configparser
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import _checks as C  # noqa: E402
import _combat as CB  # noqa: E402

BUDGET_S = 180.0
LAUNCH_WAIT_S = 15.0
HB_WAIT_S = 3.0
REOPEN_WAIT_S = 5.0
SHUTDOWN_WAIT_S = 10.0
LIVE_SEED = 1234


def utf8_console() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


@dataclass
class Ctx:
    root: Path
    py: str
    art: Path
    hb_dir: Path
    milestone: str = "M1"
    pub_port: int = 5555
    pull_port: int = 5556
    launcher: subprocess.Popen | None = None
    launcher_log: Path | None = None
    pids: dict[str, int] = field(default_factory=dict)
    last_turn: int | None = None
    pkg_missing: str | None = None
    save_backup: Path | None = None
    started: list[int] = field(default_factory=list)   # launcher pids the gate started


# --------------------------------------------------------------------------- process helpers

def launcher_children(ctx: Ctx) -> dict[str, list[int]]:
    """{role: [pids]} among the launcher's descendants, matched on cmdline (--role + repo path)."""
    import psutil
    found: dict[str, list[int]] = {r: [] for r in C.ROLES}
    if ctx.launcher is None:
        return found
    try:
        kids = psutil.Process(ctx.launcher.pid).children(recursive=True)
    except psutil.NoSuchProcess:
        return found
    for k in kids:
        try:
            role = C.cmdline_matches_role(k.cmdline(), ctx.root)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        if role:
            found.setdefault(role, []).append(k.pid)
    return found


def launcher_log_tail(ctx: Ctx, n: int = 12) -> str:
    if ctx.launcher_log and ctx.launcher_log.exists():
        lines = ctx.launcher_log.read_text(encoding="utf-8", errors="replace").splitlines()
        return "\n".join(lines[-n:])
    return ""


def launch(ctx: Ctx, cmd: list[str], label: str) -> list[str]:
    """Start the launcher and wait for one child per role.  Returns problems; sets ctx.pids."""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8", MOS_HEARTBEAT_DIR=str(ctx.hb_dir))
    for f in ctx.hb_dir.glob("*.json"):
        f.unlink()
    ctx.launcher_log = ctx.art / f"launcher_{label}.log"
    lf = open(ctx.launcher_log, "w", encoding="utf-8")
    ctx.launcher = subprocess.Popen(cmd, cwd=str(ctx.root), env=env, stdout=lf, stderr=subprocess.STDOUT,
                                    creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
    ctx.started.append(ctx.launcher.pid)
    t0 = time.perf_counter()
    found: dict[str, list[int]] = {}

    def all_present() -> bool:
        nonlocal found
        found = launcher_children(ctx)
        return all(found.get(r) for r in C.ROLES) or ctx.launcher.poll() is not None
    waited = C.wait_until(all_present, LAUNCH_WAIT_S, 0.25)
    problems: list[str] = []
    if ctx.launcher.poll() is not None:
        problems.append(f"the launcher exited {ctx.launcher.returncode} after {waited:.1f} s (cmd {' '.join(cmd)}); log tail:\n"
                        + textwrap.indent(launcher_log_tail(ctx), " " * 10))
    problems += C.check_roles_present(found)
    if problems and not any("launcher exited" in p for p in problems):
        problems[-1] += f" — waited {waited:.1f} s of {LAUNCH_WAIT_S:.0f} s"
    ctx.pids = {r: pids[0] for r, pids in found.items() if pids}
    if not problems:
        print(f"         launcher pid {ctx.launcher.pid}; children {ctx.pids} up after {waited:.1f} s")
    return problems


def stop_everything(ctx: Ctx) -> list[str]:
    """Kill the launcher tree and any process of ours still alive; return what had to be killed."""
    killed = []
    for pid in ctx.started:
        try:
            import psutil
            if psutil.pid_exists(pid):
                killed.append(f"launcher {pid}")
                C.kill_tree(pid)
        except Exception:  # noqa: BLE001
            pass
    for pid, role, _ in C.role_processes(ctx.root):
        killed.append(f"{role} {pid}")
        C.kill_tree(pid)
    return killed


def kill_role(ctx: Ctx, role: str) -> None:
    import psutil
    try:
        psutil.Process(ctx.pids[role]).kill()
    except (KeyError, psutil.NoSuchProcess):
        pass


# --------------------------------------------------------------------------- checks

def l01(ctx: Ctx) -> list[str]:
    """live/01 — ports free before start (BUS-06)."""
    ctx.milestone = C.read_milestone(ctx.root)
    exe = Path(sys.executable).resolve()
    if os.path.normcase(str(exe)) != os.path.normcase(str(C.EXPECTED_PY.resolve())):
        raise C.HarnessError(f"interpreter is {exe}, expected {C.EXPECTED_PY}")
    try:
        import psutil  # noqa: F401
        import zmq  # noqa: F401
    except ImportError as e:
        raise C.HarnessError(f"missing dependency in the MOS venv: {e}") from e
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cp.read(ctx.root / "controls_config.ini", encoding="utf-8")
    ctx.pub_port, ctx.pull_port = cp.getint("bus", "pub_port"), cp.getint("bus", "pull_port")
    for port in (ctx.pub_port, ctx.pull_port):
        pid = C.port_holder(port)
        if pid is not None:
            desc = f"pid {pid}"
            try:
                import psutil
                p = psutil.Process(pid)
                desc += f" ({p.name()}: {' '.join(p.cmdline())[:120]})"
            except Exception:  # noqa: BLE001
                pass
            raise C.HarnessError(f"port {port} is already in use by {desc} — a stale engine from a previous run; stop it, then re-run "
                                 f"(this is live/05's finding if the previous run was the game's: BUS-06)")
    return []


def l02(ctx: Ctx) -> list[str]:
    """live/02 — python -m starbour spawns engine + four terminals within 15 s (TL, BR-15)."""
    init = ctx.root / "starbour" / "__init__.py"
    if not init.exists():
        ctx.pkg_missing = f"starbour package not found at {init} — M1 not built yet (specs/M1.md)"
        return [ctx.pkg_missing]
    save = ctx.root / "saves" / "game.json"
    if save.exists():
        ctx.save_backup = ctx.art / "game.json.before-live"
        shutil.move(str(save), str(ctx.save_backup))
        print(f"         moved existing {save} aside to {ctx.save_backup} (restored at the end)")
    return launch(ctx, [ctx.py, "-m", "starbour", "--seed", str(LIVE_SEED)], "first")


def l03(ctx: Ctx) -> list[str]:
    """live/03 — hello, three turns, every window draws turn 3 within 3 s (BUS-05, D-66, C5)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    if ctx.launcher is None or ctx.launcher.poll() is not None or len(ctx.pids) < len(C.ROLES):
        raise C.Blocked("live/02 did not leave a running launcher with all five roles")
    obs = C.drive_engine(ctx.pub_port, ctx.pull_port, turns=3, hello_timeout=15.0, turn_timeout=10.0, orders=False)
    problems = C.check_bus_observations(obs, turns_expected=3, need_orders=False)
    if not obs.resolved:
        return problems
    last = obs.resolved[-1].get("turn_no")
    if not isinstance(last, int):
        return problems + ["turn.resolved carries no integer turn_no (MSG-03)"]
    ctx.last_turn = last
    view_pids = {r: ctx.pids[r] for r in C.VIEW_ROLES if r in ctx.pids}
    waited = C.wait_until(lambda: not C.check_heartbeats(C.read_heartbeats(ctx.hb_dir), last, pids=view_pids), HB_WAIT_S, 0.1)
    hbs = C.read_heartbeats(ctx.hb_dir)
    hb_problems = C.check_heartbeats(hbs, last, pids=view_pids)
    if hb_problems:
        hb_problems[-1] += f" — waited {waited:.1f} s of {HB_WAIT_S:.0f} s after the third turn.resolved"
    else:
        ms = {r: (hbs[r] or {}).get("render_ms") for r in C.VIEW_ROLES}
        print(f"         hello after {obs.hello_wait_s:.2f} s; turn.resolved waits {[round(w, 2) for w in obs.resolved_wait_s]} s; "
              f"heartbeats at turn {last} after {waited:.2f} s; render_ms {ms}")
    return problems + hb_problems


def l04(ctx: Ctx) -> list[str]:
    """live/04 — killing T2 reopens it and it draws the current turn (TL)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    if ctx.last_turn is None or "grid" not in ctx.pids:
        raise C.Blocked("live/03 did not establish a current turn and a grid pid")
    import psutil
    old = ctx.pids["grid"]
    try:
        psutil.Process(old).kill()
    except psutil.NoSuchProcess:
        pass
    new_pid: int | None = None

    def reopened() -> bool:
        nonlocal new_pid
        pids = [p for p in launcher_children(ctx).get("grid", []) if p != old and psutil.pid_exists(p)]
        new_pid = pids[0] if pids else None
        return new_pid is not None
    waited = C.wait_until(reopened, REOPEN_WAIT_S, 0.2)
    if new_pid is None:
        return [f"no new --role grid child within {REOPEN_WAIT_S:.0f} s of killing pid {old} — the launcher must respawn T2/T3/T4 (TL)"]
    ctx.pids["grid"] = new_pid
    hb_wait = C.wait_until(lambda: not C.check_heartbeats(C.read_heartbeats(ctx.hb_dir), ctx.last_turn, roles=("grid",), pids={"grid": new_pid}),
                           REOPEN_WAIT_S, 0.1)
    problems = C.check_heartbeats(C.read_heartbeats(ctx.hb_dir), ctx.last_turn, roles=("grid",), pids={"grid": new_pid})
    if problems:
        problems[-1] += (f" — the reopened grid (pid {new_pid}, up after {waited:.1f} s) did not draw turn {ctx.last_turn} within "
                         f"{REOPEN_WAIT_S:.0f} s; it needs state.hello every 2 s to draw without waiting for a turn (D-70)")
    else:
        print(f"         grid respawned as pid {new_pid} after {waited:.1f} s; drew turn {ctx.last_turn} after {hb_wait:.1f} s more")
    return problems


def l05(ctx: Ctx) -> list[str]:
    """live/05 — killing T1 ends the game: autosave, no orphans (BUS-06, OV-12)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    if ctx.launcher is None or ctx.launcher.poll() is not None or "command" not in ctx.pids:
        raise C.Blocked("no running launcher with a command terminal")
    kill_role(ctx, "command")
    waited = C.wait_until(lambda: ctx.launcher.poll() is not None and not C.role_processes(ctx.root), SHUTDOWN_WAIT_S, 0.2)
    problems: list[str] = []
    if ctx.launcher.poll() is None:
        problems.append(f"the launcher is still running {SHUTDOWN_WAIT_S:.0f} s after T1 was killed — closing T1 must end the game (TL, BUS-06)")
    remaining = [(pid, role) for pid, role, _ in C.role_processes(ctx.root)]
    problems += C.check_all_gone(remaining, f"{SHUTDOWN_WAIT_S:.0f} s after T1 was killed (orphans; BUS-06, D-59)")
    save = ctx.root / "saves" / "game.json"
    if not save.exists():
        problems.append(f"{save} does not exist after shutdown — autosave every turn and on shutdown (OV-12, C6)")
    if problems:
        killed = stop_everything(ctx)
        if killed:
            problems.append("the gate had to kill: " + ", ".join(killed))
    else:
        print(f"         launcher and all children gone {waited:.1f} s after killing T1; save present")
    return problems


def l06(ctx: Ctx) -> list[str]:
    """live/06 — terminating the launcher leaves nothing behind (BUS-06, D-59)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    problems = launch(ctx, [ctx.py, "-m", "starbour", "--seed", str(LIVE_SEED)], "second")
    if problems:
        stop_everything(ctx)
        return problems
    time.sleep(1.0)
    ctx.launcher.terminate()
    waited = C.wait_until(lambda: not C.role_processes(ctx.root), SHUTDOWN_WAIT_S, 0.2)
    remaining = [(pid, role) for pid, role, _ in C.role_processes(ctx.root)]
    problems = C.check_all_gone(remaining, f"{SHUTDOWN_WAIT_S:.0f} s after the launcher was terminated (children must watch MOS_LAUNCHER_PID; D-59)")
    for port in (ctx.pub_port, ctx.pull_port):
        if C.port_holder(port) is not None:
            problems.append(f"port {port} is still held after shutdown — the engine must close its sockets (LINGER 0) and exit (BUS-06)")
    if problems:
        killed = stop_everything(ctx)
        if killed:
            problems.append("the gate had to kill: " + ", ".join(killed))
    else:
        print(f"         all children gone {waited:.1f} s after terminating the launcher; ports free")
    return problems


def l07(ctx: Ctx) -> list[str]:
    """live/07 — (M5) the same against dist\\starbour\\starbour.exe; launcher alive after 10 s (BR-14/15)."""
    exe = ctx.root / "dist" / "starbour" / "starbour.exe"
    if not exe.exists():
        return [f"{exe} not found — build it with build.ps1 (BR-14)"]
    problems = launch(ctx, [str(exe), "--seed", str(LIVE_SEED)], "exe")
    if problems:
        stop_everything(ctx)
        return problems
    time.sleep(10.0)
    if ctx.launcher.poll() is not None:
        problems.append(f"starbour.exe exited {ctx.launcher.returncode} within 10 s of start — 'the exe opens and exits' (BR-15)")
    found = launcher_children(ctx)
    problems += C.check_roles_present(found)
    ctx.launcher.terminate()
    C.wait_until(lambda: not C.role_processes(ctx.root), SHUTDOWN_WAIT_S, 0.2)
    problems += C.check_all_gone([(pid, role) for pid, role, _ in C.role_processes(ctx.root)], "after terminating starbour.exe")
    stop_everything(ctx)
    return problems


def windows_at_turn(hb_dir: Path, turn_no: int, pids: dict[str, int], wait_s: float = HB_WAIT_S) -> dict:
    """Wait up to `wait_s` for every view window's heartbeat to read `turn_no` (C5, BUS-05); return what was seen.
    live/08 calls this from the driver's `after_over` hook, i.e. before 'new' is pushed (specs/M2.md § 3 order)."""
    waited = C.wait_until(lambda: not C.check_heartbeats(C.read_heartbeats(hb_dir), turn_no, pids=pids), wait_s, 0.1)
    hbs = C.read_heartbeats(hb_dir)
    return {"turn_no": turn_no, "waited_s": round(waited, 2), "wait_s": wait_s,
            "problems": C.check_heartbeats(hbs, turn_no, pids=pids),
            "render_ms": {r: (hbs[r] or {}).get("render_ms") for r in C.VIEW_ROLES}}


def windows_problems(seen: dict | None, turn_no: int) -> list[str]:
    """live/08 — the heartbeat verdict taken at game.over, before 'new' was pushed (OUT-16, BUS-05, C5)."""
    if not seen:                       # game.over never arrived: check_game_over already names that
        return []
    hb = list(seen.get("problems") or [])
    if hb:
        hb[-1] += (f" — waited {seen.get('waited_s', 0.0):.1f} s of {seen.get('wait_s', HB_WAIT_S):.0f} s after game.over at turn {turn_no}, "
                   f"before 'new' was pushed; every window redraws turn.resolved {turn_no} with the game-over banner (OUT-16, BUS-05, C5)")
    return hb


def l08(ctx: Ctx) -> list[str]:
    """live/08 — (M2) end-to-end game over: launch teleported into a star, N turns → game.over, every
    window at turn N, save deleted, 'new' → hello at turn 0, closing T1 leaves nothing (OV-09b, MSG-09, OUT-16, C9)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    if C.role_processes(ctx.root):
        stop_everything(ctx)
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cp.read(ctx.root / "controls_config.ini", encoding="utf-8")
    k_g, falloff, scale = cp.getfloat("hazards", "k_g"), cp.getfloat("hazards", "falloff"), cp.getfloat("hazards", "pull_turns_scale")
    sw, sh = (int(v) for v in cp.get("world", "sector_size").lower().split("x"))
    # the star cell comes from world_index (C9), never from a literal
    report, save_dir = ctx.art / f"world_{LIVE_SEED}.json", ctx.art / f"saves_world_{LIVE_SEED}"
    if save_dir.exists():
        shutil.rmtree(save_dir, ignore_errors=True)
    what = f"sim --seed {LIVE_SEED} --turns 1 --ai-player --world-index"
    res = C.run_cmd([ctx.py, "-m", "starbour", "sim", "--seed", str(LIVE_SEED), "--turns", "1", "--ai-player", "--world-index",
                     "--report", str(report), "--save-dir", str(save_dir)], ctx.root, 40.0)
    if res.timed_out or res.returncode != 0:
        return [f"{what} exited {res.returncode}: {res.tail(8)} — live/08 needs world_index to pick a star cell (C9, D-104)"]
    doc, err = C.load_json(report)
    index = doc.get("world_index") if isinstance(doc, dict) else None
    errs = C.world_index_shape_errors(index)
    if errs:
        return [f"{what}: " + "; ".join(errs[:4])]
    pick = C.pick_star(index, k_g=k_g, falloff=falloff, scale=scale, sector_size=(sw, sh))
    if pick is None:
        return [f"world_index for seed {LIVE_SEED} lists no medium-bucket star with a free neighbouring cell among its {len(index.get('stars') or [])} stars (C9)"]
    star, _ = pick
    pull = C.pull_at(float(star["mass"]), 0, k_g, falloff)
    n = C.countdown_turns(pull, scale)
    cell = C.teleport_arg(star["galaxy"], star["sector"], star["cell"])
    # a fresh game: the gate's own save from live/05 goes (the user's original is restored at the end)
    save = ctx.root / "saves" / "game.json"
    if save.exists():
        shutil.copy2(save, ctx.art / "game.json.before-live08")
        save.unlink()
    problems = launch(ctx, [ctx.py, "-m", "starbour", "--seed", str(LIVE_SEED), "--teleport", cell], "gameover")
    if problems:
        problems.append("the launcher must accept --teleport and pass it to the engine role, as it does --seed (C9 C1 additions, D-104)")
        stop_everything(ctx)
        return problems
    # Order per specs/M2.md § 3: game.over → every window at turn N → save deleted → 'new' → hello at turn 0 → close T1.
    # The heartbeats are read inside the driver the moment game.over arrives, before it pushes 'new': afterwards the
    # windows redraw the new game at turn 0 within milliseconds and a check made here would race them (§ 7 gate note).
    view_pids = {r: ctx.pids[r] for r in C.VIEW_ROLES if r in ctx.pids}
    obs = C.drive_game_over(ctx.pub_port, ctx.pull_port, turns=n, save_path=save, order_after_over=False, new_game=True, turn_after_new=False,
                            hello_timeout=15.0, turn_timeout=10.0, after_over=lambda _obs: windows_at_turn(ctx.hb_dir, n, view_pids))
    problems = C.check_game_over(obs, n, order_after_over=False, new_game=True, turn_after_new=False)
    problems += windows_problems(obs.get("after_over"), n)
    kill_role(ctx, "command")
    waited = C.wait_until(lambda: ctx.launcher.poll() is not None and not C.role_processes(ctx.root), SHUTDOWN_WAIT_S, 0.2)
    if ctx.launcher.poll() is None:
        problems.append(f"the launcher is still running {SHUTDOWN_WAIT_S:.0f} s after T1 was killed after game over — closing T1 must end the game (TL, BUS-06)")
    problems += C.check_all_gone([(pid, role) for pid, role, _ in C.role_processes(ctx.root)],
                                 f"{SHUTDOWN_WAIT_S:.0f} s after T1 was killed after game over (orphans; BUS-06, D-59)")
    if problems:
        killed = stop_everything(ctx)
        if killed:
            problems.append("the gate had to kill: " + ", ".join(killed))
        tail = launcher_log_tail(ctx)
        if tail.strip():
            problems.append("launcher log tail:\n" + textwrap.indent(tail, " " * 10))
    else:
        seen = obs.get("after_over") or {}
        print(f"         star at {star['cell']} in {star['galaxy']}/{star['sector']} (mass {float(star['mass']):.3f}, pull {pull:.3f}, N = {n}); "
              f"game.over after {obs.get('game_over_wait_s')} s; windows at turn {n} after {seen.get('waited_s')} s, read before 'new' "
              f"(render_ms {seen.get('render_ms')}); save deleted; new → seed {(obs.get('hello_after_new') or {}).get('seed')} at turn 0; "
              f"everything gone {waited:.1f} s after killing T1")
    return problems


def status_in_battle(hb_dir: Path) -> Any:
    """The status window's heartbeat `in_battle` field (C10 / live/09, [ASSUMED] C5 addition), '<missing>' when absent."""
    doc, _ = C.load_json(hb_dir / "status.json")
    return doc.get("in_battle", "<missing>") if isinstance(doc, dict) else "<no heartbeat>"


def l09(ctx: Ctx) -> list[str]:
    """live/09 — (M3) end-to-end combat: launch with --spawn and --ai passive, push `fire phaser <id>`, two turns →
    combat events, every window at the current turn, T4's heartbeat says in_battle, closing T1 leaves nothing
    (CM-01, OUT-04, BUS-06, C10)."""
    if ctx.pkg_missing:
        raise C.Blocked(ctx.pkg_missing)
    if C.role_processes(ctx.root):
        stop_everything(ctx)
    cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=None)
    cp.read(ctx.root / "controls_config.ini", encoding="utf-8")
    sw, sh = (int(v) for v in cp.get("world", "sector_size").lower().split("x"))
    report, save_dir = ctx.art / f"world_{LIVE_SEED}.json", ctx.art / f"saves_world_{LIVE_SEED}"
    if save_dir.exists():
        shutil.rmtree(save_dir, ignore_errors=True)
    what = f"sim --seed {LIVE_SEED} --turns 1 --ai-player --world-index"
    res = C.run_cmd([ctx.py, "-m", "starbour", "sim", "--seed", str(LIVE_SEED), "--turns", "1", "--ai-player", "--world-index",
                     "--report", str(report), "--save-dir", str(save_dir)], ctx.root, 40.0)
    if res.timed_out or res.returncode != 0:
        return [f"{what} exited {res.returncode}: {res.tail(8)} — live/09 needs world_index to pick the spawn cell (C9, D-104)"]
    doc, err = C.load_json(report)
    index = doc.get("world_index") if isinstance(doc, dict) else None
    errs = C.world_index_shape_errors(index)
    if errs:
        return [f"{what}: " + "; ".join(errs[:4])]
    (gx, gy), (sx, sy), (cx, cy) = index["player_start"]
    blocked = C.terrain_cells(index, (gx, gy), (sx, sy))
    cell = next(((cx + dx, cy + dy) for dx, dy in C.NEIGHBOURS if C.in_sector((cx + dx, cy + dy), (sw, sh)) and (cx + dx, cy + dy) not in blocked), None)
    if cell is None:
        return [f"no free cell next to the player's start {cx},{cy} for seed {LIVE_SEED} (world_index; gate logic)"]
    spawn = CB.spawn_arg("kestrel", (gx, gy), (sx, sy), cell, 0, 10)
    save = ctx.root / "saves" / "game.json"
    if save.exists():
        shutil.copy2(save, ctx.art / "game.json.before-live09")
        save.unlink()
    problems = launch(ctx, [ctx.py, "-m", "starbour", "--seed", str(LIVE_SEED), "--spawn", spawn, "--ai", "passive"], "combat")
    if problems:
        problems.append("the launcher must accept --spawn and --ai passive and pass them to the engine role, as it does --seed and --teleport (C10 C1 additions, D-125, D-126)")
        stop_everything(ctx)
        return problems
    view_pids = {r: ctx.pids[r] for r in C.VIEW_ROLES if r in ctx.pids}

    def after_first(obs: dict) -> dict:
        n = obs["resolved"][-1]["turn_no"]
        seen = windows_at_turn(ctx.hb_dir, n, view_pids)
        C.wait_until(lambda: status_in_battle(ctx.hb_dir) is True, HB_WAIT_S, 0.1)
        seen["in_battle"] = status_in_battle(ctx.hb_dir)
        return seen
    obs = CB.drive_combat(ctx.pub_port, ctx.pull_port, spawn_cell=cell, turns=2, hello_timeout=15.0, turn_timeout=10.0, after_first=after_first)
    problems = CB.check_combat_bus(obs, turns=2)
    seen = obs.get("after_first") or {}
    if seen:
        hb = list(seen.get("problems") or [])
        if hb:
            hb[-1] += f" — waited {seen.get('waited_s', 0.0):.1f} s of {seen.get('wait_s', HB_WAIT_S):.0f} s after the first turn.resolved (BUS-05, C5)"
        problems += hb
        if seen.get("in_battle") is not True:
            problems.append(f"T4's heartbeat in_battle = {seen.get('in_battle')!r}, expected true while the player's ship is in a battle "
                            f"(status.json carries in_battle; [ASSUMED] C5 addition, M3 § 4 live/09)")
    kill_role(ctx, "command")
    waited = C.wait_until(lambda: ctx.launcher.poll() is not None and not C.role_processes(ctx.root), SHUTDOWN_WAIT_S, 0.2)
    if ctx.launcher.poll() is None:
        problems.append(f"the launcher is still running {SHUTDOWN_WAIT_S:.0f} s after T1 was killed during a battle — closing T1 must end the game (TL, BUS-06)")
    problems += C.check_all_gone([(pid, role) for pid, role, _ in C.role_processes(ctx.root)],
                                 f"{SHUTDOWN_WAIT_S:.0f} s after T1 was killed during a battle (orphans; BUS-06, D-59)")
    if problems:
        killed = stop_everything(ctx)
        if killed:
            problems.append("the gate had to kill: " + ", ".join(killed))
        tail = launcher_log_tail(ctx)
        if tail.strip():
            problems.append("launcher log tail:\n" + textwrap.indent(tail, " " * 10))
    else:
        first = obs["resolved"][0]
        print(f"         spawn {obs.get('target')} at {list(cell)} next to {cx},{cy}; fire accepted; turn {first['turn_no']} events {first['types']}; "
              f"windows at turn {first['turn_no']} after {seen.get('waited_s')} s, T4 in_battle true; everything gone {waited:.1f} s after killing T1")
    return problems


CHECKS = [
    C.Check("live/01", "ports free before start", "BUS-06", l01),
    C.Check("live/02", "launcher spawns engine + four terminals", "TL, BR-15, C1", l02),
    C.Check("live/03", "three turns; every window draws the current turn", "BUS-05, D-66, C5", l03),
    C.Check("live/04", "closing T2 reopens it, drawn at the current turn", "TL, D-70", l04),
    C.Check("live/05", "closing T1 ends the game: autosave, no orphans", "BUS-06, OV-12", l05),
    C.Check("live/06", "terminating the launcher leaves no children", "BUS-06, D-59", l06),
    C.Check("live/08", "end-to-end game over: teleport into a star, game.over, save deleted, new", "OV-09b, MSG-09, OUT-16, C9", l08, since="M2"),
    C.Check("live/09", "end-to-end combat: spawn, fire, combat events, T4 in_battle, close T1", "CM-01, OUT-04, BUS-06, C10", l09, since="M3"),
    C.Check("live/07", "starbour.exe launches and stays up", "BR-14, BR-15", l07, since="M5"),
]


def run_gate(ctx: Ctx) -> int:
    ctx.art.mkdir(parents=True, exist_ok=True)
    ctx.hb_dir.mkdir(parents=True, exist_ok=True)
    try:
        return C.run_checks(CHECKS, ctx, milestone=lambda: ctx.milestone, select=None, budget_s=BUDGET_S,
                            label=f"live gate — root {ctx.root}", result_path=ctx.art / "last_run.json")
    finally:
        killed = stop_everything(ctx)
        if killed:
            print("cleanup: killed " + ", ".join(killed))
        save = ctx.root / "saves" / "game.json"
        if ctx.save_backup is not None or save.exists():
            try:
                if save.exists():
                    shutil.copy2(save, ctx.art / "game.json.from-live")
                    save.unlink()
                if ctx.save_backup is not None and ctx.save_backup.exists():
                    save.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(ctx.save_backup), str(save))
                    print(f"cleanup: restored the original {save}")
            except OSError as e:
                print(f"cleanup: could not restore {save}: {e}")


# --------------------------------------------------------------------------- selftest

def selftest(root: Path) -> int:
    import psutil
    fx = HERE / "fixtures"
    sys.path.insert(0, str(fx))
    from stub_engine import StubEngine  # noqa: E402
    results: list[tuple[bool, str]] = []

    def t(name: str, ok: bool, detail: object = "") -> None:
        results.append((ok, name))
        print(("ok    " if ok else "FAIL  ") + name + ("" if ok or not detail else f"\n      -> {detail}"))

    print(f"== live selftest — cwd {Path.cwd()} ==")
    # ports
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    holder = C.port_holder(port)
    t(f"ports: port_holder names the listener (pid {os.getpid()}) on {port}", holder == os.getpid(), f"got {holder}")
    s.close()
    time.sleep(0.2)
    t("ports: a released port reads as free", C.port_holder(port) is None)

    # cmdline matcher
    py = str(C.EXPECTED_PY)
    t("matcher: dev terminal cmdline → role", C.cmdline_matches_role([py, "-m", "starbour", "--role", "grid"], root) == "grid")
    t("matcher: frozen exe cmdline → role", C.cmdline_matches_role([r"E:\somewhere\dist\starbour\starbour.exe", "--role", "engine"], root) == "engine")
    t("matcher: a foreign python with --role is ignored", C.cmdline_matches_role([r"C:\other\python.exe", "x.py", "--role", "grid"], root) is None)
    t("matcher: the launcher itself (no --role) is ignored", C.cmdline_matches_role([py, "-m", "starbour"], root) is None)
    t("matcher: role filter", C.cmdline_matches_role([py, "-m", "starbour", "--role", "grid"], root, "status") is None)

    # pure live checkers
    full = {r: [100 + i] for i, r in enumerate(C.ROLES)}
    t("roles: all five present passes", not C.check_roles_present(full))
    t("roles: a missing role fails naming it", any("'status'" in m for m in C.check_roles_present({**full, "status": []})))
    hbs = {r: {"role": r, "turn_no": 3, "rendered_at": 1.0, "pid": 100 + i, "render_ms": 38} for i, r in enumerate(C.VIEW_ROLES)}
    t("heartbeats: current turn passes", not C.check_heartbeats(hbs, 3, pids={r: 100 + i for i, r in enumerate(C.VIEW_ROLES)}))
    stale = {**hbs, "grid": {**hbs["grid"], "turn_no": 2}}
    t("heartbeats: a stale window fails", any("stale" in m for m in C.check_heartbeats(stale, 3)))
    t("heartbeats: a missing file fails", any("no heartbeat" in m for m in C.check_heartbeats({**hbs, "output": None}, 3)))
    t("heartbeats: an old pid fails", any("old process" in m for m in C.check_heartbeats(hbs, 3, pids={"grid": 999})))
    slow = {**hbs, "status": {**hbs["status"], "render_ms": 900}}
    t("heartbeats: render over 0.5 s fails", any("render_ms" in m for m in C.check_heartbeats(slow, 3)))
    t("gone: empty list passes", not C.check_all_gone([], "x"))
    t("gone: survivors fail naming pids", any("pid 42" in m for m in C.check_all_gone([(42, "engine")], "x")))

    # stub launcher: good vs broken orphan behaviour (no window opens)
    stub = fx / "stub_launcher.py"
    for mode, expect_gone in (("good", True), ("broken", False)):
        proc = subprocess.Popen([sys.executable, str(stub), "--mode", mode], cwd=str(root),
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            def kids() -> dict[str, list[int]]:
                out: dict[str, list[int]] = {}
                try:
                    for k in psutil.Process(proc.pid).children(recursive=True):
                        r = C.cmdline_matches_role(k.cmdline(), root)
                        if r:
                            out.setdefault(r, []).append(k.pid)
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass
                return out
            waited = C.wait_until(lambda: len(kids()) == 2, 10.0, 0.2)
            found = kids()
            t(f"stub launcher '{mode}': two --role children found via the matcher ({waited:.1f} s)", len(found) == 2, found)
            child_pids = [p for ps in found.values() for p in ps]
            proc.terminate()
            proc.wait(5)
            t0 = time.perf_counter()
            limit = SHUTDOWN_WAIT_S if expect_gone else 3.0
            gone_after = C.wait_until(lambda: not any(psutil.pid_exists(p) for p in child_pids), limit, 0.1)
            remaining = [(p, "stub") for p in child_pids if psutil.pid_exists(p)]
            problems = C.check_all_gone(remaining, "after terminating the stub launcher")
            if expect_gone:
                t(f"stub launcher 'good': children exit on their own within 2 s of the launcher dying ({gone_after:.2f} s)", not problems and gone_after <= 2.5, problems or f"{gone_after:.2f} s")
            else:
                t(f"stub launcher 'broken': orphans are detected (still alive after {limit:.0f} s)", bool(problems), "no orphan detected — the check cannot discriminate")
        finally:
            C.kill_tree(proc.pid)
            for p in list(child_pids if 'child_pids' in locals() else []):
                try:
                    psutil.Process(p).kill()
                except psutil.NoSuchProcess:
                    pass
    leftovers = [p for p, r, _ in C.role_processes(root) if r.startswith("stub")]
    t("stub launcher: no stub process left behind by the selftest", not leftovers, leftovers)

    # bus client without orders (as live/03 uses it)
    for mode, expect in (("good", None), ("stuck_turn", "not strictly increasing")):
        eng = StubEngine(mode)
        eng.start()
        eng.ready.wait(5)
        try:
            obs = C.drive_engine(eng.pub_port, eng.pull_port, turns=3, hello_timeout=10.0, turn_timeout=1.5, orders=False)
        finally:
            eng.stop()
        problems = C.check_bus_observations(obs, turns_expected=3, need_orders=False)
        if expect is None:
            t(f"bus (no orders): stub engine '{mode}' passes", not problems, problems)
        else:
            t(f"bus (no orders): stub engine '{mode}' fails naming it", any(expect in m for m in problems), problems)

    # ---------------------------------------------------------------- M2: live/08
    print("-- M2 --")
    with tempfile.TemporaryDirectory() as td:
        for mode, expect in (("game_over", None), ("game_over_leaky", ("save", "snapshot.game_over", "C9"))):
            save = Path(td) / f"{mode}.json"
            eng = StubEngine(mode, save_path=save, die_at=7)
            eng.start()
            eng.ready.wait(5)
            try:
                obs = C.drive_game_over(eng.pub_port, eng.pull_port, turns=7, save_path=save, order_after_over=False, new_game=True, turn_after_new=False,
                                        hello_timeout=10.0, turn_timeout=2.0, reply_timeout=2.0, over_timeout=2.0, file_wait=1.0)
            finally:
                eng.stop()
            problems = C.check_game_over(obs, 7, order_after_over=False, new_game=True, turn_after_new=False)
            if expect is None:
                t(f"live/08 driver: stub engine '{mode}' passes (game.over after {obs.get('game_over_wait_s')} s; new → seed {C._get(obs, 'hello_after_new', 'seed')})",
                  not problems, problems)
            else:
                t(f"live/08 driver: stub engine '{mode}' fails naming {expect}", all(any(w in m for m in problems) for w in expect), problems)

    # live/08 order (M2 § 3; § 7 gate note): the heartbeats must be read before 'new' is pushed.  StubWindows redraws
    # like T2/T3/T4 — a heartbeat per turn.resolved and per state.hello (D-70) — so once 'new' is out the files read
    # turn 0 within milliseconds, which is what the gate's old order (check after the driver returned) raced.
    from stub_windows import StubWindows  # noqa: E402
    with tempfile.TemporaryDirectory() as td:
        hb_dir = Path(td) / "hb"
        hb_dir.mkdir()
        save = Path(td) / "game.json"
        my_pids = {r: os.getpid() for r in C.VIEW_ROLES}
        eng = StubEngine("game_over", save_path=save, die_at=7)
        eng.start()
        eng.ready.wait(5)
        win = StubWindows(eng.pub_port, hb_dir)
        win.start()
        win.ready.wait(5)
        new_seen_in_hook: list[bool] = []

        def read_windows(_obs: dict) -> dict:
            new_seen_in_hook.append(any(str(p.get("command")) == "new" for _, p in eng.received))
            return windows_at_turn(hb_dir, 7, my_pids, wait_s=3.0)
        try:
            first_draw = C.wait_until(lambda: bool(win.drawn), 3.0, 0.05)
            t(f"stub windows: drew the stub engine's hello before the driver started ({first_draw:.2f} s)", bool(win.drawn))
            obs = C.drive_game_over(eng.pub_port, eng.pull_port, turns=7, save_path=save, order_after_over=False, new_game=True, turn_after_new=False,
                                    hello_timeout=10.0, turn_timeout=2.0, reply_timeout=2.0, over_timeout=2.0, file_wait=1.0, after_over=read_windows)
            problems = C.check_game_over(obs, 7, order_after_over=False, new_game=True, turn_after_new=False) + windows_problems(obs.get("after_over"), 7)
            seen = obs.get("after_over") or {}
            new_reached = any(str(p.get("command")) == "new" for _, p in eng.received)
            t(f"live/08 order: heartbeats read turn 7 inside the driver before 'new' reached the engine (waited {seen.get('waited_s')} s); "
              f"then new → hello at turn 0 (seed {C._get(obs, 'hello_after_new', 'seed')})",
              not problems and new_seen_in_hook == [False] and new_reached, problems or {"new seen in hook": new_seen_in_hook, "new reached": new_reached})
            # the old order: the same assertion made after the driver has returned, i.e. after 'new' and the hello at turn 0
            drew0 = C.wait_until(lambda: not C.check_heartbeats(C.read_heartbeats(hb_dir), 0, pids=my_pids), 3.0, 0.05)
            stuck = C.wait_until(lambda: not C.check_heartbeats(C.read_heartbeats(hb_dir), 7, pids=my_pids), 1.0, 0.1)
            late = C.check_heartbeats(C.read_heartbeats(hb_dir), 7, pids=my_pids)
            t(f"live/08 order: the old order (heartbeats checked after the driver pushed 'new') fails — the windows drew turn 0 {drew0:.2f} s "
              f"after the driver returned and never show turn 7 again (polled {stuck:.1f} s)",
              len(late) == len(C.VIEW_ROLES) and all("turn_no 0 ≠ current turn 7" in m for m in late) and stuck >= 1.0, late)
        finally:
            win.stop()
            eng.stop()

    def gate_status(code: str, milestone: str) -> str:
        chk = next(c for c in CHECKS if c.code == code)
        canned = C.Check(chk.code, chk.title, chk.ids, lambda ctx: ["canned failure"], since=chk.since, until=chk.until)
        buf, real = io.StringIO(), sys.stdout
        sys.stdout = buf
        try:
            with tempfile.TemporaryDirectory() as td2:
                C.run_checks([canned], None, milestone=lambda: milestone, select=None, budget_s=10.0, label="gating", result_path=Path(td2) / "r.json")
        finally:
            sys.stdout = real
        line = next((ln for ln in buf.getvalue().splitlines() if ln.split() and ln.split()[0] in (C.PASS, C.FAIL, C.SKIP, C.BLOCKED) and code in ln), "")
        return line.split()[0] if line else "?"
    t("gating: live/08 SKIPs at MILESTONE M1 and runs (here FAILs) at M2", gate_status("live/08", "M1") == C.SKIP and gate_status("live/08", "M2") == C.FAIL,
      (gate_status("live/08", "M1"), gate_status("live/08", "M2")))
    t("gating: live/07 still SKIPs until M5", gate_status("live/07", "M2") == C.SKIP and gate_status("live/07", "M5") == C.FAIL)
    t("teleport_arg formats C9's six integers", C.teleport_arg([3, 4], [7, 9], [5, 5]) == "3 4 7 9 5 5")

    # ---------------------------------------------------------------- M3: live/09 (specs/M3.md § 4)
    print("-- M3 --")
    from stub_engine import SPAWN_SHIP, default_snapshot  # noqa: E402
    spawn_cell = default_snapshot(spawn=True)["ships"][SPAWN_SHIP]["position"][2]
    with tempfile.TemporaryDirectory() as td:
        hb_dir = Path(td) / "hb"
        hb_dir.mkdir()
        my_pids = {r: os.getpid() for r in C.VIEW_ROLES}
        for mode, expect in (("battle", None), ("battle_leaky", ("combat", "in_battle", "battles"))):
            eng = StubEngine(mode)
            eng.start()
            eng.ready.wait(5)
            win = StubWindows(eng.pub_port, hb_dir)
            win.start()
            win.ready.wait(5)
            seen_hook: list[dict] = []

            def read_windows(obs: dict) -> dict:
                n = obs["resolved"][-1]["turn_no"]
                seen = windows_at_turn(hb_dir, n, my_pids, wait_s=3.0)
                C.wait_until(lambda: status_in_battle(hb_dir) is True, 2.0, 0.05)
                seen["in_battle"] = status_in_battle(hb_dir)
                seen_hook.append(seen)
                return seen
            try:
                obs = CB.drive_combat(eng.pub_port, eng.pull_port, spawn_cell=spawn_cell, turns=2, hello_timeout=10.0, reply_timeout=2.0, turn_timeout=2.0,
                                      after_first=read_windows)
            finally:
                win.stop()
                eng.stop()
            problems = CB.check_combat_bus(obs, turns=2)
            seen = obs.get("after_first") or {}
            problems += list(seen.get("problems") or [])
            if seen.get("in_battle") is not True:
                problems.append(f"T4's heartbeat in_battle = {seen.get('in_battle')!r}, expected true")
            if expect is None:
                t(f"live/09 driver: stub engine 'battle' passes (fire accepted, turn {(obs.get('resolved') or [{}])[0].get('turn_no')} combat events "
                  f"{(obs.get('resolved') or [{}])[0].get('combat_texts', [''])[:1]}, windows at the turn, T4 in_battle true)", not problems, problems)
            else:
                t(f"live/09 driver: stub engine 'battle_leaky' fails naming {expect}", all(any(w in m for m in problems) for w in expect), problems)
    t("gating: live/09 SKIPs at MILESTONE M2 and runs (here FAILs) at M3", gate_status("live/09", "M2") == C.SKIP and gate_status("live/09", "M3") == C.FAIL,
      (gate_status("live/09", "M2"), gate_status("live/09", "M3")))
    t("spawn_arg formats C10's --spawn string", CB.spawn_arg("kestrel", [3, 4], [7, 9], [13, 11], 0, 10) == "kestrel 3 4 7 9 13 11 0 10")

    bad = [n for ok, n in results if not ok]
    print(f"== live selftest: {len(results) - len(bad)} ok, {len(bad)} failed ==")
    return 1 if bad else 0


# --------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    utf8_console()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".", help="repo root (default: current directory)")
    ap.add_argument("--selftest", action="store_true", help="exercise the checkers with stub processes; opens no window")
    a = ap.parse_args(argv)
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
    if sys.platform != "win32":
        print("HARNESS ERROR: the live gate drives Windows consoles; run it on Windows")
        return 3
    art = root / "verify" / "artifacts" / "live"
    hb_dir = Path(os.environ.get("MOS_HEARTBEAT_DIR") or tempfile.mkdtemp(prefix="starbour_hb_"))
    ctx = Ctx(root=root, py=sys.executable, art=art, hb_dir=hb_dir)
    print(f"heartbeat dir {hb_dir}")
    return run_gate(ctx)


if __name__ == "__main__":
    sys.exit(main())
