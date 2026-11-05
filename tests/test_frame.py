"""Checks against closed-form solutions + consistency between the analysis model and the CAD."""

import math

import numpy as np
import pytest

from prandtl_prop.frame import beam_fe
from prandtl_prop.frame.arm_model import ArmContext, arm_mass_breakdown, evaluate, socket_half_width, unpack
from prandtl_prop.frame.config import load_frame_config, load_materials_config
from prandtl_prop.frame.layout import body_half_dims, canopy_rect, dist_point_rect, root_radius, solve_layout
from prandtl_prop.frame.materials import load_materials
from prandtl_prop.frame.rotor import modal_margin, operating_points, rev_per_s_for_thrust
from prandtl_prop.frame.sections import ArmSection, poly_props


@pytest.fixture(scope="module")
def cfg():
    return load_frame_config()


@pytest.fixture(scope="module")
def mats():
    return load_materials(load_materials_config())


def test_rectangle_section_exact():
    b, h = 20.0, 30.0
    p = poly_props([(-b / 2, -h), (b / 2, -h), (b / 2, 0), (-b / 2, 0)])
    assert p["A"] == pytest.approx(b * h)
    zc = p["Sy"] / p["A"]
    assert zc == pytest.approx(-h / 2)
    assert p["Iyy0"] - p["A"] * zc**2 == pytest.approx(b * h**3 / 12)
    assert p["Izz0"] == pytest.approx(h * b**3 / 12)


def test_arm_section_channel_reduces_inertia():
    s = ArmSection(22.0, 30.0, 2.48, 1.68)
    p = s.props()
    assert s.channel_valid
    assert 0 < p["Iyy"] < 22 * 30**3 / 12
    assert 0 < p["Izz"] < 30 * 22**3 / 12
    assert p["A"] == pytest.approx(22 * 30 - p["A_channel"])
    assert p["J"] > 0 and p["Q"] > 0


def test_cantilever_static_and_modal():
    E, Iyy, L, m_tip = 7e9, 2e-8, 0.15, 0.08
    x = np.linspace(0, L, 21)
    EI = np.full(20, E * Iyy)
    mpl = np.full(20, 0.02)  # 3 g beam
    K, M = beam_fe.assemble_bending(x, EI, mpl, m_tip, 0.0)
    F = np.zeros(K.shape[0])
    F[-2] = 10.0
    u = beam_fe.static_solve(K, F)
    assert u[-2] == pytest.approx(10 * L**3 / (3 * E * Iyy), rel=1e-6)
    f, _ = beam_fe.natural_frequencies(K, M, 1)
    m_eff = m_tip + 0.236 * 0.02 * L  # Rayleigh
    assert f[0] == pytest.approx(math.sqrt(3 * E * Iyy / L**3 / m_eff) / (2 * math.pi), rel=2e-3)


def test_rotor_bands_and_margin(cfg):
    op = operating_points(2000, cfg)
    assert op["thrust_to_weight"] > 3
    bands = op["forbidden_bands_Hz"]
    assert all(b[0] < b[1] for b in bands)
    assert all(bands[i][1] < bands[i + 1][0] for i in range(len(bands) - 1))
    lo, hi = op["allowed_windows_Hz"][0]
    assert modal_margin(0.5 * (lo + hi), op) > 0
    assert modal_margin(op["f1P_hover_Hz"], op) < 0
    T = cfg["propulsion"]["ct_static"] * 1.225 * 50**2 * (cfg["propulsion"]["prop_diameter"] / 1000) ** 4
    assert rev_per_s_for_thrust(T, cfg["propulsion"]) == pytest.approx(50.0)


def test_layout_constraints_hold(cfg):
    lay = solve_layout(cfg, socket_half_width(22.0, cfg))
    D = cfg["propulsion"]["prop_diameter"]
    c = cfg["layout"]
    assert lay["front_prop_gap"] >= c["prop_tip_clearance_min"] - 1e-6
    assert lay["side_prop_gap"] >= c["prop_tip_clearance_min"] - 1e-6
    rect = canopy_rect(cfg)
    for mx, my in lay["motor_positions"]:
        assert dist_point_rect(mx, my, rect) - D / 2 >= c["prop_to_canopy_clearance_min"] - 1e-3
    assert lay["binding_constraints"], "the optimum must sit on at least one constraint"


def test_root_radius_clears_rectangle_corner(cfg):
    hx, hy = body_half_dims(cfg)
    th = math.radians(50)
    w = socket_half_width(22.0, cfg)
    r0 = root_radius(th, w, hx, hy, margin=0.0)
    # every point of the root face segment is outside (or on) the body rectangle
    for o in np.linspace(-w, w, 101):
        x = r0 * math.cos(th) - o * math.sin(th)
        y = r0 * math.sin(th) + o * math.cos(th)
        assert abs(x) >= hx - 1e-6 or abs(y) >= hy - 1e-6


def _ctx(cfg, mats, mat="PPA-CF"):
    op = operating_points(2000, cfg)
    lay = solve_layout(cfg, socket_half_width(22.0, cfg))
    hx, hy = body_half_dims(cfg)
    return ArmContext(cfg, mats[mat], mats["PAHT-CF"], lay["arm_free_length"], lay["root_radius"], 2 * hy, 200, op), lay


def test_stiffer_material_raises_frequency(cfg, mats):
    x = [22.0, 30.0, 0.5, 4, 7]
    f = {m: evaluate(x, _ctx(cfg, mats, m)[0])["f_vert_Hz"] for m in ("PPA-CF", "PAHT-CF", "PETG-HF")}
    assert f["PPA-CF"] > f["PAHT-CF"] > f["PETG-HF"]


def test_cad_arm_matches_model_mass(cfg, mats):
    cq = pytest.importorskip("cadquery")  # noqa: F841
    from prandtl_prop.frame.cad.parts import make_arm
    from prandtl_prop.frame.geometry import derive

    ctx, lay = _ctx(cfg, mats)
    x = [22.0, 30.0, 0.5, 4, 7]
    res = evaluate(x, ctx)
    an = {"layout": lay, "arm": {"result": res}}
    g = derive(cfg, an)
    arm = make_arm(g)
    vol = sum(s.Volume() for s in arm.solids().vals())
    model = arm_mass_breakdown(unpack(x, mats["PPA-CF"]), ctx)["total_g"] / mats["PPA-CF"].rho
    # CAD has the exit slot, motor holes and the tenon chamfer that the beam model ignores
    assert vol == pytest.approx(model, rel=0.08)


def test_dovetail_self_locks_and_is_printable(cfg):
    from prandtl_prop.frame.arm_model import dovetail

    dv = dovetail(cfg)
    # wedge self-locks when tan(taper half-angle) < friction coefficient
    assert dv["taper_per_side"] < dv["friction_coefficient"]
    # flank overhang must print without support on both parts (<= 45 deg from vertical)
    assert dv["flank_angle_deg"] <= 45
    # tolerance absorbed by home_gap travel: +/- width error = 2 * taper * home_gap
    assert 2 * dv["taper_per_side"] * dv["home_gap"] >= 0.2


def test_dovetail_joint_stiffer_than_arm_and_survives_crash_case(cfg, mats):
    from prandtl_prop.frame.arm_model import joint_springs, unpack

    ctx, _ = _ctx(cfg, mats)
    x = [22.0, 30.0, 0.5, 4, 7]
    d = unpack(x, mats["PPA-CF"])
    js = joint_springs(d, ctx)
    sec = __import__("prandtl_prop.frame.sections", fromlist=["ArmSection"]).ArmSection(22.0, 30.0, d["t_w"], d["t_f"]).props()
    arm_rot = mats["PPA-CF"].E * sec["Iyy"] / ctx.L * 1e-3      # N*m/rad, cantilever EI/L
    assert js["vert"] > 10 * arm_rot
    r = evaluate(x, ctx)
    assert r["margins"]["joint_crash"] > 0
    assert r["self_locking_ratio"] > 2


def test_dovetail_cad_seats_and_slides_out(cfg, mats):
    pytest.importorskip("cadquery")
    from prandtl_prop.frame.cad import parts as P
    from prandtl_prop.frame.cad.assembly import intersection_volume
    from prandtl_prop.frame.geometry import arm_axes, derive

    ctx, lay = _ctx(cfg, mats)
    res = evaluate([22.0, 30.0, 0.5, 4, 7], ctx)
    g = derive(cfg, {"layout": lay, "arm": {"result": res}})
    body = P.make_bottom_body(g)
    ang, (ux, uy) = arm_axes(g)[0]
    arm = P.place_arm(P.make_arm(g), g, ang)
    assert intersection_volume(arm, body) < 1e-3                       # seated: line-on-line, no clash
    assert intersection_volume(arm.translate((-0.5 * ux, -0.5 * uy, 0)), body) > 1.0   # deeper -> wedges
    for dist in (5.0, 20.0):
        assert intersection_volume(arm.translate((dist * ux, dist * uy, 0)), body) < 1e-3  # slides out freely


# ------------------------------------------------------------------ prandtl pipeline integration


def _fpv_design(overrides=None):
    from prandtl_prop.design import load_design, resolve_path

    return load_design(resolve_path("designs/gmt_fpv10_tri.yaml"), overrides or [])


def _fake_upstream(design):
    """Upstream metrics consistent with a constant-CT rotor, so the mapping can be checked exactly."""
    op, spec = design.operating_point, design.multirotor
    D = op.diameter_in * 0.0254
    ct, cp = 0.045, 0.014
    n = math.sqrt(spec.hover_thrust_N / (ct * op.rho_kg_m3 * D**4))
    vehicle = {
        "hover_rpm": 60 * n,
        "hover_shaft_power_W": cp * op.rho_kg_m3 * n**3 * D**5,
        "max_thrust_torque_Nm": 0.25,
        "max_thrust_rpm": 15000.0,
    }
    return {"prop_mass_kg": 0.013, "blade_mass_kg": 0.0043}, vehicle


def test_frame_stage_is_registered():
    from prandtl_prop import pipeline

    assert "frame" in pipeline.STAGES
    assert pipeline.STAGE_UPSTREAM["frame"] == ("aero", "cad", "multirotor")
    assert "frame" in pipeline.STAGE_FUNCTIONS and "frame" in pipeline.STAGE_DESIGN_INPUTS
    assert "frame" not in pipeline.DEFAULT_STAGES          # minutes, like fea/cfd
    names = [p.name for p in pipeline._input_files("frame", _fpv_design())]
    assert {"frame.yaml", "materials.yaml"} <= set(names)  # config edits invalidate the cache


def test_frame_section_in_design_schema():
    d = _fpv_design(["frame.build_cad=false", "frame.optimizer_generations=20"])
    assert d.frame.build_cad is False and d.frame.optimizer_generations == 20
    with pytest.raises(ValueError):
        _fpv_design(["frame.not_a_key=1"])


def test_vehicle_inputs_recover_rotor_coefficients_and_orders(cfg):
    from prandtl_prop.frame.vehicle import apply_vehicle_inputs, vehicle_inputs

    d = _fpv_design()
    cad, veh = _fake_upstream(d)
    vin = vehicle_inputs(d, cad, veh)
    assert vin["ct_static"] == pytest.approx(0.045, rel=1e-9)
    assert vin["cp_static"] == pytest.approx(0.014, rel=1e-9)
    assert vin["prop_blades"] == 3 and vin["harmonics"] == [1, 3, 6, 9]
    assert vin["prop_mass_g"] == pytest.approx(13.0)
    assert vin["motor_max_thrust_N"] == pytest.approx(d.multirotor.max_thrust_N)
    new = apply_vehicle_inputs(cfg, vin)
    assert new["propulsion"]["prop_diameter"] == pytest.approx(254.0)
    assert new["loads"]["harmonics"] == [1, 3, 6, 9]
    assert cfg["propulsion"]["prop_blades"] == 2       # the standalone config is not mutated
    op = operating_points(1500, new)
    assert op["Q_max_Nm"] == pytest.approx(0.25)       # BEMT torque wins over the constant-CP estimate


def test_consistency_reports_payload_to_close_the_loop():
    from prandtl_prop.frame.vehicle import consistency

    d = _fpv_design()
    an = {"auw_g": 2000.0, "layout": {"motor_radius": 213.0},
          "_cfg_propulsion": {"motor_mass_g": 50.0, "prop_mass_g": 13.0}}
    metrics, warnings = consistency(d, an, d.frame)
    assert metrics["suggested_payload_mass_kg"] == pytest.approx(2.0 - 4 * 0.063)
    assert any("payload_mass_kg" in w for w in warnings)          # 2.0 kg vs 1.446 kg spec
    an["auw_g"] = 1e3 * d.multirotor.mass_kg
    _, warnings = consistency(d, an, d.frame)
    assert not any("payload_mass_kg" in w for w in warnings)
