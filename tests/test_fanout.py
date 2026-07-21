from threading import Barrier, Lock

import pytest

from enji_guard_cli.fanout import BoundedFanout
from enji_guard_cli.settings import FanoutSettings


def test_fanout_bounds_concurrency_and_preserves_input_order() -> None:
    barrier = Barrier(3)
    lock = Lock()
    active = 0
    peak = 0

    def operation(value: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        if value > 0:
            barrier.wait(timeout=1)
        with lock:
            active -= 1
        return value * 10

    result = BoundedFanout(FanoutSettings(max_concurrency=3)).map((3, 2, 1, 0), operation)

    assert result == (30, 20, 10, 0)
    assert peak == 3


def test_fanout_rejects_non_positive_concurrency() -> None:
    with pytest.raises(ValueError, match="must be positive"):
        BoundedFanout(FanoutSettings(max_concurrency=0))
