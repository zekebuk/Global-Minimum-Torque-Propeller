"""Acoustics for a design: the design as built, and the twist comparison at matched thrust.

``design_noise`` evaluates the design at its operating point (the aero stage's trimmed twist).
``compare_twists`` holds the planform, rpm, and thrust fixed and compares twists: the global-minimum-
torque twist, the Prandtl-bell family (root-bending weight mu > 0, tip unloaded), Betz/MIL, and a
constant-pitch prop. That isolates what the loading distribution does to the noise, which is the
claim under test: that unloading the tip (weaker tip vortex) lowers the dB.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import numpy as np
from scipy.optimize import brentq

from prandtl_prop.acoustics.rotor import AcousticsSpec, rotor_noise
from prandtl_prop.aero.bemt import BemtResult, solve_rotor
from prandtl_prop.aero.polars import AnalyticPolar


def zero_lift_alpha(polar, result: BemtResult) -> np.ndarray:
    """alpha0 per station (Cl = 0 at the station's Reynolds number)."""

    if isinstance(polar, AnalyticPolar):
        return np.full(len(result.solutions), polar.alpha0_deg)
    out = []
    for name, s in zip(result.stations.airfoil, result.solutions, strict=True):
        try:
            out.append(brentq(lambda a, name=name, s=s: polar.evaluate(name, a, s.reynolds).cl, -15.0, 10.0, xtol=1e-4))
        except ValueError:
            out.append(0.0)
    return np.array(out)


def sections_for(design, stations) -> list:
    """3D sections for these stations (any twist), with the design's airfoils and manufacturing changes."""

    from prandtl_prop.cad.airfoils import load_airfoil
    from prandtl_prop.cad.sections import build_sections
    from prandtl_prop.design import resolve_path
    from prandtl_prop.pipeline import apply_manufacturing

    directory = resolve_path(design.airfoils.directory)
    cache: dict[str, Any] = {}
    airfoils = []
    for name in stations.airfoil:
        if name not in cache:
            cache[name] = load_airfoil(name, directory, design.airfoils.points_per_side, design.airfoils.substitutes)
        airfoils.append(cache[name])
    sections = build_sections(stations.r_m, stations.chord_m, stations.twist_deg, airfoils, design.geometry.pitch_axis_chord_fraction)
    return apply_manufacturing(design, sections)


def _te_thickness(design, spec: AcousticsSpec) -> float:
    if spec.trailing_edge_thickness_m is not None:
        return spec.trailing_edge_thickness_m
    return design.airfoils.trailing_edge_thickness_m or 0.0002


def noise_of(design, result: BemtResult, polar, spec: AcousticsSpec) -> dict[str, Any]:
    """Noise of one solved rotor (stations carry the twist actually flown)."""

    stations = replace(result.stations, twist_deg=result.stations.twist_deg + result.collective_offset_deg)
    sections = sections_for(design, stations) if spec.thickness_noise else None
    alpha0 = zero_lift_alpha(polar, result)
    return rotor_noise(result, sections, spec, alpha0_deg=alpha0, te_thickness_m=_te_thickness(design, spec))


def design_noise(design, aero_run, spec: AcousticsSpec) -> tuple[BemtResult, dict[str, Any]]:
    from prandtl_prop import mission

    stations, polar, corrections, collective, op = mission.bemt_context(design, aero_run)
    result = solve_rotor(stations, op, polar, collective_offset_deg=collective, tip_loss=design.aero.tip_loss, corrections=corrections)
    return result, noise_of(design, result, polar, spec)


def compare_twists(
    design, aero_run, spec: AcousticsSpec, *, bending_weights=(0.0, 0.1, 0.2, 0.4, 0.8), cl_limit: float | None = None
) -> dict[str, Any]:
    """GMT, the bell family, MIL, and constant pitch at the design's planform, rpm, and thrust."""

    from prandtl_prop import mission
    from prandtl_prop.aero import gmt

    stations, polar, corrections, collective, op = mission.bemt_context(design, aero_run)
    base = solve_rotor(stations, op, polar, collective_offset_deg=collective, tip_loss=design.aero.tip_loss, corrections=corrections)
    thrust = float(base.summary["thrust_N"])
    limit = cl_limit if cl_limit is not None else design.gmt.cl_limit
    limit = max(limit, max(s.cl for s in base.solutions) + 0.05)  # never tighter than the design itself flies
    weights = tuple(bending_weights if bending_weights is not None else spec.bending_weights)
    cases = gmt.compare(stations, op, polar, thrust, cl_limit=limit, bending_weights=weights, tip_loss=design.aero.tip_loss)
    rows: dict[str, tuple[BemtResult, dict[str, Any]]] = {}
    for mu, (_, result) in sorted(cases["gmt"].items()):
        rows[f"GMT mu={mu:g}"] = (result, noise_of(design, result, polar, spec))
    rows["Betz/MIL"] = (cases["mil"][1], noise_of(design, cases["mil"][1], polar, spec))
    pitch, helix = cases["helix"]
    rows[f"constant pitch (P/D {pitch / (2 * op.radius_m):.2f})"] = (helix, noise_of(design, helix, polar, spec))
    rows["design as built"] = (base, noise_of(design, base, polar, spec))
    return {"thrust_N": thrust, "rpm": op.rpm, "rows": rows}
