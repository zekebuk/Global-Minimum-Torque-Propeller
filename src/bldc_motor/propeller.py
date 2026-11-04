"""Propeller load model in non-dimensional coefficient form.

    T = CT(J) * rho * n^2 * D^4
    P = CP(J) * rho * n^3 * D^5      Q = P / omega = CP * rho * n^2 * D^5 / (2*pi)
    J = V_inf / (n * D)

``n`` is in rev/s. CT and CP are polynomials in J (ascending coefficients); static
(hover / thrust-stand) use needs only the constant terms. The model neglects Reynolds-number
dependence of CT/CP, which is a few-percent effect across the rpm range of small FPV props;
fit the coefficients near the rpm range you care about.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np

from bldc_motor import units


def _poly(coeffs: Sequence[float], x: float) -> float:
    total = 0.0
    for power, coeff in enumerate(coeffs):
        total += coeff * x**power
    return total


@dataclass(frozen=True)
class PropellerModel:
    name: str
    diameter_m: float
    ct_coeffs: tuple[float, ...]
    cp_coeffs: tuple[float, ...]
    blade_count: int = 2
    pitch_m: float | None = None
    mass_g: float | None = None
    source: str = "unspecified"
    notes: str = ""

    def __post_init__(self) -> None:
        if self.diameter_m <= 0.0:
            raise ValueError(f"{self.name}: diameter must be positive")
        if not self.ct_coeffs or not self.cp_coeffs:
            raise ValueError(f"{self.name}: CT and CP need at least one coefficient")
        object.__setattr__(self, "ct_coeffs", tuple(float(c) for c in self.ct_coeffs))
        object.__setattr__(self, "cp_coeffs", tuple(float(c) for c in self.cp_coeffs))

    @property
    def disk_area_m2(self) -> float:
        return math.pi * (self.diameter_m / 2.0) ** 2

    def advance_ratio(self, omega_rad_s: float, v_inf_m_s: float = 0.0) -> float:
        n = omega_rad_s / units.TWO_PI
        return v_inf_m_s / (n * self.diameter_m) if n > 0.0 else 0.0

    def ct(self, j: float = 0.0) -> float:
        return _poly(self.ct_coeffs, j)

    def cp(self, j: float = 0.0) -> float:
        return _poly(self.cp_coeffs, j)

    def cq(self, j: float = 0.0) -> float:
        return self.cp(j) / units.TWO_PI

    def thrust(
        self, omega_rad_s: float, v_inf_m_s: float = 0.0, rho: float = units.SEA_LEVEL_AIR_DENSITY_KG_M3
    ) -> float:
        n = max(omega_rad_s, 0.0) / units.TWO_PI
        return self.ct(self.advance_ratio(omega_rad_s, v_inf_m_s)) * rho * n**2 * self.diameter_m**4

    def torque(
        self, omega_rad_s: float, v_inf_m_s: float = 0.0, rho: float = units.SEA_LEVEL_AIR_DENSITY_KG_M3
    ) -> float:
        n = max(omega_rad_s, 0.0) / units.TWO_PI
        return self.cq(self.advance_ratio(omega_rad_s, v_inf_m_s)) * rho * n**2 * self.diameter_m**5

    def power(
        self, omega_rad_s: float, v_inf_m_s: float = 0.0, rho: float = units.SEA_LEVEL_AIR_DENSITY_KG_M3
    ) -> float:
        return self.torque(omega_rad_s, v_inf_m_s, rho) * omega_rad_s

    def figure_of_merit(self, j: float = 0.0) -> float:
        """Static figure of merit (ideal momentum-theory power / actual power).

        FM = T^1.5 / (P * sqrt(2 rho A)); with A = pi D^2 / 4 this reduces to
        CT^1.5 / (CP * sqrt(pi/2)).
        """

        ct, cp = self.ct(j), self.cp(j)
        if ct <= 0.0 or cp <= 0.0:
            return 0.0
        return ct**1.5 / (cp * math.sqrt(math.pi / 2.0))

    # ---- constructors ----------------------------------------------------------------
    @classmethod
    def from_static_point(
        cls,
        name: str,
        diameter_m: float,
        rpm: float,
        thrust_n: float,
        torque_nm: float,
        rho: float = units.SEA_LEVEL_AIR_DENSITY_KG_M3,
        **kwargs,
    ) -> PropellerModel:
        """Build static coefficients from one measured or computed (rpm, thrust, torque) point."""

        n = rpm / 60.0
        ct = thrust_n / (rho * n**2 * diameter_m**4)
        cp = units.TWO_PI * torque_nm / (rho * n**2 * diameter_m**5)
        return cls(name=name, diameter_m=diameter_m, ct_coeffs=(ct,), cp_coeffs=(cp,), **kwargs)

    @classmethod
    def fit_static(
        cls,
        name: str,
        diameter_m: float,
        rpm: Sequence[float],
        thrust_n: Sequence[float],
        torque_nm: Sequence[float],
        rho: float = units.SEA_LEVEL_AIR_DENSITY_KG_M3,
        **kwargs,
    ) -> tuple[PropellerModel, dict]:
        """Least-squares fit of static CT, CP to thrust-stand points (T = kT n^2, Q = kQ n^2).

        Returns the model and fit diagnostics (R^2 and RMS residuals).
        """

        n = np.asarray(rpm, dtype=float) / 60.0
        thrust = np.asarray(thrust_n, dtype=float)
        torque = np.asarray(torque_nm, dtype=float)
        mask = n > 0.0
        basis = n[mask] ** 2
        k_t = float(basis @ thrust[mask] / (basis @ basis))
        k_q = float(basis @ torque[mask] / (basis @ basis))
        ct = k_t / (rho * diameter_m**4)
        cp = units.TWO_PI * k_q / (rho * diameter_m**5)
        diagnostics = {
            "points": int(mask.sum()),
            "thrust_r2": _r_squared(thrust[mask], k_t * basis),
            "torque_r2": _r_squared(torque[mask], k_q * basis),
            "thrust_rms_n": float(np.sqrt(np.mean((thrust[mask] - k_t * basis) ** 2))),
            "torque_rms_nm": float(np.sqrt(np.mean((torque[mask] - k_q * basis) ** 2))),
        }
        return cls(name=name, diameter_m=diameter_m, ct_coeffs=(ct,), cp_coeffs=(cp,), **kwargs), diagnostics

    def to_dict(self) -> dict:
        data = asdict(self)
        data["ct_coeffs"] = list(self.ct_coeffs)
        data["cp_coeffs"] = list(self.cp_coeffs)
        return data

    @classmethod
    def from_dict(cls, name: str, data: dict) -> PropellerModel:
        payload = dict(data)
        payload.pop("name", None)
        if "diameter_in" in payload:
            payload["diameter_m"] = payload.pop("diameter_in") * units.INCH_TO_M
        if "pitch_in" in payload:
            payload["pitch_m"] = payload.pop("pitch_in") * units.INCH_TO_M
        payload["ct_coeffs"] = tuple(payload["ct_coeffs"])
        payload["cp_coeffs"] = tuple(payload["cp_coeffs"])
        return cls(name=name, **payload)


def _r_squared(measured: np.ndarray, predicted: np.ndarray) -> float:
    ss_res = float(np.sum((measured - predicted) ** 2))
    ss_tot = float(np.sum((measured - measured.mean()) ** 2))
    return 1.0 - ss_res / ss_tot if ss_tot > 0.0 else float("nan")
