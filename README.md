<<<<<<< HEAD
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
=======
# Global Minimum Torque Propeller

This repository contains research code for a thesis propeller workflow. The code compares minimum-induced-loss (MIL) and global-minimum-torque (GMT/Bell-spanload-inspired) propeller geometries at a thesis operating point, then carries selected geometry through aerodynamic post-processing and reduced-order beam FEA setup.

The project is not a general propeller design package. It is a collection of scripts, tests, and documentation used to generate and audit specific thesis outputs.

## What Is Here

- Geometry tools for reading/scaling reference propeller geometry, producing MIL/GMT comparison cases, and writing geometry/audit outputs.
- Aerodynamic tools based on blade element momentum theory (BEMT), with XFOIL polar-file support and optional comparison to XROTOR outputs.
- A Streamlit UI for configuring and reviewing aero-solver runs.
- MATLAB 3D beam/frame FEA scripts for reduced-order structural screening.
- Python tests for theory checks, output-path boundaries, optimizer validity, and solver behavior.

## What This Does Not Claim

- "GMT" names in this repository describe the geometry/spanload family being studied; they are not a proof of global optimality across every possible propeller design.
- The aerodynamic model is BEMT-based, not CFD.
- The structural model is a reduced-order 3D beam/frame model, not shell or solid FEA.
- Debug placeholder airfoil polars are explicitly not thesis-valid. Thesis-quality BEMT runs require real/generated polar CSVs.
- Results depend on the supplied geometry, polar data, operating point, and the assumptions documented in the solver READMEs.

## Repository Layout

| Path | Purpose |
| --- | --- |
| `Geometry_optimization.py` | Main geometry optimization and reference-scaling script. |
| `geometry/` | Operating-point inverse-design workflow and supporting geometry/BEMT helpers. |
| `aero_solver.py` | Standalone BEMT aero solver. |
| `aero_solver_ui.py` | Streamlit frontend for the aero solver. |
| `aero/` | Final aero validation/post-processing scripts. |
| `airfoil_tools/` | Airfoil coordinate conversion and XFOIL polar generation utilities. |
| `fea/` | Python-side FEA wrappers and helper code. |
| `Propeller_3D_Beam_6DOF_FEA/` | MATLAB reduced-order 3D beam/frame FEA implementation. |
| `outputs/` | Local/generated optimizer outputs. Contents may be regenerated. |
| `archive/` | Deprecated UI, older scripts, and debug-output notes. |
| `test_*.py` | Pytest coverage for theory, boundaries, outputs, and optimizer validity. |

## Setup

Use a recent Python 3 environment. From the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

The MATLAB FEA workflow requires MATLAB. Thesis-valid aero runs also require existing airfoil polar CSVs or a working XFOIL executable that can generate them.

## Common Commands

Run the final geometry workflow:

```powershell
python geometry\run_final_geometry.py
```

Run the final aero validation workflow:

```powershell
python aero\run_final_aero.py
```

Print the final FEA handoff paths and MATLAB command:

```powershell
python fea\run_final_fea.py
```

Launch the aero-solver UI:

```powershell
python -m streamlit run aero_solver_ui.py
```

Run the Python tests:

```powershell
pytest
```

## Data Flow

The intended high-level flow is:

1. Read or generate propeller geometry.
2. Compare MIL and GMT-style geometry/loading cases at the selected operating point.
3. Recompute or inspect aerodynamic station data with BEMT and airfoil polar data.
4. Pass locked geometry and aero-load CSVs to the reduced-order beam FEA workflow.
5. Use generated CSV, JSON, plot, and metadata outputs for thesis figures and audit trails.

When generated, final thesis-use inputs are organized under:

```text
results/FINAL_THESIS_USE_THESE/
```

Generated results and plots are intentionally separated from source code. See `output_paths.py` and `cleanup_outputs.py` for the current output-folder conventions.

## More Documentation

- `README_AERO_SOLVER.md` explains the BEMT solver inputs, equations, target-thrust iteration, and output files.
- `README_AERO_SOLVER_UI.md` explains the Streamlit UI and aero-postprocessing output structure.
- `airfoil_tools/README_XFOIL_POLARS.md` explains airfoil conversion and XFOIL polar generation.
- `fea/README_FEA.md` explains the final FEA organization.
- `Propeller_3D_Beam_6DOF_FEA/README.md` and `Propeller_3D_Beam_6DOF_FEA/README_FEA_FORMULATION.md` document the MATLAB beam/frame model.
>>>>>>> b0c2eec9931078e4e317da2fc8240e2ea8884bf7
