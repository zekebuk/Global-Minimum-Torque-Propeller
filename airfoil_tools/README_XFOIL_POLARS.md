# XFOIL Polar Generation

## Inputs

Airfoil coordinate files live in:

```text
CAD\airfoils_good\
```

The source `*_YZ.txt` files are 3-column coordinate files:

```text
column 1: ignored
column 2: y/c
column 3: x/c
```

Convert them to XFOIL `.dat` files with:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' airfoil_tools\convert_yz_to_xfoil_dat.py --airfoil-dir CAD\airfoils_good
```

Converted files are written to:

```text
CAD\airfoils_good\xfoil_dat\
```

## XFOIL Executable

`generate_xfoil_polars.py` searches for XFOIL in this order:

1. `--xfoil path\to\xfoil.exe`
2. `XFOIL_EXE` environment variable
3. recursive search under `XFOIL6.99\`
4. `xfoil` or `xfoil.exe` on `PATH`
5. common local folders:
   - `external_tools\xfoil\xfoil.exe`
   - `tools\xfoil\xfoil.exe`
   - `CAD\airfoils_good\xfoil.exe`

Example:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' airfoil_tools\generate_xfoil_polars.py `
  --airfoil-dir "C:\Users\Zekeb\OneDrive\OneDrive - Cal Poly\ME 599 THESIS\CAD\airfoils_good" `
  --xfoil "C:\path\to\xfoil.exe"
```

Smoke-test headless XFOIL execution:

```powershell
& 'C:\Users\Zekeb\anaconda3\python.exe' airfoil_tools\test_xfoil_headless.py
```

## Generated Outputs

Raw XFOIL polar files:

```text
CAD\airfoils_good\generated_polars\raw\AIRFOIL_ReXXXXX_xfoil.txt
```

Parsed CSV files:

```text
CAD\airfoils_good\generated_polars\AIRFOIL_ReXXXXX_polar.csv
```

Per-case logs:

```text
CAD\airfoils_good\generated_polars\logs\AIRFOIL_ReXXXXX_xfoil.log
```

CSV columns:

```text
airfoil, Re, alpha_deg, Cl, Cd, Cm, converged
```

The generator uses:

- `Re = 100000, 150000, 200000, 300000`
- alpha sweep `-8 deg` to `+16 deg`
- alpha step `0.5 deg`
- timeout `60 s` per airfoil/Re case by default

If the continuous `ASEQ` sweep hangs after XFOIL has started, the generator can retry using bounded single-alpha XFOIL calls. This still writes only real parsed XFOIL rows; failed alpha points are ignored and noted in the per-case log. Disable this with:

```powershell
--no-segmented-fallback
```

If a converted root `*_YZ.txt` airfoil fails, the generator can retry an alternate coordinate file under the same airfoil directory, such as `CAD\airfoils_good\airfoils_bad\AIRFOIL.txt`. This is logged explicitly and can be disabled with:

```powershell
--no-alternate-coordinates
```

For the current thesis polar set, A3 uses `CAD\airfoils_good\airfoils_bad\A3.txt` because the root A3 YZ conversion is a stepped coordinate loop that sends XFOIL viscous transition checks to NaN.

## Common Failure Modes

- XFOIL executable missing:
  `XFOIL executable not found. Cannot generate thesis-quality polar data.`
- XFOIL hangs:
  the process is killed after the configured timeout. If segmented fallback is enabled, the generator retries bounded single-alpha calls; otherwise the case is marked failed.
- Too few alpha points converge:
  fewer than 10 converged points marks the case failed.
- Bad coordinate loop:
  inspect the corresponding `.dat`, raw output, and log files.

## Thesis Validity

`placeholder_debug_polar` is not thesis-valid. The aero solver only permits it with `--debug-mode`, and debug outputs use a `_debug_placeholder` case suffix. Final thesis aero results must use existing or generated polar CSV files.
