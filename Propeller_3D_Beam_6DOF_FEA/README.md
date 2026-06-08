# Propeller 3D Beam 6DOF FEA

This folder contains a reduced-order 3D beam/frame finite element model for propeller blade structural screening. It is separate from `Propeller_Beam_FEA` and does not depend on or overwrite that code.

The main folder intentionally keeps the scripts you run directly. Solver,
plotting, export, and math support functions live in `helpers/`; the runnable
scripts add that folder to the MATLAB path automatically.

## What The Model Does

The blade is represented by spanwise beam elements. Each CSV radial station becomes a node, or the CSV can be resampled to a requested node count. Adjacent radial stations are connected by 2-node 3D frame elements.

Each node has 6 degrees of freedom:

- `ux`: axial/spanwise displacement
- `uy`: edgewise/in-plane displacement
- `uz`: flapwise/out-of-plane displacement
- `rx`: twist rotation about the local spanwise axis
- `ry`: bending rotation about the local y axis
- `rz`: bending rotation about the local z axis

Each 2-node element has 12 total DOF and uses a standard Euler-Bernoulli 3D frame stiffness matrix with `EA`, `GJ`, `EIy`, and `EIz`.

The element formulation follows the Chapter 10 three-dimensional frame
reference:

- local element stiffness uses `EA/L`, `GJ/L`, `12EI/L^3`, `6EI/L^2`,
  `4EI/L`, and `2EI/L`
- the local/global transform uses `u_local = B*u_global`
- element stiffness is transformed with `K_e = B'*k_e*B`
- local element force recovery uses `f_e = k_e*B*U_e + f0e`

## Geometry Input

Geometry is read from CSV using `read_prop_geometry.m`. The reader accepts common column names including:

- `r/R`, `mu`, `r_over_R`
- `radius_m`, `radius_in`, `r`
- `c/R`, `chord_over_R`
- `chord_m`, `chord_in`, `chord`
- `twist_deg`, `beta_deg`
- `airfoil`

If only normalized radius and chord are present, specify the propeller radius in meters. The default is a 22 inch diameter propeller:

```matlab
radius_m = 0.2794;
```

## Coordinate System

For each element:

- local `x` is along the beam from node `i` to node `i+1`
- local `y` is chordwise / edgewise
- local `z` is thickness / flapwise

Average blade twist from the two element nodes rotates the local `y/z` axes about the local `x` axis. The element transform is built so:

```matlab
u_local = T*u_global
k_global = T'*k_local*T
```

## Section Properties

The section is approximated as a solid-equivalent airfoil rectangle:

- chord `c`
- thickness `t = thickness_ratio*c`
- `A ~= area_factor*c*t`
- `Iy ~= inertia_factor*c*t^3/12`
- `Iz ~= inertia_factor*t*c^3/12`
- `J` uses a thin-rectangle torsion estimate with a torsion factor

This is intended for reduced-order screening and comparison, not local solid stress prediction.

## Loads

The model applies distributed element loads:

- centrifugal load: `q_x(r) = rho*A(r)*omega^2*r`
- thrust/flapwise load: `q_z(r)`
- optional edgewise/tangential load: `q_y(r)`
- optional distributed torsional moment: `m_x(r)`
- optional torsion from an aerodynamic-center / elastic-axis chordwise offset

The Chapter 10 constant distributed load vector covers local `w_x`, `w_y`,
and `w_z`. This solver also supports propeller distributed torsion `m_x` by
adding a custom equivalent nodal torsion load to local rotational `rx`:

```matlab
Tx1 = mx*L/2;
Tx2 = mx*L/2;
```

Internally both load conventions are retained:

- `fe_local` / `applied_load_local`: applied equivalent nodal load
- `f0e_local`: Chapter 10 restoring fixed-end load, equal to `-fe_local`

Thrust load distributions can be:

- `uniform`
- `elliptical`
- `bell`
- `csv`

The thrust distribution is normalized so its integral over the modeled blade equals the specified total thrust per blade.

By default both the elastic axis and aerodynamic center are placed at quarter chord, so no extra thrust-induced torsion is created without better geometry. To study torsion from load offset, set:

```matlab
elastic_axis_fraction_chord = 0.35;
aero_center_fraction_chord = 0.25;
include_aero_torsion_from_offset = true;
```

## Boundary Conditions

The root node is fully fixed:

```matlab
ux = uy = uz = rx = ry = rz = 0
```

The blade is treated as a cantilever clamped at the hub/root station.

The static solve uses the partitioned Chapter 10 form:

```matlab
Uf = Kff \ (Ff - F0f - Kfr*Ur)
```

For the usual clamped root, `Ur = 0`.

## Running

Run the final locked MIL/GMT static and modal cases:

```matlab
cd Propeller_3D_Beam_6DOF_FEA
main_propeller_3d_beam_fea
```

The main driver reads:

- `../../results/FINAL_THESIS_USE_THESE/01_geometry/final_mil_geometry.csv`
- `../../results/FINAL_THESIS_USE_THESE/01_geometry/final_gmt_geometry.csv`
- `../../results/FINAL_THESIS_USE_THESE/02_aero_validation/mil_bemt_validation_LOCKED.csv`
- `../../results/FINAL_THESIS_USE_THESE/02_aero_validation/gmt_bemt_validation_LOCKED.csv`
- `../../results/FINAL_THESIS_USE_THESE/02_aero_validation/locked_aero_summary.json`

Run the older reference/optimized MIL vs GMT comparison:

```matlab
run_mil_gmt_comparison
```

This runs only the 22-inch geometry. For `Reference_Propeller_Geometry.xlsx`,
the GMT table is read from columns `L:Q`, rows `5:43`, and the MIL table is
read from columns `L:Q`, rows `55:92`; the 18-inch block is ignored.

Run validation checks:

```matlab
validate_chapter10_frame_theory
validate_quiz_cases_A_to_E
validate_cantilever_tip_load
validate_axial_bar
validate_torsion
```

Plot a results CSV with the ME504 2D plotting app:

```matlab
plot_propeller_with_me504_app
plot_propeller_with_me504_app([], 'span-flap')
```

This creates a visualization-only 2D quad ribbon from the beam stations and passes `x`, `elem`, `ul`, and `sv` into `UI_2D_PLOT_CP`. The ME504 app must be installed from `ME 504/UI_2D_PLOT_CP/UI_2D_PLOT_CP.mlappinstall`.

If the ME504 app p-code is damaged or incompatible with your MATLAB install, use the native viewer:

```matlab
plot_propeller_native_viewer
plot_propeller_native_viewer([], 'span-flap')
plot_propeller_native_viewer([], '3D isometric')
```

The native viewer has engineering validation views for 3D ribbon contours, span-flap/span-edge projections, spanwise line plots, internal force diagrams, section property plots, load distribution plots, deformed/undeformed overlays, and imported hand/Abaqus comparisons. The 3D view uses chord, twist, displacement, and nodal rotations to draw a twisted chord ribbon; it is still a beam-result visualization, not a solid/shell mesh.

The `Color Bands` input controls only the number of visual color intervals used in the plot colormap. It does not change the solver results, station spacing, or mesh resolution.

Station probing accepts a station number, an element string like `e3`, an `r/R` value, or an approximate radius in meters. Export buttons can write the current plot, the augmented station table, and the current summary table.

## Outputs

Plots and CSV files are written under:

```text
../../results/fea/
../../results/plots/fea/
```

The main solver writes clean, stable final locked folders:

```text
../../results/fea/beam_solver/final_locked_mil_gmt/final_locked_mil
../../results/fea/beam_solver/final_locked_mil_gmt/final_locked_gmt
../../results/fea/beam_solver/final_locked_mil_gmt/final_locked_mil_gmt_fea_summary.csv
../../results/plots/fea/beam_solver/final_locked_mil
../../results/plots/fea/beam_solver/final_locked_gmt
```

Each case folder is cleared at the start of each run, then rewritten with
the latest CSVs and plots. The older MIL/GMT comparison script rewrites two folders:

```text
../../results/fea/comparisons/reference_mil_vs_gmt
../../results/fea/comparisons/optimized_mil_vs_gmt
../../results/plots/fea/comparisons
```

The station results CSV includes:

- `r/R`, `r_m`, `chord_m`, `twist_deg`
- section properties: `A`, `Iy`, `Iz`, `J`
- nodal displacement/rotation: `ux`, `uy`, `uz`, `rx`, `ry`, `rz`
- recovered resultants: `N`, `Mx_torsion`, `My_bending`, `Mz_bending`
- approximate stresses: `sigma_axial`, `sigma_flap`, `sigma_edge`, `tau_torsion`, `sigma_combined`

The comparison script also exports:

- summary metrics for reference MIL/GMT and optimized MIL/GMT geometries
- paired radial stress data for a paired t-test
- combined-stress and modal-frequency comparison plots

The default plot set includes both centerline deflection and a twisted chord ribbon colored by approximate combined beam stress. The simplified beam view shows the reduced-order structural model. Ribbon view is for visualization and stress envelope display; it is not a shell or solid mesh.

Element midpoint stress is available as a one-point element evaluation in
`stress.element_one_point`. The station-level stress curves use nodal
projection from adjacent element resultants for smoother visualization and
should be labeled as visualization only.

## Assumptions And Limitations

- This is a reduced-order beam model, not 3D solid FEA.
- It does not capture local bolt-hole bearing stress.
- It does not capture washer/contact/clamp effects.
- It does not capture root fillet stress concentration accurately.
- It does not capture detailed forged carbon fiber orientation or voids.
- Composite material is treated as an effective homogeneous material unless extended later.
- Beam stresses are approximate section stresses, not local 3D stress.
- The section property model is simplified and should be replaced with CAD, test, or laminate-derived properties for design work.
- Results should be used for structural screening and MIL/GMT comparison, then validated with Abaqus or experiment.

## Modal Analysis Structure

`assembleGlobalMassMatrix.m` assembles the 6DOF beam mass matrix in the same DOF order as the static stiffness matrix. The default is a consistent translational 3D frame mass matrix with simple consistent rotary inertia from `rho*J`, `rho*Iy`, and `rho*Iz`. A lumped fallback is available through `mass_matrix_type = 'lumped'`.

`solveModalEigenproblem.m` solves the generalized eigenvalue problem:

```matlab
K*phi = omega^2*M*phi
```

The root uses the same clamped boundary condition as the static model. Mode shapes are reconstructed to the full global DOF vector and normalized by maximum displacement by default. `run_modal_analysis.m` remains as a backward-compatible wrapper.

The main driver now writes:

- `*_modal_frequencies.csv`
- `*_mode_shapes.csv`
- `*_mode_##_shape_3d.png`
- `*_campbell.csv` next to the case results when `run_campbell_diagram_enabled = true`
- `../../results/plots/fea/comparisons/*_campbell_diagram.png/.svg` for paper-ready Campbell diagram output

The native viewer can read these files next to a results CSV and show natural frequency tables plus 3D/span-flap/span-edge mode shapes. `run_mil_gmt_comparison.m` also writes modal comparison CSVs in each comparison folder.

The Campbell export sweeps RPM from zero to `campbell_max_RPM`, writes 1x through 4x excitation harmonics, blade-passing frequency, and the first five modal frequencies. The current implementation treats modal frequencies as constant with RPM; centrifugal-stiffened Campbell trends can be added later by making the modal solve RPM-dependent.

Optional centrifugal stiffening uses an approximate geometric stiffness from recovered axial centrifugal force and solves `(K + Kg)*phi = omega^2*M*phi`. These are labeled as centrifugally stiffened frequencies. Gyroscopic effects are not included.

Use:

```matlab
validate_modal_analysis_sanity
```

to run basic modal sanity checks for symmetry, positive frequencies, and expected trends with stiffness, density, root stiffness, and blade length.
