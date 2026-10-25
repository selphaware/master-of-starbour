"""Topic names and payload dataclasses for MSG-01...09 plus state.hello (CONTRACTS C3, D-58).

Wire format: ZeroMQ multipart ``[topic (ASCII), JSON (UTF-8)]``.  Pure: no sockets here.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

ORDER_SUBMITTED = "order.submitted"   # MSG-01  T1 -> engine
TURN_END = "turn.end"                 # MSG-02  T1 -> engine
TURN_RESOLVED = "turn.resolved"       # MSG-03  engine -> all
VIEW_CHANGED = "view.changed"         # MSG-04  T1 -> engine -> all (D-57)
EVENT_LOG = "event.log"               # MSG-05  engine -> all
ORDER_REJECTED = "order.rejected"     # MSG-06  engine -> all
ORDER_ACCEPTED = "order.accepted"     # MSG-07  engine -> all
SYSTEM_SHUTDOWN = "system.shutdown"   # MSG-08  launcher/T1 -> engine -> all
GAME_OVER = "game.over"               # MSG-09  engine -> all
GAME_MASTERY = "game.mastery"         # MSG-09  engine -> all
STATE_HELLO = "state.hello"           # [ASSUMED] D-70, D-83  engine -> all, every 2 s

TOPICS = (ORDER_SUBMITTED, TURN_END, TURN_RESOLVED, VIEW_CHANGED, EVENT_LOG, ORDER_REJECTED, ORDER_ACCEPTED,
          SYSTEM_SHUTDOWN, GAME_OVER, GAME_MASTERY, STATE_HELLO)

HELLO_INTERVAL_S = 2.0   # D-70 / D-83: a protocol constant, not a play tunable


def encode(topic: str, payload: dict) -> list[bytes]:
    """D-58: frame 0 topic (ASCII), frame 1 JSON (UTF-8)."""
    return [topic.encode("ascii"), json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")]


def decode(frames: list[bytes]) -> tuple[str, dict]:
    """Inverse of ``encode``; raises ValueError for anything malformed."""
    if len(frames) != 2:
        raise ValueError(f"expected 2 frames, got {len(frames)} (D-58)")
    topic = frames[0].decode("ascii")
    payload = json.loads(frames[1].decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"payload for {topic} is not a JSON object (D-58)")
    return topic, payload


@dataclass(frozen=True, slots=True)
class OrderSubmitted:
    """MSG-01."""

    command: str
    args: list[str]
    ship_id: str
    turn_no: int
    client: str = "command"
    topic = ORDER_SUBMITTED

    def to_dict(self) -> dict:
        return {"command": self.command, "args": list(self.args), "ship_id": self.ship_id, "turn_no": self.turn_no,
                "client": self.client}

    @classmethod
    def from_dict(cls, d: dict) -> "OrderSubmitted":
        return cls(str(d["command"]), [str(a) for a in d.get("args") or []], str(d.get("ship_id") or ""),
                   int(d.get("turn_no") or 0), str(d.get("client") or "command"))


@dataclass(frozen=True, slots=True)
class TurnEnd:
    """MSG-02."""

    turn_no: int
    topic = TURN_END

    def to_dict(self) -> dict:
        return {"turn_no": self.turn_no}

    @classmethod
    def from_dict(cls, d: dict) -> "TurnEnd":
        return cls(int(d.get("turn_no") or 0))


@dataclass(frozen=True, slots=True)
class TurnResolved:
    """MSG-03: the full snapshot plus the turn's events (BUS-07)."""

    turn_no: int
    snapshot: dict
    events: list[dict] = field(default_factory=list)
    topic = TURN_RESOLVED

    def to_dict(self) -> dict:
        return {"turn_no": self.turn_no, "snapshot": self.snapshot, "events": list(self.events)}

    @classmethod
    def from_dict(cls, d: dict) -> "TurnResolved":
        return cls(int(d["turn_no"]), dict(d["snapshot"]), list(d.get("events") or []))


@dataclass(frozen=True, slots=True)
class ViewChanged:
    """MSG-04: ``{"level": "sector", "focus": {...}}`` or ``{"level": "follow"}``."""

    level: str
    focus: dict | None = None
    topic = VIEW_CHANGED

    def to_dict(self) -> dict:
        d: dict = {"level": self.level}
        if self.focus is not None:
            d["focus"] = dict(self.focus)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ViewChanged":
        focus = d.get("focus")
        return cls(str(d.get("level") or "follow"), dict(focus) if isinstance(focus, dict) else None)


@dataclass(frozen=True, slots=True)
class EventLog:
    """MSG-05."""

    turn_no: int
    type: str
    severity: str
    text: str
    topic = EVENT_LOG

    def to_dict(self) -> dict:
        return {"turn_no": self.turn_no, "type": self.type, "severity": self.severity, "text": self.text}

    @classmethod
    def from_dict(cls, d: dict) -> "EventLog":
        return cls(int(d.get("turn_no") or 0), str(d.get("type") or "system"), str(d.get("severity") or "info"),
                   str(d.get("text") or ""))


@dataclass(frozen=True, slots=True)
class OrderRejected:
    """MSG-06."""

    order: dict
    reason: str
    topic = ORDER_REJECTED

    def to_dict(self) -> dict:
        return {"order": dict(self.order), "reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> "OrderRejected":
        return cls(dict(d.get("order") or {}), str(d.get("reason") or ""))


@dataclass(frozen=True, slots=True)
class OrderAccepted:
    """MSG-07.  ``queued`` is the ship's queue length after this order (D-93)."""

    order: dict
    queue_position: int
    queued: int = 0
    text: str = ""
    topic = ORDER_ACCEPTED

    def to_dict(self) -> dict:
        return {"order": dict(self.order), "queue_position": self.queue_position, "queued": self.queued, "text": self.text}

    @classmethod
    def from_dict(cls, d: dict) -> "OrderAccepted":
        return cls(dict(d.get("order") or {}), int(d.get("queue_position") or 0), int(d.get("queued") or 0),
                   str(d.get("text") or ""))


@dataclass(frozen=True, slots=True)
class SystemShutdown:
    """MSG-08."""

    reason: str
    topic = SYSTEM_SHUTDOWN

    def to_dict(self) -> dict:
        return {"reason": self.reason}

    @classmethod
    def from_dict(cls, d: dict) -> "SystemShutdown":
        return cls(str(d.get("reason") or ""))


@dataclass(frozen=True, slots=True)
class GameEnd:
    """MSG-09: game.over or game.mastery."""

    turn_no: int
    cause: str
    summary: dict = field(default_factory=dict)
    topic = GAME_OVER

    def to_dict(self) -> dict:
        return {"turn_no": self.turn_no, "cause": self.cause, "summary": dict(self.summary)}

    @classmethod
    def from_dict(cls, d: dict) -> "GameEnd":
        return cls(int(d.get("turn_no") or 0), str(d.get("cause") or ""), dict(d.get("summary") or {}))


@dataclass(frozen=True, slots=True)
class StateHello:
    """[ASSUMED] D-70: the latest snapshot, every 2 s, so a late terminal can draw at once."""

    turn_no: int
    snapshot: dict
    seed: int
    version: str
    topic = STATE_HELLO

    def to_dict(self) -> dict:
        return {"turn_no": self.turn_no, "snapshot": self.snapshot, "seed": self.seed, "version": self.version}

    @classmethod
    def from_dict(cls, d: dict) -> "StateHello":
        return cls(int(d["turn_no"]), dict(d["snapshot"]), int(d.get("seed") or 0), str(d.get("version") or ""))


PAYLOADS = {
    ORDER_SUBMITTED: OrderSubmitted, TURN_END: TurnEnd, TURN_RESOLVED: TurnResolved, VIEW_CHANGED: ViewChanged,
    EVENT_LOG: EventLog, ORDER_REJECTED: OrderRejected, ORDER_ACCEPTED: OrderAccepted, SYSTEM_SHUTDOWN: SystemShutdown,
    GAME_OVER: GameEnd, GAME_MASTERY: GameEnd, STATE_HELLO: StateHello,
}


def parse_payload(topic: str, payload: dict):
    """The typed dataclass for a topic, or None for an unknown topic."""
    cls = PAYLOADS.get(topic)
    return cls.from_dict(payload) if cls else None
