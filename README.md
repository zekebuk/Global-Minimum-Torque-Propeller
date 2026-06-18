# Aerodynamic Design and Structural Evaluation of a Global Minimum Torque Multirotor Propeller

This repository contains the analysis code used to generate geometry, aerodynamic postprocessing results, structural screening outputs, and thesis figures for a 22-inch global-minimum-torque-inspired multirotor propeller. The thesis compares a two-blade minimum induced loss (MIL) baseline with a Prandtl bell-spanload-inspired GMT case at a locked static-hover operating condition: 4000 rpm and 35 N target thrust.

The repository is a research workflow, not a general propeller design package. It supports repeatable geometry generation, BEMT-based aerodynamic postprocessing, XROTOR comparison artifacts where available, reduced-order structural screening, and thesis figure/table generation. The locked modeled comparison showed a modest predicted torque reduction for the GMT case at matched thrust and RPM. The GMT propeller has not yet been physically tested, and this repository does not claim experimental validation, CFD validation, acoustic SPL validation, or thrust-stand performance data for the GMT design.

## Contents

- [Repository Structure](#repository-structure)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Reproducibility Workflow](#reproducibility-workflow)
- [Locked Modeled Result](#locked-modeled-result)
- [Main Generated Figures](#main-generated-figures)
- [Data Sources](#data-sources)
- [Limitations](#limitations)
- [Citation](#citation)
- [Author](#author)

## Repository Structure

| Path | Purpose |
| --- | --- |
| `src/prandtl_prop/` | Lightweight reusable package for locked constants, metrics, CSV loading, plotting, and workflow helpers. |
| `scripts/` | Rerunnable final workflow entry points. |
| `data/geometry/` | Small locked MIL/GMT station-based geometry CSVs used by the public workflow. |
| `data/processed/` | Locked BEMT station data and summary tables copied from the final thesis outputs. |
| `outputs/figures/` | Regenerated thesis-ready figures from `scripts/make_thesis_figures.py`. |
| `outputs/tables/` | Regenerated summary tables from the wrapper scripts. |
| `outputs/geometry_exports/` | Regenerated geometry exports from the wrapper scripts. |
| `docs/` | Workflow notes, appendix-ready code summary, audit notes, and figure inventory. |
| `tests/` | Lightweight pytest checks for locked constants, geometry sanity, and modeled results. |
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
python scripts/run_bemt_comparison.py
python scripts/run_xrotor_comparison.py
python scripts/run_structural_screening.py
python scripts/make_thesis_figures.py
pytest
```

The wrapper scripts write clean regenerated artifacts to `outputs/`. Archived scripts are deliberately excluded from the quick start.

## Reproducibility Workflow

1. `python scripts/run_geometry_generation.py`
   Copies the locked MIL/GMT geometry into `outputs/geometry_exports/` and checks that radial stations, chord, and airfoil schedule are consistent.

2. `python scripts/run_bemt_comparison.py`
   Reads the locked BEMT station data and writes a modeled result table to `outputs/tables/locked_bemt_summary.csv`.

3. `python scripts/run_xrotor_comparison.py`
   Reports available XROTOR comparison artifacts. XROTOR is treated as external-software context, not as an experimental validation source.

4. `python scripts/run_structural_screening.py`
   Summarizes available reduced-order structural and Abaqus-derived artifacts. Structural outputs are screening evidence, not a full certification analysis.

5. `python scripts/make_thesis_figures.py`
   Regenerates the thesis-ready PNG/PDF figures from the locked geometry and processed station data.

## Locked Modeled Result

Operating condition: static hover, 22 inch diameter, two blades, 4000 rpm, 35 N target thrust.

Power is computed from `P = Q * omega`, with `omega = 4000 rpm * 2*pi / 60`.

| Case | Thrust [N] | Torque [N*m] | Power [W] | Notes |
| --- | ---: | ---: | ---: | --- |
| MIL | 35.000000 | 0.859266 | 359.928407 | Baseline matched case |
| GMT | 34.999994 | 0.846403 | 354.540491 | GMT-inspired matched case |

At this modeled condition, the GMT-inspired case reduces predicted torque by 1.497%, reported as 1.50%. The locked comparison also gives a GMT tip-loading reduction of 10.287% and an inboard center-of-thrust shift of 0.01896 r/R.

## Main Generated Figures

`scripts/make_thesis_figures.py` regenerates:

- `fig_twist_distribution.png`
- `fig_chord_distribution.png`
- `fig_angle_of_attack_distribution.png`
- `fig_lift_coefficient_distribution.png`
- `fig_drag_coefficient_distribution.png`
- `fig_reynolds_distribution.png`
- `fig_sectional_thrust_distribution.png`
- `fig_sectional_torque_distribution.png`
- `fig_cumulative_thrust_distribution.png`
- `fig_cumulative_torque_distribution.png`
- `fig_mil_gmt_summary_bar.png`
- `fig_xrotor_bemt_comparison.png`, when compatible XROTOR summary data are present
- `fig_structural_von_mises_envelope.png` and `fig_structural_displacement_envelope.png`, when compatible structural summaries are present

Figures are exported to `outputs/figures/` as high-resolution PNG and PDF files where possible.

## Data Sources

- Final locked geometry: `data/geometry/final_mil_geometry.csv` and `data/geometry/final_gmt_geometry.csv`.
- Final locked BEMT data: `data/processed/*_bemt_validation_LOCKED.csv`.
- Locked summary data: `data/processed/locked_aero_summary.csv` and `data/processed/locked_aero_summary.json`.
- Optional XROTOR and structural context: summarized by `scripts/run_xrotor_comparison.py` and `scripts/run_structural_screening.py` when compatible artifacts are present.

Small locked CSV/JSON files are mirrored into `data/` so the clean workflow can be rerun without depending on archived development scripts.

## Limitations

- The aerodynamic model is BEMT-based postprocessing with precomputed airfoil polar CSVs, not CFD.
- The GMT geometry is inspired by bell-spanload theory, but the final result is a constrained modeled comparison, not proof of global optimality.
- The 1.50% torque reduction is a modest numerical result at one static-hover condition.
- No physical GMT thrust-stand data, acoustic SPL validation, or flight-test validation are included.
- XFOIL, XROTOR, MATLAB, Abaqus, OpenVSP, and PSU-WOPWOP related work require external software and are documented separately from the fully Python-rerunnable workflow.

## Citation

Citation metadata are provided in `CITATION.cff`. Until the final repository URL is assigned, cite the thesis/code as:

Zeke Bukovansky. 2026. *Aerodynamic Design and Structural Evaluation of a Global Minimum Torque Multirotor Propeller*. Thesis research code, California Polytechnic State University, San Luis Obispo.

## Author

Zeke Bukovansky  
California Polytechnic State University, San Luis Obispo  
Master's thesis research code, 2026
