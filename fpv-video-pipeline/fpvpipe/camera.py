"""Work out which camera shot a clip, how it was set up, and what exposure it actually used.

Sources, by camera family (all read straight from the file, nothing guessed from file names unless nothing else exists):
  GoPro (HERO5+, Mission 1)   moov/udta 'GPMF' box: MINF model, FMWR firmware, PTCL color profile, VFOV lens,
                              EISE/EISA in-camera stabilization, PTWB, PIMN/PIMX ISO limits, EXPT shutter mode.
                              Telemetry track: SHUT exposure time (s), ISOE ISO, WBAL kelvin, GPS5/GPS9 + GPSU.
  DJI (Avata 2, O4, Action)   'djmd' protobuf track: product name, firmware, bit depth, frame rate; per frame ISO,
                              exposure time (rational), white balance. Optional .SRT sidecar: iso/shutter/fnum/GPS.
                              The Avata 2 schema has no color-profile or aperture field, so D-Log M is inferred
                              (10-bit + low-contrast image) and aperture comes from the camera table.
  Anything else               ffprobe tags (make/model/encoder) + image statistics.
"""
from __future__ import annotations

import json
import re
import struct
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import gpmf, media, protobuf

# Fixed-aperture lenses. Values marked None are unverified; set them in config.toml [cameras] to get ND estimates.
CAMERAS = {
    "HERO13 Black": {"f_number": 2.5, "base_iso": 100},
    "HERO12 Black": {"f_number": 2.5, "base_iso": 100},
    "HERO11 Black": {"f_number": 2.5, "base_iso": 100},
    "HERO10 Black": {"f_number": 2.8, "base_iso": 100},
    "HERO9 Black": {"f_number": 2.8, "base_iso": 100},
    "HERO8 Black": {"f_number": 2.8, "base_iso": 100},
    "HERO7 Black": {"f_number": 2.8, "base_iso": 100},
    "HERO6 Black": {"f_number": 2.8, "base_iso": 100},
    "Mission 1 Pro": {"f_number": None, "base_iso": 100},
    "Mission 1": {"f_number": None, "base_iso": 100},
    "Avata 2": {"f_number": 2.8, "base_iso": 100},
}


@dataclass
class ClipInfo:
    path: Path
    brand: str = "unknown"
    model: str = "unknown"
    firmware: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bit_depth: int = 8
    duration_s: float = 0.0
    profile: str = "unknown"          # GP-Log / Flat / Standard / D-Log M / HLG / Normal
    profile_source: str = ""          # metadata | image statistics
    lens: str = ""
    in_camera_stabilization: str = ""
    shutter_s: list[float] = field(default_factory=list)
    iso: list[float] = field(default_factory=list)
    wb_k: list[float] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    utc: datetime | None = None
    time_source: str = ""
    settings: dict = field(default_factory=dict)
    image: dict = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- MP4 box walking (no full-file reads: clips are GBs)

def _boxes(f, start: int, end: int):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        hdr = f.read(8)
        if len(hdr) < 8:
            return
        size, typ = struct.unpack(">I4s", hdr)
        hdr_len = 8
        if size == 1:
            size = struct.unpack(">Q", f.read(8))[0]
            hdr_len = 16
        elif size == 0:
            size = end - pos
        if size < hdr_len:
            return
        yield typ.decode("latin-1"), pos + hdr_len, pos + size
        pos += size


def _find_box(f, start, end, *names):
    for typ, a, b in _boxes(f, start, end):
        if typ == names[0]:
            return (a, b) if len(names) == 1 else _find_box(f, a, b, *names[1:])
    return None


def gopro_settings(path: Path) -> dict:
    with open(path, "rb") as f:
        size = path.stat().st_size
        loc = _find_box(f, 0, size, "moov", "udta", "GPMF")
        if not loc:
            return {}
        f.seek(loc[0])
        buf = f.read(loc[1] - loc[0])
    out: dict = {}

    def walk(s, e):
        for k, t, sz, r, o in gpmf._iter_klv(buf, s, e):
            if t == "\x00":
                walk(o, o + sz * r)
            elif k not in out:
                v = gpmf._decode(buf, t, sz, r, o)
                if isinstance(v, np.ndarray):
                    v = v.ravel().tolist()
                    v = v[0] if len(v) == 1 else v
                out[k] = v
    walk(0, len(buf))
    return out


# ---------------------------------------------------------------- image statistics (works for every camera)

def image_stats(path: Path, duration_s: float, n: int = 5) -> dict:
    lumas, sats = [], []
    for k in range(n):
        t = duration_s * (k + 0.5) / n
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1",
             "-vf", "scale=192:-2", "-pix_fmt", "rgb24", "-f", "rawvideo", "-"],
            capture_output=True).stdout
        if not raw:
            continue
        px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.float64) / 255.0
        lumas.append(px @ [0.2126, 0.7152, 0.0722])
        mx, mn = px.max(axis=1), px.min(axis=1)
        sats.append(np.where(mx > 0, (mx - mn) / np.maximum(mx, 1e-6), 0))
    if not lumas:
        return {}
    y, s = np.concatenate(lumas), np.concatenate(sats)
    p1, p50, p99 = np.percentile(y, [1, 50, 99])
    return {
        "luma_p1": round(float(p1), 3), "luma_median": round(float(p50), 3), "luma_p99": round(float(p99), 3),
        "clipped_frac": round(float(np.mean(y > 0.98)), 4), "crushed_frac": round(float(np.mean(y < 0.02)), 4),
        "saturation_mean": round(float(s.mean()), 3),
        # A log curve lifts blacks and compresses highlights: narrow range, raised floor, low saturation.
        "looks_log": bool((p99 - p1) < 0.72 and p1 > 0.04 and s.mean() < 0.35),
    }


# ---------------------------------------------------------------- GoPro

def _gopro(info: ClipInfo):
    st = gopro_settings(info.path)
    info.settings = st
    info.brand = "GoPro"
    info.model = str(st.get("MINF") or info.model)
    info.firmware = str(st.get("FMWR", ""))
    color = str(st.get("PTCL", "")).upper()
    if color:
        is_log2 = "LOG2" in color.replace("-", "").replace(" ", "") or "MISSION" in info.model.upper()
        info.profile = (("GP-Log2" if is_log2 else "GP-Log") if "LOG" in color else "Flat" if "FLAT" in color
                        else "Natural" if "NATURAL" in color else "Standard")
        info.profile_source = f"metadata PTCL={color}"
    lens = {"W": "Wide", "L": "Linear", "S": "SuperView", "H": "HyperView", "N": "Narrow", "X": "Max SuperView"}
    vf = str(st.get("VFOV", ""))
    info.lens = lens.get(vf, vf)
    if st.get("EISE"):
        info.in_camera_stabilization = "off" if st["EISE"] == "N" else str(st.get("EISA") or "on")
    try:
        tr = gpmf.parse(media.extract_gpmf(info.path), keys=("SHUT", "ISOE", "WBAL", "GPS5", "GPS9", "GPSU", "GPSF", "DVNM"))
    except Exception as e:
        info.notes.append(f"telemetry unreadable: {e}")
        return
    info.shutter_s = tr["SHUT"].array().ravel().tolist()
    info.iso = tr["ISOE"].array().ravel().tolist()
    info.wb_k = tr["WBAL"].array().ravel().tolist()
    g9, g5 = tr["GPS9"].array(), tr["GPS5"].array()
    if g9.size and g9.shape[1] >= 9:
        ok = (g9[:, 8] >= 2) & (np.abs(g9[:, 0]) > 0.01)
        if ok.any():
            row = g9[ok][len(g9[ok]) // 2]
            info.lat, info.lon = float(row[0]), float(row[1])
            info.utc = datetime(2000, 1, 1, tzinfo=timezone.utc) + timedelta(days=float(row[5]), seconds=float(row[6]))
            info.time_source = "GPS9"
    elif g5.size:
        # GPS5 carries no fix flag of its own: GPSF (one value per payload) says whether that block had a 2D/3D lock.
        # Without a lock the camera still writes coordinates, and they are garbage (seen: altitude 9.5 km at sea).
        fix_blocks = tr["GPSF"].samples
        if len(fix_blocks) == len(tr["GPS5"].samples):
            ok = np.concatenate([np.full(len(b), float(f.ravel()[0]) >= 2)
                                 for b, f in zip(tr["GPS5"].samples, fix_blocks)])
        else:
            ok = np.full(len(g5), bool(fix_blocks) and all(float(f.ravel()[0]) >= 2 for f in fix_blocks))
        ok &= (np.abs(g5[:, 0]) > 0.01) & (np.abs(g5[:, 2]) < 9000)
        if ok.any():
            row = g5[ok][len(g5[ok]) // 2]
            info.lat, info.lon = float(row[0]), float(row[1])
            if tr["GPSU"].strings:
                info.utc = _parse_gpsu(tr["GPSU"].strings[len(tr["GPSU"].strings) // 2])
                info.time_source = "GPSU"
    if info.lat is None and (g5.size or g9.size):
        info.notes.append("GPS was on but had no lock: position ignored. Pass --lat/--lon for the ND-used estimate")
    elif info.lat is None:
        info.notes.append("no GPS fix in telemetry (GPS off or no lock): pass --lat/--lon for the ND-used estimate")


def _parse_gpsu(s: str) -> datetime | None:
    try:
        return datetime.strptime(s[:12], "%y%m%d%H%M%S").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


# ---------------------------------------------------------------- DJI

# ClipMeta.eis_status enum (telemetry-parser dvtm_wm169 schema, which covers the Avata 2).
DJI_EIS = {0: "off", 1: "RockSteady", 2: "HorizonSteady", 3: "HyperSmooth-like", 4: "Tradeoff",
           5: "HorizonBalancing", 6: "Deepspace", 7: "off (with crop)"}

def _dji(info: ClipInfo, probe: dict, idx: int):
    info.brand = "DJI"
    from . import mp4
    try:
        packets = mp4.track_samples(info.path, ("djmd",))
    except (OSError, struct.error, TypeError, ValueError):
        packets = []
    if not packets:
        packets = _dji_packets_ffprobe(info.path, idx)
    if not packets:
        info.notes.append("djmd track present but no packets readable")
        return
    step = max(1, len(packets) // 300)  # ~300 frames is plenty for exposure statistics
    with open(info.path, "rb") as f:
        def read(p):
            f.seek(p[0])
            return f.read(p[1])
        dji_header(read(packets[0]), info)
        for p in packets[::step]:
            dji_frame(read(p), info)
    _dji_srt(info)


def _dji_packets_ffprobe(path: Path, idx: int) -> list[tuple[int, int]]:
    # JSON, not CSV: ffprobe prints fields in its own order, not the order requested.
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", str(idx), "-show_entries", "packet=pos,size",
                          "-of", "json", str(path)], capture_output=True, text=True).stdout
    return [(int(p["pos"]), int(p["size"])) for p in json.loads(out or "{}").get("packets", [])
            if str(p.get("pos", "")).isdigit() and str(p.get("size", "")).isdigit()]


def dji_header(first: bytes, info: ClipInfo):
    """First djmd sample: ProductMeta{1: ClipMeta{1: header{6 firmware, 10 product}, 9: EIS}, 2: StreamMeta{3: video}}."""
    hdr = protobuf.path(first, 1, 1)
    if hdr:
        fl = protobuf.fields(hdr)
        if 10 in fl:
            info.model = fl[10][0].decode("utf-8", "replace").replace("DJI ", "")
        if 6 in fl:
            info.firmware = fl[6][0].decode("utf-8", "replace")
    eis = protobuf.path(first, 1, 9)
    if eis is not None:
        code = int(protobuf.fields(eis).get(1, [0])[0]) if eis else 0  # proto3 omits 0 = off
        info.in_camera_stabilization = DJI_EIS.get(code, f"mode {code}")
    vsm = protobuf.path(first, 2, 3)
    if vsm:
        fl = protobuf.fields(vsm)
        if 5 in fl:
            info.bit_depth = int(fl[5][0]) or info.bit_depth


def dji_frame(buf: bytes, info: ClipInfo):
    """Per-frame: ProductMeta{3: FrameMeta{2: Camera{3: Iso{1 float}, 4: ExposureTime{1 [num, den]}, 6: WB{1 K}}}}."""
    cam = protobuf.path(buf, 3, 2)
    if not cam:
        return
    iso = protobuf.path(cam, 3)
    if iso and 1 in (fl := protobuf.fields(iso)):
        info.iso.append(protobuf.as_float(fl[1][0]))
    exp = protobuf.path(cam, 4)
    if exp:
        r = protobuf.repeated_ints(exp, 1)
        if len(r) >= 2 and r[1]:
            info.shutter_s.append(r[0] / r[1])
    wb = protobuf.path(cam, 6)
    if wb and 1 in (fl := protobuf.fields(wb)):
        info.wb_k.append(float(fl[1][0]))


def _dji_srt(info: ClipInfo):
    srt = next((p for p in (info.path.with_suffix(".SRT"), info.path.with_suffix(".srt")) if p.exists()), None)
    if not srt:
        return
    text = srt.read_text(errors="replace")
    kv = re.findall(r"\[\s*(\w+)\s*:\s*([^\]\s]+)[^\]]*\]", text)
    vals: dict[str, list[str]] = {}
    for k, v in kv:
        vals.setdefault(k.lower(), []).append(v)
    if not info.iso and "iso" in vals:
        info.iso = [float(v) for v in vals["iso"]]
    if not info.shutter_s and "shutter" in vals:
        info.shutter_s = [eval_fraction(v) for v in vals["shutter"]]
    if "fnum" in vals:
        info.settings["fnum"] = float(vals["fnum"][0]) / (100.0 if float(vals["fnum"][0]) > 50 else 1.0)
    if "color_md" in vals:
        info.settings["color_md"] = vals["color_md"][0]
    if "latitude" in vals and "longitude" in vals:
        lat, lon = float(vals["latitude"][len(vals["latitude"]) // 2]), float(vals["longitude"][len(vals["longitude"]) // 2])
        if abs(lat) > 0.01:
            info.lat, info.lon = lat, lon
    info.notes.append(f"read sidecar {srt.name}")


def eval_fraction(s: str) -> float:
    if "/" in s:
        a, b = s.split("/", 1)
        return float(a) / float(b)
    return float(s)


# ---------------------------------------------------------------- entry point

def identify(path: Path, look: bool = True) -> ClipInfo:
    """look=False skips the 5-frame image statistics (decode-heavy); used by the archive scanner."""
    path = Path(path)
    probe = media.ffprobe(path)
    s = media.video_summary(probe)
    info = ClipInfo(path, width=s["width"], height=s["height"], fps=s["fps"],
                    bit_depth=10 if s["ten_bit"] else 8, duration_s=s["duration_s"])
    tags = {k.lower(): str(v) for k, v in probe.get("format", {}).get("tags", {}).items()}
    djmd = next((int(st["index"]) for st in probe["streams"] if st.get("codec_tag_string") == "djmd"
                 or "DJI meta" in st.get("tags", {}).get("handler_name", "")), None)
    if media.gpmf_stream_index(probe) is not None or "gopro" in tags.get("encoder", "").lower():
        _gopro(info)
    elif djmd is not None:
        _dji(info, probe, djmd)
    else:
        info.brand = tags.get("make") or tags.get("com.apple.quicktime.make") or ("DJI" if path.name.upper().startswith("DJI_") else "unknown")
        info.model = tags.get("model") or tags.get("com.apple.quicktime.model") or tags.get("encoder", "unknown")
        info.notes.append("no camera telemetry track: exposure unknown")
    if info.utc is None and tags.get("creation_time"):
        try:
            info.utc = datetime.fromisoformat(tags["creation_time"].replace("Z", "+00:00"))
            info.time_source = "file creation_time"
            if info.brand == "GoPro":
                info.notes.append("GoPro writes local camera time as creation_time; pass --utc-offset if no GPS")
        except ValueError:
            pass

    info.image = image_stats(path, info.duration_s) if look else {}
    if info.brand == "DJI" or info.profile == "unknown":
        log = info.image.get("looks_log")
        if info.brand == "DJI":
            info.profile = ("D-Log M" if log and info.bit_depth >= 10 else
                            "HLG or other 10-bit" if info.bit_depth >= 10 else "Normal (8-bit)")
            if "color_md" in info.settings:
                info.profile = f"{info.settings['color_md']} (SRT)"
        elif log is not None:
            info.profile = "log (inferred)" if log else "standard (inferred)"
        info.profile_source = info.profile_source or "image statistics + bit depth"
    elif not info.profile.startswith("GP-Log") and info.image.get("looks_log") and info.bit_depth >= 10:
        info.notes.append(f"metadata says {info.profile} but the image looks log: check the camera's color setting")
    return info
