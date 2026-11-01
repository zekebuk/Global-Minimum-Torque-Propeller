"""Resolve-free preview: graded segments, caching (only changed shots re-render), exact length, 1-1-1 tags."""
import json
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from fpvpipe import colorsci as cs, fastpreview  # noqa: E402
from fpvpipe.cutlist import CutList, Shot  # noqa: E402
from test_color import log_encode, to_display  # noqa: E402


def clip(path, cast, seconds=3, w=160, h=96):
    rng = np.random.default_rng(0)
    frames = []
    for _ in range(int(30 * seconds)):
        # realistic cues: neutral surfaces at several brightness levels (shadowed rock, rock, cloud), as in real flights
        img = np.zeros((h, w, 3)); img[:32] = 0.3 * np.array([0.6, 0.8, 1.2]); img[32:64] = 0.18; img[64:] = 0.1 * np.array([0.5, 1, 0.4])
        img[40:64, :50] = 0.05; img[6:20, 100:150] = 0.6
        frames.append((log_encode(img * rng.lognormal(0, 0.1, (h, w, 1)) * 0.5 * np.array(cast)) * 65535).astype("<u2"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{w}x{h}", "-r", "30",
                    "-i", "-", "-c:v", "prores_ks", "-pix_fmt", "yuv422p10le", str(path)],
                   input=np.stack(frames).tobytes(), check=True)


def test_preview_cache_and_output(tmp_path):
    lut = tmp_path / "toy.cube"
    cs.write_cube(lut, to_display, n=17)
    a, b = tmp_path / "a.mov", tmp_path / "b.mov"
    clip(a, (1.3, 1, 0.75)); clip(b, (0.85, 1, 1.2))
    cfg = {"color": {"luts": {}, "conversion_lut": lut, "nodes": {"order": "scene"}}}
    c = CutList("REEL", 30.0, [Shot(str(a), 0.5, 1.5), Shot(str(b), 0.5, 1.7), Shot(str(a), 1.5, 2.5)])
    out = tmp_path / "p.mp4"
    r1 = fastpreview.build(c, cfg, out, profile_of=lambda p: "toy")
    r2 = fastpreview.build(c, cfg, out, profile_of=lambda p: "toy")
    c.trim(2, tail_frames=6)
    r3 = fastpreview.build(c, cfg, out, profile_of=lambda p: "toy")
    assert (r1.rendered, r2.rendered, r3.rendered) == (3, 0, 1)
    st = json.loads(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,color_transfer:format=duration",
                                    "-of", "json", str(out)], capture_output=True, text=True).stdout)
    assert st["streams"][0]["width"] == 540 and st["streams"][0]["color_transfer"] == "bt709"
    assert abs(float(st["format"]["duration"]) - c.duration()) < 0.05
    # the two oppositely cast clips come out matched: mid-frame grey band is neutral in both
    def grey_chroma(t):
        raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1", "-pix_fmt", "rgb24",
                              "-f", "rawvideo", "-"], capture_output=True).stdout
        img = np.frombuffer(raw, np.uint8).reshape(960, 540, 3) / 255.0
        g = img[440:620, 300:].reshape(-1, 3).mean(0)          # lit grey rock (right of the shadowed part)
        return max(g) - min(g)
    assert grey_chroma(0.5) < 0.04 and grey_chroma(1.5) < 0.04
