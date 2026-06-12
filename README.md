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
