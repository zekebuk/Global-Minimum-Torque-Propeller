from __future__ import annotations

import argparse
import csv
import math
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

try:
    from .convert_yz_to_xfoil_dat import REQUIRED_AIRFOILS, convert_required_airfoils
except ImportError:
    from convert_yz_to_xfoil_dat import REQUIRED_AIRFOILS, convert_required_airfoils


REYNOLDS_NUMBERS = (100000, 150000, 200000, 300000)
ALPHA_START_DEG = -8.0
ALPHA_END_DEG = 16.0
ALPHA_STEP_DEG = 0.5
MIN_CONVERGED_ALPHA_POINTS = 10
CODE_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = CODE_DIR.parent if CODE_DIR.name.lower() == "code" else CODE_DIR
DEFAULT_XFOIL_ROOT = PROJECT_ROOT / "XFOIL6.99"


@dataclass(frozen=True)
class XfoilCaseResult:
    airfoil: str
    reynolds_number: int
    csv_path: Path | None
    raw_path: Path
    log_path: Path
    converged_points: int
    success: bool
    failure_reason: str = ""
    coordinate_source: str = ""
    recovery_used: str = ""


def find_xfoil_executable(
    explicit_path: str | Path | None = None,
    airfoil_dir: str | Path | None = None,
    workspace_root: str | Path | None = None,
    xfoil_root: str | Path | None = DEFAULT_XFOIL_ROOT,
) -> Path | None:
    candidates: list[Path] = []
    if explicit_path is not None:
        candidates.append(Path(explicit_path))

    env_path = os.environ.get("XFOIL_EXE")
    if env_path:
        candidates.append(Path(env_path))

    recursive_roots: list[Path] = []
    if xfoil_root is not None:
        recursive_roots.append(Path(xfoil_root))
    for root in _unique_paths(recursive_roots):
        if root.exists():
            for executable_name in ("xfoil.exe", "XFOIL.exe", "xfoil", "XFOIL"):
                candidates.extend(path for path in root.rglob(executable_name) if path.is_file())

    for executable_name in ("xfoil.exe", "xfoil", "XFOIL.exe", "XFOIL"):
        found = shutil.which(executable_name)
        if found:
            candidates.append(Path(found))

    roots: list[Path] = []
    if workspace_root is not None:
        roots.append(Path(workspace_root))
    roots.append(Path.cwd())
    if airfoil_dir is not None:
        airfoil_path = Path(airfoil_dir)
        roots.append(airfoil_path)
        roots.extend(airfoil_path.parents)

    for root in _unique_paths(roots):
        candidates.extend(
            [
                root / "external_tools" / "xfoil" / "xfoil.exe",
                root / "tools" / "xfoil" / "xfoil.exe",
                root / "CAD" / "airfoils_good" / "xfoil.exe",
            ]
        )

    for candidate in _unique_paths(candidates):
        if candidate.is_file():
            return candidate.resolve()
    return None


def build_xfoil_command_script(dat_path: Path, raw_polar_path: Path, reynolds_number: int) -> str:
    lines = [
        f"LOAD {dat_path}",
        "PANE",
        "OPER",
        f"VISC {reynolds_number}",
        "MACH 0",
        "ITER 200",
        "PACC",
        str(raw_polar_path),
        "",
        f"ASEQ {ALPHA_START_DEG:g} {ALPHA_END_DEG:g} {ALPHA_STEP_DEG:g}",
        "PACC",
        "",
        "QUIT",
        "",
    ]
    return "\n".join(lines)


def build_xfoil_single_alpha_command_script(
    dat_path: Path,
    raw_polar_path: Path,
    reynolds_number: int,
    alpha_deg: float,
) -> str:
    lines = [
        f"LOAD {dat_path}",
        "PANE",
        "OPER",
        f"VISC {reynolds_number}",
        "MACH 0",
        "ITER 200",
        "PACC",
        str(raw_polar_path),
        "",
        f"ALFA {alpha_deg:g}",
        "PACC",
        "",
        "QUIT",
        "",
    ]
    return "\n".join(lines)


def expected_alpha_values(
    alpha_min: float = ALPHA_START_DEG,
    alpha_max: float = ALPHA_END_DEG,
    alpha_step: float = ALPHA_STEP_DEG,
) -> list[float]:
    values: list[float] = []
    count = int(round((alpha_max - alpha_min) / alpha_step))
    for index in range(count + 1):
        values.append(round(alpha_min + index * alpha_step, 6))
    return values


def parse_xfoil_polar(raw_polar_path: Path) -> dict[float, dict[str, float]]:
    parsed: dict[float, dict[str, float]] = {}
    if not raw_polar_path.exists():
        return parsed
    for line in raw_polar_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        parts = line.strip().split()
        if len(parts) < 3:
            continue
        try:
            alpha = float(parts[0])
            cl = float(parts[1])
            cd = float(parts[2])
        except ValueError:
            continue
        cm = math.nan
        if len(parts) >= 5:
            try:
                cm = float(parts[4])
            except ValueError:
                cm = math.nan
        parsed[round(alpha, 6)] = {"Cl": cl, "Cd": cd, "Cm": cm}
    return parsed


def rows_for_csv(airfoil_name: str, reynolds_number: int, parsed_rows: dict[float, dict[str, float]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for alpha in sorted(parsed_rows):
        parsed = parsed_rows[alpha]
        rows.append(
            {
                "airfoil": airfoil_name,
                "Re": reynolds_number,
                "alpha_deg": alpha,
                "Cl": parsed["Cl"],
                "Cd": parsed["Cd"],
                "Cm": parsed["Cm"],
                "converged": True,
            }
        )
    return rows


def write_polar_csv(csv_path: Path, rows: Sequence[dict[str, object]]) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["airfoil", "Re", "alpha_deg", "Cl", "Cd", "Cm", "converged"])
        writer.writeheader()
        writer.writerows(rows)


def write_log(
    log_path: Path,
    command_script: str,
    stdout: str,
    stderr: str,
    returncode: int | None,
    failure_reason: str,
    notes: Sequence[str] = (),
) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                "XFOIL command script:",
                command_script,
                "",
                f"Return code: {returncode}",
                f"Failure reason: {failure_reason}",
                *[f"Note: {note}" for note in notes],
                "",
                "STDOUT:",
                stdout,
                "",
                "STDERR:",
                stderr,
                "",
            ]
        ),
        encoding="utf-8",
    )


def _run_xfoil_script(
    xfoil_executable: Path,
    command_script: str,
    work_dir: Path,
    timeout_s: float,
) -> tuple[str, str, int | None, str]:
    stdout = ""
    stderr = ""
    returncode: int | None = None
    failure_reason = ""
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            [str(xfoil_executable)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=work_dir,
        )
        stdout, stderr = process.communicate(command_script, timeout=timeout_s)
        returncode = process.returncode
    except subprocess.TimeoutExpired:
        if process is not None:
            process.kill()
            stdout, stderr = process.communicate()
            returncode = process.returncode
        failure_reason = f"XFOIL timed out after {timeout_s:g} seconds"
    except OSError as exc:
        failure_reason = f"failed to start XFOIL: {exc}"
    return stdout, stderr, returncode, failure_reason


def _alpha_file_token(alpha_deg: float) -> str:
    prefix = "p" if alpha_deg >= 0 else "m"
    return prefix + f"{abs(alpha_deg):05.2f}".replace(".", "p")


def _combine_raw_polar_files(raw_paths: Sequence[Path], combined_raw_path: Path) -> None:
    combined_raw_path.parent.mkdir(parents=True, exist_ok=True)
    sections: list[str] = []
    for raw_path in raw_paths:
        if raw_path.exists():
            sections.append(raw_path.read_text(encoding="utf-8", errors="ignore"))
    combined_raw_path.write_text("\n\n".join(sections), encoding="utf-8")


def _run_segmented_alpha_fallback(
    xfoil_executable: Path,
    work_dat_path: Path,
    raw_path: Path,
    work_dir: Path,
    airfoil_name: str,
    reynolds_number: int,
    timeout_s: float,
) -> tuple[dict[float, dict[str, float]], str, str, list[str]]:
    parsed: dict[float, dict[str, float]] = {}
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    notes: list[str] = ["segmented single-alpha XFOIL recovery was used"]
    segment_raw_paths: list[Path] = []
    consecutive_failures = 0

    segmented_dir = work_dir / "segmented"
    segmented_dir.mkdir(parents=True, exist_ok=True)
    for alpha in expected_alpha_values():
        alpha_token = _alpha_file_token(alpha)
        alpha_raw_path = segmented_dir / f"{airfoil_name}_Re{reynolds_number}_{alpha_token}.txt"
        if alpha_raw_path.exists():
            alpha_raw_path.unlink()
        command_script = build_xfoil_single_alpha_command_script(
            Path(work_dat_path.name),
            Path("segmented") / alpha_raw_path.name,
            reynolds_number,
            alpha,
        )
        stdout, stderr, returncode, failure_reason = _run_xfoil_script(
            xfoil_executable,
            command_script,
            work_dir,
            timeout_s,
        )
        stdout_parts.append(f"--- alpha {alpha:g}, returncode={returncode}, failure={failure_reason} ---\n{stdout}")
        stderr_parts.append(f"--- alpha {alpha:g} ---\n{stderr}")
        alpha_rows = parse_xfoil_polar(alpha_raw_path)
        if failure_reason or not alpha_rows:
            consecutive_failures += 1
            notes.append(f"alpha {alpha:g} did not converge")
        else:
            parsed.update(alpha_rows)
            segment_raw_paths.append(alpha_raw_path)
            consecutive_failures = 0

        if consecutive_failures >= 6 and not parsed:
            notes.append("aborted segmented recovery after six initial alpha failures")
            break
        if consecutive_failures >= 6 and len(parsed) >= MIN_CONVERGED_ALPHA_POINTS:
            notes.append("stopped segmented recovery after six consecutive high-alpha failures")
            break

    _combine_raw_polar_files(segment_raw_paths, raw_path)
    return parsed, "\n".join(stdout_parts), "\n".join(stderr_parts), notes


def run_xfoil_case(
    xfoil_executable: Path,
    dat_path: Path,
    output_dir: Path,
    airfoil_name: str,
    reynolds_number: int,
    timeout_s: float = 60.0,
    alpha_timeout_s: float = 10.0,
    allow_segmented_fallback: bool = True,
    coordinate_source_note: str = "",
) -> XfoilCaseResult:
    raw_dir = output_dir / "raw"
    log_dir = output_dir / "logs"
    work_dir = output_dir / "work" / f"{airfoil_name}_Re{reynolds_number}"
    raw_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{airfoil_name}_Re{reynolds_number}_xfoil.txt"
    raw_work_path = work_dir / raw_path.name
    csv_path = output_dir / f"{airfoil_name}_Re{reynolds_number}_polar.csv"
    log_path = log_dir / f"{airfoil_name}_Re{reynolds_number}_xfoil.log"
    work_dat_path = work_dir / dat_path.name
    shutil.copy2(dat_path, work_dat_path)
    for stale_path in (raw_work_path, raw_path, csv_path):
        if stale_path.exists():
            stale_path.unlink()
    command_script = build_xfoil_command_script(Path(work_dat_path.name), Path(raw_work_path.name), reynolds_number)

    stdout, stderr, returncode, failure_reason = _run_xfoil_script(
        xfoil_executable,
        command_script,
        work_dir,
        timeout_s,
    )

    if raw_work_path.exists():
        shutil.copy2(raw_work_path, raw_path)

    parsed = parse_xfoil_polar(raw_path)
    rows = rows_for_csv(airfoil_name, reynolds_number, parsed)
    converged_points = sum(1 for row in rows if row["converged"])

    if not failure_reason and returncode not in (0, None):
        failure_reason = f"XFOIL exited with return code {returncode}"
    if not failure_reason and converged_points < MIN_CONVERGED_ALPHA_POINTS:
        failure_reason = (
            f"only {converged_points} alpha points converged; "
            f"minimum required is {MIN_CONVERGED_ALPHA_POINTS}"
        )

    recovery_used = ""
    notes: list[str] = []
    if coordinate_source_note:
        notes.append(coordinate_source_note)
    if failure_reason and allow_segmented_fallback:
        fallback_parsed, fallback_stdout, fallback_stderr, fallback_notes = _run_segmented_alpha_fallback(
            xfoil_executable,
            work_dat_path,
            raw_path,
            work_dir,
            airfoil_name,
            reynolds_number,
            alpha_timeout_s,
        )
        fallback_rows = rows_for_csv(airfoil_name, reynolds_number, fallback_parsed)
        fallback_converged_points = sum(1 for row in fallback_rows if row["converged"])
        if fallback_converged_points >= MIN_CONVERGED_ALPHA_POINTS:
            parsed = fallback_parsed
            rows = fallback_rows
            converged_points = fallback_converged_points
            stdout = "\n".join([stdout, "", "SEGMENTED FALLBACK STDOUT:", fallback_stdout])
            stderr = "\n".join([stderr, "", "SEGMENTED FALLBACK STDERR:", fallback_stderr])
            notes.extend(fallback_notes)
            notes.append(f"original ASEQ failure: {failure_reason}")
            failure_reason = ""
            recovery_used = "segmented_single_alpha"
        else:
            notes.extend(fallback_notes)
            notes.append(
                f"segmented recovery produced {fallback_converged_points} alpha points; "
                f"minimum required is {MIN_CONVERGED_ALPHA_POINTS}"
            )

    write_log(log_path, command_script, stdout, stderr, returncode, failure_reason, notes)

    if failure_reason:
        print(f"WARNING: {airfoil_name} Re={reynolds_number} failed: {failure_reason}")
        return XfoilCaseResult(
            airfoil=airfoil_name,
            reynolds_number=reynolds_number,
            csv_path=None,
            raw_path=raw_path,
            log_path=log_path,
            converged_points=converged_points,
            success=False,
            failure_reason=failure_reason,
            coordinate_source=str(dat_path),
            recovery_used=recovery_used,
        )

    write_polar_csv(csv_path, rows)
    return XfoilCaseResult(
        airfoil=airfoil_name,
        reynolds_number=reynolds_number,
        csv_path=csv_path,
        raw_path=raw_path,
        log_path=log_path,
        converged_points=converged_points,
        success=True,
        coordinate_source=str(dat_path),
        recovery_used=recovery_used,
    )


def find_alternate_coordinate_dat(airfoil_dir: Path, primary_dat_path: Path, airfoil_name: str) -> Path | None:
    candidates = [
        airfoil_dir / "airfoils_bad" / f"{airfoil_name}.txt",
        airfoil_dir / "Good xy" / f"{airfoil_name}.txt",
    ]
    primary_resolved = primary_dat_path.resolve() if primary_dat_path.exists() else primary_dat_path
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            if candidate.resolve() == primary_resolved:
                continue
        except OSError:
            pass
        return candidate
    return None


def generate_xfoil_polars(
    airfoil_dir: str | Path,
    xfoil_executable: str | Path | None = None,
    output_dir: str | Path | None = None,
    timeout_s: float = 60.0,
    alpha_timeout_s: float = 10.0,
    allow_segmented_fallback: bool = True,
    allow_alternate_coordinates: bool = True,
    xfoil_root: str | Path | None = DEFAULT_XFOIL_ROOT,
) -> list[XfoilCaseResult]:
    root = Path(airfoil_dir)
    dat_dir = root / "xfoil_dat"
    polar_dir = Path(output_dir) if output_dir is not None else root / "generated_polars"
    convert_required_airfoils(root, dat_dir)
    polar_dir.mkdir(parents=True, exist_ok=True)

    xfoil_path = find_xfoil_executable(xfoil_executable, airfoil_dir=root, xfoil_root=xfoil_root)
    if xfoil_path is None:
        print("XFOIL executable not found. Cannot generate thesis-quality polar data.")
        return []

    results: list[XfoilCaseResult] = []
    missing_airfoils: list[str] = []
    for airfoil_name in REQUIRED_AIRFOILS:
        dat_path = dat_dir / f"{airfoil_name}.dat"
        if not dat_path.is_file():
            missing_airfoils.append(airfoil_name)
            continue
        for reynolds_number in REYNOLDS_NUMBERS:
            result = run_xfoil_case(
                xfoil_path,
                dat_path,
                polar_dir,
                airfoil_name,
                reynolds_number,
                timeout_s=timeout_s,
                alpha_timeout_s=alpha_timeout_s,
                allow_segmented_fallback=False,
            )
            retried_with_alternate = False
            if not result.success and allow_alternate_coordinates:
                alternate_dat = find_alternate_coordinate_dat(root, dat_path, airfoil_name)
                if alternate_dat is not None:
                    print(
                        f"WARNING: {airfoil_name} Re={reynolds_number} primary coordinate source failed; "
                        f"retrying XFOIL with alternate coordinate file {alternate_dat}"
                    )
                    retried_with_alternate = True
                    result = run_xfoil_case(
                        xfoil_path,
                        alternate_dat,
                        polar_dir,
                        airfoil_name,
                        reynolds_number,
                        timeout_s=timeout_s,
                        alpha_timeout_s=alpha_timeout_s,
                        allow_segmented_fallback=allow_segmented_fallback,
                        coordinate_source_note=(
                            f"alternate coordinate source used after primary failure: {alternate_dat}"
                        ),
                    )
            if not result.success and allow_segmented_fallback and not retried_with_alternate:
                result = run_xfoil_case(
                    xfoil_path,
                    dat_path,
                    polar_dir,
                    airfoil_name,
                    reynolds_number,
                    timeout_s=timeout_s,
                    alpha_timeout_s=alpha_timeout_s,
                    allow_segmented_fallback=True,
                )
            results.append(result)

    print_generation_summary(results, missing_airfoils)
    return results


def print_generation_summary(results: Sequence[XfoilCaseResult], missing_airfoils: Sequence[str]) -> None:
    attempted = len(results)
    successful = sum(1 for result in results if result.success)
    failed = [result for result in results if not result.success]
    print("")
    print("XFOIL polar generation summary")
    print(f"  total cases attempted      : {attempted}")
    print(f"  successful polar files     : {successful}")
    print(f"  failed cases               : {len(failed)}")
    print(f"  missing airfoils           : {', '.join(missing_airfoils) if missing_airfoils else 'none'}")
    recovered = [result for result in results if result.success and result.recovery_used]
    alternate_sources = [
        result
        for result in results
        if result.success and result.coordinate_source and "airfoils_bad" in result.coordinate_source
    ]
    print(f"  recovered by fallback      : {len(recovered)}")
    print(f"  alternate coordinate cases : {len(alternate_sources)}")
    if failed:
        print("  failed airfoil/Re values   :")
        for result in failed:
            print(f"    {result.airfoil} Re={result.reynolds_number}: {result.failure_reason}")


def _unique_paths(paths: Sequence[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path)
        if key not in seen:
            unique.append(path)
            seen.add(key)
    return unique


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Generate XFOIL polar CSV files for required thesis airfoils.")
    parser.add_argument("--airfoil-dir", type=Path, default=Path("CAD/airfoils_good"))
    parser.add_argument("--xfoil", type=Path, default=None)
    parser.add_argument("--xfoil-root", type=Path, default=DEFAULT_XFOIL_ROOT)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--timeout-s", type=float, default=60.0)
    parser.add_argument("--alpha-timeout-s", type=float, default=10.0)
    parser.add_argument("--no-segmented-fallback", action="store_true")
    parser.add_argument("--no-alternate-coordinates", action="store_true")
    return parser


def main() -> int:
    args = build_arg_parser().parse_args()
    results = generate_xfoil_polars(
        args.airfoil_dir,
        args.xfoil,
        args.output_dir,
        timeout_s=args.timeout_s,
        alpha_timeout_s=args.alpha_timeout_s,
        allow_segmented_fallback=not args.no_segmented_fallback,
        allow_alternate_coordinates=not args.no_alternate_coordinates,
        xfoil_root=args.xfoil_root,
    )
    successful_paths = [result.csv_path for result in results if result.success and result.csv_path is not None]
    for path in successful_paths:
        print(f"Wrote {path}")
    if not results:
        return 1
    return 0 if len(successful_paths) == len(results) else 2


if __name__ == "__main__":
    raise SystemExit(main())
