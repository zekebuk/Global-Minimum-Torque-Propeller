# BLDC Motor Development Toolkit (FPV)

`src/bldc_motor/` is a self-contained toolkit for developing and matching brushless outrunner
motors for FPV multirotors: model → bench test → identify → validate → match. It is separate
from the locked thesis workflow (`prandtl_prop`) and does not change any thesis result.

```mermaid
flowchart LR
    A[Bench test<br/>thrust stand CSV] --> B[teststand<br/>import + unit normalisation]
    B --> C[steady-state<br/>plateau extraction]
    C --> D[identification<br/>Kv, I0(ω), R, CT/CP, thermal]
    D --> E[library JSON<br/>measured components]
    E --> F[powertrain<br/>battery→ESC→motor→prop]
    F --> G[thermal<br/>steady + transient]
    F --> H[matching sweep<br/>hover / punch-out / limits]
    D --> I[model-vs-stand<br/>validation plot]
    S[synthetic<br/>labelled fake logs] -.rehearse.-> B
```

## Quick start

```bash
python -m pip install -e .
bldc-motor list                                   # bundled (illustrative) components
bldc-motor point  --motor generic_2207_1950kv --prop generic_5.1x4.6_tri --battery lipo_6s_1300
bldc-motor hover  --motor generic_2207_1950kv --prop generic_5.1x4.6_tri --battery lipo_6s_1300 --auw-g 650
bldc-motor match  --dry-mass-g 250 --batteries lipo_6s_1300 lipo_4s_1500
bldc-motor thermal --motor generic_2207_1950kv --prop generic_5.1x4.6_tri --battery lipo_6s_1300 --profile "0:0.3,10:1,20:0.3"
bldc-motor effmap --motor generic_2207_1950kv --prop generic_5.1x4.6_tri --battery lipo_6s_1300
bldc-motor rewind --motor generic_2207_1950kv --kv 2550

# bench data → fitted motor + prop → reuse everywhere via --library
bldc-motor import stand_run.csv --steady --diameter-in 5.1
bldc-motor fit stand_run.csv --no-load no_prop_run.csv --r-ll-mohm 64.2 --r-temp-c 23 \
    --diameter-in 5.1 --supply-v 24 --name my_2207_1950 --template generic_2207_1950kv
bldc-motor --library outputs/motor/fit/fitted_library.json point --motor my_2207_1950 --prop fitted_prop --battery lipo_6s_1300

python scripts/run_motor_development_demo.py      # whole pipeline on synthetic data
```

Outputs go to `outputs/motor/` (git-ignored; regenerate at will).

> **The bundled library is illustrative.** Its motors, props and packs are generic
> size-class placeholders (`source: "illustrative"`), not manufacturer data and not
> measurements. Replace them with fitted components before drawing conclusions.

## Model and assumptions

### Motor (`motor.py`)

Quasi-steady DC-equivalent model, averaged over many electrical cycles:

| Relation | Meaning |
| --- | --- |
| `V = Ke(T_m)·ω + R(T_w)·I` | voltage balance (back-EMF + resistive drop) |
| `Q = Ke(T_m)·(I − I0(ω))` | shaft torque |
| `I0(ω) = i0_a + i0_slope·ω` | no-load loss current |
| `R(T) = R_ref·(1 + α_Cu·(T − T_ref))`, α_Cu = 0.00393 /K | copper resistance |
| `Ke(T) = Ke_ref·(1 + β·(T − T_ref))`, β ≈ −0.0012 /K | NdFeB remanence drift |
| `Ke = Kt = 60 / (2π·Kv)` | SI identity (Kv in rpm/V) |

Exact power balance: `V·I = I²R + Ke·ω·I0 + Q·ω` (copper + iron/friction + shaft).

Assumptions:

* `R` is the **line-to-line** resistance. In six-step commutation two phases conduct in series,
  and that is also the right DC-equivalent value when Kv is measured the usual hobby way
  (no-load rpm per DC-bus volt).
* `I0` constant ⇔ loss ∝ ω (bearing friction, hysteresis); the linear term ⇔ loss ∝ ω² (eddy
  currents). Windage (∝ ω³) is neglected.
* Magnet temperature ≈ stator/bell node temperature.
* Inductance, commutation/timing advance, and current ripple are not modelled. These matter at
  very high eRPM, where they show up as a "Kv sag" at high load. If the model-vs-stand
  residuals grow with speed × current, this is the likely cause.
* Motor constant `Km = Kt/√R` [N·m/√W] is the size-honest figure of merit. A rewind of the
  same stator (`MotorParams.rewound`) keeps Km fixed, because Kv ∝ 1/N and R ∝ N² at
  constant copper fill. Changing Kv trades voltage for current; it does not buy efficiency.

### Propeller (`propeller.py`)

`T = CT(J)·ρn²D⁴`, `Q = CP(J)·ρn²D⁵/2π`, with CT and CP as polynomials in `J = V/(nD)`.
Static figure of merit is `FM = CT^1.5 / (CP·√(π/2))`. Reynolds-number dependence is neglected
(a few percent across the rpm range of small props), so fit coefficients in the rpm band you
care about. `PropellerModel.from_static_point` builds coefficients from any single point,
for example the thesis's locked 22-inch MIL point (checked in the tests).

### Powertrain (`powertrain.py`)

* ESC = ideal buck with duty `d = throttle × max_duty` (linear throttle, no throttle curve),
  conduction loss as a series resistance: `V_motor = d·V_bus − I·R_esc`.
* Bus current by power conservation: `I_bus = d·I_motor + I_q`.
* `n_motors` identical motors at the same throttle share one pack:
  `V_bus = V_oc(SoC) − (R_pack + R_leads)·n·I_bus`. This is solved as a nested 1-D root problem
  (shaft torque balance inside, pack sag outside). Both are monotone, so the root is unique.
* The LiPo OCV(SoC) table is generic; pass `ocv_table` for a measured pack. Regen is not modelled.

### Thermal (`thermal.py`)

Two-node network with speed-dependent convection:

```
C_w dT_w/dt = P_cu − G_ws (T_w − T_s)
C_s dT_s/dt = P_iron + G_ws (T_w − T_s) − G_sa(ω) (T_s − T_amb)
G_sa(ω) = g_static + g_ref (ω/ω_ref)^0.8        (turbulent Nusselt scaling, Nu ∝ Re^0.8)
```

`R(T_w)` and `Ke(T_s)` are updated every step, so copper-loss runaway is captured. Rotor
mechanics are treated as quasi-static (ms time scales vs. s for thermal). The integrator is
Heun's method; `steady_state` is a damped fixed point that reports `converged=False` on runaway.
Stand-measured `G_sa` underestimates in-flight cooling (forward flight adds airflow), so stand
numbers are conservative.

## Identifiability: why you must measure R (or run at full throttle)

A stand measures bus voltage `V_b`, bus current `I_b`, speed `ω` and torque `Q`, but **not
the ESC duty**. Eliminating the duty, each point gives only the loss balance

```
V_b·I_b − Q·ω = R·I_m² + Ke·ω·I0,     I_m = Q/Ke + I0
```

which is unchanged under `(Ke, R, I0) → (s·Ke, s²·R, I0/s)` for any `s`. Bus-side partial-
throttle data alone therefore **cannot** determine Kv (see `test_bus_side_scaling_degeneracy`).
Any one of these breaks the scaling:

1. Measure `R_ll` with a 4-wire milliohm meter and pass it in (`--r-ll-mohm`). This is the
   recommended route. The fit then uses the inferred duty
   `d = [Ke·ω + √((Ke·ω)² + 4·V_b·R_tot·I_net)] / (2·V_b)`.
2. Include points where the motor voltage is known: 100 % throttle (duty = max duty) or a
   no-prop run from a DC supply at 100 %. Only then may `--fit-resistance` be used.
   The fitter refuses the unidentifiable case.

On synthetic data with stand-level noise, the fit recovers Kv within about 0.3 %, R within about
1 % (when fitted), and I0 within about 10 %. The reported σ values are statistical only. Thermal
drift inside a hold window adds bias of a few σ, so keep holds short and log temperature.

Temperature matters: a 27 s stepped sweep on a 2207 can heat the winding by more than 100 K.
If the fitter ignores the bell temperature, the magnet weakening alone biases Kv by about 8 %.
Log a winding/motor temperature (`temp_c`) and, ideally, a bell temperature
(`magnet_temp_c`). The fitter corrects R and Ke per point and reports values at `ref_temp_c`.

## Bench-test protocol

Safety first: use a prop guard or enclosure and a current-limited supply or a fused pack, and
keep a physical kill switch within reach. Never stand in the prop plane.

1. **Resistance.** Cold motor at a known temperature. Use a 4-wire (Kelvin) milliohm meter on
   each of the three line-to-line pairs and average them. Record the temperature and correct it
   with `--r-temp-c`. A spread of more than 3 % between pairs indicates a winding or
   termination fault.
2. **No-load run (Kv, I0).** No prop, DC supply at 2–4 voltages, 100 % throttle, 3 s holds.
   Log V, I, rpm and temperature. For eRPM telemetry, pass `--erpm --pole-pairs N` (12N14P → 7).
3. **Loaded sweep.** Prop on, throttle steps of 0.1 from 0.2 to 1.0 with 2–3 s holds, finishing
   with a 100 % step. Let the motor cool between sweeps. Log V, I, rpm, thrust, torque and
   temperature(s) at ≥ 10 Hz. The importer averages each plateau after `--settle-s`.
4. **Thermal run.** Fixed throttle for at least 3 time constants (usually 60–120 s on a 5" motor),
   logging temperature, V, I, rpm and torque. Fit with
   `bldc-motor fit-thermal run.csv --power-w <mean loss>`, where loss = `V·I − Q·ω`. This gives
   `G` and `C` for the dominant mode at that speed.
5. **Validate.** Run `bldc-motor fit ... --supply-v <V>` to produce `model_vs_stand.png`. The
   residuals should be within sensor noise. A systematic trend points to a missing effect
   (see the motor assumptions above).

### Thrust-stand CSV schema

Headers of the form `Name (unit)` are parsed and converted to SI. Recognised names (case and
punctuation are ignored) and units:

| Canonical | Typical headers | Units accepted |
| --- | --- | --- |
| `time_s` | Time, Timestamp | s, ms, us, min |
| `throttle` | Throttle, Command, Duty | fraction, % |
| `esc_signal_us` | ESC signal, PWM | µs, ms (throttle derived as (µs−1000)/1000 if no throttle column) |
| `voltage_v` / `current_a` | Voltage, Current (ESC input, i.e. DC bus) | V, mV / A, mA |
| `rpm` | Motor Optical Speed, Motor Electrical Speed, RPM | rpm, krpm, Hz, rad/s |
| `thrust_n` | Thrust, Force | N, kgf, gf, g, lbf, ozf |
| `torque_nm` | Torque | N·m, N·mm, N·cm, kgf·cm, gf·cm, ozf·in, lbf·in |
| `temp_c` | Winding Temp, Motor Temp, Temperature | °C, K, °F |
| `magnet_temp_c` | Bell Temp, Rotor Temp, Case Temp | °C, K, °F |

Anything else can be mapped explicitly with `load_stand_csv(column_map={...})`. Negative
thrust or torque (from load-cell orientation) is sign-flipped, with a warning. Lines starting
with `#` are ignored. Synthetic logs start with `# SYNTHETIC ...` and must never be reported as
measurements.

## Module map

| Module | Responsibility |
| --- | --- |
| `units.py` | SI conversions, header-unit tables, material constants |
| `motor.py` | `MotorParams`, `ThermalParams`, `MotorPoint`; rewind scaling |
| `propeller.py` | `PropellerModel`; static CT/CP fit |
| `powertrain.py` | `Battery`, `ESC`, `Powertrain` operating-point solvers |
| `thermal.py` | steady state, throttle profiles, transient simulation |
| `teststand.py` | CSV import, derived metrics (g/W, η, CT, CP, FM), plateau extraction |
| `identification.py` | Kv/I0/R/thermal fits with identifiability guards |
| `matching.py` | hover / punch-out / thermal / limit sweep across components |
| `synthetic.py` | labelled synthetic stand logs, bench-supply helper |
| `library.py` | JSON component library load/merge/save |
| `plotting.py` | efficiency map + prop line, model-vs-stand, thermal trace |
| `cli.py` | `bldc-motor` command line |

## Known limitations / next steps

* No inductance or commutation model, so high-eRPM, high-load "Kv sag" is not predicted.
* Props: no Reynolds-number correction and no forward-flight polar beyond the polynomial in J.
* ESC switching losses are lumped into the series resistance.
* Pack model: a single resistance with no RC polarisation dynamics, so a punch-out sag
  transient appears instantaneous.
* Matching assumes symmetric throttle on all motors (hover and punch-out), not yaw or attitude
  authority margins.
