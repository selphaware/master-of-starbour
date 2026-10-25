"""Thin pyzmq wrappers: Publisher, Puller (engine side), Subscriber, Pusher (clients) - BUS-03, D-57, D-58.

Every socket uses LINGER 0 so a closing process frees its ports at once (M1 Known traps).
``port=0`` binds a random free port and exposes it as ``.port`` (for tests and gates).
"""
from __future__ import annotations

import json
import logging

import zmq

from starbour.bus.messages import decode, encode

log = logging.getLogger(__name__)
HOST = "127.0.0.1"


class _Socket:
    def __init__(self, kind: int, context: zmq.Context | None = None) -> None:
        self._own_ctx = context is None
        self.ctx = context or zmq.Context.instance()
        self.sock = self.ctx.socket(kind)
        self.sock.setsockopt(zmq.LINGER, 0)
        self.port: int = 0

    def _bind(self, port: int) -> None:
        if port == 0:
            self.port = self.sock.bind_to_random_port(f"tcp://{HOST}")
        else:
            self.sock.bind(f"tcp://{HOST}:{port}")
            self.port = port

    def _connect(self, port: int) -> None:
        self.sock.connect(f"tcp://{HOST}:{port}")
        self.port = port

    def close(self, linger_ms: int = 0) -> None:
        try:
            self.sock.close(linger=linger_ms)
        except zmq.ZMQError:
            pass


class Publisher(_Socket):
    """The engine's PUB - the only publisher on the bus (D-57)."""

    def __init__(self, port: int, context: zmq.Context | None = None) -> None:
        super().__init__(zmq.PUB, context)
        self._bind(port)

    def publish(self, topic: str, payload: dict) -> int:
        """Send one message; returns the payload size in bytes (BUS-07 logging)."""
        frames = encode(topic, payload)
        self.sock.send_multipart(frames)
        return len(frames[1])

    def publish_raw(self, topic: str, payload_bytes: bytes) -> None:
        self.sock.send_multipart([topic.encode("ascii"), payload_bytes])


class Puller(_Socket):
    """The engine's PULL for orders and control messages."""

    def __init__(self, port: int, context: zmq.Context | None = None) -> None:
        super().__init__(zmq.PULL, context)
        self._bind(port)
        self.poller = zmq.Poller()
        self.poller.register(self.sock, zmq.POLLIN)

    def recv(self, timeout_ms: int) -> tuple[str, dict] | None:
        """One decoded message, or None after ``timeout_ms``.  Malformed frames are logged and dropped."""
        if not self.poller.poll(timeout_ms):
            return None
        frames = self.sock.recv_multipart()
        try:
            return decode(frames)
        except (ValueError, UnicodeDecodeError) as e:
            log.warning("dropped malformed message: %s", e)
            return None


class Subscriber(_Socket):
    """A terminal's SUB, subscribed to every topic."""

    def __init__(self, port: int, context: zmq.Context | None = None) -> None:
        super().__init__(zmq.SUB, context)
        self.sock.setsockopt(zmq.SUBSCRIBE, b"")
        self._connect(port)
        self.poller = zmq.Poller()
        self.poller.register(self.sock, zmq.POLLIN)

    def recv(self, timeout_ms: int) -> tuple[str, dict, bytes] | None:
        """(topic, payload, raw payload bytes) or None after the timeout."""
        if not self.poller.poll(timeout_ms):
            return None
        frames = self.sock.recv_multipart()
        try:
            topic, payload = decode(frames)
        except (ValueError, UnicodeDecodeError) as e:
            log.warning("dropped malformed message: %s", e)
            return None
        return topic, payload, frames[1]


class Pusher(_Socket):
    """T1's and the launcher's PUSH into the engine."""

    def __init__(self, port: int, context: zmq.Context | None = None) -> None:
        super().__init__(zmq.PUSH, context)
        self._connect(port)

    def send(self, topic: str, payload: dict) -> None:
        self.sock.send_multipart(encode(topic, payload))


def payload_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
