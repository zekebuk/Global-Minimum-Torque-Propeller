from __future__ import annotations

import argparse
from pathlib import Path


REQUIRED_AIRFOILS = ("MH115", "S4310", "A3", "A4", "A6", "A7", "A11", "A13")


def parse_yz_line(line: str, source: Path, line_number: int) -> tuple[float, float] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    parts = stripped.replace(",", " ").split()
    if len(parts) < 3:
        return None
    try:
        y_over_c = float(parts[1])
        x_over_c = float(parts[2])
    except ValueError as exc:
        raise ValueError(f"{source}:{line_number} is not numeric 3-column YZ data: {line!r}") from exc
    return x_over_c, y_over_c


def convert_yz_to_xfoil_dat(source_path: Path, output_dir: Path, airfoil_name: str | None = None) -> Path:
    name = airfoil_name or source_path.stem.replace("_YZ", "").replace(".zy", "")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{name}.dat"

    points: list[tuple[float, float]] = []
    for line_number, line in enumerate(source_path.read_text(encoding="utf-8").splitlines(), start=1):
        parsed = parse_yz_line(line, source_path, line_number)
        if parsed is not None:
            points.append(parsed)

    if len(points) < 3:
        raise ValueError(f"{source_path} did not contain enough coordinate points")
    points = remove_consecutive_duplicate_points(points)

    lines = [name]
    lines.extend(f"{x:.8f} {y:.8f}" for x, y in points)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path


def remove_consecutive_duplicate_points(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    cleaned: list[tuple[float, float]] = []
    for point in points:
        if cleaned and abs(cleaned[-1][0] - point[0]) <= 1.0e-12 and abs(cleaned[-1][1] - point[1]) <= 1.0e-12:
            continue
        cleaned.append(point)
    return cleaned


def convert_required_airfoils(airfoil_dir: str | Path, output_dir: str | Path | None = None) -> list[Path]:
    root = Path(airfoil_dir)
    out_dir = Path(output_dir) if output_dir is not None else root / "xfoil_dat"
    written: list[Path] = []
    missing: list[str] = []
    for airfoil_name in REQUIRED_AIRFOILS:
        source = root / f"{airfoil_name}_YZ.txt"
        if not source.exists():
            missing.append(airfoil_name)
            continue
        written.append(convert_yz_to_xfoil_dat(source, out_dir, airfoil_name))

    if missing:
        raise FileNotFoundError(f"missing required YZ coordinate files: {', '.join(missing)}")
    return written


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert 3-column YZ coordinate files to XFOIL .dat files.")
    parser.add_argument("--airfoil-dir", type=Path, default=Path("CAD/airfoils_good"))
    parser.add_argument("--output-dir", type=Path, default=None)
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    written = convert_required_airfoils(args.airfoil_dir, args.output_dir)
    for path in written:
        print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
