# Final FEA Organization

The final thesis-locked geometry and aerodynamic loading package is stored in:

- `results/FINAL_THESIS_USE_THESE/01_geometry/`
- `results/FINAL_THESIS_USE_THESE/02_aero_validation/`

Locked geometry files:

- `results/FINAL_THESIS_USE_THESE/01_geometry/final_mil_geometry.csv`
- `results/FINAL_THESIS_USE_THESE/01_geometry/final_gmt_geometry.csv`

Locked aerodynamic load files used by the beam FEA:

- `results/FINAL_THESIS_USE_THESE/02_aero_validation/mil_bemt_validation_LOCKED.csv`
- `results/FINAL_THESIS_USE_THESE/02_aero_validation/gmt_bemt_validation_LOCKED.csv`
- `results/FINAL_THESIS_USE_THESE/02_aero_validation/locked_aero_summary.json`

The active MATLAB beam implementation is preserved in its original location:

- `Code/Propeller_3D_Beam_6DOF_FEA/`

A non-destructive copy may also exist under:

- `Code/fea/legacy_3d_beam/`

Use `Code/fea/run_final_fea.py` as the Python-side final FEA boundary. It reports the locked final input paths and the MATLAB command to run.

The main MATLAB driver has been updated to read the locked MIL and GMT geometry and BEMT load files:

```matlab
cd Code/Propeller_3D_Beam_6DOF_FEA
main_propeller_3d_beam_fea
```

Outputs are written under:

- `results/fea/beam_solver/final_locked_mil_gmt/final_locked_mil/`
- `results/fea/beam_solver/final_locked_mil_gmt/final_locked_gmt/`
- `results/fea/beam_solver/final_locked_mil_gmt/final_locked_mil_gmt_fea_summary.csv`

Existing outputs generated from older reference geometry, UI defaults, raw optimizer geometry, or the former 48 N debug setup should be treated as archived/debug only.
