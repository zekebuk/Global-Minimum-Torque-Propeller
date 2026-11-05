"""Physics model of one modular arm and its load path into the body.

Design vector x = [b, h_root, taper, n_walls, n_layers]
    b         outer width (mm)              -> lateral stiffness, prop download
    h_root    outer height at the root (mm) -> vertical stiffness/strength
    taper     h_tip / h_root                -> material where the moment is small is removed
    n_walls   wall loops on the webs        -> web thickness = n * line_width (printable by construction)
    n_layers  skin layers on the flanges    -> flange thickness = n * layer_height

Load path (x measured from the body centre, SI inside the FE model):
    [0, r_root-L_t]   body box (tub + top plate), clamped at the centre (symmetric collective mode)
    [r_root-L_t, r_root]  socket: body box + arm tenon in parallel
    joint             Winkler contact spring (printed faces are not perfectly flat)
    [r_root, r_root+L]  tapered arm, lumped motor + prop + pad at the motor centre
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import beam_fe
from .config import G
from .materials import Material
from .rotor import modal_margin
from .sections import ArmSection, box_props

MM = 1e-3


@dataclass
class ArmContext:
    cfg: dict
    mat: Material
    body_mat: Material
    L: float                # free arm length, root face -> motor centre (mm)
    r_root: float           # body centre -> root face (mm)
    body_width: float       # effective box width for diagonal bending (mm)
    body_mass_g: float      # non-arm structural mass carried by the clamp (for body segment inertia)
    op: dict                # operating point (rotor.operating_points)


def unpack(x, mat: Material) -> dict:
    b, h_r, taper, n_w, n_l = float(x[0]), float(x[1]), float(x[2]), int(round(x[3])), int(round(x[4]))
    return {
        "b": b, "h_root": h_r, "taper": taper, "h_tip": h_r * taper,
        "n_walls": n_w, "n_layers": n_l,
        "t_w": n_w * mat.line_width, "t_f": n_l * mat.layer_height,
    }


def section_at(d: dict, s: float) -> ArmSection:
    """s in [0, 1] from root face to motor centre."""
    h = d["h_root"] * (1 - (1 - d["taper"]) * s)
    return ArmSection(d["b"], h, d["t_w"], d["t_f"])


def tenon_width(d: dict, cfg: dict) -> float:
    return d["b"] + 2 * cfg["arm"]["tenon_ear_width"]


def dovetail(cfg: dict) -> dict:
    j = cfg["arm"]["joint"]
    beta = math.radians(j["flank_angle_deg"])
    return {**j, "beta": beta, "flare": j["depth"] * math.tan(beta)}


def socket_half_width(b: float, cfg: dict) -> float:
    """Half width of the socket boss: tenon + dovetail flare (widest at the mouth) + socket wall."""
    return (b + 2 * cfg["arm"]["tenon_ear_width"]) / 2 + dovetail(cfg)["flare"] + cfg["body"]["socket_wall_t"]


def tie_bolt_y(b: float, cfg: dict) -> float:
    """Tie bolts run through the middle of the socket walls, outboard of the dovetail foot."""
    return (b + 2 * cfg["arm"]["tenon_ear_width"]) / 2 + dovetail(cfg)["flare"] + cfg["body"]["socket_wall_t"] / 2


def body_interior_height(h_root: float, cfg: dict) -> float:
    return max(h_root, cfg["electronics"]["fc_stack"]["height"])


def arm_mass_breakdown(d: dict, ctx: ArmContext) -> dict:
    cfg, rho = ctx.cfg, ctx.mat.rho
    L = ctx.L
    # A(s) is exactly linear in s (outer rectangle and channel both scale linearly with h), so the
    # trapezoid rule on the two end sections is exact.
    root = section_at(d, 0).props()
    tip = section_at(d, 1).props()
    span = 0.5 * (root["A"] + tip["A"]) * L * rho
    Lt = cfg["arm"]["tenon_length"]
    bt = tenon_width(d, cfg)
    dv = dovetail(cfg)
    inserts = dv["retention_screws"] * math.pi * (cfg["body"]["m3_insert_hole_d"] / 2) ** 2 * dv["insert_depth"]
    section = bt * d["h_root"] + dv["depth"] * dv["flare"] - root["A_channel"]   # + two foot triangles
    taper_loss = dv["taper_per_side"] * Lt**2 * d["h_root"]                      # plan taper, both sides
    tenon = (section * Lt - taper_loss - inserts) * rho
    r = cfg["arm"]["pad_diameter"] / 2
    shaft = math.pi * (cfg["propulsion"]["motor_shaft_clearance_d"] / 2) ** 2
    pad = (math.pi * r * r - d["b"] * r - shaft) * d["h_tip"] * rho
    return {"span_g": span, "tenon_g": tenon, "pad_g": max(pad, 0.0), "total_g": span + tenon + max(pad, 0.0)}


def joint_springs(d: dict, ctx: ArmContext) -> dict:
    """Winkler-foundation rotational stiffness of the seated dovetail (N*m/rad).

    Contact is on the two 60-deg flanks and the floor only (upper sides have clearance, top plate has
    relief). A Winkler layer of modulus k_w on a face inclined phi from horizontal gives, per unit
    *projected* area, vertical stiffness k_w*cos(phi) and horizontal stiffness k_w*sin(phi).
      vertical rotation : upward reaction on the flanks (projected width 2*flare*cos(phi)),
                          downward on the floor (foot width); the two act in series.
      lateral rotation  : flanks only, projected height = depth on each side.
      torsion           : floor + flanks across the foot width.
    """
    ld = ctx.cfg["loads"]
    dv = dovetail(ctx.cfg)
    k_w = ctx.body_mat.E / ld["joint_contact_layer"] * ld["joint_contact_fraction"]  # N/mm^3
    Lt = ctx.cfg["arm"]["tenon_length"]
    bt = tenon_width(d, ctx.cfg)
    phi = math.pi / 2 - dv["beta"]                    # flank angle from horizontal
    w_up = 2 * dv["flare"] * math.cos(phi)
    w_down = bt + 2 * dv["flare"]
    w_v = 2 * w_up * w_down / (w_up + w_down)
    k_vert = k_w * w_v * Lt**3 / 12 * MM          # N*mm/rad -> N*m/rad
    k_lat = k_w * 2 * dv["depth"] * math.sin(phi) * Lt**3 / 12 * MM
    k_tor = k_w * Lt * w_down**3 / 12 * MM
    return {"vert": k_vert, "lat": k_lat, "tor": k_tor, "w_up": w_up, "w_down": w_down}


def joint_capacity(d: dict, ctx: ArmContext, H_box: float) -> dict:
    """Static moment capacity (N*mm) of each link in the socket load path, rigid-plastic.

    Thrust bends the arm tip up: the tenon pushes UP on the flanks near the mouth and DOWN on the
    floor near the back. Flank load goes up through the socket block in compression to the top plate,
    and back to the floor through the steel tie bolts at x = -Lt/4 (lever Lt/2 to the floor reaction).
    """
    cfg, bm = ctx.cfg, ctx.body_mat
    body = cfg["body"]
    dv = dovetail(cfg)
    Lt = cfg["arm"]["tenon_length"]
    js = joint_springs(d, ctx)
    flank = bm.S_static * js["w_up"] / math.cos(math.pi / 2 - dv["beta"]) * Lt**2 / 4  # bearing on the true flank area
    washer = math.pi / 4 * (dv["washer_od"] ** 2 - body["m3_clearance_d"] ** 2) * bm.S_static
    F_bolt = min(dv["tie_bolt_proof_N"], washer)
    tie = dv["tie_bolts_per_socket"] * F_bolt * Lt / 2
    box = box_props(ctx.body_width, H_box, body["top_plate_t"], body["bottom_plate_t"], body["wall_t"])
    box_cap = bm.S_static * box["Iyy"] * body["stiffness_knockdown"] / (H_box / 2)
    caps = {"flank_bearing": flank, "tie_bolts": tie, "body_box": box_cap}
    gov = min(caps, key=caps.get)
    return {"caps_Nmm": caps, "governing": gov, "M_cap": caps[gov], "F_tie_bolt_N": F_bolt}


def build_models(d: dict, ctx: ArmContext) -> dict:
    cfg, mat, bm = ctx.cfg, ctx.mat, ctx.body_mat
    prop = cfg["propulsion"]
    body = cfg["body"]
    Lt = cfg["arm"]["tenon_length"]
    n_arm = cfg["arm"]["fe_elements"]
    H_int = body_interior_height(d["h_root"], cfg)
    H_box = body["bottom_plate_t"] + H_int + body["top_plate_t"]
    box = box_props(ctx.body_width, H_box, body["top_plate_t"], body["bottom_plate_t"], body["wall_t"])
    kb = body["stiffness_knockdown"]
    EI_body_v = bm.E * 1e6 * box["Iyy"] * kb * 1e-12
    W, tp, tb = ctx.body_width, body["top_plate_t"], body["bottom_plate_t"]
    EI_body_l = bm.E * 1e6 * (tp + tb) * W**3 / 12 * kb * 1e-12   # in-plane plates
    GJ_body = bm.G * 1e6 * box["J"] * kb * 1e-12
    m_body = ctx.body_mass_g * 1e-3 / 4 / (ctx.r_root * MM)        # kg/m, quarter body per arm
    ip_body = m_body * ((W * MM) ** 2 + (H_box * MM) ** 2) / 12

    root = section_at(d, 0).props()
    bt = tenon_width(d, cfg)
    # tenon section = solid tenon rectangle minus the same channel as the arm root
    ten_Iyy = bt * d["h_root"] ** 3 / 12 - (d["b"] * d["h_root"] ** 3 / 12 - root["Iyy"])
    ten_Izz = d["h_root"] * bt**3 / 12 - (d["h_root"] * d["b"] ** 3 / 12 - root["Izz"])
    ten_A = bt * d["h_root"] - root["A_channel"]

    x_nodes = [0.0]
    EIv, EIl, GJ, mpl, ipl = [], [], [], [], []

    def add(n, length, eiv, eil, gj, m, ip):
        for _ in range(n):
            x_nodes.append(x_nodes[-1] + length / n)
            EIv.append(eiv); EIl.append(eil); GJ.append(gj); mpl.append(m); ipl.append(ip)

    add(4, (ctx.r_root - Lt) * MM, EI_body_v, EI_body_l, GJ_body, m_body, ip_body)
    add(3, Lt * MM,
        EI_body_v + mat.E * 1e6 * ten_Iyy * 1e-12,
        EI_body_l + mat.E * 1e6 * ten_Izz * 1e-12,
        GJ_body + mat.G * 1e6 * root["J"] * 1e-12,
        m_body + ten_A * mat.rho,   # rho[g/mm^3]*A[mm^2] = g/mm = kg/m
        ip_body)
    js = joint_springs(d, ctx)
    lj = 0.5 * MM
    add(1, lj, js["vert"] * lj, js["lat"] * lj, js["tor"] * lj, 1e-9, 1e-12)
    stations = []
    for e in range(n_arm):
        s_mid = (e + 0.5) / n_arm
        p = section_at(d, s_mid).props()
        stations.append(p)
        add(1, ctx.L * MM / n_arm,
            mat.E * 1e6 * p["Iyy"] * 1e-12,
            mat.E * 1e6 * p["Izz"] * 1e-12,
            mat.G * 1e6 * p["J"] * 1e-12,
            p["A"] * mat.rho,          # g/mm == kg/m
            mat.rho * p["Ip"] * 1e-6)  # rho[g/mm^3]*Ip[mm^4] = g*mm = 1e-6 kg*m (polar inertia per length)

    x = np.array(x_nodes)
    # tip lumped properties
    mass = arm_mass_breakdown(d, ctx)
    m_motor = prop["motor_mass_g"] * 1e-3
    m_prop = prop["prop_mass_g"] * 1e-3
    m_pad = mass["pad_g"] * 1e-3
    m_bolts = 4e-3
    m_tip = m_motor + m_prop + m_pad + m_bolts
    zc = section_at(d, 1).props()["zc"]
    r_m = prop["motor_base_diameter"] / 2 * MM
    h_m = prop["motor_height"] * MM
    z_cg = (prop["motor_cg_above_base"] - zc) * MM
    z_p = (prop["motor_height"] - zc) * MM
    D = prop["prop_diameter"] * MM
    I_rock = m_motor * (3 * r_m**2 + h_m**2) / 12 + m_motor * z_cg**2 + m_prop * D**2 / 24 + m_prop * z_p**2
    I_yaw = 0.5 * m_motor * (0.8 * r_m) ** 2 / 2 + m_pad * (cfg["arm"]["pad_diameter"] / 2 * MM) ** 2 / 2
    return {
        "x": x, "EIv": np.array(EIv), "EIl": np.array(EIl), "GJ": np.array(GJ),
        "mpl": np.array(mpl), "ipl": np.array(ipl),
        "m_tip": m_tip, "I_rock": I_rock, "I_yaw": I_yaw, "stations": stations,
        "mass": mass, "joint": js, "H_box": H_box, "EI_body_v": EI_body_v,
    }


def evaluate(xvec, ctx: ArmContext) -> dict:
    cfg, mat, op = ctx.cfg, ctx.mat, ctx.op
    d = unpack(xvec, mat)
    ld = cfg["loads"]
    res = {"design": d}
    tip = section_at(d, 1.0)
    res["channel_tip_w"] = tip.channel_w
    res["channel_tip_h"] = tip.channel_h
    if not tip.channel_valid or tip.channel_w <= 0:
        res["valid"] = False
        return res
    mdl = build_models(d, ctx)
    Kv, Mv = beam_fe.assemble_bending(mdl["x"], mdl["EIv"], mdl["mpl"], mdl["m_tip"], mdl["I_rock"])
    Kl, Ml = beam_fe.assemble_bending(mdl["x"], mdl["EIl"], mdl["mpl"], mdl["m_tip"], mdl["I_yaw"])
    Kt, Mt = beam_fe.assemble_torsion(mdl["x"], mdl["GJ"], mdl["ipl"] + 1e-9, mdl["I_rock"])
    fv, vv = beam_fe.natural_frequencies(Kv, Mv, 2)
    fl, _ = beam_fe.natural_frequencies(Kl, Ml, 1)
    ft, _ = beam_fe.natural_frequencies(Kt, Mt, 1)

    # static: limit thrust at the tip
    F_lim = op["T_limit_N"]
    Fv = np.zeros(Kv.shape[0]); Fv[-2] = F_lim
    u = beam_fe.static_solve(Kv, Fv)
    tip_defl_mm = u[-2] * 1e3
    tip_slope_deg = math.degrees(u[-1])

    # stresses along the arm (moment from the tip load, plus motor reaction torque laterally)
    L = ctx.L
    n = cfg["arm"]["fe_elements"]
    Q_lim = op["Q_max_Nm"] * ld["dynamic_factor"] * 1e3  # N*mm
    sig_max, tau_max, s_crit = 0.0, 0.0, 0.0
    root_p = section_at(d, 0.0).props()
    for e, p in enumerate([root_p] + mdl["stations"]):
        s_st = 0.0 if e == 0 else (e - 0.5) / n
        M = F_lim * L * (1 - s_st)
        sec = p
        sig = M * sec["c_max"] / sec["Iyy"] + Q_lim * sec["c_lat"] / sec["Izz"]
        tau = F_lim * sec["Q"] / (sec["Iyy"] * 2 * d["t_w"])
        if sig > sig_max:
            sig_max, s_crit = sig, s_st
        tau_max = max(tau_max, tau)
    SF = ld["safety_factor"]
    root = section_at(d, 0).props()
    M_root_hover = op["T_hover_N"] * L
    sig_m = M_root_hover * root["c_max"] / root["Iyy"]
    sig_a = ld["fatigue_alt_fraction"] * sig_m
    goodman = sig_a / mat.S_endurance + sig_m / mat.S_static

    # joint crash case + fuse ratio (reported). M_limit_root = limit thrust x free length.
    M_arm_cap = mat.S_static * root["Iyy"] / root["c_max"]                 # N*mm
    jc = joint_capacity(d, ctx, mdl["H_box"])
    M_crash = ld["crash_factor"] * F_lim * L
    fuse_ratio = jc["M_cap"] / M_arm_cap
    dv = dovetail(cfg)
    self_lock = dv["friction_coefficient"] / dv["taper_per_side"]

    # prop download on the arm in the slipstream -> equivalent mass penalty
    prop = cfg["propulsion"]
    v = cfg["arm"]["slipstream_factor"] * op["v_induced_hover"]
    l_under = max(min(L, prop["prop_diameter"] / 2) - cfg["arm"]["pad_diameter"] / 2, 0.0)
    download_N = 0.5 * prop["air_density"] * v * v * cfg["arm"]["drag_coefficient"] * d["b"] * l_under * 1e-6
    download_g = download_N / G * 1e3

    res.update({
        "valid": True,
        "f_vert_Hz": float(fv[0]), "f_vert2_Hz": float(fv[1]), "f_lat_Hz": float(fl[0]), "f_tor_Hz": float(ft[0]),
        "tip_defl_mm": tip_defl_mm, "tip_slope_deg": tip_slope_deg,
        "sigma_ult_MPa": SF * sig_max, "sigma_crit_station": s_crit,
        "tau_ult_MPa": SF * tau_max, "goodman": goodman,
        "sigma_mean_MPa": sig_m, "sigma_alt_MPa": sig_a,
        "fuse_ratio": fuse_ratio, "M_arm_cap_Nm": M_arm_cap / 1e3, "M_crash_Nm": M_crash / 1e3,
        "joint_caps_Nm": {k: v / 1e3 for k, v in jc["caps_Nmm"].items()}, "joint_governing": jc["governing"],
        "joint_cap_Nm": jc["M_cap"] / 1e3, "self_locking_ratio": self_lock,
        "mass": mdl["mass"], "download_N": download_N, "download_g": download_g,
        "objective_g": mdl["mass"]["total_g"] + download_g,
        "joint": mdl["joint"], "H_box": mdl["H_box"],
    })
    arm = cfg["arm"]
    res["margins"] = {
        "f_vert": modal_margin(res["f_vert_Hz"], op),
        "f_lat": modal_margin(res["f_lat_Hz"], op),
        "f_tor": modal_margin(res["f_tor_Hz"], op),
        "static_strength": mat.S_static / res["sigma_ult_MPa"] - 1,
        "interlaminar": mat.tau_interlaminar / res["tau_ult_MPa"] - 1,
        "fatigue_goodman": (1 / SF) / goodman - 1,
        "tip_slope": ld["tip_slope_limit_deg"] / max(tip_slope_deg, 1e-9) - 1,
        "joint_crash": jc["M_cap"] / M_crash - 1,
        "dovetail_fits": (d["h_root"] - 2 * d["t_f"] - dv["depth"]) / d["h_root"],
        "channel_w": tip.channel_w / arm["channel_min_width"] - 1,
        "channel_h": tip.channel_h / arm["channel_min_height"] - 1,
        "tip_height": d["h_tip"] / arm["min_tip_height"] - 1,
    }
    return res
