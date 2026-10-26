"""CadQuery solid construction and STEP/STL export for blade sections.

CadQuery (OpenCascade) is an optional dependency: ``pip install -e .[cad]``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from prandtl_prop.cad.sections import BladeSection


@dataclass(frozen=True)
class SolidReport:
    volume_m3: float
    center_of_mass_m: tuple[float, float, float]
    bounding_box_m: tuple[float, float, float, float, float, float]
    is_valid: bool


def _cq():
    try:
        import cadquery as cq
    except ImportError as exc:  # pragma: no cover - exercised only without the optional dependency
        raise ImportError("CAD export needs CadQuery: pip install -e .[cad]") from exc
    return cq


def section_wire(points: np.ndarray):
    """Closed wire through one section: a spline over the outline, plus a TE segment if the TE is open."""

    cq = _cq()
    vectors = [cq.Vector(*map(float, point)) for point in points]
    trailing_gap = vectors[0].sub(vectors[-1]).Length
    if trailing_gap < 1.0e-9:
        return cq.Wire.assembleEdges([cq.Edge.makeSpline(vectors[:-1] + [vectors[0]])])
    return cq.Wire.assembleEdges([cq.Edge.makeSpline(vectors), cq.Edge.makeLine(vectors[-1], vectors[0])])


def blade_solid(sections: Sequence[BladeSection], hub_radius_m: float | None = None):
    """Loft the sections into one blade solid; optionally extend the root section into a hub stub."""

    cq = _cq()
    wires = [section_wire(section.points_3d()) for section in sections]
    if hub_radius_m is not None and hub_radius_m < sections[0].r_m:
        stub = sections[0].points_3d().copy()
        stub[:, 0] = 0.9 * hub_radius_m
        wires.insert(0, section_wire(stub))
        ruled_root = cq.Solid.makeLoft(wires[:2], True)
        body = cq.Solid.makeLoft(wires[1:], False)
        return ruled_root.fuse(body).clean()
    return cq.Solid.makeLoft(wires, False)


def loft_outlines(outlines: Sequence[np.ndarray], max_spacing_ratio: float = 4.0):
    """Loft through explicit 3D outlines (root to tip), all with the same point count.

    Runs of consecutive outlines with identical (y, z) shape (a prismatic tab or hub) are joined by
    ruled lofts, the rest by smooth lofts, and the pieces fused on their shared planar faces. A smooth
    B-spline loft through identical sections and on into changing ones is ill-conditioned: it passed
    the validity check but even a plane cut through it failed. Strongly uneven section spacing has
    the same effect (the B-spline overshoots between sections), so within a smooth run a jump in the
    gap between adjacent sections larger than ``max_spacing_ratio`` is rejected.
    """

    cq = _cq()
    wires = [section_wire(points) for points in outlines]
    prism = [np.allclose(outlines[i + 1][:, 1:], outlines[i][:, 1:], rtol=0.0, atol=1.0e-9) for i in range(len(outlines) - 1)]
    runs: list[tuple[bool, int, int]] = []
    for i, is_prism in enumerate(prism):
        if runs and runs[-1][0] == is_prism:
            runs[-1] = (is_prism, runs[-1][1], i + 1)
        else:
            runs.append((is_prism, i, i + 1))
    pieces = []
    for is_prism, start, end in runs:
        if is_prism:
            pieces.append(cq.Solid.makeLoft([wires[start], wires[end]], True))
            continue
        spacing = np.diff([float(points[0, 0]) for points in outlines[start : end + 1]])
        jumps = np.maximum(spacing[1:] / spacing[:-1], spacing[:-1] / spacing[1:]) if len(spacing) > 1 else np.ones(1)
        if jumps.max() > max_spacing_ratio:
            raise ValueError(
                f"Adjacent section gaps differ {jumps.max():.1f}x (limit {max_spacing_ratio}); "
                "the smooth loft overshoots between unevenly spaced sections. Add sections or even out the spacing."
            )
        pieces.append(cq.Solid.makeLoft(wires[start : end + 1], False))
    solid = pieces[0]
    for piece in pieces[1:]:
        solid = solid.fuse(piece)
    return solid.clean() if len(pieces) > 1 else solid


def full_blade_solid(sections: Sequence[BladeSection], root_geometry):
    """Blade with clamped root tab and transition (see ``prandtl_prop.cad.root``), bolt holes cut along z."""

    from prandtl_prop.cad.root import bolt_centres, full_blade_outlines

    return outlines_solid(
        full_blade_outlines(sections, root_geometry),
        bolt_centres(sections, root_geometry),
        root_geometry.bolt_diameter_m,
        4.0 * root_geometry.tab_thickness_m,
    )


def drafted_boss(diameter_m: float, thickness_m: float, z_centre_m: float, draft_deg: float):
    """Disc of the given diameter at its mid-plane, tapering by ``draft_deg`` toward both faces."""

    cq = _cq()
    r_mid = 0.5 * diameter_m
    r_face = r_mid - 0.5 * thickness_m * math.tan(math.radians(draft_deg))
    up = cq.Solid.makeCone(r_mid, r_face, 0.5 * thickness_m, cq.Vector(0, 0, z_centre_m), cq.Vector(0, 0, 1))
    down = cq.Solid.makeCone(r_mid, r_face, 0.5 * thickness_m, cq.Vector(0, 0, z_centre_m), cq.Vector(0, 0, -1))
    return up.fuse(down).clean()


def one_piece_solid(
    blade_outlines: Sequence[np.ndarray],
    boss_diameter_m: float,
    boss_thickness_m: float,
    z_centre_m: float,
    holes: Sequence[tuple[float, float, float]] = (),
    hole_diameters_m: Sequence[float] = (),
    draft_deg: float = 1.0,
    blade_count: int = 2,
):
    """One-piece prop: a hub boss plus the blade loft and its copies every 360 / B deg.

    The boss is two frustums meeting at its mid-plane (the mold's parting plane), each side drafted
    by ``draft_deg`` so it releases along the rotor axis.

    The blade's root tab starts inside the boss and is thinner than it, so no faces coincide: the tab
    faces lie inside the boss and the tab sides cut its cylinder transversally (fusing two lofted
    halves on a shared plane at the axis was tried and failed). Holes run along z through the boss.
    Returns (prop, sector): the sector is blade 1 plus the boss within +-180 / B deg of +x (the
    half x >= 0 for two blades), for the FEA.
    """

    cq = _cq()
    blade = loft_outlines(blade_outlines)
    boss = cq.Solid.makeCylinder(
        0.5 * boss_diameter_m, boss_thickness_m, cq.Vector(0, 0, z_centre_m - 0.5 * boss_thickness_m), cq.Vector(0, 0, 1)
    )
    prop = boss.fuse(blade)
    for index in range(1, blade_count):
        prop = prop.fuse(blade.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), 360.0 * index / blade_count))
    prop = prop.clean()
    for (x, y, z), diameter in zip(holes, hole_diameters_m, strict=True):
        length = 2.0 * boss_thickness_m
        prop = prop.cut(cq.Solid.makeCylinder(0.5 * diameter, length, cq.Vector(x, y, z - 0.5 * length), cq.Vector(0, 0, 1)))
    prop = prop.clean()
    side = 2.0 * float(np.max(np.abs(np.vstack(blade_outlines))))
    if blade_count == 2:
        wedge = cq.Solid.makeBox(side, 2.0 * side, 2.0 * side, cq.Vector(0.0, -side, -side))
    else:
        angles = np.linspace(-math.pi / blade_count, math.pi / blade_count, 9)
        corner = side / math.cos(math.pi / 8)  # polygon circumscribes the arc of radius `side`
        points = [(0.0, 0.0)] + [(corner * math.cos(a), corner * math.sin(a)) for a in angles]
        wedge = cq.Workplane("XY", origin=(0, 0, -side)).polyline(points).close().extrude(2.0 * side).val()
    sector = prop.intersect(wedge).clean()
    return prop, sector


def outlines_solid(
    outlines: Sequence[np.ndarray],
    holes: Sequence[tuple[float, float, float]] = (),
    hole_diameter_m: float = 0.0,
    hole_length_m: float = 0.0,
):
    """Loft explicit outlines and cut round holes along z, centred on each (x, y, z) in ``holes``."""

    cq = _cq()
    blade = loft_outlines(outlines)
    for x, y, z in holes:
        hole = cq.Solid.makeCylinder(0.5 * hole_diameter_m, hole_length_m, cq.Vector(x, y, z - 0.5 * hole_length_m), cq.Vector(0, 0, 1))
        blade = blade.cut(hole)
    return blade.clean()


def rotor_solid(blade, blade_count: int, hub_radius_m: float | None = None, hub_height_m: float | None = None):
    """Pattern the blade about +z; add a hub cylinder centred on z = 0 when a hub radius is given."""

    cq = _cq()
    shapes = [blade.rotate(cq.Vector(0, 0, 0), cq.Vector(0, 0, 1), 360.0 * index / blade_count) for index in range(blade_count)]
    rotor = shapes[0]
    for shape in shapes[1:]:
        rotor = rotor.fuse(shape)
    if hub_radius_m is not None:
        height = hub_height_m if hub_height_m is not None else 2.0 * hub_radius_m
        hub = cq.Solid.makeCylinder(hub_radius_m, height, cq.Vector(0, 0, -height / 2.0), cq.Vector(0, 0, 1))
        rotor = rotor.fuse(hub)
    return rotor.clean()


def report(shape, relative_tolerance: float = 1.0e-9) -> SolidReport:
    """Mass properties via tolerance-driven integration; CadQuery's default ``Volume()`` is coarse (~0.2% error)."""

    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, properties, relative_tolerance)
    center = properties.CentreOfMass()
    box = shape.BoundingBox()
    return SolidReport(
        volume_m3=float(properties.Mass()),
        center_of_mass_m=(float(center.X()), float(center.Y()), float(center.Z())),
        bounding_box_m=(box.xmin, box.xmax, box.ymin, box.ymax, box.zmin, box.zmax),
        is_valid=bool(shape.isValid()),
    )


def inertia_zz_about_cg(shape, density_kg_m3: float) -> float:
    """Mass moment of inertia about the z axis through the centre of mass (kg m^2)."""

    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps

    properties = GProp_GProps()
    BRepGProp.VolumeProperties_s(shape.wrapped, properties, 1.0e-9)
    return float(properties.MatrixOfInertia().Value(3, 3)) * density_kg_m3


def export_step(shape, path: Path) -> Path:
    """Write STEP at true size in millimetres.

    The model is built in metres, but OpenCascade's STEP writer labels the file in millimetres, so
    the shape is scaled by 1000 first. SolidWorks and other CAD tools then import it at true size.
    """

    cq = _cq()
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(cq.Workplane().add(shape.scale(1000.0)), str(path), exportType=cq.exporters.ExportTypes.STEP)
    return path


def import_step_m(path: Path):
    """Read a STEP written by ``export_step`` back into metres."""

    cq = _cq()
    return cq.importers.importStep(str(path)).val().scale(1.0e-3)


def export_stl(shape, path: Path, tolerance_m: float = 1.0e-4) -> Path:
    """Write STL in metres (STL carries no units; OpenFOAM and CalculiX use SI)."""

    cq = _cq()
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(
        cq.Workplane().add(shape),
        str(path),
        exportType=cq.exporters.ExportTypes.STL,
        tolerance=tolerance_m,
        angularTolerance=math.radians(5.0),
    )
    return path


def write_solidworks_curves(sections: Sequence[BladeSection], directory: Path) -> list[Path]:
    """Write one closed XYZ curve per section, plus LE and TE guide curves, in millimetres.

    SolidWorks: Insert > Curve > Curve Through XYZ Points > Browse (one file per curve), then
    Lofted Boss/Base through the section curves in order, with the LE/TE curves as guides.
    """

    directory.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    leading: list[np.ndarray] = []
    trailing: list[np.ndarray] = []
    for index, section in enumerate(sections):
        points = section.points_3d() * 1000.0
        leading.append(points[len(section.airfoil.x) - 1])
        trailing.append(0.5 * (points[0] + points[-1]))
        closed = np.vstack([points, points[:1]])
        path = directory / f"section_{index:02d}_r{section.r_m * 1000.0:07.2f}mm.sldcrv"
        path.write_text("".join(f"{x:.6f}\t{y:.6f}\t{z:.6f}\n" for x, y, z in closed), encoding="utf-8")
        written.append(path)
    for name, points in (("guide_leading_edge", leading), ("guide_trailing_edge", trailing)):
        path = directory / f"{name}.sldcrv"
        path.write_text("".join(f"{x:.6f}\t{y:.6f}\t{z:.6f}\n" for x, y, z in points), encoding="utf-8")
        written.append(path)
    return written
