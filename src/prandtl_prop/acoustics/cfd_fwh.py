"""Tonal noise from a CFD solution: Farassat 1A on the MRF wall surface (independent of BEMT).

The MRF solution is steady in the rotating frame, so the blade-surface pressure is a steady load
that rotates with the rotor: exactly the loading the hover FW-H treats. Every wall face of the
rotor patch (all blades and the hub) is a panel; OpenFOAM's boundary normals point out of the fluid,
so the FW-H outward body normal is minus the face area vector. Kinematic pressure times the
density gives the gauge pressure. The same panels give the thickness noise of the real surface.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

import numpy as np

from prandtl_prop.acoustics import fwh
from prandtl_prop.acoustics.rotor import P_REF, AcousticsSpec, observers


def cfd_surface_noise(
    case: Path, rpm: float, radius_m: float, blade_count: int, rho: float, spec: AcousticsSpec, patch: str = "blade"
) -> dict[str, Any]:
    from prandtl_prop.cfd import openfoam

    mesh = openfoam.read_mesh(case)
    time = openfoam.solution_times(case)[-1]
    faces = list(mesh.patch_faces(patch))
    centres, area_vectors = openfoam.face_geometry(mesh, faces)
    p = openfoam.read_field(time / "p", 1)
    p_wall = p.boundary.get(patch)
    p_wall = p.internal[mesh.owner[faces]] if p_wall is None else np.asarray(p_wall).reshape(-1)
    areas = np.linalg.norm(area_vectors, axis=1)
    normals = -area_vectors / areas[:, None]
    obs, distance = observers(spec, radius_m)
    history = fwh.pressure_history(
        fwh.RotorSources(panel_centroids_m=centres, panel_normals=normals, panel_areas_m2=areas, panel_pressures_Pa=rho * p_wall),
        obs,
        omega_rad_s=rpm * math.pi / 30.0,
        blade_count=1,  # the patch holds every blade already
        c0=spec.speed_of_sound_m_s,
        rho0=rho,
        samples_per_rev=spec.samples_per_rev,
    )
    harmonics = spec.harmonics * blade_count
    loading = fwh.harmonics(history["loading"], 1, harmonics)[:, blade_count - 1 :: blade_count]
    thickness = fwh.harmonics(history["thickness"], 1, harmonics)[:, blade_count - 1 :: blade_count]
    total = fwh.harmonics(history["loading"] + history["thickness"], 1, harmonics)[:, blade_count - 1 :: blade_count]
    force = (rho * p_wall[:, None] * normals * areas[:, None]).sum(axis=0)  # pressure force on the fluid
    return {
        "loading": loading,
        "thickness": thickness,
        "total": total,
        "observer_distance_m": distance,
        "elevations_deg": np.array(spec.elevations_deg),
        "pressure_thrust_N": float(-force[2]),
        "faces": len(faces),
    }


def spl(amplitude_rms: np.ndarray) -> np.ndarray:
    return 20.0 * np.log10(np.maximum(amplitude_rms, 1e-30) / P_REF)
