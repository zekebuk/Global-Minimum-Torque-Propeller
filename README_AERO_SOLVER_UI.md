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
C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\CAD\airfoils_good
```

Generated XFOIL polar CSVs:

```text
CAD\airfoils_good\generated_polars\
```

Default XFOIL root:

```text
C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\XFOIL6.99
```

Optional XROTOR executable:

```text
C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\Xrotor7.55\Xrotor\xrotor.exe
```

## Running `thesis_22_static_35N`

1. Launch the UI.
2. Select `optimized_mil_22`.
3. Confirm the geometry CSV path points to `results/geometry/optimized/optimized_mil/optimized_mil.csv`.
4. Click `Validate Inputs`.
5. If polar CSVs are missing, click `Generate XFOIL Polars`.
6. Click `Run Aero Solver`.
7. Review summary metrics, sanity checks, station table, and plots.

The normal run uses XFOIL-generated polar CSVs and BEMT as the primary solver. The target thrust iteration remains in the backend.

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

`valid_for_thesis = true` means the BEMT result used real/generated polar data, matched target thrust within tolerance, and passed the solver sanity checks:

- `P = Q * omega`
- `CP = 2*pi*CQ`
- station loads integrate to total thrust and torque
- hover induced velocity is active for static hover
- no NaN or infinite values

## Placeholder Polar

`placeholder_debug_polar` is debug-only. The UI will not use it unless `Allow debug placeholder polar` is explicitly checked. If enabled, the UI shows a red warning and the result is not thesis-valid.

## XROTOR

XROTOR is optional validation/comparison only. It runs after BEMT and writes:

```text
results/aero/xrotor_comparison_summary.csv
results/aero/xrotor_comparison_summary.md
results/aero/xrotor/
```

XROTOR failure does not invalidate a passing BEMT result.

## Config Save/Load

Default config path:

```text
configs/aero/thesis_22_static_35N_config.json
```

Use `Save config to JSON` and `Load config from JSON` in the main panel to preserve UI settings.
