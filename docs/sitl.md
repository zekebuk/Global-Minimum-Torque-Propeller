# SITL bridge: fly the prop in your simulator, then let the missions pick the prop

The propeller pipeline designs a blade at one point: diameter, rpm, free stream and thrust. A
multirotor flies a whole envelope, and its controller, motors and battery decide which part of that
envelope the prop actually works in. This bridge closes the loop with a custom simulator running
PX4:

```
design  ──►  prandtl run --stages sitl  ──►  rotor tables + motor/battery card + PX4 params  ──►  your sim + PX4 SITL
   ▲                                                                                                │
   │                                                                                    ULog / truth-CSV logs
   │                                                                                                ▼
   └────── best twist (stations.csv, tables, params)  ◄──  prandtl sitl-optimize --log ...  ◄── mission replay
```

```
prandtl run designs/gmt_fpv10_tri.yaml --stages sitl                    # export for the simulator
prandtl sitl-replay   designs/gmt_fpv10_tri.yaml --log flight1.ulg --log survey.csv
prandtl sitl-optimize designs/gmt_fpv10_tri.yaml --log flight1.ulg --log survey.csv --weights 3,1
```

With no `--log`, both commands use three built-in reference missions (hover, survey, freestyle),
which stand in for SITL logs until real ones exist.

Arm blockage is deliberately left out. The frame is not chosen yet, and the estimate below shows it
is a ~2% thrust correction that hardly moves the twist optimum. Add it as a CFD-derived factor once
the frame CAD exists.

## 1. What the sim gets (`sitl` stage)

| File | Contents |
| --- | --- |
| `rotor_table.json` | C_T, C_Q, C_H, C_M over (λ_c, μ) (plus rpm for a Reynolds-dependent polar), definitions, VRS flags |
| `rotor_tables.hpp` + `prandtl_rotor.hpp` | the same table as `constexpr` arrays and a header-only C++17 evaluator, wrench, motor, battery |
| `sitl_model.json` | motor (Kv, R, I₀, J), battery (cells, Ah, R, OCV curve), mass, inertia, rotor layout, frames, assumptions |
| `px4_params.txt` | `CA_ROTORi_CT/KM/PX/PY`, `THR_MDL_FAC`, `MPC_THR_HOVER`, `BAT1_*` fitted to the model |
| `mission_*.csv` | reference missions in the truth-log format: the template for your sim's logger |
| `rotor_tables.png`, `missions.png`, `reference_missions.csv`, `operating_histogram.csv` | checks and the replay of the design as built |

### Rotor tables, from first principles

With the blade fixed, the forward-flight BEMT (`multirotor.rotor_forward`) depends on the rotor speed
Ω and on the hub's inflow. The inflow has an axial part V_axial along the thrust axis (climb > 0)
and an edgewise part V_edge in the disk plane. With a Reynolds-independent polar, dimensional
analysis collapses this to

```
λ_c = V_axial / ΩR,   μ = V_edge / ΩR
T = ρA(ΩR)² C_T,  Q = ρA(ΩR)² R C_Q,  H = ρA(ΩR)² C_H,  M_roll = ρA(ΩR)² R C_M
```

So one (λ_c, μ) table is **exact at every rpm**. The test checks this against the BEMT to 1e-9. A
table polar adds an rpm axis.

The tables cover:
- **Descent.** λ_c from −0.4 (descent) to +0.5 (climb or windmilling).
- **Edgewise flow.** μ from 0 to 0.6.
- **Clamping.** Inflow beyond the grid is clamped, and every load still goes to zero with (ΩR)²
  as Ω → 0.
- **Accuracy.** Interpolation between nodes is within 0.3% on the 10″ design.

The hub roll moment C_M is new in `rotor_forward`. It is the azimuth average of r·dT·sin ψ: the
advancing blade's extra lift on a rigid, unhinged prop.

**Vortex-ring state** is flagged, not modelled. The flag is evaluated from the interpolated thrust,
using the boundaries of Wolkovitch and Johnson (NASA TP-2005-213477):

```
−2 < V_axial / v_h < −0.3   and   V_edge / v_h < 1,   with v_h = ΩR √(C_T / 2)
```

### Per-step use in the simulator (FRD body frame, rotors thrust along z_r = −z_b)

```
v_rel   = v_air,b + ω_b × p_i                        hub velocity relative to the air
V_axial = −v_rel,z;  V_edge = |v_rel,xy|;  e_f = v_rel,xy / V_edge
loads   = evaluate(table, rpm_i, V_axial, V_edge)
force   = T z_r − H e_f
moment  = p_i × force − s_i M_roll e_f + s_i Q z_b    (s = +1 counter-clockwise from above)
```

The sign rules:
- **Advancing side.** It lies at s(e_f × z_r), so a counter-clockwise rotor moving forward has its
  advancing side on the right and rolls the body left.
- **Reaction torque.** It yaws the body by +s·Q, the same sign as PX4's `KM > 0` for counter-clockwise
  rotors.
- **Tested.** `test_wrench_signs` pins both signs, and the C++ header is compiled and compared with
  Python in `test_cpp_header_matches_python`.

### Motor, ESC and battery

```
ESC      motor sees u·V_bus (u = PX4 normalised motor command); bus current u·I/η_esc
motor    I = (u V_bus − ω/Kv)/R_m;  Q_m = Kt (I − I₀);  J dω/dt = Q_m − Q_aero
braking  synchronous rectification (BLHeli_32 / AM32 under DShot): I < 0 regenerates; off → I ≥ 0
battery  V_bus = V_oc(SoC) − R_b Σ I_bus   (closed form: V_bus = (V_oc + g Σ u e)/(1 + g Σ u²), g = R_b/(R_m η))
```

V_oc(SoC) is a typical LiPo rest curve: 4.20 to 3.27 V per cell. The parameters are the same ones
the `multirotor` stage uses.

The bus is shared and scaled by each duty cycle. That is why the hover battery power, 178.6 W, is
below the multirotor stage's 193.6 W: that stage charges every motor with n·R_b at full current. It
is also why the motor time constant is 11 ms here against 22 ms there.

`test_motor_forward_dynamics_track_the_replay` integrates this ODE with the replay's duty and bus
voltage. The rotor speeds come back within 1%.

### PX4 parameters

Each parameter comes from the model's static thrust curve: J dω/dt = 0 at each command, at the
mid-charge bus voltage.

| Parameter | 10″ GMT tri | How |
| --- | --- | --- |
| `CA_ROTORi_CT` | 16.19 N | thrust at u = 1 (PX4: T = CT u²) |
| `THR_MDL_FAC` | 0.974 | least-squares fit of T(u)/T(1) to f u² + (1 − f) u |
| `CA_ROTORi_KM` | ±0.01263 m | Q/T at hover, + for counter-clockwise rotors |
| `MPC_THR_HOVER` | 0.219 | T_hover / CT |
| `CA_ROTORi_PX/PY` | ±0.1485 m | quad-X layout from `multirotor.arm_length_m` (placeholder until the frame) |
| `BAT1_*` | 6S, 4000 mAh, 12 mΩ | from `multirotor` |

Check the names against your PX4 version. `CA_ROTORi_KM` carries the GMT trade: a torque-minimised
prop has a small Q/T, and that is exactly the yaw authority PX4 will see.

## 2. Logs and replay

**What a log becomes.** Each log becomes a `Trace`: the body air velocity and rates, plus the wrench
[total thrust, L, M, N] the rotors had to produce.

**Supported sources:**
- **Truth CSV (preferred).** Columns `t_s, vx_b, vy_b, vz_b` (air-relative, FRD), optionally
  `p, q, r, v_bus`, then either `rpm_0..rpm_{n−1}` or `thrust_total, L, M, N`. Log this from the sim,
  which knows the true airspeed and rpm. The stage's `mission_*.csv` files are examples.
- **PX4 ULog.** Topics used:
  - `actuator_motors` sets the time base;
  - `vehicle_attitude`, `vehicle_local_position`, `vehicle_angular_velocity` and `battery_status`
    give attitude, velocity, rates and bus voltage;
  - `wind` is subtracted when present;
  - `esc_status` rpm is used when reported. Otherwise rpm is solved from the command and bus voltage
    through the motor model, which assumes the sim runs this same model.
  - `test_ulog_ingest_reconstructs_rpm` writes a minimal ULog and reads it back.

**Replaying a candidate prop.** A candidate must deliver the same wrench from the same inflow.

1. **Rotor speeds.** Damped Newton on the n rotor speeds solves wrench4(rpm) = W. Each rotor's loads
   depend only on its own speed, so one perturbed evaluation gives the Jacobian. The full nonlinear
   rotor enters, including torque, hub roll moment and H-force. A prop with less torque per thrust
   therefore needs a larger thrust split for the same yaw moment, so yaw authority is priced in. An
   earlier proportional-allocation scheme oscillated in fast banked turns, which is why it was
   replaced.
2. **Motor.** Q_m = Q_aero + J dω/dt gives the current and terminal voltage. The shared-bus voltage
   comes from SoC and R_b, and duty is u = V_term / V_bus.
3. **Battery.** SoC and the cell energy ∫V_oc I_bus dt are integrated over time.

A sample counts as **saturated** when any of these holds:
- some motor needs u > `sitl.max_throttle`;
- the wrench is unreachable inside the speed limits;
- the battery cannot supply the power;
- a non-braking ESC would need negative current.

The test round trip recovers logged rpm to 1e-4.

**Frozen trajectory.** This is the one big assumption: the candidate flies the same path. It holds
while the controller tracks, and saturation shows where it would not. The real check is to fly the
candidate's exported tables in SITL, which is the loop.

### Reference missions

These are quasi-steady rigid-body traces, not a controller:
- **Velocity.** The velocity follows segment targets through a triple-pole critically damped
  filter, so jerk is continuous.
- **Thrust.** The thrust vector is m(a − g) − D, low-passed at 25 rad/s as an attitude-loop
  bandwidth. Unfiltered turbulence would mean unbounded angular acceleration.
- **Attitude and moments.** Attitude comes from the thrust direction and heading, and the moments
  are I ω̇ + ω × Iω.
- **Wind.** A mean plus one-pole turbulence with a 30 m length scale.

| Mission | Content |
| --- | --- |
| hover | 120 s in 3 m/s wind, 1.5 m/s gusts |
| survey | climb to ~40 m, four 300 m legs at 12 m/s with 180° turns, loiter, descend; 5 m/s cross-wind |
| freestyle | twice: 15 m/s punch-out, 10 m/s dive through the wake, 25 m/s straight, ~2.6 g banked turn, 6 rad/s yaw spin |

Replay of the 10″ GMT tri as built:

| Mission | Energy | Mean battery power | p99 duty | Saturated | Vortex-ring state |
| --- | --- | --- | --- | --- | --- |
| hover | 5.95 Wh / 120 s | 178.6 W | 0.44 | 0 % | 2.4 % (vertical gusts) |
| survey | 8.40 Wh / 193 s | 156.7 W | 0.47 | 0 % | 4.2 % (descent) |
| freestyle | 6.32 Wh / 70 s | 325.3 W | 0.88 | 0.06 % | 7.1 % (dives) |

![Reference missions](images/sitl_missions.png)

## 3. Mission-weighted selection (`sitl-optimize`)

**The candidate family.**
- **Planform.** The design's own planform, optionally chord-scaled with `sitl.chord_scales`.
- **GMT twists.** The global-minimum-torque twist for the vehicle's hover thrust, designed at
  f × (hover rpm as built) and bell weight μ.
- **References.** The design as built, Betz/MIL and constant pitch.

**Evaluation.** Every candidate gets its own tables and replays every mission on the **same motor
and battery**: the hardware you buy, with Kv as matched for the design as built. Then:
- the objective is Σ w_m E_m, the weighted battery energy over the missions;
- a candidate is feasible if, on every mission, saturation plus time over the current limit stays
  within the larger of `sitl.allowed_saturation_fraction` and the as-built value.

**What `best/` holds:** `stations.csv` (the twist, ready for a design file), the tables, the C++
header and `px4_params.txt`. Load those into the sim, fly, and log; the next iteration starts there.

RESULTS_PLACEHOLDER

## Assumptions and limits

- **Rotor aerodynamics.** BEMT with uniform induced velocity per annulus and Glauert momentum
  edgewise. Reverse flow carries no load, wake skew is ignored, and the hub is rigid (no flapping
  relief of the roll moment).
- **No interference.** Rotor–rotor and rotor–arm interference are not modelled; arm blockage waits
  for the frame.
- **Blockage estimate.** For an arm of width w under one disk radius:
  - the arm covers w/(πR) of the disk, about 3% for a 12 mm arm on a 10″ prop;
  - the force on the arm ÷ thrust ≈ C_d × q_wake/(T/A) × w/(πR) ≈ 1.2 × 0.5 × 0.03 ≈ 2%;
  - that acts as a uniform factor on vehicle thrust. Its once-per-blade-pass part matters for
    vibration and noise, not for energy or navigation.
- **Vortex-ring state** is flagged only. Descent-heavy missions need an empirical VRS model in the
  sim before the replay's descent energy can be trusted.
- **Missions are not a controller.** Use real SITL logs for decisions; the reference missions exist
  to exercise the pipeline.
- **Electrical model.** Ideal DC machine; an open-loop duty ESC; a generic LiPo curve. Replace these
  with bench data (thrust stand: T, Q, I and rpm against command) when the motor is bought. That
  bench test also validates the tables directly.
