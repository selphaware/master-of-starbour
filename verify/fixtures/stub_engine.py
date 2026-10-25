"""A stub ZeroMQ engine speaking CONTRACTS C3, for --selftest of the bus client and checks.

Modes:
  good           hello every `hello_interval`; turn.end → turn_no + 1 and a changed snapshot;
                 valid orders → order.accepted; move 99 0 → order.rejected naming CMD-06
  stuck_turn     turn.end → republishes the same turn_no and snapshot (gate must catch it)
  same_snapshot  turn_no advances in the payload but the snapshot never changes
  reject_no_id   rejections do not cite CMD-06
  no_reply       orders are swallowed
  no_hello       never publishes state.hello

Runs as a thread (StubEngine) or as a script (prints the bound ports as JSON on stdout).
Opens no window.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

MODES = ("good", "stuck_turn", "same_snapshot", "reject_no_id", "no_reply", "no_hello")


def default_snapshot() -> dict:
    doc = json.loads((HERE / "snapshot_good.json").read_text(encoding="utf-8"))
    doc["turn_no"] = 0
    return doc


class StubEngine(threading.Thread):
    def __init__(self, mode: str = "good", pub_port: int = 0, pull_port: int = 0, hello_interval: float = 0.5,
                 snapshot: dict | None = None) -> None:
        super().__init__(daemon=True, name=f"stub-engine-{mode}")
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode
        self.hello_interval = hello_interval
        self.snapshot = copy.deepcopy(snapshot) if snapshot else default_snapshot()
        self.turn_no = int(self.snapshot.get("turn_no", 0))
        self._want_pub, self._want_pull = pub_port, pull_port
        self.pub_port = self.pull_port = 0
        self.ready = threading.Event()
        self._halt = threading.Event()
        self.received: list[tuple[str, dict]] = []

    def stop(self) -> None:
        self._halt.set()
        self.join(5)

    # ---- helpers
    def _publish(self, pub, topic: str, payload: dict) -> None:
        pub.send_multipart([topic.encode("ascii"), json.dumps(payload).encode("utf-8")])

    def _advance(self) -> dict:
        """Resolve a turn and return the turn.resolved payload."""
        if self.mode == "stuck_turn":
            return {"turn_no": self.turn_no, "snapshot": self.snapshot, "events": []}
        self.turn_no += 1
        if self.mode != "same_snapshot":
            self.snapshot["turn_no"] = self.turn_no
            pid = self.snapshot["player_id"]
            cap = self.snapshot["captains"][pid]
            cap["food"] = max(0, cap["food"] - 1)
            cap["water"] = max(0, cap["water"] - 1)
            ship = self.snapshot["ships"][cap["active_ship"]]
            (gx, gy), (sx, sy), (cx, cy) = ship["position"]
            ship["position"] = [[gx, gy], [sx, sy], [(cx + 1) % self.snapshot["sizes"]["sector"][0], cy]]
        events = [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "Lantern moved", "ids": ["MV-01"]},
                  {"phase": "TP-06", "type": "upkeep", "severity": "info", "text": "upkeep", "ids": ["SUP-01"]}]
        return {"turn_no": self.turn_no, "snapshot": self.snapshot, "events": events}

    def _order_reply(self, order: dict) -> tuple[str, dict] | None:
        if self.mode == "no_reply":
            return None
        args = order.get("args") or []
        w, h = self.snapshot["sizes"]["sector"]
        try:
            x, y = int(args[0]), int(args[1])
        except (ValueError, IndexError):
            return "order.rejected", {"order": order, "reason": "move needs <x y> or <dx dy> (CMD-06)"}
        if 0 <= x < w and 0 <= y < h:
            return "order.accepted", {"order": order, "queue_position": 1}
        if self.mode == "reject_no_id":
            return "order.rejected", {"order": order, "reason": f"target {x},{y} is outside the sector"}
        return "order.rejected", {"order": order, "reason": f"target {x},{y} is outside the sector {w}x{h} (CMD-06)"}

    def run(self) -> None:
        import zmq
        ctx = zmq.Context()
        pub = ctx.socket(zmq.PUB)
        pull = ctx.socket(zmq.PULL)
        for s in (pub, pull):
            s.setsockopt(zmq.LINGER, 0)
        self.pub_port = pub.bind_to_random_port("tcp://127.0.0.1") if not self._want_pub else (pub.bind(f"tcp://127.0.0.1:{self._want_pub}") or self._want_pub)
        self.pull_port = pull.bind_to_random_port("tcp://127.0.0.1") if not self._want_pull else (pull.bind(f"tcp://127.0.0.1:{self._want_pull}") or self._want_pull)
        poller = zmq.Poller()
        poller.register(pull, zmq.POLLIN)
        self.ready.set()
        next_hello = 0.0
        try:
            while not self._halt.is_set():
                now = time.perf_counter()
                if self.mode != "no_hello" and now >= next_hello:
                    self._publish(pub, "state.hello", {"turn_no": self.turn_no, "snapshot": self.snapshot, "seed": 1234, "version": "0.1.0"})
                    next_hello = now + self.hello_interval
                if not poller.poll(50):
                    continue
                frames = pull.recv_multipart()
                if len(frames) != 2:
                    continue
                topic = frames[0].decode("ascii", "replace")
                try:
                    payload = json.loads(frames[1].decode("utf-8"))
                except ValueError:
                    continue
                self.received.append((topic, payload))
                if topic == "turn.end":
                    self._publish(pub, "turn.resolved", self._advance())
                elif topic == "order.submitted":
                    reply = self._order_reply(payload)
                    if reply:
                        self._publish(pub, *reply)
                elif topic == "system.shutdown":
                    self._publish(pub, "system.shutdown", payload)
                    break
        finally:
            pub.close(0)
            pull.close(0)
            ctx.term()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="good", choices=MODES)
    ap.add_argument("--pub-port", type=int, default=0)
    ap.add_argument("--pull-port", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=30.0, help="how long to serve")
    a = ap.parse_args()
    eng = StubEngine(a.mode, a.pub_port, a.pull_port)
    eng.start()
    eng.ready.wait(5)
    print(json.dumps({"pub_port": eng.pub_port, "pull_port": eng.pull_port, "mode": a.mode}), flush=True)
    time.sleep(a.seconds)
    eng.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
