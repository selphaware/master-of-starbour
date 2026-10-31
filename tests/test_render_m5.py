"""The M5 renders (C4, C5, C12): T4's PNL-08 progress and Lodestone lines, the Master header and ∞, T3's fireworks frames, title frame and
the still render of a mastery turn, and the T3 process's ceremony state machine (EG-08, OUT-15, D-158, D-159)."""
from __future__ import annotations

import dataclasses
import io
import random
import re

from rich.console import Console

from starbour.config import GameConfig
from starbour.engine.state import GameState
from starbour.terminals import palette as P
from starbour.terminals import render_output, render_status
from starbour.terminals.output import OutputTerminal
from starbour.terminals.status import StatusTerminal
from starbour.terminals._base import make_console


def plain(renderable, width: int = 120) -> str:
    con = Console(file=io.StringIO(), width=width, record=True, force_terminal=False, color_system=None, legacy_windows=False, highlight=False)
    con.print(renderable)
    return con.export_text(styles=False)


def snap_with(small_state: GameState, **cap_fields) -> dict:
    snap = small_state.snapshot()
    snap["captains"][snap["player_id"]].update(cap_fields)
    return snap


# ---------------------------------------------------------------- T4 (PNL-08, EG-08, C12)

def test_status_progress_line_shows_the_unmet_thresholds(small_state: GameState, small_cfg: GameConfig) -> None:
    snap = snap_with(small_state, gold=100, silver=0, fear=95, respect=85, items={"orichalcum": 1, "adamantine": 1})
    text = plain(render_status.render(snap, None, small_cfg))
    line = next(ln for ln in text.splitlines() if "Mastery" in ln)
    assert re.search(r"respect\D{0,4}85\s*/\s*90\b", line) and re.search(r"specials\D{0,4}2\s*/\s*3\b", line), line
    assert "wealth ✓" in line and "fear ✓" in line
    assert "Lodestone" not in text and "MASTER OF STARBOUR" not in text


def test_status_lodestone_line_after_placement_and_master_header(small_state: GameState, small_cfg: GameConfig) -> None:
    snap = small_state.snapshot()
    snap["lodestone"] = {"sector": "1,0/2,2", "cell": [5, 5], "distance": 3, "placed_turn": 1, "guardians": ["c-900", "c-901"], "guardians_alive": 2,
                         "picked_up_turn": None}
    text = plain(render_status.render(snap, None, small_cfg))
    line = next(ln for ln in text.splitlines() if "Lodestone" in ln)
    assert "1,0/2,2" in line and "guardians 2 alive" in line and "Mastery" not in text
    assert render_status.guardians_alive(snap) == 2 and render_status.master_flag(snap) is False
    snap["captains"][snap["player_id"]].update(master=True, infinite_wealth=True, fear=100, respect=100)
    snap["mastery_turn"] = 9
    text = plain(render_status.render(snap, None, small_cfg))
    assert "MASTER OF STARBOUR" in text.splitlines()[0] and text.count("∞") >= 4, "header title, ∞ for gold, silver, food, water"
    assert render_status.master_flag(snap) is True
    ascii_cfg = dataclasses.replace(small_cfg, glyph_set="ascii")
    text = plain(render_status.render(snap, None, ascii_cfg))
    assert "* MASTER OF STARBOUR *" in text and "inf" in text
    term = StatusTerminal(small_cfg, 5555)
    term.snapshot = snap
    assert term.heartbeat_fields()["master"] is True and term.heartbeat_fields()["lodestone_guardians_alive"] == 2


# ---------------------------------------------------------------- T3 (OUT-15, EG-08, D-159)

def test_fireworks_frames_fit_differ_and_are_pure() -> None:
    frames = render_output.fireworks_frames(120, 20, random.Random(7), 3)
    assert len(frames) == 3
    for f in frames:
        rows = f.plain.split("\n")
        assert len(rows) == 20 and all(len(r) <= 120 for r in rows) and f.plain.strip()
    assert len({f.plain for f in frames}) == 3
    again = render_output.fireworks_frames(120, 20, random.Random(7), 3)
    assert [f.plain for f in again] == [f.plain for f in frames], "a pure function of the rng"
    assert render_output.fireworks_frames(80, 10, random.Random(1), 0) == []


def test_title_frame_has_the_font_and_the_words() -> None:
    t = render_output.title_frame(120)
    rows = [r for r in t.plain.splitlines() if r.strip()]
    assert len(rows) >= 6 and all(len(r) <= 120 for r in t.plain.splitlines())
    assert "master of starbour" in re.sub(r"\s+", " ", t.plain.lower())
    assert P.BLOCK in t.plain
    narrow = render_output.title_frame(60)
    assert all(len(r) <= 60 for r in narrow.plain.splitlines()) and len([r for r in narrow.plain.splitlines() if r.strip()]) >= 11, "two lines of letters"
    tiny = render_output.title_frame(20)
    assert "MASTER OF STARBOUR" in tiny.plain and all(len(r) <= 20 for r in tiny.plain.splitlines())
    hashed = render_output.title_frame(120, "#")
    assert "#" in hashed.plain and P.BLOCK not in hashed.plain
    assert len(P.title_rows("MASTER OF STARBOUR")) == 5 and all(len(set(P.title_rows("M"))) for _ in [0])


def test_output_render_of_a_mastery_turn_carries_the_title(small_state: GameState, small_cfg: GameConfig) -> None:
    payload = {"turn_no": 4, "snapshot": small_state.snapshot(),
               "events": [{"phase": "TP-05", "type": "mastery", "severity": "highlight", "text": "MASTER OF STARBOUR - held", "ids": ["OUT-15"]}]}
    text = plain(render_output.render(payload, None, small_cfg, 120), 120)
    assert "MASTER OF STARBOUR" in text and P.BLOCK in text
    plain_turn = plain(render_output.render({"turn_no": 4, "snapshot": payload["snapshot"], "events": []}, None, small_cfg, 120), 120)
    assert P.BLOCK not in plain_turn


def test_output_terminal_ceremony_runs_then_finishes_and_keeps_consuming(small_state: GameState, small_cfg: GameConfig) -> None:
    out = io.StringIO()
    term = OutputTerminal(dataclasses.replace(small_cfg, ceremony_seconds=0), 5555)
    term.console = make_console()
    term.console.file = out
    assert term.heartbeat_fields() == {"ceremony": None}
    term.on_other("game.mastery", {"turn_no": 4, "cause": "held", "summary": {}, "captain": "c-000", "item": "Lodestone of Starbour"})
    assert term.ceremony == "done" and any("MASTER OF STARBOUR" in ln.plain for ln in term.lines)
    long = OutputTerminal(dataclasses.replace(small_cfg, ceremony_seconds=30), 5555)
    long.console = make_console()
    long.console.file = io.StringIO()
    long.on_other("game.mastery", {"turn_no": 4, "cause": "held", "summary": {}})
    assert long.ceremony == "running" and long.frame is not None and long.recv_ms() < 250
    snap = small_state.snapshot()
    long.on_resolved({"turn_no": 5, "snapshot": snap, "events": [{"phase": "TP-08", "type": "summary", "severity": "info", "text": "Turn 5 resolved", "ids": []}]})
    assert long.turn_no == 5 and long.ceremony == "running", "a turn arriving mid-ceremony is consumed and drawn into the heartbeat"
    long.ceremony_until = 0.0
    long.on_idle()
    assert long.ceremony == "done" and long.frame is None and long.recv_ms() == 250
