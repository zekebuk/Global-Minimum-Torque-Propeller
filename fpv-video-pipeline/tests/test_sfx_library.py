"""SFX library: manifest is link-free and complete; fetch files sounds by label; Resolve bin import is idempotent."""
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import resolve as rlib  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = json.loads((ROOT / "assets" / "sfx_manifest.json").read_text())


def test_manifest_is_public_safe_and_matches_the_tools():
    text = (ROOT / "assets" / "sfx_manifest.json").read_text()
    assert "Signature" not in text and "http" not in text           # never commit signed links (public repo)
    s = MANIFEST["sounds"]
    assert len(s) == 24 and all(len(v["takes"]) == 2 for v in s.values())
    # names the soundscape mixer and reel tools look up
    for need in ("bed/forest", "drone/motor_loop", "whoosh/whoosh_short", "ambience/dive_wind", "impact/sub_hit"):
        assert need in s
    assert s["riser/riser_2bar_128bpm"]["duration_s"] == 3.75 and s["bed/quarry"]["loop"] is True


def test_fetch_downloads_checks_and_indexes(tmp_path):
    srcdir = tmp_path / "src"; srcdir.mkdir()
    one = {"whoosh/whoosh_short": MANIFEST["sounds"]["whoosh/whoosh_short"]}
    links = {}
    for t in one["whoosh/whoosh_short"]["takes"]:
        f = srcdir / t["file"].replace("/", "_")
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=f=400:d=2", str(f)], check=True)
        links[t["file"]] = f.as_uri()
    (tmp_path / "m.json").write_text(json.dumps({"generated": "x", "sounds": one}))
    (tmp_path / "links.json").write_text(json.dumps({"expires_utc": "2099-01-01T00:00:00Z", "links": links}))
    cfg = re.sub(r'(?m)^sfx = ".*"$', f'sfx = "{(tmp_path / "SFX").as_posix()}"', (ROOT / "config.toml").read_text())
    (tmp_path / "cfg.toml").write_text(cfg)
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_sfx.py"), "--links", str(tmp_path / "links.json"),
                        "--manifest", str(tmp_path / "m.json"), "--config", str(tmp_path / "cfg.toml")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (tmp_path / "SFX" / "whoosh" / "whoosh_short_1.mp3").exists()
    idx = json.loads((tmp_path / "SFX" / "library.json").read_text())
    assert idx["whoosh/whoosh_short_2.mp3"]["generation_id"] == "10RL3ipItghWAZSqiDzy"
    (tmp_path / "links.json").write_text(json.dumps({"expires_utc": "2000-01-01T00:00:00Z", "links": links}))
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "fetch_sfx.py"), "--links", str(tmp_path / "links.json"),
                        "--manifest", str(tmp_path / "m.json"), "--config", str(tmp_path / "cfg.toml")],
                       capture_output=True, text=True)
    assert r.returncode == 1 and "expired" in r.stdout


class FakeClip:
    def __init__(self, n): self.n = n
    def GetName(self): return self.n


class FakeFolder:
    def __init__(self, n): self.n, self.subs, self.clips = n, [], []
    def GetName(self): return self.n
    def GetSubFolderList(self): return self.subs
    def GetClipList(self): return self.clips


class FakePool:
    def __init__(self): self.cur = None
    def AddSubFolder(self, parent, n):
        f = FakeFolder(n); parent.subs.append(f); return f
    def SetCurrentFolder(self, f): self.cur = f
    def ImportMedia(self, paths):
        clips = [FakeClip(Path(p).name) for p in paths]; self.cur.clips += clips; return clips


def test_resolve_bin_import_is_idempotent(tmp_path):
    for rel in ("whoosh/whoosh_short_1.mp3", "whoosh/whoosh_long_1.mp3", "bed/forest_1.mp3"):
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True); (tmp_path / rel).write_bytes(b"x")
    (tmp_path / "library.json").write_text("{}")
    pool, sfx_bin = FakePool(), FakeFolder("03_SFX")
    assert rlib.import_sfx_library(pool, sfx_bin, tmp_path) == {"bed": 1, "whoosh": 2}
    assert rlib.import_sfx_library(pool, sfx_bin, tmp_path) == {}                    # second build: nothing new
    (tmp_path / "whoosh" / "whoosh_reverse_1.mp3").write_bytes(b"x")
    assert rlib.import_sfx_library(pool, sfx_bin, tmp_path) == {"whoosh": 1}         # only the new sound
    assert sorted(f.GetName() for f in sfx_bin.subs) == ["bed", "whoosh"]
