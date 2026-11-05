"""Checks against closed-form solutions + consistency between the analysis model and the CAD."""

import math
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from frame_pipeline import beam_fe  # noqa: E402
from frame_pipeline.arm_model import ArmContext, arm_mass_breakdown, evaluate, socket_half_width, unpack  # noqa: E402
from frame_pipeline.config import load_frame_config, load_materials_config  # noqa: E402
from frame_pipeline.layout import body_half_dims, canopy_rect, dist_point_rect, root_radius, solve_layout  # noqa: E402
from frame_pipeline.materials import load_materials  # noqa: E402
from frame_pipeline.rotor import modal_margin, operating_points, rev_per_s_for_thrust  # noqa: E402
from frame_pipeline.sections import ArmSection, poly_props  # noqa: E402


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
    E, I, L, m_tip = 7e9, 2e-8, 0.15, 0.08
    x = np.linspace(0, L, 21)
    EI = np.full(20, E * I)
    mpl = np.full(20, 0.02)  # 3 g beam
    K, M = beam_fe.assemble_bending(x, EI, mpl, m_tip, 0.0)
    F = np.zeros(K.shape[0]); F[-2] = 10.0
    u = beam_fe.static_solve(K, F)
    assert u[-2] == pytest.approx(10 * L**3 / (3 * E * I), rel=1e-6)
    f, _ = beam_fe.natural_frequencies(K, M, 1)
    m_eff = m_tip + 0.236 * 0.02 * L  # Rayleigh
    assert f[0] == pytest.approx(math.sqrt(3 * E * I / L**3 / m_eff) / (2 * math.pi), rel=2e-3)


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
    from frame_pipeline.cad.parts import make_arm
    from frame_pipeline.geometry import derive

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
