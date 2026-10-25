# Master of Starbour

A text-based, turn-based starship strategy game for Windows 10/11 terminals.
Explore a seeded universe of galaxies and sectors, trade with planets, fight or
make peace with rival captains, earn fear and respect, and find the ultimate secret
item to become Master of Starbour — then keep playing with everything maxed.

*"Starbour" is a pun on harbour / starship. It is a title, not a place.*

Four colourful console windows — Command, Grid, Output, Status — plus an engine
process, over ZeroMQ on your machine. The player queues any number of orders and
ends the turn; every captain's orders resolve at once.

**Status:** in development. The design is in `docs/master_of_starbour_blueprint.xlsx`
(rendered as `docs/DESIGN.md`); the build is milestone-driven (`specs/`). Releases
will appear under GitHub Releases as a zipped one-folder build (`starbour.exe`).

## Play (from a release)

1. Download the latest zip from Releases and extract it anywhere.
2. Run `starbour.exe`. Four windows open.
3. Type `help` in the Command window.

The build is not code-signed yet, so Windows SmartScreen will warn on first run:
choose *More info → Run anyway*. Everything tunable is in `controls_config.ini`
next to `starbour.exe`; your save is `saves\game.json`.

## Build from source

```powershell
git clone git@github.com:selphaware/master-of-starbour.git E:\CODE\master-of-starbour
cd E:\CODE\master-of-starbour
<your python 3.12> -m venv MOS
.\MOS\Scripts\python.exe -m pip install -e .[dev]
.\launch.ps1
```

`docs/SETUP.md` has the full environment and verification steps.

## Licence

MIT — see `LICENSE`. Copyright (c) 2026 Usman Ahmad.
