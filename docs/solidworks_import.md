# Importing Pipeline Geometry into SolidWorks

Each `cad` run (`runs/<design>/cad/<key>/`) writes:

| File | Units | Use |
| --- | --- | --- |
| `blade.step`, `rotor.step` | mm (true size) | File > Open as a solid part. This is the quickest route. |
| `solidworks_curves/section_NN_r####.##mm.sldcrv` | mm | One closed airfoil section per BEMT station, for a native SolidWorks loft |
| `solidworks_curves/guide_leading_edge.sldcrv`, `guide_trailing_edge.sldcrv` | mm | Guide curves for the loft |
| `blade_m.stl`, `rotor_m.stl`, `blade_m.brep` | m | Meshing (OpenFOAM, gmsh/CalculiX). Choose metres if you open an STL in SolidWorks. |

Earlier STEP files from this pipeline (before commit `fdcf820`) were labelled mm while the coordinates
were in metres, so they imported 1000x too small. Re-run `prandtl run <design> --stages cad` to
regenerate them.

## Frame

The frames match: +Z is the rotation axis and thrust direction, +X runs along the blade span, and
the blade moves toward +Y (the rotor spins about +Z). The blade root sits at r = 0.2R (55.88 mm)
and the tip at 279.3 mm. There is no hub unless `geometry.hub_radius_m` is set. Add your own
hub/root transition in SolidWorks, or set a hub radius so the root section is extended inward.

## Native loft from curves

1. Insert > Curve > Curve Through XYZ Points > Browse, and pick `section_00_...sldcrv`. Repeat for
   every section file in order. A SolidWorks macro can batch this if you do it often.
2. Optionally import `guide_leading_edge.sldcrv` and `guide_trailing_edge.sldcrv` the same way.
3. Insert > Boss/Base > Loft. Select the section curves root to tip, and add the guide curves.
4. Close the tip: the last section is at r/R = 0.9997 and leaves a small flat face. Fillet or cap
   it to suit manufacturing.

Each section file repeats its first point at the end, so the curve is closed. The section curves
pass through exactly the points the BEMT, CAD, FEA, and CFD stages use, so a SolidWorks part built
this way matches the analysed geometry.
