from __future__ import annotations

import json
import math
import sys
from datetime import datetime
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import postprocess_final_geometries as final_post
import run_final_thesis_solver as core


MIL_INPUT = ROOT / "results" / "geometry_optimizer" / "final_exports" / "final_mil_geometry.csv"
GMT_INPUT = ROOT / "results" / "geometry_optimizer" / "final_exports" / "final_gmt_cleaned_valid_geometry.csv"
OUTPUT_DIR = ROOT / "results" / "final_geometry"
CONTROLLED_MIL = OUTPUT_DIR / "controlled_mil_geometry.csv"
CONTROLLED_GMT = OUTPUT_DIR / "controlled_gmt_geometry.csv"
AUDIT = OUTPUT_DIR / "final_geometry_audit.md"
SCRIPT_NAME = "Code/geometry/run_final_geometry.py"


def main() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    op = core.OperatingCondition()
    polar_database = final_post.load_polar_database(final_post.AIRFOIL_DIR)

    mil = core.read_frozen_geometry(MIL_INPUT, "MIL")
    gmt = core.read_frozen_geometry(GMT_INPUT, "GMT")
    chord_status = core.lock_chord_and_verify(mil, gmt)

    repair_log = {
        "MIL": core.repair_twist(mil, op, polar_database),
        "GMT": core.repair_twist(gmt, op, polar_database),
    }

    core.write_controlled_geometry(CONTROLLED_MIL, mil, "controlled_MIL")
    core.write_controlled_geometry(CONTROLLED_GMT, gmt, "controlled_GMT")
    write_metadata(CONTROLLED_MIL, "controlled MIL final geometry")
    write_metadata(CONTROLLED_GMT, "controlled GMT final geometry")
    write_geometry_figures(mil, gmt)
    write_audit(mil, gmt, chord_status, repair_log)

    print("Final geometry complete")
    print(f"  MIL input: {MIL_INPUT}")
    print(f"  GMT input: {GMT_INPUT}")
    print(f"  output folder: {OUTPUT_DIR}")
    print(f"  max_abs_chord_delta_m: {chord_status['max_abs_chord_delta_m']:.12g}")
    for label in ("MIL", "GMT"):
        print(
            f"  {label} max twist repair delta: "
            f"{repair_log[label]['max_abs_twist_repair_delta_deg']:.6f} deg"
        )
    return 0


def write_metadata(path: Path, role: str) -> None:
    payload = {
        "case": path.stem,
        "role": role,
        "generation_script": SCRIPT_NAME,
        "generation_timestamp": datetime.now().isoformat(timespec="seconds"),
        "authoritative_inputs": [str(MIL_INPUT), str(GMT_INPUT)],
        "chord_policy": "GMT chord locked to MIL chord at every station.",
        "twist_policy": "beta_final_deg includes documented physical validation if repair was needed.",
    }
    path.with_suffix(".metadata.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")


def write_geometry_figures(mil: list[core.Station], gmt: list[core.Station]) -> None:
    specs = [
        ("controlled_chord_distribution", "chord_m", "Chord [m]"),
        ("controlled_twist_distribution", "beta_final_deg", "Final twist beta [deg]"),
        ("controlled_twist_repair_delta", "twist_repair_delta_deg", "Twist repair delta [deg]"),
    ]
    for stem, attr, ylabel in specs:
        fig, ax = plt.subplots(figsize=(6.6, 4.2), constrained_layout=True)
        plot_station_series(ax, mil, attr, "MIL", "#1f77b4")
        plot_station_series(ax, gmt, attr, "GMT", "#d62728")
        ax.set_xlabel("r/R [-]")
        ax.set_ylabel(ylabel)
        ax.grid(True, color="#d8d8d8", linewidth=0.8)
        ax.legend(frameon=False)
        ax.set_xlim(0.18, 1.01)
        for spine in ("top", "right"):
            ax.spines[spine].set_visible(False)
        fig.savefig(OUTPUT_DIR / f"{stem}.png", dpi=300)
        fig.savefig(OUTPUT_DIR / f"{stem}.svg")
        plt.close(fig)


def plot_station_series(ax: plt.Axes, stations: list[core.Station], attr: str, label: str, color: str) -> None:
    ax.plot(
        [station.r_R for station in stations],
        [float(getattr(station, attr)) for station in stations],
        marker="o",
        markersize=3.0,
        linewidth=1.8,
        color=color,
        label=label,
    )


def write_audit(
    mil: list[core.Station],
    gmt: list[core.Station],
    chord_status: dict[str, object],
    repair_log: dict[str, dict[str, object]],
) -> None:
    pass_fail = final_pass_fail(mil, gmt, chord_status, repair_log)
    lines = [
        "# Final Geometry Audit",
        "",
        "This geometry package is the source of truth for final thesis aerodynamic postprocessing.",
        "",
        "## Source Files",
        "",
        f"- MIL frozen source: `{rel(MIL_INPUT)}`",
        f"- GMT frozen source: `{rel(GMT_INPUT)}`",
        "",
        "## Output Files",
        "",
        f"- MIL controlled geometry: `{rel(CONTROLLED_MIL)}`",
        f"- GMT controlled geometry: `{rel(CONTROLLED_GMT)}`",
        "",
        "## Chord Lock",
        "",
        f"- Station count: {chord_status['station_count']}",
        f"- Radial stations match: {status(chord_status['radial_stations_match'])}",
        f"- Airfoil schedule matches: {status(chord_status['airfoil_schedule_match'])}",
        f"- Max absolute chord delta after locking: {chord_status['max_abs_chord_delta_m']:.12g} m",
        "",
        "## Twist Repair",
        "",
        "| Case | changed stations | max repair delta [deg] | monotonic/smooth twist | repair > 3 deg |",
        "|---|---:|---:|---|---|",
    ]
    for label in ("MIL", "GMT"):
        log = repair_log[label]
        exceeds = float(log["max_abs_twist_repair_delta_deg"]) > core.MAX_TWIST_REPAIR_DELTA_WARN_DEG
        lines.append(
            f"| {label} | {log['changed_station_count']} | {log['max_abs_twist_repair_delta_deg']:.12g} | "
            f"{status(log['validation']['smooth_twist'])} | {status(not exceeds)} |"
        )
    lines.extend(
        [
            "",
            "## Final Geometry Pass/Fail",
            "",
            "| Check | Status |",
            "|---|---|",
        ]
    )
    for key, value in pass_fail.items():
        lines.append(f"| {key} | {status(value)} |")
    AUDIT.write_text("\n".join(lines) + "\n", encoding="utf-8")


def final_pass_fail(
    mil: list[core.Station],
    gmt: list[core.Station],
    chord_status: dict[str, object],
    repair_log: dict[str, dict[str, object]],
) -> dict[str, bool]:
    return {
        "monotonic r/R": all(b.r_R > a.r_R for a, b in zip(mil, mil[1:]))
        and all(b.r_R > a.r_R for a, b in zip(gmt, gmt[1:])),
        "positive chord": all(station.chord_m > 0.0 and math.isfinite(station.chord_m) for station in mil + gmt),
        "locked chord": float(chord_status["max_abs_chord_delta_m"]) <= 1.0e-12,
        "finite final twist": all(math.isfinite(station.beta_final_deg) for station in mil + gmt),
        "smooth final twist": bool(repair_log["MIL"]["validation"]["smooth_twist"])
        and bool(repair_log["GMT"]["validation"]["smooth_twist"]),
        "no repair greater than 3 deg": float(repair_log["MIL"]["max_abs_twist_repair_delta_deg"]) <= 3.0
        and float(repair_log["GMT"]["max_abs_twist_repair_delta_deg"]) <= 3.0,
    }


def rel(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def status(value: object) -> str:
    return "pass" if bool(value) else "fail"


if __name__ == "__main__":
    raise SystemExit(main())
