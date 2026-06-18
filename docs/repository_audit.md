# Repository Audit

## Final Workflow Surface

- Final scripts: `scripts/run_geometry_generation.py`, `scripts/run_bemt_comparison.py`, `scripts/run_xrotor_comparison.py`, `scripts/run_structural_screening.py`, and `scripts/make_thesis_figures.py`.
- Final source package: `src/prandtl_prop/`.
- Final locked data: `data/geometry/` and `data/processed/`.
- Final regenerated outputs: `outputs/geometry_exports/`, `outputs/tables/`, `outputs/figures/`, and `outputs/reports/`.
- Final tests: `tests/`.

These are the only code paths cited by the README and workflow docs as the reproducible thesis workflow.

## Archive Boundary

`archive/` preserves development history that is not part of the final thesis-grade workflow:

- `archive/legacy_scripts/`: earlier geometry optimizers, BEMT/UI tooling, FEA helper directories, and old tests.
- `archive/exploratory_notebooks/`: exploratory MATLAB notebook work.
- `archive/old_outputs/`: superseded optimizer outputs, old generated CSV/XLSX files, audit reports, and legacy reference inputs.

Archived files may be useful for historical context, but they should not be used in quick-start commands or cited as final methodology unless a specific archived artifact is shown to have directly produced a final thesis/paper result.

## Cleanup Notes

- Generated caches and pytest temp folders were removed where filesystem permissions allowed.
- Small locked CSV/JSON files were kept in `data/` so the clean workflow can rerun without navigating historical output folders.
- External-software work with XFOIL, XROTOR, MATLAB, Abaqus, OpenVSP, and PSU-WOPWOP remains documented as context rather than as a fully Python-rerunnable workflow.
