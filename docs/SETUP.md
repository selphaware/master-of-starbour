# SETUP.md — environment and the first Claude Code sessions

Windows 10/11, PowerShell. Every command names the interpreter explicitly; never
bare `python` (it may be the Microsoft Store stub — turn the `python.exe` /
`python3.exe` aliases off under Settings → Apps → Advanced app settings → App
execution aliases).

Checks you can run yourself are marked **✔ check**. Expected output follows each.

## 1. Repo and virtual environment (once)

```powershell
cd E:\CODE\master-of-starbour
git remote -v                      # ✔ check: origin git@github.com:selphaware/master-of-starbour.git
```

Create the venv **MOS** if it does not exist yet, from your Python 3.12
(`A:\envs\py312\python.exe` on eagle1 — adjust if yours moved):

```powershell
A:\envs\py312\python.exe -m venv E:\CODE\master-of-starbour\MOS
E:\CODE\master-of-starbour\MOS\Scripts\python.exe --version              # ✔ check: Python 3.12.x
E:\CODE\master-of-starbour\MOS\Scripts\python.exe -m pip install -U pip
E:\CODE\master-of-starbour\MOS\Scripts\python.exe -m pip install -e .[dev]
```

`-e .[dev]` installs the runtime packages (rich, prompt_toolkit, pyzmq) and the
dev/gate packages (pytest, pytest-timeout, psutil, openpyxl, pyinstaller) from
`pyproject.toml`. You install these, not Claude Code: the gates self-test with this
interpreter, so it must exist before the harness does.

`[VERIFY]` The `mos` package does not exist until M1, so the editable install has
nothing to link yet. If pip refuses for that reason, install the same packages
directly and re-run `-e .[dev]` once M1 is green (it only matters for the `mos`
console script; `python -m mos` from the repo root works either way):

```powershell
E:\CODE\master-of-starbour\MOS\Scripts\python.exe -m pip install rich prompt_toolkit pyzmq pytest pytest-timeout psutil openpyxl pyinstaller
```

```powershell
E:\CODE\master-of-starbour\MOS\Scripts\python.exe -c "import rich, prompt_toolkit, zmq, pytest, psutil, openpyxl; print('ok', zmq.zmq_version())"
# ✔ check: ok 4.3.x
```

## 2. Prove the kit is consistent (before any Claude Code session)

```powershell
E:\CODE\master-of-starbour\MOS\Scripts\python.exe tools\blueprint_to_design.py --check
# ✔ check: docs are current with the workbook
E:\CODE\master-of-starbour\MOS\Scripts\python.exe -m pytest tests\test_config_matches_blueprint.py -q
# ✔ check: 63 passed
git add -A; git commit -m "Add build kit (CLAUDE.md, docs, specs, config, scaffolding)"
```

If `--check` says STALE, run the script without `--check`, read the diff, commit.

## 3. Session 1 — build the harness (Claude Code)

```powershell
cd E:\CODE\master-of-starbour
claude
```

`/model` → the current **Fable** model (Fable 5.1 at the time of writing), highest
effort. Harness generation is environment discovery plus multi-file work, where
Fable's lead is largest. Then paste:

```
python is E:\CODE\master-of-starbour\MOS\Scripts\python.exe
Read docs/BOOTSTRAP.md and follow it.
```

It will report Phase 0 (ask it to wait for your go-ahead if it does not), build
`verify\`, `.claude\settings.json` and `docs\ENVIRONMENT.md`,
run both selftests, and stop. **It must not write anything under `mos\`.**

✔ check when it stops:

```powershell
git status --short                                      # only verify/, .claude/, docs/ENVIRONMENT.md, .gitignore
E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\smoke_test.py --selftest   # ✔ exit 0, lists each checker pass/fail pair
E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\live_test.py --selftest    # ✔ exit 0
Get-Content .claude\settings.json | Select-String "MOS\\Scripts\\python.exe"       # ✔ the hook uses the absolute path
```

## 4. Restart, confirm the hook, live-fire it ⚠️

Hooks register at session start. The harness did not exist when session 1 began.

```
/exit
```
```powershell
claude
```
```
/hooks
```

✔ check: a `PostToolUse` hook on `Write|Edit|MultiEdit` whose command starts with
`E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\hook_gate.py`. Approve it
if prompted. If it is missing or shows bare `python`, stop and fix `settings.json`.

Live-fire — never trust a harness you have not watched reject something:

```
Create mos/__init__.py containing only the line  raise RuntimeError("live-fire")
Make only that one write, then stop. Do not fix it.
```

✔ expected: immediately after the write, the gate's complaint appears on its own
(`FAIL smoke/02 … mos package not runnable` or similar) — you did not ask for it.
If the file is written and nothing appears, the hook is dead; fix before going on.

```
Delete mos/__init__.py.
```
```powershell
git add -A; git commit -m "Add build-loop harness"
```

## 5. Session 2 — build M1

Fresh session. `/model` → Fable (recommended for M1: Windows process model, bus
and launcher are discovery-heavy) or Opus; highest effort.

```
python is E:\CODE\master-of-starbour\MOS\Scripts\python.exe
Read specs/M1.md and build it. Don't stop until both
  E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\smoke_test.py .
and
  E:\CODE\master-of-starbour\MOS\Scripts\python.exe verify\live_test.py .
exit 0. Report as CLAUDE.md asks.
```

Windows will open and close on your desktop when the live gate runs. Watch for
the moment a gate fires after a write and the agent fixes itself unprompted —
that is the loop working. If the same check fails three times for the same reason,
the agent should stop and say whether the spec, the gate or the brief is wrong; if
it keeps iterating, stop it and read `specs/M1.md` § Known traps.

## 6. Review what the machine cannot judge

```powershell
.\launch.ps1
```

Look at the four windows: colours, glyphs (if `★ ◎ ▲` show as boxes, set
`[ui] glyph_set = ascii` in `controls_config.ini`), the prompt, T3 grouped by phase,
T4 values. Type `move 3 0`, `end`, watch T2/T3/T4 change, close T2 (it reopens),
`quit` (everything closes, `saves\game.json` exists, Task Manager shows no
`python.exe` from `MOS`). If something is wrong, sharpen `specs/M1.md` and re-run;
do not hand-patch the game — that breaks the loop's premise.

```powershell
git add -A; git commit -m "M1: engine, bus, grids, launcher, terminals (gates green)"
```

Then re-run the same M1 spec on **Sonnet** at default effort in a scratch branch:
green in ≤ 3 iterations means the spec is robust and Sonnet is your cheap build
model for M2+; not green means the spec, not the model, needs work.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `Python was not found` | Store alias stub | Disable aliases; absolute path everywhere |
| Hook never fires | Bare `python` in settings.json, or session started before the harness existed | Absolute path; restart Claude Code |
| `Address in use` on 5555/5556 | an engine from an earlier run is still alive | `Get-Process python \| Where-Object {$_.Path -like '*MOS*'} \| Stop-Process`; then raise it as a gate failure (live/05) |
| Glyphs show as `?` or boxes | console font / code page | `[ui] glyph_set = ascii`; or Windows Terminal with a Unicode font |
| Windows open then vanish | child crashed on start | `logs\<role>.log` has the traceback; the launcher must not swallow it |
| Gate passes, game looks wrong | the gate cannot judge looks | sharpen the spec, not the code |
