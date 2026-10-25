"""GeneralGrid - sparse semantics, bounds, Void (WG-00)."""
from __future__ import annotations

import pytest

from starbour.world.grid import GeneralGrid, chebyshev


def test_empty_grid_is_all_void() -> None:
    g: GeneralGrid[str] = GeneralGrid(4, 3)
    assert g.size == (4, 3) and g.width == 4 and g.height == 3
    assert len(g) == 0
    assert all(g.is_void(x, y) for x in range(4) for y in range(3))
    assert g.get(2, 1) is None
    assert len(g.free_cells()) == 12


def test_set_get_remove() -> None:
    g: GeneralGrid[str] = GeneralGrid(5, 5)
    g.set(1, 2, "star")
    assert g.get(1, 2) == "star" and not g.is_void(1, 2) and (1, 2) in g and len(g) == 1
    assert g.coords() == [(1, 2)] and g.items() == [((1, 2), "star")] and g.values() == ["star"]
    assert g.remove(1, 2) == "star" and g.is_void(1, 2) and g.remove(1, 2) is None


def test_out_of_bounds_raises() -> None:
    g: GeneralGrid[int] = GeneralGrid(3, 3)
    assert not g.in_bounds(3, 0) and not g.in_bounds(0, -1) and g.in_bounds(2, 2)
    with pytest.raises(IndexError):
        g.get(3, 0)
    with pytest.raises(IndexError):
        g.set(-1, 0, 1)
    with pytest.raises(ValueError):
        GeneralGrid(0, 3)


def test_iteration_is_sorted_not_insertion_ordered() -> None:
    g: GeneralGrid[int] = GeneralGrid(10, 10)
    for x, y in ((9, 9), (0, 5), (3, 1), (0, 0)):
        g.set(x, y, 1)
    assert g.coords() == [(0, 0), (0, 5), (3, 1), (9, 9)] == list(g)


def test_neighbours_clip_to_bounds() -> None:
    g: GeneralGrid[int] = GeneralGrid(3, 3)
    assert g.neighbours(0, 0) == [(0, 1), (1, 0), (1, 1)]
    assert len(g.neighbours(1, 1)) == 8


def test_chebyshev() -> None:
    assert chebyshev((0, 0), (3, 1)) == 3
    assert chebyshev((5, 5), (5, 5)) == 0
    assert chebyshev((2, 7), (4, 1)) == 6
