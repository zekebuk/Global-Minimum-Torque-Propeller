# Appendix Code Summary

GitHub Repository: [insert final repository URL here]

## Purpose of the Codebase

This repository contains the thesis research code used to generate geometry, aerodynamic postprocessing results, structural screening outputs, and thesis figures for a 22-inch multirotor propeller study. The study compares a minimum induced loss (MIL) baseline propeller with a global-minimum-torque-inspired (GMT-inspired) geometry based on a bell-spanload loading concept.

The full code is hosted externally rather than printed in full in the thesis. The repository is more reproducible and maintainable than static code listings because it preserves the input data, scripts, generated tables, figures, and audit notes needed to rerun the workflow.

## Inputs

The primary locked inputs are station-based propeller geometry files for the MIL and GMT-inspired cases, precomputed airfoil polar CSV files, and the fixed operating condition. The operating condition is static hover at 4000 rpm with a target thrust of 35 N for a two-blade, 22-inch-diameter propeller.

## Main Workflow

The workflow starts with station-based geometry, performs BEMT aerodynamic postprocessing, compares integrated thrust and torque, and generates thesis-ready plots and tables. Optional structural and XROTOR comparison artifacts are included for context where available.

## Geometry Generation

Geometry is represented by nondimensional radial station `r/R`, dimensional radius, chord, twist, airfoil label, and case label. The locked comparison holds chord, radial stations, and airfoil schedule fixed between the MIL and GMT-inspired cases so that the main modeled difference is the twist/loading strategy.

## Aerodynamic Postprocessing

Aerodynamic postprocessing uses stationwise BEMT calculations with airfoil polar lookup. The final locked comparison reports MIL thrust of 35.000000 N and GMT thrust of 34.999994 N at 4000 rpm. The corresponding modeled torques are 0.859266 N*m for MIL and 0.846403 N*m for GMT.

## BEMT Implementation

The BEMT implementation computes local inflow quantities, angle of attack, Reynolds number, lift and drag coefficients, sectional thrust, sectional torque, cumulative loading, and integrated performance. The locked comparison uses stationwise annular momentum with Prandtl tip-loss handling and precomputed airfoil polar data.

## XROTOR Comparison

XROTOR-related files are included where available as external-solver comparison artifacts. These files are treated as supporting numerical context, not as experimental validation. Reproducing XROTOR runs requires a local XROTOR executable and the associated input workflow.

## Structural Screening

Structural screening files include reduced-order beam/frame FEA tooling and selected Abaqus-derived artifacts. These outputs support qualitative and comparative structural evaluation. They should not be interpreted as a full certification-grade structural validation.

## Plot and Table Generation

The clean workflow regenerates final figures and tables through `scripts/make_thesis_figures.py` and the associated wrapper scripts. Figure generation uses consistent labels, units, line styles, grids, and export locations.

## Output Files

Key regenerated outputs are written to:

- `outputs/geometry_exports/`
- `outputs/tables/`
- `outputs/figures/`
- `outputs/reports/`

The locked CSV and JSON inputs needed by the clean workflow are retained under `data/geometry/` and `data/processed/`.

## Reproducibility Notes

The locked modeled comparison shows a 1.497% predicted torque reduction for the GMT-inspired case at matched thrust and RPM. This result is promising but limited. The GMT propeller has not yet been physically tested, and the repository does not claim experimental validation, acoustic SPL validation, CFD validation, or thrust-stand performance data for the GMT design.
