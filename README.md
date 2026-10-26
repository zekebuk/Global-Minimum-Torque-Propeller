# Aerodynamic Design and Structural Evaluation of a Global Minimum Torque Multirotor Propeller

This repository contains the analysis code used to generate geometry, aerodynamic postprocessing results, structural screening reports, and thesis figures for a 22-inch global-minimum-torque-inspired multirotor propeller. The thesis compares a two-blade minimum induced loss (MIL) baseline with a Prandtl bell-spanload-inspired GMT case at a locked static-hover operating condition: 4000 rpm and 35 N target thrust.

The repository is a research workflow, not a general propeller design package. The locked modeled comparison showed a modest predicted torque reduction for the GMT case at matched thrust and RPM. The GMT propeller has not yet been physically tested, and this repository does not claim experimental validation, CFD validation, acoustic SPL validation, or thrust-stand performance data for the GMT design.

## Repository Structure

| Path | Purpose |
| --- | --- |
| `src/prandtl_prop/` | The single implementation: BEMT (`aero/`), CAD (`cad/`), structures (`structures/`), external tools (`external/`), design files, run cache, pipeline, and CLI. |
| `designs/` | Design files (`mil.yaml`, `gmt.yaml`) for the iterative pipeline, plus smoke-test overrides. |
| `scripts/` | Thin entry points for the locked thesis workflow. |
| `scripts/gui/` | Interactive launchers for the Streamlit aero UI and MATLAB FEA viewer. |
| `runs/` | Pipeline run folders, created on demand and git-ignored. |
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
python -m pip install -e .[cad,dev]     # or: python -m pip install -r requirements.txt (adds the GUI extras)
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

The XROTOR and MATLAB commands are report-only by default. Add `--execute` only when the required external software and inputs are available. A requested `--execute` that cannot run exits with status 2 and says why. Archived scripts are deliberately excluded from the quick start.

## Iterative Design Pipeline (BEMT -> CAD -> FEA/CFD)

```bash
prandtl run designs/gmt.yaml                                  # fast stages: aero, sections, cad (STEP in mm), beam
prandtl run designs/gmt.yaml --stages fea                     # CalculiX solid FEA with centrifugal stiffening
prandtl run designs/gmt.yaml --stages modal                   # Campbell diagram (prestressed modes vs rpm)
prandtl run designs/gmt.yaml --stages cfd                     # OpenFOAM MRF hover CFD
prandtl run designs/gmt.yaml --stages acoustics               # FW-H tonal + BPM broadband noise, twist comparison
prandtl run designs/gmt_fpv10_tri.yaml --stages sitl          # rotor tables, C++ header, PX4 params for a simulator
prandtl sitl-optimize designs/gmt_fpv10_tri.yaml --log a.ulg  # rank GMT twists by mission energy on flown missions
prandtl run designs/gmt_pa6_cf30_folding.yaml --stages envelope,mission   # 0-20 m/s map, CFD at mission points, CFD loads -> CalculiX
prandtl design-gmt designs/fpv10_tri_spec.yaml --output designs/gmt_fpv10_tri.yaml   # 10 in tri-blade FPV: GMT twist, vehicle sizing
prandtl run designs/gmt_fpv10_tri.yaml --stages aero,sections,dfm,cad,beam,multirotor   # ... and the pipeline on it
prandtl cost designs/gmt_fpv10_tri.yaml designs/gmt_pa6_cf30_fixed.yaml --set-file designs/smoke_overrides.txt   # cost per prop vs volume by route
prandtl loop designs/gmt.yaml                                 # BEMT -> CAD -> CFD -> calibrate -> re-trim
prandtl optimize-molding designs/gmt.yaml --material peek-cf30   # thickness schedule for injection-molded CFRP
prandtl run designs/gmt_peek_cf30.yaml --stages cad,mold      # full blade (root tab, bolt holes) + core/cavity mold STEP
prandtl run designs/gmt_pa6_cf30_folding.yaml --stages hub,mold   # folding prop: one hinge pin per blade, center hub
prandtl run designs/gmt_pa6_cf30_fixed.yaml --stages hub,mold     # fixed one-piece prop (blades + boss), clamp cap
prandtl compare designs/mil.yaml designs/gmt.yaml --stages aero,beam
prandtl run designs/gmt.yaml --set operating_point.rpm=4200   # iterate on any design value
prandtl run designs/gmt.yaml --set-file designs/smoke_overrides.txt   # smoke test without thesis data
```

The pipeline re-solves BEMT with a bracketed per-station solve and thrust trim. It then lofts the
trimmed blade with CadQuery (STEP in mm for SolidWorks, plus section curves), runs a beam screen
and a CalculiX solid FEA with centrifugal stiffening, and runs OpenFOAM hover CFD. A loop
calibrates BEMT against the CFD station loads and re-trims until CFD meets the target thrust. Each
stage is cached in a content-addressed run folder. Real runs need the XFOIL polars and airfoil
coordinates in `data/` (see `data/xfoil_polars/README.md`). See `docs/iterative_workflow.md` for the
conventions, the verification evidence, and the planned CalculiX and OpenFOAM stages. See
`docs/cfrp_injection_molding.md` for the molded blade and its mold, and `docs/hub.md` for the hub.
See `docs/production.md` for the production hardware stack and cost per prop by route.
See `docs/efficiency.md` for the energy audit (where the battery energy goes, what to work on next) and the chord + twist GMT redesign.
See `docs/sitl.md` for the SITL bridge (custom simulator + PX4): rotor tables, motor/battery model, PX4 parameters, log replay, and mission-weighted prop selection.
See `docs/acoustics.md` for the aeroacoustic stage (FW-H tonal + BPM broadband) and what GMT twist does to rotor noise.
See `docs/fpv10_tri.md` for the 10 in tri-blade FPV variant (global-minimum-torque twist with the
multirotor sized around it). See `docs/mission.md` for off-design performance from static to 20 m/s, flow plots, and FEA driven
by the CFD wall loads.

**Locked result caveat:** the archived solver left the tip station unconverged in both locked
cases, and the GMT tip violates the momentum balance by 84%. A first-order estimate puts the
corrected torque reduction near 0.9%, not 1.50%. The figures below are the historical locked values
until they are re-locked with the new solver and the real polars.

## Locked-Result Reproducibility Workflow

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
