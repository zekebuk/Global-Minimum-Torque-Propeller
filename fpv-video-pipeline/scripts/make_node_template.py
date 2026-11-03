#!/usr/bin/env python3
"""Make the empty N-node serial template .drx that build_reel_project.py --template loads (no clicking in Resolve).

    python scripts/make_node_template.py SEED.drx E:/ASSETS/fpv_5node_template.drx

Resolve's scripting API cannot add nodes, but it can apply a grade from a .drx still. A .drx is XML whose <Body>
holds the node graph as zstd-compressed protobuf (one 0x81 byte, then the zstd frame). This script takes a still of a
1-node grade (SEED: Grab Still on any clip with a single node, Gallery > Export > .drx; ExportStills(..., "drx") from a
script works too), clones that node N times into a serial chain, labels the nodes the way build_reel_project.py
looks them up, and writes the result. Field numbers were read off Resolve 21.1 stills:

  graph  1 last id used | 3 frame size | 7 node (repeated) | 8 edge (repeated) | 9 graph input | 10 graph output
  node   1 id | 2 number shown in the UI | 4 x | 5 y | 6 label | 9 tools (9.1 one entry per tool in use)
  edge   1 from id | 3 to id | 7 order          input/output  3.4 = id of the first / last node
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import zstandard

LABELS = ["1 PRIMARY", "2 TONE SAT", "3 LOOK", "4 CST", "5 UNIFY"]   # build_reel_project.NODE_LABELS matches these
FIRST_ID = 5                                                          # ids 3 and 4 are the graph input / output


def _varint(buf: bytes, i: int) -> tuple[int, int]:
    shift = out = 0
    while True:
        b = buf[i]
        i += 1
        out |= (b & 0x7F) << shift
        if not b & 0x80:
            return out, i
        shift += 7


def _enc_varint(v: int) -> bytes:
    out = bytearray()
    while True:
        b, v = v & 0x7F, v >> 7
        out.append(b | (0x80 if v else 0))
        if not v:
            return bytes(out)


def parse(buf: bytes) -> list[list]:
    """Ordered [field, wire type, value] triples; value is int (varint) or raw bytes, so unknown fields round-trip."""
    out, i = [], 0
    while i < len(buf):
        key, i = _varint(buf, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(buf, i)
        elif wt == 1:
            v, i = buf[i:i + 8], i + 8
        elif wt == 2:
            n, i = _varint(buf, i)
            v, i = buf[i:i + n], i + n
        elif wt == 5:
            v, i = buf[i:i + 4], i + 4
        else:
            raise ValueError(f"wire type {wt} at byte {i}")
        out.append([num, wt, v])
    return out


def encode(fields: list[list]) -> bytes:
    out = bytearray()
    for num, wt, v in fields:
        out += _enc_varint(num << 3 | wt)
        if wt == 0:
            out += _enc_varint(v)
        elif wt == 2:
            out += _enc_varint(len(v)) + v
        else:
            out += v
    return bytes(out)


def _set(fields: list[list], num: int, wt: int, v, after: int | None = None):
    for f in fields:
        if f[0] == num:
            f[1], f[2] = wt, v
            return
    pos = next((k + 1 for k, f in enumerate(fields) if f[0] == after), len(fields))
    fields.insert(pos, [num, wt, v])


def _port(port_id: int, node_id: int, kind: int) -> bytes:
    """Graph input (kind 80) / output (kind 64) record, as Resolve writes them."""
    return encode([[1, 0, port_id], [2, 0, kind], [3, 2, encode([[1, 0, port_id], [2, 0, 64], [3, 0, port_id],
                                                                [4, 0, node_id]])]])


def build(seed_graph: bytes, labels: list[str]) -> bytes:
    g = parse(seed_graph)
    nodes = [f for f in g if f[0] == 7]
    if len(nodes) != 1:
        raise SystemExit(f"seed still has {len(nodes)} nodes; grab it from a clip with exactly one node")
    proto = parse(nodes[0][2])
    for f in proto:                  # empty node: drop the seed's tool entries (its LUT etc.), keep the slot layout
        if f[0] == 9:
            f[2] = encode([t for t in parse(f[2]) if t[0] != 1])
    x0, dx = next(v for n, _, v in proto if n == 4), 328
    ids = list(range(FIRST_ID, FIRST_ID + len(labels)))
    new_nodes = []
    for k, (nid, label) in enumerate(zip(ids, labels)):
        n = [list(f) for f in proto]
        _set(n, 1, 0, nid)
        _set(n, 2, 0, k + 1)
        _set(n, 4, 0, x0 + k * dx)
        _set(n, 6, 2, label.encode(), after=5)
        new_nodes.append([7, 2, encode(n)])
    edges = [[8, 2, encode([[1, 0, a], [3, 0, b], [5, 0, 64], [6, 0, 64], [7, 0, k + 1]])]
             for k, (a, b) in enumerate(zip(ids, ids[1:]))]
    out, placed = [], False
    for f in g:
        if f[0] in (7, 8):
            if not placed:
                out += new_nodes + edges
                placed = True
        elif f[0] == 9:
            out.append([9, 2, _port(3, ids[0], 80)])
        elif f[0] == 10:
            out.append([10, 2, _port(4, ids[-1], 64)])
        elif f[0] == 1:
            out.append([1, 0, ids[-1]])
        else:
            out.append(f)
    return encode(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("seed", type=Path, help=".drx of a 1-node grade")
    ap.add_argument("out", type=Path)
    ap.add_argument("--labels", nargs="+", default=LABELS)
    args = ap.parse_args()
    xml = args.seed.read_bytes().decode("utf-8")            # bytes: keep Resolve's CRLF line ends as they are
    m = re.search(r"(<pClipFullVer>.*?<Body>)([0-9a-f]+)(</Body>)", xml, re.S)
    if not m:
        raise SystemExit("no clip grade <Body> in the seed .drx")
    raw = bytes.fromhex(m.group(2))
    if raw[0] != 0x81:
        raise SystemExit(f"unknown body header byte {raw[0]:#x} (Resolve version change?)")
    top = parse(zstandard.ZstdDecompressor().decompress(raw[1:], max_output_size=50_000_000))
    for f in top:
        if f[0] == 1:
            f[2] = build(f[2], args.labels)
    body = b"\x81" + zstandard.ZstdCompressor(level=3).compress(encode(top))
    args.out.write_bytes((xml[:m.start(2)] + body.hex() + xml[m.end(2):]).encode("utf-8"))
    print(f"{args.out}: {len(args.labels)} serial nodes ({', '.join(args.labels)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
