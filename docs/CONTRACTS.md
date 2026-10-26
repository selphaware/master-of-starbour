# CONTRACTS.md — the interfaces the gates drive

One place for every interface shared between the game (`starbour/`) and the harness
(`verify/`). `docs/BOOTSTRAP.md` builds gates against these; `specs/M1.md` builds the
game to them. If a contract must change, change it **here first**, then the gate,
then the game, and log a D-nn row. IDs cite `docs/DESIGN.md`.

## C1 — Command lines

| Process | Command | Window |
|---|---|---|
| Launcher (TL) | `python -m starbour` (no arguments) | the console it was started from |
| Engine (T0) | `python -m starbour --role engine` | none (`CREATE_NO_WINDOW`); logs to `logs/engine.log` |
| T1 Command | `python -m starbour --role command` | new console (`CREATE_NEW_CONSOLE`) |
| T2 Grid | `python -m starbour --role grid` | new console |
| T3 Output | `python -m starbour --role output` | new console |
| T4 Status | `python -m starbour --role status` | new console |
| Headless sim (D-55) | `python -m starbour sim [options]` | none |

`python` means `E:\CODE\master-of-starbour\MOS\Scripts\python.exe` in dev and
`starbour.exe` when frozen (BR-15: `starbour.exe --role <role>`). The launcher sets
`PYTHONUTF8=1` and `MOS_LAUNCHER_PID=<its pid>` in every child's environment (D-59,
D-68). Every child exits within 2 s of `MOS_LAUNCHER_PID` no longer existing.

Common options for every role: `--config PATH` (default: `controls_config.ini` from
`starbour.paths.root()`), `--pub-port N`, `--pull-port N` (default: `[bus]`), `--log-level`,
and for the launcher and engine `--seed N` (overrides `[world] seed` for a new game;
the launcher passes it to the engine). `python -m starbour` must expose a `main()` in
`starbour/__main__.py` (the `starbour` console script in `pyproject.toml` points at it).

## C2 — Headless simulation

```
python -m starbour sim --seed 1234 --turns 30 --ai-player --report verify\artifacts\sim.json
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
| `state.hello` `[ASSUMED]` | engine → all, every 2 s for the whole game (D-70, D-83) | `{"turn_no": 0, "snapshot": {…}, "seed": 1234, "version": "0.1.0"}` — so a terminal opened late (or reopened, TL) can draw immediately; terminals render it only when its `turn_no` is newer than the one they last drew |

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

Each terminal has a pure function in `starbour/terminals/render_<role>.py`:

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
(OV-09c). `starbour.engine.save.load()` returns a state whose snapshot hash equals the
saved one.

## C7 — Exit codes shared by every gate and script

| Code | Meaning |
|---|---|
| 0 | pass |
| 1 | deliverable failure — message names the check and the blueprint ID |
| 2 | (hook only) deliverable failure fed back to the model |
| 3 | `HARNESS ERROR` — wrong interpreter, missing dependency, missing workbook; not a code problem |
| 4 | config validation failed — message names `[section] key` |

## C8 — Conventions the gates settled during the harness build (D-77…D-85)

The harness could not change this file, so it logged what it had to assume. These are
now part of the contract; the M1 builder follows them (full text in `docs/DECISIONS.md`):

- **D-77** supplies prose: a warning event contains `suppl` and one of `warn`/`low`/`mutiny`; the mutiny event contains `mutin`; mutiny timing is judged from `final.mutinies`, never prose.
- **D-78** T3's `render()` takes the whole `turn.resolved` payload (`{turn_no, snapshot, events}`) as its first argument; T1, T2, T4 take the snapshot. Events appear in the given (phase) order.
- **D-79** names: `starbour.config.load_config(path) -> GameConfig` and `state.snapshot_hash()`; expose exactly these.
- **D-80** smoke/03 bound: the best single turn's `ai_ships_moved` ≥ 50 % of (`final.ships` − 1).
- **D-81** ascii glyph set: the active ship is `^` (unicode `▲`, SYM-10).
- **D-82** tier budgets (quick 15 s / full 90 s / live 180 s) are `WARN`; per-check bounds are hard.
- **D-83** `state.hello` every 2 s for the whole game (C3 above already says so).
- **D-84** the gates pass explicit free ports with `--pub-port N --pull-port N`; `0 = random` is not relied on.
- **D-85** the live gate moves `saves\game.json` aside and restores it; it starts the launcher with `--seed 1234`.

## C9 — M2 additions (movement physics, hazards, game over)

Added for `specs/M2.md`; C1–C8 are unchanged. Every field below is additive.

**C1 additions.** The launcher forwards `--teleport` to the engine; the engine role and `sim` both accept `--teleport "gx gy sx sy cx cy"` (D-104, debug: places the player's active ship there before turn 1; the target must be a sector cell; a terrain cell is allowed — that is the point). `sim` also accepts `--player-script PATH` (one order per line, one line per turn, blank line = no order; the script replaces `--ai-player` for the player) and `--world-index` (adds `world_index` to the report).

**C2 report additions** (per turn): `player.shield_current`, `player.shield_max`, `player.hazard` = `null` or `{"kind": "star|black_hole|neutron_star|wormhole", "cell": [cx, cy], "distance": 0, "pull": 1.0, "turns_left": 6, "drain": 10}`, `player.move_remaining` = `null` or `[dx, dy]` (the persisting move), `warps` = list of `{"ship": "s-001", "from": [[gx,gy],[sx,sy]], "to": [[gx,gy],[sx,sy]], "factor": 3.0, "effective": 2.5}`, `destroyed` = list of `{"ship": "s-017", "captain": "c-004", "cause": "star|black_hole|neutron_star", "cell": [cx, cy]}`. `final` additions: `game_over_turn`, `game_over_cause` (string or null), `hazard_deaths` (count, all captains), `warps` (count). `outcome` becomes `game_over` the turn the player's active ship is destroyed; the **in-process** sim stops there (D-106), while a `--bus` sim and the real engine stay up after `game.over` — accepting only `new` and `quit` — until the requested turns are driven or a shutdown arrives, so the game-over state machine can be tested over the bus (smoke/21, live/08). With `--world-index`: `world_index` = `{"player_start": [[gx,gy],[sx,sy],[cx,cy]], "stars": [{"galaxy": [gx,gy], "sector": [sx,sy], "cell": [cx,cy], "bucket": "medium", "mass": 1.0, "colour": "yellow"}, …], "black_holes": [{"galaxy":…, "sector":…, "cell":…, "wormhole_to": null or {"galaxy":…, "sector":…, "cell":…}}], "neutron_stars": [...], "planets": [...]}` — at most 200 entries per list, nearest to the player's start first.

**C3 additions.** `turn.resolved.events[]` gain `"type"` values `hazard` (OUT-03), `destroyed` (OUT-11 / OUT-16), `warp` (OUT-02) and `stopped` (CMD-21). `game.over` (MSG-09) payload: `{"turn_no": 57, "cause": "pulled into the black hole at 5,5 in sector 3,4/7,9", "summary": {"turns": 57, "sectors_visited": 4, "mutinies": 1, "fear": 10, "respect": 13, "gold": 5, "silver": 50}}`; after it the engine answers every order except `new` and `quit` with `order.rejected` (`"reason": "game over — type new to start again (CMD-19)"`), publishes `state.hello` with `game_over: true` in the snapshot, and on `new` publishes a fresh `state.hello` at turn 0 with the new seed.

**Snapshot additions.** `ships[*]` gain `shield_current` (already present), `move_remaining`, `hazard` (as in C2) and `orders` (the queued orders as `[{"command": "move", "args": ["+0", "+6"]}]`, PNL-07); the snapshot gains `game_over: false|true`.

**C4 additions.** T2's sector render tints every cell inside a hazard's pull range and prints `turns_left` next to the player's ship glyph (GRD-03); T4 renders PNL-06 (pull, turns_left, drain) whenever `player.hazard` is not null, PNL-07 (the active ship's `orders` with a persisting move shown as `move +0 +6 (4 cells/turn)`), and a GAME OVER banner when `game_over` is true; T3 renders OUT-03 and OUT-16.

**C5–C7** unchanged.

## C10 — M3 additions (ship combat, reputation, fleets, AI roaming)

Added for `specs/M3.md`; everything is additive to C1–C9.

**C1 additions.** `sim` and the engine role accept, repeatably, `--spawn "<class> gx gy sx sy cx cy [fear] [respect]"` (D-126: an AI captain with one ship of that class at that cell before turn 1; fear/respect default to config) and `--ai passive` (D-125: the AI never opens a battle; it still fires back, offers peace, escapes and surrenders by its rules). `--player-script` lines may now be `fire torpedo <ship_id|name> [n]`, `fire phaser <id>`, `peace offer|accept|reject <captain_id>`, `escape +dx +dy`, `ship <id|n>`, `stop`, and a line may hold several orders separated by `;` (one turn). The hazards debug flags are unchanged.

**C2 report additions.** Per turn: `player.in_battle` (bool), `player.ships` (count), `battles` = list of `{"id": "b-003", "sides": ["c-000", "c-004"], "opened_turn": 12, "opened_by": "c-000", "strength_at_open": {"c-000": 118.0, "c-004": 205.0}, "exchanges": [{"attacker": "s-001", "defender": "s-017", "weapon": "torpedo|phaser", "distance": 1, "hit": true, "damage": 25.0, "shields_after": 35.0}], "ended": null | "destroyed|peace|escape", "victor": null | "c-000", "transfer": null | {"gold": 2, "silver": 10, "items": {...}, "pct": 25}}`, `reputation` = list of `{"captain": "c-000", "rule": "FR-01", "fear": 8, "respect": 0, "scale": 1.2}`, `captures` = list of `{"ship": "s-017", "from": "c-004", "to": "c-000"}`; `destroyed[*]` gains `"cause": "combat"` with `"by": "s-001"`; `spawned` (turn 0 only) = the ships `--spawn` created, with ids. `final` additions: `battles` (count opened), `battles_by_outcome` {destroyed, peace, escape, open}, `combat_deaths`, `captures`, `ai_vs_ai_battles`, `player_fear`, `player_respect`, `roams` (AI warps).

**C3 additions.** Event types `combat` (OUT-04), `peace` and `escape` (OUT-05), `loot` (OUT-06), `reputation` (OUT-09), `capture` (OUT-11), `scan` now lists captains in the sector with fear and respect (LV-04). `order.rejected` reasons for `fire` name the range and the distance (`"target s-017 is 4 cells away; phaser range 2 (CM-02)"`). Snapshot additions: `battles` (as in C2, without exchanges), `ships[*].in_battle` (battle id or null), `ships[*].target` (last fire/peace target, GRD-08), `captains[*].visible_reputation` (true for captains in the player's sector after a scan this turn).

**C4 additions.** T2: an AI ship in battle with the player renders in the SYM-12 battle colour and the last target carries the GRD-08 marker; owned ships use SYM-11. T4: PNL-06 shows enemies in range (name, distance, shields) while in battle; PNL-05 lists every owned ship. T3: OUT-04 one line per exchange (attacker → defender, weapon, hit/miss, damage, shields left), OUT-05, OUT-06, OUT-09 (`FR-nn`, Δfear, Δrespect, new levels), OUT-11 for captures. T1: `fire` on a captain you are not yet in battle with asks `Start a fight with <captain>? Respect −5 [y/N]` (UX-04, CM-01).

**Save (C6).** Open battles and per-ship order queues survive save/load.
