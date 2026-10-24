"""Run or report the optional headless MATLAB FEA screening workflow."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from prandtl_prop import config
from prandtl_prop.external import matlab_fea


def parse_args() -> argparse.Namespace:
    """Parse command-line options for the optional MATLAB FEA workflow."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute", action="store_true", help="Actually run MATLAB. Without this flag, only a report is written.")
    parser.add_argument("--matlab", default=None, help="Path or command name for the MATLAB executable.")
    parser.add_argument("--fea-dir", type=Path, default=None, help="Directory containing main_propeller_3d_beam_fea.m.")
    parser.add_argument("--timeout-s", type=int, default=1800, help="Execution timeout in seconds.")
    return parser.parse_args()


def main() -> int:
    """Write the MATLAB FEA status report, optionally executing MATLAB."""

    args = parse_args()
    result = matlab_fea.run_matlab_fea(
        execute=args.execute,
        matlab_executable=args.matlab,
        fea_directory=args.fea_dir,
        timeout_s=args.timeout_s,
    )
    print("MATLAB FEA workflow complete")
    print(f"  matlab executable: {result.matlab_executable or 'not found'}")
    print(f"  FEA directory: {config.relative(result.fea_directory) if result.fea_directory else 'not found'}")
    print(f"  return code: {result.returncode if result.returncode is not None else 'not run'}")
    print(f"  {config.relative(result.report_path)}")
    if result.error:
        print(f"  error: {result.error}", file=sys.stderr)
        return 2
    return 0 if result.returncode in (None, 0) else result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
