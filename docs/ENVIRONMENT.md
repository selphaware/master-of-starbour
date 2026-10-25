# ENVIRONMENT.md — Phase 0 discovery record

Facts about the build machine, each established by a command run on 2026-10-25
(BOOTSTRAP § Phase 0). Nothing here is assumed. Re-run the commands if the machine
changes; the gates re-check the first block (smoke/00) on every run.

Workstation: Windows 11 Pro, build 10.0.26300 (26H2). Session shell chain:
`pwsh.exe ← cmd.exe ← claude.exe ← powershell.exe ← WindowsTerminal.exe`.

## 1. Interpreter

```
> E:\CODE\master-of-starbour\MOS\Scripts\python.exe --version
Python 3.12.13
> E:\CODE\master-of-starbour\MOS\Scripts\python.exe -c "import sys; print(sys.executable, sys.version); print('prefix', sys.prefix)"
E:\CODE\master-of-starbour\MOS\Scripts\python.exe 3.12.13 | packaged by Anaconda, Inc. | (main, Mar 19 2026, 20:16:45) [MSC v.1942 64 bit (AMD64)]
prefix E:\CODE\master-of-starbour\MOS
```

Bare `python` on this machine **is the Microsoft Store stub** — confirmed, not assumed:

```
> (Get-Command python).Source ; (Get-Item (Get-Command python).Source).Length
C:\Users\uahma\AppData\Local\Microsoft\WindowsApps\python.exe
0
> where.exe python
C:\Users\uahma\AppData\Local\Microsoft\WindowsApps\python.exe
c:\Users\uahma\anaconda3\python.exe
C:\msys64\ucrt64\bin\python.exe
```

Every harness command therefore uses the absolute MOS path. The gates refuse to run
under any other interpreter (`HARNESS ERROR`, exit 3).

## 2. Packages (all present; nothing installed by the harness)

```
> E:\CODE\master-of-starbour\MOS\Scripts\python.exe -m pip list | Select-String "^(rich|prompt|pyzmq|pytest|psutil|openpyxl|pyinstaller|master)"
master-of-starbour        0.1.0     E:\CODE\master-of-starbour   (editable install, no starbour package yet)
openpyxl                  3.1.5
prompt_toolkit            3.0.53
psutil                    7.2.2
pyinstaller               6.22.3
pyinstaller-hooks-contrib 2026.9
pytest                    9.1.1
pytest-timeout            2.4.0
pyzmq                     27.2.0      (libzmq 4.3.5)
rich                      15.0.0
> E:\CODE\master-of-starbour\MOS\Scripts\python.exe -m PyInstaller --version
6.22.3
```

The brief's one-liner `print(rich.__version__, …)` fails: `rich` has no `__version__`
attribute (rich 15). Versions above come from `pip list` / `importlib.metadata`; the
gate uses `importlib.metadata.version()`.

## 3. Location

```
> (Resolve-Path E:\CODE\master-of-starbour).Path
E:\CODE\master-of-starbour
> Get-PSDrive E            -> Provider FileSystem, Root E:\, DisplayRoot (empty), Description ECLIPSE
> Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='E:'"   -> DriveType 3 (local disk), NTFS, VolumeName ECLIPSE
> net use                  -> E: is not a mapped drive. (I: maps \\localhost\e$\CODE — the same folder via loopback share; never use I: for the repo.)
> git remote -v            -> origin git@github.com:selphaware/master-of-starbour.git (fetch/push)
```

## 4. ZeroMQ round trip (`verify\artifacts\phase0\zmq_probe.py`, pyzmq 27.2.0 / libzmq 4.3.5)

200 multipart `[topic, JSON]` messages each way, random ports, SUB + PUSH in a thread:

| Case | Sent | Received | Median latency |
|---|---|---|---|
| A — PUB→SUB, SUB connected and settled 300 ms before the first publish | 200 | 200 | 1.57 ms (p95 1.63 ms) |
| B — first publish **before** the SUB connects (slow joiner) | 1 + 5 | 5 | the early message was **lost** |
| C — SUB connects, publish immediately with no settle | 200 | 142 | **58 lost** |
| D — PUSH→PULL | 200 | 200, in order | 0.90 ms |
| E — PUB→SUB with a 200 KB payload (snapshot-sized, BUS-07) | 20 | 20 | 1.31 ms |

Conclusion for CONTRACTS C3: the slow-joiner drop is real on this machine. A terminal
that connects after a publish misses it, and even a SUB that connected "just before"
loses the first ~60 messages. The engine **must** re-send `state.hello` on a timer
(every 2 s, D-70) for the whole game — not only "until the first turn" as C3 says —
because the live gate's reopen check (live/04) needs a reopened window to draw without
waiting for the next turn. PUSH→PULL needs no such care.

## 5. Console spawning and ownership (`verify\artifacts\phase0\console_probe.py`)

| Check | Result |
|---|---|
| `CREATE_NEW_CONSOLE` child → a new visible top-level window appears | yes: window class `CASCADIA_HOSTING_WINDOW_CLASS`, owner `WindowsTerminal.exe`; host processes `OpenConsole.exe` + `conhost.exe` appeared |
| Spawn → child running | 0.617 s |
| `psutil.Process(parent).children(recursive=True)` lists the child | yes |
| `child.terminate()` closes the window | yes (0.001 s, return code 1) |
| Watchdog child polling `psutil.pid_exists(MOS_LAUNCHER_PID)` every 0.25 s; launcher killed with `taskkill /F /PID` | child exited **0.527 s** after the kill (bound: 2 s) and wrote its exit marker |
| `CREATE_NO_WINDOW` child | no new visible window; `GetConsoleWindow()` = 0 |

**Console host `[VERIFY for Usman]`:** new consoles open in **Windows Terminal**, not
conhost, even though `HKCU:\Console\%%Startup` has `DelegationConsole` /
`DelegationTerminal` = `{00000000-…}` ("let Windows decide", which on this Windows 11
build means Windows Terminal). `WT_SESSION` is **not** a reliable test — it is
inherited from the parent shell (it was also set inside the no-window child). The
window class is the reliable test. Windows Terminal renders the full glyph set with
its default font; conhost with a raster font may not (`[ui] glyph_set = ascii`
fallback, D-68).

## 6. UTF-8 in a spawned console (`PYTHONUTF8=1`, new console, written to a file by the child)

```
stdout_encoding   utf-8
rich Console().encoding   utf-8
rich color_system  truecolor      rich legacy_windows  False     size 110x52
```

In the `CREATE_NO_WINDOW` child Rich reports `legacy_windows True`, `color_system
windows` — irrelevant for the engine (no UI) but a reminder that the engine must not
use Rich for anything it expects a human to see.

Human check (cannot be automated): paste this into a Windows Terminal tab and confirm
twelve distinct glyphs appear, not boxes or `?`:

```
E:\CODE\master-of-starbour\MOS\Scripts\python.exe -X utf8 -c "print('★ ◎ ▲ ▼ ● ◉ ○ ✦ ✧ △ ▪ ·')"
```

## 7. Workbook and generated docs (harness checks, not game checks)

```
> E:\CODE\master-of-starbour\MOS\Scripts\python.exe tools\blueprint_to_design.py --check
docs are current with the workbook                              exit 0
> E:\CODE\master-of-starbour\MOS\Scripts\python.exe -m pytest tests\test_config_matches_blueprint.py -p no:cacheprovider
63 passed in 0.14s                                              exit 0
```

(`-q` on the command line plus `-q` in `pyproject.toml` addopts gives `-qq`, which
hides the "N passed" line; the gate parses the exit code, not the summary.)

## 8. `.gitignore` (`git check-ignore -v`)

| Path | Result |
|---|---|
| `MOS/Scripts/python.exe` | ignored (`MOS/*`) |
| `saves/game.json`, `logs/engine.log` | ignored (`saves/`, `logs/`) |
| `verify/artifacts/x.json` | ignored (`verify/artifacts/`) |
| `dist/starbour/starbour.exe`, `build/x` | ignored (`dist/`, `build/`) |
| `.claude/settings.local.json` | ignored |
| `starbour.spec` | **not** ignored — `!starbour.spec` overrides the template's `*.spec` (other `.spec` files stay ignored) |
| `.claude/settings.json`, `verify/*.py`, `verify/fixtures/*`, `docs/ENVIRONMENT.md` | not ignored (tracked as intended) |

Nothing needed fixing.

## 9. Ports

`Get-NetTCPConnection -LocalPort 5555,5556` → nothing listening. Both free at the time
of the probe; the live gate re-checks and names the holding PID (live/01).
