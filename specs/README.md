# Milestones

One spec per milestone, written after the previous one is green and reviewed —
what M1 reveals about the process model shapes how M2 is specified. `MILESTONE`
holds the current one; the gates read it. Order is KIT-07 (Proposed), sharpened:

| Milestone | Adds | Gate checks added (see BOOTSTRAP § gates) | Status |
|---|---|---|---|
| **M1** engine + bus + grids | engine, ZeroMQ bus, launcher, four terminals, seeded world, impulse movement without gravity, AI random walk, supplies + mutiny, headless sim, autosave | smoke/00–12, live/01–06 | **done** 2026-10-25 (`8d90806`) |
| **M2** movement + hazards | gravity pull netting impulse and warp (MV-01…06, DQ-05), warp curve and reach (MV-02, CFG-23), star drain and countdowns (HZ-01…10), black hole / neutron star / wormhole crossing (ST-02…04, HZ-05/06), destruction by hazard → **game over** for the player (OV-09b, OUT-16, CMD-19 `new`), hazard overlay and T3/T4 messages (GRD-03, OUT-03, PNL-06) | ship in a Sun-like cell with no move → destroyed at turn N = ceil(7/pull); warp 10 reaches any sector; a ship with engine ≤ pull cannot leave a hole's sector; wormhole exit at the fixed partner; player destroyed → `game.over` published, save deleted, `new` works; countdown resets on leaving; persistent moves and `stop` (D-98), orders on T4 (D-99) | `specs/M2.md` written |
| **M3** combat + reputation | `fire`, `shields`, `peace`, `escape` (CMD-08…11), one exchange per turn (CM-01…13, CB-01…04), fleet strength (CM-08), AI attack/peace/escape/yield rules (CM-10, EF-01/02), fear and respect events and scaling (FR-01…10, LV-01…05), ship capture (FL-02), combat messages (OUT-04…06, 09, 11) | both sides can die in one exchange; 0 shields + hit = destroyed; peace transfer X per D-46 including X = 0; AI offers peace below `peace_shield_pct`; player destroyed in combat → game over; fear/respect deltas × clamp; AI-only 500-turn run still exits 0 | not started |
| **M4** trade + items + endgame | `scan` reveals items, `pickup`, `trade`, `buy`/`sell`, `upgrade` with the `[upgrades]` map and caps (CMD-12…15, 20; TR-*, UP-*, PRC-*, EL-*), planets' disposition and prices (PL-06, PR-01…05, EF-03…05), AI supplies and buying (D-60 lifted), eligibility and the guarded ultimate item (EG-01…06, SP-08, CM-13), **mastery** ceremony and after-effects (EG-07/08, OUT-15, EF-06, SUP-05) | changing a price in the ini changes the trade; upgrade caps hold; a scripted run that meets the thresholds → the item is placed ≥ half the universe away with `guard_ships` Bastions; obtaining it → `game.mastery`, stats maxed, every planet trades; AI captains buy food and do not mutiny en masse | not started |
| **M5** packaging | PyInstaller one-folder build (`build.ps1`, `starbour.spec`), `starbour.exe --role` launch (BR-14/15), config and saves next to the exe (BR-20/21), zip + GitHub Release notes, README (BR-22), SmartScreen note (BR-19), version on T4 (BR-18) | live/07: the exe launches six processes and stays up; closing T1 leaves no orphans; `controls_config.ini` next to the exe is the one read; a fresh Windows VM or second machine runs the zip `[VERIFY by Usman]` | not started |

Each milestone runs as two Claude Code sessions: **Mn-gates** (harness session —
updates `verify/` from the spec's § Gate changes, selftests, no game code; the deny
rules in `.claude/settings.json` are lifted for that session only and restored with
`git checkout -- .claude/settings.json` afterwards) and **Mn-build** (build session —
first step sets `specs/MILESTONE` to `Mn`, which switches the new gate rows from SKIP to
active). The gate table in `docs/BOOTSTRAP.md` grows with each milestone; existing
rows never weaken except where a decision says so (D-106).
