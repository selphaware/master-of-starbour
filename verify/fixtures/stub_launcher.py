"""A stub launcher for live_test.py --selftest: proves the orphan check discriminates.

    stub_launcher.py --mode good     spawns two children that watch MOS_LAUNCHER_PID (D-59)
                                     and exit within 2 s of it vanishing
    stub_launcher.py --mode broken   spawns two children that ignore MOS_LAUNCHER_PID

Children run as `stub_launcher.py --child --mode <m> --role stub-grid|stub-status`, so the
gate's cmdline matcher (--role plus the repo path) finds them exactly as it finds the
real terminals.  Everything spawns with CREATE_NO_WINDOW — no window opens.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

ROLES = ("stub-grid", "stub-status")


def child(mode: str) -> int:
    if mode == "broken":
        time.sleep(120)
        return 0
    import psutil
    parent = int(os.environ["MOS_LAUNCHER_PID"])
    deadline = time.time() + 120
    while time.time() < deadline:
        if not psutil.pid_exists(parent):
            return 0
        time.sleep(0.25)
    return 0


def launcher(mode: str) -> int:
    env = dict(os.environ, MOS_LAUNCHER_PID=str(os.getpid()), PYTHONUTF8="1")
    kids = []
    for role in ROLES:
        kids.append(subprocess.Popen([sys.executable, os.path.abspath(__file__), "--child", "--mode", mode, "--role", role],
                                     env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    try:
        time.sleep(120)        # until live_test terminates us
    finally:
        pass                   # a real launcher would clean up here; the stub deliberately does not
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("good", "broken"), default="good")
    ap.add_argument("--child", action="store_true")
    ap.add_argument("--role", default="")
    a = ap.parse_args()
    return child(a.mode) if a.child else launcher(a.mode)


if __name__ == "__main__":
    sys.exit(main())
