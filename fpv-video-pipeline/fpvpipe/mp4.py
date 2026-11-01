"""Read one metadata track (GoPro 'gpmd', DJI 'djmd') straight from an MP4's sample tables.

Why: telemetry is a few hundred KB inside multi-GB files. ffmpeg demuxes (reads) the whole file to pull it out, which
on a spinning archive drive is ~30 s per 4 GB file. The moov box already lists every sample's byte offset and size
(stco/co64 + stsz + stsc), so we seek to exactly those bytes. Read-only; nothing is ever written to the source file.
"""
from __future__ import annotations

import struct
from pathlib import Path


def _boxes(f, start, end):
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        h = f.read(8)
        if len(h) < 8:
            return
        size, typ = struct.unpack(">I4s", h)
        hl = 8
        if size == 1:
            size, hl = struct.unpack(">Q", f.read(8))[0], 16
        elif size == 0:
            size = end - pos
        if size < hl:
            return
        yield typ.decode("latin-1"), pos + hl, pos + size
        pos += size


def _child(f, start, end, name):
    return next(((a, b) for t, a, b in _boxes(f, start, end) if t == name), None)


def _read(f, a, b):
    f.seek(a)
    return f.read(b - a)


def track_samples(path: Path, formats=("gpmd", "djmd")) -> list[tuple[int, int]]:
    """[(offset, size)] for every sample of the first track whose sample description format is in `formats`."""
    with open(path, "rb") as f:
        size = Path(path).stat().st_size
        moov = _child(f, 0, size, "moov")
        if not moov:
            return []
        for typ, a, b in _boxes(f, *moov):
            if typ != "trak":
                continue
            mdia = _child(f, a, b, "mdia")
            minf = mdia and _child(f, *mdia, "minf")
            stbl = minf and _child(f, *minf, "stbl")
            if not stbl:
                continue
            stsd = _child(f, *stbl, "stsd")
            if not stsd:
                continue
            d = _read(f, *stsd)
            fmt = d[12:16].decode("latin-1") if len(d) >= 16 else ""  # version/flags(4) count(4) size(4) format(4)
            if fmt not in formats:
                continue
            return _samples(f, stbl)
    return []


def _samples(f, stbl) -> list[tuple[int, int]]:
    stsz = _read(f, *_child(f, *stbl, "stsz"))
    fixed, count = struct.unpack(">II", stsz[4:12])
    sizes = [fixed] * count if fixed else list(struct.unpack(f">{count}I", stsz[12:12 + 4 * count]))
    co = _child(f, *stbl, "stco")
    if co:
        c = _read(f, *co)
        n = struct.unpack(">I", c[4:8])[0]
        chunks = list(struct.unpack(f">{n}I", c[8:8 + 4 * n]))
    else:
        c = _read(f, *_child(f, *stbl, "co64"))
        n = struct.unpack(">I", c[4:8])[0]
        chunks = list(struct.unpack(f">{n}Q", c[8:8 + 8 * n]))
    s = _read(f, *_child(f, *stbl, "stsc"))
    m = struct.unpack(">I", s[4:8])[0]
    runs = [struct.unpack(">III", s[8 + 12 * i:20 + 12 * i]) for i in range(m)]  # first_chunk, per_chunk, desc
    out, k = [], 0
    for ci, off in enumerate(chunks, start=1):
        per = next((r[1] for r in reversed(runs) if r[0] <= ci), 1)
        for _ in range(per):
            if k >= len(sizes):
                return out
            out.append((off, sizes[k]))
            off += sizes[k]
            k += 1
    return out


def read_track(path: Path, formats=("gpmd", "djmd")) -> bytes:
    samples = track_samples(path, formats)
    with open(path, "rb") as f:
        parts = []
        for off, n in samples:
            f.seek(off)
            parts.append(f.read(n))
    return b"".join(parts)
