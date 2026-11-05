"""Two-plate injection mold inserts (core and cavity) for a blade, built in CadQuery/OpenCascade.

Frame: the mold opens along +z (the blade's thrust axis), so the bolt-hole pins are parallel to
the pull and the mold frame is the blade frame (units: metres in, millimetres in exported STEP).

Construction:

1. **Parting surface z = F(x, y)**, a single-valued graph, so the halves separate along z with no
   overhang. Inside each blade section it follows the mid-line between the upper and lower
   surfaces, which always lies inside the section (the chord line can exit a cambered section near
   the trailing edge, which would trap a sliver of mold). Outside the section it leaves each
   silhouette point at the mid-line's mean slope over the outer 6% of the section and eases to
   horizontal, z_P + s L (1 - exp(-|dy| / L)).
2. **Each half is lofted directly, with no boolean against the part.** Per section, the core's
   profile runs along the eased parting line to the TE exit, along the part's upper surface to the
   LE, along the parting line to the insert edge, and closes over the top of the insert; the
   cavity's uses the lower surface and closes under the bottom. The profiles are lofted spanwise
   through the same section stations as the part, so the impression interpolates the same points
   as the part. Beyond the root and tip, flat profiles (mid-line instead of the surface) close the
   insert; they share their parting edges with the impression profiles, so the pieces fuse on
   coincident planar faces. Exact booleans between the lofted part and a parting region were tried
   first and failed: valid inputs gave empty or invalid results near the silhouette.
3. **Shrinkage.** The impression uses outlines scaled by (1 + s_flow) spanwise and (1 + s_cross) in
   y and z about the rotor axis. Equal in-plane scaling preserves the twist angles; residual-stress
   warpage is not modelled (run a molding simulation).
4. **Features.** Bolt-hole core pins meeting at the parting surface (shut-off); a full-round runner
   on the parting surface, ending in a gate at the root tab's inner end; ejector-pin holes in the
   core (on the tab and runner, off the aero surfaces); leader-pin holes; spanwise cooling channels
   in both halves. Clearances are checked.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np


@dataclass(frozen=True)
class MoldDesign:
    margin_m: float = 0.025  # insert material around the part (y, z) and beyond the tip
    root_margin_m: float = 0.030  # beyond the tab end (runner length inside the insert)
    shrink_flow: float = 0.002  # spanwise (fibre flow direction)
    shrink_cross: float = 0.008  # across the flow
    parting_ease_m: float = 0.003  # length scale for the parting surface to ease to horizontal
    te_parting_fraction: float = 0.2  # parting exit on the blunt-TE flat, from the drafted corner (see half_chains)
    grid_step_m: float = 0.0008  # parting surface grid spacing in y
    runner_diameter_m: float = 0.006
    gate_diameter_m: float = 0.003
    gate_land_m: float = 0.003
    ejector_diameter_m: float = 0.004
    leader_pin_diameter_m: float = 0.012
    cooling_diameter_m: float = 0.008
    cooling_offset_m: float = 0.012  # from the part's extreme z to the channel centre
    cooling_lanes: int = 3
    ejector_radii_m: tuple[float, ...] = field(default=(0.017, 0.032))  # on the tab (avoid the bolt holes)


@dataclass
class MoldResult:
    core: object  # +z half (moving, carries the ejectors)
    cavity: object  # -z half (fixed, sprue side)
    cavity_part: object  # shrink-compensated part (the impression's shape, for inspection)
    metrics: dict


def _cq():
    import cadquery as cq

    return cq


def half_chains(outline_yz: np.ndarray, te_fraction: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """Upper and lower surface chains of one section, each from the parting exit at the TE to the LE.

    The outline order is upper TE ... LE (max y) ... lower TE. On a blunt TE the parting exit sits
    on the TE flat at ``te_fraction`` of its length from the extreme-y corner, the corner that
    drafts correctly (its flat faces away from the other half). Exiting exactly at that corner
    would put the parting line on an edge of the part; the price of moving it is a chamfer of the
    dropped corner of at most te_fraction * gap (0.12 mm for a 0.6 mm TE at 0.2).
    """

    o = np.asarray(outline_yz, dtype=float)
    le = int(np.argmax(o[:, 0]))
    first, last = o[0], o[-1]
    if np.linalg.norm(first - last) < 1.0e-9:  # closed (sharp) TE
        upper, lower = o[: le + 1], o[le:][::-1]
    elif first[0] <= last[0]:  # upper corner is the extreme
        exit_point = first + te_fraction * (last - first)
        upper, lower = np.vstack([exit_point, o[1 : le + 1]]), np.vstack([exit_point, o[le:][::-1]])
    else:
        exit_point = last + te_fraction * (first - last)
        upper, lower = np.vstack([exit_point, o[: le + 1]]), np.vstack([exit_point, o[le:-1][::-1]])
    keep = lambda chain: np.vstack([chain[:1], chain[1:][chain[1:, 0] > chain[0, 0]]])  # noqa: E731 - y increasing from the exit
    return keep(upper), keep(lower)


def parting_row(outline_yz: np.ndarray, y_grid: np.ndarray, ease_m: float, te_fraction: float = 0.0) -> np.ndarray:
    """z of the parting surface along y for one section: mid-line inside, easing to flat outside."""

    upper, lower = half_chains(outline_yz, te_fraction)
    y_min, y_max = float(upper[0, 0]), float(upper[-1, 0])
    inside = (y_grid >= y_min) & (y_grid <= y_max)
    z = np.empty_like(y_grid)
    z[inside] = 0.5 * (np.interp(y_grid[inside], upper[:, 0], upper[:, 1]) + np.interp(y_grid[inside], lower[:, 0], lower[:, 1]))
    probe = np.linspace(y_min, y_max, 400)
    mid = 0.5 * (np.interp(probe, upper[:, 0], upper[:, 1]) + np.interp(probe, lower[:, 0], lower[:, 1]))
    k = max(2, int(0.06 * len(probe)))  # slope over the outer 6% of the section, away from the LE/TE singular point
    slope_min = (mid[k] - mid[0]) / (probe[k] - probe[0])
    slope_max = (mid[-1] - mid[-1 - k]) / (probe[-1] - probe[-1 - k])
    left, right = y_grid < y_min, y_grid > y_max
    z[left] = mid[0] - slope_min * ease_m * (1.0 - np.exp(-(y_min - y_grid[left]) / ease_m))
    z[right] = mid[-1] + slope_max * ease_m * (1.0 - np.exp(-(y_grid[right] - y_max) / ease_m))
    return z


_EASE_POINTS = 25
_MID_POINTS = 41
_CHAIN_POINTS = 81


def _profile_wire(x: float, outline_yz: np.ndarray, y_grid: np.ndarray, z_outer: float, design: MoldDesign, surface: str):
    """Closed planar wire at span x for one mold half.

    surface: "upper" (core impression), "lower" (cavity impression), or "mid" (no part: a flat
    parting profile for the insert beyond the root and tip). The parting edges outside the section
    are built from identical points for all three, so pieces meet on coincident edges.
    """

    cq = _cq()
    upper, lower = half_chains(outline_yz, design.te_parting_fraction)
    exit_point, leading = upper[0], upper[-1]
    # A fixed point count per edge, denser toward the silhouette where the parting line eases out: a
    # count that varied with the section (all grid points outside it) made the spanwise skinning merge
    # incompatible parameterisations until it failed beyond about ten sections.
    t = np.linspace(0.0, 1.0, _EASE_POINTS)[1:] ** 2
    y_left = (exit_point[0] - t * (exit_point[0] - y_grid[0]))[::-1]
    y_right = leading[0] + t * (y_grid[-1] - leading[0])
    row = lambda ys: np.column_stack([ys, parting_row(outline_yz, ys, design.parting_ease_m, design.te_parting_fraction)])  # noqa: E731
    left = np.vstack([row(y_left), exit_point])
    right = np.vstack([leading, row(y_right)])

    def vec(point) -> object:
        return cq.Vector(x, float(point[0]), float(point[1]))

    def spline(points) -> object:
        return cq.Edge.makeSpline([vec(point) for point in points])

    edges = [spline(left)]
    if surface == "mid":
        y_inside = exit_point[0] + 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, _MID_POINTS)[1:-1])) * (leading[0] - exit_point[0])
        edges.append(spline(np.vstack([exit_point, row(y_inside), leading])))
    else:
        chain = upper if surface == "upper" else lower
        # Resample at a fixed count (arc length, cosine-spaced toward the TE corner and the LE): the raw
        # chains differ by a few points between sections (the max-y LE point shifts with pitch), and
        # mismatched spline knot vectors broke the spanwise skinning of the cavity half.
        raw = spline(chain[1:])
        fractions = 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, _CHAIN_POINTS)))
        resampled = [raw.positionAt(float(f), mode="length") for f in fractions]
        edges += [cq.Edge.makeLine(vec(chain[0]), vec(chain[1])), cq.Edge.makeSpline(resampled)]
    edges.append(spline(right))
    end, start = right[-1], left[0]
    edges += [
        cq.Edge.makeLine(vec(end), vec((end[0], z_outer))),
        cq.Edge.makeLine(vec((end[0], z_outer)), vec((start[0], z_outer))),
        cq.Edge.makeLine(vec((start[0], z_outer)), vec(start)),
    ]
    return cq.Wire.assembleEdges(edges)


def _loft(wires, ruled: bool):
    cq = _cq()
    return cq.Solid.makeLoft(wires, ruled)


def _loft_runs(wires, yz: Sequence[np.ndarray]):
    """Ruled lofts over runs of identical sections (prismatic tab or hub), smooth lofts elsewhere, fused."""

    same = [np.allclose(yz[i + 1], yz[i], rtol=0.0, atol=1.0e-9) for i in range(len(yz) - 1)]
    runs: list[tuple[bool, int, int]] = []
    for i, is_prism in enumerate(same):
        if runs and runs[-1][0] == is_prism:
            runs[-1] = (is_prism, runs[-1][1], i + 1)
        else:
            runs.append((is_prism, i, i + 1))
    pieces = [_loft([wires[a], wires[b]], ruled=True) if prism else _loft(wires[a : b + 1], ruled=False) for prism, a, b in runs]
    solid = pieces[0]
    for piece in pieces[1:]:
        solid = solid.fuse(piece)
    return solid


def _half(
    outlines: Sequence[np.ndarray], x_range: tuple[float, float], y_grid: np.ndarray, z_outer: float, surface: str, design: MoldDesign
):
    """One mold half: impression loft through the section stations plus flat end pieces to the insert faces.

    An end piece is skipped when the range starts (or ends) at the first (last) station, as for the
    blades of a one-piece prop, which meet a centre block instead.
    """

    yz = [np.asarray(o)[:, 1:] for o in outlines]
    xs = [float(o[0, 0]) for o in outlines]
    profile = lambda x, o, kind: _profile_wire(x, o, y_grid, z_outer, design, kind)  # noqa: E731
    solid = _loft_runs([profile(x, o, surface) for x, o in zip(xs, yz, strict=True)], yz)
    if x_range[0] < xs[0] - 1.0e-9:
        solid = solid.fuse(_loft([profile(x_range[0], yz[0], "mid"), profile(xs[0], yz[0], "mid")], ruled=True))
    if x_range[1] > xs[-1] + 1.0e-9:
        solid = solid.fuse(_loft([profile(xs[-1], yz[-1], "mid"), profile(x_range[1], yz[-1], "mid")], ruled=True))
    return solid.clean()


def shrink_factors(design: MoldDesign) -> np.ndarray:
    return np.array([1.0 + design.shrink_flow, 1.0 + design.shrink_cross, 1.0 + design.shrink_cross])


def build_mold(
    outlines: Sequence[np.ndarray],
    bolt_centres: Sequence[tuple[float, float, float]] = (),
    bolt_diameter_m: float = 0.0,
    design: MoldDesign = MoldDesign(),  # noqa: B008
) -> MoldResult:
    """Core/cavity inserts for the blade lofted through ``outlines`` (root to tip, metres, blade frame)."""

    from prandtl_prop.cad.step import outlines_solid

    cq = _cq()
    scale = shrink_factors(design)
    scaled_outlines = [np.asarray(o) * scale for o in outlines]
    bolt_scaled = [tuple(float(v) for v in np.asarray(c) * scale) for c in bolt_centres]
    bolt_r = 0.5 * bolt_diameter_m * (1.0 + design.shrink_cross)
    cavity_part = outlines_solid(scaled_outlines, bolt_scaled, 2.0 * bolt_r, 0.1)
    box = cavity_part.BoundingBox()
    x0, x1 = box.xmin - design.root_margin_m, box.xmax + design.margin_m
    y0, y1 = box.ymin - design.margin_m, box.ymax + design.margin_m
    z0, z1 = box.zmin - design.margin_m, box.zmax + design.margin_m
    y_grid = np.linspace(y0, y1, int(np.ceil((y1 - y0) / design.grid_step_m)) + 1)
    core = _half(scaled_outlines, (x0, x1), y_grid, z1, "upper", design)
    cavity = _half(scaled_outlines, (x0, x1), y_grid, z0, "lower", design)
    blank_core = _half(scaled_outlines, (x0, x1), y_grid, z1, "mid", design)
    blank_cavity = _half(scaled_outlines, (x0, x1), y_grid, z0, "mid", design)
    impression_core = _volume(blank_core) - _volume(core)
    impression_cavity = _volume(blank_cavity) - _volume(cavity)
    block_volume = (x1 - x0) * (y1 - y0) * (z1 - z0)

    # Bolt-hole core pins: one from each half, meeting at the parting surface (a shut-off).
    tab_root = scaled_outlines[0]
    tab_top, tab_bottom = float(np.max(tab_root[:, 2])), float(np.min(tab_root[:, 2]))
    for x, y, _ in bolt_scaled:
        z_split = float(parting_row(tab_root[:, 1:], np.array([y]), design.parting_ease_m, design.te_parting_fraction)[0])
        core = core.fuse(cq.Solid.makeCylinder(bolt_r, tab_top + 0.001 - z_split, cq.Vector(x, y, z_split), cq.Vector(0, 0, 1)))
        cavity = cavity.fuse(
            cq.Solid.makeCylinder(bolt_r, z_split - tab_bottom + 0.001, cq.Vector(x, y, tab_bottom - 0.001), cq.Vector(0, 0, 1))
        )
    core, cavity = core.clean(), cavity.clean()

    # Runner: full round, on the parting surface, from the insert's root face to the tab end, then a tapered gate.
    y_gate = 0.5 * (tab_root[:, 1].min() + tab_root[:, 1].max())
    z_gate = float(parting_row(tab_root[:, 1:], np.array([y_gate]), design.parting_ease_m, design.te_parting_fraction)[0])
    x_tab = float(tab_root[0, 0])
    runner_r = 0.5 * design.runner_diameter_m
    runner_end = x_tab - design.gate_land_m
    runner = cq.Solid.makeCylinder(runner_r, runner_end - x0 + 0.002, cq.Vector(x0 - 0.001, y_gate, z_gate), cq.Vector(1, 0, 0))
    gate = cq.Solid.makeCone(
        runner_r, 0.5 * design.gate_diameter_m, design.gate_land_m + 0.0005, cq.Vector(runner_end, y_gate, z_gate), cq.Vector(1, 0, 0)
    )
    feed = runner.fuse(gate)
    core, cavity = core.cut(feed), cavity.cut(feed)

    # Ejector pins (core = moving half): on the tab top face, clear of the bolt holes, plus one on the runner.
    ejector_r = 0.5 * design.ejector_diameter_m
    ejectors = []
    tab_top = float(np.max(tab_root[:, 2]))
    for radius in design.ejector_radii_m:
        x = radius * (1.0 + design.shrink_flow)
        ejectors.append(cq.Solid.makeCylinder(ejector_r, z1 - tab_top + 0.002, cq.Vector(x, y_gate, tab_top - 0.0005), cq.Vector(0, 0, 1)))
    x_runner_pin = 0.5 * (x0 + runner_end)
    ejectors.append(cq.Solid.makeCylinder(ejector_r, z1 - z_gate + 0.002, cq.Vector(x_runner_pin, y_gate, z_gate), cq.Vector(0, 0, 1)))
    for pin in ejectors:
        core = core.cut(pin)

    # Leader pins at the four corners, through both halves.
    leader_r = 0.5 * design.leader_pin_diameter_m
    inset = 0.6 * design.margin_m
    for x in (x0 + inset, x1 - inset):
        for y in (y0 + inset, y1 - inset):
            hole = cq.Solid.makeCylinder(leader_r, z1 - z0 + 0.002, cq.Vector(x, y, z0 - 0.001), cq.Vector(0, 0, 1))
            core, cavity = core.cut(hole), cavity.cut(hole)

    # Cooling channels: spanwise, cooling_offset beyond the part's z extremes, spread across y.
    cooling_r = 0.5 * design.cooling_diameter_m
    lanes = np.linspace(box.ymin + 0.15 * (box.ymax - box.ymin), box.ymax - 0.15 * (box.ymax - box.ymin), design.cooling_lanes)
    keep_clear = cooling_r + 0.5 * design.ejector_diameter_m + 0.004  # 4 mm of steel to the ejector line (3 mm required)
    lanes = [y_gate + np.copysign(keep_clear, y - y_gate) if abs(y - y_gate) < keep_clear else y for y in lanes]
    channels = {"core": [], "cavity": []}
    for y in lanes:
        for half, z in (("core", box.zmax + design.cooling_offset_m), ("cavity", box.zmin - design.cooling_offset_m)):
            channel = cq.Solid.makeCylinder(cooling_r, x1 - x0 + 0.002, cq.Vector(x0 - 0.001, float(y), z), cq.Vector(1, 0, 0))
            channels[half].append((float(y), z))
            if half == "core":
                core = core.cut(channel)
            else:
                cavity = cavity.cut(channel)

    metrics = _clearances(design, box, channels, ejectors, y_gate, x_runner_pin, design.ejector_radii_m)
    metrics.update(
        {
            "insert_size_mm": [round((x1 - x0) * 1000, 1), round((y1 - y0) * 1000, 1), round((z1 - z0) * 1000, 1)],
            "core_valid": bool(core.isValid()),
            "cavity_valid": bool(cavity.isValid()),
            "cavity_part_volume_cm3": _volume(cavity_part) * 1e6,
            "impression_volume_cm3": (impression_core + impression_cavity) * 1e6,
            # Measured before the bolt pins go in, so compared with the part without its holes.
            "impression_over_part_volume": (impression_core + impression_cavity) / _volume(outlines_solid(scaled_outlines)),
            "part_volume_fraction_in_cavity_half": impression_cavity / (impression_core + impression_cavity),
            "blanks_over_block_volume": (_volume(blank_core) + _volume(blank_cavity)) / block_volume,
            "shrink_flow": design.shrink_flow,
            "shrink_cross": design.shrink_cross,
            "gate_xyz_mm": [round(x_tab * 1000, 2), round(y_gate * 1000, 2), round(z_gate * 1000, 2)],
        }
    )
    return MoldResult(core, cavity, cavity_part, metrics)


def build_one_piece_mold(
    blade_outlines: Sequence[np.ndarray],
    boss_diameter_m: float,
    boss_thickness_m: float,
    z_centre_m: float,
    pin_holes: Sequence[tuple[float, float, float]] = (),
    design: MoldDesign = MoldDesign(),  # noqa: B008
    boss_draft_deg: float = 1.0,
    sprue_diameters_m: tuple[float, float] = (0.004, 0.006),
) -> MoldResult:
    """Core/cavity inserts for a one-piece two-blade prop (blades + drafted hub boss), centre-gated.

    Each blade's half-inserts are lofted from its section profiles exactly as for a single blade,
    and the other blade's are their 180 deg copy. The centre, between the blades' first stations,
    is a plain block split at the boss mid-plane. The drafted boss is then cut from both halves,
    and core pins for the motor holes (x, y, diameter) are added back, meeting at the parting plane.
    A sprue through the cavity (fixed) half feeds the boss centre, so the melt flows out to both
    tips and the fibres align spanwise. The centre bore is drilled after degating.
    """

    from prandtl_prop.cad.step import drafted_boss, one_piece_solid

    cq = _cq()
    scale = shrink_factors(design)
    outlines = [np.asarray(o) * scale for o in blade_outlines]
    cross = 1.0 + design.shrink_cross
    boss_d, boss_t, z_c = boss_diameter_m * cross, boss_thickness_m * cross, z_centre_m * cross
    cavity_part, _ = one_piece_solid(outlines, boss_d, boss_t, z_c, draft_deg=boss_draft_deg)
    box = cavity_part.BoundingBox()
    x1 = max(abs(box.xmin), abs(box.xmax)) + design.margin_m
    y1 = max(abs(box.ymin), abs(box.ymax)) + design.margin_m
    z0, z1 = box.zmin - design.margin_m, box.zmax + design.margin_m
    y_grid = np.linspace(-y1, y1, 2 * int(np.ceil(y1 / design.grid_step_m)) + 1)  # symmetric: the 180 deg copy matches
    x_first = float(outlines[0][0, 0])

    def halves(kind_upper: str, kind_lower: str):
        pieces = {}
        for name, kind, z_outer, z_lo, z_hi in (("core", kind_upper, z1, z_c, z1), ("cavity", kind_lower, z0, z0, z_c)):
            half = _half(outlines, (x_first, x1), y_grid, z_outer, kind, design)
            centre = cq.Solid.makeBox(2.0 * x_first, 2.0 * y1, z_hi - z_lo, cq.Vector(-x_first, -y1, z_lo))
            solid = half.fuse(half.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), 180.0)).fuse(centre).clean()
            pieces[name] = solid
        return pieces["core"], pieces["cavity"]

    core, cavity = halves("upper", "lower")
    blank_core, blank_cavity = halves("mid", "mid")  # accounting only: steel above/below the parting surface
    boss = drafted_boss(boss_d, boss_t, z_c, boss_draft_deg)
    core, cavity = core.cut(boss).clean(), cavity.cut(boss).clean()
    # Impression = blank - finished half. (Cutting the boss from the fused blanks as well was tried and
    # under-removed it: coplanar internal faces at the centre block confuse that boolean.)
    impression_core = _volume(blank_core) - _volume(core)
    impression_cavity = _volume(blank_cavity) - _volume(cavity)
    for x, y, diameter in pin_holes:
        r = 0.5 * diameter * cross
        core = core.fuse(
            cq.Solid.makeCylinder(r, z_c + 0.5 * boss_t + 1e-3 - z_c, cq.Vector(x * scale[0], y * cross, z_c), cq.Vector(0, 0, 1))
        )
        cavity = cavity.fuse(
            cq.Solid.makeCylinder(r, 0.5 * boss_t + 1e-3, cq.Vector(x * scale[0], y * cross, z_c - 0.5 * boss_t - 1e-3), cq.Vector(0, 0, 1))
        )
    core, cavity = core.clean(), cavity.clean()

    # Sprue: tapered, from the cavity insert's outer face to the boss underside at the centre.
    z_boss_bottom = z_c - 0.5 * boss_t
    sprue = cq.Solid.makeCone(
        0.5 * sprue_diameters_m[0], 0.5 * sprue_diameters_m[1], z_boss_bottom - z0 + 1.5e-3, cq.Vector(0, 0, z0 - 1e-3), cq.Vector(0, 0, 1)
    )
    cavity = cavity.cut(sprue)
    # Ejectors in the core (moving half), on the boss top between the motor holes.
    ejector_r = 0.5 * design.ejector_diameter_m
    ejectors = []
    hole_angles = [math.atan2(y, x) for x, y, _ in pin_holes] or [math.radians(45.0)]
    ring = 0.25 * boss_d
    for a in hole_angles:
        ex, ey = ring * math.cos(a + math.pi / 4), ring * math.sin(a + math.pi / 4)
        pin = cq.Solid.makeCylinder(
            ejector_r, z1 - (z_c + 0.5 * boss_t) + 2e-3, cq.Vector(ex, ey, z_c + 0.5 * boss_t - 0.5e-3), cq.Vector(0, 0, 1)
        )
        ejectors.append(pin)
        core = core.cut(pin)
    # Leader pins and cooling: as for the single-blade mold, along the full span.
    leader_r = 0.5 * design.leader_pin_diameter_m
    inset = 0.6 * design.margin_m
    for x in (-x1 + inset, x1 - inset):
        for y in (-y1 + inset, y1 - inset):
            hole = cq.Solid.makeCylinder(leader_r, z1 - z0 + 0.002, cq.Vector(x, y, z0 - 0.001), cq.Vector(0, 0, 1))
            core, cavity = core.cut(hole), cavity.cut(hole)
    cooling_r = 0.5 * design.cooling_diameter_m
    span = box.ymax - box.ymin
    lanes = np.linspace(box.ymin + 0.15 * span, box.ymax - 0.15 * span, design.cooling_lanes)
    keep_clear = cooling_r + max(ring, 0.5 * boss_d) + 0.004  # lanes pass beside the boss, its ejectors and the sprue
    lanes = [y if abs(y) >= keep_clear else math.copysign(keep_clear, y if y != 0 else 1.0) for y in lanes]
    for y in sorted(set(round(float(v), 6) for v in lanes)):
        core = core.cut(
            cq.Solid.makeCylinder(
                cooling_r, 2 * x1 + 0.002, cq.Vector(-x1 - 0.001, y, box.zmax + design.cooling_offset_m), cq.Vector(1, 0, 0)
            )
        )
        cavity = cavity.cut(
            cq.Solid.makeCylinder(
                cooling_r, 2 * x1 + 0.002, cq.Vector(-x1 - 0.001, y, box.zmin - design.cooling_offset_m), cq.Vector(1, 0, 0)
            )
        )
    part_volume = _volume(cavity_part)
    metrics = {
        "insert_size_mm": [round(2 * x1 * 1000, 1), round(2 * y1 * 1000, 1), round((z1 - z0) * 1000, 1)],
        "core_valid": bool(core.isValid()),
        "cavity_valid": bool(cavity.isValid()),
        "cavity_part_volume_cm3": part_volume * 1e6,
        "impression_volume_cm3": (impression_core + impression_cavity) * 1e6,
        "impression_over_part_volume": (impression_core + impression_cavity) / part_volume,
        "part_volume_fraction_in_cavity_half": impression_cavity / (impression_core + impression_cavity),
        "blanks_over_block_volume": (_volume(blank_core) + _volume(blank_cavity)) / (2 * x1 * 2 * y1 * (z1 - z0)),
        "cooling_to_part_steel_mm": (design.cooling_offset_m - cooling_r) * 1000,
        "clearance_ok": bool(design.cooling_offset_m - cooling_r >= 0.003),
        "gate": "centre sprue into the boss (drill the centre bore after degating)",
        "shrink_flow": design.shrink_flow,
        "shrink_cross": design.shrink_cross,
    }
    return MoldResult(core, cavity, cavity_part, metrics)


def _volume(shape) -> float:
    from prandtl_prop.cad.step import report

    return report(shape).volume_m3


def _clearances(design, box, channels, ejectors, y_gate, x_runner_pin, ejector_radii) -> dict:
    """Steel between cooling channels and the part, and between channels and ejector pins (must stay >= 3 mm)."""

    cooling_r, ejector_r = 0.5 * design.cooling_diameter_m, 0.5 * design.ejector_diameter_m
    to_part = design.cooling_offset_m - cooling_r
    lane_ys = [y for y, _ in channels["core"]]
    ejector_ys = [y_gate] * (len(ejector_radii) + 1)
    to_ejector = min(abs(ly - ey) for ly in lane_ys for ey in ejector_ys) - cooling_r - ejector_r
    return {
        "cooling_to_part_steel_mm": to_part * 1000,
        "cooling_to_ejector_steel_mm": to_ejector * 1000,
        "clearance_ok": bool(to_part >= 0.003 and to_ejector >= 0.003),
    }


def export_mold(result: MoldResult, folder: Path) -> dict[str, Path]:
    from prandtl_prop.cad.step import export_step, export_stl

    folder.mkdir(parents=True, exist_ok=True)
    paths = {
        "core_step": export_step(result.core, folder / "mold_core.step"),
        "cavity_step": export_step(result.cavity, folder / "mold_cavity.step"),
        "cavity_part_step": export_step(result.cavity_part, folder / "part_shrink_compensated.step"),
        "core_stl": export_stl(result.core, folder / "mold_core_m.stl", 2.0e-4),
        "cavity_stl": export_stl(result.cavity, folder / "mold_cavity_m.stl", 2.0e-4),
        "part_stl": export_stl(result.cavity_part, folder / "part_shrink_compensated_m.stl", 1.0e-4),
    }
    return paths


__all__ = [
    "MoldDesign",
    "MoldResult",
    "build_mold",
    "build_one_piece_mold",
    "export_mold",
    "half_chains",
    "parting_row",
    "shrink_factors",
]
