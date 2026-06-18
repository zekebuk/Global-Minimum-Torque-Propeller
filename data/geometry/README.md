# Geometry Data

This folder contains locked station-based MIL and GMT-inspired geometry files.

Columns include:

- `mu` or `r/R`: nondimensional radius.
- `radius_m`: dimensional station radius in meters.
- `dr_m`: station width used for integration.
- `chord_m`: chord in meters.
- `chord_over_R`: chord normalized by rotor radius.
- `twist_deg` or `beta_final_deg`: final blade twist in degrees.
- `airfoil`: station airfoil label.
- `case`: MIL or GMT case label.

The locked comparison keeps radial stations, chord distribution, and airfoil schedule fixed between MIL and GMT. The primary geometry difference is the twist distribution.
