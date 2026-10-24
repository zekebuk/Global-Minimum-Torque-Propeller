# XFOIL Polar Data

The pipeline's aero stage (`prandtl run designs/<case>.yaml`) reads XFOIL polars from this folder.
These files are not committed yet. Copy them from the working thesis repository:

`CAD/airfoils_good/generated_polars/*.csv`  ->  `data/xfoil_polars/`

Expected format: one file per airfoil and Reynolds number, named `<AIRFOIL>_Re<N>_polar.csv`
(for example `MH115_Re200000_polar.csv`), with columns `alpha` (deg), `Cl`, `Cd`, and optionally
`Cm` and `converged`. Rows with `converged` false are dropped. The solver interpolates linearly in
alpha and Reynolds number. Queries outside the table are clamped to its edge and counted in the run
warnings, never extrapolated.

Airfoils required by the locked geometry: MH115, S4310, A3, A4, A6, A7, A11, A13, at Re 100k, 150k, 200k, and 300k.

The locked-result scripts (`scripts/run_aero_postprocessing.py`, `scripts/make_thesis_figures.py`)
use the processed station CSVs in `data/processed/` and do not need these files.
