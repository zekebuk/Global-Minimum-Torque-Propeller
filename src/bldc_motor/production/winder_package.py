"""Build the CNC winder package: CAD layout + mandrel STEP, drawings, G-code, firmware config, plots, BOM.

The machine CAD is a **concept layout** (envelopes of purchased parts plus the custom mandrel)
for checking reach, travel and needle-to-stator geometry, and for quoting the mandrel. It is not
a detailed frame design.
"""

from __future__ import annotations

import csv
import math
from pathlib import Path

from bldc_motor.design import drawings, mechanical, model
from bldc_motor.design import manufacturing_package as mp
from bldc_motor.production import winder as w

# world frame for the layout: stator axis = world Y (horizontal), needle points down (-Z world)
AXIS_HEIGHT = 90.0


def mandrel_profile(md: dict) -> list[tuple[float, float]]:
    """(r, y) revolved profile; y = 0 at stack mid-plane, base side negative (towards the spindle)."""

    hs = md["sleeve_len"] / 2
    y_relief = -hs - md["relief_back_len"]
    y_flange = y_relief - md["flange_len"]
    y_spigot = y_flange - md["spigot_len"]
    return [
        (0.0, y_spigot),
        (md["spigot_d"] / 2, y_spigot),
        (md["spigot_d"] / 2, y_flange),
        (md["flange_d"] / 2, y_flange),
        (md["flange_d"] / 2, y_relief),
        (md["relief_back_d"] / 2, y_relief),
        (md["relief_back_d"] / 2, -hs),
        (md["sleeve_d"] / 2, -hs),
        (md["sleeve_d"] / 2, hs),
        (0.0, hs),
    ]


def build_cad(m, analysis, tp, out: Path) -> dict:
    import cadquery as cq

    from bldc_motor.design import cad

    md = w.mandrel_dims(tp.geometry, tp.config)
    prof = mandrel_profile(md)
    mandrel = cq.Workplane("XZ").polyline(prof).close().revolve(360.0, (0, 0, 0), (0, 1, 0))
    slots = None
    for k in range(3):
        cut = cq.Workplane("XY").box(0.6, md["sleeve_d"] + 2, md["sleeve_len"] + 1.0).translate((0, 0, 0))
        cut = cut.rotate((0, 0, 0), (0, 0, 1), 0).translate((0, 0, 0))
        slab = (
            cq.Workplane("XZ")
            .center(0, 0)
            .rect(0.6, md["sleeve_d"] + 2)
            .extrude(-(md["sleeve_len"] / 2 + 0.5))
            .translate((0, md["sleeve_len"] / 2 + 0.5, 0))
        )
        slab = slab.rotate((0, 0, 0), (0, 1, 0), k * 120.0)
        slots = slab if slots is None else slots.union(slab)
    bore = (
        cq.Workplane("XZ")
        .circle(2.5)
        .extrude(-(md["sleeve_len"] / 2 + md["relief_back_len"]))
        .translate((0, md["sleeve_len"] / 2, 0))
    )
    mandrel = mandrel.cut(slots).cut(bore)
    files = {}
    out.mkdir(parents=True, exist_ok=True)
    cq.exporters.export(mandrel, str(out / "W-001_mandrel.step"))
    files["mandrel_step"] = out / "W-001_mandrel.step"

    # stator (wound) re-oriented: model z -> world y, centred on the stack mid-plane, raised to AXIS_HEIGHT
    zmid = (m.dims["z"]["stator_bottom"] + m.dims["z"]["stator_top"]) / 2

    def place(shape):
        return shape.translate((0, 0, -zmid)).rotate((0, 0, 0), (1, 0, 0), -90).translate((0, 0, AXIS_HEIGHT))

    stator = place(cad.stator_stack(m, analysis))
    coils = place(cad.coils(m, analysis))
    mandrel_w = mandrel.translate((0, 0, AXIS_HEIGHT))
    g = tp.geometry
    y_flange = prof[2][1]
    plate = cq.Workplane("XY").box(320, 240, 10).translate((0, -20, 5))
    blocks = (
        cq.Workplane("XY")
        .box(40, 13, AXIS_HEIGHT - 10)
        .translate((0, y_flange - 30, 10 + (AXIS_HEIGHT - 10) / 2))
        .union(
            cq.Workplane("XY").box(40, 13, AXIS_HEIGHT - 10).translate((0, y_flange - 70, 10 + (AXIS_HEIGHT - 10) / 2))
        )
    )
    spindle = cq.Workplane("XZ").circle(4).extrude(70).translate((0, y_flange, AXIS_HEIGHT))
    pulley = cq.Workplane("XZ").circle(16).extrude(8).translate((0, y_flange - 80, AXIS_HEIGHT))
    motor_a = cq.Workplane("XY").box(42, 40, 42).translate((0, y_flange - 100, 31))
    # Z (axial) bridge above the stator and the X (radial, vertical) slide carrying the needle
    bridge_z = AXIS_HEIGHT + 70
    posts = (
        cq.Workplane("XY")
        .box(20, 20, bridge_z - 10)
        .translate((-90, 0, 10 + (bridge_z - 10) / 2))
        .union(cq.Workplane("XY").box(20, 20, bridge_z - 10).translate((90, 0, 10 + (bridge_z - 10) / 2)))
    )
    beam = cq.Workplane("XY").box(200, 20, 20).translate((0, 0, bridge_z + 10))
    rail_z = cq.Workplane("XY").box(12, 200, 8).translate((0, 0, bridge_z - 4))
    carriage = cq.Workplane("XY").box(40, 45, 30).translate((0, 0, bridge_z - 23))
    rail_x = cq.Workplane("XY").box(12, 8, 60).translate((25, 0, bridge_z - 68))
    holder = cq.Workplane("XY").box(16, 14, 22).translate((12, 0, AXIS_HEIGHT + g["r_s"] + 32))
    needle_len = 21.0
    needle = (
        cq.Workplane("XY")
        .circle(tp.config.needle_od_mm / 2)
        .extrude(needle_len)
        .translate((0, 0, AXIS_HEIGHT + g["r_s"]))
    )
    spool = cq.Workplane("XZ").circle(30).extrude(60).translate((110, -110, 50))
    tensioner = cq.Workplane("XY").box(30, 30, 50).translate((110, -40, 135))
    asm = cq.Assembly(name="rl2407_cnc_winder")
    for name, shape, color in (
        ("base_plate", plate, (0.75, 0.75, 0.78)),
        ("pillow_blocks", blocks, (0.3, 0.3, 0.3)),
        ("spindle", spindle, (0.7, 0.7, 0.7)),
        ("pulley_100T", pulley, (0.9, 0.9, 0.9)),
        ("motor_A", motor_a, (0.15, 0.15, 0.15)),
        ("mandrel", mandrel_w, (0.85, 0.65, 0.2)),
        ("stator", stator, (0.35, 0.35, 0.38)),
        ("coils", coils, (0.8, 0.45, 0.15)),
        ("bridge_posts", posts, (0.6, 0.6, 0.65)),
        ("bridge_beam", beam, (0.6, 0.6, 0.65)),
        ("z_rail_MGN12", rail_z, (0.4, 0.4, 0.45)),
        ("z_carriage", carriage, (0.2, 0.4, 0.8)),
        ("x_rail_MGN12", rail_x, (0.4, 0.4, 0.45)),
        ("needle_holder", holder, (0.2, 0.7, 0.4)),
        ("needle_18RW", needle, (0.95, 0.95, 0.95)),
        ("wire_spool", spool, (0.8, 0.5, 0.2)),
        ("tensioner", tensioner, (0.5, 0.2, 0.2)),
    ):
        asm.add(shape, name=name, color=cq.Color(*color))
    asm.save(str(out / "W-000_winder_layout.step"))
    files["layout_step"] = out / "W-000_winder_layout.step"
    compound = cq.Compound.makeCompound(
        [
            s.val()
            for s in (
                plate,
                blocks,
                spindle,
                pulley,
                motor_a,
                mandrel_w,
                stator,
                coils,
                posts,
                beam,
                rail_z,
                carriage,
                rail_x,
                holder,
                needle,
                spool,
                tensioner,
            )
        ]
    )
    compound = compound.rotate(cq.Vector(0, 0, 0), cq.Vector(1, 0, 0), 180)
    cq.exporters.export(
        cq.Workplane().add(compound),
        str(out / "winder_isometric.svg"),
        opt={
            "width": 560,
            "height": 440,
            "marginLeft": 30,
            "marginTop": 30,
            "showAxes": False,
            "projectionDir": (1.0, -1.2, -0.8),
            "strokeWidth": 0.3,
            "showHidden": False,
        },
    )
    files["isometric"] = out / "winder_isometric.svg"
    return files


def mandrel_sheet(md: dict, mass_g: float | None) -> drawings.Sheet:
    sh = drawings.Sheet(
        "DWG-W01",
        "WINDER MANDREL (EXPANDING)",
        "Tool steel 1.2842 / O1, hardened 54-58 HRC (sleeve); 4140 body",
        "Black oxide; sleeve OD ground",
        "4:1",
        mass_g,
    )
    sh.project = "CNC WINDER"
    prof = mandrel_profile(md)
    # draw axis horizontal: (r, y) -> (y, r)
    v = drawings.View(sh, 230, 130, 4.0, "SECTION A-A (SLEEVE SLOTTED 3x120 DEG, 0.6 WIDE)", label_y=200)
    outline = [(y, r) for r, y in prof] + [(y, -r) for r, y in reversed(prof)]
    v.poly(outline, "vis", True, "metal")
    for r, y in prof:
        if r > 1e-6:
            v.line(y, -r, y, r, "vis")
    v.line(prof[0][1] - 3, 0, prof[-1][1] + 3, 0, "ctr")
    y_sl0, y_sl1 = -md["sleeve_len"] / 2, md["sleeve_len"] / 2
    v.dim_v(-md["sleeve_d"] / 2, md["sleeve_d"] / 2, y_sl1, 14, f"Ø{md['sleeve_d']:.3f} -0.005/-0.015 (EXPANDS +0.05)")
    v.dim_v(
        -md["relief_back_d"] / 2,
        md["relief_back_d"] / 2,
        y_sl0 - md["relief_back_len"] / 2,
        -26,
        f"Ø{md['relief_back_d']:.2f} MAX (NEEDLE LINK PATH)",
    )
    v.dim_v(-md["flange_d"] / 2, md["flange_d"] / 2, prof[3][1], -14, f"Ø{md['flange_d']:.1f}")
    v.dim_v(-md["spigot_d"] / 2, md["spigot_d"] / 2, prof[0][1] + 4, -12, f"Ø{md['spigot_d']:.0f} h6 + FLAT")
    v.dim_h(y_sl0, y_sl1, md["sleeve_d"] / 2, 10, f"{md['sleeve_len']:.2f} SLEEVE")
    v.dim_h(y_sl0 - md["relief_back_len"], y_sl0, md["relief_back_d"] / 2, 18, f"{md['relief_back_len']:.2f} MIN")
    v.dim_h(prof[0][1], y_sl1, -md["flange_d"] / 2, -10, f"{y_sl1 - prof[0][1]:.2f} OVERALL")
    sh.notes = [
        "Grips the stator BORE ONLY: nothing may touch the stator end faces (the needle lays links on the base-side yoke face).",
        f"Back relief Ø{md['relief_back_d']:.2f} max over {md['relief_back_len']:.2f} mm is set by the needle link path (r_link - needle/2 - 0.3).",
        f"{md['draw_screw']}; front clamp washer Ø13.2 max contacts the bore lip only. Axial position set with a removable loading gauge.",
        "Sleeve OD runout to spigot <= 0.01 mm. A0 index: loading gauge fork engages the slot before tooth T1.",
    ]
    return sh


def toolpath_plot(tp: w.Toolpath, path: Path) -> Path:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    g = tp.geometry
    moves = [mv for mv in tp.moves if mv.kind != "pause"]
    # first coil only
    start = next(i for i, mv in enumerate(moves) if "turn 1/" in mv.comment)
    end = next((i for i, mv in enumerate(moves) if i > start and "link" in mv.comment), len(moves))
    seg = moves[start - 1 : end]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.2))
    phi = [-mv.a for mv in seg]
    ax1.plot(phi, [mv.z for mv in seg], "-", color="C0", lw=1)
    ax1.axhspan(
        -g["end_turn_top"], g["end_turn_top"], color="0.85", label="stack + end turns (keep-out unless in slot opening)"
    )
    for k in range(-1, 2):
        c = k * g["pitch_deg"]
        ax1.axvspan(
            c - math.degrees((g["tooth_width"] / 2) / g["r_s"]),
            c + math.degrees((g["tooth_width"] / 2) / g["r_s"]),
            color="0.6",
            alpha=0.6,
        )
    ax1.set_xlabel("needle angle relative to stator φ [deg] (tooth T1 at 0)")
    ax1.set_ylabel("axial z [mm] (0 = mid-stack, - = base side)")
    ax1.set_title("Needle path, coil T1 (unrolled)")
    ax1.legend(fontsize=7, loc="upper right")
    ax1.grid(alpha=0.3)
    ax2.plot(range(len(seg)), [mv.x for mv in seg], "C3.-", ms=3, lw=0.8)
    ax2.axhline(g["r_open"], color="k", ls="--", lw=0.8, label="slot opening radius")
    ax2.axhline(g["r_root"], color="C2", ls=":", lw=0.8, label="layer 1 start (root)")
    ax2.axhline(g["r_max"], color="C1", ls=":", lw=0.8, label="layer 1 end (under shoe)")
    ax2.set_xlabel("move index (coil T1)")
    ax2.set_ylabel("needle radius X [mm]")
    ax2.set_title(f"Radial layering: {g['turns_per_layer']} turns/layer, pitch {g['layer_pitch']:.3f} mm")
    ax2.legend(fontsize=7)
    ax2.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(path, dpi=140)
    plt.close(fig)
    return path


def build_winder_package(design_path: str | Path, out_dir: str | Path, name: str = "RL2407") -> dict:
    design, _ = mp.load_design(design_path)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    analysis = model.analyze(design, name=name)
    mech = mechanical.derive(analysis, name)
    tp = w.generate(mech, analysis)
    gcode = w.to_gcode(tp, name)
    (out / f"{name.lower()}_winding.gcode").write_text(gcode, encoding="ascii")
    (out / "fluidnc_config.yaml").write_text(w.fluidnc_config(), encoding="utf-8")
    verification = w.verify(w.parse_gcode(gcode), tp.geometry)
    checks = w.clearance_report(mech, analysis)
    timing = w.cycle_time(tp)
    loads = w.axis_loads(
        tp, mech.dims["stack"] and analysis.internals["mass_stator_iron_g"] + analysis.internals["mass_copper_g"]
    )
    toolpath_plot(tp, out / "toolpath.png")
    with (out / "winder_bom.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["item", "qty", "unit_usd", "total_usd", "source"])
        for item, qty, unit, src in w.WINDER_BOM:
            writer.writerow([item, qty, f"{unit:.2f}", f"{qty * unit:.2f}", src])
        writer.writerow(["TOTAL", "", "", f"{w.bom_total():.2f}", ""])
    files = {
        "gcode": out / f"{name.lower()}_winding.gcode",
        "config": out / "fluidnc_config.yaml",
        "plot": out / "toolpath.png",
        "bom": out / "winder_bom.csv",
    }
    try:
        files.update(build_cad(mech, analysis, tp, out / "cad"))
        sheet = mandrel_sheet(w.mandrel_dims(tp.geometry, tp.config), None)
        sheet_path = out / "cad" / "DWG-W01_mandrel.svg"
        sheet_path.write_text(sheet.render(1, 1), encoding="utf-8")
        files["mandrel_drawing"] = sheet_path
    except ImportError:
        pass
    expected = [analysis.design["turns"] * dirn for _, dirn in analysis.layout.coils]
    return {
        "files": {k: str(v) for k, v in files.items()},
        "verification": verification,
        "expected_turns": expected,
        "turns_ok": [round(t) for t in verification["turns_per_tooth"]] == expected,
        "checks": checks,
        "timing": timing,
        "loads": loads,
        "geometry": tp.geometry,
        "mandrel": w.mandrel_dims(tp.geometry, tp.config),
        "bom_total": w.bom_total(),
        "wire_m": w.wire_length_m(tp, analysis),
        "gcode_lines": len(gcode.splitlines()),
    }


def write_readme(result: dict, out_dir: str | Path, name: str = "RL2407") -> Path:
    g, t, ld, md = result["geometry"], result["timing"], result["loads"], result["mandrel"]
    v = result["verification"]
    rows = "\n".join(f"| {c[0]} | {c[1]:.3f} | {c[2]} | {'OK' if c[3] else 'FAIL'} |" for c in result["checks"])
    bom = "\n".join(f"| {item} | {qty} | ${qty * unit:,.2f} | {src} |" for item, qty, unit, src in w.WINDER_BOM)
    text = f"""# From-scratch CNC needle winder ({name} stator)

Generated by `bldc-motor design winder`. It replaces 10 min of hand winding with about
**{t["station_min"]:.1f} min per stator** ({t["machine_min"]:.2f} min machine + {t["operator_min"]:.1f} min operator),
for about **${result["bom_total"]:,.0f}** of hardware ({result["bom_total"] / 15000 * 100:.1f} % of the $15k line budget) instead of the $15-25k+ of a commercial winder.

> Status: concept design, rev P1. The kinematics, toolpath, clearances and drive sizing are computed
> and verified in software. The frame is a layout, not a detailed design. Most BOM prices are
> estimates (sources in `winder_bom.csv`). Build it, dry-run without wire, then wind first articles
> and accept them on the electrical tests in DWG-003.

## Why build one (and why not adapt a generic coil winder)

Outrunner stators need **needle (flyer-less) winding**: the wire must go *around each tooth*, not
onto a spinning bobbin. The RL2407 design was optimised for easy winding: 6 turns per tooth,
single-strand AWG23, a 2.5 mm slot opening and 0.38 slot fill. That makes a 3-axis machine
sufficient:

| Axis | Function | Drive | Speed / accel | Resolution |
| --- | --- | --- | --- | --- |
| A | stator rotation (wraps over the tooth end faces) | NEMA17 + 5:1 GT2 belt, 8 mm spindle | {result["loads"]["a_motor_rpm"]:.0f} motor rpm, 20,000 deg/s² | {ld["a_resolution_deg"]:.4f} deg = {ld["a_resolution_um_at_od"]:.1f} µm at the stator OD |
| Z | axial pass through the slot | NEMA17 + T8 (8 mm lead) on MGN12H | 100 mm/s, 3,000 mm/s² ({ld["z_motor_rpm"]:.0f} rpm) | {ld["lead_resolution_um"]:.1f} µm |
| X | radial layer position | NEMA17 + T8 (8 mm lead) on MGN12H | 50 mm/s, 2,000 mm/s² ({ld["x_motor_rpm"]:.0f} rpm) | {ld["lead_resolution_um"]:.1f} µm |

A-axis torque at the output is {ld["a_output_torque_nm"] * 1000:.1f} mN·m (inertia plus {w.DEFAULT_CONFIG.tension_n:.1f} N wire tension at the OD),
or {ld["a_motor_torque_nm"] * 1000:.1f} mN·m at the motor including rotor inertia, {ld["a_motor_torque_nm"] / 0.45 * 100:.1f} % of a 0.45 N·m NEMA17. Z force is {ld["z_force_n"]:.1f} N.
Motor speeds are capped at about 750 rpm, where 24 V NEMA17s keep most of their torque.

## Needle strategy (the key insight)

The needle is an **18RW stainless hypodermic tube** (OD 1.27 / ID 0.84 mm, McMaster 8988K48). It
**never enters the copper region of a slot**:

1. Axial passes (Z) happen with the needle tip centred in the **slot opening** (r = {g["r_open"]:.2f} mm), where there is
   no copper, leaving {(g["slot_opening"] - 1.27) / 2:.2f} mm clearance to each shoe tip.
2. Beyond each stack end (|z| = {g["z_clear"]:.2f} mm, {result["checks"][2][1]:.2f} mm above the end turns), the needle moves in
   to the layer radius and the stator rotates the needle across the tooth end face. The wire wraps the tooth at that radius.
3. Tension pulls the wire in the slot straight between the two end-face wraps, so layering is set by X:
   {g["turns_per_layer"]} turns per layer at {g["layer_pitch"]:.3f} mm pitch from r = {g["r_root"]:.2f} (root) to {g["r_max"]:.2f} mm (under the shoe).
4. Inter-coil links run on the base side **on the yoke face (r = {g["r_link"]:.2f} mm)**, never at the OD, where they would
   foul the 0.5 mm magnet overhang. That forces the fixture design: the stator is gripped **by its bore only**,
   on an expanding mandrel (DWG-W01) with a Ø{md["relief_back_d"]:.2f} mm back relief.

![toolpath](toolpath.png)

## Verified in software

The G-code is decoded by an **independent parser**. The signed winding number of the needle path around every
tooth must equal the winding table, and every segment is checked against the stator keep-out
(the stack plus end turns, except the slot openings):

- turns per tooth: `{v["turns_per_tooth"]}`
- expected (DWG-003): `{result["expected_turns"]}` → **{"MATCH" if result["turns_ok"] else "MISMATCH"}**
- collisions: **{v["collision_count"]}**; a deliberately bad move (needle into a slot at mid-stack) is caught by the test suite.

| Clearance check | Value | Limit | Result |
| --- | --- | --- | --- |
{rows}

## Bill of materials (≈ ${result["bom_total"]:,.0f})

| Item | Qty | Total | Source / status |
| --- | --- | --- | --- |
{bom}

## Build and commissioning

1. **Frame:** 10 mm aluminium plate with a 2020 bridge. Put the headstock (pillow blocks, spindle, 5:1 belt) on the plate,
   and the Z rail on the bridge with the X slide hanging from the Z carriage. Keep the needle axis radial,
   intersecting the spindle axis within 0.05 mm (shim the X slide).
2. **Electronics:** Elecrow 6x FluidNC board, 3 × TMC2209 at 24 V, limit switches on X/Z, an optical flag on A, the E-stop
   in the feed-hold chain, and a foot pedal on cycle start. Load `fluidnc_config.yaml` (fill in the pins from the board docs).
3. **Zeroing:** A0 = tooth T1 under the needle, set with the loading gauge. Z0 = stack mid-plane and X0 = spindle axis, set with a
   turned zero gauge on the mandrel. The program assumes these work offsets.
4. **Wire path:** spool → magnetic tensioner (set {w.DEFAULT_CONFIG.tension_n:.1f} N, about 12 % of the AWG23 yield force, to avoid stretching the wire and raising R)
   → ceramic eyelet → needle. Polish the needle exit to a 0.2 mm radius.
5. **Dry run** without wire at 25 % feed override, watching the end-face clearances. Then run at 100 %.
6. **First articles:** wind 5 stators; check turns visually and R line-line within ±6 % with imbalance ≤ 2 %, then hipot.
   Tune tension and X layer pitch (`layer_pitch_factor`) until the layers are tight.

## Operator cycle (per stator)

Load the stator on the mandrel and expand it (gauge sets the axial position and T1), anchor the phase-A start lead on post SA, and press the pedal.
At each pause, cut and anchor the finish lead and the next phase's start ({t["pauses"]} pauses in total). Unload.
Machine time {t["machine_min"]:.2f} min; wire laid {result["wire_m"]:.2f} m per stator.

## Files

| File | Content |
| --- | --- |
| `{name.lower()}_winding.gcode` | {result["gcode_lines"]}-line program (X, Z, A; M0 pauses with messages) |
| `fluidnc_config.yaml` | Controller config skeleton (steps/mm, limits, homing) |
| `winder_bom.csv` | Hardware BOM with sources |
| `toolpath.png` | Unrolled needle path and radial layering for coil T1 |
| `cad/W-000_winder_layout.step`, `cad/winder_isometric.svg` | Concept layout (stator, mandrel, axes, needle, spool, tensioner) |
| `cad/W-001_mandrel.step`, `cad/DWG-W01_mandrel.svg` | Expanding mandrel, the only custom precision part |

## Risks and mitigations

- **Wire springback at the end-face wraps.** Add a short dwell or a small overtravel in A (a `WinderConfig` change), and validate on first articles.
- **Tension variation:** a magnetic tensioner is needed for repeatable layering. The DIY felt brake is acceptable for pilots only.
- **Needle wear and enamel damage:** polish the exit and inspect it every 500 stators. Hipot at S5 catches enamel nicks.
- **Open-loop steppers** can miss steps without detection. Keep the motors at ≤ 25 % of their torque (they run at about {ld["a_motor_torque_nm"] / 0.45 * 100:.0f} %) and home A every stator.
"""
    path = Path(out_dir) / "README.md"
    path.write_text(text, encoding="utf-8")
    return path
