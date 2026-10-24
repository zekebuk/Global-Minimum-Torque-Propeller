# Designing the Blade for Injection-Molded CFRP

`prandtl optimize-molding` adapts a design for injection molding in short-fibre CFRP. Planform,
twist, and camber lines stay as designed, so the thesis aerodynamic design is preserved. Only the
section thickness distribution and the trailing edge change.

```bash
prandtl optimize-molding designs/gmt.yaml --material peek-cf30 --max-thickness-mm 9
prandtl run runs/gmt/molding_optimisation/gmt_peek_cf30.yaml --stages dfm,fea,modal   # confirm with CalculiX
```

## Assumptions

- **Process.** A solid blade in a two-plate mold, gated at the root, so skin fibres align spanwise
  with the flow. The mold opens perpendicular to the span.
- **Material.** The presets in `src/prandtl_prop/manufacturing/materials.py` use typical datasheet
  values: PA6-CF30 (conditioned) E 15 GPa, UTS 150 MPa; PEEK-CF30 E 24 GPa, UTS 260 MPa. Design
  values apply knockdowns of 0.85 on stiffness (skin-core fibre orientation) and 0.40 on strength
  (high-cycle fatigue, weld lines, process scatter). **Replace them with the chosen grade's datasheet.**
- **Molding rules.** Minimum blunt trailing edge 0.6 mm. Maximum solid section thickness: 6.5 mm by
  default; beyond that, sink, voids, and a cooling time growing with t^2 call for a cored or
  gas-assisted root. Spanwise thickness gradient at most 0.05 m/m. Spanwise flow length at most 150x
  the thinnest section. Draft at least 0.5 deg away from the parting line.
- **Operating envelope.** A multirotor controls thrust through rpm. The default band is 0.9-1.1 x
  design rpm, and every flap mode must stay at least 10% clear of the 1P-4P lines across the band.

## Method

1. **Geometry.** Thickness scales about each airfoil's camber line at four control radii
   (r/R = 0.2, 0.45, 0.7, 1.0), interpolated linearly between them, plus a linear wedge that blunts
   the trailing edge to the minimum thickness.
2. **Undercuts (exact).** The pull direction has no spanwise component, so every pull ray stays in
   one section plane. The blade is undercut-free if and only if every section outline is monotone
   along the pull. The best pull angle is found by scanning.
3. **Frequencies.** A rotating Euler-Bernoulli beam model covers coupled flap and lag through I_yz,
   centrifugal stiffening, and spin softening, using the true section properties. On the GMT blade it
   matches the CalculiX Campbell results to 0.3% for flap modes (lag runs 8% high, since shear
   flexibility is neglected). It runs in milliseconds, so it can sit inside the optimiser; CalculiX
   confirms the result.
4. **Objective.** The extra profile torque from thickening, computed with the Hoerner form factor
   `1 + 2 t/c + 60 (t/c)^4` at BEMT's own station inflow. That is first order: valid for moderate
   thickness changes at fixed camber, and blind to Cl_max and stall-margin changes. The default lower
   bound is therefore 1.0: molding may add thickness, never remove it.
5. **Search.** The constraints are non-smooth (mode identities switch), so gradient-based SLSQP
   fails. Instead: a uniform-scale scan and a 2-D scan of linear root-to-tip profiles find feasible
   starts, multi-start COBYLA polishes them, and the returned design is validated exactly as written.

## The frequency-placement result that drives everything

The first flap frequency rises with rotor speed, f(n)^2 = f0^2 + K n^2, with K of about 1.6 for this
blade. Keeping it 10% clear of both kP and (k+1)P over [n_lo, n_hi] needs
f(n_hi) >= 1.1 k n_hi and f(n_lo) <= 0.9 (k+1) n_lo:

| rpm band | 1P-2P (soft) | 2P-3P (stiff) | 3P-4P |
| --- | --- | --- | --- |
| 0.9-1.1 x design | f0 <= 77 Hz | 132 <= f0 <= 143 Hz | impossible |
| 0.8-1.2 x design | f0 <= 68 Hz | **impossible** | impossible |

This depends only on K, the band, and the margin, not on material. With a +/-20% rpm band, no
material or thickness avoids 2P and 3P together. Every design also crosses some kP lines while
spinning up, because the lines fan out from the origin. A soft blade crosses 2P on the way up; a
stiff blade crosses 3P and 4P (usually weaker excitation).

## Results (smoke inputs: analytic polar, NACA 4410 stand-in sections)

| Option | Thickness scales at r/R 0.2 / 0.45 / 0.7 / 1.0 | Torque penalty | Mass | Flap 1 over band | Stress | Tip deflection | Max thickness |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Baseline (sharp TE), aluminium | 1 / 1 / 1 / 1 | - | 88 g | 147-156 Hz | 45 MPa | 1.8 mm | 5.7 mm |
| **A: PEEK-CF30, stiff 2P-3P** | 1.61 / 1.15 / 1.0 / 1.0 | **+0.30%** | 60 g | 153-163 Hz | 15 MPa | 2.7 mm | 8.9 mm (root) |
| **B: PA6-CF30, soft 1P-2P** | 1.0 / 1.0 / 1.0 / 1.17 | **+0.38%** | 46 g | 107-119 Hz | 29 MPa | 7.6 mm | 5.9 mm |
| PEEK-CF30 with the 6.5 mm cap | - | infeasible | | | | | |
| PA6-CF30, stiff 2P-3P | - | infeasible even with a 10 mm cap (E/rho too low) | | | | | |

Notes:

- Results are with the default 1% model cushion (the optimiser targets an 11% margin; CalculiX must
  confirm at least 10%). Without the cushion, the optima were +0.29% (A) and +0.23% (B), but B then
  fell to 9.6% in CalculiX.
- Uniform thickening to reach 2P-3P in PEEK costs +2.0% torque. Tapering the extra thickness to
  the root (where flap stiffness comes from) while leaving the outboard light reaches the same
  frequency for +0.30%, roughly 7x less.
- A tighter 8.5 mm root cap is still feasible for PEEK (about +0.31% before the cushion).
- Option B lowers the frequency into the soft window by adding tip mass (tip sections 17% thicker)
  instead of removing material. It crosses 2P near 0.75 x design rpm during spin-up and deflects
  7.6 mm at the tip (beam, without centrifugal stiffening).
- All options pass every molding check, including undercut-free release at a pull angle of about
  9 deg from the thrust axis.

### CalculiX confirmation

Solid C3D10 models of the optimised geometry: NLGEOM static under aero plus CENTRIF, and
prestressed Campbell analyses at 0, 0.9, 1.0, and 1.1 x design rpm.

| | A: PEEK-CF30 | B: PA6-CF30 |
| --- | --- | --- |
| First flap at 0 / 3600 / 4000 / 4400 rpm (CalculiX) | 130.6 / 153.8 / 158.7 / 163.9 Hz | 75.9 / 107.2 / 113.2 / 119.4 Hz |
| Smallest separation over the band (CalculiX) | **11.7%** (from 2P at 4400 rpm) | **10.7%** (from 2P at 3600 rpm) |
| Beam model vs CalculiX, first flap | within 1% | within 1% |
| Peak von Mises outside the clamp zone (NLGEOM) | 10.8 MPa (allowable 104) | 10.6 MPa (allowable 60) |
| Tip flap deflection (NLGEOM) | 1.7 mm | 3.1 mm |
| Centrifugal root force, FEA vs beam | 0.999 | 1.000 |
| Molding checks (TE, thickness, gradient, flow, undercut, draft) | all pass, pull 9 deg | all pass, pull 2.5 deg |

Static values come from the first confirmation round; the geometry changed by under 1% in thickness
when the cushion was added. The first round also caught two defects, both now fixed: the molding
check measured margins against the blade frequency instead of the excitation (so 10% read as 9.1%),
and the Campbell check covered only the design speed. Option B sat at 9.6% at the band edge without
the cushion.

## Recommendation

- **Option A (PEEK-CF30, stiff)** is the conservative choice. No 1P/2P crossing inside the band, low
  stress, small deflection, and the GMT aero advantage survives (+0.30% torque against a GMT-MIL
  difference of about 1%). The 8.9 mm root exceeds the 6.5 mm general solid-section guideline: plan
  for a cored or ribbed root under a skin, gas assist, or accept the longer cycle. Ask the molder.
- **Option B (PA6-CF30, soft)** is cheaper and lighter, but it passes 2P on every spin-up and
  absorbs moisture (the conditioned modulus was used). Use it only if spin-up is fast and the
  airframe does not amplify 2P.
- Apply the same treatment to the MIL baseline before comparing torques, so the thesis comparison
  stays like for like.

## Before cutting steel

1. Rerun with the real airfoils and XFOIL polars, *and* regenerate polars for the thickened,
   blunted sections; the form-factor estimate is first order.
2. Insert the real material datasheet (conditioned values for PA).
3. Run a mold-filling and warpage simulation (Moldflow or Moldex3D). Anisotropic shrinkage
   (about 0.2-0.4% along the flow, 0.8-1.2% across) can change twist, the quantity this design
   optimises. Compensate the mold geometry for it.
4. Design the root/hub interface (insert, bolt pattern), which is outside this model.
