# CLAUDE.md — Master of Starbour

Standing instructions for Claude Code in this repository. Read fully at session start.

## What this is

A text-based, turn-based starship strategy game (Python 3.12, Windows 10/11).
Four Rich-coloured terminals (T1 Command, T2 Grid, T3 Output, T4 Status) plus a
headless engine and a launcher, talking over ZeroMQ on localhost. The engine is the
sole owner of game state. All captains' orders resolve simultaneously in the fixed
phase order TP-01…08. Packaged with PyInstaller as a one-folder zip on GitHub Releases.

The game has its own universe, ships and names. Star Trek and similar franchises are
genre references only — never import canonical lore, names or terms (D-03).

## Source of truth, in order

1. `docs/master_of_starbour_blueprint.xlsx` — the design workbook. **Do not edit it.**
2. `docs/DESIGN.md` — generated from the workbook by `tools/blueprint_to_design.py`;
   every row keeps its ID (OV-07, CMD-06, HZ-02, CFG-17 …). Read the sections a task
   needs; do not edit by hand.
3. `docs/DECISIONS.md` — D-01…D-53 from the workbook; D-54+ kit decisions. Append,
   never rewrite.
4. `controls_config.ini` — every tunable number. The game reads it; it hard-codes
   nothing that appears there.
5. `specs/Mn.md` — the milestone you are building. The spec cites IDs; so do you.

Status words mean exactly this: **Agreed** = Usman's decision, build it. **Proposed**
= Claude's placeholder awaiting playtest or his yes — build it as the default, keep it
configurable, never argue it is settled. **Draft** = reworded from his blueprint, not
confirmed. **Needs detail** = open.

When something is not covered by the workbook: pick the simplest default, tag it
`[ASSUMED]` in code comments and the spec notes, add a D-nn row to
`docs/DECISIONS.md` (Status: Proposed), and say so in your final report. Do not
silently invent rules.

## Environment — fixed facts (verify, then use)

| Item | Value |
|---|---|
| Repo root | `E:\CODE\master-of-starbour` (public: github.com/selphaware/master-of-starbour) |
| Interpreter | `E:\CODE\master-of-starbour\MOS\Scripts\python.exe` — the venv **MOS**, created by Usman. Never create another venv. Every command, script, hook and gate calls this path explicitly; never bare `python`. |
| Python | 3.12 |
| Shell | PowerShell. Paths are Windows paths. |
| Git | SSH remote `git@github.com:selphaware/master-of-starbour.git`. Commit after every green gate with a one-line message naming the spec step; do not push unless asked. |
| Runtime deps | rich, prompt_toolkit, pyzmq (install into MOS if missing; record in `pyproject.toml`) |
| Dev deps | pytest, pytest-timeout, psutil, openpyxl, pyinstaller |
| Ports | ZeroMQ `[bus] pub_port = 5555`, `pull_port = 5556` (CFG-59); gates bind random ports |

Bare `python` on Windows may be the Microsoft Store 0-byte stub. If any command
shows `Python was not found`, that is why — use the absolute path.

## Definition of done

A spec step is done when the gates say so, not when the code looks right.

```
E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\smoke_test.py . --quick   # runs on every write (hook), ≤ 15 s
E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\smoke_test.py .           # the step gate, ≤ 90 s
E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\live_test.py .            # the milestone gate: real windows, ≤ 3 min
```

Exit 0 = pass. Non-zero = fail, with a specific message. Exit 3 + `HARNESS ERROR` =
the environment is broken, not the code — stop and report, do not "fix" the game.

`docs/BOOTSTRAP.md` says what the gates must catch; `specs/Mn.md` says what the
milestone adds to them. The gates exist to catch **broken but passes tests**:
turns don't advance · AI never acts · a terminal shows last turn's state · orphaned
processes after T1 or the launcher closes · the exe opens and exits · game over not
fired on destruction · mutiny not fired at zero supplies · mastery not fired ·
a config key silently ignored.

## Autonomy

- Work through gate failures on your own. Read the gate's message; it names the
  check and the ID.
- **Three strikes:** if the same check fails three times for the same reason, stop.
  Say which of these is wrong: the spec, the gate, or the brief. Do not iterate a
  fourth time and do not weaken the gate.
- Stop also when an external dependency is unreachable (pip index, GitHub) or when
  a decision cannot be undone and the spec does not settle it.
- Never edit `verify/`, `.claude/settings.json`, `docs/master_of_starbour_blueprint.xlsx`,
  `docs/DESIGN.md` or `docs/config_keys.json`. A builder that can edit its own judge
  will eventually edit its own judge. If a gate is wrong, say so in the report.
- Never change the *structure* of `controls_config.ini` without a D-nn row. Adding
  a key is allowed when the spec asks for it or an `[ASSUMED]` rule needs one.

## Architecture rules the gates depend on

- **Engine owns state** (BUS-04). Terminals are views: subscribe → render → print.
  They hold no game logic and never write game state.
- **Everything goes through the engine** (D-57). T1 and the launcher PUSH to the
  engine's PULL socket; the engine is the only PUB. `view.changed` and
  `system.shutdown` are re-published by the engine. Wire format: ZeroMQ multipart
  `[topic, JSON]`, UTF-8; topics are the MSG names (D-58).
- **`turn.resolved` carries a full snapshot** every turn (BUS-07, MSG-03) plus the
  turn's `events[]`. Snapshot bytes and redraw time are logged; the target is a
  redraw under 0.5 s (BUS-05).
- **Headless simulation is a first-class entry point** (D-55):
  `python -m starbour sim --seed N --turns T --ai-player --report PATH`. The engine runs
  in-process with an AI-controlled player and writes a JSON report. The gates drive
  this; keep it working on every change.
- **Pure cores, thin shells.** Order parsing/validation (`starbour/commands`), turn
  resolution (`starbour/engine/phases.py`), AI decisions (`starbour/engine/ai.py`) and every
  terminal's `render(snapshot, view)` are pure functions of their inputs and are
  unit-tested without sockets, windows or sleeps.
- **Determinism** (D-67): world generation uses `random.Random(seed)`; in-game
  rolls use a separate `random.Random` derived from the seed, advanced only inside
  the engine. Same seed + same orders = identical snapshots.
- **No orphans** (BUS-06, D-59): the launcher owns every child; children also exit
  on their own within 2 s if the launcher's PID disappears. Closing T1 or the
  launcher ends the game (autosave first); closing T2/T3/T4 reopens it.
- **Config, not constants.** Any literal that tunes play (a delta, a threshold, a
  price, a size, a glyph) is read from `controls_config.ini` via `starbour/config.py`,
  which validates every key at start-up and fails loudly on a missing one.
- **Paths** (D-61): `controls_config.ini`, `saves/` and `logs/` live next to the exe
  when frozen (`sys.frozen`), else at the repo root. `starbour/paths.py` is the only
  place that decides this.

## Code conventions

- Python 3.12: `from __future__ import annotations`, dataclasses (`slots=True`),
  `pathlib`, type hints everywhere, no `print` outside terminals (use `logging`).
- Package `starbour` (D-86 — not `mos`: on NTFS that is the venv folder `MOS\`). Layout in `specs/M1.md`. Entry point `python -m starbour`.
- Tests in `tests/`, pytest, `pytest-timeout` on anything that touches sockets or
  processes (10 s default). Mark slow tests `@pytest.mark.slow`; `--quick` skips them.
- Cite blueprint IDs in docstrings of the function that implements a rule:
  `"""Impulse move — MV-01, CMD-06."""`. A grep for an ID should land on its code,
  its test and its gate check.
- British English in strings, comments and docs. Colour, not color (except in
  library APIs).
- Rich for all terminal output; one palette module shared by all four terminals
  (UX-08). Glyphs come from config (`[ui] glyph_set`), never literals in render code.
- No franchise names anywhere, including test fixtures and placeholder ship names.

## Working with Usman

He is doing due diligence on everything you produce. In every final report:

1. The exact commands you ran and their exit codes (gates, tests, selftest).
2. What you assumed (`[ASSUMED]`), what you could not verify (`[VERIFY]`), what is
   fragile (`[RISK]`) — as a short **Challenge** section.
3. Anything in the spec or brief that turned out to be wrong, said plainly.
4. How he can check the result himself in one or two commands.

Bottom line first. Concise. No claims about the environment that did not come from
a command you ran.
