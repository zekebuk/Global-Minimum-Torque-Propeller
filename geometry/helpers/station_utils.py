from __future__ import annotations

from collections.abc import Sequence


def is_monotonic_increasing(values: Sequence[float]) -> bool:
    return all(right > left for left, right in zip(values, values[1:]))


def max_abs_delta(left: Sequence[float], right: Sequence[float]) -> float:
    return max((abs(a - b) for a, b in zip(left, right)), default=0.0)
