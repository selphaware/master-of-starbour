# Milestones

One spec per milestone, written after the previous one is green and reviewed —
what M1 reveals about the process model shapes how M2 is specified. `MILESTONE`
holds the current one; the gates read it. Order is KIT-07 (Proposed), sharpened:

| Milestone | Adds | Gate checks | Status |
|---|---|---|---|
| **M1** engine + bus + grids | engine, ZeroMQ bus, launcher, four terminals, seeded world, impulse movement, AI random walk, supplies + mutiny, headless sim, autosave | smoke/00–12, live/01–06 | **done** 2026-10-25 (`8d90806`, tag `m1-game`) |
| **M2** movement + hazards | gravity, warp curve, star drain and countdowns, holes, wormholes, persistent moves + `stop`, orders on T4, game over | smoke/13–22, live/08 | **done** 2026-10-26 (tag `m2-game`) |
| **M3** ship combat + reputation + fleets | battles, torpedoes/phasers, peace/escape/destroy outcomes, fear & respect with scaling, AI attack/yield/surrender, capture and fleet movement, AI roaming between sectors, scan shows reputation; M2 fix-ups D-114…116 | smoke/23–36, live/09 | `specs/M3.md` written |
| **M4** planets + trade + items | planet stock and disposition (PL-*, PR-*, EF-03…05), planets in combat (CM-11), `scan`/`pickup` items, `trade`, `buy`/`sell`, `upgrade` with the `[upgrades]` map and caps, prices, AI supplies and buying (D-60 lifted), respect from trade (FR-07/08) | changing a price in the ini changes the trade; upgrade caps hold; destroyed planet looted and removed; AI buys food and the population stays bounded | not started |
| **M5** endgame + mastery | eligibility (EG-01…04), placement of the ultimate item ≥ half the universe away with `guard_ships` Bastions (SP-08, CM-13), mastery ceremony and after-effects (EG-07/08, OUT-15, EF-06, SUP-05) | a scripted run meeting the thresholds → item placed, guardians fight, pickup → `game.mastery`, stats maxed, every planet trades, play continues | not started |
| **M6** packaging | PyInstaller one-folder build (`build.ps1`, `starbour.spec`), `starbour.exe --role` (BR-14/15), config and saves next to the exe (BR-20/21), zip + GitHub Release, README (BR-22), SmartScreen note (BR-19), version on T4 (BR-18) | live/07: the exe launches six processes and stays up; closing T1 leaves no orphans; the ini next to the exe is the one read; a second machine runs the zip `[VERIFY by Usman]` | not started |

Six milestones, not five (D-118): planets and the endgame each earned their own
scenario gates.

Each milestone runs as two Claude Code sessions: **Mn-gates** (harness session —
updates `verify/` from the spec's § Gate changes, selftests, no game code; the deny
rules in `.claude/settings.json` are lifted for that session only and restored with
`git checkout -- .claude/settings.json` afterwards) and **Mn-build** (build session —
first step sets `specs/MILESTONE` to `Mn`, which switches the new gate rows from SKIP to
active). The gate table in `docs/BOOTSTRAP.md` grows with each milestone; existing
rows never weaken except where a decision says so (D-106).
