"""Material presets. Values are typical-datasheet placeholders with stated knockdowns: replace them
with the datasheet of the actual grade, in the conditioned state if the polymer absorbs moisture.

Effective design values:

- E_Pa = datasheet tensile modulus (flow direction) x 0.85. Injection-molded short fibres align
  with the flow in the skins but lie transverse in the core, and flow-front orientation varies.
- allowable_stress_Pa = datasheet tensile strength x 0.40. This covers high-cycle fatigue (1P/2P at
  4000 rpm is about 10^8 cycles per 400 h), notches, weld lines, and process scatter.
"""

from __future__ import annotations

PRESETS: dict[str, dict[str, object]] = {
    "aluminum": {
        "name": "aluminum",
        "E_Pa": 70.0e9,
        "nu": 0.33,
        "density_kg_m3": 2700.0,
        "allowable_stress_Pa": 250.0e6,
        "note": "Baseline used by the thesis structural screening.",
    },
    "pa6-cf30": {
        "name": "PA6-CF30 (conditioned)",
        "E_Pa": 0.85 * 15.0e9,
        "nu": 0.35,
        "density_kg_m3": 1280.0,
        "allowable_stress_Pa": 0.40 * 150.0e6,
        "note": "Typical conditioned (50% RH) PA6 + 30% short carbon fibre: E 15 GPa, UTS 150 MPa; knockdowns 0.85 / 0.40.",
    },
    "peek-cf30": {
        "name": "PEEK-CF30",
        "E_Pa": 0.85 * 24.0e9,
        "nu": 0.40,
        "density_kg_m3": 1410.0,
        "allowable_stress_Pa": 0.40 * 260.0e6,
        "note": "Typical PEEK + 30% short carbon fibre: E 24 GPa, UTS 260 MPa, low moisture uptake; knockdowns 0.85 / 0.40.",
    },
}


def preset(name: str) -> dict[str, object]:
    key = name.strip().lower()
    if key not in PRESETS:
        raise KeyError(f"Unknown material preset {name!r}; available: {sorted(PRESETS)}")
    return dict(PRESETS[key])
