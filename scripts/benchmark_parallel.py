"""Measure sequential vs parallel test execution, honestly.

    python scripts/benchmark_parallel.py [--suite tests/api] [--repeats 1]

Runs the same selection twice - once with a single worker, once with
``-n auto`` - and reports the wall-clock difference.

What this is *not*: a benchmark of the application. It measures how much of the
suite's runtime is spent waiting on I/O and can therefore be overlapped. Numbers
depend entirely on the machine, the core count and how loaded it is, so the
script prints those alongside the result and the README quotes the exact command
rather than a bare figure.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable


def run(args: list[str]) -> tuple[float, int, str]:
    """Run pytest and return elapsed seconds, exit code and the summary line."""
    started = time.perf_counter()
    completed = subprocess.run(
        [PYTHON, "-m", "pytest", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    elapsed = time.perf_counter() - started

    summary = ""
    for line in reversed(completed.stdout.splitlines()):
        if " passed" in line or " failed" in line or " error" in line:
            summary = line.strip().strip("= ")
            break
    return elapsed, completed.returncode, summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare sequential and parallel test runs.")
    parser.add_argument(
        "--suite",
        nargs="+",
        default=["tests/api"],
        help=(
            "Test path(s) to measure. Accepts several, so a group can be measured "
            "as CI runs it: --suite backend/tests tests/api tests/security "
            "(default: tests/api - I/O bound, so it benefits most)"
        ),
    )
    parser.add_argument(
        "--ignore",
        action="append",
        default=[],
        help="Path to exclude, repeatable (e.g. --ignore tests/ui)",
    )
    parser.add_argument("--repeats", type=int, default=1, help="Repeat and take the best time")
    parser.add_argument("--workers", default="auto", help="Value for -n (default: auto)")
    parser.add_argument("--json", dest="as_json", action="store_true", help="Emit JSON")
    args = parser.parse_args()

    base = [
        *args.suite,
        *[f"--ignore={path}" for path in args.ignore],
        "-q",
        "--no-header",
        "-p",
        "no:cacheprovider",
        # `serial` tests are excluded from both sides so the comparison is
        # like-for-like: they are deliberately not parallelisable, and including
        # them would understate the speedup while adding failures that are not
        # about parallelism at all.
        "-m",
        "not serial",
    ]

    print(f"Machine   : {platform.platform()}")
    print(f"Python    : {platform.python_version()}")
    print(f"CPU count : {os.cpu_count()}")
    print(f"Suite     : {' '.join(args.suite)}")
    print(f"Repeats   : {args.repeats} (best of)")
    print()

    sequential = []
    parallel = []

    for attempt in range(1, args.repeats + 1):
        print(f"[{attempt}/{args.repeats}] sequential ...", end=" ", flush=True)
        elapsed, code, summary = run(base)
        sequential.append(elapsed)
        print(f"{elapsed:6.1f}s  ({summary or f'exit {code}'})")

        print(f"[{attempt}/{args.repeats}] parallel   ...", end=" ", flush=True)
        elapsed, code, summary = run([*base, "-n", args.workers])
        parallel.append(elapsed)
        print(f"{elapsed:6.1f}s  ({summary or f'exit {code}'})")

    best_sequential = min(sequential)
    best_parallel = min(parallel)
    # Guarded: a suite fast enough to finish in under a millisecond would
    # otherwise divide by zero and report a meaningless speedup.
    speedup = best_sequential / best_parallel if best_parallel > 0.001 else 0.0
    saved = best_sequential - best_parallel

    if args.as_json:
        print(
            json.dumps(
                {
                    "suite": " ".join(args.suite),
                    "cpu_count": os.cpu_count(),
                    "sequential_seconds": round(best_sequential, 2),
                    "parallel_seconds": round(best_parallel, 2),
                    "speedup": round(speedup, 2),
                    "seconds_saved": round(saved, 2),
                },
                indent=2,
            )
        )
        return 0

    print()
    print("=" * 56)
    print(f"  Sequential   {best_sequential:8.1f}s")
    print(f"  Parallel     {best_parallel:8.1f}s  (-n {args.workers})")
    print(f"  Speedup      {speedup:8.2f}x")
    print(f"  Saved        {saved:8.1f}s")
    print("=" * 56)
    print()
    print("Measures how much of the suite is I/O wait that can be overlapped -")
    print("not application performance. Results vary with hardware and load.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
