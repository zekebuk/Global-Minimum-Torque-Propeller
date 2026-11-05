"""Analytical (first-order) electromagnetic, loss, mass and thermal model of an outrunner.

Design parameters -> geometry -> magnetic circuit -> Ke, R, I0(omega), mass, thermal network,
demagnetisation current limit -> ``MotorParams`` usable by the rest of ``bldc_motor``.

Physics (SI):

* Airgap flux density, surface magnets: ``Bg0 = k_leak * Br * hm / (hm + mu_r * kc * g)``, with
  Carter's coefficient ``kc = tau_s / (tau_s - gamma*g)`` and ``gamma = (w_so/g)^2 / (5 + w_so/g)``.
* Iron saturation as a smooth minimum over the tooth, yoke and bell flux limits:
  ``Bg = Bg0 / (1 + sum((B_i/Bsat_i)^8))^(1/8)``.
* Fundamental ``B1 = (4/pi) * Bg * sin(alpha*pi/2)``. Phase back-EMF constant (peak)
  ``Ke_ph = k_w * N_ph * B1 * D * L``. DC-equivalent ``Ke = sqrt(3)*Ke_ph`` (wye) or
  ``Ke_ph`` (delta), i.e. no-load line-to-line peak equals the DC bus (sinusoidal/FOC drive;
  six-step reads ~4 % higher Kv).
* Resistance from turn length (rounded-rectangle coil around the tooth): ``R_ll = 2 R_ph`` (wye)
  or ``2/3 R_ph`` (delta).
* No-load loss: Steinmetz hysteresis ``k_h f B^2`` plus classical lamination eddy loss
  ``pi^2 t^2 f^2 B^2 / (6 rho_e rho_m)`` times an excess factor, plus bearing friction.
  Expressed as ``I0(omega) = i0_a + i0_slope*omega`` for the DC-equivalent model.
* Demagnetisation: no-load magnet field plus the armature reaction field from the fundamental MMF
  ``F1 = (3/pi) k_w N_ph I_pk / p`` must stay below 0.8*Hcj at the grade's maximum temperature.

Accuracy is first-order (roughly +/-10-20 % on Kv/R/I0 before calibration). ``Calibration`` holds
correction factors that are fitted from built prototypes (see ``calibration.py``).
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path

from bldc_motor import units
from bldc_motor.design import geometry, materials, winding
from bldc_motor.motor import MotorParams, ThermalParams

LEAKAGE_FACTOR = 0.93
LEAD_LENGTH_M = 0.06
MAGNET_OVERHANG_M = 1.0e-3
DEMAG_KNEE_FRACTION = 0.8
REFERENCE_TEMP_C = 25.0


@dataclass
class Calibration:
    """Multiplicative corrections fitted from measured prototypes (1.0 = uncalibrated)."""

    kv_factor: float = 1.0
    resistance_factor: float = 1.0
    iron_loss_factor: float = 1.0
    friction_factor: float = 1.0
    convection_factor: float = 1.0
    samples: int = 0
    source: str = "uncalibrated analytical model"
    history: list = field(default_factory=list)  # per-prototype measured/predicted ratios

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2) + "\n", encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path | None) -> Calibration:
        if path is None or not Path(path).exists():
            return cls()
        return cls(**json.loads(Path(path).read_text(encoding="utf-8")))


@dataclass
class DesignAnalysis:
    design: dict
    geometry: geometry.Geometry
    layout: winding.WindingLayout
    motor: MotorParams | None
    valid: bool
    violations: dict
    internals: dict = field(default_factory=dict)

    @property
    def mass_g(self) -> float:
        return self.internals.get("mass_total_g", math.nan)

    @property
    def violation_total(self) -> float:
        return float(sum(self.violations.values()))


def _grade(name: str) -> materials.MagnetGrade:
    return next(g for g in materials.MAGNET_GRADES if g.name == name)


def _lamination(thickness_mm: float) -> materials.Lamination:
    return next(lam for lam in materials.LAMINATIONS if abs(lam.thickness_mm - thickness_mm) < 1e-9)


def analyze(design: dict, calibration: Calibration | None = None, name: str = "designed_motor") -> DesignAnalysis:
    cal = calibration or Calibration()
    g = geometry.derive(design)
    slots, poles = design["slot_pole"]
    p = poles // 2
    lay = winding.layout(slots, poles)
    grade = _grade(design["magnet_grade"])
    lam = _lamination(design["lamination_mm"])
    violations = dict(g.violations)
    if not lay.balanced:
        violations["winding_unbalanced"] = 1.0

    # ---- magnetic circuit -----------------------------------------------------------
    hm = design["magnet_mm"] * 1e-3
    alpha = design["magnet_arc"]
    ratio = g.slot_opening / g.airgap
    gamma = ratio**2 / (5.0 + ratio)
    carter = g.slot_pitch / max(g.slot_pitch - gamma * g.airgap, 1e-9)
    g_eff = carter * g.airgap
    br = grade.br_t * (1.0 + grade.br_coeff_per_k * (REFERENCE_TEMP_C - 20.0))
    bg0 = LEAKAGE_FACTOR * br * hm / (hm + grade.mu_rec * g_eff)
    pole_pitch = math.pi * g.r_s / p
    tooth_area_ratio = g.slot_pitch / max(g.w_t * materials.STACKING_FACTOR, 1e-9)
    yoke_ratio = alpha * pole_pitch / max(2.0 * g.h_y * materials.STACKING_FACTOR, 1e-9)
    bell_ratio = alpha * pole_pitch / max(2.0 * design["bell_mm"] * 1e-3, 1e-9)

    def flux_densities(bg):
        return bg * tooth_area_ratio, bg * yoke_ratio, bg * bell_ratio

    b_t0, b_y0, b_b0 = flux_densities(bg0)
    saturation = (
        (b_t0 / materials.B_SAT_LAMINATION_T) ** 8
        + (b_y0 / materials.B_SAT_LAMINATION_T) ** 8
        + (b_b0 / materials.B_SAT_BELL_T) ** 8
    )
    bg = bg0 / (1.0 + saturation) ** 0.125
    b_tooth, b_yoke, b_bell = flux_densities(bg)
    b1 = 4.0 / math.pi * bg * math.sin(alpha * math.pi / 2.0)

    # ---- winding / back-EMF --------------------------------------------------------
    turns = design["turns"]
    coils_per_phase = slots // 3
    n_ph = turns * coils_per_phase
    diameter = 2.0 * g.r_s + g.airgap
    ke_ph = lay.winding_factor * n_ph * b1 * diameter * g.stack
    delta = design["connection"] == "delta"
    ke_dc = (ke_ph if delta else math.sqrt(3.0) * ke_ph) / cal.kv_factor

    d_bare = materials.awg_diameter_mm(design["awg"]) * 1e-3
    d_ins = materials.insulated_diameter_mm(design["awg"]) * 1e-3
    strands = design["strands"]
    a_strand = math.pi * d_bare**2 / 4.0
    a_wire = strands * a_strand
    slot_area = max(g.slot_area, 1e-12)
    fill_bare = 2.0 * turns * a_wire / slot_area
    fill_envelope = 2.0 * turns * strands * d_ins**2 / slot_area  # square-packing envelope
    if fill_envelope > 0.9:
        violations["fill_physical"] = (fill_envelope - 0.9) * 10.0
    depth = max(g.r2 - g.r1, 1e-6)
    coil_build = turns * strands * d_ins**2 / depth / 0.8
    t_liner = materials.SLOT_LINER_THICKNESS_M
    l_turn = 2.0 * (g.stack + 2 * t_liner) + 2.0 * (g.w_t + 2 * t_liner) + math.pi * coil_build
    rho_cu = materials.COPPER_RESISTIVITY_20C_OHM_M * (1.0 + units.COPPER_TEMP_COEFF_PER_K * (REFERENCE_TEMP_C - 20.0))
    r_phase = rho_cu * (n_ph * l_turn + LEAD_LENGTH_M) / a_wire
    r_ll = (2.0 / 3.0 * r_phase if delta else 2.0 * r_phase) * cal.resistance_factor

    # ---- masses [kg] ---------------------------------------------------------------
    k_st = materials.STACKING_FACTOR
    area_yoke = math.pi * (g.r1**2 - g.r_i**2)
    area_teeth = slots * g.w_t * depth
    area_shoes = math.pi * (g.r_s**2 - g.r2**2) * max(1.0 - g.slot_opening / g.slot_pitch, 0.0)
    m_yoke = area_yoke * g.stack * k_st * materials.STEEL_DENSITY_KG_M3
    m_teeth = (area_teeth + area_shoes) * g.stack * k_st * materials.STEEL_DENSITY_KG_M3
    m_cu = 3.0 * (n_ph * l_turn + LEAD_LENGTH_M) * a_wire * materials.COPPER_DENSITY_KG_M3
    magnet_length = g.stack + MAGNET_OVERHANG_M
    m_mag = alpha * 2.0 * math.pi * (g.r_m_in + hm / 2.0) * hm * magnet_length * materials.NDFEB_DENSITY_KG_M3
    bell_t = design["bell_mm"] * 1e-3
    m_bell_steel = (
        2.0 * math.pi * (g.r_m_out + bell_t / 2.0) * bell_t * magnet_length * materials.BELL_STEEL_DENSITY_KG_M3
    )
    m_bell_al = (
        0.7 * math.pi * g.r_bell_out**2 * 1.2e-3 + 2.0 * math.pi * g.r_bell_out * 0.6e-3 * (g.stack + 2e-3)
    ) * materials.ALUMINIUM_DENSITY_KG_M3
    od_mm = design["stator_od_mm"]
    m_fixed = (1.5 + 0.012 * od_mm**2) * 1e-3  # shaft, bearings, base, screws
    mass_total = m_yoke + m_teeth + m_cu + m_mag + m_bell_steel + m_bell_al + m_fixed

    # ---- no-load losses -> I0(omega) -------------------------------------------------
    b2_mass = m_teeth * b_tooth**2 + m_yoke * b_yoke**2
    electrical_per_mech = p / units.TWO_PI  # f_e = electrical_per_mech * omega
    c_hyst = lam.k_hyst * electrical_per_mech * b2_mass  # W per rad/s
    t_lam = lam.thickness_mm * 1e-3
    c_eddy = (
        math.pi**2
        * t_lam**2
        * electrical_per_mech**2
        * b2_mass
        / (6.0 * lam.resistivity_ohm_m * materials.STEEL_DENSITY_KG_M3)
    ) * materials.IRON_LOSS_EXCESS_FACTOR  # W per (rad/s)^2
    c_hyst *= cal.iron_loss_factor
    c_eddy *= cal.iron_loss_factor
    friction_torque = 1.0e-4 * (od_mm / 22.0) ** 2 * cal.friction_factor
    i0_a = (c_hyst + friction_torque) / ke_dc
    i0_per_rad_s = c_eddy / ke_dc
    i0_per_krpm = i0_per_rad_s * units.rpm_to_rad_s(1000.0)

    # ---- thermal network ------------------------------------------------------------
    slot_perimeter = 2.0 * depth + max(g.slot_root_width, 0.0) + (2.0 * math.pi * g.r2 / slots - g.w_t)
    a_contact = slots * slot_perimeter * g.stack
    g_ws = materials.SLOT_LINER_CONDUCTIVITY_W_MK / t_liner * a_contact
    a_ext = 2.0 * (2.0 * math.pi * g.r_bell_out * (g.stack + 3e-3) + math.pi * g.r_bell_out**2)
    omega_ref = materials.CONVECTION_V_REF_M_S / g.r_bell_out
    thermal = ThermalParams(
        c_winding_j_per_k=m_cu * materials.COPPER_CP_J_KG_K,
        c_stator_j_per_k=(m_yoke + m_teeth + m_bell_steel) * materials.STEEL_CP_J_KG_K
        + m_mag * materials.NDFEB_CP_J_KG_K
        + (m_bell_al + 0.5 * m_fixed) * materials.ALUMINIUM_CP_J_KG_K,
        g_winding_stator_w_per_k=g_ws,
        g_stator_ambient_static_w_per_k=materials.CONVECTION_H_STILL_W_M2K * a_ext * cal.convection_factor,
        g_stator_ambient_ref_w_per_k=materials.CONVECTION_H_REF_W_M2K * a_ext * cal.convection_factor,
        omega_ref_rad_s=omega_ref,
        convection_exponent=0.8,
    )

    # ---- demagnetisation current limit -------------------------------------------------
    t_hot = grade.max_temp_c
    br_hot = grade.br_t * (1.0 + grade.br_coeff_per_k * (t_hot - 20.0))
    hcj_hot = grade.hcj_ka_m * 1e3 * (1.0 + grade.hcj_coeff_per_k * (t_hot - 20.0))
    b_m_hot = br_hot * hm / (hm + grade.mu_rec * g_eff)
    h_no_load = (br_hot - b_m_hot) / (materials.MU0 * grade.mu_rec)
    margin = DEMAG_KNEE_FRACTION * hcj_hot - h_no_load
    i_pk_demag = max(margin, 0.0) * (hm + grade.mu_rec * g_eff) * p * math.pi / (3.0 * lay.winding_factor * n_ph)
    i_dc_demag = i_pk_demag * (1.5 if delta else math.sqrt(3.0) / 2.0)
    if margin <= 0.0:
        violations["demag_no_load"] = -margin / (DEMAG_KNEE_FRACTION * hcj_hot)

    internals = {
        "carter": carter,
        "g_eff_mm": g_eff * 1e3,
        "bg0_t": bg0,
        "bg_t": bg,
        "b1_t": b1,
        "b_tooth_t": b_tooth,
        "b_yoke_t": b_yoke,
        "b_bell_t": b_bell,
        "saturation_factor": bg / bg0,
        "winding_factor": lay.winding_factor,
        "n_series_turns_per_phase": n_ph,
        "ke_dc_v_s_rad": ke_dc,
        "fill_bare": fill_bare,
        "fill_envelope": fill_envelope,
        "turn_length_mm": l_turn * 1e3,
        "coil_build_mm": coil_build * 1e3,
        "slot_area_mm2": slot_area * 1e6,
        "tooth_width_mm": g.w_t * 1e3,
        "slot_root_width_mm": g.slot_root_width * 1e3,
        "yoke_mm": g.h_y * 1e3,
        "wire_bare_mm": d_bare * 1e3,
        "wire_insulated_mm": d_ins * 1e3,
        "r_phase_ohm": r_phase,
        "mass_stator_iron_g": (m_yoke + m_teeth) * 1e3,
        "mass_teeth_g": m_teeth * 1e3,
        "mass_yoke_g": m_yoke * 1e3,
        "mass_copper_g": m_cu * 1e3,
        "mass_magnets_g": m_mag * 1e3,
        "mass_bell_steel_g": m_bell_steel * 1e3,
        "mass_bell_al_g": m_bell_al * 1e3,
        "mass_fixed_g": m_fixed * 1e3,
        "mass_total_g": mass_total * 1e3,
        "rotor_mass_g": (m_mag + m_bell_steel + m_bell_al) * 1e3,
        "hysteresis_w_per_rad_s": c_hyst,
        "eddy_w_per_rad_s2": c_eddy,
        "friction_torque_nm": friction_torque,
        "demag_current_a": i_dc_demag,
        "lamination_count": int(round(g.stack / t_lam)),
        "magnet_count": poles,
        "outer_diameter_mm": 2.0 * g.r_bell_out * 1e3,
        "electrical_hz_per_krpm": p * 1000.0 / 60.0,
    }
    valid = sum(violations.values()) == 0.0 and ke_dc > 0.0
    motor = None
    if ke_dc > 0.0 and r_ll > 0.0:
        motor = MotorParams(
            name=name,
            kv_rpm_per_v=units.ke_to_kv(ke_dc),
            r_ll_ohm=r_ll,
            i0_a=i0_a,
            i0_a_per_krpm=i0_per_krpm,
            ref_temp_c=REFERENCE_TEMP_C,
            beta_magnet_per_k=grade.br_coeff_per_k,
            pole_pairs=p,
            mass_g=mass_total * 1e3,
            max_current_a=i_dc_demag,
            max_winding_temp_c=150.0,
            max_magnet_temp_c=grade.max_temp_c,
            thermal=thermal,
            source="analytical design model" + ("" if cal.samples == 0 else f" (calibrated, {cal.samples} samples)"),
            notes=f"{slots}N{poles}P {design['connection']} {turns}T x{strands} AWG{design['awg']} {grade.name}",
        )
    return DesignAnalysis(design, g, lay, motor, valid, violations, internals)
