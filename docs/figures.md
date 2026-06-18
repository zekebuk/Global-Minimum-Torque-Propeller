# Figure Inventory

The clean figure regeneration script is:

```bash
python scripts/make_thesis_figures.py
```

Outputs are written to `outputs/figures/` as PNG and PDF files where possible.

## Core Geometry Figures

- `fig_twist_distribution.png`
- `fig_chord_distribution.png`

## Core Aerodynamic Figures

- `fig_angle_of_attack_distribution.png`
- `fig_lift_coefficient_distribution.png`
- `fig_drag_coefficient_distribution.png`
- `fig_reynolds_distribution.png`
- `fig_sectional_thrust_distribution.png`
- `fig_sectional_torque_distribution.png`
- `fig_cumulative_thrust_distribution.png`
- `fig_cumulative_torque_distribution.png`
- `fig_mil_gmt_summary_bar.png`

## Optional Comparison Figures

- `fig_xrotor_bemt_comparison.png` when compatible XROTOR summary data are present.
- `fig_structural_von_mises_envelope.png` when compatible structural summary data are present.
- `fig_structural_displacement_envelope.png` when compatible structural summary data are present.

The optional figures should be interpreted as supporting numerical context, not as experimental validation.
