"""Concentrated (tooth-wound) double-layer three-phase windings via the star of slots.

Every tooth carries one coil. Coil ``k`` links flux with electrical angle ``theta_k = k*p*2*pi/Q``.
Each coil phasor (or its reverse) is assigned to the 60-degree phase belt that contains it
(A+, C-, B+, A-, C+, B-). The winding factor is ``k_w = k_p * k_d``, where:

* pitch factor ``k_p = |sin(p*pi/Q)|`` (a coil spans one slot pitch), and
* distribution factor ``k_d = |sum(sign * e^{j*theta})| / n_coils_per_phase``.
"""

from __future__ import annotations

import cmath
import math
from dataclasses import dataclass
from functools import lru_cache

PHASE_BELTS = (("A", +1), ("C", -1), ("B", +1), ("A", -1), ("C", +1), ("B", -1))


@dataclass(frozen=True)
class WindingLayout:
    slots: int
    poles: int
    coils: tuple[tuple[str, int], ...]  # (phase, direction) per tooth
    pitch_factor: float
    distribution_factor: float
    balanced: bool
    periodicity: int  # gcd(Q, 2p): number of (anti)symmetric machine sections
    cogging_lcm: int

    @property
    def winding_factor(self) -> float:
        return self.pitch_factor * self.distribution_factor

    @property
    def pole_pairs(self) -> int:
        return self.poles // 2

    @property
    def unbalanced_magnetic_pull(self) -> bool:
        """A single machine section means radial magnetic forces do not cancel."""

        return self.periodicity == 1

    def notation(self) -> str:
        """Conventional winding string, one letter per tooth (upper case = forward, lower = reversed)."""

        return "".join(phase if direction > 0 else phase.lower() for phase, direction in self.coils)

    def coils_of(self, phase: str) -> list[tuple[int, int]]:
        return [(index + 1, direction) for index, (ph, direction) in enumerate(self.coils) if ph == phase]


@lru_cache(maxsize=64)
def layout(slots: int, poles: int) -> WindingLayout:
    if slots % 3 or poles % 2 or slots == poles:
        raise ValueError(f"{slots}N{poles}P is not a valid three-phase concentrated winding")
    p = poles // 2
    n = slots // 3
    angles = [math.degrees(k * p * 2.0 * math.pi / slots) % 360.0 for k in range(slots)]
    best: tuple[float, bool, list[tuple[str, int]]] | None = None
    # Coil phasors often fall exactly on belt edges; search the belt offset and keep the
    # balanced assignment with the largest distribution factor.
    for offset in [0.5 + i for i in range(60)]:
        coils = []
        phasors: dict[str, complex] = {"A": 0j, "B": 0j, "C": 0j}
        counts = {"A": 0, "B": 0, "C": 0}
        for angle in angles:
            belt = int(((angle + 30.0 - offset) % 360.0) // 60.0)
            phase, direction = PHASE_BELTS[belt]
            coils.append((phase, direction))
            phasors[phase] += direction * cmath.exp(1j * math.radians(angle))
            counts[phase] += 1
        balanced = _is_balanced(phasors, counts, n)
        k_d = abs(phasors["A"]) / max(counts["A"], 1)
        candidate = (k_d if balanced else -1.0 + k_d * 1e-3, balanced, coils)
        if best is None or candidate[0] > best[0] + 1e-12:
            best = candidate
    assert best is not None
    k_d, balanced, coils = best
    return WindingLayout(
        slots=slots,
        poles=poles,
        coils=tuple(coils),
        pitch_factor=abs(math.sin(p * math.pi / slots)),
        distribution_factor=k_d if balanced else 0.0,
        balanced=balanced,
        periodicity=math.gcd(slots, poles),
        cogging_lcm=slots * poles // math.gcd(slots, poles),
    )


def _is_balanced(phasors: dict[str, complex], counts: dict[str, int], n: int) -> bool:
    if any(counts[ph] != n for ph in "ABC") or abs(phasors["A"]) < 1e-9:
        return False
    magnitudes = [abs(phasors[ph]) for ph in "ABC"]
    if max(magnitudes) - min(magnitudes) > 1e-9 * max(magnitudes):
        return False
    angle_b = math.degrees(cmath.phase(phasors["B"] / phasors["A"])) % 360.0
    angle_c = math.degrees(cmath.phase(phasors["C"] / phasors["A"])) % 360.0
    return {round(angle_b) % 360, round(angle_c) % 360} == {120, 240}
