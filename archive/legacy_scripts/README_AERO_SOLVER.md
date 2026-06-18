# Standalone BEMT Aero Solver

`aero_solver.py` is a simple Blade Element Momentum Theory evaluator for the thesis propeller workflow. It is intentionally separate from the FEA code: it reads blade geometry and airfoil polar data, computes aerodynamic loads, and writes station-level loads that the structural solver can import later.

## Inputs

Geometry CSV columns:

- `r_R`: nondimensional radius, `r/R`
- `r_m`: radial station location in meters
- `chord_m`: local chord in meters
- `twist_deg`: local geometric pitch/twist in degrees
- `airfoil`: local airfoil name

The loader also accepts the existing thesis aliases `mu` for `r_R` and `radius_m` for `r_m`. If `dr_m` is present, it is used as the station width. If not, station widths are computed from radial midpoint edges.

Operating condition fields:

- `diameter_m`
- `radius_m`
- `blade_count`
- `rpm`
- `rho`
- `V_inf_m_s`
- `target_thrust_N`
- `hover_mode`

Default thesis operating case:

```text
case_name = thesis_22_static_35N
diameter_m = 0.5588
radius_m = 0.2794
blade_count = 2
rpm = 4000
rho = 1.225
V_inf_m_s = 0
target_thrust_N = 35
hover_mode = true
```

Polar CSV columns:

- `airfoil` or `airfoil_name`
- `Re`
- `alpha_deg`
- `Cl`
- `Cd`
- `Cm` optional

`airfoil` or `airfoil_name` is required for normal multi-airfoil thesis runs. If multiple Reynolds-number tables are available, the solver interpolates between the nearest Reynolds tables after computing local `Re = rho*Vrel*chord/mu`.

In normal thesis mode, the solver requires real/generated polar CSV data. If no `--polar` file is supplied, it scans:

```text
CAD/airfoils_good/
CAD/airfoils_good/generated_polars/
```

for polar CSV files. If no real polar files are found, the solver exits instead of using placeholder data.

Normal thesis runs also check that XFOIL-generated polar CSVs exist for:

```text
MH115, S4310, A3, A4, A6, A7, A11, A13
```

at:

```text
Re = 100000, 150000, 200000, 300000
```

If any required generated polar CSV is missing, `aero_solver.py` attempts to run `airfoil_tools/generate_xfoil_polars.py` through the Python API and uses the existing XFOIL executable discovery logic. If generation fails, normal mode stops with a clear error. It does not fall back to placeholder polars.

If `--debug-mode` is supplied, the solver uses a placeholder debug polar:

```text
Cl = Cl_alpha * alpha_rad
Cd = Cd0 + k * Cl^2
```

This placeholder is not thesis-quality airfoil data. The solver emits a warning when it is used and always marks `valid_for_thesis = false`. Placeholder `Cl` is clipped to `Cl_max_debug = 1.5` by default to avoid unbounded debug lift at high alpha, and the summary reports the clipping fraction.

## Airfoil Coordinate Conversion And XFOIL Polars

The airfoil coordinate tools live in `airfoil_tools/`.

Convert root-level `*_YZ.txt` coordinate files to XFOIL `.dat` files:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' airfoil_tools\convert_yz_to_xfoil_dat.py --airfoil-dir CAD\airfoils_good
```

This reads the 3-column YZ files as:

```text
column 1: ignored
column 2: y/c
column 3: x/c
```

and writes:

```text
CAD\airfoils_good\xfoil_dat\*.dat
```

Generate XFOIL polar CSVs:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' airfoil_tools\generate_xfoil_polars.py --airfoil-dir CAD\airfoils_good --xfoil path\to\xfoil.exe
```

The generator can also find the local XFOIL 6.99 executable automatically under:

```text
XFOIL6.99\xfoil.exe
```

The generator uses:

- `Re = 100000, 150000, 200000, 300000`
- alpha sweep `-8 deg` to `+16 deg`
- step `0.5 deg`

If XFOIL is unavailable, it prints:

```text
XFOIL executable not found. Cannot generate thesis-quality polar data.
```

XFOIL is required for thesis-valid BEMT results unless an explicit real polar CSV is supplied with `--polar`.

## Equations

Angular speed:

```text
omega = rpm * 2*pi/60
```

Disk area:

```text
A = pi * radius_m^2
```

Hover induced velocity estimate:

```text
vi = sqrt(T / (2*rho*A))
```

At each radial station:

```text
Vt = omega * r
Va = V_inf + vi
Vrel = sqrt(Vt^2 + Va^2)
phi = atan2(Va, Vt)
alpha = twist - phi
q = 0.5*rho*Vrel^2
dL = q * chord * Cl * dr
dD = q * chord * Cd * dr
dT = B * (dL*cos(phi) - dD*sin(phi))
dF_tangential = B * (dL*sin(phi) + dD*cos(phi))
dQ = r * dF_tangential
```

Global outputs:

```text
T = sum(dT)
Q = sum(dQ)
P = Q * omega
CT = T / (rho*n^2*D^4)
CQ = Q / (rho*n^2*D^5)
CP = P / (rho*n^3*D^5)
n = rpm/60
```

Important: `Q` is aerodynamic shaft torque from tangential blade force. It is not the blade spanwise torsional moment.

## Target Thrust Iteration

`iterate_to_target_thrust(...)` applies one collective twist offset to all stations and uses scalar bracketing plus bisection to match `target_thrust_N`. It does not change chord, airfoil, or radial station locations.

The command-line interface runs target matching by default when `target_thrust_N` is supplied. Use `--no-match-target` only for debugging an unmatched raw geometry evaluation.

The initial collective bracket is `[-30 deg, +10 deg]` and expands if needed. If no root is found, the solver reports:

```text
Could not match target thrust with collective twist offset.
```

## Outputs

`export_results(results, output_dir, case_name)` writes:

- `{case_name}_stations.csv`
- `{case_name}_summary.json`
- `{case_name}_summary.txt`

Station-level CSV columns include:

- geometry: `r_R`, `r_m`, `chord_m`, `twist_deg`
- local aero state: `airfoil`, `Re`, `polar_Re_used`, `polar_source`, `phi_deg`, `alpha_deg`, `Cl`, `Cd`, `Cm`, `alpha_clipped`, `Vt_m_s`, `Va_m_s`, `Vrel_m_s`
- loads: `dT_N`, `dQ_Nm`, `qz_N_per_m`, `qy_tangential_N_per_m`, `torque_loading_Nm_per_m`
- cumulative loading fractions

The load columns intended for later FEA import are:

- `qz_N_per_m`
- `qy_tangential_N_per_m`
- `torque_loading_Nm_per_m`

The summary JSON/TXT also reports:

- `model_source`
- `valid_for_optimizer_summary`
- `valid_for_bemt_summary`
- `valid_for_thesis_export`
- `target_matched`
- `collective_twist_offset_deg`
- `xfoil_polars_used`
- `xfoil_success`
- `xrotor_ran`
- `xrotor_success`
- `polar_is_placeholder`
- `valid_for_thesis`
- `failure_reason`
- `Vi_target_m_s`
- `Vi_computed_from_result_m_s`
- `Vi_difference_m_s`
- `CT_expected`
- `CT_expected_error_pct`
- `clipping_fraction_pct`
- `alpha_clipping_fraction_pct`
- `Re_min`
- `Re_max`
- `max_Cd`
- `airfoils_used`
- `generated_polars_used`
- `failed_polar_cases`

## Model Sources

Every summary JSON and station CSV carries `model_source`:

- `optimizer_reduced_order`: geometry and station aero values generated directly by `Geometry_optimization.py`.
- `optimizer_station_data`: `aero_solver.py` loaded optimizer station columns such as `alpha_deg`, `cl`, `cd`, `dthrust_n`, and `dtorque_nm` and used them as the source of truth.
- `bemt_xfoil`: `aero_solver.py` recomputed station aerodynamics from geometry plus real/generated XFOIL polar data.

In imported CSV mode, station aero values are not silently mixed with BEMT recomputation. The solver still reports kinematic estimates for plotting:

```text
omega = rpm*2*pi/60
Vt = omega*r
Vi = sqrt(target_thrust/(2*rho*pi*R^2)) for static hover
Va = V_inf + Vi
Vrel = sqrt(Vt^2 + Va^2)
phi = atan2(Va,Vt)
Re = rho*Vrel*chord/mu
```

For imported optimizer CSVs, Reynolds and inflow fields are kinematic estimates. They are useful for inspecting operating scale, but they are not proof that a BEMT/XFOIL station solve was rerun.

The legacy `valid_for_thesis` flag is retained for compatibility and mirrors `valid_for_thesis_export`. Prefer the explicit flags:

- `valid_for_optimizer_summary`: valid for optimizer-source summary/figure use.
- `valid_for_bemt_summary`: valid as a BEMT/XFOIL recomputation.
- `valid_for_thesis_export`: valid for thesis export in the context of the reported `model_source`.

Debug placeholder polars always make `valid_for_thesis_export = false`.

Optimizer-imported thesis exports also enforce manufacturability checks. A case is marked diagnostic if twist has a positive bump greater than `0.5 deg` after `r/R > 0.35`, if more than `10%` of stations hit the optimizer Cl cap, if more than `10%` hit alpha limits, if target thrust misses the selected tolerance, if torque is nonpositive, or if any station load is nonfinite.

`plot_results(results, output_dir)` writes PDF, SVG, and 300 dpi PNG figures for:

- thrust loading vs `r/R`
- torque loading vs `r/R`
- cumulative thrust vs `r/R`
- cumulative torque vs `r/R`
- `Cl` distribution vs `r/R`
- `Cd` distribution vs `r/R`
- alpha distribution vs `r/R`
- Reynolds number distribution vs `r/R`
- twist distribution vs `r/R`

## Validation Checks

Each evaluation records sanity checks for:

- `P = Q * omega`
- `CP = 2*pi*CQ`
- `sum(dT)` equals total thrust
- `sum(dQ)` equals total torque
- `qz` integrates to thrust
- torque loading integrates to torque
- hover mode uses induced velocity for static hover
- no NaN or infinite values
- thrust target error is reported
- SI units are used internally

Before exported comparison figures are plotted, station CSV geometry is checked against the recorded source geometry CSV for `r/R`, `r_m`, `chord_m`, `twist_input_deg`, and `airfoil`. A mismatch refuses to plot so stale station outputs cannot accidentally become thesis figures.

Each generated plot has a `filename.metadata.json` sidecar recording `generating_script`, `model_source`, `geometry_source_csv`, `station_source_csv`, `twist_column_used`, and Git provenance when available. Figures are exported as PNG and SVG only; PDF export is intentionally disabled.

## Output Structure

- `results/geometry_optimizer/<case>/`: direct data outputs from `Geometry_optimization.py`.
- `results/aero_postprocess/optimizer_station_data/<case>/`: `aero_solver.py` reading optimizer CSV station values directly, without recomputing alpha, Cl, Cd, dT, or dQ.
- `results/aero_postprocess/bemt_xfoil/<case>/`: `aero_solver.py` recomputing aerodynamic quantities with BEMT and XFOIL/generated polar data.
- `results/aero_postprocess/comparisons/<comparison_name>/`: MIL vs GMT comparison data.
- `results/verification/theory/`: `theory_verification.py` comparison data.
- `results/fea/beam_solver/<case>/`, `results/fea/abaqus_exports/`, and `results/fea/comparisons/`: downstream structural data.
- `results/plots/...`: all thesis-ready PNG/SVG figures. Run `python scripts/generate_all_results_plots.py` from the repo root to regenerate available plots.

Model source names used in metadata:

- `optimizer_reduced_order`: geometry generated directly by `Geometry_optimization.py`.
- `optimizer_station_data`: aero postprocessing that imports optimizer station alpha, Cl, Cd, dT, and dQ.
- `bemt_xfoil`: aero postprocessing that recomputes station aero with BEMT and XFOIL/generated polars.
- `xrotor_optional`: optional validation data only.

## Recommended Thesis Commands

Generate optimizer geometry:

```powershell
python Geometry_optimization.py --preset thesis_mil_22_manufacturable --make-plots
python Geometry_optimization.py --preset thesis_gmt_22_manufacturable --make-plots
```

Run aero postprocessing using optimizer station data:

```powershell
python aero_solver.py --geometry results\geometry_optimizer\optimized_mil\optimized_mil_optimizer_geometry.csv --case-name optimized_mil_22 --prefer-imported-station-aero
python aero_solver.py --geometry results\geometry_optimizer\optimized_gmt\optimized_gmt_optimizer_geometry.csv --case-name optimized_gmt_22 --prefer-imported-station-aero
```

Run BEMT/XFOIL recomputation:

```powershell
python aero_solver.py --geometry results\geometry_optimizer\optimized_mil\optimized_mil_optimizer_geometry.csv --case-name optimized_mil_22
python aero_solver.py --geometry results\geometry_optimizer\optimized_gmt\optimized_gmt_optimizer_geometry.csv --case-name optimized_gmt_22
```

Run theory verification:

```powershell
python theory_verification.py
```

Generate MIL vs GMT comparison plots:

```powershell
python aero_solver.py --compare-mil-station-csv results\aero_postprocess\optimizer_station_data\optimized_mil\optimized_mil_22_optimizer_station_data_stations.csv --compare-gmt-station-csv results\aero_postprocess\optimizer_station_data\optimized_gmt\optimized_gmt_22_optimizer_station_data_stations.csv --compare-label optimized_mil_vs_gmt
```

XROTOR remains optional validation/comparison. It is not the primary solver and a missing XROTOR executable should not block BEMT/XFOIL results.

## Example CLI

Run the default thesis case with real/generated polar CSV data:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' aero_solver.py
```

Run an explicitly invalid debug case with placeholder polar:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' aero_solver.py --debug-mode
```

Debug-mode output uses a `_debug_placeholder` case-name suffix by default so placeholder files are not mistaken for final thesis results.

Run with an explicit geometry and polar file:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' aero_solver.py `
  --geometry results\geometry_optimizer\optimized_mil\optimized_mil_optimizer_geometry.csv `
  --polar path\to\airfoil_polar.csv `
  --match-target
```

Default output locations:

- optimizer station mode data: `results/aero_postprocess/optimizer_station_data/<case>/`; plots: `results/plots/aero_postprocess/optimizer_station_data/<case>/`
- BEMT/XFOIL mode data: `results/aero_postprocess/bemt_xfoil/<case>/`; plots: `results/plots/aero_postprocess/bemt_xfoil/<case>/`

Optional XROTOR validation path:

```text
xrotor_root = C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\Xrotor7.55
```

This path is configuration only and is not used in the BEMT math. If an executable is not found, the solver prints:

```text
XROTOR executable not found. Skipping optional XROTOR validation.
```

Run optional XROTOR comparison:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' aero_solver.py --run-xrotor
```

XROTOR is optional validation only. The CLI runs it only when `--run-xrotor` is passed. It runs after the BEMT result is complete and thesis-valid, uses a generated XROTOR `.prop` file and headless `OPER/RPM/DISP` commands, and writes raw logs under:

```text
results/aero_postprocess/xrotor/
```

If XROTOR succeeds, comparison files are written:

```text
results/aero_postprocess/xrotor/xrotor_comparison_summary.csv
results/aero_postprocess/xrotor/xrotor_comparison_summary.md
```

For static hover cases, the XROTOR comparison uses a small `0.1 m/s` analysis velocity to avoid XROTOR zero-freestream divide-by-zero behavior. This does not affect the BEMT thesis result. If XROTOR fails or times out, the solver prints a warning, saves failure logs, and leaves `valid_for_thesis` controlled by the BEMT/XFOIL result only.

## Limitations

This is a reduced-order, auditable BEMT tool. It does not include CFD, blade-vortex interaction, compressibility corrections, stall-delay modeling, Prandtl tip/root loss, swirl induction iteration, or structural deformation coupling. Placeholder polars are debug-only and are never thesis-valid. Final thesis outputs should come from real/generated XFOIL polar CSVs; XROTOR outputs are comparison data, not the primary thesis aero solution.
