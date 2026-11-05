"""Blade sections in 3D and their solid-section properties.

Blade frame (shared by CAD, structures, and CFD):

- +z is the rotation axis and thrust direction; the rotor spins about +z, so the blade moves toward +y.
- +x runs along the span; a section at radius r lies in the plane x = r.
- Each airfoil is scaled by its chord, placed with its pitch axis (``pitch_axis_chord_fraction``,
  quarter chord by default) on the x axis, and pitched nose-up by beta: the leading edge points to
  +y and rises toward +z, and the upper (suction) surface faces +z.

So a local airfoil point (s along the chord from the pitch axis toward the TE, t normal toward the
upper surface) maps to ``(r, -s cos(beta) - t sin(beta), -s sin(beta) + t cos(beta))``.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from prandtl_prop.cad.airfoils import Airfoil


@dataclass(frozen=True)
class SectionProperties:
    """Solid-section properties in the local chord frame (s toward TE, t toward upper surface), about the centroid."""

    area_m2: float
    centroid_s_m: float
    centroid_t_m: float
    I_ss_m4: float
    I_tt_m4: float
    I_st_m4: float

    def principal(self) -> tuple[float, float, float]:
        """Return (I_max, I_min, angle_rad) of the principal axes relative to the chord line."""

        mean = 0.5 * (self.I_ss_m4 + self.I_tt_m4)
        radius = float(np.hypot(0.5 * (self.I_ss_m4 - self.I_tt_m4), self.I_st_m4))
        angle = 0.5 * float(np.arctan2(-2.0 * self.I_st_m4, self.I_ss_m4 - self.I_tt_m4))
        return mean + radius, mean - radius, angle


@dataclass(frozen=True)
class BladeSection:
    r_m: float
    chord_m: float
    beta_deg: float
    airfoil: Airfoil
    pitch_axis_chord_fraction: float

    def local_outline(self) -> np.ndarray:
        """Outline (s, t) in metres, pitch axis at the origin."""

        loop = self.airfoil.loop()
        return np.column_stack([(loop[:, 0] - self.pitch_axis_chord_fraction) * self.chord_m, loop[:, 1] * self.chord_m])

    def points_3d(self) -> np.ndarray:
        outline = self.local_outline()
        beta = np.radians(self.beta_deg)
        s, t = outline[:, 0], outline[:, 1]
        return np.column_stack([np.full(len(s), self.r_m), -s * np.cos(beta) - t * np.sin(beta), -s * np.sin(beta) + t * np.cos(beta)])

    def properties(self) -> SectionProperties:
        return polygon_properties(self.local_outline())


def build_sections(
    r_m: Sequence[float],
    chord_m: Sequence[float],
    beta_deg: Sequence[float],
    airfoils: Sequence[Airfoil],
    pitch_axis_chord_fraction: float = 0.25,
) -> list[BladeSection]:
    return [
        BladeSection(float(r), float(c), float(b), airfoil, pitch_axis_chord_fraction)
        for r, c, b, airfoil in zip(r_m, chord_m, beta_deg, airfoils, strict=True)
    ]


def polygon_properties(outline: np.ndarray) -> SectionProperties:
    """Area, centroid, and centroidal second moments of a simple closed polygon (Green's theorem)."""

    s, t = outline[:, 0], outline[:, 1]
    s_next, t_next = np.roll(s, -1), np.roll(t, -1)
    cross = s * t_next - s_next * t
    area = 0.5 * cross.sum()
    sign = 1.0 if area > 0.0 else -1.0
    area *= sign
    cross *= sign
    centroid_s = ((s + s_next) * cross).sum() / (6.0 * area)
    centroid_t = ((t + t_next) * cross).sum() / (6.0 * area)
    # Second moments about the origin: I_ss = integral of t^2 (bending about the chord line), etc.
    I_ss_origin = ((t**2 + t * t_next + t_next**2) * cross).sum() / 12.0
    I_tt_origin = ((s**2 + s * s_next + s_next**2) * cross).sum() / 12.0
    I_st_origin = ((s * t_next + 2 * s * t + 2 * s_next * t_next + s_next * t) * cross).sum() / 24.0
    return SectionProperties(
        area_m2=float(area),
        centroid_s_m=float(centroid_s),
        centroid_t_m=float(centroid_t),
        I_ss_m4=float(I_ss_origin - area * centroid_t**2),
        I_tt_m4=float(I_tt_origin - area * centroid_s**2),
        I_st_m4=float(I_st_origin - area * centroid_s * centroid_t),
    )


def section_table(sections: Sequence[BladeSection]) -> list[dict[str, float | str]]:
    rows: list[dict[str, float | str]] = []
    for section in sections:
        props = section.properties()
        I_max, I_min, angle = props.principal()
        rows.append(
            {
                "r_m": section.r_m,
                "chord_m": section.chord_m,
                "beta_deg": section.beta_deg,
                "airfoil": section.airfoil.name,
                "airfoil_source": section.airfoil.source,
                "thickness_ratio": section.airfoil.max_thickness,
                "area_m2": props.area_m2,
                "centroid_s_m": props.centroid_s_m,
                "centroid_t_m": props.centroid_t_m,
                "I_ss_m4": props.I_ss_m4,
                "I_tt_m4": props.I_tt_m4,
                "I_st_m4": props.I_st_m4,
                "I_principal_max_m4": I_max,
                "I_principal_min_m4": I_min,
                "principal_angle_deg": float(np.degrees(angle)),
            }
        )
    return rows
