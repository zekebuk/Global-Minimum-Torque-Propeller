# Mechanical checks: RL2407

| Check | Value | Limit | Result |
| --- | --- | --- | --- |
| tube wall around bearing [mm] | 1.600 | >= 1.0 | OK |
| bearing span / shaft d | 2.235 | >= 1.5 | OK |
| bearings do not overlap [mm] | 4.941 | >= 1.0 | OK |
| end turns -> bell plate [mm] | 0.800 | >= 0.5 | OK |
| end turns -> flange [mm] | 0.500 | >= 0.3 | OK |
| skirt -> flange gap [mm] | 0.600 | >= 0.4 | OK |
| end turns -> bell relief radial [mm] | 1.000 | >= 0.5 | OK |
| magnet -> stator airgap [mm] | 0.350 | >= 0.25 | OK |
| mount threads -> stator floor [mm] | 0.900 | >= 0.8 | OK |
| mount holes inside flange [mm] | 1.350 | >= 1.0 | OK |
| mount holes clear the tube [mm] | 3.714 | >= 0.5 | OK |
| vent holes between hub and relief [mm] | 1.500 | >= 0.5 | OK |
| web between window and M3 hole [mm] | 2.620 | >= 1.5 | OK |
| window radial width (lead exit, 3 x 20AWG) [mm] | 5.164 | >= 3.5 | OK |
| e-clip fits shaft | 4.000 | DIN 6799 3.2 range | OK |

## Axial stack-up (z from base bottom face, mm)

| Datum | z |
| --- | --- |
| flange_top | 3.500 |
| stator_bottom | 5.241 |
| stator_top | 12.741 |
| tube_top | 12.941 |
| endturn_top | 13.981 |
| mag_bottom | 4.741 |
| mag_top | 13.241 |
| skirt_bottom | 4.100 |
| ring_bottom | 4.100 |
| ring_top | 13.641 |
| plate_bottom | 14.781 |
| plate_top | 16.381 |
| hub_top | 18.881 |
| shoulder | 12.941 |
| lower_bearing_top | 4.000 |
| upper_bearing_bottom | 8.941 |
| clip_groove | -0.420 |
| shaft_bottom | -1.440 |
| shaft_top | 27.881 |
| grub | 17.631 |

## Mission performance at true mass

| Mass basis | Motor g | AUW g | Hover g/W | T/W | Burst winding C | Score | Feasible |
| --- | --- | --- | --- | --- | --- | --- | --- |
| analytical model mass | 41.1 | 642 | 5.79 | 8.52 | 123 | +1.740 | True |
| CAD mass | 47.5 | 667 | 5.69 | 8.19 | 123 | +1.718 | True |
| CAD mass + leads | 50.7 | 680 | 5.65 | 8.04 | 123 | +1.708 | True |
