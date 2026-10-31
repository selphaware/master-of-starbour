# Master of Starbour — DESIGN.md

**Generated from `docs/master_of_starbour_blueprint.xlsx` by `tools/blueprint_to_design.py`. Do not edit by hand — edit the workbook and regenerate.**

Workbook version: v2.6 · 2026-10-31 · your blueprint + chat facts (A1–A33) + your answers to Q-01…Q-37 (tab 16; Q-38 open) + M1–M4 playtest changes (CMD-08, UX-04, UX-09, OB-05, CFG-25 retuned, CFG-74…83, D-136…139, D-152: respect start 30, planets fire below 10). Yellow = yours to fill or a calculator input. Purple 'Proposed' = Claude's placeholder awaiting your yes.

How to read this file:

- Every row keeps its stable ID (OV-07, CMD-06, HZ-02, CFG-17 …). Cite IDs in every spec, test, gate and decision.
- **Agreed** = Usman's decision, ready for a spec. **Proposed** = Claude's placeholder (a number, a formula, a default) awaiting playtest or Usman's yes — never treat it as decided. **Draft** = reworded from his blueprint, not yet confirmed. **Needs detail** / **Not started** = open.
- Every number is a placeholder read from `controls_config.ini` and tuned by playtest. There are no fixed turn-count targets.
- The decision log (tab 17) is in `docs/DECISIONS.md`; the config-key manifest (tab 15) is also in `docs/config_keys.json`.

Contents:

- [0 Index](#0-index)
- [1 Overview](#1-overview)
- [2 Architecture](#2-architecture)
- [3 T1 Command](#3-t1-command)
- [4 T2 Grid](#4-t2-grid)
- [5 T3 Output](#5-t3-output)
- [5b T4 Status](#5b-t4-status)
- [6 World Grids](#6-world-grids)
- [7 Stellar](#7-stellar)
- [8 Hazards](#8-hazards)
- [9 Ships](#9-ships)
- [10 Captain](#10-captain)
- [11 Items & Planets](#11-items-planets)
- [12 Trade & Economy](#12-trade-economy)
- [13 Combat](#13-combat)
- [14 Fear & Respect](#14-fear-respect)
- [15 Config Keys](#15-config-keys)
- [16 Open Questions](#16-open-questions)
- [18 Build & Release](#18-build-release)
- [Source v1](#source-v1)

---

## 0 Index

**Master of Starbour — Design Blueprint (structured v2.6)**

*v2.6 · 2026-10-31 · your blueprint + chat facts (A1–A33) + your answers to Q-01…Q-37 (tab 16; Q-38 open) + M1–M4 playtest changes (CMD-08, UX-04, UX-09, OB-05, CFG-25 retuned, CFG-74…83, D-136…139, D-152: respect start 30, planets fire below 10). Yellow = yours to fill or a calculator input. Purple 'Proposed' = Claude's placeholder awaiting your yes.*

### Project

- **Game** — Master of Starbour — 'Starbour' is a pun on harbour / starship; the title is won with the ultimate secret item · Chat A1 · Ans Q-26
- **Repository** — git@github.com:selphaware/master-of-starbour.git — public; code + installable; git over SSH · Chat A2–A3, A7
- **Local root** — E:\CODE\master-of-starbour · Chat A6
- **Python** — 3.12 — venv MOS → E:\CODE\master-of-starbour\MOS\Scripts\python.exe · Chat A5
- **Target** — Downloadable Windows 10/11 executable (PyInstaller one-folder zip, GitHub Releases) · Chat A3 · Ans Q-28
- **Win** — Obtain the ultimate secret item → Master of Starbour: ceremony, then play carries on · Chat A4 · Ans Q-15
- **Lose** — Only when the player's active ship is destroyed; start again with a new seed. Running out of food or water is not game over — the crew mutiny and the captain is demoted to a low-class ship · Chat A6, A10, A11
- **Style** — Colourful throughout; four terminals + engine + launcher over ZeroMQ · Chat A8 · Ans Q-18, Q-25

### Tabs & progress

Live counts from each tab's Status column. Proposed = Claude's proposal awaiting your yes — say 'agreed' or change it. 'Open · no Q' = open rows citing no question (plain fill-ins only).

| Tab | Group | What it holds | Agreed | Proposed | Draft | Needs detail | Not started | Rows | % Agreed | Open · no Q |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 Overview | Concept | Game type, purpose, player goals, win/lose, turn phases | 16 | 4 | 4 | 0 | 0 | 24 | 0.6667 | 0 |
| 2 Architecture | Concept | Terminals, bus, process model, message catalogue, diagram | 12 | 10 | 0 | 0 | 0 | 22 | 0.5455 | 0 |
| 3 T1 Command | Terminals | Command catalogue, prompt behaviour, order queueing | 16 | 8 | 6 | 0 | 0 | 30 | 0.5333 | 0 |
| 4 T2 Grid | Terminals | View levels, symbol legend, overlays, refresh | 4 | 21 | 0 | 0 | 0 | 25 | 0.16 | 0 |
| 5 T3 Output | Terminals | Message types, log behaviour | 6 | 5 | 12 | 0 | 0 | 23 | 0.2609 | 0 |
| 5b T4 Status | Terminals | Permanent status panels | 2 | 5 | 0 | 0 | 0 | 7 | 0.2857 | 0 |
| 6 World Grids | World | Grid hierarchy, cell contents, generation rules | 7 | 5 | 0 | 0 | 0 | 12 | 0.5833 | 0 |
| 7 Stellar | World | Stellar types, derived quantities, buckets, calculator | 5 | 7 | 0 | 0 | 0 | 12 | 0.4167 | 0 |
| 8 Hazards | World | Gravity pull, shield drain, black holes, wormholes, calculator | 5 | 5 | 0 | 0 | 0 | 10 | 0.5 | 0 |
| 9 Ships | Entities | Ship attributes, movement, warp curve, classes | 7 | 11 | 0 | 1 | 0 | 19 | 0.3684 | 1 |
| 10 Captain | Entities | Captain attributes, fleet rules, endgame eligibility | 14 | 9 | 0 | 0 | 0 | 23 | 0.6087 | 0 |
| 11 Items & Planets | Entities | Items, element catalogue, planets | 14 | 21 | 0 | 0 | 0 | 35 | 0.4 | 0 |
| 12 Trade & Economy | Rules | Currencies, trade rules, upgrade map, sources and sinks | 17 | 3 | 0 | 0 | 0 | 20 | 0.85 | 0 |
| 13 Combat | Rules | Combat model recommendation, outcomes, loot | 8 | 9 | 0 | 0 | 0 | 17 | 0.4706 | 0 |
| 14 Fear & Respect | Rules | Fear / respect events, level mechanics, effects | 11 | 11 | 0 | 0 | 0 | 22 | 0.5 | 0 |
| 15 Config Keys | Config | Config keys for controls_config.ini | 21 | 62 | 0 | 0 | 0 | 83 | 0.253 | 0 |
| 16 Open Questions | Questions | Open design questions, answers, resolution | 37 | 0 | 0 | 1 | 0 | 38 | 0.9737 | — |
| 17 Decisions | Decisions | Decision log | 64 | 0 | 0 | 0 | 0 | 64 | 1 | — |
| 18 Build & Release | Delivery | Repo, environment, packaging, distribution, build-kit files | 25 | 8 | 0 | 0 | 0 | 33 | 0.7576 | 0 |
| Source v1 | Source | Original blueprint, verbatim, plus chat addendum | — | — | — | — | — | — | — | — |
| Total |  |  | 291 | 204 | 22 | 2 | 0 | 519 | 0.5607 | 1 |

### Legend

- **Yellow cell** — Yours to fill in or decide — or an input to a calculator (7 Stellar, 8 Hazards, 9 Ships).
- **Status** — Agreed = decided by you (ready for a spec) · Proposed = Claude's proposal from your answers, awaiting your yes · Draft = from your blueprint, not yet confirmed · Needs detail = a follow-up question is open in chat · Not started = nothing decided.
- **Origin** — BP rNN = your blueprint row on 'Source v1' · Chat An = your words in chat (addendum) · Ans Q-xx = your answer on tab 16 · Claude = Claude's proposal · Implied rNN = inferred from that row · Project = Project instructions.
- **Open Q** — Q-xx = a row on '16 Open Questions'. Every question now has a resolution column saying where its answer went.
- **Config key** — [section] key as it will appear in controls_config.ini — all on '15 Config Keys'.
- **IDs** — Stable row IDs (CMD-06, HZ-02 …) cited by specs, tests and DECISIONS.md. Keep them when editing; add new rows with the next number.
- **Tab colours** — Blue = concept · Teal = terminals · Purple = world · Green = entities · Orange = rules · Slate = config · Red = questions · Dark green = decisions · Black = build · Grey = source.

---

## 1 Overview

**1 · Overview**

*What the game is, what the player is trying to do, and how a turn runs*

### Concept

| ID | Topic | Statement | Open Q | Origin | Status |
|---|---|---|---|---|---|
| OV-01 | Format | Text-based game played in terminals. Colourful throughout — every terminal uses colour (Rich). |  | BP r3; Chat A8 | Agreed |
| OV-02 | Turn structure | Turn-based: the player queues any number of orders, then ends the turn; all captains' orders resolve simultaneously in the fixed phase order below. | Q-01 | BP r4; Ans Q-01 | Agreed |
| OV-02b | Immediate vs queued | Immediate (no turn needed): help, status, fleet, ship, view, orders, scan. Queued until 'end': move, warp, fire, shields, peace, escape, trade, upgrade, pickup. A second queued order of the same kind for the same ship replaces the first. A persisting move (CMD-06) counts as that ship's queued move; 'stop' (CMD-21) cancels it. | Q-01 | Claude | Proposed |
| OV-03 | Player role | The player is the captain of their ship. |  | BP r9 | Draft |
| OV-04 | Exploration | Starts with one starship and explores the universe: Universe → Galaxies → Sectors → stars / planets / ships / objects. |  | BP r8 | Draft |
| OV-05 | Combat | Ships (and planets) can battle each other (13 Combat). | Q-12 | BP r10; Ans Q-21 | Draft |
| OV-06 | Progression | Captains gain wealth (gold and silver), items, fear and respect. |  | BP r11, r80 | Draft |
| OV-07 | Endgame — becoming Master of Starbour | When respect and fear both reach the threshold (e.g. 90 of 100) and the captain holds enough gold and silver, they become eligible to find the ultimate secret item, guarded by ships in a far-away sector. Obtaining it makes them Master of Starbour: max warp, weapons, shields, engine; infinite wealth; respect and fear at maximum; all planets willing to trade. | Q-15, Q-26 | BP r12; Ans Q-15, Q-26, Q-37 | Agreed |
| OV-08 | Fleet | A captain may own several ships (no limit) and switch between them from the command terminal. | Q-13 | BP r13; Ans Q-13 | Agreed |
| OV-09 | Win condition | The game does not end. Mastery triggers a celebration and ceremony, then play carries on with the max powers. | Q-16 | Chat A4 | Agreed |
| OV-09b | Lose condition | Game over only when the player's active ship is destroyed (the captain is on it); the player starts again. | Q-29 | Chat A6, A10; Ans Q-29 | Agreed |
| OV-09c | 'Start again' | New game = new seed; the save is deleted on game over. | Q-33 | Ans Q-33 | Agreed |
| OV-10 | Opponents | Other captains are AI-controlled; they trade, battle, offer peace and progress. Count per galaxy is a config key. | Q-13 | Implied r90; Claude | Proposed |
| OV-11 | Pacing | No fixed turn counts. Rough feel only: a battle every 5–7 turns on average; mastery a long way off. Every number in this workbook is a placeholder tuned by playtest — 'what is appropriate and what works'. | Q-35 | Ans Q-35; Chat A15, A16 | Agreed |
| OV-12 | Save / load | Autosave every turn, one slot per game, save deleted on game over. Location: next to the exe. | Q-33 | Ans Q-33, Q-28 | Agreed |
| OV-13 | Starbour | 'Starbour' is a play on harbour / starship — not the universe's name. 'Master of Starbour' is the title won with the ultimate secret item. | Q-26 | Ans Q-26 | Agreed |

### Turn resolution order

Agreed as option A of Q-01: all captains' orders resolve together, in this fixed order.

| ID | Phase | What resolves | Open Q | Origin | Status |
|---|---|---|---|---|---|
| TP-01 | 1 Orders | Player and AI captains queue orders; the player ends the turn. |  | BP r4; Ans Q-01 | Agreed |
| TP-02 | 2 Movement | Impulse and warp moves resolve (effective speed = nominal − gravity pull). | Q-02 | Ans Q-02 | Agreed |
| TP-03 | 3 Hazards | Gravity countdowns and shield drain apply (8 Hazards). |  | Implied r65–72 | Agreed |
| TP-04 | 4 Combat | Battles resolve one exchange per turn: destroy / escape / peace (13 Combat). | Q-12 | Claude | Proposed |
| TP-05 | 5 Trade & pickup | Accepted trades settle; upgrades apply; item pickups happen. |  | Ans Q-07, Q-14 | Agreed |
| TP-06 | 6 Upkeep | Food and water −1 each; mutiny check (SUP-03); shields recharge if not in a hazard or battle. | Q-19 | Ans Q-19; Chat A11; Claude | Proposed |
| TP-07 | 7 Reputation | Fear and respect deltas apply (14 Fear & Respect). |  | Implied r94–103 | Agreed |
| TP-08 | 8 Publish & render | turn.resolved on the bus; T2, T3 and T4 redraw once; autosave. | Q-31 | Ans Q-31, Q-33 | Agreed |

---

## 2 Architecture

**2 · Architecture**

*Terminals, the message bus, processes and the message catalogue*

### Terminals

| ID | Terminal | Role | Detail tab | Open Q | Origin | Status |
|---|---|---|---|---|---|---|
| T1 | Command | Player types orders, switches ships, changes the grid view, ends the turn. | 3 T1 Command |  | BP r17 | Agreed |
| T2 | Grid | Map at Universe, Galaxy or Sector level; auto-follows the active ship, 'view' overrides. | 4 T2 Grid | Q-17 | BP r18; Ans Q-17 | Agreed |
| T3 | Output | Results, reports, log. | 5 T3 Output |  | BP r19 | Agreed |
| T4 | Status | Permanent status panel: captain, active ship, fleet, cargo. | 5b T4 Status | Q-25 | Ans Q-25 | Agreed |
| T0 | Engine | Owns the game state; resolves turns; publishes state. Its own process, no window (or a hidden console). | — | Q-18 | Ans Q-18 | Agreed |
| TL | Launcher | Starts the engine and the four terminals; owns them; closing T1 or the launcher ends the game and kills the rest; closing T2 / T3 / T4 reopens it. | 18 Build & Release | Q-32 | Ans Q-32 | Agreed |

### Bus & processes

| ID | Topic | Statement | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|
| BUS-01 | Transport | A message bus connects all processes. |  |  | BP r21 | Agreed |
| BUS-02 | Process model | One process per terminal plus the engine; threads within each. | Project fixed fact. |  | Project | Agreed |
| BUS-03 | Technology | ZeroMQ (pyzmq) over localhost TCP: engine PUB → terminals SUB for state; T1 PUSH → engine PULL for orders. | Ports are config keys. | Q-18 | Ans Q-18 | Agreed |
| BUS-04 | Single owner of state | The engine process is the only writer of game state; terminals are views. |  | Q-18 | Ans Q-18 | Agreed |
| BUS-05 | Refresh model | Terminals redraw once per turn.resolved, full redraw, target under 0.5 s. |  | Q-31 | Ans Q-31 | Agreed |
| BUS-06 | Startup / shutdown | Launcher owns child processes; 'quit' or closing T1 → engine saves, launcher terminates all children; no orphans. | Build gates: 'orphaned processes', 'exe opens and exits'. | Q-32 | Ans Q-32 | Agreed |
| BUS-07 | Snapshot vs delta | state.updated carries a full snapshot each turn (simplest; sizes are small). | Revisit only if redraw exceeds 0.5 s. | Q-31 | Claude | Proposed |

### Message catalogue

Payload fields are proposals; rename freely.

| ID | Message | Publisher → Subscribers | Payload (fields) | Open Q | Origin | Status |
|---|---|---|---|---|---|---|
| MSG-01 | order.submitted | T1 → Engine | command, args, ship_id, turn_no |  | Claude | Proposed |
| MSG-02 | turn.end | T1 → Engine | turn_no |  | Claude | Proposed |
| MSG-03 | turn.resolved | Engine → T2, T3, T4 | turn_no, full state snapshot, events[] | Q-31 | Ans Q-31; Claude | Proposed |
| MSG-04 | view.changed | T1 → T2 | level (universe / galaxy / sector), focus coordinates |  | Claude | Proposed |
| MSG-05 | event.log | Engine → T3 | type, text, severity, turn_no (also inside turn.resolved) |  | Claude | Proposed |
| MSG-06 | order.rejected | Engine → T1, T3 | order, reason | Q-34 | Ans Q-34 | Proposed |
| MSG-07 | order.accepted | Engine → T1 | order, queue position |  | Claude | Proposed |
| MSG-08 | system.shutdown | Launcher → all | reason | Q-32 | Ans Q-32 | Proposed |
| MSG-09 | game.over / game.mastery | Engine → all | cause / ceremony payload | Q-37 | Claude | Proposed |

Diagram — every process talks only to the bus

### ZeroMQ bus (localhost)

- **▲ ▼** — ▲ ▼ · ▲ ▼ · ▲ ▼ · ▲ ▼
- **T2 Grid** — T3 Output · T4 Status · T1 Command · T0 Engine

Launcher starts and owns all six processes (Q-32).

---

## 3 T1 Command

**3 · T1 Command terminal**

*The input terminal: every command the player can type, and how the prompt behaves*

- **Purpose** — Where the player types orders, switches ships, changes the grid view and ends the turn. · BP r17, r13, r18
- **Process / module** — TBD — e.g. mos/terminals/command.py · Template
- **Input style** — Free-text command line with history and tab completion. · Claude · Proposed
- **Publishes** — order.submitted, turn.end, view.changed · 2 Architecture
- **Subscribes** — order.accepted, order.rejected, turn.resolved (unlocks the prompt) · Ans Q-34
- **Library** — Rich (colour output) + prompt_toolkit (input) · Claude · Proposed

### Command catalogue

Immediate = acts at once. Queued = waits for 'end'. Rename freely.

| ID | Command | Syntax | What it does | When valid | Feedback / errors | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| CMD-01 | help | help [command] | Lists commands, or shows one command's syntax. | Immediate |  |  | Template | Draft |
| CMD-02 | status | status | Shows the active ship's stats and the captain's wealth, fear, respect, food and water. | Immediate |  |  | Implied r75, r80 | Draft |
| CMD-03 | ship | ship <name \| n> | Switches the active ship. | Immediate; captain owns more than one ship | Error if unknown | Q-13 | BP r13 | Agreed |
| CMD-04 | fleet | fleet | Lists all owned ships, their locations and state. | Immediate |  | Q-13 | Implied r13 | Draft |
| CMD-05 | view | view universe \| galaxy \| sector \| follow | Changes the Grid terminal level; 'follow' returns to auto-follow. | Immediate |  | Q-17 | BP r18; Ans Q-17 | Agreed |
| CMD-06 | move | move <dx dy \| x y> [speed] | Impulse move within the sector. speed ≤ engine_power; cells moved per turn = floor(speed − gravity pull). A target beyond this turn's reach is not clipped: the order persists and the ship continues next turn until it arrives, a new move replaces it, or 'stop' cancels it. | Queued | Reject if speed > engine_power or target outside the sector | Q-02 | Ans Q-02; Chat A22 | Agreed |
| CMD-07 | warp | warp <gx gy> <sx sy> [factor] | Warp to a sector (in any galaxy). factor ≤ warpcore_power; reach from the warp curve (9 Ships). | Queued | Reject if destination beyond reach for the factor | Q-02 | Ans Q-02 | Agreed |
| CMD-08 | fire | fire <target> \| fire torpedo <target> [n] \| fire phaser <target> | Attacks a ship or planet in range. 'fire <target>' fires one torpedo and the phasers; the long forms fire one weapon (n torpedoes, default 1). One torpedo order and one phaser order per ship per turn; no standing orders — a fire order lasts one turn and must be given again each turn (the AI fires every turn by itself). Starting a fight costs respect; asks for confirmation. | Queued; target in range | Confirm (Q-34) | Q-12 | Implied r75, r100; Ans Q-34; Chat A30–A31 | Agreed |
| CMD-09 | shields | shields status | Shows current / max shields. Shield power is not adjustable — it drains and recharges. | Immediate |  | Q-12 | Claude | Proposed |
| CMD-10 | peace | peace offer \| accept \| reject <captain \| planet> | Offers, accepts or rejects peace during a battle. | Queued; during a battle |  | Q-12 | Implied r99, r108 | Draft |
| CMD-11 | escape | escape <dx dy> | Moves out of all enemies' weapon range; succeeds if the move completes (no dice). | Queued; during a battle |  | Q-12 | Claude | Proposed |
| CMD-12 | trade | trade <captain \| planet> give <items> for <items> | Proposes a trade of items, gold or silver. 1 gold = 20 silver. | Queued; counterparty in the same cell |  | Q-14 | Ans Q-14; Claude | Proposed |
| CMD-13 | upgrade | upgrade <component> with <element> | Spends an element to improve torpedoes, phasers, shields, engine or warp core (12 Trade, upgrade map). | Queued | Reject if the element does not improve that component | Q-14 | BP r91 | Draft |
| CMD-14 | scan | scan | Reveals what is in the current cell (items) and, with fog of war on, the surrounding sector. | Immediate |  | Q-07, Q-30 | Ans Q-07, Q-30 | Agreed |
| CMD-15 | pickup | pickup <item> | Picks up an item in the current cell after a scan. Items are never picked up automatically. | Queued; item in cell and scanned |  | Q-07 | Ans Q-07 | Agreed |
| CMD-16 | orders | orders [cancel n] | Reviews or cancels queued orders before the turn ends. | Immediate |  | Q-01 | Template | Draft |
| CMD-17 | end | end | Ends the turn; all queued orders execute. | Immediate | Confirm if a hazard countdown is running and no move is queued | Q-01, Q-34 | BP r4 | Agreed |
| CMD-18 | quit | quit | Confirms, autosaves, then the launcher closes every window. | Immediate | Confirm | Q-32, Q-34 | Ans Q-32, Q-34 | Agreed |
| CMD-19 | new | new [seed] | Starts a new game (new seed). Offered after game over; also available any time with confirmation. | Immediate | Confirm | Q-33 | Ans Q-33 | Agreed |
| CMD-20 | buy / sell | buy <item> <qty> / sell <item> <qty> | Buys from or sells to the planet in the current cell at its prices (shortcut for trade with a planet). | Queued; planet in cell |  | Q-14 | Ans Q-14; Claude | Proposed |
| CMD-21 | stop | stop [all] | All stop: cancels the active ship's persisting move and any queued move for this turn. 'stop all' (alias 'all stop') does it for every owned ship. Syntax is Claude's; the command is yours. | Immediate | Info message if there is nothing to stop | Q-02 | Chat A22 | Agreed |

### Prompt & interaction

| ID | Aspect | Current thinking | Example | Edge cases | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| UX-01 | Prompt format | Turn number, active ship, queued-order count. | [T12 · Kestrel · 2 orders] > |  |  |  | Claude | Proposed |
| UX-02 | Many orders per turn | Orders are queued, then 'end' executes them all. |  | Same-kind order for the same ship replaces the earlier one (OV-02b). |  | Q-01 | BP r4; Ans Q-01 | Agreed |
| UX-03 | Error display | Rejected orders and invalid commands show inline on T1 and are echoed to the T3 log. |  |  |  | Q-34 | Ans Q-34 | Agreed |
| UX-04 | Confirmations | Confirm before: starting a fight, quit, new game, ending a turn inside a hazard with no move queued, and ending a turn in a battle with no fire order queued for the active ship (Proposed — a reminder, since fire orders are per turn). |  |  |  | Q-34 | Ans Q-34; Claude (battle reminder) | Agreed |
| UX-05 | History / autocomplete | Arrow-key history; tab completion of commands, ship, captain and planet names. |  |  |  |  | Claude | Proposed |
| UX-06 | Waiting for resolution | Prompt locks after 'end' and shows a spinner until turn.resolved. |  |  |  |  | Claude | Proposed |
| UX-07 | Orders per ship | Each owned ship has its own order queue; 'ship' switches which queue you are filling. |  |  |  | Q-13 | Claude | Proposed |
| UX-08 | Colour | Commands, errors, warnings and confirmations each have their own colour; same palette as T2 / T3 / T4. |  |  |  |  | Chat A8 | Agreed |
| UX-09 | Numbers in-game | help fire lists the live numbers from the ini (torpedo damage and hit chance against the target's engine, phaser damage at distance 0 / 1 / 2); the T4 battle panel shows each enemy's shields % and, once scanned, the shield % at which it will sue for peace (EF-02). | help fire → 'torpedo 25 at 58 % vs engine 4; phasers 15 / 10 / 5' |  | Read from config, never literals (D-49). | Q-12 | Chat A31 | Agreed |

---

## 4 T2 Grid

**4 · T2 Grid terminal**

*The map: levels, symbol legend, overlays and refresh*

- **Purpose** — Shows the map at Universe, Galaxy or Sector level; auto-follows the active ship, 'view' overrides. · BP r18; Ans Q-17
- **Process / module** — TBD — e.g. mos/terminals/grid.py · Template
- **Subscribes** — turn.resolved, view.changed · 2 Architecture
- **Publishes** — nothing (display only) · Claude · Proposed
- **Render library** — Rich — full-colour cells · Chat A8 · Proposed
- **Refresh** — Full redraw once per turn.resolved and on view.changed · Ans Q-31
- **Fog of war** — Config key [world] fog_of_war = on \| off. On: sector contents hidden until visited or scanned; galaxy / universe layout always visible. · Ans Q-30

### View levels

Default sizes are proposals; all three are config keys (Q-05).

| ID | Level | Each cell shows | Grid size (M × N) | Highlights | Cell format | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| VW-01 | Universe | A galaxy or Void. | 8 × 8 galaxies | Galaxy containing the active ship | 2 chars + colour | Q-05 | BP r35–36; Claude | Proposed |
| VW-02 | Galaxy | A sector or Void. | 16 × 16 sectors | Sector containing the active ship; sectors with known hazards | 2 chars + colour | Q-05 | BP r39–40; Claude | Proposed |
| VW-03 | Sector | A star / black hole / neutron star / planet, items, ships, or Void. | 24 × 24 cells | Active ship; owned ships; enemy ships; hazard cells | 2 chars + colour | Q-05, Q-06 | BP r43–44; Claude | Proposed |

### Symbol legend

Glyphs are proposals. Star colours follow the real stellar sequence from temperature (7 Stellar).

| ID | Object | Glyph | Colour | Colour rule | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| SYM-01 | Void | · | dark grey |  |  |  | Claude | Proposed |
| SYM-02 | Galaxy (universe view) | ◎ | white |  |  |  | Claude | Proposed |
| SYM-03 | Sector (galaxy view) | ▪ | grey; cyan if visited |  |  | Q-30 | Claude | Proposed |
| SYM-04 | Star | ★ | by temperature | red < 3 700 K · orange < 5 200 · yellow < 6 000 · yellow-white < 7 500 · white < 10 000 · blue-white < 30 000 · blue above | Thresholds are config keys | Q-10 | BP r53–55, r111; Claude | Proposed |
| SYM-05 | Neutron star | ✦ | magenta |  | Pulls across the sector, weaker than a black hole | Q-09 | Ans Q-09 | Proposed |
| SYM-06 | Black hole | ● | bright red |  | Whole-sector hazard |  | Claude | Proposed |
| SYM-07 | Wormhole | ◉ | purple |  | A black hole with a fixed partner | Q-11 | Ans Q-11 | Proposed |
| SYM-08 | Planet | ○ | green |  |  |  | Claude | Proposed |
| SYM-09 | Item (normal / special) | ? / ✧ | yellow / bright yellow |  | Shown only after a scan when fog of war is on | Q-07 | Ans Q-07 | Proposed |
| SYM-10 | Active ship (player) | ▲ | bright cyan |  |  |  | Claude | Proposed |
| SYM-11 | Other owned ship | △ | cyan |  |  | Q-13 | Claude | Proposed |
| SYM-12 | AI captain's ship | ▼ | orange; red when in battle with you |  |  |  | Claude | Proposed |
| SYM-13 | Binary / triple star | ★★ | each by its own temperature |  | Stars are always in separate cells (Q-06); a system = stars in adjacent cells | Q-06 | Ans Q-06 | Agreed |
| SYM-14 | Several things in one cell | ship glyph wins |  | Priority: active ship > other ship > item > terrain | Only one star / planet / hole per cell | Q-06 | Ans Q-06; Claude | Proposed |

### Overlays & behaviour

| ID | Aspect | Current thinking | Example | Edge cases | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| GRD-01 | Coordinates | Zero-based x y, column labels along the top, row labels down the side; commands use the same numbers. | move 12 7 |  |  | Q-05 | Claude | Proposed |
| GRD-02 | Level switching | Auto-follows the active ship (sector view); 'view' overrides until 'view follow'. |  |  |  | Q-17 | Ans Q-17 | Agreed |
| GRD-03 | Hazard overlay | Cells within pull range are tinted; the countdown (turns until pulled in) is shown next to the ship. |  |  |  | Q-02 | Claude | Proposed |
| GRD-04 | Fog of war | Config on / off (see header). |  |  |  | Q-30 | Ans Q-30 | Agreed |
| GRD-05 | Viewport | Grid is sized to fit the window at the default sizes; larger configs scroll centred on the ship. |  |  |  | Q-05 | Claude | Proposed |
| GRD-06 | Refresh rule | Full redraw once per turn.resolved. |  |  |  | Q-31 | Ans Q-31 | Agreed |
| GRD-07 | Header line | Level · galaxy / sector coordinates · turn number · fog on/off. |  |  |  |  | Claude | Proposed |
| GRD-08 | Target marker | Highlights the ship or planet named in the last fire / trade order. |  |  |  |  | Claude | Proposed |

---

## 5 T3 Output

**5 · T3 Output terminal**

*Results, reports and the event log*

- **Purpose** — Shows results, reports and information — the event log. · BP r19
- **Process / module** — TBD — e.g. mos/terminals/output.py · Template
- **Subscribes** — turn.resolved, event.log, order.rejected · 2 Architecture
- **Publishes** — nothing (display only) · Claude · Proposed
- **Render library** — Rich — colour per severity · Chat A8
- **Scrollback** — Last 500 lines kept; grouped by phase within a turn · Claude · Proposed

### Message types

'Example line' is where you write what it should actually look like.

| ID | Message type | Triggered by | Content (fields) | Example line | Severity / colour | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| OUT-01 | Turn summary | Turn resolved | turn no, orders executed, notable events | TBD | Info |  | Implied r4 | Draft |
| OUT-02 | Movement result | Move / warp resolved | ship, from, to, speed / factor, pull netted | TBD | Info | Q-02 | Ans Q-02 | Draft |
| OUT-03 | Hazard warning | Ship within pull range | object, pull, turns until pulled in, shield drain this turn | TBD | Warning (yellow) | Q-02 | Ans Q-02 | Draft |
| OUT-04 | Combat report | Battle exchange resolved | attacker, defender, weapon, hit / miss, damage, shields left | TBD | Alert (red) | Q-12 | Claude | Proposed |
| OUT-05 | Peace / escape | Peace offered / accepted / rejected; escape attempted | parties, result, X% transferred | TBD | Alert | Q-12 | Implied r107–108 | Draft |
| OUT-06 | Loot acquired | Opponent destroyed, or peace settlement | items, gold, silver, stat gains | TBD | Info (green) | Q-12 | Implied r106 | Draft |
| OUT-07 | Trade result | Trade / buy / sell accepted or rejected | counterparty, given, received | TBD | Info | Q-14 | Ans Q-14 | Draft |
| OUT-08 | Upgrade applied | Element used on a component | component, before → after | TBD | Info | Q-14 | Implied r91 | Draft |
| OUT-09 | Fear / respect change | Any control event | event, Δ fear, Δ respect, new levels | TBD | Info |  | Implied r94–103 | Draft |
| OUT-10 | Scan result / item found | scan; pickup | items in cell, hazards nearby | TBD | Info | Q-07 | Ans Q-07 | Draft |
| OUT-11 | Ship lost / fleet change | Ship destroyed, acquired or switched | ship, cause | TBD | Alert | Q-13 | Implied r13 | Draft |
| OUT-12 | Endgame eligibility | Thresholds reached | which thresholds; where the guarded sector is | TBD | Highlight | Q-15 | Ans Q-15 | Draft |
| OUT-13 | Invalid command / rejected order | Bad input, impossible order | message, reason (echo of T1) | TBD | Error | Q-34 | Ans Q-34 | Agreed |
| OUT-14 | System | Bus connected, saved, AI thinking, shutdown | message | TBD | Muted | Q-32 | Ans Q-32 | Draft |
| OUT-15 | Mastery ceremony | Ultimate item obtained | fireworks animation, big ASCII-art title, stats maxed, planets open | TBD | Highlight (full colour) | Q-37 | Ans Q-37 | Agreed |
| OUT-16 | Game over | Player's ship destroyed | cause, turn reached, run summary, 'new' to start again | TBD | Alert | Q-29 | Ans Q-29 | Agreed |
| OUT-17 | Supplies warning | Food or water ≤ warn_turns, or at 0 | turns left, turns until mutiny | TBD | Warning | Q-19 | Ans Q-19; Claude | Proposed |
| OUT-18 | Mutiny | Food or water at 0 for mutiny_turns_at_zero turns | ship lost; moved to <ship> or demoted to a Kestrel; supplies given; fear change | TBD | Alert | Q-19 | Chat A11, A19 | Agreed |

### Behaviour

| ID | Aspect | Current thinking | Example | Edge cases | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| OB-01 | Verbosity | Normal by default; 'verbose' shows AI captains' events elsewhere in the galaxy. |  |  |  |  | Claude | Proposed |
| OB-02 | History | 500 lines; grouped by phase within a turn. |  |  |  | Q-31 | Claude | Proposed |
| OB-03 | Styling | Colour per severity; same palette as T2 / T4. |  |  |  |  | Chat A8 | Agreed |
| OB-04 | Order within a turn | Grouped by phase (movement → hazards → combat → trade → upkeep → reputation). |  |  |  | Q-01 | Ans Q-01 | Proposed |
| OB-05 | Game log files | The engine writes logs/game-<seed>.log (every event, untruncated, plus one line per AI decision with its reason and rule ID) and logs/turns-<seed>.jsonl (one JSON line per resolved turn in the sim-report shape). [logging] ai_trace = sector \| battle \| all \| off limits the AI lines (default sector: AI in the player's sector, plus every battle and roam). Never read by the game. | T3 c-163 Elin Kestle: in battle b-001, weaker (strength 220 v 245) -> no peace; fires torpedo+phaser at s-000 (CM-10, D-125) | Unbounded files; one per seed. | Built as M3 step 2 (D-135); filter key arrives with M4. |  | Chat A29 | Agreed |

---

## 5b T4 Status

**5b · T4 Status terminal**

*The permanent status panel agreed in Q-25*

- **Purpose** — Always-visible state so T3 can stay a pure log: captain, active ship, fleet, cargo, supplies. · Ans Q-25
- **Process / module** — TBD — e.g. mos/terminals/status.py · Template
- **Subscribes** — turn.resolved · 2 Architecture
- **Publishes** — nothing (display only) · Claude · Proposed
- **Render library** — Rich panels / tables, colour-coded bars · Chat A8

### Panels

| ID | Panel | Shows | Updates when | Position / size | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| PNL-01 | Captain | name, gold, silver, fear (0–100 bar), respect (0–100 bar), eligibility for the ultimate item | turn.resolved | top |  | Q-15 | Ans Q-15, Q-25 | Agreed |
| PNL-02 | Active ship | name, class, torpedoes (count), phasers (0–10), shields current / max, engine (0–10), warp core (0–10), position | turn.resolved | middle |  | Q-12 | Ans Q-25; Claude | Proposed |
| PNL-03 | Supplies | food, water (turns remaining), colour-coded warning, mutiny countdown when at 0 | turn.resolved | middle |  | Q-19 | Ans Q-19; Chat A11 | Proposed |
| PNL-04 | Cargo | elements & compounds held, special items | turn.resolved | lower |  |  | Implied r83 | Proposed |
| PNL-05 | Fleet | every owned ship: name, location, shields, orders queued | turn.resolved | bottom |  | Q-13 | Ans Q-13 | Proposed |
| PNL-06 | Hazard / battle | current pull, countdown, enemies in range | turn.resolved | side | Only when relevant | Q-02, Q-12 | Claude | Proposed |
| PNL-07 | Orders | the active ship's queued orders in order (command and arguments), including a persisting move with its remaining distance; Fleet (PNL-05) keeps the per-ship count | turn.resolved, and on every accepted or cancelled order | middle | T1's 'orders' lists the same | Q-01 | Chat A21 | Agreed |

---

## 6 World Grids

**6 · World grids**

*Universe → Galaxy → Sector, sparse storage, seeded generation*

### Grid hierarchy

| ID | Level | Is a grid of | Each cell holds | Size (M × N) | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| WG-00 | GeneralGrid | — (base class) | Sparse: a dict keyed by (x, y) → cell contents; empty = Void. Bounds M × N are properties. | n/a | Q-04 | BP r30–32; Ans Q-04 | Agreed |
| WG-01 | Universe | Galaxies | A Galaxy or Void. | 8 × 8 (config) | Q-05 | BP r34–36; Claude | Proposed |
| WG-02 | Galaxy | Sectors | A Sector or Void. | 16 × 16 (config) | Q-05 | BP r38–40; Claude | Proposed |
| WG-03 | Sector | Cells | At most one terrain object (star, black hole, neutron star, planet) per cell; any number of items and ships. | 24 × 24 (config) | Q-06 | BP r42–44; Ans Q-06 | Agreed |

### Generation rules

| ID | Rule | Statement | Notes | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| SP-01 | Seed | Everything is generated from one seed; the same seed gives the same universe. | Wormhole pairs fixed at seeding. | [world] seed | Q-08 | Ans Q-08, Q-11 | Agreed |
| SP-02 | Star systems | One star per cell; binary / triple systems are stars in adjacent cells. |  | [world] multi_star_pct | Q-06 | Ans Q-06 | Agreed |
| SP-03 | Item density | About 10 special items and 100 normal items per galaxy. |  | [world] special_items_per_galaxy = 10, normal_items_per_galaxy = 100 | Q-07 | Ans Q-07 | Agreed |
| SP-04 | Planets | Planets sit at a Chebyshev distance planet_distance_min … planet_distance_max from their star (both config; 1 … 3 by default, so some systems are compact and some spread); 0–3 per system. A planet's cell is never inside another star's cell. |  | [world] planets_per_system_max = 3, planet_distance_min = 1, planet_distance_max = 3 |  | Claude; Chat A24 | Agreed |
| SP-05 | Starting position | Player starts in a sector with a planet and no black hole or neutron star. |  | [world] safe_start = true | Q-08 | Claude | Proposed |
| SP-06 | AI captains | 3 AI captains per galaxy, placed in random safe sectors. |  | [world] ai_captains_per_galaxy = 3 | Q-13 | Claude | Proposed |
| SP-07 | Sector density | Per sector: 0–3 stars, 0–1 black hole or neutron star (never with the player's start), items per SP-03. |  | [world] * |  | Claude | Proposed |
| SP-08 | Ultimate item | Placed at eligibility time in a sector at least half the universe diameter from the captain, guarded by ships. |  | [endgame] guard_ships = 4 | Q-15 | Ans Q-15 | Agreed |

---

## 7 Stellar

**7 · Stellar objects**

*Types, attributes, and the energy → temperature → colour chain with a live calculator*

### Types

| ID | Type | Attributes | Notes | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| ST-01 | Star | radius, base_energy, mass | Pull and shield drain in its own cell only. Fatal if the ship stays. |  |  | BP r49, r51; Ans Q-02 | Agreed |
| ST-02 | Neutron star | mass (lower than a black hole), radius | Like a black hole but weaker pull: pulls across the sector; fatal if pulled in; no wormhole; no shield drain. | [stellar] neutron_mass | Q-09 | Ans Q-09; Claude | Proposed |
| ST-03 | Black hole | mass (very high), radius, wormhole (bool) | Pulls across the whole sector; a ship may lack the engine power to escape (8 Hazards calculator). | [stellar] blackhole_mass |  | BP r60–62; Ans Q-02 | Agreed |
| ST-04 | Wormhole | Black hole with a fixed partner (set at seeding) | Exit can be in the same or another galaxy; the ship survives the crossing; pulled in again if it stays. |  | Q-11 | Ans Q-11 | Agreed |

### Derived quantities

| ID | Quantity | Formula | Notes | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| DQ-01 | Volume | volume = 4/3 · π · radius³ |  |  | Q-10 | Ans Q-10 | Agreed |
| DQ-02 | Energy | energy = base_energy × volume |  |  | Q-10 | BP r57; Ans Q-10 | Agreed |
| DQ-03 | Temperature | temperature = temp_scale × energy^0.25 | Stefan–Boltzmann flavour; temp_scale = 2 700 gives a Sun-like star ≈ 5 800 K. | [stellar] temp_scale = 2700 | Q-10 | Claude | Proposed |
| DQ-04 | Colour | colour = lookup(temperature, colour thresholds) | Real stellar sequence: red → orange → yellow → yellow-white → white → blue-white → blue. | [stellar] colour_thresholds | Q-10 | Claude | Proposed |
| DQ-05 | Pull | pull = k_g × mass / (1 + distance)^falloff  (star: same cell only, distance = 0) | Nets against impulse and warp speed (8 Hazards). | [hazards] k_g = 1.0, falloff = 1.5 | Q-02 | Ans Q-02; Claude | Proposed |

### Star energy buckets

| ID | Bucket | Energy range | Example | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| EB-01 | Low | energy < 5 | Dying star — radius 0.3, base 2 → energy ≈ 0.2, ≈ 1 900 K, red | [stellar] energy_low_max = 5 | Q-10 | Claude | Proposed |
| EB-02 | Medium | 5 ≤ energy < 100 | Sun-like — radius 1, base 5 → energy ≈ 21, ≈ 5 800 K, yellow | [stellar] energy_medium_max = 100 | Q-10 | Claude | Proposed |
| EB-03 | High | energy ≥ 100 | Giant — radius 3, base 8 → energy ≈ 905, ≈ 14 800 K, blue-white | — | Q-10 | Claude | Proposed |

Calculator — change the yellow inputs and the rows below recompute

Inputs are placeholders; tune in playtest.

- **temp_scale** — 2700 · [stellar] temp_scale
- **energy_low_max** — 5 · [stellar] energy_low_max
- **energy_medium_max** — 100 · [stellar] energy_medium_max

### Colour thresholds (K)

- **0** — red
- **3700** — orange
- **5200** — yellow
- **6000** — yellow-white
- **7500** — white
- **10000** — blue-white
- **30000** — blue
- **Example star** — radius · base_energy · volume · energy · bucket · temperature K · colour
- **dying star** — 0.3 · 2 · 0.1131 · 0.2262 · low · 1862 · red
- **Sun-like** — 1 · 5 · 4.189 · 20.94 · medium · 5776 · yellow
- **giant** — 3 · 8 · 113.1 · 904.8 · high · 1.481e+04 · blue-white
- **your own** — 1.5 · 6 · 14.14 · 84.82 · medium · 8194 · white

---

## 8 Hazards

**8 · Hazard rules**

*Gravity, shield drain, black holes, wormholes — with a pull calculator*

### Rules

| ID | Object | Trigger | Effect | Magnitude | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| HZ-01 | Star | Ship 1+ cells away | No effect. | 0 |  |  | BP r65 | Agreed |
| HZ-02 | Star | Ship in the same cell | Pull nets against speed; if the ship does not get out, it is pulled in after N turns and destroyed. | N = ceil(pull_turns_scale / pull); Sun-like ≈ 7 turns | [hazards] pull_turns_scale = 7 | Q-02 | BP r66–67; Ans Q-02 | Agreed |
| HZ-03 | Star | Ship in the same cell | Shields drain each turn by the star's bucket. | low 5 · medium 10 · high 20 per turn (of 100) | [hazards] drain_low = 5, drain_medium = 10, drain_high = 20 | Q-03 | Ans Q-03; Claude | Proposed |
| HZ-04 | Black hole | Ship anywhere in the sector | Pull nets against speed at every distance; a ship whose engine ≤ pull cannot move away. Pulled in and destroyed after N turns if it does not get out. | see calculator | [stellar] blackhole_mass = 20 | Q-02 | BP r70, r72; Ans Q-02 | Agreed |
| HZ-05 | Wormhole | Ship pulled into a wormhole black hole | Emerges alive at the free cell nearest to wormhole_exit_distance cells from the partner hole (same or other galaxy), countdown reset, inside the partner's pull; pulled in again if it does not get out. At the default 2 a Kestrel (engine 4) is held and a Corsair (engine 6) pulls away — the exit distance decides which engines escape (see the calculator). |  | [hazards] wormhole_exit_distance = 2 | Q-11 | BP r71–72; Ans Q-11; Chat A25–A28 (distance: Claude's 2, agreed) | Agreed |
| HZ-06 | Neutron star | Ship anywhere in the sector | As a black hole with lower mass; no shield drain; no wormhole. | see calculator | [stellar] neutron_mass = 8 | Q-09 | Ans Q-09; Claude | Proposed |
| HZ-07 | Planet | Ship in the same cell | No hazard; trading and landing happen here. |  |  | Q-21 | Claude | Proposed |
| HZ-08 | Countdown reset | Ship leaves the pull range (star: the cell; hole: the sector) | Countdown resets to N. |  |  | Q-02 | Claude | Proposed |
| HZ-09 | Zero shields | Shields reach 0 (from drain or combat) | The next hit of any kind destroys the ship. |  |  | Q-03 | Ans Q-03 | Agreed |
| HZ-10 | Warp inside pull | Warping out of a hole's sector | Warp factor is netted by pull too: effective = factor − pull; if ≤ 0 the warp fails. |  |  | Q-02 | Claude | Proposed |

Pull calculator — yellow inputs; distance is in sector cells from the hole

All inputs are placeholders — the 7-turn Sun-like star is only the blueprint's example. Tune in playtest (Chat A16).

- **k_g** — 1 · [hazards] k_g
- **falloff** — 1.5 · [hazards] falloff exponent
- **blackhole_mass** — 20 · [stellar] blackhole_mass
- **neutron_mass** — 8 · [stellar] neutron_mass
- **sun_mass** — 1 · a Sun-like star's mass
- **pull_turns_scale** — 7 · [hazards] pull_turns_scale
- **distance** — black hole pull · min engine to move · turns to pulled in · neutron pull · min engine to move · turns to pulled in
- **0** — 20 · impossible · 1 · 8 · 8.1 · 1
- **1** — 7.071 · 7.1 · 1 · 2.828 · 2.9 · 3
- **2** — 3.849 · 3.9 · 2 · 1.54 · 1.6 · 5
- **3** — 2.5 · 2.6 · 3 · 1 · 1.1 · 7
- **4** — 1.789 · 1.8 · 4 · 0.7155 · 0.8 · 10
- **5** — 1.361 · 1.4 · 6 · 0.5443 · 0.6 · 13
- **6** — 1.08 · 1.1 · 7 · 0.432 · 0.5 · 17
- **7** — 0.8839 · 0.9 · 8 · 0.3536 · 0.4 · 20
- **8** — 0.7407 · 0.8 · 10 · 0.2963 · 0.4 · 24
- **9** — 0.6325 · 0.7 · 12 · 0.253 · 0.3 · 28
- **10** — 0.5482 · 0.6 · 13 · 0.2193 · 0.3 · 32
- **11** — 0.4811 · 0.5 · 15 · 0.1925 · 0.3 · 37
- **12** — 0.4267 · 0.5 · 17 · 0.1707 · 0.2 · 42
- **Sun-like star, same cell:** — 1 · 7 · ← pull, turns until pulled in (should read 7 with the defaults)

---

## 9 Ships

**9 · Ships**

*Ship class, movement rules with the warp curve calculator, ship classes*

Ship class — Ships = {torpedos, phasers, shield_power, engine_power, warpcore_power, captain}

| ID | Attribute | Type | Range / meaning | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| SH-01 | torpedos | int (ammo count) | 0 … ∞; each hit does torpedo_damage (15; was 25) | Bought from planets or looted. | Q-12 | BP r75; Claude | Proposed |
| SH-02 | phasers | float 0–10 | Damage per exchange = phasers × phaser_damage_per_unit (5), falling with distance | Never runs out. | Q-12 | BP r75; Claude | Proposed |
| SH-03 | shield_max / shield_current | float 0–100 | Current absorbs all damage; at 0 the next hit destroys the ship. Recharges shield_recharge (5) per turn outside hazards and battle. | shield_max is what upgrades raise; not looted (CB-01). | Q-03, Q-12 | BP r75, r68, r106; Ans Q-03; Claude | Proposed |
| SH-04 | engine_power | float 0–10 | Max impulse speed; cells per turn = floor(speed − pull). |  | Q-02 | BP r76; Ans Q-02 | Agreed |
| SH-05 | warpcore_power | float 0–10 | Max warp factor; reach from the warp curve below; 10 = anywhere in one turn. |  | Q-02 | BP r77; Ans Q-02 | Agreed |
| SH-06 | captain | Captain | The owning captain (10 Captain). |  |  | BP r75 | Agreed |
| SH-07 | hull | — (none) | No hull stat: shields are the ship's health (Q-03). |  | Q-03 | Ans Q-03 | Agreed |
| SH-08 | name, class | str | Original names; class from the table below. |  |  | Template | Needs detail |
| SH-09 | position | (galaxy xy, sector xy, cell xy) |  |  |  | Implied r8 | Agreed |
| SH-10 | cargo | — (captain-level) | Items belong to the captain, not the ship; no cargo limit. |  | Q-14 | Ans Q-14 | Agreed |

### Movement

| ID | Mode | Speed → distance per turn | Range limit | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| MV-01 | Impulse | cells = floor(speed − pull); speed 1 → 1 cell, 2 → 2 cells; 2.5 → 2 cells. | Within the sector | Fractional engine power only matters against pull. | Q-02 | Ans Q-02; Chat A9 | Agreed |
| MV-02 | Warp | reach (sectors) = w × B^((w/10)^p); w = 10 → anywhere. B = 16, p = 4 give: 1 → 1.0, 2 → 2.0, 5 → 5.9, 8 → 25, 9 → 55, 9.9 → 142, 9.99999 → 160. | Universe diameter ≈ 8 × 16 × √2 ≈ 181 sectors with the proposed sizes — so 9.99999 is fast but not everywhere. | Distance between sectors in different galaxies = Chebyshev distance on the flattened sector grid. | Q-02 | Ans Q-02; Claude | Proposed |
| MV-03 | Pull vs warp | effective factor = w − pull at the departure cell; if ≤ 0 the warp fails. |  |  | Q-02 | Claude | Proposed |
| MV-04 | Cost | Moving costs nothing except food and water upkeep each turn. |  |  | Q-19 | Claude | Proposed |
| MV-05 | Arrival cell after warp | Nearest free cell to the sector centre. |  |  | Q-02 | Claude | Proposed |
| MV-06 | Fleet movement | Owned ships in the same cell as the active ship move with it; others hold position. |  |  | Q-13 | Claude | Proposed |

Warp curve calculator — change B and p (yellow) and the reach column recomputes

B and p are placeholders; tune in playtest.

- **B** — 16 · [movement] warp_curve_base
- **p** — 4 · [movement] warp_curve_exponent
- **warp factor** — reach (sectors) · note
- **1** — 1
- **2** — 2.009
- **3** — 3.068
- **4** — 4.294
- **5** — 5.946
- **6** — 8.594
- **7** — 13.62
- **8** — 24.91
- **9** — 55.5
- **9.5** — 90.88
- **9.9** — 142
- **10** — 160 · universe diameter ≈ 181 sectors at 8 × 16
- **10** — anywhere

### Ship classes

Proposed starting set; names are placeholders for you to replace.

| ID | Class name | Role | Base stats (torp / phaser / shield / engine / warp) | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| CLS-01 | Kestrel (starting ship) | Scout / trader | 6 / 3 / 60 / 4 / 3 | Weak but fast enough to escape |  | Claude | Proposed |
| CLS-02 | Corsair | Raider | 12 / 5 / 80 / 6 / 5 | AI captains' usual ship |  | Claude | Proposed |
| CLS-03 | Bastion | Heavy | 20 / 8 / 100 / 3 / 4 | Guards the ultimate item | Q-15 | Claude | Proposed |

---

## 10 Captain

**10 · Captain & fleet**

*Captain class, fleet rules, endgame eligibility*

Captain class — Captain = {name, gold_coins, silver_coins, items, fear_level, respect_level}

| ID | Attribute | Type | Range / meaning | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| CP-01 | name | str |  |  |  | BP r80 | Agreed |
| CP-02 | gold_coins | int | Start: 5 | 1 gold = 20 silver. | Q-14 | BP r80; Ans Q-14; Claude | Proposed |
| CP-03 | silver_coins | int | Start: 50 |  | Q-14 | BP r80; Claude | Proposed |
| CP-04 | items | List[Item] |  | Items belong to the captain, shared across the fleet. | Q-14 | BP r80; Ans Q-14 | Agreed |
| CP-05 | fear_level | int 0–100 | Start: 10 |  | Q-15 | BP r80; Ans Q-15 | Agreed |
| CP-06 | respect_level | int 0–100 | Start: 10 |  | Q-15 | BP r80; Ans Q-15 | Agreed |
| CP-07 | food, water | int | Start: 30 each; −1 each per turn | Buy from planets. At 0: mutiny (11 Items, SUP-03). | Q-19 | Ans Q-19; Chat A11 | Proposed |
| CP-08 | ships / active_ship | List[Ship] / Ship | No limit |  | Q-13 | Ans Q-13 | Agreed |
| CP-09 | is_player | bool |  | AI captains use the same class. | Q-13 | Claude | Proposed |
| CP-10 | is_master | bool | Set when the ultimate item is obtained |  | Q-37 | Ans Q-37 | Agreed |

### Fleet rules

| ID | Rule | Statement | Notes | Example | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| FL-01 | Switching | The command terminal switches the active ship; each ship has its own order queue. |  |  | Q-13 | BP r13; Claude | Proposed |
| FL-02 | Acquiring ships | Buy from a planet (price in gold) or capture: a ship whose captain accepts peace at 0 shields surrenders it. |  |  | Q-13 | Claude; Chat A26 | Agreed |
| FL-03 | Inactive ships | Hold position unless in the active ship's cell (then they move with it). |  |  | Q-13 | Claude | Proposed |
| FL-04 | Game over | Game over when the player's active ship is destroyed — the captain is on it. Other owned ships do not save the run. |  |  | Q-29 | Ans Q-29; Chat A10 | Agreed |
| FL-05 | Fleet in battle | All ships in range fight under the same rules; strength for fear / respect is the fleet total. |  |  | Q-13, Q-22 | Ans Q-13, Q-22 | Agreed |

### Endgame eligibility

| ID | Threshold | Value | Notes | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| EG-01 | Wealth | ≥ 100 gold-equivalent (gold × 20 + silver ≥ 2 000 silver) | Tuned to ~70–100 turns (Q-35). | [endgame] wealth_min_silver = 2000 | Q-15 | Ans Q-15; Claude | Proposed |
| EG-02 | Respect | ≥ 90 |  | [endgame] respect_min = 90 | Q-15 | Ans Q-15 | Agreed |
| EG-03 | Fear | ≥ 90 |  | [endgame] fear_min = 90 | Q-15 | Ans Q-15 | Agreed |
| EG-04 | Required items | Hold at least one special item of each category (11 Items). |  | [endgame] required_special_categories = 3 | Q-15 | Claude | Proposed |
| EG-05 | Ultimate item — name | Lodestone of Starbour (recommended; lodestone = the navigator's stone, fits the harbour pun). Alternatives: Crystal of Starbour, Starbour Keelstone. |  |  | Q-26 | Ans Q-26; Claude | Proposed |
| EG-06 | Ultimate item — where | Placed when eligible, ≥ half the universe diameter away, in a sector guarded by guard_ships Bastion-class ships that must be beaten. |  | [endgame] guard_ships = 4 | Q-15 | Ans Q-15 | Agreed |
| EG-07 | Mastery — effect | Weapons, shields, engine, warp = max; wealth infinite; fear and respect = 100; all planets willing to trade. Play continues. |  |  | Q-37 | Chat A4; Ans Q-37 | Agreed |
| EG-08 | Mastery — ceremony | Fireworks animation and big ASCII-art title on T3 (full colour); T4 shows the title permanently. |  |  | Q-37 | Ans Q-37 | Agreed |

---

## 11 Items & Planets

**11 · Items, elements & planets**

*Item class, element catalogue (real + mythical names), planet class and rules*

Items — Items = {food, water, element_compounds}

| ID | Item | Type | Purpose / use | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| IT-01 | food | int | Consumed −1 per turn by crew and captain; bought from planets. | At 0: mutiny (SUP-03). | Q-19 | Ans Q-19 | Agreed |
| IT-02 | water | int | As food. |  | Q-19 | Ans Q-19 | Agreed |
| IT-03 | element_compounds | List[Element] | Trade goods; some upgrade ship components. |  | Q-14 | BP r83, r91 | Agreed |
| IT-04 | Pickup | rule | Enter the cell → scan → choose to pick up. Never automatic. |  | Q-07 | Ans Q-07 | Agreed |
| IT-05 | Rarity | rule | ≈ 10 special and 100 normal items per galaxy; no respawn. |  | Q-07 | Ans Q-07; Claude | Proposed |

### Supplies & mutiny

Your rule: at zero food or water the crew mutiny and the captain is demoted to a low-class ship. The mechanics around it are proposals.

| ID | Rule | Statement | Notes | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| SUP-01 | Consumption | Food and water each fall by 1 per turn. |  |  | Q-19 | Ans Q-19 | Agreed |
| SUP-02 | Warning | T3 / T4 warn when either is ≤ warn_turns; at 0 the mutiny countdown shows. |  | [supplies] warn_turns = 5 | Q-19 | Claude | Proposed |
| SUP-03 | Mutiny | After mutiny_turns_at_zero consecutive turns with food or water at 0, the crew mutiny and take the active ship. | Not game over. | [supplies] mutiny_turns_at_zero = 3 | Q-19 | Chat A11 | Agreed |
| SUP-04 | With a fleet | If the captain owns other ships, the captain moves to one of them (the nearest — Proposed detail) and carries on. |  |  | Q-19 | Chat A19; Claude (nearest) | Agreed |
| SUP-04b | Without a fleet | If no other ship is available, the captain is demoted: a Kestrel-class ship with mutiny_supplies food and water appears at the nearest cell of the same sector that is outside every hazard's pull range (the same cell when that cell is safe); coins and items kept; fear −mutiny_fear_delta. The mutineers keep the old ship where it is. |  | [supplies] mutiny_ship_class = Kestrel, mutiny_supplies = 10, mutiny_fear_delta = 10 | Q-19 | Chat A11, A19, A28; Claude (values, placement) | Agreed |
| SUP-04c | The mutineers | They keep the taken ship and become a new AI captain. |  |  | Q-19 | Chat A18 | Agreed |
| SUP-05 | Mastery | A Master of Starbour has infinite wealth, so supplies never run out. |  |  | Q-37 | Claude | Proposed |

Element catalogue — read from controls_config.ini [elements]

Names: real minerals, metals and gems plus mythical ones; no franchise names. Every row is one config line: name = category, effect, value, rarity. Values are placeholders.

| ID | Name | Category | Effect when used | Rarity / value (silver) | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| EL-01 | Iridium | Precious metal | Torpedoes +4 | normal · 30 | Q-14, Q-20 | Claude | Proposed |
| EL-02 | Osmium | Precious metal | Phasers +0.5 | normal · 40 | Q-14, Q-20 | Claude | Proposed |
| EL-03 | Rhodium | Precious metal | Shields max +5 | normal · 40 | Q-14, Q-20 | Claude | Proposed |
| EL-04 | Sapphire | Exotic crystal | Shields max +10 | normal · 60 | Q-14, Q-20 | Claude | Proposed |
| EL-05 | Moonstone | Exotic crystal | Engine +0.5 | normal · 60 | Q-14, Q-20 | Claude | Proposed |
| EL-06 | Sunstone | Exotic crystal | Phasers +1 | normal · 80 | Q-14, Q-20 | Claude | Proposed |
| EL-07 | Antihydrogen | Antimatter | Warp core +0.5 | normal · 100 | Q-14, Q-20 | Claude | Proposed |
| EL-08 | Positronium | Antimatter | Torpedoes +10 | normal · 100 | Q-14, Q-20 | Claude | Proposed |
| EL-09 | Orichalcum | Mythical (special) | Shields max +25 | special · 400 | Q-14, Q-20 | Claude | Proposed |
| EL-10 | Adamantine | Mythical (special) | Phasers +2 | special · 400 | Q-14, Q-20 | Claude | Proposed |
| EL-11 | Aetherite | Mythical (special) | Warp core +1.5 | special · 500 | Q-14, Q-20 | Claude | Proposed |
| EL-12 | Star-iron | Mythical (special) | Engine +1.5 | special · 500 | Q-14, Q-20 | Claude | Proposed |

Planet class — Planet = {name, food, water, element_compounds, shields, torpedos, phasers, prezident_name}

| ID | Attribute | Type | Range / meaning | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| PL-01 | name | str |  | Original names. |  | BP r87 | Agreed |
| PL-02 | food, water | int | Stock; regenerates +5 per turn up to a cap |  | Q-21 | BP r87; Claude | Proposed |
| PL-03 | element_compounds | List[Element] | Stock for sale; no regeneration |  | Q-21 | BP r87; Claude | Proposed |
| PL-04 | shields, torpedos, phasers | as ships | Planets fight with the same rules; they cannot move or escape. |  | Q-21 | BP r87; Ans Q-21 | Agreed |
| PL-05 | prezident_name | str | Head of the planet; trade counterparty; decides to attack if the captain's respect is low. |  | Q-21, Q-24 | BP r87, r90; Ans Q-21 | Agreed |
| PL-06 | disposition | int −100…100 | Derived from the captain's respect (14 Fear & Respect, effects table). |  | Q-24 | Chat A13; Claude | Proposed |

### Planet rules

| ID | Rule | Statement | Notes | Example | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| PR-01 | Trade | The captain trades items and wealth with the planet's head; buy / sell at the planet's prices. |  |  | Q-14 | BP r90; Ans Q-14 | Agreed |
| PR-02 | Attack | Planets can be attacked and can attack (e.g. a hostile president). Destroyed planet → all its resources are looted. Peace → X% to the victor (13 Combat CB-03). |  |  | Q-21 | Ans Q-21 | Agreed |
| PR-03 | Stock regeneration | Food and water regenerate; elements do not. |  |  | Q-21 | Claude | Proposed |
| PR-04 | Reach | The ship must be in the planet's cell to trade. |  |  | Q-14 | Claude | Proposed |
| PR-05 | Disposition | Planets respond to respect: below 20 they refuse trade and may attack; prices improve as respect rises; after mastery every planet trades. |  |  | Q-24 | Chat A13; Claude | Proposed |

---

## 12 Trade & Economy

**12 · Trade & economy**

*Currencies, trading, upgrades, wealth sources and sinks*

### Rules

| ID | Rule | Statement | Notes | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| TR-01 | Currencies | Gold and silver coins; 1 gold = 20 silver; gold is rarer. |  | [economy] silver_per_gold = 20 | Q-14 | Ans Q-14 | Agreed |
| TR-02 | Trade partners | Other captains and planet heads. |  |  |  | BP r90 | Agreed |
| TR-03 | Tradeable things | Items (food, water, elements) and coins. |  |  |  | BP r90 | Agreed |
| TR-04 | Buying | Items are bought with gold / silver at a planet's prices. |  |  | Q-14 | Ans Q-14 | Agreed |
| TR-05 | Upgrades | Elements improve torpedoes, phasers, shields, engine, warp. The map (which element → which component, by how much) lives in controls_config.ini [upgrades]; the game reads it, never hard-codes it. |  | [upgrades] * | Q-14 | BP r91; Chat A17 | Agreed |
| TR-06 | Valuation | A planet accepts a trade if what it receives is worth ≥ what it gives at list prices, adjusted by disposition (±20%). AI captains the same, ±10%. |  | [economy] * | Q-14, Q-24 | Claude | Proposed |
| TR-07 | Wealth sources (money in) | Loot from destroyed ships / planets, peace settlements, selling items to planets. | 'Wealth sources' = where money comes from. |  | Q-14 | Ans Q-14; Claude | Proposed |
| TR-08 | Wealth sinks (money out) | Food and water every few turns, torpedoes, elements, buying ships. Without sinks money only piles up and the endgame threshold is just a timer. | 'Wealth sinks' = things you must spend on. |  | Q-14 | Claude | Proposed |
| TR-09 | Respect from trade | Any accepted trade → respect +; acquiring a special item → respect +. |  | [respect] trade_accepted_delta, special_item_delta |  | BP r102–103 | Agreed |
| TR-10 | After mastery | Every planet trades (and at base prices). |  |  | Q-37 | Ans Q-37 | Agreed |

Upgrade map — which element improves which component

Agreed as a configurable table: the structure is fixed, every number is read from controls_config.ini [upgrades]. Values here are placeholders. Caps keep every stat ≤ its max.

| ID | Component | Improving elements | Effect per unit | Cap | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| UP-01 | Torpedoes | Iridium +4 · Positronium +10 | ammo count | none | Q-14 | Chat A17; Claude (values) | Agreed |
| UP-02 | Phasers | Osmium +0.5 · Sunstone +1 · Adamantine +2 | phaser power | 10 | Q-14 | Chat A17; Claude (values) | Agreed |
| UP-03 | Shields (max) | Rhodium +5 · Sapphire +10 · Orichalcum +25 | shield_max | 100 | Q-14 | Chat A17; Claude (values) | Agreed |
| UP-04 | Engine | Moonstone +0.5 · Star-iron +1.5 | engine_power | 10 | Q-14 | Chat A17; Claude (values) | Agreed |
| UP-05 | Warp core | Antihydrogen +0.5 · Aetherite +1.5 | warpcore_power | 10 | Q-14 | Chat A17; Claude (values) | Agreed |

Price list (silver) — read from controls_config.ini [prices]

Structure agreed; values are placeholders configurable outside the game (Chat A17).

| ID | Good | Buy price | Sell price (planet pays) | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|
| PRC-01 | Food (1 unit) | 2 | 1 | [prices] food_buy, food_sell | Q-14 | Chat A17; Claude (values) | Agreed |
| PRC-02 | Water (1 unit) | 2 | 1 | [prices] water_buy, water_sell | Q-14 | Chat A17; Claude (values) | Agreed |
| PRC-03 | Torpedo (1) | 10 | 5 | [prices] torpedo_buy, torpedo_sell | Q-14 | Chat A17; Claude (values) | Agreed |
| PRC-04 | Elements | value from [elements] | element_sell_pct of value (60%) | [prices] element_sell_pct | Q-14 | Chat A17; Claude (values) | Agreed |
| PRC-05 | Ship (Kestrel / Corsair) | 40 gold / 120 gold | — | [prices] ship_<class> | Q-13 | Chat A17; Claude (values) | Agreed |

---

## 13 Combat

**13 · Combat**

*The recommended combat model (Q-12) and the outcomes from the blueprint*

### Outcomes

| ID | Outcome | Condition | Transfer to winner | Notes | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| CB-01 | Destroy | Target's shields at 0 and it takes a hit | All items and coins; torpedoes added; phasers, engine and warp = max(own, victim's), capped at 10; shields not transferred. | max() rather than adding stops stats racing past 10 (your Q-12 point). |  | Q-12 | BP r106; Claude | Proposed |
| CB-02 | Escape | Escaper ends the movement phase outside every enemy's weapon range | Nothing. | Deterministic — depends on engine vs ranges and pull. |  | Q-12 | BP r107; Claude | Proposed |
| CB-03 | Peace | One side offers, the other accepts | Victor = the side that did NOT offer. X = max(0, (V_shields − L_shields) / V_shields) × peace_loot_max; X% of the loser's items and coins go to the victor. | If the victor's shields are lower, X = 0 — peace with nothing transferred. | [battle] peace_loot_max = 50 | Q-12, Q-21 | BP r108; Ans Q-21; Chat A12 | Agreed |
| CB-04 | Player loses | Player's active ship destroyed | — game over; start again. |  |  | Q-29 | Ans Q-29; Chat A10 | Agreed |

### Combat model — recommendation

One exchange per turn; battle = being in weapon range. Alternatives and failure modes in the notes.

| ID | Aspect | Recommendation | Why | Alternative / how it fails | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| CM-01 | Starting a fight | Any 'fire' order on a target not already in battle with you starts one; costs respect; asks for confirmation. |  |  | [respect] start_fight_delta | Q-23 | BP r100; Ans Q-34 | Agreed |
| CM-02 | Range | Phasers 2 cells, torpedoes 5 cells (Chebyshev). Being inside any enemy's range = in battle. | Gives escape a meaning without dice. | [ALTERNATIVE] same-cell only — too binary with 24 × 24 sectors. | [battle] phaser_range = 2, torpedo_range = 5 | Q-12 | Claude | Proposed |
| CM-03 | Torpedoes | Ammo; each fired torpedo hits with p = 0.7 − 0.03 × target engine; damage torpedo_damage (15 after the M3 playtest; was 25). | Fast ships dodge. | [RISK] randomness in a deterministic game — keep the seed in the RNG so replays match. | [battle] torpedo_hit_base = 0.7, torpedo_evade_per_engine = 0.03, torpedo_damage = 25 | Q-12 | Claude; Chat A26, A32 | Agreed |
| CM-04 | Phasers | Always hit; damage = phasers × 5 × (1 − distance / 3). | Reliable close-range weapon. |  | [battle] phaser_damage_per_unit = 5 | Q-12 | Claude | Proposed |
| CM-05 | Shields | Absorb all damage; at 0 the next hit destroys (Q-03). Recharge 5 / turn outside battle and hazards. | Recharge happens anywhere outside hazards and battle (no repair yards exist); the rate is config. |  | [battle] shield_recharge = 5 | Q-03 | Ans Q-03; Claude; Chat A23, A26 | Agreed |
| CM-06 | Exchange order | Per turn: all torpedoes resolve, then phasers, simultaneously for both sides; destruction checked after. | Both sides can die in the same exchange — rare, dramatic, fair. |  |  | Q-01 | Claude | Proposed |
| CM-07 | Rounds per turn | One exchange per turn; a battle spans several turns. | Gives time to escape or sue for peace — matches 'battle every 5–7 turns' pacing. | [ALTERNATIVE] resolve to the end in one turn — no decisions mid-battle. |  | Q-12 | Claude | Proposed |
| CM-08 | Opponent strength | strength = Σ over the fleet of (shield_current + torpedoes × torpedo_damage + phasers × phaser_damage_per_unit × 3). Stronger = strength ratio > 1.0. | Shields and weapons, fleet included (your Q-22). |  |  | Q-22 | Ans Q-22; Claude | Proposed |
| CM-09 | Peace offers | Either side may offer each turn; the other side's next order accepts or rejects; rejecting costs nothing. |  |  |  | Q-12 | Claude | Proposed |
| CM-10 | AI behaviour | Attack if strength ratio ≥ 1.3 and the target's fear < 50; offer peace when own shields < 30% and weaker; try to escape when shields < 15%. |  | [VERIFY] in playtest — this sets how often battles happen. | [ai] * | Q-13, Q-24 | Claude | Proposed |
| CM-11 | Planets in combat | Same rules; a planet cannot move or escape; destroyed planet is looted and removed. |  |  |  | Q-21 | Ans Q-21 | Agreed |
| CM-12 | Fleet in combat | Every ship in range fights; the player gives each ship its own orders. |  |  |  | Q-13 | Ans Q-13 | Agreed |
| CM-13 | Guardians | The ultimate item's sector holds guard_ships Bastion-class ships that attack on entry. |  |  | [endgame] guard_ships = 4 | Q-15 | Ans Q-15 | Agreed |

---

## 14 Fear & Respect

**14 · Fear & respect**

*Events, levels, and what the levels change in the world*

### Events

Levels are ints 0–100. Base deltas are config keys, scaled by the strength ratio (LV-03).

| ID | Event | Condition | Fear Δ | Respect Δ | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| FR-01 | Battle won | Any opponent | +8 | — | [fear] battle_won_delta = 8 | Q-35 | BP r94; Claude | Proposed |
| FR-02 | Battle lost | AI captains only (a player loss is game over) | −8 | — | [fear] battle_lost_delta = 8 | Q-29 | BP r95; Ans Q-29 | Agreed |
| FR-03 | Battle won vs stronger | strength ratio < 1 | (FR-01) | +10 | [respect] win_vs_stronger_delta = 10 | Q-22 | BP r98; Claude | Proposed |
| FR-04 | Battle won vs weaker who offered peace | ratio > 1 and the loser offered peace | (FR-01) | −8 | [respect] win_vs_weaker_peace_delta = 8 | Q-22 | BP r99; Claude | Proposed |
| FR-05 | Battle won vs weaker, player started it | ratio > 1 and the player fired first | (FR-01) | −8 | [respect] win_vs_weaker_started_delta = 8 | Q-23 | BP r99; Claude | Proposed |
| FR-06 | Starting a fight | Player fires first | — | −5 | [respect] start_fight_delta = 5 | Q-23 | BP r100; Claude | Proposed |
| FR-07 | Trade accepted | Any accepted trade | — | +3 | [respect] trade_accepted_delta = 3 | Q-35 | BP r102; Claude | Proposed |
| FR-08 | Special item acquired | Special category | — | +6 | [respect] special_item_delta = 6 | Q-35 | BP r103; Claude | Proposed |
| FR-09 | Stacking | FR-05 and FR-06 both apply when the player starts and wins against a weaker opponent. |  |  |  | Q-23 | Ans Q-23 | Agreed |
| FR-10 | No extra events, no decay | Losing to a weaker opponent, mercy, escaping: no effect. Levels do not drift. |  |  |  | Q-36 | Ans Q-36 | Agreed |

### Level mechanics

| ID | Aspect | Current thinking | Example | Edge cases | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| LV-01 | Range | int 0–100 for both. |  |  |  | Q-15 | Ans Q-15 | Agreed |
| LV-02 | Starting values | Fear 10, respect 30. |  | Respect 30 agreed 2026-10-31 (D-152, option A): at 10 with EF-04 refusal at 20 a new captain cannot buy food (the only respect without trade is FR-03 / FR-08). Fear 10 is still a placeholder. | [fear] start = 10, [respect] start = 30 | Q-35 | Claude; Chat A33 | Agreed |
| LV-03 | Scaling | delta × clamp(strength ratio of the opponent vs you, 0.5, 2.0) — beating a much stronger fleet counts double. |  |  | [reputation] scale_min = 0.5, scale_max = 2.0 | Q-22 | BP r111; Claude | Proposed |
| LV-04 | Visibility | Other captains' levels are visible when in the same sector (via scan). |  |  |  | Q-36 | Ans Q-36 | Agreed |
| LV-05 | Tuning | All deltas are placeholders. No target turn count — tune by playtest until the game feels right. |  |  |  | Q-35 | Chat A15 | Agreed |

### Effects in the world

Split agreed (Chat A13): FEAR acts on ships — AI captains yield. RESPECT acts on planets — they comply. Thresholds are placeholders.

| ID | Who | Fear effect | Respect effect | Notes | Config key | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|
| EF-00 | The split | Fear → ships yield. | Respect → planets comply. |  |  | Q-24 | Chat A13 | Agreed |
| EF-01 | AI captains — attacking | Attack the player only if the player's fear < attack_fear_max, or they are ≥ attack_ratio_min stronger. | — |  | [ai] attack_fear_max = 50, attack_ratio_min = 1.3 | Q-24 | Claude | Proposed |
| EF-02 | AI captains — yielding | Offer peace earlier as fear rises (shield threshold 30% at fear 0 → 60% at fear 100); at fear ≥ yield_fear_min a weaker captain surrenders the ship outright when its shields drop below 20%. | Accept the player's peace offers more readily as respect rises. | Surrender = the player gains the ship (FL-02). | [ai] peace_shield_pct (at fear 0) = 30, peace_shield_pct_at_fear100 = 60, yield_fear_min = 80, surrender_shield_pct = 20 | Q-24 | Claude; Chat A26 | Agreed |
| EF-03 | AI captains — trade | — | Accept trades at slightly worse terms for themselves as respect rises (±10%). | Minor; delete if you want respect to be planets-only. | [economy] respect_price_band = 10 | Q-24 | Claude | Proposed |
| EF-04 | Planets — trade | — | Refuse trade below planet_refuse_respect; prices improve up to planet_price_band % at respect 100. | Refusal threshold 20 confirmed 2026-10-31 (D-152); the price band stays Proposed. Refusal precedes fire: EF-05 fires only below 10. | [economy] planet_refuse_respect = 20, planet_price_band = 20 | Q-24 | Chat A13; Claude | Proposed |
| EF-05 | Planets — attacking | — | May attack if respect < planet_attack_respect_max and they are stronger. | 10, not 20 (D-152): 'refused' (EF-04, below 20) and 'shot at' were one tier. From the start of 30 each started fight is −5 (FR-06) and each bullying win −8 more (FR-04/05); two bullying wins reach 4 and every stronger planet within phaser range opens fire. Build: a planet opens only within phaser range, then fights at full range (kit D-150). | [ai] planet_attack_respect_max = 10 | Q-24 | Claude; Chat A33 | Agreed |
| EF-06 | After mastery | Both at 100: every ship yields; every planet trades. |  |  |  | Q-37 | Ans Q-37 | Agreed |

---

## 15 Config Keys

**15 · controls_config.ini**

*Every tunable, one row per key. Proposed defaults are Claude's; the build kit generates the .ini from this tab.*

### Keys

Structure agreed; every numeric value is a placeholder you can change outside the game (Chat A17). [upgrades], [prices] and [elements] are data sections the game reads, never hard-codes.

| ID | Section | Key | Type | Default | Unit / range | Meaning | Used by | Open Q | Origin | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| CFG-01 | [fear] | battle_won_delta | int | 8 |  | Fear gained for a battle won. | FR-01 | Q-35 | Claude | Proposed |
| CFG-02 | [fear] | battle_lost_delta | int | 8 |  | Fear lost for a battle lost (AI only). | FR-02 | Q-29 | Claude | Proposed |
| CFG-03 | [fear] | start | int | 10 | 0–100 | Starting fear. | LV-02 | Q-35 | Claude | Proposed |
| CFG-04 | [respect] | win_vs_stronger_delta | int | 10 |  | Respect gained for beating a stronger opponent. | FR-03 | Q-22 | Claude | Proposed |
| CFG-05 | [respect] | win_vs_weaker_peace_delta | int | 8 |  | Respect lost for beating a weaker opponent who offered peace. | FR-04 | Q-22 | Claude | Proposed |
| CFG-06 | [respect] | win_vs_weaker_started_delta | int | 8 |  | Respect lost for beating a weaker opponent in a fight you started. | FR-05 | Q-23 | Claude | Proposed |
| CFG-07 | [respect] | start_fight_delta | int | 5 |  | Respect lost for starting any fight. | FR-06 | Q-23 | Claude | Proposed |
| CFG-08 | [respect] | trade_accepted_delta | int | 3 |  | Respect gained per accepted trade. | FR-07 | Q-35 | Claude | Proposed |
| CFG-09 | [respect] | special_item_delta | int | 6 |  | Respect gained per special item. | FR-08 | Q-35 | Claude | Proposed |
| CFG-10 | [respect] | start | int | 30 | 0–100 | Starting respect. 30 since M4 (D-152; was 10). | LV-02 | Q-35 | Claude; Chat A33 | Agreed |
| CFG-11 | [reputation] | scale_min, scale_max | float | 0.5, 2.0 |  | Clamp on the strength-ratio multiplier. | LV-03 | Q-22 | Claude | Proposed |
| CFG-12 | [stellar] | temp_scale | float | 2700 | K | temperature = temp_scale × energy^0.25. | DQ-03 | Q-10 | Claude | Proposed |
| CFG-13 | [stellar] | energy_low_max | float | 5 |  | Energy below this = low bucket. | EB-01 | Q-10 | Claude | Proposed |
| CFG-14 | [stellar] | energy_medium_max | float | 100 |  | Energy below this = medium; above = high. | EB-02 | Q-10 | Claude | Proposed |
| CFG-15 | [stellar] | colour_thresholds | list | 3700,5200,6000,7500,10000,30000 | K | red \| orange \| yellow \| yellow-white \| white \| blue-white \| blue. | DQ-04, SYM-04 | Q-10 | Claude | Proposed |
| CFG-16 | [stellar] | blackhole_mass | float | 20 |  | Pull at distance d = k_g × mass / (1 + d)^falloff. | HZ-04 | Q-02 | Claude | Proposed |
| CFG-17 | [stellar] | neutron_mass | float | 8 |  | As above, weaker. | HZ-06 | Q-09 | Claude | Proposed |
| CFG-18 | [hazards] | k_g | float | 1 |  | Gravity constant. | DQ-05 | Q-02 | Claude | Proposed |
| CFG-19 | [hazards] | falloff | float | 1.5 |  | Pull falloff exponent with distance. | DQ-05 | Q-02 | Claude | Proposed |
| CFG-20 | [hazards] | pull_turns_scale | int | 7 | turns | Turns until pulled in = ceil(scale / pull). Sun-like star = 7. | HZ-02 | Q-02 | Ans Q-02 | Agreed |
| CFG-21 | [hazards] | drain_low, drain_medium, drain_high | int | 5, 10, 20 | shield / turn | Shield drain in a star's cell by bucket. | HZ-03 | Q-03 | Claude | Proposed |
| CFG-22 | [movement] | impulse_max, warp_max | float | 10 |  | Upper bound for engine and warp core. | SH-04, SH-05 | Q-02 | Ans Q-02 | Agreed |
| CFG-23 | [movement] | warp_curve_base, warp_curve_exponent | float | 16, 4 |  | reach = w × base^((w/10)^exponent); w = 10 → anywhere. | MV-02 | Q-02 | Claude | Proposed |
| CFG-24 | [battle] | phaser_range, torpedo_range | int | 2, 5 | cells | Weapon ranges. | CM-02 | Q-12 | Claude | Proposed |
| CFG-25 | [battle] | torpedo_hit_base, torpedo_evade_per_engine, torpedo_damage | float | 0.7, 0.03, 15 |  | Torpedo hit chance and damage. | CM-03 | Q-12 | Claude; damage 25 → 15 after the M3 playtest (Chat A32) | Proposed |
| CFG-26 | [battle] | phaser_damage_per_unit | float | 5 |  | Phaser damage per power unit at distance 0. | CM-04 | Q-12 | Claude | Proposed |
| CFG-27 | [battle] | shield_recharge | int | 5 | / turn | Recharge outside battle and hazards. | CM-05 | Q-03 | Claude | Proposed |
| CFG-28 | [battle] | peace_loot_max | int | 50 | % | Max share transferred on peace. | CB-03 | Q-12 | Claude | Proposed |
| CFG-29 | [ai] | attack_ratio_min, attack_fear_max | float | 1.3, 50 |  | When AI captains attack. | CM-10, EF-01 | Q-24 | Claude | Proposed |
| CFG-30 | [ai] | peace_shield_pct, escape_shield_pct | int | 30, 15 | % | When AI offers peace / tries to escape. | CM-10 | Q-24 | Claude | Proposed |
| CFG-31 | [ai] | planet_attack_respect_max | int | 10 |  | Stronger planets open fire below this respect (EF-05). Below the refusal threshold (CFG-43, 20) so refusal precedes fire. 10 since M4 (D-152; was 20). | EF-05 | Q-24 | Claude; Chat A33 | Agreed |
| CFG-32 | [economy] | silver_per_gold | int | 20 |  | 1 gold = 20 silver. | TR-01 | Q-14 | Ans Q-14 | Agreed |
| CFG-33 | [upgrades] | torpedoes | list | iridium:4, positronium:10 | ammo | Elements that add torpedoes, and how many. | UP-01 | Q-14 | Chat A17; Claude (values) | Agreed |
| CFG-34 | [upgrades] | phasers | list | osmium:0.5, sunstone:1, adamantine:2 | power | Elements that raise phaser power (cap 10). | UP-02 | Q-14 | Chat A17; Claude (values) | Agreed |
| CFG-35 | [upgrades] | shields | list | rhodium:5, sapphire:10, orichalcum:25 | shield_max | Elements that raise max shields (cap 100). | UP-03 | Q-14 | Chat A17; Claude (values) | Agreed |
| CFG-36 | [upgrades] | engine | list | moonstone:0.5, star_iron:1.5 | power | Elements that raise engine power (cap 10). | UP-04 | Q-14 | Chat A17; Claude (values) | Agreed |
| CFG-37 | [upgrades] | warp | list | antihydrogen:0.5, aetherite:1.5 | power | Elements that raise warp core power (cap 10). | UP-05 | Q-14 | Chat A17; Claude (values) | Agreed |
| CFG-38 | [prices] | food_buy, food_sell, water_buy, water_sell | int | 2, 1, 2, 1 | silver | Planet prices for supplies. | PRC-01, PRC-02 | Q-14 | Chat A17; Claude (values) | Agreed |
| CFG-39 | [prices] | torpedo_buy, torpedo_sell | int | 10, 5 | silver | Planet prices for torpedoes. | PRC-03 | Q-14 | Chat A17; Claude (values) | Agreed |
| CFG-40 | [prices] | element_sell_pct | int | 60 | % | What a planet pays for an element, as % of its value. | PRC-04 | Q-14 | Chat A17; Claude (values) | Agreed |
| CFG-41 | [prices] | ship_kestrel, ship_corsair | int | 40, 120 | gold | Ship purchase prices. | PRC-05 | Q-13 | Chat A17; Claude (values) | Agreed |
| CFG-42 | [elements] | <name> = category, value, rarity | list | see tab 11 |  | One line per element in the catalogue; the game never hard-codes elements. | EL-01…12 | Q-14, Q-20 | Chat A17; Claude (values) | Agreed |
| CFG-43 | [economy] | planet_refuse_respect, planet_price_band, respect_price_band | int | 20, 20, 10 | % | Trade effects of respect. planet_refuse_respect 20 confirmed 2026-10-31 (D-152); the two bands stay Proposed. | EF-03, EF-04 | Q-24 | Claude | Proposed |
| CFG-44 | [ai] | yield_fear_min | int | 80 |  | Fear at which weaker AI captains surrender their ship. | EF-02 | Q-24 | Claude | Proposed |
| CFG-45 | [supplies] | food_start, water_start | int | 30, 30 |  | Starting supplies; −1 each per turn. | CP-07 | Q-19 | Claude | Proposed |
| CFG-46 | [supplies] | warn_turns | int | 5 | turns | Warn when food or water is at or below this. | SUP-02 | Q-19 | Claude | Proposed |
| CFG-47 | [supplies] | mutiny_turns_at_zero | int | 3 | turns | Consecutive turns at 0 before the crew mutiny. | SUP-03 | Q-19 | Claude | Proposed |
| CFG-48 | [supplies] | mutiny_ship_class, mutiny_supplies, mutiny_fear_delta | mixed | Kestrel, 10, 10 |  | What the demoted captain gets, and the fear hit. | SUP-04 | Q-19 | Claude | Proposed |
| CFG-49 | [endgame] | respect_min, fear_min | int | 90, 90 |  | Eligibility thresholds. | EG-02, EG-03 | Q-15 | Ans Q-15 | Agreed |
| CFG-50 | [endgame] | wealth_min_silver | int | 2000 | silver-equiv. | Eligibility wealth threshold. | EG-01 | Q-15 | Claude | Proposed |
| CFG-51 | [endgame] | required_special_categories | int | 3 |  | Special items needed (one per category). | EG-04 | Q-15 | Claude | Proposed |
| CFG-52 | [endgame] | guard_ships | int | 4 |  | Guardians at the ultimate item. | EG-06, CM-13 | Q-15 | Claude | Proposed |
| CFG-53 | [world] | seed | int | random |  | World-generation seed; shown on T4 so a run can be repeated. | SP-01 | Q-08 | Claude | Proposed |
| CFG-54 | [world] | universe_size, galaxy_size, sector_size | M×N | 8×8, 16×16, 24×24 | cells | Grid dimensions. | VW-01…03 | Q-05 | Claude | Proposed |
| CFG-55 | [world] | fog_of_war | bool | on |  | On: sector contents hidden until visited / scanned. | GRD-04 | Q-30 | Ans Q-30 | Agreed |
| CFG-56 | [world] | special_items_per_galaxy, normal_items_per_galaxy | int | 10, 100 |  | Item density. | SP-03 | Q-07 | Ans Q-07 | Agreed |
| CFG-57 | [world] | ai_captains_per_galaxy | int | 3 |  | AI captains generated per galaxy. | SP-06 | Q-13 | Claude | Proposed |
| CFG-58 | [world] | safe_start | bool | true |  | Start sector has a planet and no hole. | SP-05 | Q-08 | Claude | Proposed |
| CFG-59 | [bus] | pub_port, pull_port | int | 5555, 5556 |  | ZeroMQ localhost ports. | BUS-03 | Q-18 | Ans Q-18 | Agreed |
| CFG-60 | [ui] | log_lines | int | 500 |  | T3 scrollback. | OB-02 | Q-31 | Claude | Proposed |
| CFG-61 | [world] | planet_distance_min, planet_distance_max | int | 1, 3 | cells | Chebyshev distance band from a planet to its star. | SP-04 |  | Chat A24 | Agreed |
| CFG-62 | [hazards] | wormhole_exit_distance | int | 2 | cells | Distance from the partner hole at which a ship emerges from a wormhole; decides which engines can pull away. | HZ-05 | Q-11 | Claude; Chat A28 | Agreed |
| CFG-63 | [world] | galaxy_fill_pct, sector_fill_pct | int | 80, 80 | % | Share of universe cells holding a galaxy and of galaxy cells holding a sector; the rest are Void. | WG-01, WG-02 | Q-05 | Claude (D-71) | Proposed |
| CFG-64 | [world] | multi_star_pct | int | 20 | % | Share of star systems that are binary or triple (stars in adjacent cells). | SP-02 | Q-06 | Claude (D-71) | Proposed |
| CFG-65 | [world] | planets_per_system_max | int | 3 |  | Planets per star system, 0 … max. | SP-04 |  | Claude (D-71) | Proposed |
| CFG-66 | [world] | hole_pct, neutron_pct, wormhole_pct | int | 15, 50, 30 | % | Share of sectors with a hole; share of holes that are neutron stars; share of black holes paired as wormholes. | SP-07, ST-02…04 | Q-09, Q-11 | Claude (D-89) | Proposed |
| CFG-67 | [stellar] | radius_min, radius_max, base_energy_min, base_energy_max | float | 0.3, 3.0, 1, 9 |  | Uniform draws for generated stars (the tab 7 examples span 0.3 … 3 and 2 … 8). | ST-01, DQ-01, DQ-02 | Q-10 | Claude (D-89) | Proposed |
| CFG-68 | [stellar] | colour_names | list | red, orange, yellow, yellow-white, white, blue-white, blue |  | Colour names, one more than colour_thresholds. | DQ-04, SYM-04 | Q-10 | Claude (D-71) | Proposed |
| CFG-69 | [ui] | glyph_set | str | unicode | unicode \| ascii | T2 glyph set; ascii for consoles whose font lacks ★ ◎ ▲. | SYM-01…14 |  | Claude (D-68) | Proposed |
| CFG-70 | [ships] | kestrel, corsair, bastion, starting_class | list | 6,3,60,4,3 · 12,5,80,6,5 · 20,8,100,3,4 · kestrel | torp, phaser, shield, engine, warp | Ship classes and the player's starting class. | CLS-01…03 |  | Claude (D-71) | Proposed |
| CFG-71 | [captain] | gold_start, silver_start | int | 5, 50 | coins | Starting wealth. | CP-02, CP-03 | Q-14 | Claude (D-71) | Proposed |
| CFG-72 | [ai] | peace_shield_pct_at_fear100, surrender_shield_pct | int | 60, 20 | % | AI offers peace below a shield % that rises with the player's fear (CFG-30 at fear 0 → this at fear 100); at fear ≥ yield_fear_min a weaker AI surrenders its ship below surrender_shield_pct. | EF-02 | Q-24 | Claude | Proposed |
| CFG-73 | [ai] | roam_every_turns | int | 10 | turns | An AI captain warps to a random reachable sector this often; 0 = never. | OV-10, D-103 | Q-13 | Claude | Proposed |
| CFG-74 | [logging] | ai_trace | str | sector | sector \| battle \| all \| off | Which AI decision lines go to logs/game-<seed>.log. | OB-05 |  | Claude | Proposed |
| CFG-75 | [world] | ai_class_mix | list | corsair:60, kestrel:25, bastion:15 | % weights | Class mix of generated AI captains; with one class no AI ever opens on another (ratios 1.0). | SP-06, CLS-01…03, D-131 | Q-13 | Claude (D-131) | Proposed |
| CFG-76 | [battle] | phaser_max | float | 10 |  | Cap on phaser power (upgrades and loot). | SH-02, UP-02, CB-01, D-130 | Q-12 | Claude (D-130) | Proposed |
| CFG-77 | [planets] | shield_max, torpedoes, phasers | mixed | 80, 10, 4 |  | Every planet's defence (PL-04: planets fight with the ship rules; cannot move or escape). | PL-04, CM-11, PR-02 | Q-21 | Claude (D-140) | Proposed |
| CFG-78 | [planets] | food_stock_start, water_stock_start, stock_regen, stock_cap | int | 100, 100, 5, 200 | units | Planet supplies stock, regeneration per turn and cap. | PL-02, PR-03 | Q-21 | Claude (D-140) | Proposed |
| CFG-79 | [planets] | element_stacks_min, element_stacks_max, stack_units_min, stack_units_max | int | 2, 4, 1, 5 |  | How many element stacks a planet holds for sale at generation and how many units each; no regeneration. | PL-03, PR-03 | Q-21 | Claude (D-140) | Proposed |
| CFG-80 | [world] | item_units_min, item_units_max | int | 1, 3 | units | Units in an element stack lying in space (SP-03 items). | SP-03, IT-03, IT-05 | Q-07 | Claude (D-141) | Proposed |
| CFG-81 | [ai] | buy_supplies_below, docking_income_silver | int | 10, 10 | units, silver/turn | An AI captain heads for a planet and restocks when food or water falls below the first; the second is a placeholder income while docked at a planet that will trade with it, so the AI economy neither starves nor explodes — tune by the population gate. | OV-10, D-60, SUP-03, D-144 | Q-13, Q-19 | Claude (D-144) | Proposed |
| CFG-82 | [battle] | shield_cap | float | 100 |  | Cap on shield_max from upgrades and loot (SH-03, UP-03). | SH-03, UP-03 | Q-14 | Claude (D-143) | Proposed |
| CFG-83 | [ai] | respect_start | int | 40 | 0–100 | Starting respect of AI captains: generated (SP-06), mutineers (SUP-04c) and --spawn captains without --respect. The player uses [respect] start (CFG-10). A balance knob for the AI economy loop (D-144), not a design value. | LV-02, SP-06, SUP-04c, D-144, D-149 | Q-24 | Claude (D-149) | Proposed |

---

## 16 Open Questions

**16 · Open questions**

*Your answers (column F, verbatim) and how each was folded in (column G). Agreed = done; Needs detail = a follow-up question is in chat.*

| ID | Area | Question (short) | Options offered | Your answer (verbatim) | Resolution — where it went | Status |
|---|---|---|---|---|---|---|
| Q-01 | Turns | How do orders resolve; immediate vs queued; conflicting orders? | A) Simultaneous, fixed phase order [ASSUMED]  B) Sequential | player just executes a turn. So your option A I think works. | A agreed → OV-02, TP-01…08. Immediate / queued split and 'same-kind order replaces' are Proposed (OV-02b). | Agreed |
| Q-02 | Movement & hazards | Impulse → cells? Warp → reach? Countdown reset? Fatal? | Countdown: A) resets on leaving [ASSUMED]  B) persists | lets say impulse of 1 gives 1 sector cell at a time, impulse of 2 gives 2 sector cells at a time. <br><br>With warp 1, one galaxy cell at a time, then 2 at a time for warp 2. <br><br>note: these are float values so we can have e.g. warp 9.2 potentially, so will need to be calculated exponentially as such since warp 10 can be anywhere in the universe in one turn. 9.99999 is very fast and can do many turns at once but no where near warp 10.<br><br>the grids will need to be somewhat big i think. <br><br>the gravitational pull factor will net against the speed. <br><br>you will need to come up with calculations to begin with i feel, and maybe put them in this spreadsheet.<br><br>Falling into a star is fatal, shields get drained, and if they dont escape and impulse or warp out in time, they will get destroyed. Same with a black hole (but less time to move and need much more engine power, a ship may have not enough power to enter in a black hole sector and escape | Folded into MV-01…03, HZ-02/04/08/10, DQ-05 + calculators on 8 Hazards and 9 Ships. Fractional impulse = floor (chat A9). | Agreed |
| Q-03 | Shield drain | Drain per turn? What at 0 shields? |  | with 0 shields, they get destroyed with one hit I imagine.<br><br>Drain per turn, we should come up with a config of numbers that we can set in the config ini | HZ-09, SH-07, CM-05; drain numbers Proposed in HZ-03 / CFG. | Agreed |
| Q-04 | Grid storage | Dense or sparse? | A) Dense [ASSUMED]  B) Sparse | I would say sparse. Every sector here and there may have items to acquire by the ship, exotic and special items even rare | Sparse → WG-00. | Agreed |
| Q-05 | Grid sizes & coordinates | M × N per level? Coordinate format? |  | so in the grid terminal we can switch between views: current sector, current galaxy, universe. Ill let you figure out dimensions, but this should be configurable | Sizes Proposed and configurable (VW-01…03, CFG). Coordinates Proposed (GRD-01). | Agreed |
| Q-06 | Cell occupancy | Several things per cell? Binary systems? | A) One terrain object + ships [ASSUMED]  B) Composite | one star per cell (not in one cell) they can be close are far apart in the sector. Items can be in the same cell, along with ships. Just the stars need to be in separate cells | One terrain object per cell; items and ships share → WG-03, SP-02, SYM-13/14. | Agreed |
| Q-07 | Special items | Types? Rarity? Respawn? Pickup? |  | special items have to be picked up by choice i.e. you enter a sector cell with a special item, you use scanners to detect what it is, then a choice to pick it up. Types ill let you come up with by looking at star trek and star wars types of material. Yes special items are rare, id say in the whole galaxy there are probably 10 special items, and in the whole galaxy 100 normal items | Scan → choose → pickup (CMD-14/15, IT-04); 10 special / 100 normal per galaxy (SP-03); types Proposed (EL-01…12). | Agreed |
| Q-08 | World generation | Seeded? Density? Safe start? | A) Seeded, safe start [ASSUMED]  B) Fixed map | Seeded | Seeded → SP-01. Safe start Proposed (SP-05). | Agreed |
| Q-09 | Neutron star | Attributes and behaviour? |  | like a black hole but less gravitational pull | Black-hole-like, weaker → ST-02, HZ-06, calculator. | Agreed |
| Q-10 | Physics | Radius or volume? f, g, h? Thresholds? |  | Energy by volume (volume determined by radius). You determine the thresholds which can be configurable | Volume → DQ-01/02. temp_scale, thresholds, colours Proposed with a live calculator on 7 Stellar. | Agreed |
| Q-11 | Wormholes | Fixed pairs? Same galaxy? Survivable? |  | fixed pairs set at seeding! Can end up in same galaxy or different galaxy. Ship can survive when it crosses through. | Fixed pairs at seeding, any galaxy, survivable → ST-04, HZ-05. | Agreed |
| Q-12 | Combat model | Range, weapons, destruction, rounds, loot, peace X%? |  | come up with a solid recommendation here please | Full recommendation on 13 Combat (CM-01…13, CB-01…03), all Proposed. Peace formula → chat question 4. | Agreed |
| Q-13 | Fleet & AI | Inactive ships? Limit? AI captains? Fleet battles? |  | yes multiple ships can be in play in a battle, there can be a fleet. No limit I reckon, if multiple ships engage they they all engage in the same battle rules. Hope that is clear - let me know if not | No limit; fleets fight as one → OV-08, FL-05, CM-12. Inactive-ship and AI-count defaults Proposed (FL-03, SP-06). | Agreed |
| Q-14 | Economy | Gold / silver? Prices? Sinks? Upgrade map? Items per captain or ship? |  | you can buy items with gold/silver. One gold coin is worth 20 silver coins. So gold is rarer than silver. Items are per captain. <br><br>What do you mean by wealth sinks? Upgrade map? | 1 gold = 20 silver; buy items; items per captain → TR-01/04, SH-10, CP-04. Upgrade map, prices and the element catalogue are config sections (chat A17) → tab 12, CFG [upgrades] [prices] [elements]. | Agreed |
| Q-15 | Endgame thresholds | Thresholds; required items; the ultimate item? |  | respect levels and fear levels are int. when reached to e.g. 90% on both, and enough gold and silver (you determine what values) then they are eligible to find the ultimate secret item which will be guardedi in a sector in a galaxy surrounded by ships which need to be battled. the location of the ultimate secret item can be set somewhere far from current location. | Ints, 90 thresholds, wealth threshold Proposed, guarded far-away sector → EG-01…06, SP-08, CM-13. | Agreed |
| Q-16 | Win / lose | Does the game end? Lose condition? |  | Answered 2026-10-25. WIN: the game does not end; becoming Master of Starbour triggers a celebration and ceremony, then play carries on (D-07). LOSE: losing a battle loses the game and the player starts again (D-09). Definition of 'losing' → Q-29. | Closed earlier (D-07, D-09). | Agreed |
| Q-17 | Grid zoom | 'view' command, auto-follow, both? | A) Auto-follow + 'view' override [ASSUMED] | Agreed | A → GRD-02, CMD-05. | Agreed |
| Q-18 | Bus & engine | IPC? Separate engine process? | A) Engine process + ZeroMQ [ASSUMED]  B) In-process queues | Agreed | A → BUS-03/04, T0. | Agreed |
| Q-19 | Food & water | What are they for? |  | yes crew and captain consumption which is depleted every turn -1, so its important for the captain to purchase food and water when possible | −1 per turn, buy from planets → IT-01/02, CP-07. At 0: mutiny (A11); move to another owned ship if any, else demotion (A19); mutineers become an AI captain (A18) → SUP-01…05, OUT-18. | Agreed |
| Q-20 | Crystal naming | Original name for the powerful crystal? |  | use real crystal names and mythical crystal names | Real + mythical names → EL-01…12 Proposed. | Agreed |
| Q-21 | Planets | Attackable? Conquest? Regeneration? |  | yes planets can be attacked and vice versa, e.g. if a planet prezident does not like the captain, or if the captain gets greedy or has good reason to battle, if a planet gets destroyed in battle then can take all resources, or a peace would ensure x% goes to the victor (i.e. the one who does not sought for peace) - hope there is no contradiction here, perhaps you can come up with a better suggestion otherwise | Attackable both ways; destroyed = looted; peace X% to the side that did not sue → PR-02, CM-11, CB-03 (formula agreed, chat A12). | Agreed |
| Q-22 | Opponent strength | How is stronger / weaker computed? |  | stronger / weaker is determined by shields and weapons strength, fleet included yes since they act as one unit against the other | Shields + weapons, fleet included → CM-08 (formula Proposed). | Agreed |
| Q-23 | Respect stacking | Do FR-05 and FR-06 both apply? | A) Both  B) Larger only | option A | A → FR-09. | Agreed |
| Q-24 | Effects of fear & respect | What do the levels change? |  | all changes I imagine. Challenge me here and be clear what youre asking | Split agreed in chat (A13): fear → ships yield, respect → planets comply → EF-00…06 (thresholds Proposed). | Agreed |
| Q-25 | Fourth terminal | Three or four terminals? |  | What is the purpose of the permanent status? Makes sense to show this, agreed | T4 Status agreed → 5b T4 Status tab, T4 row. | Agreed |
| Q-26 | 'Starbour' | Universe name, title, or both? | A) Universe + title [ASSUMED]  B) Title only | no Starbour is a play on words of Harbour / Starshp. Master of Starbour means they win the ultimate secret item (need to come up with a good name like Crystal of Starbour or something like that, which will give the ultimate levels of weapons, shields, engine, warp, etc.) | Pun, not a place; title comes with the ultimate item → OV-13; names Proposed (EG-05). | Agreed |
| Q-27 | Licence | Which licence for the public repo? |  | No license, perhaps we create one | MIT — you asked Claude to choose (chat A20) → BR-04, D-50. | Agreed |
| Q-28 | Packaging | PyInstaller mode, installer, signing, CI, file locations? | A) One-folder zip on GitHub Releases, unsigned, config next to exe [ASSUMED] | option A | A → BR-14…20. | Agreed |
| Q-29 | What counts as losing | Destroyed only? Escape / peace? Active or last ship? New seed? Reload? | A) Active ship destroyed; new seed; no reload [ASSUMED]  B) Last ship  C) Any defeat | only when ship is destroyed game is over. | Destroyed only; the active ship (chat A10) → OV-09b, CB-04, FL-04. | Agreed |
| Q-30 | Fog of war | Visible from the start or revealed? | A) Sector hidden until visited [ASSUMED]  B) All visible  C) All hidden | could we have an option which is configurable to do A or B? | Configurable A / B → GRD-04, CFG fog_of_war. | Agreed |
| Q-31 | Refresh model | Live or once per turn? | A) Once per turn.resolved [ASSUMED]  B) Live | Resolved oncer per turn resolved | Once per turn → BUS-05, GRD-06. | Agreed |
| Q-32 | Launcher lifecycle | Who opens / closes the terminals? | A) Launcher owns children [ASSUMED] | option A | A → TL, BUS-06. | Agreed |
| Q-33 | Save / load | Slots? Autosave? Reload after loss? | A) Autosave, one slot, deleted on game over [ASSUMED]  B) Manual | option A | A → OV-12, OV-09c, CMD-19. | Agreed |
| Q-34 | Errors & confirmations | Where do errors appear? What confirms? | A) Inline on T1 + T3 echo; confirm fights and quit [ASSUMED] | option A | A → UX-03/04, OUT-13. | Agreed |
| Q-35 | Pacing | Turns to mastery? Battle frequency? |  | Battle can happen on average say 5-7 turns, can be longer or shorter. Mastery is achieved once the ultimate secret item is obtained, that could take a long time, e.g. 100 turns maybe longer, maybe a bit shorter say 70 turns | Rough feel only — no fixed turn counts (chat A15). All numbers are placeholders tuned by playtest → OV-11, LV-05. | Agreed |
| Q-36 | Fear & respect — extra events | Mercy, escape, decay, visibility? | A) None extra, no decay, visible in sector [ASSUMED] | option A | A → FR-10, LV-04. | Agreed |
| Q-37 | Mastery ceremony | What does it look like? What changes after? |  | something fancy with nice terminal graphics of fireworks and big smart art words. Nothing changes afterwards except respect and fear are maximum levels along with weapons, shields, etc. <br><br>Remember ships and planets will be more compliant with these levels so nothing really changes - hope that makes sense | Fireworks + ASCII title; nothing else changes → OUT-15, EG-08, EF-06. | Agreed |
| Q-38 | Combat | Pursuit: 88 % of AI-vs-AI battles end by escape because the weaker side flees at 15 % shields and nobody follows (CM-10 has no pursuit rule). Should the stronger side pursue? | A) No pursuit — battles are skirmishes  B) Stronger side moves toward a fleeing enemy at full power while it has torpedoes [Claude's recommendation]  C) Pursue only when the fleeing captain's fear is low |  | Open after the M3 playtest (2026-10-30). | Needs detail |

---

## 18 Build & Release

**18 · Build & release**

*Project identity, dev environment, packaging and distribution, build-kit files*

### Project identity

| ID | Item | Value / current thinking | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|
| BR-01 | Game name | Master of Starbour |  | Q-26 | Chat A1 | Agreed |
| BR-02 | Repository | git@github.com:selphaware/master-of-starbour.git | Public. HTTPS for the README: https://github.com/selphaware/master-of-starbour |  | Chat A2, A7 | Agreed |
| BR-03 | Repo contents | Public. Source code and the installable package. |  |  | Chat A3 | Agreed |
| BR-04 | Licence | MIT. Chosen by Claude at your request: anyone may use, modify, redistribute and sell, with attribution and no warranty. LICENSE file at the repo root, copyright holder Usman Ahmad. | Not legal advice. | Q-27 | Chat A14, A20; Claude | Agreed |
| BR-05 | Package / import name | mos | Short; matches the venv name. |  | Claude | Proposed |
| BR-23 | Git access | SSH for all git operations; scripts and CLAUDE.md use the SSH remote. |  |  | Chat A7 | Agreed |

### Development environment

| ID | Item | Value / current thinking | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|
| BR-06 | Project root | E:\CODE\master-of-starbour | Confirmed. |  | Chat A6 | Agreed |
| BR-07 | Virtual environment | MOS — E:\CODE\master-of-starbour\MOS |  |  | Chat A5 | Agreed |
| BR-08 | Python executable | E:\CODE\master-of-starbour\MOS\Scripts\python.exe |  |  | Chat A5 | Agreed |
| BR-09 | Python version | 3.12 |  |  | Project | Agreed |
| BR-10 | Dependencies | rich, prompt_toolkit, pyzmq, pytest, pyinstaller (pyproject.toml). |  | Q-18 | Ans Q-18; Claude | Proposed |
| BR-11 | Test runner | pytest |  |  | Project | Agreed |
| BR-12 | Build machine | Your Windows workstation (option A); CI later if wanted. |  | Q-28 | Ans Q-28 | Agreed |

### Packaging & distribution

| ID | Item | Value / current thinking | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|
| BR-13 | Target | Downloadable executable package for Windows 10/11. |  |  | Chat A3; Project | Agreed |
| BR-14 | Packaging tool | PyInstaller, one-folder build. |  | Q-28 | Ans Q-28 | Agreed |
| BR-15 | Multi-process launch | mos.exe (launcher) spawns engine + four terminals as new console windows, each 'mos.exe --role <engine\|command\|grid\|output\|status>'. | Build gates: 'exe opens and exits', 'orphaned processes'. | Q-32 | Ans Q-32; Claude | Proposed |
| BR-16 | Installer format | Zip of the one-folder build. |  | Q-28 | Ans Q-28 | Agreed |
| BR-17 | Release channel | GitHub Releases on the repo. |  | Q-28 | Ans Q-28 | Agreed |
| BR-18 | Versioning | Semver; git tag per release; version shown on T4. |  |  | Claude | Proposed |
| BR-19 | Code signing | Unsigned at first — downloaders will see a SmartScreen warning; document it in the README. |  | Q-28 | Ans Q-28 | Agreed |
| BR-20 | controls_config.ini location | Next to the exe. |  | Q-28 | Ans Q-28 | Agreed |
| BR-21 | Save-game location | Next to the exe (saves/). |  | Q-33 | Ans Q-33; Claude | Proposed |
| BR-22 | Public README | How to play, SmartScreen note, how to build from source, MIT licence line. |  | Q-27 | Claude | Proposed |

Build-kit files this Project will produce

Status = whether the file exists yet.

| ID | File | Purpose | Notes | Open Q | Origin | Status |
|---|---|---|---|---|---|---|
| KIT-01 | DESIGN.md / PRD | Design document generated from this workbook. | Exists: docs/DESIGN.md (generated) — repo commit 2bc85af or earlier. |  | Project | Agreed |
| KIT-02 | BOOTSTRAP.md | First prompt for Claude Code. | Exists: docs/BOOTSTRAP.md — repo commit 2bc85af or earlier. |  | Project | Agreed |
| KIT-03 | CLAUDE.md | Standing instructions in the repo. | Exists: CLAUDE.md — repo commit 2bc85af or earlier. |  | Project | Agreed |
| KIT-04 | SETUP.md | Python 3.12, venv MOS, dependencies. | Exists: docs/SETUP.md — repo commit 2bc85af or earlier. |  | Project | Agreed |
| KIT-05 | DECISIONS.md | Exported from 17 Decisions. | Exists: docs/DECISIONS.md (generated + kit rows) — repo commit 2bc85af or earlier. |  | Project | Agreed |
| KIT-06 | controls_config.ini | Generated from 15 Config Keys. | Exists: controls_config.ini — repo commit 2bc85af or earlier. |  | BP r111 | Agreed |
| KIT-07 | Milestone specs | Milestone specs: M1 engine + bus + grids (done) · M2 movement + hazards (done) · M3 ship combat + reputation + fleets · M4 planets + trade + items + upgrades · M5 endgame + mastery · M6 packaging. Six, not five: planets and the endgame each earned their own gate set. | specs/M1.md, specs/M2.md exist; specs/M3.md next. |  | Claude | Proposed |
| KIT-08 | build.ps1 / launch.ps1 | Build the exe; launch the processes. | launch.ps1 and launch.bat exist; build.ps1 arrives with packaging (M6). |  | Project | Proposed |
| KIT-10 | LICENSE | MIT licence text with the copyright line. | Exists: LICENSE — repo commit 2bc85af or earlier. | Q-27 | Chat A20 | Agreed |
| KIT-09 | Gates / harness | Turns advance; AI acts; viewports fresh; no orphans; exe runs; game over fires on destruction; mutiny fires at zero supplies; mastery fires. Includes an AI-only run-to-completion check (robustness, not a pacing target). | Exists: verify/ (smoke_test, live_test, hook_gate) — repo commit 2bc85af or earlier. |  | Project; Chat A15 | Agreed |

---

## Source v1

**Source v1 — your blueprint, verbatim**

*Do not edit. 'BP rNN' = row NN below. 'Chat An' = addendum rows. 'Ans Q-xx' = your answer on tab 16.*

| Row | Text |
|---|---|
| 1 | Game Type |
| 3 | Text based game |
| 4 | Turn based game i.e. user enters a bunch of inputs (could be many inputs, think of star trek birth of the federation), then turn execution |
| 6 | Purpose |
| 8 | User/player starts off with a starship, where they can explore the universe (universe --> galaxies --> sectors --> stars/planets/ships/objects) |
| 9 | The user player is the captain of the ship they have, more details on the ship and universe later |
| 10 | Ships can battle with each other. |
| 11 | Captains can gain wealth and special items. |
| 12 | Once a certain level of wealth, respect and fear and specific items are obtained, they will be eligible to find the ultimate secret item (I need more thought on what the name can be here), which will allow them to have infinite wealth and max weapons and max shields and max engine/warpcore, then they can continue with the game with these max super powers. |
| 13 | A captain, during the game, may have more than one ship and the terminal will allow the user player to switch between their ships. |
| 15 | Terminal Based Game |
| 17 | One terminal for commands |
| 18 | One to show the grid (either at universe level, galaxy level, sector level) |
| 19 | One for output/results/info |
| 21 | I am envisioning a MQ/BUS connecting all terminals in real-time (see basic diagram on the right) \| MQ / BUS |
| 22 | i.e. |
| 23 | Grid \| Output \| Terminal |
| 26 | Classes |
| 28 | This section outlines the various objects and relationships and structures in the game |
| 30 | GeneralGrid |
| 31 | General Grid Class: something like a list of lists perhaps, or something more efficient i.e. a matrix of somesort |
| 32 | General Grid is a M x N matrix |
| 34 | Universe Grid |
| 35 | Universe = {Galaxy(s), Void(s)} |
| 36 | Each cell = either a Galaxy or Void |
| 38 | Galaxy Grid |
| 39 | Galaxy = {Sector(s), Void(s)} |
| 40 | Each cell = either a Sector or Void |
| 42 | Sector Grid |
| 43 | Sector = { Stellar Object(s), Planet(s), Ship(s), Special Items(s), Void(s) } |
| 44 | Each cell = either stellar object or planet or special item or void |
| 45 | Special items are somewhat rare and shouldn’t be expected in every sector |
| 46 | There may be binary star systems or even triple |
| 48 | Stellar Class |
| 49 | Stellar Types = {Star, Black Hole, Neutron Star} |
| 51 | Stellar Class = {radius, base_energy, mass} |
| 52 | energy (float) is bucketed in such a way where: |
| 53 | - low energy i.e. low temperature --> dying star e.g. white dwarf |
| 54 | - medium energy i.e. med temperature --> normal star e.g. our sun |
| 55 | - high energy i.e. high temperature --> big star e.g. beetleguese |
| 56 | Temperature is a function of Energy, where |
| 57 | Energy = Base Energy * Radius (or Volume) |
| 58 | Gravitational Strength is a function of mass |
| 60 | BlackHole = {mass, radius, wormhole (boolean)} |
| 61 | Mass of a black hole would be very high |
| 62 | Mass determines the gravitational strength |
| 64 | Some Examples of actions |
| 65 | If a ship is one or more grid-cell away from a star then 0 gravitational effect is applied |
| 66 | But if the ship is in the same cell as a star, then depending on gravitational strength, it will pull the ship in the star over a certain length of turn i.e. time |
| 67 | e.g. for the Sun it may take 7 turns before getting pulled in if the ship does not move |
| 68 | Also, when the ship is in the same cell as a star, shields get drained depending on energy of the star |
| 70 | For Black Holes: regardless of which cell a ship is in in the sector where there is a black hole, the ship will be pulled in (and destroyed) |
| 71 | For Wormholes: it connects to another blackhole, getting pulled in again on the otherside if the ship does not move. |
| 72 | Depending on the gravitational strength of the blackhole/wormhole, it will take a number of turns for it to be pulled in if the ship decides not to move |
| 74 | Ships |
| 75 | Ships = {torpedos, phasers, shield_power, engine-power, warpcore_power, captain: CaptainClass} |
| 76 | engine_power (float) determines max impulse speed (0-10) - float. |
| 77 | warpcore_power (float) determines max warp speed (0-10) - float. Note: At warp 10, means ship can go anywhere in the universe in one turn. |
| 79 | Captain |
| 80 | Captain = {name: str, gold_coins: int, silver_coins: int, items: List[Item], fear_level: float, respect_level: float} |
| 82 | Items |
| 83 | Items = {food: int, water: int, element_compounds: List[Elements_Compounds]} |
| 84 | Elements_Compounds = {typical precious metals (not including gold and silver), exotic crystals and metals, antimatter elements and compounds, crystals: Crystal (i.e. dilithium, other types of powerful crystals) |
| 86 | Planets |
| 87 | Planet = {name: str, food: int, water: int, element_ccompounds: List[Elements_Compounds], shields, torpedos, phasers, prezident_name: str} |
| 89 | Rules |
| 90 | The captain can trade items/wealth(gold, silver) with other captains or heads of planets |
| 91 | Certain elements and compounds can improve torpedos, phasers, shields, engine, warp |
| 93 | Fear Controls |
| 94 | Battles won (regardless of oponent level) --> fear level increases |
| 95 | Battles loss (regardless of oponent level) --> fear level decreases |
| 97 | Respect Controls |
| 98 | Battles won (against stronger opponent) --> respect increases |
| 99 | Battles won (against weaker opponent where opponent offers peace OR the fight is started by the player against the weaker opponent) --> respect decreases |
| 100 | Starting fights --> respect decreases |
| 102 | Any accepted trade --> respect increases |
| 103 | Acquiring special items (elements and compounds) --> respect increases |
| 105 | Battles |
| 106 | if A beats B and destroys B --> A acquires all items including wealth, weaponds, engine power, warp power, (not including shield power) |
| 107 | If same, but B escapes --> No transaction made |
| 108 | If same, but peace is made during battle, then A gains X% of B's items. X% based on % of net shields: A shield power - B shield power during battle |
| 110 | Control config |
| 111 | All of these controls of +/- fear and respect values should be based on various conditions (be smart about this), and base values set in a controls_config.ini (same goes for energy --> temperature settings and colour of stars intervals). Note: temperature is calculated from energy, colour is calcuated from temperature. |

Addendum — stated in chat on 2026-10-25, verbatim.

- **A1** — name has been settled to: master of starbour.
- **A2** — git repo is: git@github.com:selphaware/master-of-starbour.git.
- **A3** — idea is to make this into an executable package in the end which can be downloaded. i plan to have both code and installable on this repo (for the public).
- **A4** — end goal is to become master of starbour where you get this level of warp, weapons, etc, and you just carry on playing... all planets willing to trade... so the game doesnt end, you just carry on (that is the end)... so there will be a sort of celebration and ceremony, then carry on.
- **A5** — python venv will be in: E:\CODE\master-of-starbour\MOS\Scripts\python.exe (i.e. env name is MOS)
- **A6** — of course when you lose the battle you lose the game and start again. and yes project root you assumed is correct.
- **A7** — yes repo is public i will be doing git operations via ssh.
- **A8** — also, this will be a colourful game
- **A9** — [Fractional impulse] Agreed with default (floor; the fraction only matters against gravity pull)
- **A10** — [Game over with a fleet] Agreed with default (the active ship — the captain is on it)
- **A11** — [Food/water at zero] the crew mutiny and the captain gets demoted to a low class ship
- **A12** — [Peace formula] Agreed with default (victor = the side that didn't offer; X = victor's shield advantage as a fraction × 50%, floored at 0)
- **A13** — [Fear vs respect] yes ships can possibly yield, but with respect planets can be compliant, let me know if you need more logic details here
- **A14** — [Licence] Us MIT license then or an appropriate one?
- **A15** — [Pacing] Well i didnt like this Q in the first place, there is no set number of turns for these matters
- **A16** — [Black hole 7 turns] 7 is just an example, we have to do what is appropriate and what works
- **A17** — Agreed on the upgrade map, this should also be configurable in the ini file. hope that clears it up. Choose whatever values you deem appropriate. Point is, these numbers can be configured outside the game.
- **A18** — Agreed on mutineers.
- **A19** — if the captain has more than one ship, when mutineed then will go to one of the other ships. if no other ship available, demoted then.
- **A20** — Choose the best license for this.
- **A21** — we should show the queued orders in status.
- **A22** — when doing e.g. move +0 +10 but engine = 4 i.e. max impulse speed, it correctly moves +0 +4, but should save for the next move for next turn i.e. move +0 +6, then +0 +2... these commands should be queued. we can have a all stop command to cease further moving.
- **A23** — i noticed the shields increased, is that normal, and why? also i noticed the planets are next to the star, are they in other cells away from the star like our solar system in other sectors?
- **A24** — Agreed on: Config distance band — `[world] planet_distance_min = 1, planet_distance_max = 3`, so some systems are spread. Cheap, keeps the rule configurable (D-49). I'd recommend this if you want variety.
- **A25** — wormhole: leave distance at 1, so the user will need to pull away with impulse, but if their impulse engine is not great enough then, yes they are stuck. does that work?
- **A26** — agreed with your other clarity points
- **A27** — on the wormhole, what do you recommend, being one cell away?
- **A28** — go for it  [in answer to: wormhole exit distance 2 recommended; and the demoted captain's Kestrel appearing at the nearest cell outside any pull range]
- **A29** — [after the first M3 fight, 2026-10-30] perhaps its worth adding extensive logging to view that to see what is going on exactly
- **A30** — youre right, i didnt enter the phaser command, i just did.. end end end. so i should have fired phasers for each turn.
- **A31** — [asked: 1 standing fire orders? 2 'fire <target>' = one torpedo + phasers? 3 show the combat numbers in-game?] 1. no, every turn the user should put in the fire commands  2. yes  3. yes
- **A32** — [after a second Kestrel duel, played correctly and lost on torpedo rolls in three turns; offered torpedo_damage 25 → 15 or Kestrel shields 60 → 100] make the change yourself please
- **A33** — [after the M4 build, 2026-10-31; asked: A = accept respect start 30 and lower the planet-fire threshold to 10 so refusal (20) precedes fire, or B = keep 10 and lower both thresholds] option A
