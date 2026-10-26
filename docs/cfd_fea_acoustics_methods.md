# Higher-fidelity methods: transition, free wake, aeroelasticity, unsteady loading noise

This note covers four methods added on top of the BEMT / MRF-CFD / beam / FW-H stack. Each one
targets a specific assumption that the earlier stages made and that the energy audit
(docs/efficiency.md) showed to matter:

| Assumption in the baseline stack | Method that removes it | Module |
| --- | --- | --- |
| One analytic polar for every section; fully turbulent CFD | XFOIL e^N transition polars of the *molded* sections; 2D OpenFOAM k-omega SST and Langtry-Menter gamma-Re_theta | `aero/xfoil.py`, `aero/reconcile.py`, `cfd/airfoil2d.py` |
| Annulus momentum inflow (uniform per annulus, axisymmetric) | Free-vortex-wake lifting line, time-marching | `aero/fvw.py` |
| Rigid blade (designed twist = flown twist) | Static aeroelastic BEMT <-> beam loop; 1/rev fatigue | `structures/aeroelastic.py` |
| Steady blade loads in FW-H (hover-like tones only) | FW-H F1A with azimuth-varying compact loads from the free wake | `acoustics/fwh.py` |

Design point throughout: `designs/gmt_fpv10_tri.yaml` (10 in, 3 blades, 7400-7500 rpm hover,
3.54 N per rotor). Edgewise case: 10 m/s cruise, 6.7 deg nose-down disk tilt, 6798 rpm (the
trimmed forward-flight point from docs/mission.md).

## A. Transition: section polars and why the 3D CFD and BEMT disagreed

### Method

- **Geometry.** The polars are generated for the sections as molded, not the catalogue airfoil:
  each station's thickness scale and blunt trailing edge (`manufacturing.injection.modify_airfoil`)
  is applied. Stations are binned (thickness to 0.1, TE ratio to 0.01) so a design needs about 9
  section keys (`section_key`, e.g. `NACA4410T100E020`).
- **XFOIL** (Drela) at Re 4e4 to 3.5e5, two alpha sweeps outward from 0 deg so the boundary layer
  restarts from a converged attached solution. Transition is e^N with Ncrit set from the
  free-stream turbulence by Mack's correlation, Ncrit = -8.43 - 2.4 ln(Tu). A rotor blade sees
  its own wake and the previous blade's tip vortex, so Tu is not known; Ncrit 9 (quiet tunnel),
  7 (Tu ~ 0.2 %), 4 (Tu ~ 1 %) and a 5 % chord trip bracket it.
- **Post-stall** to +-180 deg by Viterna-Corrigan, Cd_max = 1.11 + 0.018 AR (AR = 10), stitched
  continuously at the last converged angle.
- **2D CFD** (`cfd/airfoil2d.py`): gmsh hybrid mesh (40-layer structured boundary layer, first
  cell 4e-5 c so y+ < 1, unstructured far field), one cell thick, OpenFOAM v1912. k-omega SST (fully turbulent) and kOmegaSSTLM (Langtry-Menter, the
  local-correlation transition model; Re_theta_t from Tu = 0.17 %, i.e. Ncrit ~ 7). The LM model
  is run as URANS (pimpleFoam) because its steady solution never settles: the laminar
  separation bubble sheds.

Assumptions: 2D sections (no rotational augmentation of Cl near the root, no spanwise flow); the
same Ncrit everywhere on the blade; incompressible (tip Mach 0.29).

### XFOIL build

Ubuntu's `xfoil` package is compiled with floating-point traps and aborts (SIGFPE) on most
low-Re cases. Rebuild it without them:

```
apt-get source xfoil && cd xfoil-*/
sed -i 's/-ffpe-trap=[a-z,]*//g' bin/Makefile* plotlib/Makefile* osgen/Makefile* 2>/dev/null
(cd plotlib && make) && (cd bin && make xfoil) && cp bin/xfoil /usr/local/bin/xfoil-nofpe
```

`find_xfoil()` uses `$PRANDTL_XFOIL`, then `xfoil-nofpe`, then `xfoil`. The tests that run XFOIL
are skipped when none is found.

### Results

Section, alpha = 4 deg, Re = 5e4 (outboard section, design condition):

| Model | Cl | Cd | Note |
| --- | --- | --- | --- |
| XFOIL, free, Ncrit 7 | 0.839 | 0.0313 | laminar to 73 % chord on top, then bubble |
| XFOIL, free, Ncrit 4 | 0.857 | 0.0237 | earlier transition, smaller bubble |
| XFOIL, tripped 5 % | 0.791 | 0.0289 | turbulent from the trip |
| OpenFOAM k-omega SST (2D, steady) | 0.726 | 0.0267 | fully turbulent; within 8 % of tripped XFOIL |
| OpenFOAM SST-LM (2D, URANS, Tu 0.17 %) | 0.643 +- 0.056 | 0.0509 +- 0.008 | bubble sheds; Cd +76 % vs tripped |

Rotor (BEMT with each polar library; `prandtl section-polars ... --cfd 3.543,42.89,7400`):

| Polar library | T at 7500 rpm (N) | P at 7500 rpm (W) | rpm for 3.544 N | P at 3.544 N (W) | FM |
| --- | --- | --- | --- | --- | --- |
| analytic (design) | 3.544 | 35.16 | 7499 | 34.78 | 0.545 |
| XFOIL free, Ncrit 9 | 2.826 | 37.55 | 8232 | 49.76 | 0.381 |
| XFOIL free, Ncrit 7 | 3.295 | 38.75 | 7731 | 42.44 | 0.446 |
| XFOIL free, Ncrit 4 | 3.773 | 38.71 | 7291 | 35.65 | 0.531 |
| XFOIL tripped 5 % | 3.757 | 40.66 | 7293 | 37.49 | 0.505 |
| 3D CFD, k-omega SST, MRF (at 7400 rpm) | 3.543 | 42.89 | | | |

Scaled to the CFD's 7400 rpm (T ~ n^2, P ~ n^3; assumes constant Cl, Cd over the 1.3 % change), the
tripped library gives T 3.66 N (+3 % vs CFD) and P 39.1 W (-9 %). The analytic polar gives 33.8 W.
Section Reynolds numbers run from 23,000 (root) to 63,000.

### What this means

1. **The 3D CFD is a fully turbulent result, and so is the CFD-calibrated BEMT.** k-omega SST
   with no transition model behaves like a tripped section, in 2D (within 8 % of tripped XFOIL)
   and in 3D (the rotor table above: thrust within 3 %, power within 9 %). Of the +27 % power gap
   between the design BEMT (33.8 W at 7400 rpm) and the 3D CFD (42.9 W), the tripped section polar
   accounts for about 60 % (39.1 W). That part is section drag at Re 2-6e4, which the analytic polar
   underestimates. The remaining ~9 % is 3D, mesh or wall-function effects not resolved here.
   Free transition spans FM 0.38 to 0.53 depending on Ncrit alone.
2. **At this Reynolds number, transition is the dominant aerodynamic uncertainty**, larger than
   anything the inflow model changes (section B). Free transition with a laminar bubble can go
   either way: XFOIL's e^N gives *higher* Cl at moderate alpha, while the LM URANS shows the bubble
   bursting and shedding, with much higher drag. Which one the real blade sees depends on the
   turbulence it ingests, which only a test can settle.
3. **Design consequence.** A trip strip or turbulator at ~5 % chord on the suction side makes the
   blade behave like the tripped polar regardless of inflow turbulence: predictable, and free of
   bubble-shedding noise (the LBL-VS source in the BPM model). It is a cheap feature to add to the
   mold. Until a rotor test says otherwise, use the tripped XFOIL library as the default polar and
   treat free transition as a sensitivity case.

4. **The best-L/D lift coefficient moves.** In the XFOIL libraries the sections have more drag
   than the analytic polar at Cl < 0.6, where the GMT blade's outboard sections run, and less drag
   near Cl ~ 1 (tripped: L/D 31 at alpha ~ 6.5 deg vs a flat 22 for the analytic polar; see
   `polars.png`). The GMT optimum Cl* = sqrt(cd0/k) = 0.78 was derived from the analytic polar.
   On the tripped polar the optimum is nearer Cl 1, so re-running the GMT chord + twist optimizer on
   the tripped library should give a narrower blade at higher Cl. That is the next design step these
   polars enable.

To fly a design on the XFOIL polars, point the design at the generated station file and library:

```yaml
geometry: {stations_csv: data/xfoil_polars/generated/gmt_fpv10_tri/stations_tripped_5pct.csv}
aero: {polar: {type: table, directory: data/xfoil_polars/generated/gmt_fpv10_tri/tripped_5pct}}
```

## B. Free-vortex wake

### Method

Time-marching lifting line after NREL OLAF / CHARM-type methods (docstring of `aero/fvw.py`):
blades as quarter-chord lifting lines with vortex rings, circulation from the polar by
Kutta-Joukowski (Gamma = 1/2 |W| c Cl), iterated each step; each step sheds a row of rings that
then convect freely with the induced velocity of all vorticity. Biot-Savart with the Vatistas
n = 2 core and Squire core growth rc^2 = rc0^2 + 4 alpha_L delta nu t (delta = 50). The kernel is
compiled with numba (68x faster than NumPy; identical results), which makes an 8-revolution wake
at 36 steps per revolution a one-minute run.

Assumptions: inviscid wake apart from the core model; rigid blades; the polar is the 2D one; no
hub or fuselage; wake truncated after `revolutions` turns (checked: going from a 6- to an 8-turn wake changes
thrust by 0.2 %).

### Results

| Case | Quantity | BEMT | Free wake | Difference |
| --- | --- | --- | --- | --- |
| Hover, 7400 rpm | thrust | 3.54 N | 3.22 N | -9 % |
| | torque | | | -5 % (FM 0.50 vs 0.54) |
| Edgewise 10 m/s, 6798 rpm | thrust | 3.56 N | 3.19 N | -10 % |
| | torque | 0.0367 N m | 0.0343 N m | -7 % |
| | peak outer-disk Cl | 0.93 | 0.64 | BEMT conservative |

The free wake converges (+-1 % between revolutions after 3 turns). Its thrust is lower than BEMT
in both cases, and the gap is systematic rather than resolution (test blade: -12 % at 6
panels/16 steps and at 8 panels/24 steps with an 8-turn wake). The free wake resolves the
downwash concentrated inboard of the previous blade's tip vortex that Prandtl's tip-loss factor
approximates. Two consequences:

- The hover efficiency the optimizer reports is optimistic by ~4 points of FM. All designs carry
  the same bias, so the ranking between them holds.
- The edgewise stall constraint in the optimizer (outer-disk Cl from BEMT with uniform inflow) is
  conservative: the free wake's peak is 0.64, well below stall. That constraint can be relaxed, or
  better, checked with the free wake on the finalists.

## C. Static aeroelasticity and once-per-revolution fatigue

### Method

`static_aeroelastic` iterates BEMT -> line loads (aero + centrifugal) -> beam -> elastic twist ->
BEMT, relaxed 0.5, until the twist changes by < 0.005 deg. `once_per_rev_fatigue` takes one
blade's azimuth-resolved thrust and drag per span (the free wake's), replaces the BEMT line loads
at every azimuth, re-solves the beam, and checks the modified Goodman criterion
sigma_a / S_f + sigma_m / S_u <= 1 / SF at the worst station and fibre.

Assumed material (conditioned PA6-CF30, along flow; replace with coupon data): S_u = 120 MPa,
S_f = 30 MPa at 2e8 cycles (500 h at 7000 rpm), SF = 1.5.

The root stress of this blade is set mostly by the in-plane (lag) moment and the centrifugal
tension. The net flap moment is small because centrifugal relief cancels most of the aero flap
moment. A scaled stress would be wrong here, which is why the beam is re-solved at each azimuth
rather than the stress scaled with the flap moment.

### Results

| Quantity | Hover | Full throttle | Edgewise 10 m/s |
| --- | --- | --- | --- |
| elastic wash-in at the tip | +0.15 deg | +0.61 deg | |
| rigid model's power error | small | overstates power by 2.65 % | |
| 1/rev blade thrust (first harmonic / mean) | | | 0.55 N / 1.06 N (52 %) |
| root stress, mean / alternating | 10.2 / 0.1 MPa | | 9.0 / 2.8 MPa |
| Goodman utilisation (with SF 1.5) | 0.13 | | 0.25 (margin +199 %) |

Lift acts at the quarter chord, ahead of the section centroid, so the blade twists nose-up
(wash-in) under load. The effect is small in hover and matters at full throttle, where the
optimizer should use the flown twist. Fatigue is not a constraint for this blade at 10 m/s.
The alternating stress grows with advance ratio (to first order, with mu), so recheck at the
fastest mission leg.

## D. Unsteady loading noise from the free wake

### Method

FW-H F1A compact loading noise now takes an azimuth-varying blade force history
(`RotorSources.compact_force_history_N`, (k, n, 3) over one revolution in the blade frame). The
history is Fourier-resampled to the acoustic time grid and differentiated spectrally. The rate of
the force on the fluid in the hub frame is L_dot = Omega x L + R(psi) dL_b/dtau. With
a periodic flight state, blade k is blade 0 delayed by k/B of a revolution, so the blade sum stays
exact. Two analytic checks in tests/test_acoustics.py: a constant history reproduces the steady
source to round-off, and a B/rev axial load seen from the axis gives the exact compact-dipole tone.

Assumptions: observers move with the rotor and the medium moves at 10 m/s (flight Mach 0.03); that
convection is neglected. Compact chordwise loading; loading harmonics resolved to the free wake's
step (48 per revolution: blade-loading harmonics to 24/rev). BPF1 and OASPL change by < 1 dB from
24 to 48 steps per revolution; BPF2 and above change by up to 4 dB (the BVI content is not
converged at that resolution).
Thickness and broadband noise are unchanged from docs/acoustics.md and not included in the
table.

### Results

Edgewise 10 m/s, observers at 10 R = 1.27 m, loading noise only (BPF = 340 Hz):

| Observer | Steady loading, BPF1 / OASPL (dB) | Unsteady (free wake), BPF1 / OASPL (dB) |
| --- | --- | --- |
| in plane, ahead (flight direction) | 45.7 / 45.7 | 58.0 / 58.2 |
| in plane, advancing side | 45.7 / 45.7 | 50.5 / 50.6 |
| in plane, retreating side | 45.7 / 45.7 | 49.8 / 50.1 |
| in plane, behind | 45.7 / 45.7 | 44.6 / 46.7 |
| 45 deg below, ahead | 43.4 / 43.4 | 69.9 / 71.4 |
| 45 deg below, advancing side | 43.4 / 43.4 | 66.5 / 69.1 |
| on the axis, below | (none) | 70.0 / 71.8 |

Physics: steady loading radiates only through the blade's rotation, which at tip Mach 0.26 is a
weak source (the Bessel factor J_mB(mB M sin theta) is small) and is zero on the axis. The 1/rev
asymmetry of edgewise flight, together with the higher harmonics from the skewed wake, puts load
harmonics at n = B (3/rev). Those radiate at BPF as an axial dipole with no Bessel suppression. The
2-3/rev blade loads (0.12 N and 0.08 N, 12 % and 7 % of the mean) raise tonal loading noise below and ahead of the rotor
by **25-28 dB**, and ahead of it in plane by 12 dB. For a small, slow-tipped rotor in forward flight, unsteady loading, not steady
loading or thickness, sets the tones heard on the ground. Hover acoustics overstate how quiet the
rotor is in cruise.

Design levers that follow: reduce the 2-3/rev load content, i.e. a lower advance ratio (larger or
faster-turning rotors for the same cruise speed) and less skewed inflow. Blade count is a
hypothesis worth testing with this tool: the BPF tone is fed by the B/rev load harmonic, and the
harmonics decay with order (0.55, 0.12, 0.08, 0.03 N for 1-4/rev here), so B = 4 may be quieter in
cruise. The hover-only acoustics cannot see that trade.

## Reproducing

```
prandtl section-polars designs/gmt_fpv10_tri.yaml --cfd 3.543,42.89,7400     # A (minutes; needs XFOIL)
#   A, 2D CFD: airfoil2d.solve(points, case_dir, airfoil2d.Airfoil2DCase(reynolds=5e4, alpha_deg=4, model="kOmegaSSTLM", transient=True))
pytest tests/test_aero_methods.py tests/test_acoustics.py                    # B, D checks
```

The free-wake, aeroelastic and unsteady-noise studies above are short scripts over
`fvw.solve`, `aeroelastic.static_aeroelastic` / `once_per_rev_fatigue` and `fwh.pressure_history`.
`python scripts/fvw_study.py` runs all of them and writes `outputs/methods/<design>/summary.json`.
