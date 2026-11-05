"""Mechanical design of the complete outrunner: parts, fits, axial stack-up and clearance checks.

Architecture (conventional FPV outrunner, "fixed-tube" type):

* **Base** (7075-T6) with a flange carrying the M3 mounting pattern and an integral centre tube.
  The stator is bonded onto the tube OD. Two deep-groove bearings sit in H6 bores at the tube's
  two ends, with a relief bore between them.
* **Shaft** (hardened 52100 / SUJ2): an Ø4 journal through both bearings, retained below by a
  DIN 6799 e-clip; a shoulder to an Ø5 section seats on the upper inner race. The bell hub is
  pressed onto the Ø5 section and locked with an M3 grub screw on a flat, and an M5x0.8 prop
  thread sits on top.
* **Rotor**: an aluminium bell (7075-T6) with a low-carbon steel back-iron ring pressed and
  bonded into its skirt, and 2p arc magnets bonded inside the ring (alternating N-in / S-in
  part numbers).

All dimensions are mm. The z axis is the shaft axis, with z = 0 at the base's bottom face.
Values derived from the electromagnetic design (``model.analyze``) are not re-chosen here.
Everything else (bearing size, wall thicknesses, clearances) is a documented mechanical
choice in ``MECH_RULES``.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field

from bldc_motor.design import geometry
from bldc_motor.design.model import DesignAnalysis

# Deep-groove ball bearings (ISO 15, 68 series / miniature): d x D x B, inner/outer land diameters.
BEARINGS = {
    "684ZZ": {"d": 4.0, "D": 9.0, "B": 4.0, "inner_land": 5.2, "outer_land": 7.8, "mass_g": 1.2},
    "685ZZ": {"d": 5.0, "D": 11.0, "B": 5.0, "inner_land": 6.4, "outer_land": 9.6, "mass_g": 2.0},
    "693ZZ": {"d": 3.0, "D": 8.0, "B": 4.0, "inner_land": 4.4, "outer_land": 6.8, "mass_g": 1.0},
}
# DIN 6799 retaining washers (e-clips): size -> groove diameter, groove width, shaft range.
E_CLIPS = {"3.2": {"groove_d": 3.2, "groove_w": 0.64, "shaft_min": 4.0, "shaft_max": 5.0, "thickness": 0.6}}

MECH_RULES = {
    "coat_mm": 0.20,  # epoxy powder coat on stator faces and slots (masked on the ID)
    "end_turn_extra_mm": 0.30,  # winding bulge beyond coil build at the end faces
    "end_turn_axial_gap_mm": 0.8,  # end turns -> bell plate
    "stator_seat_gap_mm": 0.5,  # end turns -> base flange shoulder
    "skirt_gap_mm": 0.6,  # bell skirt bottom -> base flange top
    "magnet_overhang_mm": 0.5,  # each end, magnet beyond the lamination stack
    "ring_shoulder_mm": 0.4,  # ring extends above magnets to seat on the bell shoulder
    "bell_skin_mm": 0.6,  # aluminium wall outside the steel ring
    "bell_plate_mm": 1.6,
    "hub_boss_height_mm": 2.5,  # boss above the plate carrying the grub screw
    "hub_boss_d_mm": 9.0,
    "flange_thickness_mm": 3.5,
    "mount_pattern_mm": 16.0,  # M3 square pattern, typical for 22xx-24xx FPV motors
    "mount_thread_depth_mm": 2.6,  # blind M3, >= 0.8 mm floor protects the end turns from long screws
    "prop_thread_length_mm": 9.0,
    "bearing": "684ZZ",
    "e_clip": "3.2",
    "upper_shaft_d_mm": 5.0,
    "lead_awg": 20,
    "lead_length_mm": 150.0,
    "vent_holes": 6,
    "vent_hole_d_mm": 5.0,
    "base_windows": 4,  # lightening / cooling windows between the mount bosses; leads exit through W1
    "base_window_half_angle_deg": 25.0,
    "base_window_inner_wall_mm": 1.5,  # tube -> window
    "base_window_rim_mm": 1.4,  # window -> flange edge
    "grub_screw": "M3x3 ISO 4029 cup point",
}


@dataclass
class MechanicalDesign:
    name: str
    design: dict
    dims: dict
    checks: list = field(default_factory=list)  # (name, value, limit, ok)

    @property
    def ok(self) -> bool:
        return all(c[3] for c in self.checks)

    def to_dict(self) -> dict:
        return asdict(self)


def derive(analysis: DesignAnalysis, name: str = "motor", rules: dict | None = None) -> MechanicalDesign:
    r = {**MECH_RULES, **(rules or {})}
    d = analysis.design
    g = analysis.geometry
    i = analysis.internals
    brg = BEARINGS[r["bearing"]]
    clip = E_CLIPS[r["e_clip"]]
    slots, poles = d["slot_pole"]
    mm = 1e3

    stator_od = 2 * g.r_s * mm
    stator_id = 2 * g.r_i * mm
    stack = d["stack_mm"]
    coat = r["coat_mm"]
    end_turn = i["coil_build_mm"] + coat + r["end_turn_extra_mm"]  # axial winding overhang per end
    # ---- axial stack-up (z up from the base bottom face) ----
    z_flange_top = r["flange_thickness_mm"]
    z_stator_bottom = z_flange_top + end_turn + r["stator_seat_gap_mm"]  # lamination bottom face
    z_stator_top = z_stator_bottom + stack
    z_tube_top = z_stator_top + coat
    z_endturn_top = z_stator_top + end_turn
    z_mag_bottom = z_stator_bottom - r["magnet_overhang_mm"]
    z_mag_top = z_stator_top + r["magnet_overhang_mm"]
    magnet_length = z_mag_top - z_mag_bottom
    z_skirt_bottom = z_flange_top + r["skirt_gap_mm"]
    z_ring_bottom = z_skirt_bottom
    z_ring_top = z_mag_top + r["ring_shoulder_mm"]
    z_plate_bottom = max(z_endturn_top + r["end_turn_axial_gap_mm"], z_ring_top + 0.5)
    z_plate_top = z_plate_bottom + r["bell_plate_mm"]
    z_hub_top = z_plate_top + r["hub_boss_height_mm"]
    z_shoulder = z_tube_top  # Ø4 -> Ø5 shoulder seats on the upper inner race (flush with tube top)
    z_lower_bearing_top = brg["B"]
    z_upper_bearing_bottom = z_tube_top - brg["B"]
    z_clip_groove = -0.1 - clip["groove_w"] / 2.0  # just below the lower inner race (+0.1 shim)
    z_shaft_bottom = z_clip_groove - clip["groove_w"] / 2.0 - 0.7
    z_shaft_top = z_hub_top + r["prop_thread_length_mm"]

    ring_id = 2 * g.r_m_out * mm
    ring_od = 2 * g.r_bell_out * mm
    bell_od = ring_od + 2 * r["bell_skin_mm"]
    magnet_ri = g.r_m_in * mm
    magnet_ro = g.r_m_out * mm
    magnet_arc_deg = d["magnet_arc"] * 360.0 / poles
    tube_od = stator_id - 0.04  # bonded slip fit: 0.02-0.09 mm diametral clearance
    # Flange just large enough for the M3 bosses (hole radius 1.25 + 1.6 mm wall), never wider than the bell.
    flange_d = min(bell_od - 1.0, 2.0 * (r["mount_pattern_mm"] / math.sqrt(2) + 1.25 + 1.6))
    # Coils sit radially between the slot bottom and the tooth shoe (their build is tangential), so
    # the end turns are bounded radially by the shoe inner diameter plus coat and bulge.
    endturn_od = 2 * g.r2 * mm + 2 * (coat + r["end_turn_extra_mm"])
    endturn_id = 2 * g.r1 * mm - 2 * coat

    dims = {
        "slots": slots,
        "poles": poles,
        "stator_od": stator_od,
        "stator_id": stator_id,
        "stack": stack,
        "lamination_t": d["lamination_mm"],
        "lamination_count": i["lamination_count"],
        "coat": coat,
        "tooth_width": i["tooth_width_mm"],
        "slot_opening": d["slot_opening_mm"],
        "slot_bottom_d": 2 * g.r1 * mm,
        "shoe_inner_d": 2 * g.r2 * mm,
        "tip_height": geometry.TOOTH_TIP_HEIGHT_MM,
        "end_turn_overhang": end_turn,
        "endturn_od": endturn_od,
        "endturn_id": endturn_id,
        "magnet_ri": magnet_ri,
        "magnet_ro": magnet_ro,
        "magnet_thickness": d["magnet_mm"],
        "magnet_arc_deg": magnet_arc_deg,
        "magnet_length": magnet_length,
        "magnet_grade": d["magnet_grade"],
        "airgap": d["airgap_mm"],
        "ring_id": ring_id,
        "ring_od": ring_od,
        "ring_length": z_ring_top - z_ring_bottom,
        "bell_od": bell_od,
        "bell_skin": r["bell_skin_mm"],
        "bell_plate": r["bell_plate_mm"],
        "bell_inner_relief_d": min(ring_id - 1.0, max(endturn_od + 2.0, stator_od + 1.0)),
        "hub_bore": r["upper_shaft_d_mm"],
        "hub_boss_d": r["hub_boss_d_mm"],
        "vent_holes": r["vent_holes"],
        "vent_hole_d": r["vent_hole_d_mm"],
        "vent_pcd": 0.5 * (r["hub_boss_d_mm"] + min(ring_id - 1.0, stator_od)) + 0.5,
        "flange_d": flange_d,
        "flange_thickness": r["flange_thickness_mm"],
        "mount_pattern": r["mount_pattern_mm"],
        "mount_thread_depth": r["mount_thread_depth_mm"],
        "tube_od": tube_od,
        "bearing": r["bearing"],
        "bearing_d": brg["d"],
        "bearing_D": brg["D"],
        "bearing_B": brg["B"],
        "bearing_relief_d": brg["outer_land"] - 0.4,
        "shaft_d": brg["d"],
        "shaft_upper_d": r["upper_shaft_d_mm"],
        "e_clip": r["e_clip"],
        "clip_groove_d": clip["groove_d"],
        "clip_groove_w": clip["groove_w"],
        "lead_awg": r["lead_awg"],
        "lead_length": r["lead_length_mm"],
        "base_windows": r["base_windows"],
        "base_window_half_angle_deg": r["base_window_half_angle_deg"],
        "base_window_r_in": tube_od / 2 + r["base_window_inner_wall_mm"],
        "base_window_r_out": flange_d / 2 - r["base_window_rim_mm"],
        "grub_screw": r["grub_screw"],
        "z": {
            "flange_top": z_flange_top,
            "stator_bottom": z_stator_bottom,
            "stator_top": z_stator_top,
            "tube_top": z_tube_top,
            "endturn_top": z_endturn_top,
            "mag_bottom": z_mag_bottom,
            "mag_top": z_mag_top,
            "skirt_bottom": z_skirt_bottom,
            "ring_bottom": z_ring_bottom,
            "ring_top": z_ring_top,
            "plate_bottom": z_plate_bottom,
            "plate_top": z_plate_top,
            "hub_top": z_hub_top,
            "shoulder": z_shoulder,
            "lower_bearing_top": z_lower_bearing_top,
            "upper_bearing_bottom": z_upper_bearing_bottom,
            "clip_groove": z_clip_groove,
            "shaft_bottom": z_shaft_bottom,
            "shaft_top": z_shaft_top,
            "grub": z_plate_top + r["hub_boss_height_mm"] / 2.0,
        },
    }
    dims["shaft_length"] = z_shaft_top - z_shaft_bottom
    dims["overall_height_no_shaft"] = z_hub_top
    mech = MechanicalDesign(name=name, design=d, dims=dims)
    _check(mech)
    return mech


def _check(m: MechanicalDesign) -> None:
    d, z = m.dims, m.dims["z"]
    checks = [
        ("tube wall around bearing [mm]", (d["tube_od"] - d["bearing_D"]) / 2, ">= 1.0", (d["tube_od"] - d["bearing_D"]) / 2 >= 1.0),
        ("bearing span / shaft d", (z["upper_bearing_bottom"] - z["lower_bearing_top"] + d["bearing_B"]) / d["shaft_d"], ">= 1.5", (z["upper_bearing_bottom"] - z["lower_bearing_top"] + d["bearing_B"]) / d["shaft_d"] >= 1.5),
        ("bearings do not overlap [mm]", z["upper_bearing_bottom"] - z["lower_bearing_top"], ">= 1.0", z["upper_bearing_bottom"] - z["lower_bearing_top"] >= 1.0),
        ("end turns -> bell plate [mm]", z["plate_bottom"] - z["endturn_top"], ">= 0.5", z["plate_bottom"] - z["endturn_top"] >= 0.5),
        ("end turns -> flange [mm]", z["stator_bottom"] - d["end_turn_overhang"] - z["flange_top"], ">= 0.3", z["stator_bottom"] - d["end_turn_overhang"] - z["flange_top"] >= 0.3),
        ("skirt -> flange gap [mm]", z["skirt_bottom"] - z["flange_top"], ">= 0.4", z["skirt_bottom"] - z["flange_top"] >= 0.4),
        ("end turns -> bell relief radial [mm]", (d["bell_inner_relief_d"] - d["endturn_od"]) / 2, ">= 0.5", (d["bell_inner_relief_d"] - d["endturn_od"]) / 2 >= 0.5),
        ("magnet -> stator airgap [mm]", d["magnet_ri"] - d["stator_od"] / 2, ">= 0.25", d["magnet_ri"] - d["stator_od"] / 2 >= 0.25),
        ("mount threads -> stator floor [mm]", d["flange_thickness"] - d["mount_thread_depth"], ">= 0.8", d["flange_thickness"] - d["mount_thread_depth"] >= 0.8),
        ("mount holes inside flange [mm]", d["flange_d"] / 2 - d["mount_pattern"] / math.sqrt(2) - 1.5, ">= 1.0", d["flange_d"] / 2 - d["mount_pattern"] / math.sqrt(2) - 1.5 >= 1.0),
        ("mount holes clear the tube [mm]", d["mount_pattern"] / math.sqrt(2) - 1.5 - d["tube_od"] / 2, ">= 0.5", d["mount_pattern"] / math.sqrt(2) - 1.5 - d["tube_od"] / 2 >= 0.5),
        ("vent holes between hub and relief [mm]", (d["vent_pcd"] - d["vent_hole_d"]) / 2 - d["hub_boss_d"] / 2, ">= 0.5", (d["vent_pcd"] - d["vent_hole_d"]) / 2 - d["hub_boss_d"] / 2 >= 0.5),
        ("web between window and M3 hole [mm]", _window_web(d), ">= 1.5", _window_web(d) >= 1.5),
        ("window radial width (lead exit, 3 x 20AWG) [mm]", d["base_window_r_out"] - d["base_window_r_in"], ">= 3.5", d["base_window_r_out"] - d["base_window_r_in"] >= 3.5),
        ("e-clip fits shaft", d["shaft_d"], f"DIN 6799 {d['e_clip']} range", E_CLIPS[d["e_clip"]]["shaft_min"] <= d["shaft_d"] <= E_CLIPS[d["e_clip"]]["shaft_max"]),
    ]  # fmt: skip
    m.checks = checks


def _window_web(d: dict) -> float:
    """Shortest distance from a window edge (at +/- half angle around 0/90/...) to an M3 hole (at 45 deg)."""

    r_hole = d["mount_pattern"] / math.sqrt(2)
    angle = math.radians(45.0 - d["base_window_half_angle_deg"])
    return r_hole * math.sin(angle) - 1.25


# ---- revolved part profiles (r, z), shared by the CAD solids and the section drawings ------------
def base_profile(m: MechanicalDesign) -> list[tuple[float, float]]:
    d, z = m.dims, m.dims["z"]
    r_f, r_t = d["flange_d"] / 2, d["tube_od"] / 2
    rb, rr, B = d["bearing_D"] / 2, d["bearing_relief_d"] / 2, d["bearing_B"]
    return [(rb, 0.0), (r_f, 0.0), (r_f, z["flange_top"]), (r_t, z["flange_top"]), (r_t, z["tube_top"]), (rb, z["tube_top"]),
            (rb, z["tube_top"] - B), (rr, z["tube_top"] - B), (rr, B), (rb, B)]  # fmt: skip


def shaft_profile(m: MechanicalDesign, chamfer: float = 0.3) -> list[tuple[float, float]]:
    d, z = m.dims, m.dims["z"]
    r4, r5, rg, gw, c = d["shaft_d"] / 2, d["shaft_upper_d"] / 2, d["clip_groove_d"] / 2, d["clip_groove_w"], chamfer
    return [(0.0, z["shaft_bottom"]), (r4 - c, z["shaft_bottom"]), (r4, z["shaft_bottom"] + c), (r4, z["clip_groove"] - gw / 2),
            (rg, z["clip_groove"] - gw / 2), (rg, z["clip_groove"] + gw / 2), (r4, z["clip_groove"] + gw / 2), (r4, z["shoulder"]),
            (r5, z["shoulder"]), (r5, z["shaft_top"] - c), (r5 - c, z["shaft_top"]), (0.0, z["shaft_top"])]  # fmt: skip


def bell_profile(m: MechanicalDesign) -> list[tuple[float, float]]:
    d, z = m.dims, m.dims["z"]
    ro, rbore, rrel = d["bell_od"] / 2, d["ring_od"] / 2, d["bell_inner_relief_d"] / 2
    rh, rboss = d["hub_bore"] / 2, d["hub_boss_d"] / 2
    return [(rbore, z["skirt_bottom"]), (ro, z["skirt_bottom"]), (ro, z["plate_top"]), (rboss, z["plate_top"]), (rboss, z["hub_top"]),
            (rh, z["hub_top"]), (rh, z["plate_bottom"]), (rrel, z["plate_bottom"]), (rrel, z["ring_top"]), (rbore, z["ring_top"])]  # fmt: skip


def ring_profile(m: MechanicalDesign) -> list[tuple[float, float]]:
    d, z = m.dims, m.dims["z"]
    return [
        (d["ring_id"] / 2, z["ring_bottom"]),
        (d["ring_od"] / 2, z["ring_bottom"]),
        (d["ring_od"] / 2, z["ring_top"]),
        (d["ring_id"] / 2, z["ring_top"]),
    ]


def bearing_profile(m: MechanicalDesign, z0: float) -> list[tuple[float, float]]:
    d = m.dims
    return [
        (d["bearing_d"] / 2, z0),
        (d["bearing_D"] / 2, z0),
        (d["bearing_D"] / 2, z0 + d["bearing_B"]),
        (d["bearing_d"] / 2, z0 + d["bearing_B"]),
    ]


# ---- tolerances / fits used on the drawings ------------------------------------------------
FITS = {
    "bearing_bore": ("H6", 0.000, +0.009),  # Ø9 housing, bearings retained with Loctite 641
    "shaft_journal": ("h5", -0.004, 0.000),  # Ø4 shaft in 684 inner ring (slide/push fit)
    "hub_bore": ("H7", 0.000, +0.012),  # Ø5 hub on Ø5 k6 shaft section: light press + grub screw
    "shaft_upper": ("k6", +0.001, +0.009),
    "ring_in_bell": ("H7/s6", 0.000, +0.021),  # ring OD s6 into bell bore H7, plus Loctite 638
    "ring_od": ("s6", +0.035, +0.048),
    "tube_od": ("-0.02/-0.05", -0.05, -0.02),  # vs nominal stator ID: bonded slip fit (Loctite 638)
    "stator_id": ("+0.03/0", 0.0, +0.03),
}
GENERAL_TOLERANCE = "ISO 2768-mK"
