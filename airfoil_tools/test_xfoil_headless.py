from __future__ import annotations

import argparse
import subprocess
from pathlib import Path

try:
    from .generate_xfoil_polars import DEFAULT_XFOIL_ROOT, find_xfoil_executable
except ImportError:
    from generate_xfoil_polars import DEFAULT_XFOIL_ROOT, find_xfoil_executable


def run_xfoil_quit_smoke_test(xfoil_exe: Path, timeout_s: float = 30.0) -> int:
    try:
        completed = subprocess.run(
            [str(xfoil_exe)],
            input="QUIT\n",
            text=True,
            capture_output=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        print("XFOIL headless test timed out.")
        return 2
    except OSError as exc:
        print(f"XFOIL headless test failed to start: {exc}")
        return 3

    print(f"XFOIL executable: {xfoil_exe}")
    print(f"Return code: {completed.returncode}")
    print(f"stdout bytes: {len(completed.stdout.encode('utf-8', errors='ignore'))}")
    print(f"stderr bytes: {len(completed.stderr.encode('utf-8', errors='ignore'))}")
    if completed.returncode == 0:
        print("XFOIL headless execution succeeded.")
        return 0
    print("XFOIL headless execution completed with nonzero return code.")
    return 1


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Minimal headless XFOIL QUIT smoke test.")
    parser.add_argument("--xfoil", type=Path, default=None)
    parser.add_argument("--xfoil-root", type=Path, default=DEFAULT_XFOIL_ROOT)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    xfoil_exe = find_xfoil_executable(args.xfoil, xfoil_root=args.xfoil_root)
    if xfoil_exe is None:
        print("XFOIL executable not found. Cannot generate thesis-quality polar data.")
        return 1
    return run_xfoil_quit_smoke_test(xfoil_exe, args.timeout_s)


if __name__ == "__main__":
    raise SystemExit(main())
