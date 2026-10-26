"""Injection-molding geometry rules and checks for a solid short-fibre CFRP blade.

Process assumptions (state them, then check them against the real material and mold):

- Solid blade in a two-plate mold, gated at the root, so fibres in the skins align spanwise with
  the flow. The mold opens along a direction d in the y-z plane, perpendicular to the span.
- A sharp trailing edge cannot be filled and chips in service, so the trailing edge is blunted
  to a minimum thickness.

Geometry modification keeps each airfoil's camber line and scales its thickness distribution, then
adds a linear wedge so the trailing-edge thickness reaches the minimum:

    t'(x) = s t(x) + max(0, h_te / c - s t(1)) x,   upper/lower = camber +/- t' / 2

Undercut test (exact for this mold): d has no spanwise component, so every pull ray stays in one
section plane. The part is undercut-free if and only if every section outline splits at its
extremes along e (perpendicular to d in-plane) into two chains that are monotone in e. The local
draft angle of a chain segment is atan(|de| / |dh|); it is zero where the surface is parallel to the
pull, which is acceptable only at the parting line.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace

import numpy as np

from prandtl_prop.cad.airfoils import Airfoil
from prandtl_prop.cad.sections import BladeSection


@dataclass(frozen=True)
class MoldingRules:
    min_trailing_edge_thickness_m: float = 0.0006
    max_section_thickness_m: float = 0.0065
    max_thickness_gradient: float = 0.05  # change in max thickness per unit span (m/m)
    max_flow_length_ratio: float = 150.0  # spanwise fill length / thinnest section max thickness
    min_draft_deg: float = 0.5  # away from the parting line
    parting_zone_fraction: float = 0.02  # of the section extent along e, either side of each parting point


def modify_airfoil(airfoil: Airfoil, thickness_scale: float, trailing_edge_thickness_ratio: float) -> Airfoil:
    """Scale thickness about the camber line and blunt the trailing edge to at least the given t/c."""

    camber = 0.5 * (airfoil.upper + airfoil.lower)
    thickness = (airfoil.upper - airfoil.lower) * thickness_scale
    wedge = max(0.0, trailing_edge_thickness_ratio - float(thickness[-1]))
    thickness = thickness + wedge * airfoil.x
    return Airfoil(
        f"{airfoil.name}_t{thickness_scale:.3f}",
        airfoil.x,
        camber + 0.5 * thickness,
        camber - 0.5 * thickness,
        f"{airfoil.source}; thickness x{thickness_scale:.4f}, TE >= {trailing_edge_thickness_ratio:.4f} c",
    )


def thickness_schedule(r_R: np.ndarray, control_points: Sequence[tuple[float, float]]) -> np.ndarray:
    """Thickness scale at each station from (r/R, scale) control points (linear, clamped at the ends)."""

    if not control_points:
        return np.ones_like(r_R)
    points = sorted(control_points)
    return np.interp(r_R, [p[0] for p in points], [p[1] for p in points])


def molded_sections(sections: Sequence[BladeSection], scales: np.ndarray, trailing_edge_thickness_m: float | None) -> list[BladeSection]:
    out = []
    for section, scale in zip(sections, scales, strict=True):
        te_ratio = (trailing_edge_thickness_m or 0.0) / section.chord_m
        out.append(replace(section, airfoil=modify_airfoil(section.airfoil, float(scale), te_ratio)))
    return out


@dataclass(frozen=True)
class PullCheck:
    pull_angle_deg: float
    undercut_sections: list[int]
    min_draft_deg: float  # over all sections, excluding the parting zones
    worst_section: int


def pull_check(sections: Sequence[BladeSection], pull_angle_deg: float, rules: MoldingRules | None = None) -> PullCheck:
    """Undercut and draft check for a mold opening along d = (-sin(psi), cos(psi)) in the y-z plane."""

    return pull_check_outlines([section.points_3d() for section in sections], pull_angle_deg, rules)


def pull_check_outlines(outlines: Sequence[np.ndarray], pull_angle_deg: float, rules: MoldingRules | None = None) -> PullCheck:
    """Same check on explicit 3D outlines (x = const each), e.g. including the root transition."""

    rules = rules or MoldingRules()
    psi = math.radians(pull_angle_deg)
    d = np.array([-math.sin(psi), math.cos(psi)])
    e = np.array([math.cos(psi), math.sin(psi)])
    undercut: list[int] = []
    worst, worst_index = math.inf, 0
    for index, points in enumerate(outlines):
        outline = np.asarray(points)[:, 1:]
        a, h = outline @ e, outline @ d
        start, end = int(np.argmin(a)), int(np.argmax(a))
        count = len(a)
        chains = []
        for step in (1, -1):
            idx = [start]
            while idx[-1] != end:
                idx.append((idx[-1] + step) % count)
            chains.append(np.array(idx))
        extent = float(a[end] - a[start])
        zone = rules.parting_zone_fraction * extent
        section_draft = math.inf
        for chain in chains:
            da, dh = np.diff(a[chain]), np.diff(h[chain])
            if np.any(da < -1.0e-12):
                undercut.append(index)
                break
            middle = 0.5 * (a[chain][1:] + a[chain][:-1])
            away = (middle > a[start] + zone) & (middle < a[end] - zone)
            if away.any():
                section_draft = min(section_draft, float(np.degrees(np.min(np.arctan2(da[away], np.abs(dh[away]))))))
        if section_draft < worst:
            worst, worst_index = section_draft, index
    return PullCheck(pull_angle_deg, sorted(set(undercut)), worst, worst_index)


def best_pull_angle(
    sections: Sequence[BladeSection] | Sequence[np.ndarray], rules: MoldingRules | None = None, search_deg: float = 45.0
) -> PullCheck:
    """Scan pull angles (0.5 deg steps) for the undercut-free direction with the largest minimum draft.

    Accepts blade sections or explicit 3D outlines.
    """

    rules = rules or MoldingRules()
    outlines = [s.points_3d() if isinstance(s, BladeSection) else np.asarray(s) for s in sections]
    candidates = [pull_check_outlines(outlines, float(angle), rules) for angle in np.arange(-search_deg, search_deg + 0.25, 0.5)]
    valid = [c for c in candidates if not c.undercut_sections]
    pool = valid or candidates
    return max(pool, key=lambda c: (c.min_draft_deg, -abs(c.pull_angle_deg)))


def section_thickness_profile(section: BladeSection) -> tuple[np.ndarray, np.ndarray]:
    """Chordwise stations (m from LE) and local thickness (m)."""

    airfoil = section.airfoil
    return airfoil.x * section.chord_m, (airfoil.upper - airfoil.lower) * section.chord_m


def check_molding(
    sections: Sequence[BladeSection], rules: MoldingRules | None = None, pull_angle_deg: float | None = None
) -> dict[str, object]:
    """All molding checks; values plus pass/fail per rule."""

    rules = rules or MoldingRules()
    radii = np.array([s.r_m for s in sections])
    t_max = np.array([float(np.max(section_thickness_profile(s)[1])) for s in sections])
    t_te = np.array([float(section_thickness_profile(s)[1][-1]) for s in sections])
    gradient = np.abs(np.diff(t_max) / np.diff(radii))
    # Hele-Shaw: chordwise conductance per section ~ integral of t^3 over the chord; spanwise fill resistance ~ integral(dr / G).
    conductance = np.array([float(np.trapezoid(section_thickness_profile(s)[1] ** 3, section_thickness_profile(s)[0])) for s in sections])
    fill_resistance = float(np.trapezoid(1.0 / conductance, radii))
    flow_ratio = float((radii[-1] - radii[0]) / t_max.min())
    pull = pull_check(sections, pull_angle_deg, rules) if pull_angle_deg is not None else best_pull_angle(sections, rules)
    checks = {
        "trailing_edge_thickness": bool(t_te.min() >= rules.min_trailing_edge_thickness_m * (1 - 1e-9)),
        "max_section_thickness": bool(t_max.max() <= rules.max_section_thickness_m),
        "thickness_gradient": bool(gradient.max() <= rules.max_thickness_gradient),
        "flow_length_ratio": bool(flow_ratio <= rules.max_flow_length_ratio),
        "undercut_free": not pull.undercut_sections,
        "draft": bool(pull.min_draft_deg >= rules.min_draft_deg),
    }
    return {
        "min_trailing_edge_thickness_m": float(t_te.min()),
        "max_section_thickness_m": float(t_max.max()),
        "min_section_thickness_m": float(t_max.min()),
        "max_thickness_gradient": float(gradient.max()),
        "spanwise_flow_length_ratio": flow_ratio,
        "fill_resistance_index_m^-3": fill_resistance,
        "pull_angle_deg": pull.pull_angle_deg,
        "undercut_sections": pull.undercut_sections,
        "min_draft_deg": pull.min_draft_deg,
        "min_draft_section": pull.worst_section,
        "checks": checks,
        "all_pass": all(checks.values()),
    }


def hoerner_form_factor(thickness_ratio: np.ndarray | float) -> np.ndarray | float:
    """Profile-drag form factor of a 2D section, 1 + 2 (t/c) + 60 (t/c)^4 (Hoerner)."""

    t = np.asarray(thickness_ratio, dtype=float)
    return 1.0 + 2.0 * t + 60.0 * t**4
