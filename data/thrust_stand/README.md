# Thrust Stand Data

No locked GMT thrust-stand validation data are included in this repository.

This folder is reserved for raw or processed experimental data if physical testing is added later. Any future files should document units, test configuration, calibration details, and whether the data were used in final thesis figures.

## Format for Motor Bench Data

Thrust-stand logs for motor development (`bldc-motor import` / `bldc-motor fit`) should follow the column schema and bench protocol in `docs/motor_development.md`: `Name (unit)` headers; bus voltage and current measured at the ESC input; rpm; thrust; torque; and winding and bell temperatures where available. Keep the measured line-to-line resistance, its measurement temperature, the propeller mass and diameter, the supply or pack used, and the ESC firmware/settings in a sidecar note next to each log.

Synthetic logs from `bldc-motor synth` begin with a `# SYNTHETIC` comment. They must not be stored here as measurements.
