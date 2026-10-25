"""T3 Output process: the event log with a scrollback of [ui] log_lines lines (T3, OB-01...04, OUT-13/14).

Each ``turn.resolved`` appends its rendered block (events grouped by phase); ``event.log``
and ``order.rejected`` append single lines.  The window is fully redrawn from the
scrollback each time (GRD-06 style), so it never shows a stale turn.
"""
from __future__ import annotations

import logging
from collections import deque

from rich.console import Group, RenderableType
from rich.text import Text

from starbour.bus import messages as M
from starbour.config import GameConfig
from starbour.terminals import palette as P
from starbour.terminals._base import ViewTerminal
from starbour.terminals.render_output import game_over_lines, log_line, turn_lines

log = logging.getLogger("starbour.terminal")


class OutputTerminal(ViewTerminal):
    def __init__(self, cfg: GameConfig, pub_port: int) -> None:
        super().__init__("output", cfg, pub_port, lambda *a, **k: Text(""), "T3 Output")
        self.lines: deque[Text] = deque(maxlen=max(10, cfg.log_lines))
        self.lines.append(Text("T3 Output - events grouped by phase; rejected orders echo here (UX-03)", style=P.STYLES["label"]))

    def renderable(self) -> RenderableType:
        visible = max(1, self.console.height - 1)
        rows = list(self.lines)[-visible:]
        return Group(*rows)

    def on_resolved(self, payload: dict) -> None:
        self.accept_snapshot(payload["snapshot"], payload["turn_no"])
        for line in turn_lines(payload, self.console.width):
            self.lines.append(line)
        self.draw()

    def on_hello(self, payload: dict) -> None:
        snapshot = payload.get("snapshot")
        if not isinstance(snapshot, dict):
            return
        from starbour.engine.state import hash_snapshot
        if hash_snapshot(snapshot) == self.last_hash:
            return
        first = self.snapshot is None
        self.accept_snapshot(snapshot, payload.get("turn_no", snapshot.get("turn_no", 0)))
        if first:
            self.lines.append(log_line(f"joined the game at turn {self.turn_no} (seed {snapshot.get('seed')})", "muted"))
        self.draw()

    def on_view_changed(self, payload: dict) -> None:
        self.view = payload

    def on_other(self, topic: str, payload: dict) -> None:
        if topic == M.EVENT_LOG:
            ev = M.EventLog.from_dict(payload)
            self.lines.append(log_line(ev.text, ev.severity, ev.type))
            self.draw()
        elif topic == M.ORDER_REJECTED:
            rej = M.OrderRejected.from_dict(payload)
            order = rej.order
            text = " ".join([str(order.get("command", "")), *[str(a) for a in order.get("args") or []]]).strip()
            self.lines.append(log_line(f"rejected '{text}': {rej.reason}", "error", "rejected"))
            self.draw()
        elif topic == M.ORDER_ACCEPTED:
            acc = M.OrderAccepted.from_dict(payload)
            if acc.text and acc.order.get("command") in ("new", "ship"):
                self.lines.append(log_line(acc.text, "muted", "accepted"))
                self.draw()
        elif topic == M.GAME_OVER:
            for line in game_over_lines(payload, self.console.width):   # OUT-16: cause, turn, summary, 'new'
                self.lines.append(line)
            self.draw()
        elif topic == M.GAME_MASTERY:
            end = M.GameEnd.from_dict(payload)
            self.lines.append(log_line(f"{topic}: {end.cause}", "highlight", "game"))
            self.draw()


def main(cfg: GameConfig, pub_port: int) -> int:
    return OutputTerminal(cfg, pub_port).run()
