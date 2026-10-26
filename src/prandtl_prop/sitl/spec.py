"""Design-file section ``sitl``: what the SITL export, the log replay, and the mission optimizer read."""

from __future__ import annotations

from dataclasses import dataclass

LAMBDA_GRID = (-0.4, -0.3, -0.2, -0.15, -0.1, -0.075, -0.05, -0.025, 0.0, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.3, 0.4, 0.5)
MU_GRID = (0.0, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5, 0.6)


@dataclass(frozen=True)
class SitlSpec:
    """Rotor-table grid, vehicle layout, battery, reference missions, and the mission-optimizer search space.

    Tables are in rotor coefficients over the axial inflow ratio lambda_c = V_axial / (Omega R) and
    the edgewise advance ratio mu = V_edge / (Omega R). With a Reynolds-independent polar these two
    fix the coefficients exactly, so one rpm suffices; a table polar needs ``rpm_fractions`` (of the
    design rpm) as a third axis.
    """

    lambda_grid: tuple[float, ...] = LAMBDA_GRID
    mu_grid: tuple[float, ...] = MU_GRID
    rpm_fractions: tuple[float, ...] = (0.4, 0.7, 1.0, 1.4, 2.0)  # used only for Reynolds-dependent polars
    layout: str = "quad_x"  # quad_x | hex_x | octo_x, PX4 rotor order; positions from multirotor.arm_length_m
    rotor_positions_m: tuple[tuple[float, float], ...] = ()  # override (x forward, y right) per rotor, FRD
    spin_directions: tuple[int, ...] = ()  # override: +1 counter-clockwise seen from above (PX4 KM > 0)
    iyy_kg_m2: float | None = None  # None: = multirotor.ixx_kg_m2
    active_braking: bool = True  # ESC synchronous rectification (DShot BLHeli_32 / AM32 default): regenerative
    cell_count: int | None = None  # None: round(battery_voltage_V / 3.7)
    sample_rate_hz: float = 50.0
    missions: tuple[str, ...] = ("hover", "survey", "freestyle")
    mission_weights: tuple[float, ...] = (1.0, 1.0, 1.0)
    # Mission optimizer: GMT twist designed at (design_rpm_fraction x hover rpm, bell weight), same planform.
    design_rpm_fractions: tuple[float, ...] = (0.6, 0.7, 0.8, 0.9, 1.0, 1.1)
    chord_scales: tuple[float, ...] = (1.0,)
    bending_weights: tuple[float, ...] = (0.0, 0.1, 0.2, 0.4)
    helix_baseline: bool = True
    max_throttle: float = 1.0  # a sample needing more duty than this on any motor is saturated
    allowed_saturation_fraction: float = 0.002  # of mission time, saturated or above the motor current limit
