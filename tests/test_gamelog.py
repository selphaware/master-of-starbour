"""Gameplay logs (M3 step 2, D-135): AI decision traces, the text and JSON-lines writers, the terminals' scrollback clear."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from starbour.commands.parse import parse
from starbour.config import GameConfig
from starbour.engine import ai
from starbour.engine import orders as O
from starbour.engine.gamelog import GameLogs, decision_line, event_line, log_names, turn_lines, turn_row
from starbour.engine.server import Engine
from starbour.engine.state import GameState
from starbour.terminals._base import SCROLLBACK_CLEAR, clear_screen, make_console
from starbour.world.generate import game_rng
from tests.test_combat import arena, spawn


def traced(state: GameState, cfg: GameConfig, cap, **kw) -> tuple[list, list[ai.Decision]]:
    trace: list[ai.Decision] = []
    return ai.decide(cap, state, game_rng(1), cfg, trace=trace, **kw), trace


# ---------------------------------------------------------------- AI decision traces

def test_trace_does_not_change_what_the_ai_decides(small_state: GameState, small_cfg: GameConfig) -> None:
    arena(small_state)
    cap, _ = spawn(small_state, "corsair", (6, 5))
    plain = [(o.command, o.args) for o in ai.decide(cap, small_state, game_rng(1), small_cfg)]
    got, trace = traced(small_state, small_cfg, cap)
    assert [(o.command, o.args) for o in got] == plain
    assert len(trace) == 1


def test_trace_names_the_reason_and_rules_for_opening_a_battle(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "corsair", (6, 5))
    _, trace = traced(small_state, small_cfg, cap)
    d = trace[0]
    assert (d.captain_id, d.ship_id) == (cap.id, foe.id)
    assert f"fires torpedo+phaser at {ship.id}" in d.text and "opens a battle" in d.text
    assert d.ids == ("CM-10", "EF-01")


def test_trace_in_battle_not_weaker_matches_the_documented_line(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "bastion", (6, 5))
    O.submit(small_state, parse(f"fire phaser {foe.id}", ship.id), small_cfg)
    small_state.turn_no = 3
    _, trace = traced(small_state, small_cfg, cap, passive=True)
    line = decision_line(3, trace[0].to_dict())
    assert line.startswith(f"T3 {cap.id} {cap.name}: in battle b-001, not weaker")
    assert f"no peace; fires torpedo+phaser at {ship.id}" in line
    assert line.endswith("(CM-10, D-125)")


def test_trace_gives_each_yielding_reason(small_state: GameState, small_cfg: GameConfig) -> None:
    ship = arena(small_state)
    cap, foe = spawn(small_state, "bastion", (6, 5), torpedoes=0, phasers=1)
    O.submit(small_state, parse(f"fire phaser {foe.id}", ship.id), small_cfg)
    small_state.turn_no = 1
    bt = small_state.battles["b-001"]
    foe.shield_current = 30.0
    assert "weaker" in traced(small_state, small_cfg, cap)[1][0].text and "offers peace" in traced(small_state, small_cfg, cap)[1][0].text
    bt.offers[small_state.player_id] = 1
    assert "accepts it" in traced(small_state, small_cfg, cap)[1][0].text
    bt.offers.clear()
    foe.shield_current = 10.0
    text = traced(small_state, small_cfg, cap)[1][0].text
    assert "escapes: moves from" in text and "EF-02" in traced(small_state, small_cfg, cap)[1][0].ids
    foe.shield_current = 5.0
    small_state.player.fear = small_cfg.yield_fear_min
    got, trace = traced(small_state, small_cfg, cap)
    assert got[0].command == "surrender" and "surrenders" in trace[0].text


def test_trace_covers_walk_and_passive_hold(small_state: GameState, small_cfg: GameConfig) -> None:
    arena(small_state)
    cap, foe = spawn(small_state, "corsair", (11, 11))
    small_state.player.fear = 90   # no target worth attacking
    got, trace = traced(small_state, small_cfg, cap)
    assert got and "random walk" in trace[0].text and trace[0].ids == ("OV-10",)
    got, trace = traced(small_state, small_cfg, cap, passive=True)
    assert got == [] and "holds" in trace[0].text and trace[0].ids == ("D-125",)


# ---------------------------------------------------------------- pure formatters

def test_event_line_keeps_the_text_untruncated_and_on_one_line() -> None:
    long = "x" * 600
    line = event_line(7, {"phase": "TP-04", "type": "combat", "severity": "alert", "text": f"a\nb {long}", "ids": ["OUT-04", "CM-10"]})
    assert line == f"T7 TP-04 alert/combat: a / b {long} [OUT-04, CM-10]"
    assert "\n" not in line


def test_turn_lines_are_in_phase_order_with_decisions_leading() -> None:
    events = [{"phase": "TP-06", "type": "upkeep", "severity": "info", "text": "six", "ids": []},
              {"phase": "TP-02", "type": "movement", "severity": "info", "text": "two", "ids": []},
              {"phase": "TP-01", "type": "orders", "severity": "muted", "text": "one", "ids": []},
              {"phase": "TP-02", "type": "movement", "severity": "info", "text": "two b", "ids": []}]
    decisions = [{"captain": "c-001", "name": "A B", "ship": "s-001", "text": "walks", "ids": ["OV-10"]}]
    lines = turn_lines(4, events, decisions)
    assert [ln.split(":")[0] for ln in lines] == ["T4 c-001 A B", "T4 TP-01 muted/orders", "T4 TP-02 info/movement", "T4 TP-02 info/movement", "T4 TP-06 info/upkeep"]
    assert lines[2].endswith("two") and lines[3].endswith("two b"), "events keep their order within a phase"
    assert lines[0] == "T4 c-001 A B: walks (OV-10)"


# ---------------------------------------------------------------- the writers

def run_engine(cfg: GameConfig, tmp_path: Path, turns: int, seed: int = 99) -> Engine:
    engine = Engine(cfg, seed=seed, save_path=tmp_path / "saves" / "game.json", bus=False, ai_player=True, max_turns=turns, resume=False,
                    log_dir=tmp_path / "logs")
    for _ in range(turns):
        engine.resolve_and_publish()
    return engine


def test_engine_writes_both_logs_each_turn(small_cfg: GameConfig, tmp_path: Path) -> None:
    run_engine(small_cfg, tmp_path, 3)
    text_name, jsonl_name = log_names(99)
    text = (tmp_path / "logs" / text_name).read_text(encoding="utf-8").splitlines()
    rows = [json.loads(ln) for ln in (tmp_path / "logs" / jsonl_name).read_text(encoding="utf-8").splitlines()]
    assert text[0].startswith("# Master of Starbour gameplay log - seed 99")
    assert [r["turn_no"] for r in rows] == [1, 2, 3]
    for turn in (1, 2, 3):
        mine = [ln for ln in text if ln.startswith(f"T{turn} ")]
        assert any(f" TP-08 info/summary: Turn {turn} resolved" in ln for ln in mine)
        phases = [ln.split()[1] for ln in mine if ln.split()[1].startswith("TP-")]
        assert phases == sorted(phases), "events are in phase order"
        assert any(" c-" in ln.split(":")[0] for ln in mine), "an AI decision line per turn"
    first = rows[0]
    for key in ("turn_no", "orders_executed", "ai_ships_moved", "player", "captains", "events", "snapshot_hash", "snapshot_bytes", "resolve_ms"):
        assert key in first, f"C2 per_turn key {key}"
    assert first["events"] == [e["text"] for e in first["event_records"]]
    assert first["ai_decisions"] and set(first["ai_decisions"][0]) >= {"captain", "name", "ship", "text", "ids"}
    assert len(first["snapshot_hash"]) == 40


def test_jsonl_row_matches_the_sim_report_row(small_cfg: GameConfig, tmp_path: Path) -> None:
    """The jsonl line is the C2 per_turn row (the sim report uses the same builder) plus the two additive keys."""
    engine = run_engine(small_cfg, tmp_path, 2)
    row = json.loads((tmp_path / "logs" / log_names(99)[1]).read_text(encoding="utf-8").splitlines()[-1])
    stats = dict(engine.state.last_stats)
    stats.update({"snapshot_hash": row["snapshot_hash"], "snapshot_bytes": row["snapshot_bytes"], "resolve_ms": row["resolve_ms"]})
    expected = turn_row(engine.state, [{"text": t} for t in row["events"]], stats)
    assert {k: v for k, v in row.items() if k not in ("event_records", "ai_decisions")} == json.loads(json.dumps(expected))


def test_new_game_starts_new_files_and_resume_appends(small_cfg: GameConfig, tmp_path: Path) -> None:
    run_engine(small_cfg, tmp_path, 2)
    text_path = tmp_path / "logs" / log_names(99)[0]
    jsonl_path = tmp_path / "logs" / log_names(99)[1]
    n_text = len(text_path.read_text(encoding="utf-8").splitlines())
    # a resumed game (same save) appends
    resumed = Engine(small_cfg, save_path=tmp_path / "saves" / "game.json", bus=False, ai_player=True, resume=True, log_dir=tmp_path / "logs")
    assert resumed.resumed and resumed.state.turn_no == 2
    resumed.resolve_and_publish()
    assert len(text_path.read_text(encoding="utf-8").splitlines()) > n_text
    assert [json.loads(ln)["turn_no"] for ln in jsonl_path.read_text(encoding="utf-8").splitlines()] == [1, 2, 3]
    # `new` with the same seed starts the files afresh; another seed gets its own files
    from starbour.commands.parse import Order
    resumed.handle_new(Order("new", ("99",), "", "immediate"))
    assert jsonl_path.read_text(encoding="utf-8") == "" and len(text_path.read_text(encoding="utf-8").splitlines()) == 1
    resumed.handle_new(Order("new", ("100",), "", "immediate"))
    assert (tmp_path / "logs" / log_names(100)[0]).is_file() and resumed.gamelog is not None and resumed.gamelog.seed == 100


def test_no_log_dir_writes_nothing(small_cfg: GameConfig, tmp_path: Path) -> None:
    engine = Engine(small_cfg, seed=99, bus=False, ai_player=True, resume=False)
    engine.resolve_and_publish()
    assert engine.gamelog is None


def test_an_unwritable_log_never_stops_a_turn(small_cfg: GameConfig, tmp_path: Path) -> None:
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the folder should be", encoding="utf-8")
    engine = Engine(small_cfg, seed=99, bus=False, ai_player=True, resume=False, log_dir=blocker)
    assert engine.gamelog is not None and engine.gamelog.broken
    engine.resolve_and_publish()
    assert engine.state.turn_no == 1


def test_game_logs_survive_the_player_ship_being_lost(small_state: GameState, tmp_path: Path) -> None:
    """turn_row falls back to stats['player_lost'] when there is no active ship (game over)."""
    logs = GameLogs(tmp_path, small_state.seed)
    small_state.turn_no = 5
    small_state.ships.pop(small_state.player.active_ship, None)
    logs.append_turn(small_state, [{"phase": "TP-03", "type": "destroyed", "severity": "alert", "text": "lost", "ids": []}],
                     {"player_lost": {"ship_class": "kestrel", "position": None}})
    row = json.loads(logs.jsonl_path.read_text(encoding="utf-8"))
    assert row["player"]["ship_class"] == "kestrel" and row["events"] == ["lost"]


# ---------------------------------------------------------------- scrollback clear (T2, T3, T4)

def terminal_console() -> tuple:
    out = io.StringIO()
    console = make_console()
    console.file = out
    return console, out


def test_clear_screen_clears_the_scrollback_after_the_viewport() -> None:
    console, out = terminal_console()
    assert console.is_terminal
    clear_screen(console)
    text = out.getvalue()
    assert "\x1b[2J" in text and text.endswith(SCROLLBACK_CLEAR)
    assert text.index("\x1b[2J") < text.index(SCROLLBACK_CLEAR)


def test_clear_screen_writes_no_control_codes_to_a_pipe() -> None:
    from rich.console import Console
    out = io.StringIO()
    clear_screen(Console(file=out, force_terminal=False))
    assert out.getvalue() == ""


@pytest.mark.parametrize("role", ["grid", "output", "status"])
def test_every_full_redraw_clears_the_scrollback(role: str, cfg: GameConfig, small_state: GameState) -> None:
    from starbour.terminals._base import ViewTerminal
    from starbour.terminals.output import OutputTerminal
    from starbour.terminals.render_grid import render
    from starbour.terminals.status import StatusTerminal
    term = {"grid": lambda: ViewTerminal("grid", cfg, 5555, render, "T2 Grid"),
            "output": lambda: OutputTerminal(cfg, 5555),
            "status": lambda: StatusTerminal(cfg, 5555)}[role]()
    console, out = terminal_console()
    term.console = console
    term.on_resolved({"turn_no": 1, "snapshot": small_state.snapshot(), "events": []})
    text = out.getvalue()
    assert SCROLLBACK_CLEAR in text and text.index(SCROLLBACK_CLEAR) < text.index("\n"), "scrollback cleared before the frame is printed"
