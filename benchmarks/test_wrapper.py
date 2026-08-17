"""Microbenchmarks for the wrapper code that runs on every patched call.

CI runs `pytest tests`, so these are opt-in: `uv run pytest benchmarks`.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from blockbuster.blockbuster import BlockBuster, BlockingError, _wrap_blocking

if TYPE_CHECKING:
    from pytest_benchmark.fixture import BenchmarkFixture

_THIS_FILE = Path(__file__).name


def _noop() -> None:
    pass


def test_construction(benchmark: BenchmarkFixture) -> None:
    """Resolving the scanned and excluded module paths of every wrapped function."""
    benchmark(BlockBuster, "blockbuster")


async def test_predicate(benchmark: BenchmarkFixture) -> None:
    """A call the predicate allows, before any frame is looked at."""
    benchmark(_wrap_blocking([], [], _noop, "_noop", [], lambda: True))


async def test_can_block_in(benchmark: BenchmarkFixture) -> None:
    """A call allowed by `can_block_in`, matched on the caller's frame."""
    wrapper = _wrap_blocking(
        [], [], _noop, "_noop", [(_THIS_FILE, {"call_allowed"})], lambda: False
    )

    def call_allowed() -> None:
        wrapper()

    benchmark(call_allowed)


async def test_blocked(benchmark: BenchmarkFixture) -> None:
    """A blocking call: the whole stack is walked and `BlockingError` is raised."""
    wrapper = _wrap_blocking([], [], _noop, "_noop", [], lambda: False)

    def call_blocked() -> None:
        with contextlib.suppress(BlockingError):
            wrapper()

    benchmark(call_blocked)


async def test_scanned_module_miss(benchmark: BenchmarkFixture) -> None:
    """A call from outside the scanned modules: the whole stack is walked."""
    benchmark(
        _wrap_blocking(
            ["/path/which/cannot/match"], [], _noop, "_noop", [], lambda: False
        )
    )
