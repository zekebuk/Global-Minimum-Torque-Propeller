# rl_design_5in_freestyle_6s: motor design spec sheet

Mission `5in_freestyle_6s`; design found by **rl_ppo**, refined on the manufacturable grid and screened for tolerance yield. Model: first-order analytical (`bldc_motor.design.model`), calibration: uncalibrated analytical model. **Predicted values: verify on the bench before production.**

## Design

| Parameter | Value |
| --- | --- |
| stator outer diameter | 24 mm |
| lamination stack length | 7.5 mm |
| stator ID / OD | 0.51 |
| yoke thickness / stator radius | 0.11 |
| tooth width / slot pitch | 0.43 |
| slot opening between shoes | 2.5 mm |
| mechanical airgap | 0.35 mm |
| magnet radial thickness | 1.7 mm |
| magnet arc / pole pitch | 0.83 |
| bell steel back-iron thickness | 1 mm |
| slots, poles | 12N14P |
| turns per tooth coil | 6 |
| magnet wire gauge | 23 |
| strands in hand | 1 |
| phase connection | wye |
| NdFeB grade | N52 |
| lamination thickness | 0.1 mm |

Winding (12N14P, k_w = 0.933): `AacCBbaACcbB` (upper case = forward); see `winding.csv`.

## Predicted electrical / magnetic

| Quantity | Value |
| --- | --- |
| Kv | 1687 rpm/V |
| R line-to-line (25 C) | 87.7 mOhm |
| Km | 19.12 mN·m/√W |
| Airgap / tooth / yoke / bell flux density | 0.65 / 1.59 / 1.16 / 1.45 T |
| Slot fill (bare copper) | 0.38 |
| Demagnetisation current limit (at 80 C) | 140 A |
| Mass | 41.1 g |

## Predicted in-aircraft performance

| Quantity | Value |
| --- | --- |
| All-up weight | 642 g |
| Hover throttle / efficiency | 0.28 / 5.79 g/W (motor η 0.897) |
| Thrust-to-weight | 8.5 |
| Punch-out motor current | 34.2 A |
| Winding / magnet temperature after burst | 123 / 59 C |

## Manufacturability

Score 1.00 (1 = no concerns); unit cost 4.71 USD (illustrative cost model).

| Rule | Value | Limit | Status |
| --- | --- | --- | --- |
| slot_fill | 0.378 | <= 0.38 ideal, 0.5 max | OK |
| needle_access | 2.5 | >= 1.21 mm | OK |
| wire_stiffness | 0.573 | <= 0.8 mm | OK |
| wire_fragility | 0.573 | >= 0.15 mm | OK |
| strands_in_hand | 1 | <= 3 | OK |
| turn_count_low | 6 | >= 3 | OK |
| turn_count_labour | 6 | strand-turns <= 40 | OK |
| airgap | 0.35 | >= 0.25 mm | OK |
| magnet_thickness | 1.7 | >= 1.0 mm | OK |
| tooth_width | 2.7 | >= 1.2 mm | OK |
| yoke_thickness | 1.32 | >= 0.8 mm | OK |
| shoe_overhang | 0.541 | >= 0.25 mm | OK |
| magnet_count | 14 | <= 28 | OK |
| unbalanced_pull | 2 | gcd(Q,2p) >= 2 | OK |
| delta_circulating | 0 | wye preferred (no 3rd-harmonic loop) | OK |

Tolerance Monte Carlo (200 as-built samples): **yield 100%**, Kv 1690 rpm/V ± 1.9% (1σ), burst winding P90 133 C.

## Bench acceptance test (prototype)

1. R line-to-line, 4-wire, corrected to 25 C: 87.7 mOhm ± 6 %.
2. No-load Kv at 100 % throttle: 1687 rpm/V ± 6% (3σ of tolerance Monte Carlo, min ±3 %).
3. Loaded sweep on the mission prop; compare to `predicted_stand_curve.csv` (model prediction, no noise).
4. Fit with `bldc-motor fit ... --r-ll-mohm <measured>`; feed the fitted motor to `bldc-motor design calibrate` to correct the model, then re-run the pipeline.

## Method comparison (same evaluation budget)

| Method | Evaluations | Best raw score | Best refined score | Best yield | First feasible at eval |
| --- | --- | --- | --- | --- | --- |
| rl_ppo | 157851 | +1.720 | +1.747 | 100% | 1991 |
| differential_evolution | 157845 | +1.779 | +1.779 | 72% | 1826 |
| random_search | 157851 | +1.568 | +1.679 | 87% | 1589 |

Files: `lamination.dxf`, `cross_section.svg`, `winding.csv`, `bom.csv`, `library.json`, `design.json`, `predicted_stand_curve.csv`, `design_policy.npz`, `training_log.csv`, `convergence.png`.
