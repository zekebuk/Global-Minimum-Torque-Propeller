"""Compatibility wrapper for the headless MATLAB FEA workflow report."""

from __future__ import annotations

from run_matlab_fea import main

if __name__ == "__main__":
    raise SystemExit(main())
