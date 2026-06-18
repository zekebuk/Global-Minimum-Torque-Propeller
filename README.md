# Aerodynamic Design and Structural Evaluation of a Global Minimum Torque Multirotor Propeller

This repository contains the analysis code used to generate geometry, aerodynamic postprocessing results, structural screening reports, and thesis figures for a 22-inch global-minimum-torque-inspired multirotor propeller. The thesis compares a two-blade minimum induced loss (MIL) baseline with a Prandtl bell-spanload-inspired GMT case at a locked static-hover operating condition: 4000 rpm and 35 N target thrust.

The repository is a research workflow, not a general propeller design package. The locked modeled comparison showed a modest predicted torque reduction for the GMT case at matched thrust and RPM. The GMT propeller has not yet been physically tested, and this repository does not claim experimental validation, CFD validation, acoustic SPL validation, or thrust-stand performance data for the GMT design.

## Repository Structure

| Path | Purpose |
| --- | --- |
| `scripts/` | Final thesis workflow entry points. |
| `scripts/helpers/` | Helper functions used by the final workflow scripts. |
| `scripts/gui/` | Interactive launchers for the Streamlit aero UI and MATLAB FEA viewer. |
| `src/prandtl_prop/` | Retained importable package and tested source modules from the cleaned thesis workflow. |
| `data/geometry/` | Locked MIL/GMT station-based geometry CSVs used by the workflow. |
| `data/processed/` | Locked BEMT station data and summary files. |
| `outputs/geometry_exports/` | Regenerated geometry exports. |
| `outputs/tables/` | Regenerated summary tables. |
| `outputs/reports/` | Regenerated workflow reports. |
| `outputs/figures/` | Regenerated thesis-ready figures. |
| `docs/` | Workflow notes, appendix-ready code summary, audit notes, and figure inventory. |
| `tests/` | Pytest checks for locked constants, geometry sanity, and modeled results. |
| `archive/` | Legacy scripts, exploratory notebooks, and old generated outputs preserved for history only. |

The final workflow is intentionally separated from historical development material. Archived files are not part of the thesis-grade reproducible workflow and are not cited as final methodology.

## Installation

Use Python 3.10 or newer.

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -e .
```

The core locked-result workflow runs from CSV/JSON data in this repository. Historical regeneration attempts, UI experiments, and external-solver scripts are preserved under `archive/` and may require local XFOIL, XROTOR, MATLAB, Abaqus, OpenVSP, or PSU-WOPWOP installations.

## Quick Start

```bash
python scripts/run_geometry_generation.py
python scripts/run_aero_postprocessing.py
python scripts/run_xrotor_headless.py
python scripts/run_matlab_fea.py
python scripts/make_thesis_figures.py
pytest
```

The XROTOR and MATLAB commands are report-only by default. Add `--execute` only when the required external software and inputs are available. Archived scripts are deliberately excluded from the quick start.

## Reproducibility Workflow

1. `python scripts/run_geometry_generation.py`
   Exports the locked MIL/GMT geometry to `outputs/geometry_exports/` and writes `outputs/reports/geometry_audit.md`.

2. `python scripts/run_aero_postprocessing.py`
   Reads the locked BEMT station data and writes `outputs/tables/locked_bemt_summary.csv` and `outputs/tables/locked_bemt_station_summary.csv`.

3. `python scripts/run_xrotor_headless.py`
   Writes `outputs/reports/xrotor_headless_report.md`. With `--execute`, it can run a local XROTOR executable using a provided command file.

4. `python scripts/run_matlab_fea.py`
   Writes `outputs/reports/matlab_fea_report.md`. With `--execute`, it can run the archived MATLAB beam FEA workflow when MATLAB and the FEA directory are available.

5. `python scripts/make_thesis_figures.py`
   Regenerates thesis-ready PNG/PDF figures from the locked geometry and processed station data.

## Interactive GUI Launchers

The GUI launchers are convenience tools for inspection and development, not the locked thesis quick-start workflow.

```bash
python scripts/gui/start_streamlit_aero_ui.py
python scripts/gui/start_matlab_fea_ui.py
```

The MATLAB launcher opens the native propeller beam viewer by default. Use `--viewer me504` to request the ME504 plotting app adapter.

## Locked Modeled Result

Operating condition: static hover, 22 inch diameter, two blades, 4000 rpm, 35 N target thrust.

| Case | Thrust [N] | Torque [N*m] | Power [W] | Notes |
| --- | ---: | ---: | ---: | --- |
| MIL | 35.000000 | 0.859266 | 359.928407 | Baseline matched case |
| GMT | 34.999994 | 0.846403 | 354.540491 | GMT-inspired matched case |

At this modeled condition, the GMT-inspired case reduces predicted torque by 1.497%, reported as 1.50%. The locked comparison also gives a GMT tip-loading reduction of 10.287% and an inboard center-of-thrust shift of 0.01896 r/R.

## Generated Figures

`scripts/make_thesis_figures.py` regenerates the final geometry, aerodynamic station, cumulative loading, and modeled MIL/GMT summary figures into `outputs/figures/`.

## Data Sources

- Final locked geometry: `data/geometry/final_mil_geometry.csv` and `data/geometry/final_gmt_geometry.csv`.
- Final locked BEMT data: `data/processed/*_bemt_validation_LOCKED.csv`.
- Locked summary data: `data/processed/locked_aero_summary.csv` and `data/processed/locked_aero_summary.json`.
- Optional XROTOR and MATLAB FEA context: summarized by `scripts/run_xrotor_headless.py` and `scripts/run_matlab_fea.py`.

## Limitations

- The aerodynamic model is BEMT-based postprocessing, not CFD.
- The GMT geometry is inspired by bell-spanload theory, but the final result is a constrained modeled comparison, not proof of global optimality.
- The 1.50% torque reduction is a modest numerical result at one static-hover condition.
- No physical GMT thrust-stand data, acoustic SPL validation, or flight-test validation are included.
- XFOIL, XROTOR, MATLAB, Abaqus, OpenVSP, and PSU-WOPWOP related work require external software and are documented separately from the fully Python-rerunnable workflow.

## Citation

Citation metadata are provided in `CITATION.cff`. Until the final repository URL is assigned, cite the thesis/code as:

Zeke Bukovansky. 2026. *Aerodynamic Design and Structural Evaluation of a Global Minimum Torque Multirotor Propeller*. Thesis research code, California Polytechnic State University, San Luis Obispo.
