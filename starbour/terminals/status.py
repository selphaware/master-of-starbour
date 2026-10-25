"""T4 Status process: subscribe -> render_status.render -> print (T4, PNL-01...05)."""
from __future__ import annotations

from rich.console import RenderableType

from starbour.config import GameConfig
from starbour.terminals._base import ViewTerminal
from starbour.terminals.render_status import render


def _render(snapshot: dict, view: dict | None, cfg: GameConfig, width: int, height: int | None = None) -> RenderableType:
    return render(snapshot, view, cfg, width)


def main(cfg: GameConfig, pub_port: int) -> int:
    return ViewTerminal("status", cfg, pub_port, _render, "T4 Status").run()
