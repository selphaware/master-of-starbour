"""T2 Grid process: subscribe -> render_grid.render -> print (T2, GRD-*, VW-*)."""
from __future__ import annotations

from starbour.config import GameConfig
from starbour.terminals._base import ViewTerminal
from starbour.terminals.render_grid import render


def main(cfg: GameConfig, pub_port: int) -> int:
    return ViewTerminal("grid", cfg, pub_port, render, "T2 Grid").run()
