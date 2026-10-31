"""Structured help (M5 § 1b, D-154): five groups as Command · When · What tables that fit the console width, `help <group>`, the
`help <command>` panel with Syntax · Does · When · Example · Numbers · IDs, and the legacy plain-line helper (CMD-01, UX-09, C4)."""
from __future__ import annotations

import io
import re

import pytest
from rich.console import Console

from starbour.commands.catalogue import COMMANDS, GROUPS, available_names, commands_in, group_names, lookup
from starbour.config import GameConfig
from starbour.engine.state import GameState
from starbour.terminals import render_command as RC

BOX = "│|┃╎┆┊║"
ROW = re.compile(rf"^[\s{BOX}]*(?P<name>[a-z?]+)[\s{BOX}]+(?P<when>now|at end)\b", re.I)    # the gate's HELP_ROW_RULE (D-162 (2))


def plain(renderable, width: int) -> str:
    con = Console(file=io.StringIO(), width=width, record=True, force_terminal=False, color_system=None, legacy_windows=False, highlight=False)
    con.print(renderable)
    return con.export_text(styles=False)


def words(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for w in re.findall(r"[A-Za-z0-9_<>+\-]+", text):
        out[w] = out.get(w, 0) + 1
    return out


def rows(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for ln in text.splitlines():
        m = ROW.match(ln)
        if m:
            out[m.group("name").lower()] = out.get(m.group("name").lower(), 0) + 1
    return out


def test_every_command_has_a_group_example_and_ids() -> None:
    keys = {g for g, _ in GROUPS}
    assert group_names() == ["about", "looking", "fighting", "trading", "game"]
    for c in COMMANDS.values():
        assert c.group in keys and c.example and c.all_ids[0] == c.id and c.when in ("now", "at end"), c.name
    assert [c.name for c in commands_in("fighting")] == ["fire", "peace", "escape"]
    assert sum(len(commands_in(g)) for g in keys) == len(COMMANDS), "every command sits in exactly one group"
    assert all(c.available for c in COMMANDS.values()), "every catalogue command is available at M5"


@pytest.mark.parametrize("width", [80, 120])
def test_help_fits_the_width_and_lists_every_command_once(width: int, cfg: GameConfig, default_state: GameState) -> None:
    snap = default_state.snapshot()
    wide = plain(RC.render_help(None, 400, cfg, snap), 400)
    text = plain(RC.render_help(None, width, cfg, snap), width)
    assert text.strip() and all(len(ln.rstrip()) <= width for ln in text.splitlines()), "no line longer than the width (D-154)"
    assert "…" not in text, "wrapped, never cropped"
    lost = {w: n for w, n in words(wide).items() if words(text).get(w, 0) < n}
    assert not lost, f"words lost at width {width}: {sorted(lost)[:8]}"
    got = rows(text)
    names = [n for n in available_names() if n != "?"]
    assert all(got.get(n, 0) == 1 for n in names), {n: got.get(n, 0) for n in names if got.get(n, 0) != 1}
    assert not [n for n in got if n not in names], "no stray rows"
    low = " ".join(ln for ln in text.splitlines() if not ROW.match(ln)).lower()
    for g, title in GROUPS:
        assert g in low and title.lower() in low, title


@pytest.mark.parametrize("width", [80, 120])
def test_help_group_and_command_forms(width: int, cfg: GameConfig) -> None:
    fighting = plain(RC.render_help("fighting", width, cfg, None), width)
    assert set(rows(fighting)) == {"fire", "peace", "escape"} and all(len(ln.rstrip()) <= width for ln in fighting.splitlines())
    panel = plain(RC.render_help("warp", width, cfg, None), width).lower()
    for label in ("syntax", "does", "when", "example", "numbers", "ids"):
        assert label in panel, label
    assert "cmd-07" in panel and "mv-02" in panel and "warp" in panel and "…" not in panel
    assert all(len(ln.rstrip()) <= width for ln in panel.splitlines())
    assert "unknown" in plain(RC.render_help("teleport", width, cfg, None), width)
    assert set(rows(plain(RC.render_help("GAME", width, cfg, None), width))) == {"help", "end", "quit", "new"}, "groups are case-insensitive"


def test_help_min_width_is_80(cfg: GameConfig) -> None:
    text = plain(RC.render_help(None, 40, cfg, None), 80)
    assert text.strip() and max(len(ln.rstrip()) for ln in text.splitlines()) <= 80 and max(len(ln.rstrip()) for ln in text.splitlines()) > 40


def test_help_fire_numbers_come_from_the_ini(cfg: GameConfig, small_cfg: GameConfig) -> None:
    from starbour.engine.combat import phaser_damage, torpedo_hit_p
    import dataclasses
    hot = dataclasses.replace(small_cfg, torpedo_damage=31.0, phaser_damage_per_unit=7.0)
    text = plain(RC.render_help("fire", 120, hot, None), 120)
    start = hot.ships[hot.starting_class]
    assert "31" in text and f"{int(round(torpedo_hit_p(start.engine, hot) * 100))} %" in text
    for d in range(0, hot.phaser_range + 1):
        assert f"{phaser_damage(start.phasers, 7.0, d):g}" in text
    legacy = "\n".join(t.plain for t in RC.help_text("fire", hot))
    assert "31" in legacy, "help_text keeps the M4 contract (D-148 (9))"
    assert any("move" in t.plain for t in RC.help_text()), "help_text() without a config still lists the commands"
    assert lookup("?") is COMMANDS["help"]
