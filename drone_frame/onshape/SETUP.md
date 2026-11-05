# Onshape setup for PF10

The pipeline is the single source of truth. Onshape gets the optimized numbers through three generated
custom features and a variable table, all in `onshape/generated/` and rewritten on every
`python run_pipeline.py`. Reference STEP geometry for every part is in `outputs/cad/step/`.

There are two ways to use Onshape. Most people want both.

| Path | When | What you get |
| --- | --- | --- |
| **A. Native parametric** | Iterating on the design inside Onshape | Arm rebuilt by the `PF10 Modular Arm` feature; body modeled with `#pf10_*` variables and sockets cut by `PF10 Arm Socket` |
| **B. Reference import** | Fast start, drawings, simulation, checking A | Every part as imported STEP, positioned in the flight frame |

`onshape_bootstrap.py` automates creating the document, the Feature Studios and the STEP imports via the
Onshape REST API (API keys). The steps below are the manual equivalent.

---

## 0. Document structure

Create one document named `PF10 Frame` with these tabs (in this order):

| Tab | Type | Contents |
| --- | --- | --- |
| `FS PF10 Variables` | Feature Studio | `generated/PF10FrameVariables.fs` |
| `FS PF10 Arm` | Feature Studio | `generated/PF10ModularArm.fs` |
| `FS PF10 Socket` | Feature Studio | `generated/PF10ArmSocket.fs` |
| `Arm` | Part Studio | arm (native, path A) |
| `Body` | Part Studio | bottom body + top plate (native or imported) |
| `Electronics` | Part Studio | `envelopes.step` keep-out bodies (Jetson, stack, battery, motors, props, camera) |
| `Accessories` | Part Studio | Jetson tray, canopy, camera mount, battery pad, GPS mast, legs (imported STEP) |
| `PF10 Assembly` | Assembly | everything mated in the flight frame |
| `Reference STEP` | Imported files | `outputs/cad/step/*.step` (path B) |

Coordinate convention everywhere: **X forward, Y left, Z up**, origin at the body center on the underside
of the bottom plate. The arm Part Studio uses arm-local coordinates: the origin is at the top center of the
arm root face, +X runs toward the motor, and the top face is z = 0. So the Part Studio origin is the arm's
mate point.

## 1. Feature Studios

For each of the three `.fs` files:

1. **Insert → Feature Studio**. Name it as in the table.
2. Onshape writes two header lines (`FeatureScript NNNN;` and `import(...)`). The generated files use
   std version `2399`. If Onshape flags it, keep Onshape's two lines and paste the rest of the file below them.
3. Paste the file contents and click **Commit**. Then open the **Feature Studio's toolbar menu → Add custom features to toolbar**.

`PF10ModularArm.fs` and `PF10ArmSocket.fs` are independent; they don't import each other. Both contain
the same generated `tenonSection` function, so the dovetail tenon and its socket always match. The joint
is described in `docs/dovetail_joint.md`.

## 2. Variables

Pick one:

* **Feature (recommended):** in each Part Studio, insert **PF10 Frame Variables** as the first feature.
  Every `#pf10_*` variable is then defined in that Part Studio.
* **Variable Studio:** **Insert → Variable Studio** named `PF10 Variables`, then add the rows from
  `generated/variable_studio.txt` (or `variables.csv`). Reference it from each Part Studio with
  **Variable Studio reference**.

Because the variables come from the pipeline, never edit them by hand. Edit `config/frame.yaml` and re-run instead.

## 3. Part Studio `Arm` (PPA-CF)

1. `PF10 Frame Variables` (optional, for drawings).
2. **PF10 Modular Arm**. The defaults are the optimized arm. To drive it from variables, type e.g.
   `#pf10_arm_L` in the length field.
3. **Fillet**: 1.5 mm on the four long edges of the span and 0.8 mm on the pad rim. **Never fillet the
   dovetail flanks, the foot bottom, or the edges between them.** They are the seat. A 0.3 mm chamfer on
   the tenon's inner end eases insertion.
4. **Mate connector** at the origin (root-face top center, Z up, X along the arm). Name it `ARM_ROOT`.
5. **Assign material**: create a custom material `PPA-CF (printed)` with density **1.17 g/cm³**
   (ASSUMPTION; weigh a coupon). The arm prints 100 % solid, so CAD mass ≈ printed mass.
6. **Configurations** (optional): a list input `Motor` = {Both, 16x16, 19x19} driving the feature's
   *Motor pattern* parameter, and a list input `Variant` = {Optimised, PAHT-CF spare}. The second fills the
   arm parameters with the PAHT-CF optimum from `outputs/design_report.md` §3.

## 4. Part Studio `Body` (PAHT-CF)

**Native (path A).** Every dimension below is a `#pf10_*` variable:

1. `PF10 Frame Variables`.
2. Sketch on Top plane. Draw a center rectangle `2*#pf10_body_hx × 2*#pf10_body_hy`, then four socket bosses.
   Each boss is a rectangle on a construction line at ±`#pf10_arm_angle` and 180°∓`#pf10_arm_angle` from +X,
   running radially from `#pf10_root_radius - #pf10_tenon_L - #pf10_socket_wall - #pf10_socket_clear` to
   `#pf10_root_radius`, with width `2*#pf10_socket_half_w`.
3. Extrude the outline `#pf10_t_bp` to make the **floor**.
4. Extrude the outer rectangle region up from the floor by `#pf10_H_int`. **Shell** it to `#pf10_wall_t` with
   the top face removed. Then extrude the four boss regions solid over the same height.
5. **PF10 Arm Socket** (body mode): pick the body, keep "All four arms" on. The defaults match the arm.
   This cuts the tapered dovetail cavities, pigtail windows, tie-bolt holes and the radial retention-screw
   slots. Make the socket blocks at least `2*#pf10_socket_half_w` wide so the tie bolts land in solid wall.
6. Bosses (Ø7, sketch points at the coordinates listed in `outputs/frame_geometry.json` → `top_bolt_pts`,
   plus the 30.5 mm stack pattern and battery-pad points). Then **Hole** features: M3 heat-set insert pilot
   Ø4.0 × 6 mm.
7. **New part** `Top plate`: same outline, extruded `#pf10_t_tp` starting at `#pf10_z_arm_top`. Apply
   **PF10 Arm Socket** again with *Top-plate mode* on. That cuts the tie-bolt holes and the 0.3 mm relief
   pockets over the tenons. Then add holes at the top-bolt and grommet positions (`grommet_pts`) and the
   cable slot.
8. **Mate connectors** `SOCKET_0..3`: origin at (`#pf10_root_radius`·cos a, `#pf10_root_radius`·sin a,
   `#pf10_z_arm_top`), rotated by `a` about Z, for a = θ, −θ, 180+θ, 180−θ.
9. Material `PAHT-CF (printed)`, density 1.06 g/cm³ (ASSUMPTION). Bottom body fill factor ≈ 0.78, so
   expect printed mass ≈ 0.78 × CAD solid mass.

**Imported (path B).** Import `bottom_body.step` and `top_plate.step` into the Part Studio. They arrive in
the flight frame already.

Sanity check: in path A, measure your native body against the imported `bottom_body.step`. The volume
should match within a few percent. Fillets and insert bosses explain small differences.

## 5. `Electronics` and `Accessories`

Import `envelopes.step` (keep-out solids) and the accessory STEPs: `jetson_tray`, `canopy`,
`camera_mount`, `battery_pad`, `gps_mast`, `landing_leg`. All are in the flight frame except
`landing_leg`, which is in arm-local coordinates like the arm. Before you rely on the tray, **edit
`electronics.jetson.mount_rect` to match your carrier board and re-run the pipeline.** The default hole
rectangle is a placeholder.

## 6. Assembly `PF10 Assembly`

1. Insert `Body` (both parts) and **Fix** the bottom body.
2. Insert the arm 4 times. **Fastened mate** each `ARM_ROOT` to `SOCKET_i` (the nominal seated position).
   Because the arm is mirror-symmetric about its own XZ plane, one part number serves all four positions.
   To show a swap, use a **Slider** mate along the arm axis instead. The arm slides out through the
   cavity's widening taper with no clash.
3. Insert the electronics envelopes and accessories. They are already in the flight frame, so fasten each
   one to the body origin.
4. **Interference check**: right-click the assembly → *Show clashes*. The pipeline's own check
   (`outputs/cad/cad_report.json`) reports 0 clashes over 24 pairs. Onshape should agree.
5. **Mass properties**: with the custom materials assigned, compare against `design_report.md`. Remember
   the fill factors.

## 7. Simulation (Onshape Simulation, Professional/Enterprise)

Use it to cross-check the beam model, not to replace the coupon and tap tests.

* **Modal**: assembly with the arms fastened, bottom-body center fixed, the motor as a point mass
  (`motor_mass_g + prop_mass_g` at the motor CG). Expect the first modes near the pipeline's
  vertical/lateral/torsion values (see `design_report.md`). The pipeline models the socket joint with a
  Winkler spring, so Onshape's bonded contact will read slightly stiffer.
* **Linear static**: 27.5 N limit load upward on the pad top face. Compare tip deflection and root stress.
* Enter the materials as isotropic, using the pipeline's *design* values (`E_design`, `G` in the report),
  not the raw TDS numbers.

## 8. Release, export and print

* Part numbers: `PF10-ARM`, `PF10-BODY`, `PF10-TOP`, `PF10-TRAY`, `PF10-CAN`, `PF10-CAM`, `PF10-BAT`,
  `PF10-GPS`, `PF10-LEG`. Create an Onshape **Version** named after the git commit that produced the
  numbers.
* Export: right-click the part → **Export → STEP**, then open it in Bambu Studio. Bambu Studio meshes STEP
  itself, which is better than a coarse STL. Settings per part are in `outputs/print_plan.md`. Print-oriented
  STLs are also in `outputs/cad/stl/`.

## Round-trip rule

Design changes go **config → pipeline → Onshape**, never Onshape → pipeline. If you change a dimension in
Onshape for a quick test, mirror it in `config/frame.yaml` (bounds or fixed values) before the next pipeline
run, or the next paste will overwrite it.
