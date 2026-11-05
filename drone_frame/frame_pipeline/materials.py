"""Printed-material design allowables built from TDS values and explicit knock-down factors."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Material:
    name: str
    E: float            # design Young's modulus along the print plane, MPa
    G: float            # design shear modulus, MPa
    rho: float          # density, g/mm^3
    S_static: float     # design static strength (compared against ultimate stress), MPa
    S_endurance: float  # design endurance strength (alternating, ~1e8 cycles), MPa
    tau_interlaminar: float  # design shear strength across layer interfaces, MPa
    line_width: float
    layer_height: float
    hdt_C: float
    print: dict = field(default_factory=dict, compare=False, hash=False)

    @property
    def specific_stiffness(self) -> float:
        """E/rho in MPa per (g/cm^3)."""
        return self.E / (self.rho * 1e3)


def build_material(name: str, entry: dict) -> Material:
    tds = entry["tds"]
    E = min(tds["E_tensile_xy"], tds["E_flex_xy"]) * entry["k_E_process"] * entry["k_E_env"]
    G = entry["shear_modulus_factor"] * E / (2.0 * (1.0 + entry["poisson"]))
    S = tds["sigma_xy"] * entry["k_process"] * entry["k_env"]
    tau = entry["interlaminar_shear_ratio"] * tds["sigma_z"] * entry["k_process"] * entry["k_env"]
    return Material(
        name=name,
        E=E,
        G=G,
        rho=entry["density"] * 1e-3,
        S_static=S,
        S_endurance=entry["fatigue_ratio"] * S,
        tau_interlaminar=tau,
        line_width=entry["print"]["line_width"],
        layer_height=entry["print"]["layer_height"],
        hdt_C=entry["hdt_C"],
        print=entry["print"],
    )


def load_materials(mat_cfg: dict) -> dict[str, Material]:
    return {name: build_material(name, entry) for name, entry in mat_cfg.items()}
