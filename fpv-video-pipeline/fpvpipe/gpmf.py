"""Minimal GoPro GPMF (telemetry) parser: enough to pull GYRO and ACCL out of a HERO file.

Format (github.com/gopro/gpmf-parser): a stream of KLV records, each with an 8-byte header
    FourCC key (4 bytes) | type (1 char) | struct size (1 byte) | repeat (uint16, big-endian)
followed by size*repeat bytes of big-endian payload, padded to a 4-byte boundary.
Type 0x00 means the payload is itself a list of KLV records (DEVC -> STRM -> ...).

Inside each STRM, SCAL comes before the sensor data and divides the raw integers into SI units:
GYRO -> rad/s, ACCL -> m/s^2. STMP (optional) is the payload start time in microseconds.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

import numpy as np

_NUMERIC = {
    "b": "b", "B": "B", "s": "h", "S": "H", "l": "i", "L": "I",
    "f": "f", "d": "d", "j": "q", "J": "Q",
}


@dataclass
class SensorTrack:
    key: str
    samples: list[np.ndarray] = field(default_factory=list)   # one (n, axes) block per payload
    stamps_us: list[int | None] = field(default_factory=list)  # STMP for each block
    order: str = ""                                           # ORIN axis order, e.g. "ZXY"
    strings: list[str] = field(default_factory=list)          # string-typed keys, e.g. GPSU UTC time

    def array(self) -> np.ndarray:
        return np.concatenate(self.samples) if self.samples else np.zeros((0, 3))


def _iter_klv(buf: bytes, start: int = 0, end: int | None = None):
    end = len(buf) if end is None else end
    i = start
    while i + 8 <= end:
        key = buf[i:i + 4]
        typ = chr(buf[i + 4])
        size = buf[i + 5]
        repeat = struct.unpack(">H", buf[i + 6:i + 8])[0]
        length = size * repeat
        payload_start = i + 8
        if payload_start + length > end:
            break  # truncated payload: stop rather than misread
        if key == b"\x00\x00\x00\x00":  # padding
            i += 4
            continue
        yield key.decode("latin-1"), typ, size, repeat, payload_start
        i = payload_start + ((length + 3) & ~3)


def _decode(buf: bytes, typ: str, size: int, repeat: int, off: int, type_str: str = "") -> np.ndarray | str | None:
    if typ in ("c", "U"):
        return buf[off:off + size * repeat].decode("latin-1").rstrip("\x00")
    if typ == "?":  # complex structure described by the stream's TYPE key, e.g. GPS9 = "lllllllSS"
        codes = "".join(_NUMERIC.get(c, "") for c in type_str)
        if not codes or len(codes) != len(type_str) or struct.calcsize(f">{codes}") != size:
            return None
        vals = struct.unpack(f">{codes * repeat}", buf[off:off + size * repeat])
        return np.asarray(vals, dtype=np.float64).reshape(repeat, len(codes))
    code = _NUMERIC.get(typ)
    if code is None:
        return None
    width = struct.calcsize(code)
    per_struct = size // width
    count = per_struct * repeat
    vals = struct.unpack(f">{count}{code}", buf[off:off + count * width])
    return np.asarray(vals, dtype=np.float64).reshape(repeat, per_struct)


def parse(buf: bytes, keys: tuple[str, ...] = ("GYRO", "ACCL")) -> dict[str, SensorTrack]:
    tracks = {k: SensorTrack(k) for k in keys}

    def walk(start: int, end: int):
        for key, typ, size, repeat, off in _iter_klv(buf, start, end):
            if typ == "\x00":
                if key == "STRM":
                    read_stream(off, off + size * repeat)
                else:
                    walk(off, off + size * repeat)

    def read_stream(start: int, end: int):
        scal, stamp, orin, type_str = None, None, "", ""
        for key, typ, size, repeat, off in _iter_klv(buf, start, end):
            if key == "TYPE":
                type_str = _decode(buf, typ, size, repeat, off)
            elif key == "SCAL":
                scal = _decode(buf, typ, size, repeat, off)
            elif key == "STMP":
                stamp = int(_decode(buf, typ, size, repeat, off).ravel()[0])
            elif key == "ORIN":
                orin = _decode(buf, typ, size, repeat, off)
            elif key in tracks:
                raw = _decode(buf, typ, size, repeat, off, type_str)
                if isinstance(raw, str):
                    tracks[key].strings.append(raw)
                    continue
                if not isinstance(raw, np.ndarray):
                    continue
                if scal is not None:
                    s = np.asarray(scal).ravel()
                    raw = raw / (s if s.size == raw.shape[1] else s[0])
                tracks[key].samples.append(raw)
                tracks[key].stamps_us.append(stamp)
                if isinstance(orin, str) and orin:
                    tracks[key].order = orin

    walk(0, len(buf))
    return tracks


def timeline(track: SensorTrack, duration_s: float) -> np.ndarray:
    """Per-sample time in seconds. Uses STMP when every block has one, else spreads samples evenly."""
    n_total = sum(len(b) for b in track.samples)
    if n_total == 0:
        return np.zeros(0)
    mean_dt = duration_s / n_total
    if all(s is not None for s in track.stamps_us) and len(track.samples) > 1:
        t0 = track.stamps_us[0]
        out = []
        for idx, block in enumerate(track.samples):
            start = (track.stamps_us[idx] - t0) / 1e6
            if idx + 1 < len(track.samples):
                span = (track.stamps_us[idx + 1] - t0) / 1e6 - start
            else:
                span = len(block) * mean_dt
            out.append(start + np.arange(len(block)) * span / len(block))
        return np.concatenate(out)
    return np.arange(n_total) * mean_dt
