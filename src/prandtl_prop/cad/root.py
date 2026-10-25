"""Blade root: a clamped tab plus an explicit transition into the first aerodynamic section.

The root is built from explicit sections, not left to the CAD kernel, so every section can be
checked for moldability and the loft keeps one topology throughout: each outline has the same
point count, the same point correspondence, and the same blunt trailing-edge gap.

- Tab: a superellipse ("rounded rectangle", exponent p) of width W (chordwise) and thickness T,
  flat in the hub plane (pitch ``tab_pitch_deg``). It runs from ``tab_inner_radius_m`` to
  ``tab_outer_radius_m``; the hub clamps this region and two bolts pass through it along z.
- Transition: from ``tab_outer_radius_m`` to the first aerodynamic station. In the pitch-axis frame,
  chordwise position s, thickness coordinate t, and pitch beta all blend with
  w = 3 x^2 - 2 x^3, x = (r - r_tab) / (r_root - r_tab). The blend has zero slope at both ends.

Point correspondence: an airfoil point at chord fraction x on the upper (lower) surface maps to the
superellipse angle theta = pi (1 - x) (theta = pi (1 + x)). So LE maps to LE, TE to TE, and
upper to upper.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from prandtl_prop.cad.sections import BladeSection


@dataclass(frozen=True)
class RootGeometry:
    tab_inner_radius_m: float = 0.014
    tab_outer_radius_m: float = 0.034
    tab_width_m: float = 0.028
    tab_thickness_m: float = 0.0085
    corner_exponent: float = 4.0
    tab_pitch_deg: float = 0.0
    transition_sections: int = 10
    bolt_diameter_m: float = 0.0053
    bolt_radii_m: tuple[float, ...] = (0.020, 0.029)
    # Chordwise position of the tab centre in the root section's (s, t) frame, from the pitch axis
    # (+s toward the TE). None centres it on the chord midpoint. The centrifugal resultant runs
    # through the rotation axis, so a tab off the line from the axis through the blade's centre of
    # mass puts an in-plane moment F_c * offset on the hub joint (see prandtl_prop.cad.hub).
    tab_centre_s_m: float | None = None
    # "square": the tab ends flat at tab_inner_radius_m (two bolts, fixed blade). "round": a
    # semicircular end centred on the single hinge pin at bolt_radii_m[0] (folding blade), so the end
    # sweeps a circle of radius tab_width_m / 2 as the blade folds; tab_inner_radius_m must then be
    # pin - tab_width_m / 2.
    tab_end: str = "square"


def _superellipse(
    x: np.ndarray, upper: bool, half_width: float, half_thickness: float, exponent: float, te_gap: float
) -> tuple[np.ndarray, np.ndarray]:
    theta = math.pi * (1.0 - x) if upper else math.pi * (1.0 + x)
    cos, sin = np.cos(theta), np.sin(theta)
    u = half_width * np.sign(cos) * np.abs(cos) ** (2.0 / exponent)
    v = half_thickness * np.sign(sin) * np.abs(sin) ** (2.0 / exponent)
    # Keep the airfoil's blunt-TE gap at the TE end, confined there so the clamp faces stay flat.
    v = v + (1.0 if upper else -1.0) * 0.5 * te_gap * (0.5 * (1.0 + cos)) ** 8
    return u, v


def tab_local_outline(root: BladeSection, geometry: RootGeometry, width_scale: float = 1.0) -> np.ndarray:
    """Tab outline in the root section's local (s, t) frame, with the same point order as the airfoil loop."""

    airfoil = root.airfoil
    te_gap = airfoil.trailing_edge_gap * root.chord_m * width_scale
    centre_s = (0.5 - root.pitch_axis_chord_fraction) * root.chord_m if geometry.tab_centre_s_m is None else geometry.tab_centre_s_m
    half_w, half_t = 0.5 * geometry.tab_width_m * width_scale, 0.5 * geometry.tab_thickness_m
    u_up, v_up = _superellipse(airfoil.x, True, half_w, half_t, geometry.corner_exponent, te_gap)
    u_lo, v_lo = _superellipse(airfoil.x, False, half_w, half_t, geometry.corner_exponent, te_gap)
    upper = np.column_stack([u_up[::-1], v_up[::-1]])
    lower = np.column_stack([u_lo[1:], v_lo[1:]])
    outline = np.vstack([upper, lower])
    outline[:, 0] += centre_s
    return outline


def _place(local: np.ndarray, r_m: float, beta_deg: float) -> np.ndarray:
    beta = math.radians(beta_deg)
    s, t = local[:, 0], local[:, 1]
    return np.column_stack([np.full(len(s), r_m), -s * math.cos(beta) - t * math.sin(beta), -s * math.sin(beta) + t * math.cos(beta)])


def root_outlines(root: BladeSection, geometry: RootGeometry) -> list[np.ndarray]:
    """3D outlines from the tab's inner end up to (not including) the first aerodynamic station."""

    if not geometry.tab_inner_radius_m < geometry.tab_outer_radius_m < root.r_m:
        raise ValueError("Root geometry needs tab_inner_radius < tab_outer_radius < first aerodynamic station radius")
    tab = tab_local_outline(root, geometry)
    airfoil = root.local_outline()
    if geometry.tab_end == "round":
        pin, radius = geometry.bolt_radii_m[0], 0.5 * geometry.tab_width_m
        if len(geometry.bolt_radii_m) != 1 or abs(pin - radius - geometry.tab_inner_radius_m) > 1.0e-6:
            raise ValueError("A round tab end needs one hinge pin and tab_inner_radius_m = pin - tab_width_m / 2")
        # Plan view: a semicircle about the pin (a 20% wide flat at the very end keeps the loft regular).
        outlines = [
            _place(tab_local_outline(root, geometry, math.sin(phi)), pin - radius * math.cos(phi), geometry.tab_pitch_deg)
            for phi in np.linspace(0.2, 0.5 * math.pi, 7)
        ]
        outlines.append(_place(tab, geometry.tab_outer_radius_m, geometry.tab_pitch_deg))
    elif geometry.tab_end == "square":
        outlines = [
            _place(tab, geometry.tab_inner_radius_m, geometry.tab_pitch_deg),
            _place(tab, geometry.tab_outer_radius_m, geometry.tab_pitch_deg),
        ]
    else:
        raise ValueError(f"tab_end must be 'square' or 'round', not {geometry.tab_end!r}")
    for x in np.linspace(0.0, 1.0, geometry.transition_sections + 2)[1:-1]:
        w = 3.0 * x**2 - 2.0 * x**3
        r = geometry.tab_outer_radius_m + x * (root.r_m - geometry.tab_outer_radius_m)
        local = (1.0 - w) * tab + w * airfoil
        outlines.append(_place(local, r, (1.0 - w) * geometry.tab_pitch_deg + w * root.beta_deg))
    return outlines


def full_blade_outlines(
    sections: Sequence[BladeSection], geometry: RootGeometry, hub_outlines: Sequence[np.ndarray] = ()
) -> list[np.ndarray]:
    """All outlines root to tip: (one-piece hub,) tab, transition, then every aerodynamic station."""

    return list(hub_outlines) + root_outlines(sections[0], geometry) + [section.points_3d() for section in sections]


def tab_outline_3d(sections: Sequence[BladeSection], geometry: RootGeometry) -> np.ndarray:
    """The full-width tab section, placed at the tab's outer (clamp-edge) radius."""

    return _place(tab_local_outline(sections[0], geometry), geometry.tab_outer_radius_m, geometry.tab_pitch_deg)


def bolt_centres(sections: Sequence[BladeSection], geometry: RootGeometry) -> list[tuple[float, float, float]]:
    """Bolt-hole axes (along z) at the tab centre line, in metres."""

    root = sections[0]
    tab = _place(tab_local_outline(root, geometry), geometry.tab_inner_radius_m, geometry.tab_pitch_deg)
    y_centre = 0.5 * (tab[:, 1].min() + tab[:, 1].max())
    z_centre = 0.5 * (tab[:, 2].min() + tab[:, 2].max())
    return [(radius, float(y_centre), float(z_centre)) for radius in geometry.bolt_radii_m]
