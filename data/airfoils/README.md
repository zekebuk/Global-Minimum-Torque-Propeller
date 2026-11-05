# Airfoil Coordinates

The pipeline's sections and cad stages read airfoil coordinates from this folder. These files are
not committed yet. Copy them from the working thesis repository's `CAD/airfoils_good/`.

Accepted formats (names match case-insensitively, ignoring spaces, `_` and `-`):

- `<AIRFOIL>.dat`: Selig format, a title line then `x y` pairs (for example `CAD/airfoils_good/xfoil_dat/MH115.dat`).
- `<AIRFOIL>_YZ.txt`: the thesis CAD export, three columns `index  y/c  x/c`.

Airfoils required by the locked geometry: MH115, S4310, A3, A4, A6, A7, A11, A13.

A missing airfoil is an error. To use a stand-in deliberately (for example while iterating on
structure), name it in the design file and every use is reported as a warning:

```yaml
airfoils:
  substitutes: {A3: NACA4410}   # or {"*": NACA4410} for all missing airfoils
```
