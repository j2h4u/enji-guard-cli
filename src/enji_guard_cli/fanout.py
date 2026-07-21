"""Bounded, order-preserving execution for application batch reads."""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from enji_guard_cli.settings import FanoutSettings


@dataclass(frozen=True, slots=True)
class BoundedFanout:
    """Execute independent reads concurrently without leaking pool policy into use cases."""

    settings: FanoutSettings

    def __post_init__(self) -> None:
        if self.settings.max_concurrency < 1:
            raise ValueError("fanout max_concurrency must be positive")

    def map[Item, Result](self, items: Sequence[Item], operation: Callable[[Item], Result]) -> tuple[Result, ...]:
        resolved = tuple(items)
        if len(resolved) <= 1:
            return tuple(operation(item) for item in resolved)
        max_workers = min(self.settings.max_concurrency, len(resolved))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="enji-read") as executor:
            return tuple(executor.map(operation, resolved))


__all__ = ["BoundedFanout"]
