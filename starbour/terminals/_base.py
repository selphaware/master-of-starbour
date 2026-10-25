"""The thin subscribe -> render -> print loop shared by T2, T3 and T4 - BUS-05, GRD-06, D-65, D-70, D-91.

A view terminal holds no game logic: it keeps the latest snapshot, redraws on every
``turn.resolved``, and redraws on a ``state.hello`` whose snapshot differs from the one
it last drew (hash compare, D-91), so a late or reopened window draws at once (TL) and a
new game at turn 0 is shown.  After every redraw it writes the C5 heartbeat and logs
``render_ms`` (BUS-05).
"""
from __future__ import annotations

import logging
import time
from typing import Callable

from rich.console import Console, RenderableType

from starbour import runtime
from starbour.bus import messages as M
from starbour.bus.zmq_io import Subscriber
from starbour.config import GameConfig
from starbour.engine.state import hash_snapshot

log = logging.getLogger("starbour.terminal")
RECV_MS = 250
TITLE = "Master of Starbour"


def make_console() -> Console:
    """M1 Known traps: force_terminal + legacy_windows=False with PYTHONUTF8=1."""
    return Console(force_terminal=True, legacy_windows=False, highlight=False, markup=False, emoji=False)


class ViewTerminal:
    """T2 / T4 base (T3 subclasses it with a scrollback)."""

    def __init__(self, role: str, cfg: GameConfig, pub_port: int, render_fn: Callable[..., RenderableType], title: str) -> None:
        self.role = role
        self.cfg = cfg
        self.pub_port = pub_port
        self.render_fn = render_fn
        self.title = title
        self.console = make_console()
        self.snapshot: dict | None = None
        self.view: dict | None = None
        self.last_hash: str | None = None
        self.turn_no = 0
        self.draws = 0

    # ---- rendering
    def renderable(self) -> RenderableType:
        return self.render_fn(self.snapshot or {}, self.view, self.cfg, self.console.width, height=self.console.height)

    def draw(self) -> None:
        """GRD-06: full redraw; log render_ms; heartbeat (C5)."""
        t0 = time.perf_counter()
        renderable = self.renderable()
        self.console.clear()
        self.console.print(renderable)
        ms = (time.perf_counter() - t0) * 1000.0
        self.draws += 1
        log.info("%s drew turn %d in %.1f ms (render_ms)", self.role, self.turn_no, ms)
        runtime.write_heartbeat(self.role, self.turn_no, ms)

    def accept_snapshot(self, snapshot: dict, turn_no: int) -> None:
        self.snapshot = snapshot
        self.turn_no = int(turn_no)
        self.view = snapshot.get("view") if isinstance(snapshot.get("view"), dict) else self.view
        self.last_hash = hash_snapshot(snapshot)

    # ---- messages
    def on_resolved(self, payload: dict) -> None:
        self.accept_snapshot(payload["snapshot"], payload["turn_no"])
        self.draw()

    def on_hello(self, payload: dict) -> None:
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            return
        if hash_snapshot(snapshot) == self.last_hash:
            return
        self.accept_snapshot(snapshot, payload.get("turn_no", snapshot.get("turn_no", 0)))
        self.draw()

    def on_view_changed(self, payload: dict) -> None:
        self.view = payload
        if self.snapshot is not None:
            self.draw()

    def on_other(self, topic: str, payload: dict) -> None:   # noqa: ARG002 - T3 overrides
        return

    def dispatch(self, topic: str, payload: dict) -> bool:
        """Returns False when the terminal should exit."""
        if topic == M.TURN_RESOLVED:
            self.on_resolved(payload)
        elif topic == M.STATE_HELLO:
            self.on_hello(payload)
        elif topic == M.VIEW_CHANGED:
            self.on_view_changed(payload)
        elif topic == M.SYSTEM_SHUTDOWN:
            log.info("%s: system.shutdown (%s) - exiting", self.role, payload.get("reason"))
            return False
        else:
            self.on_other(topic, payload)
        return True

    # ---- loop
    def run(self) -> int:
        log.info("%s: console %dx%d, terminal=%s, colour=%s", self.role, self.console.width, self.console.height,
                 self.console.is_terminal, self.console.color_system)
        self.console.set_window_title(f"{self.title} - {TITLE}")
        self.console.clear()
        self.console.print(f"{self.title}: waiting for the engine on tcp://127.0.0.1:{self.pub_port} ...", style="grey70")
        sub = Subscriber(self.pub_port)
        runtime.start_parent_watchdog(on_exit=lambda: sub.close(0))
        log.info("%s: subscribed to tcp://127.0.0.1:%d; waiting for state.hello", self.role, self.pub_port)
        first = True
        try:
            while True:
                got = sub.recv(RECV_MS)
                if got is None:
                    continue
                topic, payload, _raw = got
                if first:
                    log.info("%s: first message %s", self.role, topic)
                    first = False
                try:
                    if not self.dispatch(topic, payload):
                        return 0
                except Exception:   # noqa: BLE001 - a bad message must not kill the window
                    log.exception("%s: error handling %s", self.role, topic)
        except KeyboardInterrupt:
            return 0
        finally:
            sub.close(0)
