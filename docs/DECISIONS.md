# Master of Starbour — DECISIONS.md

Settled decisions. D-01…D-53 are generated from tab 17 of `docs/master_of_starbour_blueprint.xlsx` by `tools/blueprint_to_design.py` — do not edit them here. Kit decisions (D-54 onwards) are hand-maintained below the marker and are kept by the generator.

## From the workbook (tab 17)

| ID | Date | Decision | Rationale / source | Ref | Status |
|---|---|---|---|---|---|
| D-01 | 2026-10-25 | Python 3.12, shipped as a Windows 10/11 executable. | Project fixed facts. |  | Agreed |
| D-02 | 2026-10-25 | One command terminal plus output terminals over a local message bus; one process per terminal, threads within. | BP r15–21; Project. | BUS-01, BUS-02 | Agreed |
| D-03 | 2026-10-25 | All names are original; no franchise lore. | Project instructions. | Q-20 | Agreed |
| D-04 | 2026-10-25 | The game is called Master of Starbour. | Chat A1. | BR-01 | Agreed |
| D-05 | 2026-10-25 | Repository git@github.com:selphaware/master-of-starbour.git — public; source code and installable package. | Chat A2–A3. | BR-02, BR-03 | Agreed |
| D-06 | 2026-10-25 | Delivered as a downloadable executable package for Windows. | Chat A3. | BR-13 | Agreed |
| D-07 | 2026-10-25 | The game never ends on success: mastery triggers a celebration and ceremony, then play carries on. | Chat A4. | OV-09 | Agreed |
| D-08 | 2026-10-25 | Dev environment: root E:\CODE\master-of-starbour; venv MOS; python E:\CODE\master-of-starbour\MOS\Scripts\python.exe. | Chat A5–A6. | BR-06…08 | Agreed |
| D-09 | 2026-10-25 | Losing a battle loses the game; the player starts again. | Chat A6. | OV-09b | Agreed |
| D-10 | 2026-10-25 | The repo is public; git operations over SSH. | Chat A7. | BR-23 | Agreed |
| D-11 | 2026-10-25 | The game is colourful: every terminal uses colour throughout. | Chat A8. | OV-01, UX-08 | Agreed |
| D-12 | 2026-10-25 | Turns resolve simultaneously for all captains in the fixed phase order TP-01…08. | Ans Q-01. | OV-02 | Agreed |
| D-13 | 2026-10-25 | Impulse 1 = 1 sector cell per turn, linear; warp reach grows exponentially with the factor; warp 10 = anywhere; gravity pull nets against speed. | Ans Q-02. | MV-01…03 | Agreed |
| D-14 | 2026-10-25 | Falling into a star or black hole is fatal if the ship does not get out in time; black holes allow less time and need more engine power. | Ans Q-02. | HZ-02, HZ-04 | Agreed |
| D-15 | 2026-10-25 | At 0 shields the next hit destroys the ship; there is no separate hull stat. | Ans Q-03. | HZ-09, SH-07 | Agreed |
| D-16 | 2026-10-25 | Grids are sparse. | Ans Q-04. | WG-00 | Agreed |
| D-17 | 2026-10-25 | Grid dimensions are config keys; the Grid terminal switches between sector, galaxy and universe views. | Ans Q-05. | VW-01…03 | Agreed |
| D-18 | 2026-10-25 | One star (or hole / planet) per cell; binary systems are stars in adjacent cells; items and ships share cells. | Ans Q-06. | WG-03, SP-02 | Agreed |
| D-19 | 2026-10-25 | Items are picked up by choice: enter the cell, scan, then pick up. About 10 special and 100 normal items per galaxy. | Ans Q-07. | IT-04, SP-03 | Agreed |
| D-20 | 2026-10-25 | The world is generated from a seed; wormhole pairs are fixed at seeding and may cross galaxies; ships survive the crossing. | Ans Q-08, Q-11. | SP-01, ST-04 | Agreed |
| D-21 | 2026-10-25 | A neutron star behaves like a black hole with weaker pull. | Ans Q-09. | ST-02 | Agreed |
| D-22 | 2026-10-25 | Energy = base_energy × volume (from radius); thresholds are config keys. | Ans Q-10. | DQ-01, DQ-02 | Agreed |
| D-23 | 2026-10-25 | Fleets have no size limit; all ships in range fight under the same rules; strength counts the whole fleet (shields + weapons). | Ans Q-13, Q-22. | FL-05, CM-08 | Agreed |
| D-24 | 2026-10-25 | 1 gold = 20 silver; items are bought with coins; items belong to the captain. | Ans Q-14. | TR-01, CP-04 | Agreed |
| D-25 | 2026-10-25 | Fear and respect are ints 0–100. Eligibility: both ≥ 90 plus a wealth threshold. The ultimate secret item is guarded by ships in a sector far from the captain. | Ans Q-15. | EG-01…06 | Agreed |
| D-26 | 2026-10-25 | Grid auto-follows the active ship; 'view' overrides. | Ans Q-17. | GRD-02 | Agreed |
| D-27 | 2026-10-25 | Separate engine process; ZeroMQ pub/sub on localhost. | Ans Q-18. | BUS-03 | Agreed |
| D-28 | 2026-10-25 | Food and water are consumed −1 each per turn and bought from planets. | Ans Q-19. | IT-01 | Agreed |
| D-29 | 2026-10-25 | Elements use real and mythical crystal / mineral names. | Ans Q-20. | EL-* | Agreed |
| D-30 | 2026-10-25 | Planets can attack and be attacked; a destroyed planet is fully looted; on peace a share goes to the side that did not sue for peace. | Ans Q-21. | PR-02, CB-03 | Agreed |
| D-31 | 2026-10-25 | Respect penalties FR-05 and FR-06 both apply (stack). | Ans Q-23. | FR-09 | Agreed |
| D-32 | 2026-10-25 | Four terminals: T1 Command, T2 Grid, T3 Output, T4 Status. | Ans Q-25. | T4 | Agreed |
| D-33 | 2026-10-25 | 'Starbour' is a pun on harbour / starship; the title Master of Starbour is won with the ultimate secret item. | Ans Q-26. | OV-13 | Agreed |
| D-34 | 2026-10-25 | Packaging: PyInstaller one-folder, zipped, GitHub Releases, unsigned at first, config next to the exe. | Ans Q-28. | BR-14…20 | Agreed |
| D-35 | 2026-10-25 | Game over only when the player's ship is destroyed. | Ans Q-29. | CB-04 | Agreed |
| D-36 | 2026-10-25 | Fog of war is a config switch (on / off). | Ans Q-30. | GRD-04 | Agreed |
| D-37 | 2026-10-25 | Terminals redraw once per resolved turn. | Ans Q-31. | BUS-05 | Agreed |
| D-38 | 2026-10-25 | Launcher owns all processes; closing T1 or the launcher ends the game; closing another window reopens it. | Ans Q-32. | TL | Agreed |
| D-39 | 2026-10-25 | Autosave every turn, one slot per game, save deleted on game over; new game = new seed. | Ans Q-33. | OV-12 | Agreed |
| D-40 | 2026-10-25 | Errors inline on T1 and echoed to T3; confirmations for starting a fight and quitting. | Ans Q-34. | UX-03/04 | Agreed |
| D-41 | 2026-10-25 | Pacing target: a battle every 5–7 turns; mastery after ~70–100 turns. | Ans Q-35. | OV-11 | Agreed |
| D-42 | 2026-10-25 | No extra fear / respect events, no decay; levels visible for captains in the same sector. | Ans Q-36. | FR-10 | Agreed |
| D-43 | 2026-10-25 | Mastery ceremony: fireworks and big ASCII-art title; afterwards nothing changes except maxed levels and universal compliance. | Ans Q-37. | OUT-15, EF-06 | Agreed |
| D-44 | 2026-10-25 | Fractional impulse speed is floored: engine 2.5 moves 2 cells; the fraction only matters against gravity pull. | Chat A9. | MV-01 | Agreed |
| D-45 | 2026-10-25 | Game over when the player's active ship is destroyed; other owned ships do not save the run. | Chat A10. | OV-09b, FL-04 | Agreed |
| D-46 | 2026-10-25 | On peace, the victor is the side that did not offer it; X = victor's shield advantage as a fraction × peace_loot_max, floored at 0. | Chat A12; resolves BP r108 vs Ans Q-21. | CB-03 | Agreed |
| D-47 | 2026-10-25 | At zero food or water the crew mutiny and the captain is demoted to a low-class ship. It is not game over. | Chat A11. | SUP-03 | Agreed |
| D-48 | 2026-10-25 | Fear acts on ships (AI captains yield); respect acts on planets (they comply). | Chat A13. | EF-00 | Agreed |
| D-49 | 2026-10-25 | No fixed turn counts for pacing or thresholds; every number is a placeholder tuned by playtest. | Chat A15, A16. | OV-11, LV-05 | Agreed |
| D-50 | 2026-10-25 | Licence: MIT. Chosen by Claude at Usman's request. | Chat A14, A20. | BR-04, KIT-10 | Agreed |
| D-51 | 2026-10-25 | On a mutiny the captain moves to another owned ship if there is one; only if none is available is the captain demoted to a low-class ship. | Chat A19. | SUP-04, SUP-04b | Agreed |
| D-52 | 2026-10-25 | Mutineers keep the taken ship and become a new AI captain. | Chat A18. | SUP-04c | Agreed |
| D-53 | 2026-10-25 | The upgrade map, the price list and the element catalogue are configuration read from controls_config.ini ([upgrades], [prices], [elements]); values are Claude's placeholders, changeable outside the game. | Chat A17. | TR-05, UP-*, PRC-*, EL-* | Agreed |

<!-- KIT DECISIONS — everything below this line is hand-maintained; the generator keeps it. -->

## Kit decisions (hand-maintained)

Made while producing the build kit on 2026-10-25. Status Proposed = awaiting Usman's yes; each is offered to the workbook as a new row.

| ID | Date | Decision | Rationale / source | Ref | Status |
|---|---|---|---|---|---|
| D-54 | 2026-10-25 | Package / import name is `mos` (adopts BR-05 Proposed as the working default). | BR-05; fewer names to keep in step with the venv. | BR-05 | Proposed |
| D-55 | 2026-10-25 | Headless simulation is a first-class engine entry point: `python -m mos sim --seed N --turns T --ai-player --report PATH` runs the engine in-process with an AI-controlled player and writes a JSON report (docs/CONTRACTS.md C2). The gates drive it; it is kept working on every change. | Without it nothing about turns, AI or mutiny can be verified without windows. [ASSUMED] | D-49, KIT-09 | Proposed |
| D-56 | 2026-10-25 | Three gate tiers: hook `smoke_test.py . --quick` (≤ 15 s, only for writes under mos/, tests/, controls_config.ini, pyproject.toml) → `smoke_test.py .` (≤ 90 s, headless) → `live_test.py .` (≤ 3 min, real windows). Exit codes per CONTRACTS C7. | A full multi-process run on every edit is too slow and flaky for a hook; a unit-test-only hook misses 'broken but passes tests'. [ASSUMED] | KIT-09, BUS-06 | Proposed |
| D-57 | 2026-10-25 | Every process talks only to the engine: T1 and the launcher PUSH to the engine's PULL; the engine is the sole PUB. `view.changed` (MSG-04) and `system.shutdown` (MSG-08) are re-published by the engine rather than sent terminal-to-terminal. | One socket per terminal; one owner of ordering. Refines MSG-04/08 which were Proposed. [ASSUMED] | BUS-03, BUS-04, MSG-04, MSG-08 | Proposed |
| D-58 | 2026-10-25 | Wire format: ZeroMQ multipart `[topic, JSON payload]`, UTF-8; topics are the MSG names. | Stdlib JSON, no extra dependency; sizes small (BUS-07). [ASSUMED] | BUS-03, MSG-01…09 | Proposed |
| D-59 | 2026-10-25 | Every child process polls `psutil.pid_exists(MOS_LAUNCHER_PID)` and exits within 2 s of the launcher vanishing, in addition to the launcher terminating its children. | Belt and braces for the no-orphans rule; a hard-killed launcher cannot clean up. [ASSUMED] | BUS-06, TL | Proposed |
| D-60 | 2026-10-25 | In M1–M3 supplies upkeep and mutiny apply to the player only; AI captains' supplies switch on in M4 when AI can buy from planets. | Otherwise every AI captain mutinies every ~33 turns in a run without trade, and each mutiny adds a captain (SUP-04c) — population grows without bound. Needs Usman's answer: does SUP-04c apply to AI captains, or do AI mutineers simply replace the captain? [ASSUMED] | SUP-03, SUP-04c, CP-09 | Proposed |
| D-61 | 2026-10-25 | Paths: `controls_config.ini`, `saves/` and `logs/` live next to the exe when frozen (`sys.frozen`), else at the repo root. One module (`mos/paths.py`) decides. Save file is `saves/game.json`, one slot, atomic write. | BR-20, BR-21 Proposed; JSON keeps saves inspectable. [ASSUMED] | BR-20, BR-21, OV-12 | Proposed |
| D-62 | 2026-10-25 | `[elements]` lines are `name = category, value, rarity` (the CFG-42 form); upgrade effects live only in `[upgrades]`. Element keys are snake_case (`star_iron`). | Each number has one home; tab 11's 'effect' column would duplicate [upgrades]. [ASSUMED] | CFG-42, EL-01…12, UP-01…05 | Proposed |
| D-63 | 2026-10-25 | Usman installs runtime and dev dependencies up front from `pyproject.toml` (`pip install -e .[dev]`): rich, prompt_toolkit, pyzmq; pytest, pytest-timeout, psutil, openpyxl, pyinstaller. | Deviates from the loop-engineering guide's 'gate deps only' rule because these gates need the runtime libraries (bus round trip, Rich render). Fewer moving parts. Extends BR-10. | BR-10 | Proposed |
| D-64 | 2026-10-25 | M1 scope includes impulse `move` without gravity, AI random-walk movement and player supplies + mutiny, so that 'turns advance', 'AI acts' and 'mutiny fires' are observable in M1. All terrain (holes, wormholes, planets, items) is generated in M1; its rules arrive in M2–M4. | Sharpens KIT-07 'M1 engine + bus + grids'. | KIT-07 | Proposed |
| D-65 | 2026-10-25 | Each terminal has a pure `render(snapshot, view, cfg, width)` (CONTRACTS C4); terminal processes are thin subscribe → render → print loops with no game logic. | Makes 'stale viewport' testable headlessly. [ASSUMED] | BUS-04, BUS-05, GRD-06 | Proposed |
| D-66 | 2026-10-25 | With `MOS_HEARTBEAT_DIR` set, terminals write `<role>.json` {turn_no, rendered_at, pid, render_ms} after every redraw; off in normal play. The live gate reads them. | The only way to prove a real window drew the current turn without screen scraping. [ASSUMED] | BUS-05, KIT-09 | Proposed |
| D-67 | 2026-10-25 | Determinism: world generation uses `random.Random(seed)`; in-game rolls use a separate `random.Random` derived from the seed, advanced only in the engine; no global `random`; no set/dict iteration feeding the RNG or the snapshot. Same seed + same orders = identical snapshots. | SP-01 and CM-03's [RISK] note; makes replays and gate comparisons possible. [ASSUMED] | SP-01, CM-03 | Proposed |
| D-68 | 2026-10-25 | Windows: terminals spawn with `CREATE_NEW_CONSOLE`, the engine with `CREATE_NO_WINDOW`; the launcher sets `PYTHONUTF8=1`; `[ui] glyph_set = unicode|ascii` selects the T2 glyphs for consoles whose font lacks them. | BR-15; glyph fallback is a new Proposed key. [ASSUMED] | BR-15, SYM-01…14 | Proposed |
| D-69 | 2026-10-25 | Every milestone gate includes an AI-only run (`sim --ai-player --turns 500`) that must exit 0 without a traceback. It is a robustness check, never a pacing target. | Chat A15/A16, D-49. | OV-11, LV-05 | Proposed |
| D-70 | 2026-10-25 | The engine publishes `state.hello` (latest snapshot) every 2 s; terminals render it only when its turn_no is newer than the one they last drew; the game resumes from `saves/game.json` on launch when it exists. | ZeroMQ slow-joiner and the reopen rule (TL) need a way for a late terminal to get state. [ASSUMED] | TL, BUS-05, OV-12 | Proposed |
| D-71 | 2026-10-25 | Config keys added beyond tab 15, all placeholders: `[world] galaxy_fill_pct = 80, sector_fill_pct = 80, multi_star_pct = 20, planets_per_system_max = 3`; `[stellar] colour_names`; `[ui] glyph_set`; `[ships] kestrel/corsair/bastion/starting_class`; `[captain] gold_start, silver_start`. Offered to the workbook as Proposed rows CFG-61…. | 'A galaxy or Void' and 'a sector or Void' (WG-01/02) need a fill rule; CLS-* and CP-02/03 numbers were only on tabs 9/10. D-49: every number is config. [ASSUMED] | WG-01, WG-02, SP-02, SP-04, CLS-01…03, CP-02, CP-03 | Proposed |
| D-72 | 2026-10-25 | On a mutiny, in both branches (move to another owned ship / demotion) the captain's food and water are set to `mutiny_supplies` and the zero counter resets. | Supplies are captain-level (CP-07); without this a captain who moves to another ship mutinies again next turn. Needs Usman's yes. [ASSUMED] | SUP-04, SUP-04b, CP-07 | Proposed |
| D-73 | 2026-10-25 | `move x y` is an absolute target cell; `move +dx +dy` (signed) is relative. A move beyond this turn's reach is clipped to `floor(speed)` cells along the Chebyshev path — a single-turn order; the player re-issues. | CMD-06 lists both syntaxes without saying how to tell them apart or what happens beyond reach. [ALTERNATIVE] persistent multi-turn orders — more state, revisit after playtest. [ASSUMED] | CMD-06, MV-01 | Proposed |
| D-74 | 2026-10-25 | `docs/DESIGN.md`, `docs/DECISIONS.md` (D-01…53) and `docs/config_keys.json` are generated from the workbook by `tools/blueprint_to_design.py`; Claude Code never edits the workbook or the generated files; `--check` runs in the gate so they cannot drift. | The workbook is the source of truth; a hand-copied design would drift silently. | KIT-01, KIT-05, KIT-06 | Proposed |
| D-75 | 2026-10-25 | No skill, subagent or outer-loop script at bootstrap; promote one only from repeated friction. | Project rule 'fewer moving parts'; deviates from the loop-engineering template's skill step. | KIT-09 | Proposed |
| D-76 | 2026-10-25 | Supplies warning and mutiny timing, precisely: after consumption at TP-06, `zero_turns += 1` if food == 0 or water == 0 else reset to 0; mutiny fires when `zero_turns > mutiny_turns_at_zero`. With the defaults and no purchases that is turn 33. | SUP-03 says 'after N consecutive turns at 0'; the gate needs one unambiguous turn number. [ASSUMED] | SUP-03, CFG-47 | Proposed |
