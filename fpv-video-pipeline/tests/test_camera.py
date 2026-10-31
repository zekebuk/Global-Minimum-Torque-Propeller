"""Camera identification and ND math, offline."""
import math
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import camera, exposure, gpmf  # noqa: E402
from tests.test_pipeline import klv, nest  # noqa: E402


# --- minimal protobuf encoder for building DJI-shaped messages
def varint(n: int) -> bytes:
    n &= (1 << 64) - 1
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def msg(num: int, payload: bytes) -> bytes:
    return varint(num << 3 | 2) + varint(len(payload)) + payload


def vint(num: int, v: int) -> bytes:
    return varint(num << 3) + varint(v)


def f32(num: int, v: float) -> bytes:
    return varint(num << 3 | 5) + struct.pack("<f", v)


def test_dji_header_and_frames():
    header = msg(1, msg(1, msg(6, b"01.02.0300") + msg(10, b"DJI Avata 2")) + msg(9, b""))  # EIS off (empty)
    header += msg(2, msg(3, vint(1, 3840) + vint(5, 10)))
    info = camera.ClipInfo(Path("x.mp4"))
    camera.dji_header(header, info)
    assert (info.model, info.firmware, info.bit_depth, info.in_camera_stabilization) == ("Avata 2", "01.02.0300", 10, "off")

    for iso, (num, den) in [(100.0, (1, 1000)), (200.0, (1, 500))]:
        exp_packed = msg(1, varint(num) + varint(den))           # packed repeated int32
        cam = msg(3, f32(1, iso)) + msg(4, exp_packed) + msg(6, vint(1, 5600))
        camera.dji_frame(msg(3, msg(2, cam)), info)
    assert info.iso == [100.0, 200.0]
    assert info.shutter_s == [0.001, 0.002]
    assert info.wb_k == [5600.0, 5600.0]

    rock = msg(1, msg(9, vint(1, 1)))
    camera.dji_header(rock, info)
    assert info.in_camera_stabilization == "RockSteady"


def test_gpmf_gps9_complex_type():
    # GPS9: lat, lon, alt, 2D, 3D speed, days since 2000, seconds since midnight, DOP, fix
    row = struct.pack(">lllllllHH", 476000000, -1223000000, 50000, 0, 0, 9800, 43200000, 100, 3)
    scal = struct.pack(">9l", 10000000, 10000000, 1000, 1000, 100, 1, 1000, 100, 1)
    strm = nest("STRM", klv("TYPE", "c", 1, 9, b"lllllllSS") + klv("SCAL", "l", 4, 9, scal) + klv("GPS9", "?", 32, 1, row))
    g = gpmf.parse(nest("DEVC", strm), keys=("GPS9",))["GPS9"].array()
    np.testing.assert_allclose(g[0, :2], [47.6, -122.3])
    assert g[0, 5] == 9800 and g[0, 6] == 43200 and g[0, 8] == 3


def test_ev_and_sun():
    # f/2.8, 1/1000, ISO 100 -> EV ~12.9
    assert abs(exposure.ev100(2.8, 1 / 1000, 100) - 12.94) < 0.01
    # Solar noon at the June solstice, 40N on the Greenwich meridian: 90 - 40 + 23.44 = 73.4 deg
    elev = exposure.sun_elevation_deg(40.0, 0.0, datetime(2026, 6, 21, 12, 2, tzinfo=timezone.utc))
    assert abs(elev - 73.4) < 0.7, elev
    # High sun, clear sky = "sunny 16" territory (EV ~15)
    assert 14.7 < exposure.clear_sky_ev(60) < 15.5
    assert exposure.clear_sky_ev(1) is None


def test_nd_recommendation_and_used_estimate():
    # 60p, AE chose 1/1920 at ISO 100 with no filter -> target 1/120 needs 4 stops -> ND16
    r = exposure.recommend(1 / 1920, 100, 60, nd_on_stops=0)
    assert r["recommended_nd"] == "ND16" and r["target_shutter"] == "1/120"
    # Same light seen through an ND8: AE shows 1/240, so 1 more stop is needed -> ND16 total
    r = exposure.recommend(1 / 240, 100, 60, nd_on_stops=3)
    assert r["recommended_nd"] == "ND16" and abs(r["extra_stops"] - 1) < 1e-9
    # Round trip: scene EV 15 seen through ND16 (4 stops) at f/2.5 -> recovered ND = 4 stops
    t = 2.5 ** 2 / 2 ** (15 - 4)
    assert abs(15 - exposure.ev100(2.5, t, 100) - 4) < 1e-9
    assert exposure.nd_name(math.log2(32)) == "ND32"


def test_parse_nd_arg():
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    from camera_report import parse_nd
    assert parse_nd("ND16") == 4 and parse_nd("none") == 0 and parse_nd("2.5") == 2.5
