"""Design report (markdown + figures) from the analysis and CAD results."""

from __future__ import annotations

import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .rotor import rev_per_s_for_thrust

INK = "#1f2430"
MODE_COL = {"vertical bending": "#c0392b", "lateral bending": "#2471a3", "torsion": "#7d3c98"}


def campbell(cfg: dict, an: dict, path: Path) -> None:
    op, r = an["operating_point"], an["arm"]["result"]
    prop = cfg["propulsion"]
    T = np.linspace(0.05, 1.0, 200) * op["T_max_N"]
    n = np.array([rev_per_s_for_thrust(t, prop) for t in T])
    fig, ax = plt.subplots(figsize=(8, 5), dpi=140)
    for lo, hi in op["forbidden_bands_Hz"]:
        ax.axhspan(lo, min(hi, 900), color="#e74c3c", alpha=0.08, lw=0)
    ax.axvspan(op["T_hover_N"], op["T_hover_N"] * cfg["loads"]["cruise_thrust_factor"], color="#27ae60", alpha=0.12, lw=0,
               label="cruise thrust band")
    for k in cfg["loads"]["harmonics"]:
        ax.plot(T, k * n, color="#555", lw=1.0, ls="-" if k <= 2 else "--")
        ax.text(T[-1] * 1.01, k * n[-1], f"{k}P", fontsize=8, va="center", color="#555")
    for name, key in (("vertical bending", "f_vert_Hz"), ("lateral bending", "f_lat_Hz"), ("torsion", "f_tor_Hz")):
        ax.axhline(r[key], color=MODE_COL[name], lw=2, label=f"arm {name} {r[key]:.0f} Hz")
    ax.set_xlabel("thrust per motor [N]")
    ax.set_ylabel("frequency [Hz]")
    ax.set_ylim(0, 900)
    ax.set_xlim(0, op["T_max_N"] * 1.08)
    ax.set_title("Campbell diagram: arm modes vs rotor orders (red = banned cruise bands)", fontsize=10)
    ax.legend(fontsize=8, loc="upper left", frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def layout_plot(cfg: dict, an: dict, path: Path) -> None:
    lay = an["layout"]
    D = cfg["propulsion"]["prop_diameter"]
    fig, ax = plt.subplots(figsize=(6.5, 6.5), dpi=140)
    x0, x1, y0, y1 = lay["canopy_rect"]
    ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fc="#f5b041", alpha=0.35, ec="#b9770e", label="canopy + camera keep-out"))
    hx, hy = lay["body_half_dims"]
    ax.add_patch(plt.Rectangle((-hx, -hy), 2 * hx, 2 * hy, fc="none", ec=INK, lw=1.2, label="body"))
    for (mx, my) in lay["motor_positions"]:
        ax.add_patch(plt.Circle((mx, my), D / 2, fc="#5dade2", alpha=0.15, ec="#2471a3"))
        ax.plot([0, mx], [0, my], color="#444", lw=6, alpha=0.55, solid_capstyle="butt")
        ax.plot(mx, my, "o", color=INK)
    th = math.radians(lay["arm_angle_deg"])
    r0 = lay["root_radius"]
    ax.plot(r0 * math.cos(th), r0 * math.sin(th), "s", color="#c0392b", label="arm root face")
    ax.set_aspect("equal")
    lim = lay["motor_radius"] + D / 2 + 10
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_xlabel("x forward [mm]")
    ax.set_ylabel("y left [mm]")
    ax.set_title(f"Optimised layout: wheelbase {lay['wheelbase_diag']:.0f} mm, arm angle {lay['arm_angle_deg']:.1f} deg", fontsize=10)
    ax.legend(fontsize=7, loc="lower left", frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def material_plot(an: dict, path: Path) -> None:
    comp = an["material_comparison"]
    if not comp:
        return
    names = list(comp)
    mass = [comp[n]["result"]["mass"]["total_g"] for n in names]
    dl = [comp[n]["result"]["download_g"] for n in names]
    fig, ax = plt.subplots(figsize=(6, 3.4), dpi=140)
    ax.barh(names, mass, color="#566573", label="arm mass")
    ax.barh(names, dl, left=mass, color="#aab7b8", label="prop-download mass equivalent")
    for i, n in enumerate(names):
        tag = "feasible" if comp[n]["feasible"] else "INFEASIBLE"
        ax.text(mass[i] + dl[i] + 2, i, f"{mass[i] + dl[i]:.0f} g/arm  ({tag})", va="center", fontsize=8)
    ax.set_xlabel("objective per arm [g]")
    ax.set_xlim(0, max(m + d for m, d in zip(mass, dl)) * 1.45)
    ax.legend(fontsize=8, frameon=False, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def joint_section_plot(cfg: dict, an: dict, path: Path) -> None:
    from .geometry import derive, socket_r0, tenon_section  # noqa: F401

    g = derive(cfg, an)
    fig, ax = plt.subplots(figsize=(7, 4.2), dpi=140)
    x = -g.tenon_L / 2
    sw = g.socket_half_w  # the socket block is a constant-width box; only the cavity tapers
    ax.add_patch(plt.Rectangle((-sw, -g.arm_h_root - g.t_bp), 2 * sw, g.arm_h_root + g.t_bp, fc="#aeb6bf", ec="none", label="socket block / floor (PAHT-CF)"))
    sock = tenon_section(g, x, g.dv_side_clear, 0.0)
    ax.add_patch(plt.Polygon(sock, fc="white", ec="none"))
    ten = tenon_section(g, x)
    ax.add_patch(plt.Polygon(ten, fc="#2c2f36", ec="#2c2f36", label="arm tenon (PPA-CF)"))
    w = g.arm_b - 2 * g.arm_t_w
    zt, za = -g.arm_t_f, -g.arm_h_root + g.arm_t_f
    ax.add_patch(plt.Polygon([(0, za), (w / 2, za + w / 2), (w / 2, zt), (-w / 2, zt), (-w / 2, za + w / 2)], fc="white", ec="none"))
    ax.add_patch(plt.Rectangle((-sw, g.dv_top_relief), 2 * sw, g.t_tp, fc="#808b96", ec="none", label=f"top plate ({g.dv_top_relief} mm relief)"))
    for sy in (1, -1):
        ax.add_patch(plt.Rectangle((sy * g.tie_bolt_y - g.m3_clear / 2, -g.arm_h_root - g.t_bp - 1), g.m3_clear, g.arm_h_root + g.t_bp + g.t_tp + 2,
                                   fc="#c0392b", ec="none", label="M3 tie bolt" if sy > 0 else None))
        ax.add_patch(plt.Rectangle((sy * g.tenon_bolt_y - 2, -g.arm_h_root), 4, g.dv_insert_depth, fc="#d4ac0d", ec="none",
                                   label="retention insert" if sy > 0 else None))
        fl = [ten[0], ten[5]] if sy < 0 else [ten[1], ten[2]]
        ax.plot([p[0] for p in fl], [p[1] for p in fl], color="#27ae60", lw=3, label="seat: flanks + floor" if sy > 0 else None)
    ax.plot([ten[0][0], ten[1][0]], [ten[0][1], ten[1][1]], color="#27ae60", lw=3)
    ax.set_aspect("equal")
    ax.set_xlim(-sw - 4, sw + 4)
    ax.set_ylim(-g.arm_h_root - g.t_bp - 3, g.t_tp + 3)
    ax.set_xlabel("y [mm]")
    ax.set_ylabel("z from arm top [mm]")
    ax.set_title(f"Dovetail joint section at mid-tenon ({90 - g.dv_beta_deg:.0f} deg flanks, 1:{1 / g.dv_taper:.0f} taper)", fontsize=10)
    ax.legend(fontsize=7, loc="upper center", bbox_to_anchor=(0.5, -0.18), ncol=3, frameon=False)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def _row(*cells):
    return "| " + " | ".join(str(c) for c in cells) + " |"


def write_report(cfg: dict, mats: dict, an: dict, cad: dict | None, out_dir: Path, fig_dir: Path) -> Path:
    out_dir, fig_dir = Path(out_dir), Path(fig_dir)
    campbell(cfg, an, fig_dir / "campbell.png")
    layout_plot(cfg, an, fig_dir / "layout.png")
    material_plot(an, fig_dir / "material_comparison.png")
    joint_section_plot(cfg, an, fig_dir / "joint_section.png")
    op, lay, arm = an["operating_point"], an["layout"], an["arm"]
    r, d = arm["result"], arm["result"]["design"]
    rel = lambda p: Path(p).relative_to(out_dir).as_posix()
    L = []
    L += ["# PF10 design report", "",
          "Generated by `run_pipeline.py`; every number traces to `config/frame.yaml` and `config/materials.yaml`.",
          "Items marked ASSUMPTION in the config are placeholders until measured (see `docs/test_plan.md`).", ""]
    L += ["## Summary", "",
          _row("quantity", "value"), _row("---", "---"),
          _row("all-up weight", f"{an['auw_g']:.0f} g"),
          _row("printed frame (no legs)", f"{cad['frame_mass_g_without_legs']:.0f} g (CAD)" if cad else f"{an['frame_mass_g']:.0f} g (model)"),
          _row("arm (each, PPA-CF)", f"{r['mass']['total_g']:.1f} g"),
          _row("thrust/weight", f"{op['thrust_to_weight']:.2f}"),
          _row("hover", f"{op['T_hover_N']:.2f} N/motor, {op['rpm_hover']:.0f} rpm (1P {op['f1P_hover_Hz']:.0f} Hz)"),
          _row("wheelbase (motor-motor diagonal)", f"{lay['wheelbase_diag']:.1f} mm"),
          _row("arm modes vert / lat / torsion", f"{r['f_vert_Hz']:.0f} / {r['f_lat_Hz']:.0f} / {r['f_tor_Hz']:.0f} Hz"),
          _row("allowed modal windows", ", ".join(f"{lo:.0f}-{hi:.0f}" if hi != float('inf') and hi != 'inf' else f">{lo:.0f}" for lo, hi in op["allowed_windows_Hz"]) + " Hz"),
          _row("tip deflection / slope at limit load", f"{r['tip_defl_mm']:.2f} mm / {r['tip_slope_deg']:.2f} deg"),
          _row("arm joint", "tapered sliding dovetail (60 deg, 1:25 taper), see section 3b"),
          _row("joint crash capacity / crash moment", f"{r['joint_cap_Nm']:.0f} / {r['M_crash_Nm']:.0f} N m ({r['joint_governing']} governs)"),
          _row("fuse ratio (joint / arm root strength, reported)", f"{r['fuse_ratio']:.2f}"),
          ""]
    L += ["## 1. Operating point and excitation", "",
          f"Static rotor model T = CT rho n^2 D^4 (CT {cfg['propulsion']['ct_static']}, CP {cfg['propulsion']['cp_static']}). "
          f"Limit load = max thrust {op['T_max_N']:.1f} N x {cfg['loads']['dynamic_factor']} = {op['T_limit_N']:.1f} N; ultimate = limit x {cfg['loads']['safety_factor']}.", "",
          "A printed arm of this length cannot be stiff enough to sit above hover blade-pass (~"
          f"{op['fBP_hover_Hz']:.0f} Hz) at reasonable mass. The optimum PPA-CF arm with the frequency floor at "
          "1.15x blade-pass was infeasible inside the bounds. So the requirement is placement: every mode must sit "
          f"outside the cruise bands of orders {cfg['loads']['harmonics']} x 1P (cruise = hover..{cfg['loads']['cruise_thrust_factor']}x hover thrust, "
          f"+/-{(cfg['loads']['band_margin'] - 1) * 100:.0f} %). Transient sweeps during punch-outs are left to the RPM filter.", "",
          f"![Campbell]({rel(fig_dir / 'campbell.png')})", ""]
    L += ["## 2. Layout optimisation", "",
          "Arm stiffness goes as 1/L^3 and mass as L, so the shortest legal arm wins. For each arm angle the minimum motor radius is solved exactly from the "
          "prop-prop, prop-canopy and symmetry constraints. The free length R - r_root is then minimised over a 0.05 deg scan, which gives the global optimum.", "",
          _row("arm angle", "motor radius", "root radius", "free length", "front gap", "side gap", "canopy gap", "binding"),
          _row(*["---"] * 8),
          _row(f"{lay['arm_angle_deg']:.2f} deg", f"{lay['motor_radius']:.1f}", f"{lay['root_radius']:.1f}", f"{lay['arm_free_length']:.1f}",
               f"{lay['front_prop_gap']:.1f}", f"{lay['side_prop_gap']:.1f}", f"{lay['canopy_prop_gap']:.1f}", ", ".join(lay["binding_constraints"]) or "-"),
          "", f"![Layout]({rel(fig_dir / 'layout.png')})", ""]
    L += ["## 3. Arm section optimisation", "",
          "Mixed-integer differential evolution. Variables: width, root height, taper, wall loops, skin layers. Thicknesses are integer multiples of line "
          "width and layer height, so every candidate is printable as sliced. The objective is arm mass plus the mass equivalent of prop download on the arm.", "",
          _row("b", "h_root", "h_tip", "web", "flange", "channel at tip (w x h)"), _row(*["---"] * 6),
          _row(f"{d['b']:.2f}", f"{d['h_root']:.2f}", f"{d['h_tip']:.2f}", f"{d['t_w']:.2f} ({d['n_walls']} loops)",
               f"{d['t_f']:.2f} ({d['n_layers']} layers)", f"{r['channel_tip_w']:.1f} x {r['channel_tip_h']:.1f}"), "",
          "Constraint margins (>= 0 satisfied):", "", _row("constraint", "margin"), _row("---", "---")]
    L += [_row(k, f"{v:+.3f}") for k, v in sorted(r["margins"].items(), key=lambda kv: kv[1])]
    L += ["", f"Mass: span {r['mass']['span_g']:.1f} g + tenon {r['mass']['tenon_g']:.1f} g + pad {r['mass']['pad_g']:.1f} g; "
          f"prop download {r['download_N']:.3f} N (= {r['download_g']:.1f} g). Ultimate stress {r['sigma_ult_MPa']:.1f} MPa vs allowable "
          f"{mats[cfg['materials_assignment']['arm']].S_static:.1f} MPa; Goodman {r['goodman']:.3f}. Stiffness drives the design, not strength.", ""]
    j = cfg["arm"]["joint"]
    L += ["### 3b. Arm joint: tapered sliding dovetail", "",
          f"60 deg dovetail foot ({j['depth']} mm deep, flanks {j['flank_angle_deg']} deg from vertical), plan taper {j['taper_per_side']} per side, "
          f"{j['home_gap']} mm seat travel, flanks + floor line-on-line, straight sides +{j['side_clearance']} mm, top-plate relief {j['top_relief']} mm. "
          "Design rationale and the swap procedure are in `docs/dovetail_joint.md`.", "",
          f"![joint]({rel(fig_dir / 'joint_section.png')})", "",
          _row("quantity", "value"), _row("---", "---"),
          _row("joint rotational stiffness vert / lat / torsion", " / ".join(f"{r['joint'][k] / 1e3:.1f}" for k in ("vert", "lat", "tor")) + " kN m/rad"),
          _row("self-locking ratio (mu / taper)", f"{r['self_locking_ratio']:.1f} (>1 self-locks)"),
          _row("tolerance absorbed by seat travel", f"+/-{2 * j['taper_per_side'] * j['home_gap']:.2f} mm of width"),
          ]
    L += [_row(f"capacity: {k.replace('_', ' ')}", f"{v:.0f} N m") for k, v in r["joint_caps_Nm"].items()]
    L += [_row("crash case (crash_factor x limit root moment)", f"{r['M_crash_Nm']:.0f} N m"),
          _row("arm root static capacity", f"{r['M_arm_cap_Nm']:.0f} N m"),
          _row("fuse ratio", f"{r['fuse_ratio']:.2f}")]
    if cad and "joint" in cad:
        jj = cad["joint"]
        L += [_row("CAD: slide-out path (2-35 mm) clear", jj["slide_out_clear"]),
              _row("CAD: arm pushed 0.5 mm deeper interferes (wedge seats)", f"{jj['wedges_when_pushed']} ({jj['seat_check_interference_0p5mm_deeper_mm3']:.1f} mm3)")]
    L += ["", "The fuse ratio is below 1 on purpose, and it is reported rather than constrained. The stiffness-sized arm is about "
          f"{r['M_arm_cap_Nm'] / (r['M_crash_Nm'] / cfg['loads']['crash_factor']):.0f}x stronger than the limit load in quasi-static bending, "
          "and no printed socket with M3 hardware can be stronger than that. "
          "The joint is instead sized for the crash case, and the load path keeps printed layers in compression.", ""]
    if an["material_comparison"]:
        L += ["### Material comparison (same layout and loads)", "",
              _row("material", "E_design MPa", "feasible", "arm g", "objective g", "f_vert", "f_lat", "f_tor"), _row(*["---"] * 8)]
        for n, c in an["material_comparison"].items():
            cr = c["result"]
            L.append(_row(n, f"{mats[n].E:.0f}", c["feasible"], f"{cr['mass']['total_g']:.1f}", f"{cr['objective_g']:.1f}",
                          f"{cr['f_vert_Hz']:.0f}", f"{cr['f_lat_Hz']:.0f}", f"{cr['f_tor_Hz']:.0f}"))
        L += ["", f"![Materials]({rel(fig_dir / 'material_comparison.png')})", "",
              "PPA-CF's specific stiffness is about 3x PAHT-CF's and about 5x PETG-HF's, and arm mass follows it. PETG cannot reach the modal windows inside the bounds. "
              "The body is PAHT-CF rather than PPA-CF for its Z-bonding (loads cross layers in the sockets), 8 % elongation against 3 %, and easier printing on a passive-chamber X1C. "
              "The arm is the cheapest part to replace; see 3b for what actually limits the joint.", ""]
    L += ["## 4. Mass budget and closure", "", _row("item", "g"), _row("---", "---")]
    L += [_row(k, f"{v:.0f}") for k, v in an["components_g"].items()]
    L += [_row("printed frame", f"{an['frame_mass_g']:.0f}"), _row("**AUW**", f"**{an['auw_g']:.0f}**"), "",
          "Closure history (AUW -> loads -> layout -> arm -> AUW):", "", _row("iter", "AUW g", "arm g", "b mm", "L mm"), _row(*["---"] * 5)]
    L += [_row(h["iter"], f"{h['auw_g']:.0f}", f"{h['arm_g']:.1f}", f"{h['b']:.2f}", f"{h['L']:.1f}") for h in an["closure_history"]]
    if cad:
        L += ["", "## 5. CAD checks (CadQuery reference geometry)", "",
              _row("part", "material", "qty", "volume cm3", "fill", "mass each g", "print bbox mm", "fits X1C"), _row(*["---"] * 8)]
        for n, p in cad["parts"].items():
            L.append(_row(n, p["material"], p["qty"], f"{p['solid_volume_cm3']:.1f}", p["fill_factor"], f"{p['mass_each_g']:.1f}",
                          "x".join(str(round(v)) for v in p["print_bbox_mm"]), cad["bed_fit"][n]))
        n_cl = len(cad["clashes"])
        L += ["", f"Interference: {len(cad['interference'])} part pairs checked, **{n_cl} clashes**."
              + (" " + ", ".join(cad["clashes"]) if n_cl else ""), "",
              "![iso](figures/assembly_iso.png)", "", "![top](figures/assembly_top.png)", "", "![open](figures/body_open.png)", "",
              "![arm](figures/arm_iso.png) ![arm-bottom](figures/arm_bottom.png)", ""]
    L += ["## 6. Model limits (read before trusting the numbers)", "",
          "- Beam model: Euler-Bernoulli with a Bredt torsion constant (+/-15 %). No warping, no shear lag, no gyroscopic coupling. The spinning prop splits torsion and lateral modes into whirl pairs, so measure them.",
          "- Printed-material allowables are TDS coupon values x stated knock-downs. The torsion mode depends on the shear modulus, which is not on the TDS (factor assumed). **Tap-test the first printed arm** and recalibrate (docs/test_plan.md).",
          "- Joint: Winkler contact on the dovetail flanks and floor (1 mm compliant layer, 60 % contact assumed for the wedge-seated taper). Flank accuracy is the biggest print risk, so print the socket coupon first.",
          "- Rotor: constant static CT/CP; replace with thrust-stand data for the real motor/prop.",
          "- Crash: a quasi-static crash case (crash_factor x limit moment) sizes the joint. Impact dynamics and brittle notch failure are not modelled; test plan 1.4 checks them destructively.",
          ""]
    path = out_dir / "design_report.md"
    path.write_text("\n".join(L))
    return path
