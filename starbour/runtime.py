"""Process plumbing shared by every role: logging, the parent watchdog, heartbeats - D-59, D-66, D-68.

No game logic here.  ``start_parent_watchdog`` is what makes "no orphans" hold even when
the launcher is hard-killed: every child polls ``psutil.pid_exists(MOS_LAUNCHER_PID)``
and exits within 2 s of it vanishing (D-59, BUS-06).
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from starbour import paths

LAUNCHER_PID_ENV = "MOS_LAUNCHER_PID"
HEARTBEAT_ENV = "MOS_HEARTBEAT_DIR"
HEADLESS_ENV = "MOS_HEADLESS"
WATCHDOG_INTERVAL_S = 1.0   # D-59: poll every second, exit within 2 s


def setup_logging(role: str, level: str = "INFO", to_console: bool = False) -> Path:
    """logs/<role>.log (engine.log for the engine); optional console echo for the launcher."""
    logs = paths.logs_dir()
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"{role}.log"
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    fh = logging.FileHandler(path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(fh)
    if to_console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", datefmt="%H:%M:%S"))
        root.addHandler(sh)
    logging.getLogger("starbour").info("%s starting: pid %d, python %s", role, os.getpid(), sys.executable)
    return path


def launcher_pid() -> int | None:
    raw = os.environ.get(LAUNCHER_PID_ENV)
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def start_parent_watchdog(on_exit: Callable[[], None] | None = None, interval_s: float = WATCHDOG_INTERVAL_S) -> threading.Thread | None:
    """D-59: a daemon thread that ends this process within 2 s of the launcher disappearing.

    Returns None (and does nothing) when MOS_LAUNCHER_PID is not set, i.e. the process was
    started by hand.  ``on_exit`` runs first (close sockets, save); then ``os._exit(0)``,
    because a blocked prompt or a ZeroMQ context cannot be relied on to unwind."""
    pid = launcher_pid()
    if pid is None:
        return None
    import psutil

    def watch() -> None:
        log = logging.getLogger("starbour.watchdog")
        while True:
            time.sleep(interval_s)
            if not psutil.pid_exists(pid):
                log.warning("launcher pid %d is gone - exiting (D-59)", pid)
                try:
                    if on_exit is not None:
                        on_exit()
                except Exception:   # noqa: BLE001 - nothing may stop the exit
                    log.exception("on_exit failed")
                for h in logging.getLogger().handlers:
                    try:
                        h.flush()
                    except Exception:   # noqa: BLE001
                        pass
                os._exit(0)

    t = threading.Thread(target=watch, name="parent-watchdog", daemon=True)
    t.start()
    return t


def heartbeat_dir() -> Path | None:
    raw = os.environ.get(HEARTBEAT_ENV)
    return Path(raw) if raw else None


HEARTBEAT_RETRIES = 8
HEARTBEAT_RETRY_S = 0.01


def write_heartbeat(role: str, turn_no: int, render_ms: float, extra: dict | None = None) -> None:
    """C5 / D-66: <MOS_HEARTBEAT_DIR>/<role>.json after every redraw; a no-op in normal play.
    ``extra`` adds role-specific fields (M3: the status window's ``in_battle``, C10 / live/09).

    Windows refuses ``os.replace`` while another process has the target open for reading
    (WinError 5) - exactly what the live gate does every 100 ms - so the replace is retried
    briefly and, as a last resort, the document is written in place: a reader that catches
    a partial file simply polls again.
    """
    hb = heartbeat_dir()
    if hb is None:
        return
    log = logging.getLogger("starbour.heartbeat")
    try:
        hb.mkdir(parents=True, exist_ok=True)
        doc = {"role": role, "turn_no": turn_no, "rendered_at": time.time(), "pid": os.getpid(), "render_ms": round(render_ms, 1)}
        doc.update(extra or {})
        text = json.dumps(doc)
        tmp, final = hb / f"{role}.json.tmp", hb / f"{role}.json"
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(HEARTBEAT_RETRIES):
            try:
                os.replace(tmp, final)
                return
            except PermissionError:
                time.sleep(HEARTBEAT_RETRY_S)
        final.write_text(text, encoding="utf-8")
        log.info("heartbeat written in place after %d blocked replaces", HEARTBEAT_RETRIES)
    except OSError as e:
        log.warning("heartbeat not written: %s", e)


def headless() -> bool:
    """MOS_HEADLESS=1: the launcher opens no windows and T1 runs without a prompt (tests only, D-93)."""
    return os.environ.get(HEADLESS_ENV, "") == "1"


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
