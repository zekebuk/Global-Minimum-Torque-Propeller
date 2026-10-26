"""Annotated drawing of the 10 in GMT tri-blade quad: vehicle, selected blade, drive chain, and hover power flow.

Every number is read from the pipeline outputs (no hand-typed results):
  outputs/sitl/gmt_fpv10_tri/planform/best/stations.csv       selected chord + twist blade
  outputs/sitl/gmt_fpv10_tri/planform/best/px4_params.txt     PX4 parameters fitted to it
  outputs/sitl/gmt_fpv10_tri/planform/best/rotor_table.json   rotor tables (hover rpm)
  outputs/sitl/gmt_fpv10_tri/audit_planform/loss_breakdown.csv energy audit (hover power flow)
  data/geometry/gmt_fpv10_tri_geometry.csv                     blade as built (for comparison)
  data/motors/cots_6s_10in.yaml                                motor data

Run: python scripts/draw_system.py  ->  docs/images/system_annotated.png
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import yaml  # noqa: E402
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch, Polygon  # noqa: E402

from prandtl_prop.design import load_design  # noqa: E402
from prandtl_prop.sitl.tables import RotorTable  # noqa: E402

INK, INK2, MUTED, GRID = "#1f1e1c", "#52514e", "#8a8984", "#d9d7d2"
BLUE, BLUE_L, ORANGE, PINK, GOLD, GREEN = "#1c5cab", "#86b6ef", "#eb6834", "#d6336c", "#c9a227", "#1baf7a"
BEST = ROOT / "outputs/sitl/gmt_fpv10_tri/planform/best"
MOTOR = "T-Motor MN3110 KV780"


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def params(path: Path) -> dict[str, float]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("param set-default"):
            _, _, name, value = line.split()
            out[name] = float(value)
    return out


def naca4(code: str, n: int = 60) -> np.ndarray:
    """Closed NACA 4-digit outline (x/c, y/c), leading edge at 0."""

    m, p, t = int(code[0]) / 100, int(code[1]) / 10, int(code[2:]) / 100
    beta = np.linspace(0, math.pi, n)
    x = 0.5 * (1 - np.cos(beta))
    yt = 5 * t * (0.2969 * np.sqrt(x) - 0.126 * x - 0.3516 * x**2 + 0.2843 * x**3 - 0.1036 * x**4)
    yc = np.where(x < p, m / p**2 * (2 * p * x - x**2), m / (1 - p) ** 2 * (1 - 2 * p + 2 * p * x - x**2))
    dyc = np.where(x < p, 2 * m / p**2 * (p - x), 2 * m / (1 - p) ** 2 * (p - x))
    th = np.arctan(dyc)
    upper = np.column_stack([x - yt * np.sin(th), yc + yt * np.cos(th)])
    lower = np.column_stack([x + yt * np.sin(th), yc - yt * np.cos(th)])
    return np.vstack([upper[::-1], lower[1:]])


def blade_outline(r: np.ndarray, chord: np.ndarray, pitch_axis: float = 0.25) -> np.ndarray:
    """Plan view of one blade along +x: leading edge at +pitch_axis c, trailing edge at -(1 - pitch_axis) c."""

    le = np.column_stack([r, pitch_axis * chord])
    te = np.column_stack([r[::-1], -(1 - pitch_axis) * chord[::-1]])
    return np.vstack([le, te])


def rotate(points: np.ndarray, angle: float) -> np.ndarray:
    c, s = math.cos(angle), math.sin(angle)
    return points @ np.array([[c, s], [-s, c]])


def label(ax, xy, text, xytext, **kw):
    ax.annotate(
        text,
        xy=xy,
        xytext=xytext,
        fontsize=kw.pop("fontsize", 8.5),
        color=kw.pop("color", INK),
        ha=kw.pop("ha", "left"),
        va=kw.pop("va", "center"),
        arrowprops={"arrowstyle": "-", "color": MUTED, "lw": 0.8, "shrinkA": 2, "shrinkB": 2},
        **kw,
    )


def main() -> None:
    design = load_design(ROOT / "designs/gmt_fpv10_tri.yaml")
    R = design.operating_point.radius_m
    B = design.operating_point.blade_count
    spec = design.multirotor
    best = rows(BEST / "stations.csv")
    r_best = np.array([float(x["r_R"]) for x in best]) * R
    c_best = np.array([float(x["chord_m"]) for x in best])
    b_best = np.array([float(x["twist_deg"]) for x in best])
    built = rows(ROOT / design.geometry.stations_csv)
    r_built = np.array([float(x.get("mu") or x["r_R"]) for x in built]) * R
    c_built = np.array([float(x["chord_m"]) for x in built])
    px4 = params(BEST / "px4_params.txt")
    table = RotorTable.load(BEST / "rotor_table.json")
    motor = next(m for m in yaml.safe_load((ROOT / "data/motors/cots_6s_10in.yaml").read_text())["motors"] if m["name"] == MOTOR)
    audit = rows(ROOT / "outputs/sitl/gmt_fpv10_tri/audit_planform/loss_breakdown.csv")
    hover_new = next(a for a in audit if a["mission"] == "hover" and a["prop"] != "as built")
    hover_old = next(a for a in audit if a["mission"] == "hover" and a["prop"] == "as built")
    to_w = 3600.0 / 120.0  # the hover mission is 120 s: Wh -> mean W
    flow = {k: float(v) * to_w for k, v in hover_new.items() if k not in ("prop", "mission")}
    old_cells = float(hover_old["cells"]) * to_w

    header = (BEST / "px4_params.txt").read_text(encoding="utf-8")
    auw_text = header.split("AUW ")[1].split(" kg")[0] if "AUW " in header else None
    hover_thrust = float(auw_text) * 9.80665 / 4 if auw_text else px4["MPC_THR_HOVER"] * px4["CA_ROTOR0_CT"]
    lo, hi = 500.0, 30000.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        lo, hi = (mid, hi) if float(table.evaluate(mid, 0.0, 0.0)["thrust_N"]) < hover_thrust else (lo, mid)
    hover_rpm = 0.5 * (lo + hi)
    auw = 4 * hover_thrust / 9.80665

    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.edgecolor": GRID})
    fig = plt.figure(figsize=(22, 14.5), facecolor="white")
    grid = fig.add_gridspec(2, 2, height_ratios=[1.15, 0.8], width_ratios=[1.0, 1.25], hspace=0.18, wspace=0.06, top=0.95, bottom=0.1)

    # ------------------------------------------------------------------ vehicle, top view (x forward up)
    ax = fig.add_subplot(grid[0, 0])
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("Vehicle, top view (body FRD: x forward, y right)", fontsize=12, color=INK, loc="left")
    pos = np.array([[px4[f"CA_ROTOR{i}_PX"], px4[f"CA_ROTOR{i}_PY"]] for i in range(4)])
    km = np.array([px4[f"CA_ROTOR{i}_KM"] for i in range(4)])
    names = {0: "front-right", 1: "rear-left", 2: "front-left", 3: "rear-right"}
    outline = blade_outline(r_best, c_best)
    for i, ((x, y), k) in enumerate(zip(pos, km, strict=True)):
        cx, cy = y, x  # plot: right = +y, up = +x
        ax.plot([0, cx], [0, cy], color=INK2, lw=7, solid_capstyle="round", zorder=1)
        ax.add_patch(Circle((cx, cy), R, fill=False, ls=(0, (4, 3)), color=MUTED, lw=0.9, zorder=2))
        spin = 1 if k > 0 else -1
        for j in range(B):
            blade = rotate(outline, math.radians(25 + 120 * j) * spin) + [cx, cy]
            ax.add_patch(Polygon(blade, closed=True, fc=BLUE_L, ec=BLUE, lw=0.8, zorder=3))
        ax.add_patch(Circle((cx, cy), 0.0189, fc=INK2, ec=INK, lw=0.8, zorder=4))
        a0, a1 = (60, 150) if spin > 0 else (150, 60)
        arc = FancyArrowPatch(
            (cx + 1.12 * R * math.cos(math.radians(a0)), cy + 1.12 * R * math.sin(math.radians(a0))),
            (cx + 1.12 * R * math.cos(math.radians(a1)), cy + 1.12 * R * math.sin(math.radians(a1))),
            connectionstyle=f"arc3,rad={0.42 * spin}",
            arrowstyle="-|>",
            mutation_scale=14,
            color=ORANGE,
            lw=1.6,
            zorder=5,
        )
        ax.add_patch(arc)
        side = 1 if cx > 0 else -1
        ax.text(
            cx + side * 1.18 * R,
            cy + (0.62 if cy > 0 else -0.62) * R,
            f"Rotor {i} ({names[i]})\n{'CCW' if spin > 0 else 'CW'} from above\nCA_ROTOR{i}_KM {k:+.4f} m\nPX {x:+.4f}  PY {y:+.4f} m",
            fontsize=8.5,
            color=INK,
            ha="left" if side > 0 else "right",
            va="center",
        )
    ax.add_patch(FancyBboxPatch((-0.055, -0.065), 0.11, 0.13, boxstyle="round,pad=0.004", fc="#efece6", ec=INK2, lw=1.0, zorder=6))
    ax.text(0, 0.035, "6S 4000 mAh", fontsize=7.5, ha="center", color=INK, zorder=7)
    ax.text(0, 0.0, "FC + 4-in-1 ESC", fontsize=7.5, ha="center", color=INK, zorder=7)
    ax.text(0, -0.035, "PX4", fontsize=7.5, ha="center", color=INK, zorder=7)
    ax.annotate("", xy=(0, 0.33), xytext=(0, 0.21), arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 1.6})
    ax.text(0.008, 0.31, "forward (+x)", fontsize=9, color=INK)
    ax.text(
        -0.40,
        -0.36,
        f"Arms {spec.arm_length_m * 1000:.0f} mm motor to CG  ·  all-up mass {auw:.3f} kg  ·  hover {hover_thrust:.2f} N per rotor at {hover_rpm:,.0f} rpm\n"
        f"PX4: CA_ROTORi_CT {px4['CA_ROTOR0_CT']:.1f} N  ·  THR_MDL_FAC {px4['THR_MDL_FAC']:.3f}  ·  MPC_THR_HOVER {px4['MPC_THR_HOVER']:.3f}\n"
        "Rotor positions are quad-X placeholders until the frame is chosen; arm blockage not modelled.",
        fontsize=9,
        color=INK2,
        va="top",
    )
    ax.set_xlim(-0.5, 0.5)
    ax.set_ylim(-0.42, 0.36)

    # ------------------------------------------------------------------ blade
    ax = fig.add_subplot(grid[0, 1])
    ax.set_title(
        f"Selected blade: GMT chord + twist (designed at 0.8 x hover rpm, Cl <= 0.6), NACA 4410, {B} blades, D = {2 * R * 1000:.0f} mm",
        fontsize=12,
        color=INK,
        loc="left",
    )
    mm = 1000.0
    old = blade_outline(r_built * mm, c_built * mm)
    new = blade_outline(r_best * mm, c_best * mm)
    ax.add_patch(Polygon(old, closed=True, fill=False, ec=MUTED, ls=(0, (5, 3)), lw=1.2))
    ax.add_patch(Polygon(new, closed=True, fc=BLUE_L, ec=BLUE, lw=1.4, alpha=0.9))
    ax.plot([0, R * mm], [0, 0], color=INK2, lw=0.8, ls="-.")
    ax.add_patch(Circle((0, 0), design.hub.boss_diameter_m / 2 * mm, fc="#efece6", ec=INK2, lw=1.0))
    ax.text(
        0,
        -12.5,
        f"hub boss {design.hub.boss_diameter_m * mm:.0f} mm\nbore {design.hub.center_bore_m * mm:.0f} mm",
        fontsize=8,
        ha="center",
        va="top",
        color=INK2,
    )
    label(
        ax,
        (r_best[0] * mm + 6, 0.25 * c_best[0] * mm),
        f"root chord {c_best[0] * mm:.1f} mm\n(0.22 R cap)",
        (r_best[0] * mm - 4, 22),
        ha="left",
    )
    label(
        ax,
        (r_best[-1] * mm - 2, -0.75 * c_best[-1] * mm),
        f"tip chord {c_best[-1] * mm:.1f} mm\n(0.06 R molding floor)",
        (R * mm - 28, -26),
        ha="left",
    )
    k_old = int(np.argmin(np.abs(r_built - 0.75 * R)))
    label(
        ax,
        (r_built[k_old] * mm, 0.25 * c_built[k_old] * mm),
        "blade as built (dashed):\nwider outboard, sections at Cl 0.2-0.4",
        (r_built[k_old] * mm - 30, 21),
        ha="left",
        color=INK2,
    )
    ax.text(
        4,
        -30,
        "pitch axis at 25 % chord (dash-dot); chord ~ 1/r outboard: every loaded section near its design Cl\n"
        "full-throttle stress met without root thickening (section modulus ~ c^3)",
        fontsize=8.5,
        color=INK2,
        va="top",
    )
    for frac in (0.3, 0.5, 0.75, 0.95):
        k = int(np.argmin(np.abs(r_best - frac * R)))
        c, beta = c_best[k] * mm, b_best[k]
        foil = naca4("4410") * c
        foil[:, 0] -= 0.25 * c  # about the pitch axis
        th = -math.radians(beta)  # nose up by beta with the leading edge on the left
        sec = foil @ np.array([[math.cos(th), math.sin(th)], [-math.sin(th), math.cos(th)]])
        x0 = r_best[k] * mm
        ax.add_patch(Polygon(np.column_stack([x0 + sec[:, 0], 38 + sec[:, 1]]), closed=True, fc="#c9dcf3", ec=BLUE, lw=0.9))
        ax.plot([x0, x0], [5, 32], color=GRID, lw=0.8, ls=":")
        ax.text(x0, 46 + 0.5 * c, f"r/R {frac:.2f}\nc {c:.1f} mm\nbeta {beta:.1f} deg", fontsize=8, ha="center", va="bottom", color=INK)
    ax.text(
        R * mm + 3,
        38,
        "sections at\nscale: leading\nedge left,\nnose up by\nbeta (blade\nmoves left)",
        fontsize=7.5,
        color=MUTED,
        va="center",
    )
    ax.set_xlim(-14, R * mm + 22)
    ax.set_ylim(-40, 72)
    ax.set_aspect("equal")
    ax.set_xlabel("radius (mm)", fontsize=9, color=INK2)
    ax.tick_params(colors=MUTED, labelsize=8)
    for s in ("top", "right", "left"):
        ax.spines[s].set_visible(False)
    ax.set_yticks([])

    # ------------------------------------------------------------------ hover power flow (waterfall)
    ax = fig.add_subplot(grid[1, :])
    ax.set_title(
        f"Drive chain and hover power flow, all four rotors (energy audit, 120 s hover): {flow['cells']:.0f} W from the cells "
        f"(blade as built: {old_cells:.0f} W, {100 * (flow['cells'] / old_cells - 1):+.0f} %)",
        fontsize=12,
        color=INK,
        loc="left",
    )
    mass_g = float(rows(ROOT / "outputs/sitl/gmt_fpv10_tri/planform/candidates.csv")[0]["prop_mass_g"])
    steps = [
        (
            "battery_internal",
            "battery\ninternal R",
            GOLD,
            f"6S Li-po {spec.battery_energy_Wh / spec.battery_voltage_V * 1000:.0f} mAh\n{spec.battery_resistance_ohm * 1000:.0f} mOhm",
        ),
        ("esc", "ESC", "#e8b84a", f"duty-cycle drive, eta {spec.esc_efficiency:.2f}\nDShot, active braking"),
        ("motor_copper", "motor\ncopper I^2R", PINK, f"{MOTOR}\n{motor['resistance_ohm'] * 1000:.0f} mOhm winding"),
        (
            "motor_iron_friction",
            "motor iron\n+ friction",
            "#f08cae",
            f"{motor['no_load_A']} A no-load @ {motor['no_load_test_V']} V\n{motor['mass_kg'] * 1000:.0f} g each",
        ),
        ("profile_drag", "blade\nprofile drag", ORANGE, f"{B}-blade chord + twist GMT\n18.0 g each ({mass_g:.1f} g as built)"),
        ("induced_excess", "induced excess\n(tip loss, swirl)", BLUE_L, "uniform-inflow BEMT,\nPrandtl tip loss"),
    ]
    xs = np.arange(len(steps) + 2)
    level = flow["cells"]
    ax.bar(0, level, width=0.62, color=BLUE)
    ax.text(0, level + 3, f"{level:.1f} W", ha="center", va="bottom", fontsize=10, color=INK, weight="bold")
    for k, (key, _name, color, spec_text) in enumerate(steps, start=1):
        loss = max(flow[key], 0.0)
        ax.bar(k, loss, bottom=level - loss, width=0.62, color=color)
        ax.plot([k - 1 + 0.31, k - 0.31], [level, level], color=MUTED, lw=0.8)
        ax.text(k, level + 3, f"-{loss:.1f} W\n({100 * loss / flow['cells']:.0f} %)", ha="center", va="bottom", fontsize=9.5, color=INK)
        ax.text(k, -0.115, spec_text, ha="center", va="top", fontsize=7.8, color=INK2, transform=ax.get_xaxis_transform())
        level -= loss
    k = len(steps) + 1
    ax.plot([k - 1 + 0.31, k - 0.31], [level, level], color=MUTED, lw=0.8)
    ax.bar(k, level, width=0.62, color=BLUE)
    ax.text(
        k,
        level + 3,
        f"{level:.1f} W\n({100 * level / flow['cells']:.0f} %)",
        ha="center",
        va="bottom",
        fontsize=10,
        color=INK,
        weight="bold",
    )
    ax.text(
        k,
        -0.115,
        f"actuator-disk minimum\nT (V + v_ideal), 4 x {hover_thrust:.2f} N",
        ha="center",
        va="top",
        fontsize=7.8,
        color=INK2,
        transform=ax.get_xaxis_transform(),
    )
    ax.set_xticks(xs, ["from the cells"] + [st[1] for st in steps] + ["into the air\n(ideal, irreducible)"], fontsize=9.5, color=INK)
    ax.tick_params(axis="x", length=0, pad=6)
    for group, (a, b), color in (
        ("BATTERY + ESC", (0.6, 2.4), GOLD),
        ("MOTOR", (2.6, 4.4), PINK),
        ("PROPELLER", (4.6, 6.4), ORANGE),
        ("AIR", (6.6, 7.4), BLUE),
    ):
        ax.plot([a, b], [flow["cells"] * 1.18] * 2, color=color, lw=2.5, solid_capstyle="butt")
        ax.text((a + b) / 2, flow["cells"] * 1.2, group, ha="center", va="bottom", fontsize=9, color=INK, weight="bold")
    ax.set_ylabel("power (W)", fontsize=9, color=INK2)
    ax.set_ylim(0, flow["cells"] * 1.3)
    ax.set_xlim(-0.6, len(steps) + 1.6)
    ax.tick_params(axis="y", colors=MUTED, labelsize=8)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.grid(axis="y", color=GRID, lw=0.6)
    ax.set_axisbelow(True)
    ax.text(
        3.5,
        flow["cells"] * 0.33,
        "Largest avoidable loss: blade profile drag.   Largest uncertainty: section drag at Re ~ 1e5\n"
        "(cd0 +-30 % moves mission energy +-10 %).   Ranked work list: docs/efficiency.md",
        fontsize=9,
        color=INK2,
        va="center",
        ha="center",
        bbox={"boxstyle": "round,pad=0.6", "fc": "#f6f4ef", "ec": GRID},
    )

    fig.suptitle("10 in GMT tri-blade quadcopter: annotated system", fontsize=15, color=INK, x=0.06, ha="left", y=0.985)
    out = ROOT / "docs/images/system_annotated.png"
    fig.savefig(out, dpi=150, bbox_inches="tight", facecolor="white")
    print(out)


if __name__ == "__main__":
    main()
