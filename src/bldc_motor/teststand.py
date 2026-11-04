"""Thrust-stand log import, unit normalization, derived metrics and steady-state extraction.

Canonical columns (SI): ``time_s, throttle (0..1), esc_signal_us, voltage_v, current_a, rpm,
thrust_n, torque_nm, temp_c`` (winding / motor) and ``magnet_temp_c`` (bell / rotor). Headers of the form ``Name (unit)`` or ``Name [unit]`` are
recognised (e.g. Tyto/RCbenchmark-style ``Thrust (kgf)``, ``Torque (N·m)``,
``ESC signal (µs)``) and converted to SI. Anything unrecognised can be mapped explicitly with
``column_map={"canonical": "Exact header"}``.

Measurement location matters: stand voltage/current are normally measured on the ESC *input*
(DC bus), not the motor phases. Everything downstream treats them as bus quantities.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from bldc_motor import units

CANONICAL_QUANTITIES: dict[str, str] = {
    "time_s": "time_s",
    "throttle": "fraction",
    "esc_signal_us": "signal_us",
    "voltage_v": "voltage_v",
    "current_a": "current_a",
    "rpm": "speed_rpm",
    "thrust_n": "force_n",
    "torque_nm": "torque_nm",
    "temp_c": "temperature_c",
    "magnet_temp_c": "temperature_c",
}

# Normalized header names (lowercase alphanumerics) in priority order.
ALIASES: dict[str, tuple[str, ...]] = {
    "time_s": ("time", "times", "timestamp", "elapsedtime", "t"),
    "throttle": ("throttle", "throttlepct", "throttlepercent", "thr", "command", "duty", "dutycycle"),
    "esc_signal_us": ("escsignal", "escsignalus", "escpwm", "pwmsignal", "pwm", "esc"),
    "voltage_v": ("voltage", "busvoltage", "batteryvoltage", "vbat", "vin", "v"),
    "current_a": ("current", "buscurrent", "batterycurrent", "ibat", "i"),
    "rpm": (
        "motoropticalspeed",
        "opticalrpm",
        "motorelectricalspeed",
        "electricalrpm",
        "rpm",
        "motorrpm",
        "motorspeed",
        "speed",
    ),
    "thrust_n": ("thrust", "force", "loadcell", "thrustforce"),
    "torque_nm": ("torque", "reactiontorque"),
    "magnet_temp_c": (
        "magnettemperature",
        "magnettemp",
        "belltemperature",
        "belltemp",
        "rotortemp",
        "casetemp",
        "statortemp",
        "temp1",
    ),
    "temp_c": ("windingtemperature", "windingtemp", "motortemperature", "motortemp", "temperature", "temp", "temp0"),
}

_HEADER_UNIT = re.compile(r"^(?P<name>.*?)\s*[\(\[]\s*(?P<unit>[^\)\]]*)\s*[\)\]]\s*$")


def _normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def split_header(header: str) -> tuple[str, str]:
    match = _HEADER_UNIT.match(header.strip())
    if match:
        return match.group("name").strip(), match.group("unit").strip()
    return header.strip(), ""


@dataclass
class StandLog:
    """Columnar thrust-stand data in canonical SI units."""

    data: dict[str, np.ndarray]
    source_columns: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    def __getitem__(self, key: str) -> np.ndarray:
        return self.data[key]

    def __contains__(self, key: str) -> bool:
        return key in self.data

    def __len__(self) -> int:
        return len(next(iter(self.data.values()))) if self.data else 0

    def has(self, *keys: str) -> bool:
        return all(key in self.data and np.isfinite(self.data[key]).any() for key in keys)

    def select(self, mask: np.ndarray) -> StandLog:
        return StandLog(
            {k: v[mask] for k, v in self.data.items()},
            dict(self.source_columns),
            list(self.warnings),
            dict(self.metadata),
        )

    def to_csv(self, path: str | Path) -> Path:
        return write_columns_csv(self.data, path)


def write_columns_csv(columns: dict[str, np.ndarray], path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    keys = list(columns)
    length = len(columns[keys[0]]) if keys else 0
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(keys)
        for index in range(length):
            writer.writerow([_fmt(columns[key][index]) for key in keys])
    return target


def _fmt(value) -> str:
    if isinstance(value, (float, np.floating)):
        return "" if not math.isfinite(value) else f"{float(value):.10g}"
    return str(value)


def _to_float(text: str) -> float:
    text = text.strip()
    if not text:
        return math.nan
    try:
        return float(text)
    except ValueError:
        return math.nan


def match_columns(headers: list[str], column_map: dict[str, str] | None = None) -> dict[str, tuple[str, str]]:
    """Map canonical names -> (original header, unit)."""

    explicit = dict(column_map or {})
    matched: dict[str, tuple[str, str]] = {}
    parsed = [(header, *split_header(header)) for header in headers]
    for canonical, header in explicit.items():
        if header not in headers:
            raise KeyError(f"column_map refers to missing header '{header}'")
        matched[canonical] = (header, split_header(header)[1])
    used = {header for header, _ in matched.values()}
    # Canonical headers (e.g. files written by this toolkit) are SI by definition.
    for canonical in CANONICAL_QUANTITIES:
        if canonical not in matched and canonical in headers and canonical not in used:
            matched[canonical] = (canonical, "")
            used.add(canonical)
    for canonical, aliases in ALIASES.items():
        if canonical in matched:
            continue
        for alias in aliases:
            hit = next((p for p in parsed if p[0] not in used and _normalize_name(p[1]) == alias), None)
            if hit is not None:
                matched[canonical] = (hit[0], hit[2])
                used.add(hit[0])
                break
    return matched


def load_stand_csv(
    path: str | Path,
    column_map: dict[str, str] | None = None,
    rpm_is_electrical: bool = False,
    pole_pairs: int | None = None,
    esc_signal_range_us: tuple[float, float] = (1000.0, 2000.0),
    delimiter: str | None = None,
) -> StandLog:
    """Load a thrust-stand CSV into canonical SI columns.

    ``rpm_is_electrical``: set when the logged speed is electrical (eRPM, e.g. from bidirectional
    DShot telemetry); it is divided by ``pole_pairs``. Note that some stand software labels a
    pole-count-corrected *mechanical* speed as "electrical speed" -- check your setup.
    """

    text = Path(path).read_text(encoding="utf-8-sig")
    lines = [line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    if not lines:
        raise ValueError(f"No data in {path}")
    dialect_delimiter = delimiter or (csv.Sniffer().sniff(lines[0], delimiters=",;\t").delimiter)
    reader = csv.reader(lines, delimiter=dialect_delimiter)
    headers = [h.strip() for h in next(reader)]
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    raw = {
        header: np.array([_to_float(row[i]) if i < len(row) else math.nan for row in rows])
        for i, header in enumerate(headers)
    }

    matched = match_columns(headers, column_map)
    log = StandLog(data={}, source_columns={k: h for k, (h, _) in matched.items()}, metadata={"path": str(path)})
    for canonical, (header, unit) in matched.items():
        quantity = CANONICAL_QUANTITIES[canonical]
        values = raw[header]
        if (
            canonical == "throttle"
            and not unit
            and header != canonical
            and np.nanmax(np.abs(values), initial=0.0) > 1.5
        ):
            unit = "%"
            log.warnings.append(f"'{header}' has no unit and exceeds 1.5; interpreted as percent")
        if not unit and header != canonical and canonical not in ("throttle", "rpm"):
            log.warnings.append(f"'{header}' has no unit; assumed SI for {canonical}")
        log.data[canonical] = np.asarray(units.to_si(values, unit, quantity), dtype=float)

    if "throttle" not in log.data and "esc_signal_us" in log.data:
        low, high = esc_signal_range_us
        log.data["throttle"] = np.clip((log.data["esc_signal_us"] - low) / (high - low), 0.0, 1.0)
        log.warnings.append(f"throttle derived from ESC signal assuming linear {low:g}-{high:g} us range")
    if "rpm" in log.data and rpm_is_electrical:
        if not pole_pairs:
            raise ValueError("rpm_is_electrical requires pole_pairs")
        log.data["rpm"] = log.data["rpm"] / pole_pairs
    for key in ("thrust_n", "torque_nm"):
        if key in log.data and np.nanmedian(log.data[key]) < 0.0:
            log.data[key] = -log.data[key]
            log.warnings.append(f"{key} sign flipped (median was negative; load-cell orientation)")
    if "time_s" not in log.data:
        log.data["time_s"] = np.arange(len(rows), dtype=float)
        log.warnings.append("no time column; using sample index as time")
    return log


def add_derived_metrics(
    log: StandLog, diameter_m: float | None = None, rho: float = units.SEA_LEVEL_AIR_DENSITY_KG_M3
) -> StandLog:
    """Add electrical/mechanical power, drive efficiency, g/W and (with diameter) CT, CP, FM."""

    d = log.data
    with np.errstate(divide="ignore", invalid="ignore"):
        if log.has("voltage_v", "current_a"):
            d["p_elec_w"] = d["voltage_v"] * d["current_a"]
        if log.has("rpm", "torque_nm"):
            d["p_mech_w"] = d["torque_nm"] * d["rpm"] * units.TWO_PI / 60.0
        if "p_elec_w" in d and "p_mech_w" in d:
            d["drive_efficiency"] = np.where(d["p_elec_w"] > 1.0, d["p_mech_w"] / d["p_elec_w"], np.nan)
        if "p_elec_w" in d and log.has("thrust_n"):
            d["grams_per_watt"] = np.where(
                d["p_elec_w"] > 1.0, d["thrust_n"] * 1000.0 / units.STANDARD_GRAVITY_M_S2 / d["p_elec_w"], np.nan
            )
        if diameter_m and log.has("rpm"):
            n = d["rpm"] / 60.0
            valid = n > 1.0
            if log.has("thrust_n"):
                d["ct"] = np.where(valid, d["thrust_n"] / (rho * n**2 * diameter_m**4), np.nan)
            if log.has("torque_nm"):
                d["cp"] = np.where(valid, units.TWO_PI * d["torque_nm"] / (rho * n**2 * diameter_m**5), np.nan)
            if "ct" in d and "cp" in d:
                d["figure_of_merit"] = np.where(
                    (d["ct"] > 0) & (d["cp"] > 0), d["ct"] ** 1.5 / (d["cp"] * math.sqrt(math.pi / 2.0)), np.nan
                )
    return log


def steady_state_points(
    log: StandLog,
    command: str = "throttle",
    tolerance: float = 0.004,
    settle_s: float = 1.0,
    min_hold_s: float = 0.5,
) -> StandLog:
    """Average each constant-command plateau after discarding the first ``settle_s`` seconds.

    Typical stand scripts step throttle and hold; transient samples (rotor acceleration,
    load-cell ringing) must be removed before fitting quasi-steady models.
    """

    if command not in log.data:
        raise KeyError(f"Command column '{command}' not in log")
    cmd = log.data[command]
    time = log.data["time_s"]
    boundaries = [0]
    for index in range(1, len(cmd)):
        if not (abs(cmd[index] - cmd[boundaries[-1]]) <= tolerance):
            boundaries.append(index)
    boundaries.append(len(cmd))

    numeric_keys = [k for k in log.data if k != "time_s"]
    out: dict[str, list[float]] = {"t_start_s": [], "t_end_s": [], "n_samples": []}
    for key in numeric_keys:
        out[key] = []
        out[f"{key}_std"] = []
    for start, stop in zip(boundaries, boundaries[1:], strict=False):
        t0 = time[start]
        t_end = time[stop - 1]
        window = (time >= t0 + settle_s) & (np.arange(len(time)) >= start) & (np.arange(len(time)) < stop)
        if not window.any() or (t_end - (t0 + settle_s)) < min_hold_s:
            continue
        out["t_start_s"].append(float(t0 + settle_s))
        out["t_end_s"].append(float(t_end))
        out["n_samples"].append(float(window.sum()))
        for key in numeric_keys:
            values = log.data[key][window]
            finite = values[np.isfinite(values)]
            out[key].append(float(finite.mean()) if finite.size else math.nan)
            out[f"{key}_std"].append(float(finite.std(ddof=1)) if finite.size > 1 else math.nan)
    result = StandLog(
        {k: np.asarray(v, dtype=float) for k, v in out.items()},
        dict(log.source_columns),
        list(log.warnings),
        dict(log.metadata),
    )
    result.data["time_s"] = result.data["t_start_s"]
    return result
