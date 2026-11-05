# RL2407 manufacturing package

Everything needed to quote, build and test the RL-optimised 5" freestyle motor
(`data/motor_designs/rl_5in_freestyle_6s_v1/design.json`) on a small in-house line.

> **Status: rev P1, prototype.** The drawings, STEP files and plans are generated from the
> parametric model and have **not been checked by a human engineer**. The supplier prices are
> research snapshots (2026-11-05), not quotes, and the cycle times are estimates. Build 5-10
> prototypes, bench-test them (`docs/motor_development.md`), and calibrate the model
> (`bldc-motor design calibrate`) before ordering volume tooling.

| What | File(s) | Use |
| --- | --- | --- |
| Drawing set (9 A3 sheets) | `rl2407/drawings/RL2407_drawing_set.pdf`, `DWG-000…008_*.svg` | Assembly with parts list and assembly notes, lamination, bonded and coated core, wound stator with winding table and electrical tests, magnet (-01/-02), ring, bell, base, shaft |
| 3-D models | `rl2407/step/*.step` (7 parts + assembly) | CNC instant quotes (Xometry, PCBWay, JLCCNC), magnet and lamination RFQs |
| Lamination profile | `rl2407/dxf/P-001_lamination.dxf` | Laser, EDM or photo-etch vendors |
| Winding table | `rl2407/winding.csv` | Operator instructions (tooth, phase, direction) |
| Mechanical checks and stack-up | `rl2407/mechanical_checks.md`, `mechanical_design.json` | 15 clearance and fit checks, axial datums, performance at true CAD mass |
| Mass audit | `rl2407/mass_audit.csv` | CAD mass vs analytical model, by part group |
| Suppliers and RFQs | `suppliers.md` | Candidate suppliers per part with indicative prices and verification status |
| Assembly line plan | `assembly_line_plan.md`, `equipment_budget.csv`, `line_layout.svg` | 11 stations, $14.8k capital (< $15k incl. 15 % contingency), capacity, unit cost, ramp, quality and safety plan |

Regenerate everything with:

```bash
python -m pip install -e ".[rl,cad]"
bldc-motor design package --design data/motor_designs/rl_5in_freestyle_6s_v1/design.json --out docs/manufacturing/rl2407
bldc-motor design production --out docs/manufacturing
```

## Findings from building the package

1. **The motor is about 16 % heavier than the analytical model said.** CAD gives 47.5 g, or
   50.7 g with leads, against the model's 41.1 g. The sizing model under-counted the base,
   shaft and bell hardware: magnets and stator agree within 1 %. At the true mass the design
   is still feasible, but the T/W margin on the 8.0 requirement shrinks from 8.52 to **8.04**.
   Options if the margin matters: a hollow or titanium shaft (about 1.5 g), a 1.2 mm bell plate,
   a deeper base pocket, or shorter leads. The model's fixed-mass term should be recalibrated
   from this audit.
2. **Winding drives the line.** No automatic outrunner winder fits the budget, and
   jig-indexed hand winding (10 min per motor) is the bottleneck. The budgeted open-source CNC
   winder targets about 3 min and roughly $2.9 per motor of labour at $25/h.
3. **Two buys need quotes before anything else**, because they set most of the pilot cost:
   bonded and coated 0.10 mm stator stacks, and the custom arc magnets.
4. **Confirmed standard parts:** the bearing is **684ZZ** (NMB L-940ZZ), not MR84ZZ, and the
   e-clip is **DIN 6799 size 3.2**.
