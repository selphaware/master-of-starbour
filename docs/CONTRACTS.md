# CONTRACTS.md — the interfaces the gates drive

One place for every interface shared between the game (`mos/`) and the harness
(`verify/`). `docs/BOOTSTRAP.md` builds gates against these; `specs/M1.md` builds the
game to them. If a contract must change, change it **here first**, then the gate,
then the game, and log a D-nn row. IDs cite `docs/DESIGN.md`.

## C1 — Command lines

| Process | Command | Window |
|---|---|---|
| Launcher (TL) | `python -m mos` (no arguments) | the console it was started from |
| Engine (T0) | `python -m mos --role engine` | none (`CREATE_NO_WINDOW`); logs to `logs/engine.log` |
| T1 Command | `python -m mos --role command` | new console (`CREATE_NEW_CONSOLE`) |
| T2 Grid | `python -m mos --role grid` | new console |
| T3 Output | `python -m mos --role output` | new console |
| T4 Status | `python -m mos --role status` | new console |
| Headless sim (D-55) | `python -m mos sim [options]` | none |

`python` means `E:\CODE\master-of-starbour\MOS\Scripts\python.exe` in dev and
`mos.exe` when frozen (BR-15: `mos.exe --role <role>`). The launcher sets
`PYTHONUTF8=1` and `MOS_LAUNCHER_PID=<its pid>` in every child's environment (D-59,
D-68). Every child exits within 2 s of `MOS_LAUNCHER_PID` no longer existing.

Common options for every role: `--config PATH` (default: `controls_config.ini` from
`mos.paths.root()`), `--pub-port N`, `--pull-port N` (default: `[bus]`), `--log-level`,
and for the launcher and engine `--seed N` (overrides `[world] seed` for a new game;
the launcher passes it to the engine). `python -m mos` must expose a `main()` in
`mos/__main__.py` (the `mos` console script in `pyproject.toml` points at it).

## C2 — Headless simulation

```
python -m mos sim --seed 1234 --turns 30 --ai-player --report verify\artifacts\sim.json
                  [--config PATH] [--save-dir DIR] [--bus] [--pub-port N --pull-port N]
```

- `--ai-player`: the player's captain is driven by the same AI as the others.
- `--bus`: run the engine's real ZeroMQ server (bind to the given ports, or random
  ports when 0) and drive it through the sockets instead of in-process. Without
  `--bus`, no sockets are opened.
- `--save-dir`: where `game.json` is autosaved each turn (default `saves/`).
- Exit 0 when `turns_completed == turns_requested` **or** the run ended by
  `game_over` / `mastery` before that (both are legitimate ends). Exit 1 on any
  exception (traceback to stderr). Exit 4 when the config fails validation (message
  names the key).
- Must complete 30 turns at default grid sizes in ≤ 20 s on Usman's workstation
  `[VERIFY in Phase 0 / M1]`.

### Report (`--report`) — JSON

```json
{
  "schema": 1,
  "seed": 1234,
  "config_path": "E:\\CODE\\master-of-starbour\\controls_config.ini",
  "turns_requested": 30,
  "turns_completed": 30,
  "outcome": "running",
  "elapsed_s": 1.73,
  "per_turn": [
    {
      "turn_no": 1,
      "orders_executed": {"player": 1, "ai": 7},
      "ai_ships_moved": 5,
      "player": {"food": 29, "water": 29, "fear": 10, "respect": 10,
                 "gold": 5, "silver": 50, "ship_class": "kestrel",
                 "position": [[3, 4], [7, 9], [12, 11]]},
      "captains": 193,
      "events": ["upkeep: food 29, water 29"],
      "snapshot_hash": "6f1c…",
      "snapshot_bytes": 184233,
      "resolve_ms": 41
    }
  ],
  "final": {
    "captains": 194,
    "ships": 194,
    "mutinies": [{"turn_no": 33, "captain": "player", "result": "demoted"}],
    "game_over_turn": null,
    "mastery_turn": null
  }
}
```

`outcome` ∈ `running | game_over | mastery`. `snapshot_hash` is the SHA-1 of the
canonical JSON of the snapshot (sorted keys, no whitespace). `position` is
`[[gx, gy], [sx, sy], [cx, cy]]` (SH-09). Later milestones add fields; they never
remove or rename these.

## C3 — Message catalogue on the wire (MSG-01…09, D-57, D-58)

ZeroMQ multipart: frame 0 = topic (ASCII), frame 1 = JSON (UTF-8). Engine binds
`PUB tcp://127.0.0.1:<pub_port>` and `PULL tcp://127.0.0.1:<pull_port>`. T1 and the
launcher connect `PUSH`; terminals connect `SUB` with subscription `""` (all topics).

| Topic | Direction | JSON payload |
|---|---|---|
| `order.submitted` (MSG-01) | T1 → engine | `{"command": "move", "args": ["12", "7"], "ship_id": "s-001", "turn_no": 12, "client": "command"}` |
| `turn.end` (MSG-02) | T1 → engine | `{"turn_no": 12}` |
| `turn.resolved` (MSG-03) | engine → all | `{"turn_no": 13, "snapshot": {…}, "events": [{"phase": "TP-02", "type": "movement", "severity": "info", "text": "Kestrel moved to 12,7", "ids": ["MV-01"]}]}` |
| `view.changed` (MSG-04) | T1 → engine → all | `{"level": "sector", "focus": {"galaxy": [3, 4], "sector": [7, 9]}}` or `{"level": "follow"}` |
| `event.log` (MSG-05) | engine → all | `{"turn_no": 12, "type": "system", "severity": "muted", "text": "autosaved"}` |
| `order.rejected` (MSG-06) | engine → all | `{"order": {…}, "reason": "speed 7 exceeds engine_power 4 (CMD-06)"}` |
| `order.accepted` (MSG-07) | engine → all | `{"order": {…}, "queue_position": 2}` |
| `system.shutdown` (MSG-08) | launcher/T1 → engine → all | `{"reason": "command terminal closed"}` |
| `game.over` / `game.mastery` (MSG-09) | engine → all | `{"turn_no": 57, "cause": "…", "summary": {…}}` |
| `state.hello` `[ASSUMED]` | engine → all, on connect and every 2 s until the first turn | `{"turn_no": 0, "snapshot": {…}, "seed": 1234, "version": "0.1.0"}` — so a terminal opened late (or reopened, TL) can draw immediately |

Snapshot shape (MSG-03) — the full state, BUS-07:

```json
{
  "turn_no": 13, "seed": 1234, "version": "0.1.0",
  "sizes": {"universe": [8, 8], "galaxy": [16, 16], "sector": [24, 24]},
  "player_id": "c-000",
  "captains": {"c-000": {"name": "…", "is_player": true, "gold": 5, "silver": 50,
                          "fear": 10, "respect": 10, "food": 17, "water": 17,
                          "ships": ["s-001"], "active_ship": "s-001", "items": [],
                          "is_master": false, "mutiny_countdown": null}},
  "ships": {"s-001": {"name": "…", "class": "kestrel", "captain": "c-000",
                       "torpedoes": 6, "phasers": 3.0, "shield_max": 60.0,
                       "shield_current": 60.0, "engine": 4.0, "warp": 3.0,
                       "position": [[3, 4], [7, 9], [12, 11]], "orders_queued": 0}},
  "universe": {"galaxies": [[3, 4], …]},
  "galaxies": {"3,4": {"sectors": [[7, 9], …], "visited": [[7, 9]], "hazards": [[2, 2]]}},
  "sectors": {"3,4/7,9": {"terrain": [{"kind": "star", "cell": [5, 5], "colour": "yellow", "bucket": "medium"},
                                       {"kind": "planet", "cell": [6, 5], "name": "…"}],
                            "items": [{"id": "i-017", "cell": [9, 2], "rarity": "normal", "revealed": false}]}},
  "fog_of_war": true,
  "view": {"level": "follow"}
}
```

Terrain never changes after generation, so a snapshot may list it once per sector;
`revealed`/`visited` are the fog-of-war state (GRD-04). Sizes in bytes are logged
per turn (`snapshot_bytes`).

## C4 — Terminal render API

Each terminal has a pure function in `mos/terminals/render_<role>.py`:

```python
def render(snapshot: dict, view: dict, cfg: GameConfig, width: int = 120) -> rich.console.RenderableType
```

Properties the gates check: two snapshots that differ in anything the terminal shows
produce different rendered text; the active ship's glyph sits at the cell given by
`position` in sector view; the T4 panel shows `turn_no`, food, water, fear, respect;
the T3 render of a `turn.resolved` lists every event in phase order (OB-04). Render
functions never touch sockets, files, time or `input()`.

## C5 — Heartbeats (live gate only, D-66)

When the environment variable `MOS_HEARTBEAT_DIR` is set, each terminal writes
`<dir>\<role>.json` after every redraw:

```json
{"role": "grid", "turn_no": 13, "rendered_at": 1761400000.123, "pid": 24816, "render_ms": 38}
```

Unset in normal play. The launcher passes the variable through to children.

## C6 — Save file (OV-12, D-61)

`saves\game.json`, written atomically (temp file + replace) at the end of every
turn (TP-08). Contents: the snapshot plus `rng_state`. Deleted on game over
(OV-09c). `mos.engine.save.load()` returns a state whose snapshot hash equals the
saved one.

## C7 — Exit codes shared by every gate and script

| Code | Meaning |
|---|---|
| 0 | pass |
| 1 | deliverable failure — message names the check and the blueprint ID |
| 2 | (hook only) deliverable failure fed back to the model |
| 3 | `HARNESS ERROR` — wrong interpreter, missing dependency, missing workbook; not a code problem |
| 4 | config validation failed — message names `[section] key` |
