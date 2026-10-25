"""Bus messages - every MSG-* serialises and parses; topic names (C3, D-57, D-58)."""
from __future__ import annotations

import pytest

from starbour.bus import messages as M


def test_topic_names_are_the_msg_names() -> None:
    assert M.ORDER_SUBMITTED == "order.submitted" and M.TURN_END == "turn.end" and M.TURN_RESOLVED == "turn.resolved"
    assert M.VIEW_CHANGED == "view.changed" and M.EVENT_LOG == "event.log" and M.ORDER_REJECTED == "order.rejected"
    assert M.ORDER_ACCEPTED == "order.accepted" and M.SYSTEM_SHUTDOWN == "system.shutdown"
    assert M.GAME_OVER == "game.over" and M.GAME_MASTERY == "game.mastery" and M.STATE_HELLO == "state.hello"
    assert len(set(M.TOPICS)) == 11
    assert M.HELLO_INTERVAL_S == 2.0


@pytest.mark.parametrize("obj", [
    M.OrderSubmitted("move", ["12", "7"], "s-001", 12, "command"),
    M.TurnEnd(12),
    M.TurnResolved(13, {"turn_no": 13, "x": [1, 2]}, [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "moved", "ids": ["MV-01"]}]),
    M.ViewChanged("sector", {"galaxy": [3, 4], "sector": [7, 9]}),
    M.ViewChanged("follow"),
    M.EventLog(12, "system", "muted", "autosaved"),
    M.OrderRejected({"command": "move", "args": ["99", "0"]}, "speed 7 exceeds engine_power 4 (CMD-06)"),
    M.OrderAccepted({"command": "move", "args": ["1", "0"]}, 2, 2, "queued"),
    M.SystemShutdown("command terminal closed"),
    M.GameEnd(57, "destroyed", {"turns": 57}),
    M.StateHello(0, {"turn_no": 0}, 1234, "0.1.0"),
])
def test_payload_round_trip(obj) -> None:
    d = obj.to_dict()
    back = type(obj).from_dict(d)
    assert back == obj
    frames = M.encode(obj.topic, d)
    assert len(frames) == 2 and frames[0] == obj.topic.encode("ascii")
    topic, payload = M.decode(frames)
    assert topic == obj.topic and payload == d
    assert type(M.parse_payload(topic, payload)) is type(obj) or topic in (M.GAME_OVER, M.GAME_MASTERY)


def test_wire_format_is_utf8_json() -> None:
    frames = M.encode(M.EVENT_LOG, {"text": "café ★"})
    assert frames[1].decode("utf-8") == '{"text":"café ★"}'
    with pytest.raises(ValueError):
        M.decode([b"only-one-frame"])
    with pytest.raises(ValueError):
        M.decode([b"turn.end", b"[1, 2, 3]"])


def test_c3_examples_parse() -> None:
    sub = M.OrderSubmitted.from_dict({"command": "move", "args": ["12", "7"], "ship_id": "s-001", "turn_no": 12, "client": "command"})
    assert sub.args == ["12", "7"] and sub.turn_no == 12
    vc = M.ViewChanged.from_dict({"level": "follow"})
    assert vc.level == "follow" and vc.focus is None and vc.to_dict() == {"level": "follow"}
    hello = M.StateHello.from_dict({"turn_no": 0, "snapshot": {"turn_no": 0}, "seed": 1234, "version": "0.1.0"})
    assert hello.seed == 1234
