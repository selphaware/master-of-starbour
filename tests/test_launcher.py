"""The launcher - spawn, reopen, shutdown, no orphans (TL, BUS-06, D-59).  slow; Windows only; opens no window (MOS_HEADLESS)."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
pytestmark = [pytest.mark.slow, pytest.mark.timeout(90), pytest.mark.skipif(sys.platform != "win32", reason="Windows consoles")]
ROLES = ("engine", "command", "grid", "output", "status")


def free_ports(n: int) -> list[int]:
    socks, ports = [], []
    for _ in range(n):
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        socks.append(s)
        ports.append(s.getsockname()[1])
    for s in socks:
        s.close()
    return ports


def wait_until(pred, timeout: float, interval: float = 0.1) -> float:
    t0 = time.perf_counter()
    while not pred():
        if time.perf_counter() - t0 > timeout:
            break
        time.sleep(interval)
    return time.perf_counter() - t0


def role_of(cmdline: list[str]) -> str | None:
    for i, tok in enumerate(cmdline):
        if tok == "--role" and i + 1 < len(cmdline):
            return cmdline[i + 1]
    return None


class Game:
    def __init__(self, tmp_path: Path) -> None:
        import psutil
        self.psutil = psutil
        self.pub, self.pull = free_ports(2)
        self.hb = tmp_path / "hb"
        self.hb.mkdir()
        self.save_dir = tmp_path / "saves"
        env = dict(os.environ, PYTHONUTF8="1", MOS_HEADLESS="1", MOS_HEARTBEAT_DIR=str(self.hb))
        env.pop("MOS_LAUNCHER_PID", None)
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "starbour", "--seed", "77", "--new", "--pub-port", str(self.pub), "--pull-port", str(self.pull),
             "--save-dir", str(self.save_dir)],
            cwd=str(REPO), env=env, stdout=open(tmp_path / "launcher.out", "w"), stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))

    def children(self) -> dict[str, list[int]]:
        out: dict[str, list[int]] = {}
        try:
            for k in self.psutil.Process(self.proc.pid).children(recursive=True):
                try:
                    cl = k.cmdline()
                except self.psutil.Error:
                    continue
                r = role_of(cl)
                if r and str(self.pub) in cl:
                    out.setdefault(r, []).append(k.pid)
        except self.psutil.Error:
            pass
        return out

    def ours(self) -> list[int]:
        """Every process on the machine running one of our roles on our ports."""
        pids = []
        for p in self.psutil.process_iter(["pid", "cmdline"]):
            cl = p.info.get("cmdline") or []
            if role_of(cl) and str(self.pub) in cl:
                pids.append(p.pid)
        return pids

    def cleanup(self) -> None:
        for pid in self.ours() + [self.proc.pid]:
            try:
                self.psutil.Process(pid).kill()
            except self.psutil.Error:
                pass


@pytest.fixture
def game(tmp_path: Path):
    g = Game(tmp_path)
    try:
        yield g
    finally:
        g.cleanup()


def test_spawn_reopen_and_t1_closes_the_game(game: Game) -> None:
    waited = wait_until(lambda: all(len(game.children().get(r, [])) == 1 for r in ROLES), 20, 0.25)
    kids = game.children()
    assert all(len(kids.get(r, [])) == 1 for r in ROLES), f"children after {waited:.1f} s: {kids}"
    # every view terminal draws turn 0 from state.hello
    waited = wait_until(lambda: all((game.hb / f"{r}.json").exists() for r in ("grid", "output", "status")), 20, 0.2)
    assert all((game.hb / f"{r}.json").exists() for r in ("grid", "output", "status")), f"no heartbeats after {waited:.1f} s"
    # closing T2 reopens it
    old = kids["grid"][0]
    game.psutil.Process(old).kill()
    waited = wait_until(lambda: any(p != old for p in game.children().get("grid", [])), 10, 0.2)
    new = [p for p in game.children().get("grid", []) if p != old]
    assert new, f"grid not reopened after {waited:.1f} s"
    # closing T1 ends the game with a save and no orphans
    game.psutil.Process(kids["command"][0]).kill()
    waited = wait_until(lambda: game.proc.poll() is not None and not game.ours(), 15, 0.2)
    assert game.proc.poll() is not None, "the launcher did not exit after T1 closed"
    assert not game.ours(), f"orphans after {waited:.1f} s: {game.ours()}"
    assert (game.save_dir / "game.json").is_file()


def test_killing_the_launcher_leaves_no_children(game: Game) -> None:
    wait_until(lambda: all(len(game.children().get(r, [])) == 1 for r in ROLES), 20, 0.25)
    kids = game.children()
    assert len(kids) == 5, kids
    game.proc.kill()
    game.proc.wait(5)
    waited = wait_until(lambda: not game.ours(), 15, 0.2)
    assert not game.ours(), f"children survived the launcher by {waited:.1f} s: {game.ours()} (D-59 watchdog)"
    time.sleep(0.3)
    for port in (game.pub, game.pull):
        s = socket.socket()
        try:
            s.bind(("127.0.0.1", port))
        finally:
            s.close()
