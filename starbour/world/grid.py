"""GeneralGrid - the sparse M x N grid every level is built on (WG-00, D-16).

A cell that holds nothing is Void.  Storage is a dict keyed by (x, y); bounds are
properties.  Iteration is always in sorted (x, y) order so nothing downstream
depends on insertion order (D-67).
"""
from __future__ import annotations

from typing import Generic, Iterator, TypeVar

T = TypeVar("T")

Coord = tuple[int, int]


class GeneralGrid(Generic[T]):
    """Sparse grid: ``(x, y) -> contents``; empty = Void - WG-00."""

    __slots__ = ("_width", "_height", "_cells")

    def __init__(self, width: int, height: int) -> None:
        if width < 1 or height < 1:
            raise ValueError(f"grid size must be at least 1x1, got {width}x{height} (WG-00)")
        self._width = width
        self._height = height
        self._cells: dict[Coord, T] = {}

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    @property
    def size(self) -> Coord:
        return self._width, self._height

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self._width and 0 <= y < self._height

    def _check(self, x: int, y: int) -> None:
        if not self.in_bounds(x, y):
            raise IndexError(f"cell {x},{y} is outside the {self._width}x{self._height} grid (WG-00)")

    def get(self, x: int, y: int) -> T | None:
        """Contents of a cell, or None for Void."""
        self._check(x, y)
        return self._cells.get((x, y))

    def set(self, x: int, y: int, value: T) -> None:
        self._check(x, y)
        self._cells[(x, y)] = value

    def remove(self, x: int, y: int) -> T | None:
        self._check(x, y)
        return self._cells.pop((x, y), None)

    def is_void(self, x: int, y: int) -> bool:
        self._check(x, y)
        return (x, y) not in self._cells

    def __contains__(self, coord: object) -> bool:
        return coord in self._cells

    def __len__(self) -> int:
        """Number of occupied cells."""
        return len(self._cells)

    def coords(self) -> list[Coord]:
        """Occupied coordinates, sorted."""
        return sorted(self._cells)

    def items(self) -> list[tuple[Coord, T]]:
        """(coord, contents) pairs, sorted by coordinate."""
        return [(c, self._cells[c]) for c in sorted(self._cells)]

    def values(self) -> list[T]:
        return [self._cells[c] for c in sorted(self._cells)]

    def __iter__(self) -> Iterator[Coord]:
        return iter(sorted(self._cells))

    def neighbours(self, x: int, y: int) -> list[Coord]:
        """The in-bounds cells adjacent to (x, y) (8-neighbourhood), sorted."""
        out = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = x + dx, y + dy
                if self.in_bounds(nx, ny):
                    out.append((nx, ny))
        return sorted(out)

    def ring(self, x: int, y: int, lo: int, hi: int) -> list[Coord]:
        """The in-bounds cells at Chebyshev distance lo...hi from (x, y), sorted (SP-04 planet band, D-116)."""
        out = []
        for ny in range(max(0, y - hi), min(self._height, y + hi + 1)):
            for nx in range(max(0, x - hi), min(self._width, x + hi + 1)):
                if lo <= max(abs(nx - x), abs(ny - y)) <= hi:
                    out.append((nx, ny))
        return out

    def free_cells(self) -> list[Coord]:
        """Every Void coordinate, sorted (row-major)."""
        return [(x, y) for y in range(self._height) for x in range(self._width) if (x, y) not in self._cells]


def chebyshev(a: Coord, b: Coord) -> int:
    """Chebyshev (king's-move) distance between two cells - MV-01, CM-02."""
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def manhattan(a: Coord, b: Coord) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def nearest_free_cell(grid: GeneralGrid, around: tuple[float, float]) -> Coord | None:
    """The Void cell nearest ``around`` - MV-05 (sector centre), D-102 (wormhole exit).

    Nearest by Chebyshev distance, then Manhattan, then x, then y, so the choice is
    deterministic for a fractional centre such as (11.5, 11.5) (D-67).  ``around`` may
    itself be an occupied cell (a wormhole).  None when the grid is full.
    """
    ax, ay = float(around[0]), float(around[1])
    best: tuple[tuple[float, float, int, int], Coord] | None = None
    for y in range(grid.height):
        for x in range(grid.width):
            if (x, y) in grid:
                continue
            key = (max(abs(x - ax), abs(y - ay)), abs(x - ax) + abs(y - ay), x, y)
            if best is None or key < best[0]:
                best = (key, (x, y))
    return best[1] if best else None


def free_cell_at_distance(grid: GeneralGrid, around: Coord, distance: int) -> Coord | None:
    """The Void cell nearest to exactly ``distance`` (Chebyshev) from ``around`` - HZ-05 wormhole exit (CFG-62, D-114).

    Ordered by |d - distance|, then Manhattan distance, then x, then y (D-113 style), so an
    axis-aligned cell at the exact distance wins over a diagonal one.  None when the grid is full.
    """
    best: tuple[tuple[int, int, int, int], Coord] | None = None
    for y in range(grid.height):
        for x in range(grid.width):
            if (x, y) in grid:
                continue
            key = (abs(chebyshev((x, y), around) - int(distance)), manhattan((x, y), around), x, y)
            if best is None or key < best[0]:
                best = (key, (x, y))
    return best[1] if best else None
