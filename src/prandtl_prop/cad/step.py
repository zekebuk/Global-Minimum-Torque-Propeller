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


def export_step(shape, path: Path) -> Path:
    cq = _cq()
    path.parent.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(cq.Workplane().add(shape), str(path), exportType=cq.exporters.ExportTypes.STEP)
    return path


def export_stl(shape, path: Path, tolerance_m: float = 1.0e-4) -> Path:
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
