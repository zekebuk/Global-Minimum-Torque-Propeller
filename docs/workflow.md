# Reproducibility Workflow

The clean public workflow uses locked geometry and BEMT postprocessing data copied into `data/`. Historical implementation attempts are preserved under `archive/` for traceability, but they are not part of the final reproducible thesis workflow.

```mermaid
flowchart TD
    A[Design Inputs] --> B[Station-Based Geometry]
    B --> C[BEMT Aerodynamic Postprocessing]
    B --> D[XROTOR Comparison]
    C --> E[Performance Metrics]
    D --> E
    B --> F[Structural Screening]
    E --> G[Thesis Figures and Tables]
    F --> G
```

## Fully Python-Reproducible From This Repo

```bash
python scripts/run_geometry_generation.py
python scripts/run_bemt_comparison.py
python scripts/run_structural_screening.py
python scripts/make_thesis_figures.py
pytest
```

These commands read the locked CSV/JSON data and regenerate clean outputs under `outputs/`.

Archived scripts and notebooks are intentionally excluded from this command list.

## External Software Context

- XFOIL: used historically to generate airfoil polar CSVs. Existing locked station data are used by the clean BEMT result.
- XROTOR: optional comparison artifacts are present, but this is not experimental validation.
- MATLAB: required for the legacy 3D beam/frame structural workflow.
- Abaqus: required for native `.cae`, `.odb`, and related structural solver files.
- OpenVSP and PSU-WOPWOP: exploratory aeroacoustic tooling is present but is not a locked validation basis.
