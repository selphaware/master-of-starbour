"""In-process engine on random ports - hello, three turns, accepted / rejected (BUS-03/04, MSG-01...07, C3).  slow."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
import zmq

from starbour.bus import messages as M
from starbour.config import GameConfig
from starbour.engine.server import Engine

pytestmark = [pytest.mark.slow, pytest.mark.timeout(40)]


class Client:
    def __init__(self, pub_port: int, pull_port: int) -> None:
        self.ctx = zmq.Context()
        self.sub = self.ctx.socket(zmq.SUB)
        self.sub.setsockopt(zmq.SUBSCRIBE, b"")
        self.sub.setsockopt(zmq.LINGER, 0)
        self.sub.connect(f"tcp://127.0.0.1:{pub_port}")
        self.push = self.ctx.socket(zmq.PUSH)
        self.push.setsockopt(zmq.LINGER, 0)
        self.push.connect(f"tcp://127.0.0.1:{pull_port}")
        self.seen: list[tuple[str, dict]] = []

    def send(self, topic: str, payload: dict) -> None:
        self.push.send_multipart([topic.encode(), json.dumps(payload).encode()])

    def wait_for(self, topic: str, timeout: float, pred=None) -> dict | None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            if self.sub.poll(200):
                t, raw = self.sub.recv_multipart()
                payload = json.loads(raw)
                self.seen.append((t.decode(), payload))
                if t.decode() == topic and (pred is None or pred(payload)):
                    return payload
        return None

    def close(self) -> None:
        self.sub.close(0)
        self.push.close(0)
        self.ctx.term()


@pytest.fixture
def engine(small_cfg: GameConfig, tmp_path: Path):
    eng = Engine(small_cfg, seed=1234, save_path=tmp_path / "game.json", pub_port=0, pull_port=0, bus=True, resume=False)
    eng.start()
    thread = threading.Thread(target=eng.run, name="engine-under-test", daemon=True)
    thread.start()
    yield eng
    eng.request_stop(0)
    thread.join(5)
    assert not thread.is_alive(), "the engine loop did not stop"


def test_hello_three_turns_accepted_rejected(engine: Engine) -> None:
    client = Client(engine.pub_port, engine.pull_port)
    try:
        hello = client.wait_for(M.STATE_HELLO, 10)
        assert hello is not None and hello["turn_no"] == 0 and hello["snapshot"]["turn_no"] == 0 and hello["seed"] == 1234
        sid = hello["snapshot"]["captains"][hello["snapshot"]["player_id"]]["active_ship"]
        client.send(M.ORDER_SUBMITTED, {"command": "move", "args": ["1", "0"], "ship_id": sid, "turn_no": 0, "client": "command"})
        acc = client.wait_for(M.ORDER_ACCEPTED, 5)
        assert acc is not None and acc["queue_position"] == 1 and acc["queued"] == 1
        client.send(M.ORDER_SUBMITTED, {"command": "move", "args": ["99", "0"], "ship_id": sid, "turn_no": 0, "client": "command"})
        rej = client.wait_for(M.ORDER_REJECTED, 5)
        assert rej is not None and "CMD-06" in rej["reason"]
        client.send(M.ORDER_SUBMITTED, {"command": "warp", "args": ["1", "1", "1", "1"], "ship_id": sid, "turn_no": 0, "client": "command"})
        rej = client.wait_for(M.ORDER_REJECTED, 5)
        assert rej is not None and "CMD-07" in rej["reason"]
        turn = 0
        hashes = []
        for _ in range(3):
            client.send(M.TURN_END, {"turn_no": turn})
            res = client.wait_for(M.TURN_RESOLVED, 10, pred=lambda p: p["turn_no"] > turn)
            assert res is not None and res["turn_no"] == turn + 1 == res["snapshot"]["turn_no"]
            assert isinstance(res["events"], list) and res["events"]
            hashes.append(json.dumps(res["snapshot"], sort_keys=True))
            turn = res["turn_no"]
        assert len(set(hashes)) == 3
        assert any(t == M.EVENT_LOG and "autosaved" in p.get("text", "") for t, p in client.seen)
        assert engine.save_path.is_file()
    finally:
        client.close()


def test_view_changed_republished_and_shutdown(engine: Engine) -> None:
    client = Client(engine.pub_port, engine.pull_port)
    try:
        assert client.wait_for(M.STATE_HELLO, 10) is not None
        client.send(M.VIEW_CHANGED, {"level": "galaxy"})
        vc = client.wait_for(M.VIEW_CHANGED, 5)
        assert vc == {"level": "galaxy"}
        hello = client.wait_for(M.STATE_HELLO, 5, pred=lambda p: p["snapshot"].get("view", {}).get("level") == "galaxy")
        assert hello is not None, "a fresh hello carries the changed view (D-91)"
        client.send(M.SYSTEM_SHUTDOWN, {"reason": "test"})
        sd = client.wait_for(M.SYSTEM_SHUTDOWN, 5)
        assert sd == {"reason": "test"}
        deadline = time.perf_counter() + 5
        while not engine.stopping and time.perf_counter() < deadline:
            time.sleep(0.05)
        assert engine.stopping and engine.exit_code == 0
    finally:
        client.close()
