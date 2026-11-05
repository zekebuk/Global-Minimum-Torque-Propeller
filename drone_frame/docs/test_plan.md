# PF10 hardware test and calibration plan

The pipeline's numbers are only as good as its inputs. This plan turns each ASSUMPTION in
`config/frame.yaml` / `config/materials.yaml` into a measurement, in the order that de-risks the build
fastest. Every test ends with **"update X in config, re-run the pipeline"**, which closes the loop.

```
 config ─► optimise ─► CAD ─► print ─► TEST ─┐
   ▲                                          │
   └────────── measured values ◄──────────────┘
```

## Gate 0 - before printing the frame

| # | Test | How | Pass / update |
| --- | --- | --- | --- |
| 0.1 | Density | Print a 20 mm cube per material at 100 %, weigh to 0.01 g | `density` per material |
| 0.2 | Line width / layer | Print the arm's 4-loop wall test, measure with calipers | `line_width`; re-run (web = loops x width) |
| 0.3 | Flexural coupons | 80x10x4 mm bars, X-Y orientation, as the arm is sliced (same walls/infill/direction). 3-point bend span 64 mm on a cheap fixture + luggage scale + dial gauge | E_flex -> back out `k_E_process`; strength -> `k_process` |
| 0.4 | Conditioning | Repeat 0.3 after 7 days at room humidity (PAHT-CF, PPA-CF) | `k_E_env`, `k_env` |
| 0.5 | Socket fit | Print a 40 mm long socket coupon from the bottom-body file (crop in Bambu Studio) and one arm tenon | Slides in by hand without play -> keep `socket_clearance`; else ±0.05 |

## Gate 1 - first arm

| # | Test | How | Pass / update |
| --- | --- | --- | --- |
| 1.1 | Mass | Weigh | Within 5 % of `design_report.md` (fill factor 1.0) |
| 1.2 | Static proof | Clamp the tenon in a printed socket coupon; hang 1.5 x limit load (1.5 x 27.5 N = 4.2 kg) from the motor pad for 60 s | No crack or creep; tip deflection ≈ 1.5 x report value. Deflection off by > 15 % -> calibrate `k_E_process` |
| 1.3 | **Tap test (modal)** | Clamp as in 1.2 with a motor + prop (or equal dead mass) on the pad. Tap the pad; record with a phone microphone or a Betaflight FC taped on (gyro blackbox) and FFT. Tap vertically, laterally, and off-axis on the pad edge (torsion) | Compare with the vertical/lateral/torsion modes in the report. Back out the effective E (f ∝ √E) and set `k_E_process`. Torsion off -> adjust `shear_modulus_factor`. Re-run: the optimiser re-places the modes |
| 1.4 | Fuse check (destructive, a spare arm) | Load to failure as in 1.2 | Break must be in the arm span/root, never in the socket coupon (fuse ratio > 1.3) |

## Gate 2 - assembled frame, no props

| # | Test | How | Pass / update |
| --- | --- | --- | --- |
| 2.1 | Assembly mass | Weigh frame + all electronics | Update the component masses -> AUW closure |
| 2.2 | Arm swap | Remove 2 bolts, slide arm out, unplug MR30, replace | < 2 min, motor position repeatable (measure prop-tip height ±0.5 mm) |
| 2.3 | Jetson thermal soak | 30 min at max load (`jetson_clocks`, `stress`), canopy on, a bench fan blowing down at the hover induced velocity (~6 m/s) | SoC < 85 °C. Canopy interior < 60 °C (PETG). Otherwise enlarge the louvres or reprint the canopy in PAHT-CF |
| 2.4 | Soft-mount | Tap the Jetson tray, FFT | Mount frequency ≈ `isolation_target_Hz` (30 Hz). Choose grommet hardness accordingly |

## Gate 3 - propulsion and flight

| # | Test | How | Pass / update |
| --- | --- | --- | --- |
| 3.1 | Thrust stand | Motor + prop on the arm in its socket coupon. Sweep throttle | Fit `ct_static`, `cp_static`, `motor_max_thrust_N` -> re-run (bands move) |
| 3.2 | Hover vibration | Bidirectional DShot + RPM filter on; 60 s hover blackbox; spectrum of the gyro | Peaks only at the rotor orders. No broad hump at the arm modes. If a mode sits in a cruise band, re-run with measured values |
| 3.3 | Throttle sweep | Slow 0→100 % climb, log | Transient crossings at the arm modes stay below the filter's attenuation. Note the throttle where they occur on the Campbell diagram |
| 3.4 | Endurance / fatigue | 10 flights x 15 min, inspect arm roots and bolt holes (10x loupe) | No whitening or cracks. Log airframe hours per arm serial |

## Calibration formulas

* Modal → modulus: E_eff = E_model · (f_measured / f_model)².
  Divide by the TDS E to get the new `k_E_process` (keep `k_E_env` separate).
* Static deflection → modulus: E_eff = E_model · (δ_model / δ_measured).
* Expect 1.3 and 1.2 to agree within about 10 %. If they don't, the socket joint is the compliance
  (`joint_contact_layer`, `joint_contact_fraction`). Tighten the clearance before adjusting material values.
