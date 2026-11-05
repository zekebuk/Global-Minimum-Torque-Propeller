# PF10: a fully 3D-printed 10-inch FPV frame with an onboard Jetson

A hardware-development pipeline for a printed long-range quad. It covers modular PPA-CF arms, a PAHT-CF
body, and PETG-HF accessories, all printed on a Bambu Lab X1 Carbon. The airframe is sized around a Jetson
Orin NX/Nano companion computer.

One YAML file drives everything:

```
config/frame.yaml + config/materials.yaml
      │
      ├─► rotor model ─────────► loads, excitation bands (1P, 2P, 4P, 6P cruise bands)
      ├─► layout optimisation ─► motor positions / wheelbase (global 1-D optimum)
      ├─► arm optimisation ────► section, taper, wall loops, skin layers (mixed-integer DE)
      ├─► mass closure ────────► AUW ⇄ loads ⇄ arm ⇄ CAD mass until converged
      ├─► CadQuery CAD ────────► STEP + print-oriented STL, 24-pair interference check, bed fit, mass
      ├─► Onshape export ──────► FeatureScript custom features + #pf10_* variables
      ├─► X1C print plan ──────► per-part Bambu Studio settings
      └─► design report ───────► outputs/design_report.md (+ Campbell diagram, layout, renders)
```

## Quick start

```bash
cd drone_frame
python -m pip install -r requirements.txt
python run_pipeline.py            # full run, ~5-8 min on 4 cores
python run_pipeline.py --fast     # ~2 min, no material comparison
python -m pytest tests -q         # closed-form checks + CAD/model consistency
python onshape/onshape_bootstrap.py   # dry run of the Onshape document creation
```

Then read `outputs/design_report.md`, follow `onshape/SETUP.md` to build the Onshape document, and
print from `outputs/print_plan.md`.

## Design decisions, from first principles

**Stiffness, not strength, sizes a printed arm.** A 10-inch arm carries an ~80 g motor+prop about 145 mm from
the body. With PPA-CF's design modulus (~7.5 GPa after knock-downs), the stress at the limit load is a
few MPa against a ~93 MPa allowable. The binding constraints are the natural frequencies.

**Modes are placed, not pushed above everything.** Hover blade-pass is ~180 Hz. A printed arm stiff enough
to clear it would weigh more than 100 g each and still fail the constraints, and the optimizer proves that
inside the bounds. So the requirement is that every arm mode (vertical bending, lateral bending, torsion)
sits outside the cruise bands of the rotor orders that actually excite it: 1P (imbalance) and blade-pass
multiples (2P, 4P, 6P), each ±15 %. That leaves windows near 118–156 Hz and 235–312 Hz. Transient
crossings during punch-outs are handled by the Betaflight RPM filter, and the Campbell diagram in the report
shows them.

**Shortest legal arm wins.** Stiffness scales as 1/L³ and mass as L. Motor positions come from an exact
solve of the prop-prop, prop-canopy and tuning-symmetry constraints over the arm angle. The Jetson canopy is
taller than the prop plane, so the prop discs must clear its footprint in plan view.

**Every thickness is printable as sliced.** Web thickness = wall loops × line width and flange thickness =
skin layers × layer height. They are integer variables in the optimizer, so the optimum is a slicer setting,
not a number to round.

**The wire channel prints without support or bridges.** The arm prints top-face-down, and the channel's 45° V
becomes a pointed roof. The channel doubles as the closed torsion cell.

**Modular arms on a tapered sliding dovetail** ([docs/dovetail_joint.md](docs/dovetail_joint.md)). Every
arm is the same mirror-symmetric part. A 60° dovetail foot captures the arm vertically. A 1:25 plan taper
wedges it home with zero play and preload, turning print tolerance into axial travel; it also self-locks,
since tan 2.3° ≪ μ. The seat is flanks + floor only: the straight sides and top plate have clearance, so the
joint is not over-constrained. Upward flank load goes through the socket block **in compression** to the top
plate and back to the floor in steel tie bolts, so printed layers never carry tension. To swap an arm:
remove 2 screws from below, tap the arm out, unplug the MR30. The top plate stays on. The CAD check proves
the slide-out path is clear and that the arm wedges when pushed. Honest caveat: the stiffness-sized arm is
far stronger statically than any printed socket with M3 hardware. The joint is therefore sized for an 8×
crash case, and the "arm breaks first" ratio is reported (< 1), not claimed.

**Material assignment follows the physics.**

| Part | Material | Why |
| --- | --- | --- |
| Arms | **PPA-CF** | Highest specific stiffness: E/ρ is about 3× PAHT-CF and about 5× PETG-HF. The arm is stiffness-limited |
| Body, top plate, Jetson tray | **PAHT-CF** | Socket loads cross layers, and PAHT-CF has the best Z-bonding. It is tougher (8 % elongation), HDT 194 °C near the ESC/Jetson, and easier on a passive-chamber X1C |
| Canopy, camera cage, battery pad, GPS mast, legs | **PETG-HF** | Ductile and cheap; carries no stiffness-critical load |

**X1C reality is in the allowables.** The X1C hotend tops out at 300 °C, the bottom of the PPA-CF window,
and the chamber is passive. The PPA-CF process knock-down (0.65) is therefore harsher than PAHT-CF's (0.75).
Both CF polyamides need a 0.6 mm hardened nozzle, glue, and thorough drying.

**The Jetson sits on top, the battery at the bottom.** The Jetson gets fan inlet air from the top under the
prop wash, and the battery lowers the CG. The Jetson tray is soft-mounted on four silicone grommets on a
diamond pattern clear of the arm sockets.

## Assumptions you must replace

Search `ASSUMPTION` and `PLACEHOLDER` in `config/`. The most important ones:

1. **Jetson carrier hole rectangle** (`electronics.jetson.mount_rect`, 86 × 58 mm placeholder) and the
   envelope (100 × 80 × 36 mm).
2. **Motor / prop**: max thrust 22 N, CT 0.11, CP 0.045, motor mass 58 g. Replace with thrust-stand data
   (test plan 3.1). These move the excitation bands.
3. **Material knock-downs, densities, and the shear-modulus factor.** The torsion mode depends on G, which
   no TDS gives. Calibrate with coupons and the arm tap test (test plan 0.3, 1.3).
4. **Dovetail joint compliance** (Winkler layer 1 mm, 60 % contact when wedge-seated) and the PA-on-PA
   friction coefficient (0.25) behind the self-locking claim.

Mechanical properties are Bambu Lab TDS values (sources in `config/materials.yaml`). Densities, HDT for
PPA-CF and PETG-HF, and print temperatures were not on the pages retrieved and are flagged.

## Layout

| Path | Contents |
| --- | --- |
| `config/` | `frame.yaml` (requirements, components, bounds, loads), `materials.yaml` (TDS + knock-downs + X1C settings) |
| `frame_pipeline/` | `rotor`, `sections`, `beam_fe`, `layout`, `arm_model`, `arm_opt`, `analysis`, `geometry`, `cad/`, `onshape_export`, `print_plan`, `report` |
| `onshape/templates/` | FeatureScript templates; `onshape/generated/` is written by the pipeline |
| `onshape/SETUP.md` | Document structure, Part Studios, assembly mates, simulation, release |
| `onshape/onshape_bootstrap.py` | REST-API document creation (dry run by default) |
| `outputs/` | `design_report.md`, `analysis.json`, `frame_geometry.json`, `print_plan.md/.csv`, `cad/step`, `cad/stl`, `cad/cad_report.json`, `figures/` |
| `docs/test_plan.md` | Coupons → dovetail seat → arm tap/proof tests → thermal soak → thrust stand → flight logging, with calibration formulas |
| `docs/dovetail_joint.md` | Arm joint design: section sketch, rationale per feature, swap procedure, capacities, trade-offs |
| `tests/` | Closed-form section/beam checks, rotor bands, layout constraints, CAD-vs-model arm volume |

## Status and limits

* The geometry is checked by the pipeline: interference over 24 part pairs, X1C bed fit, the dovetail
  slide-out path and wedge seat, and the arm CAD volume against the beam model (within 8 %). Nothing has
  been printed or flown.
* The FeatureScript features and the Onshape API script were written without access to Onshape. Expect to
  fix small API/std-library mismatches on first paste. Onshape's error panel names the line.
* The beam/FE model is a design tool (see report §6). Onshape Simulation and the tap test are the checks.
