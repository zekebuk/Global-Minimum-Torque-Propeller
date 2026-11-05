# The `frame` stage: a printed airframe sized around the prop

`prandtl_prop.frame` designs the PF10 airframe: a fully 3D-printed 10 in quad with an onboard Jetson,
modular PPA-CF arms on tapered sliding dovetails, a PAHT-CF body and PETG-HF accessories, printed on a
Bambu Lab X1C. It covers layout and arm optimisation, CadQuery parts with interference checks, Onshape
FeatureScript, and an X1C print plan. The frame itself is documented in `drone_frame/README.md`,
`drone_frame/docs/dovetail_joint.md` and `drone_frame/docs/test_plan.md`.

This page covers how the frame plugs into the rest of the pipeline.

```bash
prandtl run designs/gmt_fpv10_tri.yaml --stages frame          # any design with a multirotor section
prandtl run designs/gmt_pf10_tri.yaml --stages frame --set frame.build_cad=false   # analysis only (~2 min)
python drone_frame/run_pipeline.py                             # standalone, with the config placeholders
```

## What flows in

```
 aero ──► cad ──────────────┐
   │                         ├──► frame ──► runs/<design>/frame/<key>/frame/design_report.md, cad/, print_plan.md
   └────► multirotor ────────┘                                   onshape/ (FeatureScript + #pf10 variables)
```

| Frame input | Standalone (`drone_frame/config/frame.yaml`) | As a stage |
| --- | --- | --- |
| blades, diameter, air density | 2, 10 in, 1.225 (placeholder) | `operating_point` |
| static CT, CP | 0.110, 0.045 (generic 10x5, ASSUMPTION) | hover thrust and shaft power at the BEMT-trimmed hover rpm (`multirotor`) |
| max thrust per rotor | 22 N (ASSUMPTION) | `multirotor.thrust_to_weight` x hover thrust |
| torque at max thrust | constant-CP estimate | BEMT trimmed to max thrust (`multirotor`) |
| motor mass | 58 g (ASSUMPTION) | `multirotor.motor_mass(peak torque)`, the torque-sized outrunner |
| prop mass | 14 g (ASSUMPTION) | `cad.prop_mass_kg` (one-piece prop solid) or B x blade mass |
| excitation orders | 1, 2, 4, 6 (2-blade) | 1P + blade-pass multiples: 1, B, 2B, 3B |

The frame keeps its own payload (Jetson, battery, electronics), materials, joint and print limits. The
`frame:` design section (`FrameSpec`) points at those files and sets the run options. The stage's
cache key covers the frame code, both config files and the Onshape templates.

## The loop it closes

The multirotor stage sizes the prop and motor for `multirotor.mass_kg` and `arm_length_m`. The frame
stage then computes the real all-up mass and motor radius. Because `multirotor` runs first, a mismatch
can't be closed inside one run. The stage reports it as a warning that names the value to write back:
`multirotor.payload_mass_kg` (AUW minus the motors and props), which `prandtl design-gmt` uses to
re-size the vehicle.

The frame also constrains the prop. A printed PPA-CF arm of ~145 mm carrying an ~60 g motor tops out
near 145-160 Hz in first bending. Every arm mode must sit outside the cruise bands of the rotor orders
(1P and blade-pass multiples, each +/-15 %). That only works if the 1P cruise band, 1.15 x sqrt(1.3) x
1P hover, stays below what the arm can reach. In practice this caps the hover rpm the GMT design scan may
choose: `gmt.hover_rpm_grid` in `designs/pf10_tri_spec.yaml`.

## Loop results: the GMT tri-blade on the PF10 vehicle

Each pass is `prandtl design-gmt designs/pf10_tri_spec.yaml --output designs/gmt_pf10_tri.yaml`
followed by `prandtl run designs/gmt_pf10_tri.yaml --stages frame`. Pass 1 is the existing
`gmt_fpv10_tri` design, sized for a 1.45 kg quad.

| pass | spec change | prop (hover rpm, c_max/R) | frame AUW vs spec | frame hover rpm | lowest allowed window | best arm f_vert | arm |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `gmt_fpv10_tri` as-is | 7500, 0.14 | 2.078 vs 1.446 kg (+44 %) | 8990 | 196-391 Hz | 162 Hz | infeasible (-21.5 %) |
| 2 | payload 1.835 kg, cap 6750 rpm | 6750, 0.16 | 2.302 vs 2.158 kg (+7 %) | 6971 | 152-303 Hz | 145 Hz | infeasible (-4.9 %) |
| 3 | payload 1.960 kg, cap 6500 rpm | 6500, 0.17 | 2.336 vs 2.313 kg (+1 %) | 6532 | 143-284 Hz | 141 Hz | infeasible (-1.3 %) |

Pass 3 closes the vehicle. The AUW and arm length agree with the spec within 1 % and 0.1 %, there are 0
CAD clashes, and the dovetail slide-out and wedge-seat checks pass. The arm is still 1.3 % short of the
lowest allowed window, with width and root height at their bounds (28 x 34 mm, 115 g per arm; modes
141 / 159 / 277 Hz).

**This is the trade-off.** The 2.3 kg Jetson vehicle needs ~5.7 N per rotor at hover. A printed arm at
this length cannot be stiff enough unless the prop turns slowly, and a slower GMT prop needs more chord
and more torque. The ways out, each a design decision rather than a tuning knob:

1. **Cap hover lower** (`hover_rpm_grid` up to 6250 rpm). The window drops to ~137 Hz, so the current arm
   fits. Cost: chord grows further, and hover torque/power rises (pass 2→3 already took hover power from
   273 to 292 W).
2. **Allow a taller arm** (`arm.root_height_bounds` up to ~38 mm in `drone_frame/config/frame.yaml`).
   This raises EI by ~40 %. Cost: a taller body and ~10 g more per arm.
3. **Lighten the payload** (battery, Jetson carrier). Every 100 g off lowers hover rpm by ~2 %.
4. **Accept the 1.3 % shortfall.** It is inside the model's own uncertainty: torsion constant ±15 %,
   modulus knock-downs, joint compliance. Decide after the arm tap test (`drone_frame/docs/test_plan.md` 1.3).
