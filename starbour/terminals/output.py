"""T3 Output process: the event log with a scrollback of [ui] log_lines lines (T3, OB-01...04, OUT-13/14, OUT-15).

Each ``turn.resolved`` appends its rendered block (events grouped by phase); ``event.log``
and ``order.rejected`` append single lines.  The window is fully redrawn from the
scrollback each time (GRD-06 style), so it never shows a stale turn.

The mastery ceremony (EG-08, D-159, C12): on ``game.mastery`` - the message, never a loaded
save - T3 animates ``render_output.fireworks_frames`` for ``[ui] ceremony_seconds`` from its idle
hook, then prints the block-letter title into the scrollback.  The engine never waits for it and
T3 keeps consuming ``turn.resolved`` meanwhile (a turn that arrives mid-ceremony lands in the
scrollback and the heartbeat).  The C5 heartbeat carries ``ceremony``: null | running | done.
"""
from __future__ import annotations

import logging
import random
import time
from collections import deque

from rich.console import Group, RenderableType
from rich.text import Text

from starbour.bus import messages as M
from starbour.config import GameConfig
from starbour.terminals import palette as P
from starbour.terminals._base import RECV_MS, ViewTerminal
from starbour.terminals.render_output import fireworks_frames, game_over_lines, log_line, title_frame, turn_lines

log = logging.getLogger("starbour.terminal")
FRAME_S = 0.12           # the ceremony's frame interval (display pacing, not a play tunable)
CEREMONY_RECV_MS = 40    # poll faster while animating so frames and turns both flow


class OutputTerminal(ViewTerminal):
    def __init__(self, cfg: GameConfig, pub_port: int) -> None:
        super().__init__("output", cfg, pub_port, lambda *a, **k: Text(""), "T3 Output")
        self.lines: deque[Text] = deque(maxlen=max(10, cfg.log_lines))
        self.lines.append(Text("T3 Output - events grouped by phase; rejected orders echo here (UX-03)", style=P.STYLES["label"]))
        self.ceremony: str | None = None        # C5 C12: null | running | done
        self.ceremony_until = 0.0
        self.ceremony_rng: random.Random | None = None
        self.frame: Text | None = None
        self.next_frame_at = 0.0

    # ---- drawing
    def renderable(self) -> RenderableType:
        if self.ceremony == "running" and self.frame is not None:
            return Group(self.frame)
        visible = max(1, self.console.height - 1)
        rows = list(self.lines)[-visible:]
        return Group(*rows)

    def heartbeat_fields(self) -> dict:
        return {"ceremony": self.ceremony}

    def recv_ms(self) -> int:
        return CEREMONY_RECV_MS if self.ceremony == "running" else RECV_MS

    def block(self) -> str:
        return P.BLOCK if self.cfg.glyph_set == "unicode" else "#"

    # ---- the ceremony (OUT-15, EG-08, D-159)
    def start_ceremony(self, turn_no: int) -> None:
        self.ceremony = "running"
        self.ceremony_until = time.monotonic() + float(self.cfg.ceremony_seconds)
        self.ceremony_rng = random.Random(f"ceremony-{turn_no}")
        self.next_frame_at = 0.0
        log.info("output: mastery ceremony starts for %.1f s (CFG-89)", float(self.cfg.ceremony_seconds))
        self.on_idle()

    def finish_ceremony(self) -> None:
        self.ceremony = "done"
        self.frame = None
        for row in title_frame(self.console.width, self.block()).plain.splitlines():
            self.lines.append(Text(row, style=P.STYLES["title"], no_wrap=True, overflow="crop"))
        log.info("output: mastery ceremony done")
        self.draw()

    def on_idle(self) -> None:
        if self.ceremony != "running":
            return
        now = time.monotonic()
        if now >= self.ceremony_until:
            self.finish_ceremony()
            return
        if now >= self.next_frame_at:
            glyphs = P.FIREWORK_GLYPHS["unicode" if self.cfg.glyph_set == "unicode" else "ascii"]
            self.frame = fireworks_frames(self.console.width, max(3, self.console.height - 1), self.ceremony_rng or random.Random(0), 1, glyphs)[0]
            self.next_frame_at = now + FRAME_S
            self.draw()

    # ---- messages
    def on_resolved(self, payload: dict) -> None:
        self.accept_snapshot(payload["snapshot"], payload["turn_no"])
        for line in turn_lines(payload, self.console.width, self.cfg):
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
            self.lines.append(log_line(end.cause or "MASTER OF STARBOUR", "highlight", "mastery"))
            self.start_ceremony(int(payload.get("turn_no") or end.turn_no or 0))


def main(cfg: GameConfig, pub_port: int) -> int:
    return OutputTerminal(cfg, pub_port).run()
