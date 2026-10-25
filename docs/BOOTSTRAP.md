# BOOTSTRAP — generate the build-loop harness for Master of Starbour

You are going to build a **harness**: the deterministic scaffolding that lets an
agent build this game autonomously and verifiably. You are NOT building the game.
You are building the thing that will judge the game.

Read this whole file, then `CLAUDE.md`, then `docs/CONTRACTS.md`, before doing
anything. The design is in `docs/DESIGN.md` (generated from the workbook); read the
sections `2 Architecture`, `1 Overview` (turn phases TP-01…08) and `18 Build &
Release` now. Everything else is reference.

---

## The project

**What it is:**
> Master of Starbour — a text-based, turn-based starship strategy game. Python 3.12
> on Windows 10/11. Six processes: a launcher, a headless engine that owns all game
> state, and four Rich-coloured console windows (T1 Command for typing orders, T2
> Grid for the map, T3 Output for the event log, T4 Status for the permanent status
> panel). They talk over ZeroMQ on localhost: the engine PUBs state, T1 PUSHes
> orders. The player queues any number of orders, types `end`, and every captain's
> orders (player and AI) resolve simultaneously in the fixed phase order TP-01…08.
> Later milestones add movement physics, hazards, combat, trade and the endgame;
> this harness must outlive all of them.

**Data source:**
> None external. The world is generated from a seed (SP-01). Every tunable number is
> read from `controls_config.ini` (already in the repo; 60 keys from tab 15 of the
> workbook, checked by `tests/test_config_matches_blueprint.py`). There is no
> network access at runtime.

**The deliverable:**
> The `mos` package (`python -m mos`), built milestone by milestone from
> `specs/M1.md` … `specs/M5.md`. M1 = engine + bus + grids + launcher + four
> terminals + impulse movement + supplies/mutiny + headless sim. The gate's argument
> is the repo root (`.`).

**What "working" looks like to a user (M1):**
> He runs `launch.ps1`. Four console windows open, each in colour. T2 shows a 24 × 24
> sector with a ▲ for his ship, stars in temperature colours, a planet, Void dots.
> T4 shows his captain's name, gold 5, silver 50, fear and respect bars at 10, food
> 30, water 30, the seed and turn 1. He types `move 3 0` then `end` in T1. Within half
> a second T2 redraws with the ▲ three cells right, T3 prints the turn's events
> grouped by phase (movement, upkeep), T4 shows food 29, water 29, turn 2, and T1's
> prompt reads `[T2 · <ship> · 0 orders] >`. AI ships in his sector move about on
> their own. He closes T2; it reopens by itself. He types `quit`; every window
> closes and `saves\game.json` exists. Nothing is left in Task Manager.

**What "broken but passes tests" looks like:**
> 1. **Turns don't advance.** `end` is accepted, `turn.resolved` is published, but
>    `turn_no` stays at 1 (or the snapshot is byte-identical) because the phase loop
>    returns the old state. Every unit test of each phase passes in isolation.
> 2. **The AI never acts.** AI captains exist in the snapshot and the game runs for
>    500 turns without error, but no AI order is ever generated or executed —
>    `ai.decide()` returns `[]` on a code path nobody tested, so the universe is
>    frozen scenery.
> 3. **A terminal shows last turn's state.** T2/T3/T4 render, in colour, a snapshot
>    from the previous `turn.resolved` (a cached variable, a subscriber that dropped
>    the latest message, a redraw triggered before the parse). The window looks
>    perfect and is one turn stale — invisible to any test that checks only the
>    engine.
> 4. **Orphaned processes.** Closing T1 ends the game on screen, but
>    `python.exe --role engine` keeps running (and holds the ports, so the next
>    launch fails or silently talks to the old engine). Or the launcher exits and
>    leaves four windows behind.
> 5. **The exe opens and exits.** (M5) `mos.exe` starts, spawns nothing or spawns
>    children that die on a missing module, and exits 0.
> 6. **Game over / mutiny / mastery not fired.** The condition is met in state
>    (shields 0 and a hit; food 0 for 3 turns; the ultimate item picked up) but the
>    event, the message and the consequence never happen because the check sits in
>    a phase that isn't reached or on a branch for the wrong captain.
> 7. **A config key silently ignored.** `controls_config.ini` says `food_start = 30`
>    but the code uses a literal 30 somewhere else; changing the file changes
>    nothing. Passes every test because the test used the same literal.
> 8. **Non-determinism.** Same seed, same orders, different snapshot hashes, because
>    something drew from the global `random` or iterated a set.

**Environment (verify every line in Phase 0):**
> - Python interpreter: `E:\CODE\master-of-starbour\MOS\Scripts\python.exe`
>   **— this venv already exists; do not create a new one.** Python 3.12.
> - Node/npm needed? No.
> - Already installed (by Usman, from `pyproject.toml [dev]`): rich, prompt_toolkit,
>   pyzmq, pytest, pytest-timeout, psutil, openpyxl, pyinstaller.
> - You may install additional runtime packages into the interpreter above only if a
>   spec needs them, and you record them in `pyproject.toml`.
> - Repo root `E:\CODE\master-of-starbour` on a local disk. Windows / PowerShell.
> - Ports 5555 and 5556 must be free for live runs; gates bind random ports.
> - Git over SSH; remote `git@github.com:selphaware/master-of-starbour.git`.

> **Note on the venv:** Usman created it before this session because the gate
> self-tests using that interpreter. An agent that builds its own verification
> environment has nothing trustworthy to verify with.

---

The "broken but passes tests" list is the most important thing on this page.
**It defines the gates, and the gates are the whole system.**

---

## Phase 0 — Discover the environment. Do not skip. Do not assume.

Establish facts with commands, record command + output in `docs/ENVIRONMENT.md`,
and report them before writing any harness file.

1. **Interpreter.** Run `E:\CODE\master-of-starbour\MOS\Scripts\python.exe --version`
   and `-c "import sys; print(sys.executable, sys.version)"`. Reject the Microsoft
   Store stub (`...\WindowsApps\python.exe`, 0 bytes) explicitly if any bare
   `python` is ever involved. Never use bare `python` anywhere in the harness.
2. **Packages.** `-c "import rich, prompt_toolkit, zmq, pytest, psutil, openpyxl; print(rich.__version__, zmq.__version__, zmq.zmq_version(), psutil.__version__)"`
   and `-m PyInstaller --version`. If anything is missing, run
   `-m pip install -e .[dev]` from the repo root and re-check. Report versions.
3. **Location.** Resolve the repo root to an absolute path; confirm `E:` is a local
   disk, not a mapped network drive or UNC path (`Get-PSDrive E`, `net use`).
4. **ZeroMQ round trip.** Write and run a throwaway script (keep it under
   `verify\artifacts\`, not in the repo): bind PUB + PULL on
   `bind_to_random_port`, connect SUB (subscribe `""`) + PUSH from a thread, send
   200 multipart `[topic, JSON]` messages each way, measure the median latency and
   confirm no message is lost and no `slow joiner` drop occurs when the SUB
   connects **after** the first publish (this decides whether the engine must
   re-send `state.hello`, CONTRACTS C3). Report numbers.
5. **Console spawning and ownership.** From a throwaway script: spawn a child with
   `subprocess.Popen([py, "-c", "import time; time.sleep(60)"], creationflags=subprocess.CREATE_NEW_CONSOLE)`
   — confirm a window appears (ask Usman if you cannot tell), that
   `psutil.Process(parent).children(recursive=True)` lists it, that
   `child.terminate()` closes the window, and that a child which polls
   `psutil.pid_exists(int(os.environ["MOS_LAUNCHER_PID"]))` exits within 2 s of the
   parent being killed with `taskkill /F /PID`. Also confirm `CREATE_NO_WINDOW`
   spawns an invisible child. Record which console host appeared (Windows
   Terminal or conhost) — it affects glyph rendering and is a `[VERIFY]` for Usman.
6. **UTF-8.** With `PYTHONUTF8=1` in the environment, confirm
   `-c "import sys; print(sys.stdout.encoding)"` prints `utf-8` and that Rich's
   `Console().encoding` agrees, in a spawned console (write the result to a file).
   Whether the glyphs `★ ◎ ▲ ▼ ● ◉ ○ ✦ ✧ △ ▪ ·` actually render is a human check —
   put it in your report for Usman, with the one-line command to try.
7. **Workbook and generated docs.** Run
   `tools\blueprint_to_design.py --check` — it must say the docs are current. Run
   `-m pytest tests\test_config_matches_blueprint.py -q` — it must pass. Both are
   HARNESS checks, not game checks.
8. **`.gitignore`.** Confirm `MOS/`, `saves/`, `logs/`, `verify/artifacts/`,
   `dist/`, `build/` and `.claude/settings.local.json` are ignored and that
   `!mos.spec` un-ignores the PyInstaller spec (the GitHub template ignores
   `*.spec`). Fix only what is missing.
9. **Report all of the above back to me before writing any harness file.** Say
   plainly if anything here was wrong.

---

## Phase 1 — Design the gates FIRST

The gates are the harness. Everything else is support. All of them read
`docs/CONTRACTS.md` for the interfaces they drive; **do not invent other ones**. If a
contract is insufficient for a check you need, say so in the report — do not change
`docs/CONTRACTS.md` yourself.

**Standard layout — do not deviate:**

```
verify\smoke_test.py        gate 1 — headless; argument = repo root; --quick; --selftest
verify\live_test.py         gate 2 — real processes and windows; argument = repo root; --selftest
verify\hook_gate.py         the PostToolUse hook (runs smoke_test.py --quick)
verify\_checks.py           pure checker functions shared by both gates (unit-testable)
verify\fixtures\            canned reports / stub servers for --selftest
verify\artifacts\           gate output (reports, logs, timings) — gitignored
```

Requirements for every gate:

- **Binary.** Exit 0 pass, 1 fail. Exit 3 + `HARNESS ERROR …` when the interpreter,
  a dependency or the workbook is wrong — and never blame the code for that.
- **Specific messages.** Not "verification failed" but
  `FAIL smoke/02 AI never acts: 0 AI orders executed in turns 1..30 (need ≥ 1 every turn) — see mos/engine/ai.py decide(); OV-10, SP-06`.
  Each check has a stable code (`smoke/01` …) and cites the blueprint ID. The
  message is fed back to the model; a vague message costs an iteration.
- **Fast enough for its tier.** `--quick` ≤ 15 s; full smoke ≤ 90 s; live ≤ 3 min.
  Time every check and print the timings.
- **Thresholds from the real data.** Where this brief gives a number (e.g. "AI
  orders ≥ 1 every turn"), confirm in M1 that the default config can meet it; if it
  cannot, the gate is wrong, not the game — report, do not thrash.
- **Distinguishes harness from deliverable failure.** Missing `mos` package before
  M1 starts is a *deliverable* failure with a clear message
  (`mos package not found at E:\…\mos\__init__.py — M1 not built yet`), not a crash.

### Gate 1 — `verify\smoke_test.py <repo-root> [--quick] [--selftest]`

Runs headless, no windows, random ports. Checks, in this order (stop at the first
HARNESS ERROR; otherwise run all and report every failure):

| Code | Check | Catches | IDs |
|---|---|---|---|
| smoke/00 | interpreter is the MOS venv; deps import; `tools\blueprint_to_design.py --check` current; `controls_config.ini` parses and validates (`mos.config` exit 4 message if not) | harness drift; ignored keys | CFG-*, D-53 |
| smoke/01 | `pytest tests -q -x --timeout 10` (`--quick`: `-m "not slow"`) | unit regressions | — |
| smoke/02 | `python -m mos sim --seed 1234 --turns 30 --ai-player --report …` exits 0 in ≤ 20 s; `turns_completed == 30`; `turn_no` strictly 1..30 | turns don't advance | OV-02, TP-01…08 |
| smoke/03 | every turn `orders_executed.ai ≥ 1`; over the run ≥ 50 % of AI ships changed `position` at least once | AI never acts | OV-10, SP-06 |
| smoke/04 | consecutive `snapshot_hash` values all differ | frozen state | BUS-07 |
| smoke/05 | `player.food == food_start − t` and same for water for t ≤ food_start (no trade in M1); a `supplies` warning event appears when food ≤ `warn_turns` | upkeep wired to config | SUP-01, SUP-02, CFG-45/46 |
| smoke/06 | with `--turns 40`: a mutiny event at turn `food_start + mutiny_turns_at_zero`; afterwards `player.ship_class == mutiny_ship_class`, player food == water == `mutiny_supplies`, `captains` count +1, fear reduced by `mutiny_fear_delta` | mutiny not fired | SUP-03, SUP-04b, SUP-04c, CFG-47/48 |
| smoke/07 | run `--turns 10 --seed 1234` twice → identical `snapshot_hash` sequences; `--seed 1235` → different | non-determinism | D-67 |
| smoke/08 | change `food_start` in a temp copy of the ini to 12, run with `--config` → food at turn 1 == 11 | config key silently ignored | CFG-45, D-49 |
| smoke/09 | `--bus` run on random ports: gate connects SUB + PUSH, receives `state.hello`, pushes `turn.end` ×3 → three `turn.resolved` with increasing `turn_no` and differing hashes; pushes one valid `order.submitted` (`move 1 0`) → `order.accepted`; one invalid (`move 99 0`) → `order.rejected` with a reason naming `CMD-06` | bus wiring; stale publish | BUS-03/04, MSG-01…07 |
| smoke/10 | `render()` of each terminal on two consecutive snapshots differs; T2 sector render places the active ship glyph at `position`; T4 render contains `turn_no`, food, water; T3 render lists each event; none of them imports `zmq` or calls `time`/`input` | stale viewport at render level | C4, BUS-05, GRD-06 |
| smoke/11 | `saves\game.json` exists after the run; `load()` round-trips to the final snapshot hash | autosave | OV-12, C6 |
| smoke/12 | (full only) `--turns 500 --seed 7 --ai-player` exits 0 in ≤ 120 s with no traceback; `outcome` in {running, game_over, mastery} | long-run robustness, not pacing | D-69, Chat A15 |

`--quick` runs smoke/00, 01 (fast tests), 02–04 with `--turns 10`, and 10.

Checks that a milestone has not reached yet must **skip with a visible `SKIP`**, by
reading `specs\MILESTONE` (a one-line file: `M1`), never pass silently. The M2–M5
specs will add rows to this table (hazards, combat, game over, mastery, exe).

### Gate 2 — `verify\live_test.py <repo-root> [--selftest]`

Runs the real launcher on Usman's desktop (windows will open and close). Not in the
hook. Sets `MOS_HEARTBEAT_DIR` to a temp dir and uses the default ports.

| Code | Check | Catches | IDs |
|---|---|---|---|
| live/01 | ports 5555/5556 free before start, else `HARNESS ERROR` naming the PID that holds them | stale engine from a previous run | BUS-06 |
| live/02 | `python -m mos` → within 15 s the launcher has children with `--role engine`, `command`, `grid`, `output`, `status` (psutil cmdline match) | spawn failure | TL, BR-15 |
| live/03 | gate connects SUB + PUSH; `state.hello` arrives; pushes `turn.end` ×3 → three `turn.resolved`; heartbeat files for grid/output/status reach `turn_no 3` within 3 s of the third publish | stale window | BUS-05, D-66 |
| live/04 | kill the `--role grid` process → a new `--role grid` child exists within 5 s and its heartbeat reaches the current turn | reopen rule | TL |
| live/05 | kill the `--role command` process → within 10 s the launcher and all children are gone; `saves\game.json` exists; no process has `--role` and this repo path in its cmdline | orphans; no autosave | BUS-06, OV-12 |
| live/06 | launch again; terminate the launcher → all children gone within 10 s | orphans | BUS-06, D-59 |
| live/07 | (M5) same as 02–06 against `dist\mos\mos.exe`; plus the launcher is still alive 10 s after start | exe opens and exits | BR-14/15 |

Always clean up: on any failure, kill everything the gate started before exiting.

### Prove the gates work — `--selftest`

A gate you have not watched fail is not a gate. The game does not exist yet, so
the selftest exercises the **checkers** and the **bus client** against fixtures:

- `verify\fixtures\sim_good.json` passes every smoke checker; one broken fixture
  per check (`sim_turns_stuck.json`, `sim_ai_idle.json`, `sim_hash_frozen.json`,
  `sim_no_mutiny.json`, `sim_config_ignored.json` …) fails **only** its own check,
  with the expected code in the message.
- A stub ZeroMQ server inside the selftest (good: increments `turn_no`; broken:
  republishes the same `turn_no`) makes smoke/09's client pass and fail.
- A stub launcher script (good: spawns a sleeping child that watches
  `MOS_LAUNCHER_PID`; broken: spawns a child that ignores it) makes live/06's
  orphan check pass and fail — this one opens no window.
- Run from the repo root and from a temp dir; report if they disagree.
- Confirm the "`mos` package not found" path produces the specific message and exit
  1, not a traceback.

Run the selftest. Do not proceed until it discriminates correctly.

---

## Phase 2 — Build the rest of the harness

### The verification hook — the inner loop
- `verify\hook_gate.py`: a `PostToolUse` hook matching `Write|Edit|MultiEdit`.
  Python stdlib only. Reads the tool-use JSON on stdin, extracts the file path, and
  **only acts for paths under `mos\`, `tests\`, `controls_config.ini` or
  `pyproject.toml`** — exits 0 silently for everything else (docs, specs, verify).
- Runs `E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\smoke_test.py . --quick`
  with a 60 s timeout. Exit 2 with the gate's output on stderr on failure; exit 0
  on pass; exit 0 with a `HARNESS ERROR` message when the gate itself returned 3 or
  the timeout fired (say "the quick gate exceeded 60 s — the loop may be hanging").
- **Pin the interpreter with the absolute path** in `.claude\settings.json`. A bare
  `python` that resolves to a stub dies silently.
- Before M1 exists, a write under `mos\` will make the hook report "mos package not
  found / sim not runnable" — that is correct and is what the live-fire test looks for.

### `.claude\settings.json`
- Register the hook with the absolute interpreter path.
- Allow the tools needed to run unattended: the MOS interpreter via Bash, `git`,
  Read/Write/Edit under the repo.
- **Deny edits to** `verify\**`, `.claude\settings.json`,
  `docs\master_of_starbour_blueprint.xlsx`, `docs\DESIGN.md`,
  `docs\config_keys.json`, `docs\CONTRACTS.md`. Check the current Claude Code
  documentation for the exact permission-rule syntax rather than guessing
  `[VERIFY]`; the `claude-code-guide` agent can confirm it.

### No skill, no subagent, no outer loop — yet
The upstream template adds a skill here. Not for this project: the procedure lives
in `CLAUDE.md` + `docs/CONTRACTS.md` + the specs. Promote a hook, skill or subagent
only from repeated friction (Project rule). Likewise the outer loop script: build it
only once the interactive loop is proven over M1.

### `specs\MILESTONE`
Already in the repo, one line: `M1`. Gates read it to decide which checks apply;
each milestone spec tells the builder to bump it as its last step.

---

## Phase 3 — Prove the whole thing

Before you tell me it is done:

1. `verify\smoke_test.py --selftest` and `verify\live_test.py --selftest` pass
   (each gate discriminates good from broken fixtures, with the right codes).
2. The hook logic is proven **by direct invocation**: pipe a simulated tool-use JSON
   payload into `verify\hook_gate.py` on stdin and confirm exit 2 with a useful
   message for a write under `mos\` (no package yet), exit 0 for a write under
   `docs\`, and exit 0 with `HARNESS ERROR` when the interpreter path in the payload
   environment is wrong.
   **Important:** the live hook CANNOT fire in this session — hooks register at
   session start and this harness did not exist then. Do not claim otherwise. State
   in your final report that Usman must restart Claude Code and run the live-fire
   test himself (`docs/SETUP.md` step 4).
3. Every absolute path in the harness has been verified to exist and run.
4. `git status` shows only harness files, `docs/ENVIRONMENT.md` and `.gitignore`
   changes — no game code.
5. Report: the exact gate commands for the kickoff prompt; the interpreter path;
   the hook status (with the restart requirement); the Phase 0 numbers (bus
   latency, spawn/kill timings); anything in this brief that Phase 0 contradicted;
   and a **Challenge** section: what is unverified, fragile, or likely wrong in the
   gates.

Then stop. Do not build the game. M1 starts in a fresh session from `specs/M1.md`.

---

## Rules

- **Verify, don't assert.** Every claim about the environment comes from a command
  you ran.
- **Tell me when this brief is wrong.** If Phase 0 contradicts something above, say
  so plainly rather than working around it.
- **Three identical failures mean the brief or the gate is wrong.** Stop and say which.
- Prefer fewer moving parts. Every dependency is something that can drift.
- British English in everything you write.
