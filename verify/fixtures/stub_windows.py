"""Stub view terminals for --selftest: one SUB thread that writes the C5 heartbeat files for
grid/output/status after every state.hello and turn.resolved, as T2/T3/T4 redraw on both
(D-66, D-70).  Opens no window.

Used by verify/live_test.py --selftest to prove live/08 reads the heartbeats before 'new' is
pushed: once the engine publishes state.hello at turn 0 the stub, like the real windows,
rewrites every heartbeat at turn 0 within milliseconds (specs/M2.md § 7 gate note).
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

VIEW_ROLES = ("grid", "output", "status")
REDRAW_TOPICS = ("state.hello", "turn.resolved")


class StubWindows(threading.Thread):
    def __init__(self, pub_port: int, hb_dir: Path, roles: tuple[str, ...] = VIEW_ROLES, host: str = "127.0.0.1") -> None:
        super().__init__(daemon=True, name="stub-windows")
        self.pub_port, self.host = pub_port, host
        self.hb_dir, self.roles = Path(hb_dir), tuple(roles)
        self.ready = threading.Event()
        self._halt = threading.Event()
        self.drawn: list[tuple[float, str, int]] = []      # (perf_counter, topic, turn_no) per redraw

    def stop(self) -> None:
        self._halt.set()
        self.join(5)

    def _write(self, role: str, turn_no: int) -> None:
        """C5 record, written atomically; retried when the gate holds the file open (Windows os.replace)."""
        doc = {"role": role, "turn_no": turn_no, "rendered_at": time.time(), "pid": os.getpid(), "render_ms": 1}
        tmp = self.hb_dir / f"{role}.json.tmp"
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        for attempt in range(5):
            try:
                os.replace(tmp, self.hb_dir / f"{role}.json")
                return
            except PermissionError:
                time.sleep(0.01 * (attempt + 1))

    def _draw(self, topic: str, turn_no: int) -> None:
        t0 = time.perf_counter()
        for r in self.roles:
            self._write(r, turn_no)
        self.drawn.append((t0, topic, turn_no))

    def run(self) -> None:
        import zmq
        ctx = zmq.Context()
        sub = ctx.socket(zmq.SUB)
        sub.setsockopt(zmq.SUBSCRIBE, b"")
        sub.setsockopt(zmq.LINGER, 0)
        sub.connect(f"tcp://{self.host}:{self.pub_port}")
        poller = zmq.Poller()
        poller.register(sub, zmq.POLLIN)
        self.ready.set()
        try:
            while not self._halt.is_set():
                if not poller.poll(50):
                    continue
                frames = sub.recv_multipart()
                if len(frames) != 2:
                    continue
                topic = frames[0].decode("ascii", "replace")
                try:
                    payload = json.loads(frames[1].decode("utf-8"))
                except ValueError:
                    continue
                if topic in REDRAW_TOPICS and isinstance(payload, dict) and isinstance(payload.get("turn_no"), int):
                    self._draw(topic, payload["turn_no"])
        finally:
            sub.close(0)
            ctx.term()
