from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from output_paths import PROJECT_ROOT


GENERATED_OUTPUT_DIRS = (
    PROJECT_ROOT / "results" / "geometry_optimizer",
    PROJECT_ROOT / "results" / "aero_postprocess",
    PROJECT_ROOT / "results" / "verification",
    PROJECT_ROOT / "results" / "fea" / "beam_solver",
    PROJECT_ROOT / "figures_paper" / "geometry_optimizer",
    PROJECT_ROOT / "figures_paper" / "aero_postprocess",
    PROJECT_ROOT / "figures_paper" / "verification",
    PROJECT_ROOT / "figures_paper" / "fea",
)


def _is_safe_target(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    allowed_roots = [
        (PROJECT_ROOT / "results").resolve(strict=False),
        (PROJECT_ROOT / "figures_paper").resolve(strict=False),
    ]
    return any(_is_relative_to(resolved, root) and resolved != root for root in allowed_roots)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def cleanup_outputs(*, confirm: bool = False) -> list[Path]:
    targets = [path for path in GENERATED_OUTPUT_DIRS if path.exists()]
    for target in targets:
        if confirm and not _is_safe_target(target):
            raise RuntimeError(f"refusing to delete unsafe target: {target}")
        print(("Deleting: " if confirm else "Would delete: ") + str(target))
        if confirm:
            shutil.rmtree(target)
    if not targets:
        print("No generated output folders found.")
    return targets


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Remove generated thesis output folders.")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="List generated folders without deleting them.")
    mode.add_argument("--confirm", action="store_true", help="Actually delete generated folders.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cleanup_outputs(confirm=bool(args.confirm))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
