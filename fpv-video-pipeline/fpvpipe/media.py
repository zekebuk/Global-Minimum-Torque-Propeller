"""ffprobe / ffmpeg helpers and GoPro file-name conventions."""
from __future__ import annotations

import json
import re
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

# GoPro HERO6+ naming: G + encoding (X = HEVC, H = AVC) + 2-digit chapter + 4-digit recording number.
# A recording longer than ~4 GB is split into chapters: GX010042, GX020042, GX030042 ...
GOPRO_NAME = re.compile(r"^G([XHLS])(\d{2})(\d{4})\.(MP4|LRV|THM)$", re.IGNORECASE)


@dataclass(frozen=True)
class GoProName:
    encoding: str
    chapter: int
    recording: int
    ext: str


def parse_gopro_name(name: str) -> GoProName | None:
    m = GOPRO_NAME.match(name)
    if not m:
        return None
    return GoProName(m.group(1).upper(), int(m.group(2)), int(m.group(3)), m.group(4).upper())


def ffprobe(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    return json.loads(out)


def video_summary(probe: dict) -> dict:
    """The fields that matter for this pipeline: size, fps, bit depth, transfer, duration, gyro presence."""
    v = next((s for s in probe["streams"] if s.get("codec_type") == "video"), {})
    num, _, den = v.get("avg_frame_rate", "0/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 0.0
    pix = v.get("pix_fmt", "")
    return {
        "codec": v.get("codec_name", ""),
        "width": v.get("width", 0),
        "height": v.get("height", 0),
        "fps": round(fps, 3),
        "pix_fmt": pix,
        "ten_bit": "10" in pix,
        "duration_s": float(probe.get("format", {}).get("duration", 0.0)),
        "creation_time": probe.get("format", {}).get("tags", {}).get("creation_time", ""),
        "has_gpmf": gpmf_stream_index(probe) is not None,
    }


def gpmf_stream_index(probe: dict) -> int | None:
    """GoPro telemetry lives in a data stream tagged 'gpmd' (handler 'GoPro MET')."""
    for s in probe["streams"]:
        if s.get("codec_tag_string") == "gpmd" or "GoPro MET" in s.get("tags", {}).get("handler_name", ""):
            return int(s["index"])
    return None


def extract_gpmf(path: Path) -> bytes:
    # Fast path: seek to the telemetry samples via the MP4 index (no full-file read; matters on archive HDDs).
    from . import mp4
    try:
        data = mp4.read_track(path, ("gpmd",))
        if data:
            return data
    except (OSError, struct.error, TypeError, ValueError):
        pass
    idx = gpmf_stream_index(ffprobe(path))
    if idx is None:
        raise ValueError(f"{path.name}: no GPMF telemetry stream (not a GoPro file, or stripped by re-encode)")
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{idx}", "-codec", "copy", "-f", "rawvideo", "-"],
        check=True, capture_output=True,
    ).stdout


def decode_audio(path: Path, sr: int = 22050):
    """Decode any audio/video file to mono float32 at `sr` Hz using ffmpeg."""
    import numpy as np

    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"],
        check=True, capture_output=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.float32), sr
