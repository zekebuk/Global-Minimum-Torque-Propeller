"""Small CSV and JSON helpers shared by workflow scripts."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    """Read a CSV file into a list of dictionaries."""

    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    if not rows:
        raise ValueError(f"CSV has no rows: {path}")
    return rows


def write_csv_rows(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> Path:
    """Write rows to a CSV file using stable field ordering."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: csv_value(row.get(key, "")) for key in fieldnames})
    return path


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object."""

    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"Expected JSON object: {path}")
    return payload


def write_text(path: Path, text: str) -> Path:
    """Write text, creating parent folders first."""

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def csv_value(value: object) -> object:
    """Format floats consistently for CSV output."""

    if isinstance(value, float):
        return f"{value:.12g}"
    return value

