# Custom Propeller Beam FEA Formulation

## Source Formulation

This solver follows the ME 504 Chapter 10 three-dimensional frame element formulation provided by Professor Hall. The core formulation uses a two-node 3D frame element with six degrees of freedom at each node, giving 12 element degrees of freedom total.

The Chapter 10 ideas used in this solver are:

- one 3D frame element between two nodes
- 6 DOF per node
- 12 element DOF total
- local element stiffness matrix `k_e`
- element transformation matrix `B_e`
- transformed global element stiffness `K_e = B_e^T k_e B_e`
- equivalent nodal load vectors
- global assembly by element DOF mapping
- boundary condition partitioning
- static solution of the reduced free-DOF system
- local element force recovery

This is not a shell or solid FEA solver. The propeller blade is modeled as a reduced-order 3D frame/beam structure with one 6-DOF structural node per radial station. Adjacent stations are connected by 3D frame elements. The 3D ribbon plot is a visualization and stress-envelope display, not a shell mesh.

## Problem Statement

The solver models a single propeller blade as a spanwise 3D beam. The blade geometry is discretized at radial stations from the root to the tip. Each radial station becomes one structural node. Each adjacent pair of stations forms one 3D frame element.

The purpose of the model is to estimate reduced-order blade structural response:

- displacement
- axial force
- shear force
- bending moment
- torsion when explicitly modeled
- beam stress envelope
- modal frequencies when enabled

The model is intended for engineering screening and comparison. It is not intended to capture local 3D stress concentrations, bolt-hole stress, root fillet stress, shell behavior, or laminate-level composite stress.

## Assumptions

The main assumptions are:

- The blade is represented by a 1D spanwise beam centerline.
- Each radial station has one 6-DOF structural node.
- Each element is a 2-node Euler-Bernoulli 3D frame element.
- Section properties are reduced-order properties computed from local chord and thickness assumptions.
- Local element properties are taken as representative averages over each element.
- The root node is clamped.
- Small-displacement linear static analysis is used.
- Gyroscopic effects are neglected in the reduced modal analysis.
- The ribbon view is only a visualization/stress-envelope representation.

## Coordinate Systems

The local element coordinate system follows the propeller beam convention:

- local `x` = blade span/radius direction
- local `y` = chordwise/edgewise/tangential direction
- local `z` = flapwise/thrust direction

The local element axes are transformed into the global coordinate system using the same transformation idea from the ME 504 Chapter 10 formulation. In this implementation, the blade twist angle is used when defining the local `y` and `z` axes for each element.

## Degrees of Freedom

Each node has six degrees of freedom:

```text
u_i = [u_x, u_y, u_z, theta_x, theta_y, theta_z]^T
```

For one two-node element, the local element displacement vector is:

```text
u_e = [
  u_x1, u_y1, u_z1, theta_x1, theta_y1, theta_z1,
  u_x2, u_y2, u_z2, theta_x2, theta_y2, theta_z2
]^T
```

This matches the ME 504 Chapter 10 three-dimensional frame convention.

## Element Stiffness Matrix

Each element uses the 12x12 3D frame stiffness matrix from the ME 504 formulation. The local stiffness matrix includes:

- axial stiffness from `EA/L`
- torsional stiffness from `GJ/L`
- bending stiffness about local `y` using `E Iy`
- bending stiffness about local `z` using `E Iz`

The important local stiffness terms are:

```text
EA/L
GJ/L
12 EIy/L^3
6 EIy/L^2
4 EIy/L
2 EIy/L
12 EIz/L^3
6 EIz/L^2
4 EIz/L
2 EIz/L
```

The full local matrix is assembled in `local_stiffness_3d_beam.m`.

## Local to Global Transformation

The ME 504 notation is:

```text
u_bar_e = B_e U_e
```

In the code, this convention is stored as:

```text
u_local = T * U_global
```

The local stiffness matrix is transformed to global coordinates by:

```text
K_e = T^T k_e T
```

This is the same as the Chapter 10 form:

```text
K_e = B_e^T k_e B_e
```

The element load vectors are transformed with the same operation:

```text
F_e = T^T f_e
F_0e = T^T f_0e
```

The element transformation is built in `transformation_matrix_3d_beam.m`.

## Global Assembly

The global stiffness matrix and global load vectors are assembled by mapping each element's 12 local/global DOF into the full global DOF array.

For an element connecting nodes `n1` and `n2`, the element DOF list is:

```text
[(n1-1)*6 + (1:6), (n2-1)*6 + (1:6)]
```

The global stiffness contribution is added as:

```text
K(edofs, edofs) = K(edofs, edofs) + K_e
```

This assembly is performed in `assemble_global_stiffness.m`.

## Load Vector

The solver supports distributed beam loads in the local element coordinate system:

- `q_x`: spanwise/centrifugal load
- `q_y`: chordwise/edgewise/tangential load
- `q_z`: flapwise/thrust load
- `m_x`: distributed torsional moment about local `x`, only when explicitly modeled

### Aero-solver distributed loads

When the FEA model consumes aero-solver output, the aero solver provides distributed aerodynamic loads by radial station:

- `qz_N_per_m` = flapwise/thrust distributed load
- `qy_tangential_N_per_m` = chordwise/edgewise/tangential distributed load
- `torque_loading_Nm_per_m` = `dQ/dr`, used for torque consistency checks

The FEA solver imports these quantities and interpolates them onto the beam stations. It does not compute `Cl`, `Cd`, `alpha`, `phi`, thrust, or torque internally.

### Centrifugal loading

Centrifugal loading remains internal to the FEA model:

```text
q_x(r) = rho_material A(r) omega^2 r
```

where:

- `rho_material` is material density
- `A(r)` is section area
- `omega` is rotor speed in rad/s
- `r` is radial position

### Legacy loading

Legacy uniform, elliptical, bell, or CSV thrust loading is retained for comparison and debugging.

### Shaft torque note

Total shaft torque `Q` from the aero solver is not directly applied as blade spanwise torsion. Shaft torque is represented through tangential loading `q_y` and `dQ/dr`. Blade torsion about local `x` is applied only if a separate distributed torsional moment `m_x` is provided or explicitly modeled.

## Equivalent Nodal Loads

Distributed loads are converted to equivalent nodal loads using the same element load vector idea from Chapter 10.

For a local distributed load over an element:

- axial `q_x` contributes equivalent forces to axial DOF
- transverse `q_y` contributes equivalent shear forces and end moments in the local `y` bending direction
- transverse `q_z` contributes equivalent shear forces and end moments in the local `z` bending direction
- distributed torsion `m_x` contributes equivalent nodal torsional moments only when explicitly modeled

In the implementation:

- `fe_local` is the applied equivalent nodal load vector
- `f0e_local` is the Chapter 10 restoring fixed-end load vector
- for distributed loads, `f0e_local = -fe_local`

The element load conversion is implemented in `local_equivalent_load_3d_frame.m`, and the blade loading setup is handled in `apply_propeller_loads_3d.m`.

## Boundary Conditions

The blade root is clamped. The root node constraints are:

```text
u_x = 0
u_y = 0
u_z = 0
theta_x = 0
theta_y = 0
theta_z = 0
```

The Chapter 10 partitioned form is:

```text
[F_f]   [K_ff K_fr][U_f]   [F_0f]
[F_r] = [K_rf K_rr][U_r] + [F_0r]
```

The code uses the governing form:

```text
K U + F0 = F
```

With prescribed root displacement `U_r`, the free displacement solution is:

```text
U_f = K_ff^-1 (F_f - F_0f - K_fr U_r)
```

For the clamped propeller root, `U_r = 0`.

Boundary conditions are defined in `apply_boundary_conditions.m`, and the static solve is performed in `solve_static_fea.m`.

## Solution Phase

After assembly and boundary condition partitioning, the unknown free DOF are solved from:

```text
U_f = K_ff \ (F_f - F_0f - K_fr U_r)
```

The full displacement vector `U` is reconstructed by inserting the prescribed root DOF and solved free DOF. Reactions are computed from:

```text
R = K U + F0 - F
```

This follows the Chapter 10 partitioned solution approach.

## Element Force Recovery

After the global displacement vector is solved, each element displacement vector is extracted from the global displacement vector and transformed back into local coordinates:

```text
u_e = T U_e
```

Local element forces are recovered with:

```text
f_e = k_e u_e + f_0e
```

The recovered local resultants include:

- `N`: axial force
- `Vy`: edgewise shear
- `Vz`: flapwise shear
- `T`: torsion
- `My`: bending moment
- `Mz`: bending moment

Element force recovery is implemented in `recover_internal_forces_3d.m`.

## Stress Recovery

Normal stress is recovered from axial and bending terms:

```text
sigma_x = N/A + My z/Iy + Mz y/Iz
```

The stress envelope is evaluated at representative section points around the reduced airfoil/chord section. The solver reports:

- maximum tensile stress
- maximum compressive stress
- maximum absolute normal stress
- von Mises estimate when used
- safety factor using the specified allowables

This stress recovery is a reduced-order beam stress estimate. It is not a shell stress or solid stress field.

Stress recovery is implemented through:

- `recover_beam_stresses.m`
- `beam_section_normal_stress.m`
- `computeSectionStressEnvelope.m`
- `computeVonMisesFromBeamStress.m`

## Modal Analysis

When modal analysis is enabled, the reduced eigenvalue problem is:

```text
K phi = omega_n^2 M phi
```

The natural frequency is:

```text
f_n = omega_n / (2 pi)
```

The Campbell diagram compares modal frequencies to rotor excitation harmonics and blade passing frequency. In this reduced implementation, gyroscopic effects are neglected unless a separate model is added.

Modal-related implementation files include:

- `assembleGlobalMassMatrix.m`
- `beamElementConsistentMassMatrix.m`
- `solveModalEigenproblem.m`
- `computeRotorExcitationFrequencies.m`
- `plotCampbellDiagram.m`

## Validation Checks

Useful validation checks for this model include:

- root DOF are zero for the clamped root
- free-end shear and bending approach zero near the tip
- centrifugal axial force is largest near the root and approaches zero at the tip
- imported aero `q_z` integrates to aero thrust
- imported torque loading integrates to aero torque
- `P = Q omega`
- stress generally peaks near the root/hub region
- internal units remain SI
- modal frequencies are positive and finite
- rigid-body modes are removed by the clamped root boundary condition

Existing validation scripts include:

- `validate_chapter10_frame_theory.m`
- `validate_quiz_cases_A_to_E.m`
- `validate_cantilever_tip_load.m`
- `validate_axial_bar.m`
- `validate_torsion.m`
- `validate_modal_analysis_sanity.m`
- `validate_stress_envelope_sanity.m`

## Units

Internal units:

- length: `m`
- force: `N`
- moment: `N*m`
- stress: `Pa`
- density: `kg/m^3`
- rotor speed: `rad/s`

Common reported units:

- stress: `MPa`
- displacement: `m` or `mm`, depending on the plot/report
- frequency: `Hz`

## Implementation Map

The main theory-to-code mapping is:

- `read_prop_geometry.m`: reads propeller radial station geometry.
- `build_3d_beam_mesh.m`: creates one structural beam node per radial station and one element between adjacent stations.
- `section_properties_from_geometry.m`: computes reduced beam section properties.
- `local_stiffness_3d_beam.m`: builds the 12x12 local 3D frame stiffness matrix.
- `transformation_matrix_3d_beam.m`: builds the local-to-global element transformation.
- `assemble_global_stiffness.m`: transforms element stiffness matrices and assembles global `K`.
- `load_aero_loads_for_fea.m`: imports aero-solver station loads for FEA use.
- `apply_propeller_loads_3d.m`: builds distributed loads and assembles equivalent nodal loads.
- `local_equivalent_load_3d_frame.m`: converts local distributed loads into equivalent element nodal loads.
- `apply_boundary_conditions.m`: applies the clamped root boundary condition.
- `solve_static_fea.m`: solves the partitioned Chapter 10 static system.
- `recover_internal_forces_3d.m`: recovers local element resultants.
- `recover_beam_stresses.m`: computes reduced-order beam stresses.
- `computeSectionStressEnvelope.m`: evaluates the beam stress envelope over section points.
- `assembleGlobalMassMatrix.m`: assembles the global mass matrix for modal analysis.
- `solveModalEigenproblem.m`: solves the natural frequency problem.
- `plot_3d_beam_results.m`, `plot_propeller_native_viewer.m`, and `plot_propeller_with_me504_app.m`: visualization only.

The simplified beam view shows the reduced-order structural model. Ribbon view is for visualization and stress envelope display.
