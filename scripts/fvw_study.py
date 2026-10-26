"""Free wake vs BEMT (hover, edgewise), static aeroelasticity, 1/rev fatigue and unsteady loading noise.

    python scripts/fvw_study.py [designs/gmt_fpv10_tri.yaml] [--output outputs/methods/gmt_fpv10_tri]

Writes summary.json with every number quoted in docs/cfd_fea_acoustics_methods.md (sections B-D).
Edgewise point: 10 m/s, 6.7 deg disk tilt, 6798 rpm (docs/mission.md). A few minutes with numba.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path

import numpy as np

from prandtl_prop import mission, multirotor, pipeline
from prandtl_prop.acoustics import fwh
from prandtl_prop.aero import fvw
from prandtl_prop.aero.bemt import solve_rotor
from prandtl_prop.design import load_design
from prandtl_prop.structures import aeroelastic as ae

EDGE_V, EDGE_TILT_DEG, EDGE_RPM = 10.0, 6.7, 6798.0
FULL_THROTTLE_N = 14.18


def panel_widths(stations, radius_m: float, panels: int) -> np.ndarray:
    root = float(stations.r_m[0] - 0.5 * stations.dr_m[0])
    return np.diff(root + (radius_m - root) * 0.5 * (1.0 - np.cos(np.linspace(0.0, math.pi, panels + 1))))


def edgewise_noise(result: fvw.FvwResult, widths: np.ndarray, rpm: float, radius_m: float, blades: int) -> dict:
    """FW-H loading tones from blade 0's load history vs the same loads averaged over azimuth."""

    order = np.argsort(result.psi_deg)
    r = result.r_m
    history = np.zeros((len(order), len(r), 3))
    history[..., 1] = result.dQ_dr[order, 0] / r * widths  # on the fluid: along the blade motion
    history[..., 2] = -result.dT_dr[order, 0] * widths  # on the fluid: thrust reaction down
    positions = np.column_stack([r, np.zeros_like(r), np.zeros_like(r)])
    d = 10.0 * radius_m
    s = d / math.sqrt(2.0)
    observers = {
        "in plane, ahead": (d, 0, 0),
        "in plane, advancing side": (0, -d, 0),
        "in plane, retreating side": (0, d, 0),
        "in plane, behind": (-d, 0, 0),
        "45 deg below, ahead": (s, 0, -s),
        "45 deg below, advancing side": (0, -s, -s),
        "on the axis, below": (0, 0, -d),
    }
    obs = np.array(list(observers.values()), float)
    kw = dict(omega_rad_s=rpm * math.pi / 30.0, blade_count=blades, samples_per_rev=1440)
    unsteady = fwh.pressure_history(fwh.RotorSources(compact_positions_m=positions, compact_force_history_N=history), obs, **kw)
    steady = fwh.pressure_history(fwh.RotorSources(compact_positions_m=positions, compact_forces_N=history.mean(axis=0)), obs, **kw)

    def spl(p):
        return 20.0 * np.log10(np.maximum(p, 1e-12) / 2e-5)

    def oaspl(p):
        return 10.0 * np.log10(np.sum(10.0 ** (spl(p) / 10.0), axis=-1))

    hu, hs = fwh.harmonics(unsteady["loading"], blades, 8), fwh.harmonics(steady["loading"], blades, 8)
    thrust = (result.dT_dr[order, 0] * widths).sum(axis=-1)
    harmonics = 2.0 * np.abs(np.fft.rfft(thrust)) / len(thrust)
    return {
        "observer_distance_m": d,
        "bpf_hz": blades * rpm / 60.0,
        "blade_thrust_mean_N": float(thrust.mean()),
        "blade_thrust_harmonics_N": [float(x) for x in harmonics[1:9]],
        "observers": {
            name: {
                "steady_bpf_dB": [round(float(x), 1) for x in spl(hs[i])],
                "unsteady_bpf_dB": [round(float(x), 1) for x in spl(hu[i])],
                "steady_oaspl_dB": round(float(oaspl(hs[i])), 1),
                "unsteady_oaspl_dB": round(float(oaspl(hu[i])), 1),
            }
            for i, name in enumerate(observers)
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("design", nargs="?", default="designs/gmt_fpv10_tri.yaml")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--edge-steps", type=int, default=48)
    args = parser.parse_args()
    design = load_design(args.design)
    out_dir = args.output or Path("outputs/methods") / design.name
    out_dir.mkdir(parents=True, exist_ok=True)
    run = pipeline.run_design(design, ["aero"])["aero"].run
    stations, polar, corrections, collective, op = mission.bemt_context(design, run)
    stations = replace(stations, twist_deg=stations.twist_deg + collective)
    summary: dict = {}

    # B. hover
    hover_settings = fvw.FvwSettings(panels=12, steps_per_rev=36, revolutions=8.0, run_revolutions=12.0)
    bemt = solve_rotor(stations, op, polar, tip_loss=design.aero.tip_loss, corrections=corrections).summary
    hover = fvw.solve(stations, op.radius_m, op.blade_count, op.rpm, polar, settings=hover_settings)
    summary["hover"] = {
        "rpm": op.rpm,
        "bemt": {
            "thrust_N": float(bemt["thrust_N"]),
            "torque_Nm": float(bemt["torque_Nm"]),
            "figure_of_merit": float(bemt["figure_of_merit"]),
        },
        "fvw": {"thrust_N": hover.thrust_N, "torque_Nm": hover.torque_Nm},
    }
    rho, area = op.rho_kg_m3, math.pi * op.radius_m**2
    summary["hover"]["fvw"]["figure_of_merit"] = hover.thrust_N**1.5 / math.sqrt(2.0 * rho * area) / hover.power_W
    print("hover", json.dumps(summary["hover"]))

    # B. edgewise
    tilt = math.radians(EDGE_TILT_DEG)
    edge_op = replace(op, rpm=EDGE_RPM)
    b = multirotor.rotor_forward(stations, edge_op, polar, v_edge_m_s=EDGE_V * math.cos(tilt), v_axial_m_s=EDGE_V * math.sin(tilt))
    edge_settings = fvw.FvwSettings(panels=10, steps_per_rev=args.edge_steps, revolutions=4.0, run_revolutions=5.0)
    edge = fvw.solve(
        stations,
        op.radius_m,
        op.blade_count,
        EDGE_RPM,
        polar,
        v_inf_m_s=(-EDGE_V * math.cos(tilt), 0.0, -EDGE_V * math.sin(tilt)),
        settings=edge_settings,
    )
    summary["edgewise"] = {
        "v_m_s": EDGE_V,
        "tilt_deg": EDGE_TILT_DEG,
        "rpm": EDGE_RPM,
        "bemt": {"thrust_N": float(b["thrust_N"]), "torque_Nm": float(b["torque_Nm"]), "peak_cl_outer": float(b["peak_cl_outer"])},
        "fvw": {
            "thrust_N": edge.thrust_N,
            "torque_Nm": edge.torque_Nm,
            "peak_cl_outer": float(edge.cl[:, :, edge.r_m >= 0.5 * op.radius_m].max()),
        },
    }
    print("edgewise", json.dumps(summary["edgewise"]))

    # C. static aeroelasticity and 1/rev fatigue
    summary["aeroelastic"] = {}
    for label, thrust in (("hover", design.operating_point.target_thrust_N), ("full_throttle", FULL_THROTTLE_N)):
        r = ae.static_aeroelastic(design, run, thrust_N=thrust)
        summary["aeroelastic"][label] = {
            "thrust_N": thrust,
            "tip_elastic_twist_deg": float(r["elastic_twist_deg"][-1]),
            "rigid_power_W": r["rigid"]["power_W"],
            "flown_power_W": r["flown"]["power_W"],
            "delta_power_pct": r["delta_power_pct"],
        }
    for label, result, rpm in (("hover", hover, op.rpm), ("edgewise", edge, EDGE_RPM)):
        summary["aeroelastic"][f"fatigue_{label}"] = ae.once_per_rev_fatigue(
            design, run, rpm=rpm, r_m=result.r_m, dT_dr=result.dT_dr[:, 0], dF_dr=result.dQ_dr[:, 0] / result.r_m
        )
    print("aeroelastic", json.dumps(summary["aeroelastic"]))

    # D. unsteady loading noise
    widths = panel_widths(stations, op.radius_m, edge_settings.panels)
    summary["edgewise_noise"] = edgewise_noise(edge, widths, EDGE_RPM, op.radius_m, op.blade_count)
    print("noise", json.dumps(summary["edgewise_noise"]))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    print(f"wrote {out_dir / 'summary.json'}")


if __name__ == "__main__":
    main()
