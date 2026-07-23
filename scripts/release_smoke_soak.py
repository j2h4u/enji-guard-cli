#!/usr/bin/env -S uv run --script
"""Bounded repeated release smoke probes with simple in-process metrics."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

from .release_smoke import DockerSmokeSettings, SmokeFailure, run_probe


@dataclass(frozen=True, slots=True)
class SoakSettings:
    smoke: DockerSmokeSettings
    duration_seconds: float = 300.0
    interval_seconds: float = 30.0
    max_failures: int = 0


@dataclass(frozen=True, slots=True)
class SoakMetrics:
    iterations: int
    failures: int
    elapsed_seconds: float


def run_soak(
    settings: SoakSettings,
    *,
    probe: Callable[[DockerSmokeSettings], tuple[str, ...]] = run_probe,
) -> tuple[int, SoakMetrics]:
    if settings.duration_seconds < 0 or settings.interval_seconds <= 0 or settings.max_failures < 0:
        raise ValueError("duration must be non-negative, interval positive, and failure limit non-negative")
    started = time.monotonic()
    iterations = 0
    failures = 0
    while iterations == 0 or time.monotonic() - started < settings.duration_seconds:
        iterations += 1
        try:
            probe(settings.smoke)
            print(f"PASS soak probe {iterations}")
        except (OSError, SmokeFailure, ValueError) as exc:
            failures += 1
            del exc
            print(f"FAIL soak probe {iterations}: probe failed", file=sys.stderr)
            if failures > settings.max_failures:
                break
        remaining = settings.duration_seconds - (time.monotonic() - started)
        if remaining > 0:
            time.sleep(min(settings.interval_seconds, remaining))
    metrics = SoakMetrics(iterations, failures, time.monotonic() - started)
    print(
        json.dumps(
            {
                "iterations": metrics.iterations,
                "failures": metrics.failures,
                "elapsed_seconds": round(metrics.elapsed_seconds, 3),
            }
        )
    )
    return (1 if failures > settings.max_failures else 0), metrics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--project")
    parser.add_argument("--container", default="enji-guard-cli")
    parser.add_argument("--mcp-url", default="http://127.0.0.1:18080/mcp")
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--interval", type=float, default=30.0)
    parser.add_argument("--max-failures", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=15.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    duration = cast(float, args.duration)
    interval = cast(float, args.interval)
    max_failures = cast(int, args.max_failures)
    timeout = cast(float, args.timeout)
    if duration < 0 or interval <= 0 or max_failures < 0 or timeout <= 0:
        print(
            "FAIL configuration: duration/failure limits non-negative, interval positive, timeout positive",
            file=sys.stderr,
        )
        return 2
    settings = SoakSettings(
        DockerSmokeSettings(
            cast(str, args.repo),
            cast(str | None, args.project),
            cast(str, args.container),
            cast(str, args.mcp_url),
            timeout,
        ),
        duration,
        interval,
        max_failures,
    )
    return run_soak(settings)[0]


if __name__ == "__main__":
    raise SystemExit(main())
