"""Bounded, order-preserving execution for application batch reads."""

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from dataclasses import dataclass
from typing import overload

from enji_guard_cli.settings import FanoutSettings


@dataclass(frozen=True, slots=True)
class IndependentRead[T]:
    """One zero-argument read submitted to a bounded fanout."""

    operation: Callable[[], T]

    def __call__(self) -> T:
        return self.operation()


@dataclass(frozen=True, slots=True)
class BoundedFanout:
    """Execute independent reads concurrently without leaking pool policy into use cases."""

    settings: FanoutSettings

    def __post_init__(self) -> None:
        if self.settings.max_concurrency < 1:
            raise ValueError("fanout max_concurrency must be positive")

    def map[Item, Result](self, items: Sequence[Item], operation: Callable[[Item], Result]) -> tuple[Result, ...]:
        resolved = tuple(items)
        return self._run(tuple(lambda item=item: operation(item) for item in resolved))

    @overload
    def gather[A, B](self, first: IndependentRead[A], second: IndependentRead[B]) -> tuple[A, B]: ...

    @overload
    def gather[A, B, C](
        self, first: IndependentRead[A], second: IndependentRead[B], third: IndependentRead[C]
    ) -> tuple[A, B, C]: ...

    @overload
    def gather[A, B, C, D](
        self,
        first: IndependentRead[A],
        second: IndependentRead[B],
        third: IndependentRead[C],
        fourth: IndependentRead[D],
    ) -> tuple[A, B, C, D]: ...

    def gather(
        self,
        first: IndependentRead[object],
        second: IndependentRead[object],
        third: IndependentRead[object] | None = None,
        fourth: IndependentRead[object] | None = None,
    ) -> tuple[object, ...]:
        """Run independent reads concurrently, returning declaration order."""

        reads = (first, second) + (() if third is None else (third,)) + (() if fourth is None else (fourth,))
        return self._run(tuple(reads))

    def _run[Result](self, operations: Sequence[Callable[[], Result]]) -> tuple[Result, ...]:
        resolved = tuple(operations)
        if len(resolved) <= 1:
            return tuple(operation() for operation in resolved)
        max_workers = min(self.settings.max_concurrency, len(resolved))
        with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="enji-read") as executor:
            futures = [executor.submit(copy_context().run, operation) for operation in resolved]
            return tuple(future.result() for future in futures)


__all__ = ["BoundedFanout", "IndependentRead"]
