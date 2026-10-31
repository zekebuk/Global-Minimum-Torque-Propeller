"""Schema-less protobuf wire-format reader (enough to walk DJI's `djmd` metadata by field number)."""
from __future__ import annotations

import struct


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = result = 0
    while True:
        b = buf[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 70:
            raise ValueError("bad varint")


def fields(buf: bytes) -> dict[int, list]:
    """field number -> list of raw values (int for varint, bytes for len-delimited/fixed)."""
    out: dict[int, list] = {}
    i, n = 0, len(buf)
    while i < n:
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        elif wt == 2:
            ln, i = _varint(buf, i)
            v, i = buf[i:i + ln], i + ln
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"unsupported wire type {wt}")
        if i > n:
            raise ValueError("truncated message")
        out.setdefault(num, []).append(v)
    return out


def path(buf: bytes, *nums: int) -> bytes | None:
    """Follow nested message field numbers, first occurrence at each level."""
    cur = buf
    for num in nums:
        try:
            vals = fields(cur).get(num)
        except (ValueError, IndexError):
            return None
        if not vals or not isinstance(vals[0], (bytes, bytearray)):
            return None
        cur = vals[0]
    return cur


def as_float(v) -> float:
    return struct.unpack("<f", v)[0]


def as_int32(v: int) -> int:
    return v - (1 << 64) if v >= 1 << 63 else v


def repeated_ints(msg: bytes, num: int) -> list[int]:
    """Repeated (u)int32 field, packed or not."""
    vals = fields(msg).get(num, [])
    out = []
    for v in vals:
        if isinstance(v, int):
            out.append(as_int32(v))
        else:  # packed
            i = 0
            while i < len(v):
                x, i = _varint(v, i)
                out.append(as_int32(x))
    return out
