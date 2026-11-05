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
