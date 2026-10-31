"""The launcher - TL, BUS-06, BR-15, D-38, D-59, D-68.

Spawns the engine (no window) then T1...T4 (new consoles), owns them, polls every 0.5 s:
T2/T3/T4 gone -> respawn; T1 gone, engine gone or Ctrl+C -> push system.shutdown, wait
up to 3 s for the engine to save and exit, terminate the rest, and exit with no child
alive.  Children also watch MOS_LAUNCHER_PID and exit on their own if this process is
hard-killed (D-59).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from starbour import paths, runtime
from starbour.bus import messages as M
from starbour.config import GameConfig

log = logging.getLogger("starbour.launcher")

ROLES = ("engine", "command", "grid", "output", "status")
VIEW_ROLES = ("grid", "output", "status")
POLL_S = 0.5
ENGINE_EXIT_WAIT_S = 3.0
TERMINATE_WAIT_S = 2.0
RESPAWN_LIMIT = 5          # per role per RESPAWN_WINDOW_S: a crash loop ends the game instead
RESPAWN_WINDOW_S = 30.0


def interpreter() -> tuple[list[str], dict[str, str]]:
    """The command prefix and env additions that run *one* process per role.

    A Windows venv's ``Scripts\\python.exe`` can be a redirector that spawns the base
    interpreter as a child and waits (two processes per role, and killing the stub
    kills the child through its job object).  Spawning ``sys._base_executable`` with
    ``__PYVENV_LAUNCHER__`` set to the venv executable runs the same interpreter with the
    venv's packages in a single process (D-96).  Frozen builds run ``starbour.exe``.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable], {}
    base = getattr(sys, "_base_executable", None) or sys.executable
    if os.path.normcase(base) != os.path.normcase(sys.executable) and Path(base).is_file():
        return [base, "-m", "starbour"], {"__PYVENV_LAUNCHER__": sys.executable}
    return [sys.executable, "-m", "starbour"], {}


def role_command(role: str, cfg: GameConfig, pub_port: int, pull_port: int, log_level: str,
                 seed: int | None = None, new_game: bool = False, save_dir: Path | None = None,
                 teleport: str | None = None, spawns: list[str] | None = None, ai: str | None = None,
                 give: str | None = None, coins: list | tuple | None = None, respect: int | None = None, fear: int | None = None) -> list[str]:
    """C1: ``python -m starbour --role <r>`` in dev, ``starbour.exe --role <r>`` when frozen (BR-15).

    ``--seed``, ``--new``, ``--save-dir`` and the debug ``--teleport`` / ``--spawn`` / ``--ai`` / ``--give`` / ``--coins`` /
    ``--respect`` / ``--fear`` (C9, C10, C11, D-104, D-110, D-125, D-126, D-148 (13)) go to the engine only."""
    cmd = list(interpreter()[0])
    cmd += ["--role", role, "--config", str(cfg.path), "--pub-port", str(pub_port), "--pull-port", str(pull_port),
            "--log-level", log_level]
    if role == "engine":
        if seed is not None:
            cmd += ["--seed", str(seed)]
        if new_game:
            cmd += ["--new"]
        if save_dir is not None:
            cmd += ["--save-dir", str(save_dir)]
        if teleport is not None:
            cmd += ["--teleport", teleport]
        for s in spawns or []:
            cmd += ["--spawn", s]
        if ai is not None:
            cmd += ["--ai", ai]
        if give is not None:
            cmd += ["--give", give]
        if coins is not None:
            cmd += ["--coins", str(coins[0]), str(coins[1])]
        if respect is not None:
            cmd += ["--respect", str(respect)]
        if fear is not None:
            cmd += ["--fear", str(fear)]
    return cmd


class Launcher:
    def __init__(self, cfg: GameConfig, *, pub_port: int, pull_port: int, log_level: str = "INFO", seed: int | None = None,
                 new_game: bool = False, save_dir: Path | None = None, teleport: str | None = None,
                 spawns: list[str] | None = None, ai: str | None = None, give: str | None = None, coins: list | tuple | None = None,
                 respect: int | None = None, fear: int | None = None) -> None:
        self.cfg = cfg
        self.pub_port, self.pull_port = pub_port, pull_port
        self.log_level = log_level
        self.seed = seed
        self.new_game = new_game
        self.save_dir = save_dir
        self.teleport = teleport
        self.spawns = list(spawns or [])
        self.ai = ai
        self.give, self.coins, self.respect, self.fear = give, coins, respect, fear
        self.children: dict[str, subprocess.Popen] = {}
        self.respawns: dict[str, list[float]] = {r: [] for r in ROLES}
        self.headless = runtime.headless()
        self.env = dict(os.environ, PYTHONUTF8="1", **{runtime.LAUNCHER_PID_ENV: str(os.getpid())})
        self.env.update(interpreter()[1])
        self.logs = paths.logs_dir()
        self.logs.mkdir(parents=True, exist_ok=True)
        self._handles: list = []
        self.parent_pid = self._parent_pid()

    @staticmethod
    def _parent_pid() -> int | None:
        """The process that started us (a venv stub, a shell, explorer).  If it vanishes we end the
        game too: that is what 'closing the launcher ends the game' means when a redirector stub
        is the process the user (or the gate) actually holds (TL, D-96)."""
        try:
            import psutil
            parent = psutil.Process(os.getpid()).parent()
            return parent.pid if parent is not None else None
        except Exception:   # noqa: BLE001
            return None

    def parent_gone(self) -> bool:
        if self.parent_pid is None:
            return False
        try:
            import psutil
            return not psutil.pid_exists(self.parent_pid)
        except Exception:   # noqa: BLE001
            return False

    # ---- spawning
    def spawn(self, role: str) -> subprocess.Popen:
        cmd = role_command(role, self.cfg, self.pub_port, self.pull_port, self.log_level, self.seed, self.new_game, self.save_dir,
                           self.teleport, self.spawns, self.ai, self.give, self.coins, self.respect, self.fear)
        if role == "engine" or self.headless:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            out = open(self.logs / f"{role}.stderr.log", "a", encoding="utf-8")
            self._handles.append(out)
            proc = subprocess.Popen(cmd, env=self.env, creationflags=flags, stdin=subprocess.DEVNULL, stdout=out, stderr=out)
        else:
            flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            proc = subprocess.Popen(cmd, env=self.env, creationflags=flags)
        self.children[role] = proc
        log.info("spawned %s pid %d: %s", role, proc.pid, " ".join(cmd))
        return proc

    def spawn_all(self) -> None:
        self.spawn("engine")
        for role in ("command", "grid", "output", "status"):
            self.spawn(role)

    def alive(self, role: str) -> bool:
        p = self.children.get(role)
        return p is not None and p.poll() is None

    def respawn_allowed(self, role: str) -> bool:
        now = time.perf_counter()
        recent = [t for t in self.respawns[role] if now - t < RESPAWN_WINDOW_S]
        self.respawns[role] = recent
        if len(recent) >= RESPAWN_LIMIT:
            return False
        recent.append(now)
        return True

    # ---- shutdown
    def push_shutdown(self, reason: str) -> None:
        try:
            from starbour.bus.zmq_io import Pusher
            pusher = Pusher(self.pull_port)
            pusher.send(M.SYSTEM_SHUTDOWN, M.SystemShutdown(reason).to_dict())
            time.sleep(0.1)
            pusher.close(200)
        except Exception as e:   # noqa: BLE001
            log.warning("could not push system.shutdown: %s", e)

    def shutdown(self, reason: str) -> None:
        """BUS-06: engine saves and exits first (<= 3 s), then everything else is terminated."""
        log.info("shutdown: %s", reason)
        if self.alive("engine"):
            self.push_shutdown(reason)
            deadline = time.perf_counter() + ENGINE_EXIT_WAIT_S
            while self.alive("engine") and time.perf_counter() < deadline:
                time.sleep(0.05)
            if self.alive("engine"):
                log.warning("engine did not exit within %.0f s of system.shutdown - terminating it", ENGINE_EXIT_WAIT_S)
        for role, proc in self.children.items():
            if proc.poll() is None:
                try:
                    proc.terminate()
                except OSError:
                    pass
        deadline = time.perf_counter() + TERMINATE_WAIT_S
        while any(p.poll() is None for p in self.children.values()) and time.perf_counter() < deadline:
            time.sleep(0.05)
        for role, proc in self.children.items():
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
        for proc in self.children.values():
            try:
                proc.wait(timeout=1.0)
            except (subprocess.TimeoutExpired, OSError):
                pass
        self.kill_strays()
        for h in self._handles:
            try:
                h.close()
            except OSError:
                pass
        log.info("all children gone")

    def kill_strays(self) -> None:
        """Belt and braces: any grandchild (console host helpers etc.) still under us."""
        try:
            import psutil
            me = psutil.Process(os.getpid())
            for child in me.children(recursive=True):
                try:
                    child.kill()
                except psutil.Error:
                    pass
        except Exception:   # noqa: BLE001
            pass

    # ---- main loop
    def run(self) -> int:
        self.spawn_all()
        log.info("game up: engine PUB %d PULL %d; close T1 or press Ctrl+C here to end the game", self.pub_port, self.pull_port)
        code = 0
        try:
            while True:
                time.sleep(POLL_S)
                if not self.alive("engine"):
                    rc = self.children["engine"].returncode
                    log.error("engine exited with code %s - ending the game (see logs/engine.log)", rc)
                    code = 1 if rc not in (0, None) else 0
                    self.shutdown("engine exited")
                    return code
                if not self.alive("command"):
                    log.info("command terminal closed - ending the game (TL)")
                    self.shutdown("command terminal closed")
                    return 0
                if self.parent_gone():
                    log.info("the process that started the launcher (pid %s) is gone - ending the game (TL)", self.parent_pid)
                    self.shutdown("launcher parent gone")
                    return 0
                for role in VIEW_ROLES:
                    if not self.alive(role):
                        if not self.respawn_allowed(role):
                            log.error("%s keeps dying (%d times in %.0f s) - ending the game; see logs/%s.log",
                                      role, RESPAWN_LIMIT, RESPAWN_WINDOW_S, role)
                            self.shutdown(f"{role} crash loop")
                            return 1
                        log.info("%s closed - reopening it (TL)", role)
                        self.spawn(role)
        except KeyboardInterrupt:
            self.shutdown("Ctrl+C in the launcher")
            return 0
        finally:
            if any(p.poll() is None for p in self.children.values()):
                self.shutdown("launcher exiting")


def main(cfg: GameConfig, *, pub_port: int, pull_port: int, log_level: str, seed: int | None, new_game: bool = False,
         save_dir: Path | None = None, teleport: str | None = None, spawns: list[str] | None = None, ai: str | None = None,
         give: str | None = None, coins: list | tuple | None = None, respect: int | None = None, fear: int | None = None) -> int:
    """``python -m starbour`` with no role (C1); the debug flags are forwarded to the engine (C9, C10, C11)."""
    return Launcher(cfg, pub_port=pub_port, pull_port=pull_port, log_level=log_level, seed=seed, new_game=new_game,
                    save_dir=save_dir, teleport=teleport, spawns=spawns, ai=ai, give=give, coins=coins, respect=respect, fear=fear).run()
