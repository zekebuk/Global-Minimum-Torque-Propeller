"""Section polars from XFOIL for the sections actually molded, with transition and post-stall extension.

Why. The energy audit (docs/efficiency.md) found blade profile drag to be the largest avoidable loss
and section drag the largest uncertainty. At the Reynolds numbers of a 10 in prop (Re ~ 0.3-2e5) the
boundary layer is laminar over much of the chord and separates in a laminar bubble before
transition; a fully turbulent model (the k-omega SST CFD the analytic polar was calibrated to) misses
that and roughly doubles the drag. XFOIL's viscous-inviscid interaction with e^N transition
(Drela & Giles 1987; Drela 1989) is the standard low-Re section tool and resolves the bubble.

What is solved:

- **Geometry.** Each station's real molded section: the design airfoil with the thickness scale of
  ``airfoils.thickness_scale`` and the blunt trailing edge ``airfoils.trailing_edge_thickness_m``
  (``manufacturing.injection.modify_airfoil``), so the blunt-TE base drag and the thickened root
  are in the polar. Stations are grouped by (thickness scale, TE thickness / chord) rounded to
  ``thickness_bin`` and ``te_bin``; each group gets one airfoil key and one polar set, and the
  sweeps run in parallel.
- **Flow.** Re sweeps (``reynolds``) at the stations' Mach number range neglected (M = 0; tip Mach
  is ~0.3 in hover). Transition: free, e^N with ``ncrit`` (9: clean wind tunnel; 5-7: the turbulence
  and vibration of a rotor and a molded surface; the pipeline default 7). Alpha is swept up and down
  from 0 deg separately so each branch starts from a converged boundary layer.
- **Post-stall.** XFOIL stops converging past stall. The polar is extended to +-180 deg with the
  Viterna-Corrigan method (Viterna & Corrigan 1982, as in NREL AeroDyn): from the last converged
  point (alpha_s, Cl_s, Cd_s) with Cd_max = 1.11 + 0.018 AR (AR = 10 for a blade),
      Cd = B1 sin^2 a + B2 cos a,       Cl = A1 sin 2a + A2 cos^2 a / sin a,
  blending to the flat-plate values, so stall raises drag instead of clamping lift (the analytic
  polar's weakness in edgewise flight).

Output: ``<KEY>_Re<N>_polar.csv`` files that ``aero.polars.TablePolar`` reads, plus the stations
with their airfoil keys. Runs are cached on disk by geometry and settings.
"""

from __future__ import annotations

import csv
import hashlib
import math
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from prandtl_prop.cad.airfoils import Airfoil

XFOIL_CANDIDATES = ("xfoil-nofpe", "xfoil")


def find_xfoil() -> str | None:
    """The XFOIL executable: $PRANDTL_XFOIL, else a build without FP traps, else ``xfoil``.

    Debian/Ubuntu's packaged xfoil is compiled with -ffpe-trap and dies on the first viscous
    iteration; build it from the source package without those flags (docs/cfd_fea_acoustics_methods.md).
    """

    explicit = os.environ.get("PRANDTL_XFOIL")
    if explicit:
        return explicit
    for name in XFOIL_CANDIDATES:
        path = shutil.which(name)
        if path:
            return path
    return None


@dataclass(frozen=True)
class PolarSettings:
    reynolds: tuple[float, ...] = (40e3, 70e3, 120e3, 200e3, 350e3)
    ncrit: float = 7.0
    xtr_top: float = 1.0  # forced transition x/c (1: free transition by e^N; 0.05: tripped, ~fully turbulent)
    xtr_bot: float = 1.0
    alpha_min_deg: float = -8.0
    alpha_max_deg: float = 20.0
    alpha_step_deg: float = 0.5
    panels: int = 200
    iterations: int = 200
    timeout_s: float = 120.0
    thickness_bin: float = 0.1  # stations are grouped by thickness scale and TE thickness / chord at these steps
    te_bin: float = 0.01
    workers: int = 4


def write_dat(airfoil: Airfoil, path: Path) -> Path:
    """Selig format: upper surface trailing edge to leading edge, then lower surface leading edge to trailing edge."""

    upper = np.column_stack([airfoil.x[::-1], airfoil.upper[::-1]])
    lower = np.column_stack([airfoil.x[1:], airfoil.lower[1:]])
    points = np.vstack([upper, lower])
    lines = [airfoil.name] + [f"{x:.7f} {y:.7f}" for x, y in points]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _parse_polar(path: Path) -> list[dict[str, float]]:
    rows = []
    if not path.exists():
        return rows
    started = False
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip().startswith("------"):
            started = True
            continue
        if started and line.strip():
            parts = line.split()
            try:
                values = [float(p) for p in parts[:7]]
            except ValueError:
                continue
            rows.append(dict(zip(("alpha", "cl", "cd", "cdp", "cm", "xtr_top", "xtr_bot"), values, strict=False)))
    return rows


def run_xfoil(airfoil: Airfoil, reynolds: float, settings: PolarSettings, executable: str | None = None) -> list[dict[str, float]]:
    """One Reynolds number: two alpha sweeps from 0 deg (up and down), merged and sorted. Converged points only."""

    exe = executable or find_xfoil()
    if exe is None:
        raise FileNotFoundError("XFOIL not found: install it (see docs/cfd_fea_acoustics_methods.md) or set PRANDTL_XFOIL")
    rows: list[dict[str, float]] = []
    with tempfile.TemporaryDirectory(prefix="xfoil_") as tmp:
        work = Path(tmp)
        write_dat(airfoil, work / "foil.dat")
        for name, a0, a1, step in (
            ("up", 0.0, settings.alpha_max_deg, settings.alpha_step_deg),
            ("down", -settings.alpha_step_deg, settings.alpha_min_deg, -settings.alpha_step_deg),
        ):
            script = "\n".join(
                [
                    "PLOP",
                    "G F",
                    "",
                    "LOAD foil.dat",
                    "PPAR",
                    f"N {settings.panels}",
                    "",
                    "",
                    "OPER",
                    f"VISC {reynolds:.0f}",
                    f"ITER {settings.iterations}",
                    "VPAR",
                    f"N {settings.ncrit:g}",
                    f"XTR {settings.xtr_top:g} {settings.xtr_bot:g}",
                    "",
                    "PACC",
                    f"{name}.pol",
                    "",
                    f"ASEQ {a0:g} {a1:g} {step:g}",
                    "PACC",
                    "",
                    "QUIT",
                    "",
                ]
            )
            try:
                subprocess.run([exe], input=script, text=True, cwd=work, capture_output=True, timeout=settings.timeout_s, check=False)
            except subprocess.TimeoutExpired:
                pass
            rows += _parse_polar(work / f"{name}.pol")
    unique = {round(r["alpha"], 4): r for r in rows}
    return [unique[a] for a in sorted(unique)]


def viterna(alpha_deg: np.ndarray, cl: np.ndarray, cd: np.ndarray, aspect_ratio: float = 10.0, step_deg: float = 2.0):
    """Extend a converged polar to +-180 deg.

    Between the last converged point (alpha_s, Cl_s, Cd_s) and +-90 deg: Viterna-Corrigan with
    Cd_max = 1.11 + 0.018 AR. Beyond +-90 deg (reverse flow): a flat plate,
    Cl = Cd_max sin(a) cos(a), Cd = Cd_max sin^2(a) + 0.02, continuous with Viterna at 90 deg.
    """

    cd_max = 1.11 + 0.018 * aspect_ratio
    out = {float(a): (float(c), float(d)) for a, c, d in zip(alpha_deg, cl, cd, strict=True)}

    def viterna_branch(a_s: float, cl_s: float, cd_s: float):
        s = math.radians(a_s)
        b2 = (cd_s - cd_max * math.sin(s) ** 2) / math.cos(s)
        a2 = (cl_s - cd_max * math.sin(s) * math.cos(s)) * math.sin(s) / math.cos(s) ** 2

        def at(a: float) -> tuple[float, float]:
            r = math.radians(a)
            return 0.5 * cd_max * math.sin(2 * r) + a2 * math.cos(r) ** 2 / math.sin(r), cd_max * math.sin(r) ** 2 + b2 * math.cos(r)

        return at

    hi, lo = int(np.argmax(alpha_deg)), int(np.argmin(alpha_deg))
    up = viterna_branch(float(alpha_deg[hi]), float(cl[hi]), float(cd[hi]))
    down = viterna_branch(-float(alpha_deg[lo]), -float(cl[lo]), float(cd[lo]))  # mirrored
    for a in np.arange(float(alpha_deg[hi]) + step_deg, 90.0 + 1e-9, step_deg):
        out[float(a)] = up(float(a))
    for a in np.arange(float(alpha_deg[lo]) - step_deg, -90.0 - 1e-9, -step_deg):
        c_l, c_d = down(-float(a))
        out[float(a)] = (-c_l, c_d)
    for a in np.concatenate([np.arange(90.0 + step_deg, 180.0 + 1e-9, step_deg), -np.arange(90.0 + step_deg, 180.0 + 1e-9, step_deg)]):
        r = math.radians(float(a))
        out[float(a)] = (cd_max * math.sin(r) * math.cos(r), cd_max * math.sin(r) ** 2 + 0.02)
    a = np.array(sorted(out))
    return a, np.array([out[k][0] for k in a]), np.array([out[k][1] for k in a])


def write_polar_csv(path: Path, rows: list[dict[str, float]], extended: tuple[np.ndarray, np.ndarray, np.ndarray] | None) -> Path:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["alpha", "Cl", "Cd", "Cm", "converged", "xtr_top", "xtr_bot"])
        converged = {round(r["alpha"], 4): r for r in rows}
        alphas, cls, cds = (
            extended
            if extended is not None
            else (
                np.array([r["alpha"] for r in rows]),
                np.array([r["cl"] for r in rows]),
                np.array([r["cd"] for r in rows]),
            )
        )
        for a, c_l, c_d in zip(alphas, cls, cds, strict=True):
            r = converged.get(round(float(a), 4))
            writer.writerow(
                [
                    f"{a:.3f}",
                    f"{c_l:.5f}",
                    f"{c_d:.6f}",
                    f"{r['cm']:.5f}" if r else "0.0",
                    int(r is not None),
                    f"{r['xtr_top']:.4f}" if r else "",
                    f"{r['xtr_bot']:.4f}" if r else "",
                ]
            )
    return path


def section_key(base: str, thickness_scale: float, te_ratio: float) -> str:
    return f"{base}T{round(thickness_scale * 100):03d}E{round(te_ratio * 1000):03d}"


def section_polars(design, stations, out_dir: Path, settings: PolarSettings | None = None, echo=print):
    """XFOIL polars for every distinct molded section of ``stations``; returns (stations with keys, TablePolar, summary)."""

    from prandtl_prop.aero.polars import TablePolar
    from prandtl_prop.cad.airfoils import load_airfoil
    from prandtl_prop.design import resolve_path
    from prandtl_prop.manufacturing.injection import modify_airfoil, thickness_schedule

    settings = settings or PolarSettings()
    spec = design.airfoils
    radius = design.operating_point.radius_m
    scales = thickness_schedule(stations.r_m / radius, [tuple(p) for p in spec.thickness_scale])
    te = spec.trailing_edge_thickness_m or 0.0
    keys, groups = [], {}
    for name, scale, chord in zip(stations.airfoil, scales, stations.chord_m, strict=True):
        ratio = te / float(chord)
        s_r = round(float(scale) / settings.thickness_bin) * settings.thickness_bin
        e_r = round(ratio / settings.te_bin) * settings.te_bin
        key = section_key("".join(ch for ch in name if ch.isalnum()).upper(), s_r, e_r)
        keys.append(key)
        groups.setdefault(key, (name, s_r, e_r))
    out_dir.mkdir(parents=True, exist_ok=True)
    exe = find_xfoil()
    jobs = []
    for key, (name, s_r, e_r) in groups.items():
        base = load_airfoil(name, resolve_path(spec.directory), max(spec.points_per_side, 120), spec.substitutes)
        foil = replace(modify_airfoil(base, s_r, e_r), name=key)
        tag = hashlib.sha1(f"{key}|{settings}|{np.round(foil.upper, 6).tobytes().hex()}".encode()).hexdigest()[:10]
        for re_ in settings.reynolds:
            path = out_dir / f"{key}_Re{re_:.0f}_polar.csv"
            stamp = path.with_suffix(".tag")
            if not (path.exists() and stamp.exists() and stamp.read_text() == tag):
                jobs.append((key, foil, re_, path, stamp, tag))
    summary = []
    from concurrent.futures import ProcessPoolExecutor

    with ProcessPoolExecutor(max_workers=max(1, settings.workers)) as pool:
        futures = [(job, pool.submit(run_xfoil, job[1], job[2], settings, exe)) for job in jobs]
        for (key, _foil, re_, path, stamp, tag), future in futures:
            ok = [r for r in future.result() if np.isfinite(r["cd"]) and r["cd"] > 0]
            if len(ok) < 8:
                echo(f"  {key} Re {re_:.0f}: only {len(ok)} converged points; skipped")
                continue
            alpha = np.array([r["alpha"] for r in ok])
            write_polar_csv(path, ok, viterna(alpha, np.array([r["cl"] for r in ok]), np.array([r["cd"] for r in ok])))
            stamp.write_text(tag)
            best = max(ok, key=lambda r: r["cl"] / r["cd"])
            summary.append(
                {
                    "key": key,
                    "reynolds": re_,
                    "converged": len(ok),
                    "cl_max": max(r["cl"] for r in ok),
                    "best_ld": best["cl"] / best["cd"],
                    "cl_at_best_ld": best["cl"],
                    "cd_at_best_ld": best["cd"],
                }
            )
            echo(
                f"  {key} Re {re_:>7.0f}: {len(ok):3d} points, Cl max {summary[-1]['cl_max']:.2f}, best L/D {summary[-1]['best_ld']:.0f} at Cl {best['cl']:.2f}"
            )
    keyed = replace(stations, airfoil=tuple(keys))
    return keyed, TablePolar.from_directory(out_dir), summary
