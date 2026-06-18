from __future__ import annotations


def linear_interpolate(x0: float, y0: float, x1: float, y1: float, x: float) -> float:
    if abs(x1 - x0) <= 1.0e-12:
        return y0
    return y0 + (x - x0) / (x1 - x0) * (y1 - y0)
