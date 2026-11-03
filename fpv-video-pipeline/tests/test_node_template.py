"""Offline tests for scripts/make_node_template.py on the 1-node seed still kept in templates/. No Resolve needed."""
import re
import subprocess
import sys
from pathlib import Path

import zstandard

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import make_node_template as mnt  # noqa: E402

SEED = ROOT / "templates" / "node_seed_1node.drx"


def graph(path: Path) -> list[list]:
    xml = path.read_bytes().decode("utf-8")
    raw = bytes.fromhex(re.search(r"<pClipFullVer>.*?<Body>([0-9a-f]+)</Body>", xml, re.S).group(1))
    top = mnt.parse(zstandard.ZstdDecompressor().decompress(raw[1:], max_output_size=50_000_000))
    return mnt.parse(next(v for n, _, v in top if n == 1))


def test_parse_encode_roundtrip():
    xml = SEED.read_bytes().decode("utf-8")
    raw = bytes.fromhex(re.search(r"<pClipFullVer>.*?<Body>([0-9a-f]+)</Body>", xml, re.S).group(1))
    body = zstandard.ZstdDecompressor().decompress(raw[1:], max_output_size=50_000_000)
    assert mnt.encode(mnt.parse(body)) == body


def test_five_empty_serial_nodes(tmp_path):
    out = tmp_path / "t.drx"
    subprocess.run([sys.executable, str(ROOT / "scripts" / "make_node_template.py"), str(SEED), str(out)], check=True)
    g = graph(out)
    nodes = [mnt.parse(v) for n, _, v in g if n == 7]
    ids = [next(v for f, _, v in nd if f == 1) for nd in nodes]
    labels = [next(v for f, _, v in nd if f == 6).decode() for nd in nodes]
    assert labels == mnt.LABELS and len(set(ids)) == 5
    for nd in nodes:                                     # no tools left in any node (the seed's LUT is gone)
        tools = mnt.parse(next(v for f, _, v in nd if f == 9))
        assert not [t for t in tools if t[0] == 1]
    edges = [dict((f, v) for f, _, v in mnt.parse(v)) for n, _, v in g if n == 8]
    assert [(e[1], e[3]) for e in edges] == list(zip(ids, ids[1:]))
    port = lambda k: dict((f, v) for f, _, v in mnt.parse(dict((f, v) for f, _, v in mnt.parse(
        next(v for n, _, v in g if n == k)))[3]))[4]
    assert port(9) == ids[0] and port(10) == ids[-1]     # graph input -> first node, last node -> output
