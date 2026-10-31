"""T4 Status process: subscribe -> render_status.render -> print (T4, PNL-01...07).

The C5 heartbeat gains ``in_battle`` (M3, C10 / live/09): true while the player's active
ship is in a battle according to the snapshot last drawn; and ``docked`` (the id of the
planet in the player's cell, or null) and ``items`` (the number of cargo stacks) in M4
(C11 / live/10, D-148 (13)).
"""
from __future__ import annotations

from rich.console import RenderableType

from starbour.config import GameConfig
from starbour.terminals._base import ViewTerminal
from starbour.terminals.render_status import cargo_stacks, docked_planet_id, in_battle, render


def _render(snapshot: dict, view: dict | None, cfg: GameConfig, width: int, height: int | None = None) -> RenderableType:
    return render(snapshot, view, cfg, width)


class StatusTerminal(ViewTerminal):
    def __init__(self, cfg: GameConfig, pub_port: int) -> None:
        super().__init__("status", cfg, pub_port, _render, "T4 Status")

    def heartbeat_fields(self) -> dict:
        snap = self.snapshot or {}
        return {"in_battle": in_battle(snap), "docked": docked_planet_id(snap), "items": cargo_stacks(snap)}


def main(cfg: GameConfig, pub_port: int) -> int:
    return StatusTerminal(cfg, pub_port).run()
