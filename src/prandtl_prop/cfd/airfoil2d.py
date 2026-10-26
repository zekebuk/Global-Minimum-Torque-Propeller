"""2D airfoil RANS in OpenFOAM, fully turbulent (k-omega SST) or transitional (k-omega SST-LM, gamma-Re_theta).

Purpose: an independent check of the section polars at the prop's Reynolds numbers. The 3D MRF
hover CFD runs k-omega SST with wall functions, i.e. a turbulent boundary layer from the leading
edge; XFOIL resolves the laminar run, the laminar separation bubble, and transition. Running the
same section both ways in 2D, wall-resolved, separates "turbulence model" from "3D effects" in the
3D CFD / BEMT gap.

Model (OpenFOAM v1912, incompressible ``simpleFoam``):

- Mesh: gmsh, chord 1, O-grid far field at 15 c, a structured boundary-layer block (first cell
  ``first_cell_c`` ~ y+ 1 at Re 1e5, growth 1.12), unstructured triangles outside, one cell thick
  and ``empty`` front and back.
- Flow: U = 1 at incidence alpha (the airfoil does not move), nu = 1 / Re.
- Turbulence: ``kOmegaSST`` or ``kOmegaSSTLM`` (Langtry-Menter 2009). The freestream intensity
  Tu sets the transition onset; it maps to XFOIL's e^N amplification factor through Mack's relation
  Ncrit = -8.43 - 2.4 ln(Tu), so Tu = 0.17 % corresponds to Ncrit = 7. The inlet Re_theta_t comes
  from the Langtry-Menter correlation Re_theta_t = 1173.51 - 589.428 Tu + 0.2196 / Tu^2 (Tu in %).
  Eddy-viscosity ratio 1 at the far field limits the decay of k across the domain.
- Forces: integrated from the written p, U, nut fields over the wall faces (``openfoam.wall_loads``,
  pressure plus nu_eff du/dn with the first cell at y+ ~ 1), projected on the lift and drag
  directions, averaged over the last three of ten writes (laminar bubbles can make a steady solve
  unsteady; the scatter is reported).
"""

from __future__ import annotations

import math
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from prandtl_prop.cfd.openfoam import FOAM_HEADER, _default_bashrc


@dataclass(frozen=True)
class Airfoil2DCase:
    reynolds: float = 1.0e5
    alpha_deg: float = 4.0
    model: str = "kOmegaSSTLM"  # or kOmegaSST
    turbulence_intensity: float = 0.0017
    first_cell_c: float = 4.0e-5
    layers: int = 40
    iterations: int = 6000
    transient: bool = False  # URANS (pimpleFoam): laminar separation bubbles shed; average over the second half
    end_time_c_over_u: float = 12.0  # transient run length in chord flow-through times
    time_step_c_over_u: float = 0.002
    processors: int = 4


def mack_ncrit(tu: float) -> float:
    return -8.43 - 2.4 * math.log(tu)


def re_theta_t(tu: float) -> float:
    t = 100.0 * tu
    return 1173.51 - 589.428 * t + 0.2196 / t**2 if t <= 1.3 else 331.5 * (t - 0.5658) ** -0.671


def write_mesh(points: np.ndarray, case: Path, spec: Airfoil2DCase) -> None:
    """gmsh boundary-layer mesh of the closed outline ``points`` (Selig order, chord 1) -> OpenFOAM polyMesh."""

    import gmsh

    gmsh.initialize()
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.add("airfoil")
    occ = gmsh.model.geo
    pts = points[:-1] if np.allclose(points[0], points[-1]) else points
    te_closed = np.linalg.norm(pts[0] - pts[-1]) < 1e-6
    if te_closed:
        pts = pts[:-1]
    tags = [occ.addPoint(float(x), float(y), 0.0, 0.01) for x, y in pts]
    le = int(np.argmin(pts[:, 0]))
    upper = occ.addSpline(tags[: le + 1])
    lower = occ.addSpline(tags[le:] + ([tags[0]] if te_closed else []))
    curves = [upper, lower]
    if not te_closed:
        curves.append(occ.addLine(tags[-1], tags[0]))  # blunt trailing edge
    loop = occ.addCurveLoop(curves)
    radius = 15.0
    c0 = occ.addPoint(0.5, 0.0, 0.0, 1.0)
    far = [occ.addPoint(0.5 + radius * math.cos(a), radius * math.sin(a), 0.0, 1.5) for a in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2)]
    arcs = [occ.addCircleArc(far[i], c0, far[(i + 1) % 4]) for i in range(4)]
    outer = occ.addCurveLoop(arcs)
    surface = occ.addPlaneSurface([outer, loop])
    occ.synchronize()
    field = gmsh.model.mesh.field
    bl = field.add("BoundaryLayer")
    field.setNumbers(bl, "CurvesList", curves)
    field.setNumber(bl, "Size", spec.first_cell_c)
    field.setNumber(bl, "Ratio", 1.12)
    field.setNumber(bl, "NbLayers", spec.layers)
    field.setNumber(bl, "Quads", 1)
    field.setNumbers(bl, "FanPointsList", [tags[0]] + ([] if te_closed else [tags[-1]]))
    field.setAsBoundaryLayer(bl)
    dist = field.add("Distance")
    field.setNumbers(dist, "CurvesList", curves)
    thr = field.add("Threshold")
    field.setNumber(thr, "InField", dist)
    field.setNumber(thr, "SizeMin", 0.004)
    field.setNumber(thr, "SizeMax", 1.5)
    field.setNumber(thr, "DistMin", 0.05)
    field.setNumber(thr, "DistMax", 8.0)
    field.setAsBackgroundMesh(thr)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)
    ext = occ.extrude([(2, surface)], 0, 0, 0.1, numElements=[1], recombine=True)
    occ.synchronize()
    volume = [e[1] for e in ext if e[0] == 3]
    front = ext[0][1]
    sides = [e[1] for e in ext if e[0] == 2][1:]
    # extruded side surfaces come in the order of the surface's boundary curves: 4 arcs, then the airfoil curves
    gmsh.model.addPhysicalGroup(3, volume, name="fluid")
    gmsh.model.addPhysicalGroup(2, [surface, front], name="frontAndBack")
    gmsh.model.addPhysicalGroup(2, sides[:4], name="farfield")
    gmsh.model.addPhysicalGroup(2, sides[4:], name="airfoil")
    gmsh.model.mesh.generate(3)
    gmsh.option.setNumber("Mesh.MshFileVersion", 2.2)
    msh = case / "airfoil.msh"
    gmsh.write(str(msh))
    cells = len(gmsh.model.mesh.getElementsByType(5)[0]) + len(gmsh.model.mesh.getElementsByType(6)[0])
    gmsh.finalize()
    run(case, f"gmshToFoam {msh.name}", "gmshToFoam")
    boundary = case / "constant" / "polyMesh" / "boundary"
    text = boundary.read_text()
    text = re.sub(r"(frontAndBack\s*\{\s*type\s+)\w+", r"\1empty", text)
    text = re.sub(r"(airfoil\s*\{\s*type\s+)\w+", r"\1wall", text)
    text = re.sub(r"(farfield\s*\{\s*type\s+)\w+", r"\1patch", text)
    boundary.write_text(text)
    (case / "cells.txt").write_text(str(cells))


def run(case: Path, command: str, log: str) -> None:
    script = f"source {_default_bashrc()} >/dev/null 2>&1; cd {case} && {command} > log.{log} 2>&1"
    subprocess.run(["bash", "-c", script], check=True)


def _field(case: Path, name: str, cls: str, internal: str, patches: dict[str, str]) -> None:
    body = f"\ndimensions {internal[0]};\ninternalField uniform {internal[1]};\nboundaryField\n{{\n"
    for patch, bc in patches.items():
        body += f"    {patch} {{ {bc} }}\n"
    body += "}\n"
    path = case / "0" / name
    path.write_text(FOAM_HEADER.format(cls=cls, obj=name) + body, encoding="utf-8")


def write_case(case: Path, spec: Airfoil2DCase) -> None:
    a = math.radians(spec.alpha_deg)
    u = (math.cos(a), math.sin(a), 0.0)
    nu = 1.0 / spec.reynolds
    k = 1.5 * (spec.turbulence_intensity * 1.0) ** 2
    omega = k / nu  # eddy-viscosity ratio 1
    (case / "0").mkdir(parents=True, exist_ok=True)
    empty = {"frontAndBack": "type empty;"}
    U = f"({u[0]:.8f} {u[1]:.8f} 0)"
    _field(
        case,
        "U",
        "volVectorField",
        ("[0 1 -1 0 0 0 0]", U),
        {"farfield": f"type freestreamVelocity; freestreamValue uniform {U};", "airfoil": "type noSlip;", **empty},
    )
    _field(
        case,
        "p",
        "volScalarField",
        ("[0 2 -2 0 0 0 0]", "0"),
        {"farfield": "type freestreamPressure; freestreamValue uniform 0;", "airfoil": "type zeroGradient;", **empty},
    )
    _field(
        case,
        "k",
        "volScalarField",
        ("[0 2 -2 0 0 0 0]", f"{k:.6e}"),
        {"farfield": f"type inletOutlet; inletValue uniform {k:.6e};", "airfoil": "type fixedValue; value uniform 1e-14;", **empty},
    )
    _field(
        case,
        "omega",
        "volScalarField",
        ("[0 0 -1 0 0 0 0]", f"{omega:.6e}"),
        {"farfield": f"type inletOutlet; inletValue uniform {omega:.6e};", "airfoil": "type omegaWallFunction; value uniform 1;", **empty},
    )
    _field(
        case,
        "nut",
        "volScalarField",
        ("[0 2 -1 0 0 0 0]", "0"),
        {"farfield": "type calculated; value uniform 0;", "airfoil": "type nutLowReWallFunction; value uniform 0;", **empty},
    )
    if spec.model == "kOmegaSSTLM":
        rt = re_theta_t(spec.turbulence_intensity)
        _field(
            case,
            "gammaInt",
            "volScalarField",
            ("[0 0 0 0 0 0 0]", "1"),
            {"farfield": "type inletOutlet; inletValue uniform 1;", "airfoil": "type zeroGradient;", **empty},
        )
        _field(
            case,
            "ReThetat",
            "volScalarField",
            ("[0 0 0 0 0 0 0]", f"{rt:.3f}"),
            {"farfield": f"type inletOutlet; inletValue uniform {rt:.3f};", "airfoil": "type zeroGradient;", **empty},
        )
    const = case / "constant"
    const.mkdir(exist_ok=True)
    (const / "transportProperties").write_text(
        FOAM_HEADER.format(cls="dictionary", obj="transportProperties") + f"\ntransportModel Newtonian;\nnu {nu:.6e};\n"
    )
    (const / "turbulenceProperties").write_text(
        FOAM_HEADER.format(cls="dictionary", obj="turbulenceProperties")
        + f"\nsimulationType RAS;\nRAS {{ RASModel {spec.model}; turbulence on; printCoeffs on; }}\n"
    )
    system = case / "system"
    system.mkdir(exist_ok=True)
    if spec.transient:
        app, ddt, delta_t, end_time = "pimpleFoam", "backward", spec.time_step_c_over_u, spec.end_time_c_over_u
        write_interval, write_control = end_time / 24.0, "runTime"
    else:
        app, ddt, delta_t, end_time = "simpleFoam", "steadyState", 1, spec.iterations
        write_interval, write_control = max(1, spec.iterations // 10), "timeStep"
    (system / "controlDict").write_text(
        FOAM_HEADER.format(cls="dictionary", obj="controlDict")
        + f"""
application     {app};
startFrom       latestTime;
startTime       0;
stopAt          endTime;
endTime         {end_time};
deltaT          {delta_t};
writeControl    {write_control};
writeInterval   {write_interval};
purgeWrite      0;
writeFormat     ascii;
writePrecision  10;
writeCompression off;
timeFormat      general;
timePrecision   6;
runTimeModifiable true;
functions {{}}  // this OpenFOAM build cannot hash function-object dictionaries; forces come from the fields (openfoam.wall_loads)
""",
        encoding="utf-8",
    )
    div = "bounded Gauss linearUpwind grad(U)"
    (system / "fvSchemes").write_text(
        FOAM_HEADER.format(cls="dictionary", obj="fvSchemes")
        + f"""
ddtSchemes {{ default {ddt}; }}
gradSchemes {{ default Gauss linear; grad(U) cellLimited Gauss linear 1; }}
divSchemes
{{
    default none;
    div(phi,U) {div};
    div(phi,k) bounded Gauss limitedLinear 1;
    div(phi,omega) bounded Gauss limitedLinear 1;
    div(phi,gammaInt) bounded Gauss limitedLinear 1;
    div(phi,ReThetat) bounded Gauss limitedLinear 1;
    div((nuEff*dev2(T(grad(U))))) Gauss linear;
}}
laplacianSchemes {{ default Gauss linear corrected; }}
interpolationSchemes {{ default linear; }}
snGradSchemes {{ default corrected; }}
wallDist {{ method meshWave; }}
""",
        encoding="utf-8",
    )
    (system / "fvSolution").write_text(
        FOAM_HEADER.format(cls="dictionary", obj="fvSolution")
        + """
solvers
{
    p { solver GAMG; smoother GaussSeidel; tolerance 1e-8; relTol 0.05; }
    pFinal { solver GAMG; smoother GaussSeidel; tolerance 1e-8; relTol 0; }
    "(U|k|omega|gammaInt|ReThetat)Final" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0; }
    "(U|k|omega|gammaInt|ReThetat)" { solver smoothSolver; smoother symGaussSeidel; tolerance 1e-9; relTol 0.05; }
}
SIMPLE { nNonOrthogonalCorrectors 1; consistent yes; residualControl { p 1e-7; U 1e-8; } }
PIMPLE { nOuterCorrectors 1; nCorrectors 2; nNonOrthogonalCorrectors 1; }
relaxationFactors { equations { U 0.7; k 0.5; omega 0.5; gammaInt 0.5; ReThetat 0.5; ".*Final" 1; ".*" 0.5; } fields { p 0.3; "pFinal" 1; } }
""",
        encoding="utf-8",
    )
    (system / "decomposeParDict").write_text(  # hierarchical ships with every OpenFOAM build (scotch may be a stub)
        FOAM_HEADER.format(cls="dictionary", obj="decomposeParDict")
        + f"\nnumberOfSubdomains {spec.processors};\nmethod hierarchical;\nhierarchicalCoeffs {{ n ({spec.processors} 1 1); order xyz; }}\n"
    )


def read_coefficients(case: Path, spec: Airfoil2DCase, last: int | None = None) -> dict[str, float]:
    from prandtl_prop.cfd.openfoam import read_mesh, wall_loads

    mesh = read_mesh(case)
    times = sorted(
        (d for d in case.iterdir() if d.is_dir() and d.name.replace(".", "", 1).isdigit() and float(d.name) > 0),
        key=lambda d: float(d.name),
    )
    if not times:
        raise FileNotFoundError(f"no solution written under {case}")
    a = math.radians(spec.alpha_deg)
    lift_dir, drag_dir = np.array([-math.sin(a), math.cos(a), 0.0]), np.array([math.cos(a), math.sin(a), 0.0])
    span = float(np.ptp(mesh.points[:, 2]))
    q = 0.5 * 1.0 * 1.0**2 * 1.0 * span
    rows = []
    last = last or (len(times) // 2 if spec.transient else 3)  # transient: the second half of the run
    for t in times[-last:]:
        loads = wall_loads(case, t, rho_kg_m3=1.0, nu_m2_s=1.0 / spec.reynolds, patch="airfoil", mesh=mesh)
        force = loads.force_N.sum(axis=0)
        arm = loads.centres - np.array([0.25, 0.0, 0.0])
        moment = float((arm[:, 0] * loads.force_N[:, 1] - arm[:, 1] * loads.force_N[:, 0]).sum())
        rows.append((force @ lift_dir / q, force @ drag_dir / q, moment / q, loads.viscous_force_N.sum(axis=0) @ drag_dir / q))
    data = np.array(rows)
    return {
        "iterations": float(times[-1].name),
        "samples": len(rows),
        "cl": float(data[:, 0].mean()),
        "cd": float(data[:, 1].mean()),
        "cm": float(data[:, 2].mean()),
        "cd_friction": float(data[:, 3].mean()),
        "cl_scatter": float(data[:, 0].std()),
        "cd_scatter": float(data[:, 1].std()),
    }


def solve(points: np.ndarray, case: Path, spec: Airfoil2DCase) -> dict[str, float]:
    case.mkdir(parents=True, exist_ok=True)
    write_case(case, spec)
    write_mesh(points, case, spec)
    if spec.processors > 1:
        run(case, "decomposePar -force", "decomposePar")
        app = "pimpleFoam" if spec.transient else "simpleFoam"
        run(case, f"mpirun --allow-run-as-root --oversubscribe -np {spec.processors} {app} -parallel", "solver")
        run(case, "reconstructPar -newTimes", "reconstructPar")
    else:
        run(case, "pimpleFoam" if spec.transient else "simpleFoam", "solver")
    out = read_coefficients(case, spec)
    out.update(
        {
            "cells": int((case / "cells.txt").read_text()),
            "model": spec.model,
            "reynolds": spec.reynolds,
            "alpha_deg": spec.alpha_deg,
            "tu": spec.turbulence_intensity,
            "ncrit_equivalent": mack_ncrit(spec.turbulence_intensity),
        }
    )
    return out
