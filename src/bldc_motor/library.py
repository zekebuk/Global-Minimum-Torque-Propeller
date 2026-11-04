"""Component library: load motors, propellers, batteries and ESCs from JSON.

The bundled ``data/library.json`` is illustrative. Point ``load_library(extra=...)`` (or the
CLI ``--library`` flag) at your own JSON with the same layout to add measured components;
entries in later files override earlier ones with the same name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

from bldc_motor.motor import MotorParams
from bldc_motor.powertrain import ESC, Battery
from bldc_motor.propeller import PropellerModel

SECTIONS = ("motors", "propellers", "batteries", "escs")


@dataclass
class Library:
    motors: dict[str, MotorParams] = field(default_factory=dict)
    propellers: dict[str, PropellerModel] = field(default_factory=dict)
    batteries: dict[str, Battery] = field(default_factory=dict)
    escs: dict[str, ESC] = field(default_factory=dict)

    def motor(self, name: str) -> MotorParams:
        return _get(self.motors, name, "motor")

    def propeller(self, name: str) -> PropellerModel:
        return _get(self.propellers, name, "propeller")

    def battery(self, name: str) -> Battery:
        return _get(self.batteries, name, "battery")

    def esc(self, name: str) -> ESC:
        return _get(self.escs, name, "ESC")

    def merge_json(self, data: dict) -> None:
        for name, entry in data.get("motors", {}).items():
            self.motors[name] = MotorParams.from_dict(name, entry)
        for name, entry in data.get("propellers", {}).items():
            self.propellers[name] = PropellerModel.from_dict(name, entry)
        for name, entry in data.get("batteries", {}).items():
            self.batteries[name] = Battery.from_dict(name, entry)
        for name, entry in data.get("escs", {}).items():
            self.escs[name] = ESC.from_dict(name, entry)


def _get(table: dict, name: str, kind: str):
    if name not in table:
        raise KeyError(f"Unknown {kind} '{name}'. Available: {', '.join(sorted(table))}")
    return table[name]


def bundled_library_json() -> dict:
    text = resources.files("bldc_motor").joinpath("data/library.json").read_text(encoding="utf-8")
    return json.loads(text)


def load_library(*extra: str | Path, include_bundled: bool = True) -> Library:
    library = Library()
    if include_bundled:
        library.merge_json(bundled_library_json())
    for path in extra:
        with Path(path).open(encoding="utf-8") as handle:
            library.merge_json(json.load(handle))
    return library


def save_components(path: str | Path, motors=(), propellers=(), batteries=(), escs=()) -> Path:
    """Write components to a library-format JSON file (merging into an existing file)."""

    target = Path(path)
    data: dict = {}
    if target.exists():
        data = json.loads(target.read_text(encoding="utf-8"))
    for section, items in zip(SECTIONS, (motors, propellers, batteries, escs), strict=False):
        for item in items:
            entry = item.to_dict()
            entry.pop("name", None)
            data.setdefault(section, {})[item.name] = entry
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return target
