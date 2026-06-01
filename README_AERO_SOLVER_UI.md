# Aero Solver Streamlit UI

`aero_solver_ui.py` is a browser frontend for the standalone aero solver. It does not implement BEMT equations. It builds a config dictionary, validates inputs, calls `aero_solver.py`, and displays the generated outputs.

## Install

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' -m pip install streamlit
```

`pandas`, `matplotlib`, and the existing solver dependencies are also required.

## Launch

Preferred:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' -m streamlit run aero_solver_ui.py
```

Direct Python execution is also supported; the script will relaunch itself with Streamlit:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' aero_solver_ui.py
```

## Required Paths

Default airfoil directory:

```text
CAD\airfoils_good
```

Generated XFOIL polar CSVs:

```text
CAD\airfoils_good\generated_polars\
```

Default XFOIL root:

```text
XFOIL6.99
```

Optional XROTOR executable:

```text
Xrotor7.55\Xrotor\xrotor.exe
```

## Output Structure

- Geometry optimizer data: `results/geometry_optimizer/<case>/`.
- Imported optimizer station data: `results/aero_postprocess/optimizer_station_data/<case>/`.
- BEMT/XFOIL recomputation data: `results/aero_postprocess/bemt_xfoil/<case>/`.
- Theory verification data: `results/verification/theory/`.
- FEA data: `results/fea/beam_solver/<case>/`, `results/fea/abaqus_exports/`, and `results/fea/comparisons/`.
- Thesis-ready figures: `results/plots/...`. Regenerate available plots with `python scripts/generate_all_results_plots.py`.

Figures are exported as PNG and SVG only. PDF export is disabled.

## Running `optimized_mil_22` / `optimized_gmt_22`

1. Launch the UI.
2. Select `optimized_mil_22` or `optimized_gmt_22`, or browse to a geometry CSV.
3. Confirm the resolved geometry path points to `results/geometry_optimizer/optimized_mil/optimized_mil_optimizer_geometry.csv` or `results/geometry_optimizer/optimized_gmt/optimized_gmt_optimizer_geometry.csv`.
4. Click `Validate Inputs`.
5. If polar CSVs are missing, click `Generate XFOIL Polars`.
6. Click `Run Aero Solver`.
7. Review summary metrics, sanity checks, station table, and plots.

The normal run uses XFOIL-generated polar CSVs and BEMT as the primary solver. The target thrust iteration remains in the backend.

## Model Sources Shown In The UI

The result banner shows the current case type, the actual geometry CSV used, the `model_source`, and whether the station table is imported data or BEMT/XFOIL recomputation.

- `optimizer_reduced_order`: produced directly by `Geometry_optimization.py`.
- `optimizer_station_data`: optimizer station aero columns are displayed as the source of truth.
- `bemt_xfoil`: station aero was recomputed from geometry plus XFOIL polar data.

When imported optimizer data are displayed, the UI states:

```text
Station aero values are imported from optimizer station data. Reynolds and inflow fields are kinematic estimates.
```

Those Reynolds plots are valid as kinematic operating-scale estimates. They are not BEMT/XFOIL polar lookup evidence unless `model_source = bemt_xfoil`.

The banner also shows the station source CSV and twist column used by the plots. Plot metadata sidecars use `filename.metadata.json` and record source paths, source timestamps, model source, and Git provenance when available.

## XFOIL Polars

XFOIL is required for thesis-valid BEMT results unless a real polar CSV is supplied directly to the backend. Required airfoils:

```text
MH115, S4310, A3, A4, A6, A7, A11, A13
```

Required Reynolds numbers:

```text
100000, 150000, 200000, 300000
```

The UI can check XFOIL, show polar availability, and call the existing polar generator.

## Valid For Thesis

Prefer the explicit validity flags:

- `valid_for_optimizer_summary`
- `valid_for_bemt_summary`
- `valid_for_thesis_export`

`valid_for_thesis` is retained for compatibility and mirrors `valid_for_thesis_export`. For BEMT/XFOIL results, validity requires real/generated polar data, target thrust within the selected tolerance, and solver sanity checks:

- `P = Q * omega`
- `CP = 2*pi*CQ`
- station loads integrate to total thrust and torque
- hover induced velocity is active for static hover
- no NaN or infinite values

For imported optimizer results, thesis export validity also requires smooth manufacturable twist, target thrust within tolerance, no nonfinite station loads, positive torque, no positive twist bump greater than `0.5 deg` after `r/R > 0.35`, and no more than `10%` of stations at Cl or alpha limits. The older `optimized_mil_22` and `optimized_gmt_22` cases are shown as diagnostic because they expose the optimizer twist bump and Cl/alpha clipping.

## Placeholder Polar

`placeholder_debug_polar` is debug-only. The UI will not use it unless `Allow debug placeholder polar` is explicitly checked. If enabled, the UI shows a red warning and the result is not thesis-valid.

## XROTOR

XROTOR is optional validation/comparison only, not the primary solver. It runs after BEMT and writes:

```text
results/aero_postprocess/xrotor/xrotor_comparison_summary.csv
results/aero_postprocess/xrotor/xrotor_comparison_summary.md
results/aero_postprocess/xrotor/
```

XROTOR failure does not invalidate a passing BEMT result.

## MIL vs GMT Comparison Plots

Use the `MIL vs GMT Comparison Plots` expander to select MIL and GMT station CSVs and generate overlay figures. The backend checks each station CSV against its recorded source geometry CSV before plotting `r/R`, `r_m`, `chord_m`, `twist_input_deg`, and `airfoil`. If the station CSV is stale or mismatched, the UI refuses to generate figures.

The same workflow is available from the CLI:

```powershell
python aero_solver.py --compare-mil-station-csv results\aero_postprocess\optimizer_station_data\optimized_mil\optimized_mil_22_optimizer_station_data_stations.csv --compare-gmt-station-csv results\aero_postprocess\optimizer_station_data\optimized_gmt\optimized_gmt_22_optimizer_station_data_stations.csv --compare-label optimized_mil_vs_gmt
```

Recommended thesis comparison sets are:

```powershell
python aero_solver.py --compare-mil-station-csv results\aero_postprocess\optimizer_station_data\optimized_mil\optimized_mil_22_optimizer_station_data_stations.csv --compare-gmt-station-csv results\aero_postprocess\optimizer_station_data\optimized_gmt\optimized_gmt_22_optimizer_station_data_stations.csv --compare-label optimized_mil_vs_gmt
python aero_solver.py --compare-mil-station-csv results\aero_postprocess\bemt_xfoil\optimized_mil\optimized_mil_22_bemt_xfoil_stations.csv --compare-gmt-station-csv results\aero_postprocess\bemt_xfoil\optimized_gmt\optimized_gmt_22_bemt_xfoil_stations.csv --compare-label optimized_mil_vs_gmt
```

## Config Save/Load

Default config path:

```text
results/aero_postprocess/optimizer_station_data/optimized_gmt/aero_solver_ui_config.json
```

Use `Save config to JSON` and `Load config from JSON` in the main panel to preserve UI settings.
