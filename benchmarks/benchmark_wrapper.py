"""Microbenchmark the wrapper paths that run for every patched function call."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import gc
import statistics
import time
from typing import TYPE_CHECKING

from blockbuster.blockbuster import BlockBuster, BlockingError, _wrap_blocking

if TYPE_CHECKING:
    from collections.abc import Callable


def _noop() -> None:
    pass


def _measure(call: Callable[[], None], iterations: int, rounds: int) -> list[float]:
    call()
    timings: list[float] = []
    gc.disable()
    try:
        for _ in range(rounds):
            start = time.perf_counter_ns()
            for _ in range(iterations):
                call()
            timings.append((time.perf_counter_ns() - start) / iterations)
    finally:
        gc.enable()
    return timings


def _calibrate(call: Callable[[], None], min_time: float) -> int:
    iterations = 1
    while True:
        start = time.perf_counter()
        for _ in range(iterations):
            call()
        duration = time.perf_counter() - start
        if duration >= min_time:
            return iterations
        iterations *= max(2, int(min_time / max(duration, 1e-9)))


async def _run(iterations: int | None, rounds: int, min_time: float) -> None:
    predicate = _wrap_blocking([], [], _noop, "_noop", [], lambda: True)
    allowed = _wrap_blocking(
        [],
        [],
        _noop,
        "_noop",
        [("benchmark_wrapper.py", {"call_allowed"})],
        lambda: False,
    )
    blocked = _wrap_blocking([], [], _noop, "_noop", [], lambda: False)
    scanned_miss = _wrap_blocking(
        ["/path/which/cannot/match"], [], _noop, "_noop", [], lambda: False
    )

    def call_allowed() -> None:
        allowed()

    def call_blocked() -> None:
        with contextlib.suppress(BlockingError):
            blocked()

    cases = {
        "construction": lambda: BlockBuster("blockbuster"),
        "predicate": predicate,
        "can_block_in": call_allowed,
        "blocked": call_blocked,
        "scanned_module_miss": scanned_miss,
    }
    for name, call in cases.items():
        case_iterations = iterations or _calibrate(call, min_time)
        timings = _measure(call, case_iterations, rounds)
        median = statistics.median(timings)
        spread = (
            statistics.quantiles(timings, n=20)[18]
            - statistics.quantiles(timings, n=20)[0]
        )
        print(  # noqa: T201
            f"{name:20} {median:10.0f} ns/call  p95-p05 {spread:8.0f}"
            f"  n={case_iterations}"
        )


def main() -> None:
    """Run the wrapper microbenchmarks."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--iterations", type=int)
    parser.add_argument("--rounds", type=int, default=9)
    parser.add_argument("--min-time", type=float, default=0.2)
    args = parser.parse_args()
    asyncio.run(_run(args.iterations, args.rounds, args.min_time))


if __name__ == "__main__":
    main()
