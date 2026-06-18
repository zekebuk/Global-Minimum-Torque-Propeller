# XFOIL Polar Data

The locked BEMT comparison uses precomputed XFOIL polar CSVs. In the working thesis repository, those files are stored under:

`CAD/airfoils_good/generated_polars/`

The clean workflow uses the already-processed locked station CSVs in `data/processed/`, so XFOIL is not required to regenerate the top-level figures and summary tables. Regenerating the full BEMT workflow from geometry and airfoil coordinates requires XFOIL or equivalent polar data.
