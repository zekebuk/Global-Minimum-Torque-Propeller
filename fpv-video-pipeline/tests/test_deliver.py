"""fpvpipe/deliver.py: the Instagram master's render settings and the READY FOR EXPORT verdict (offline: the checks
take a probe() dict, so no Resolve or ffprobe is needed)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import deliver  # noqa: E402

SPEC = {"width": 1080, "height": 1920, "fps": 29.97, "codec": "H264", "bitrate_kbps": 20000, "profile": "High",
        "multipass": True, "color_space_tag": "Rec.709", "gamma_tag": "Rec.709", "max_seconds": 90}
GOOD = {"width": 1080, "height": 1920, "fps": 29.97, "codec": "h264", "profile": "High", "bitrate_kbps": 18400,
        "tags": ("bt709", "bt709", "bt709"), "seconds": 23.8, "mb": 55.0,
        "audio": {"codec": "aac", "rate": 48000, "channels": 2, "bitrate_kbps": 320}}


def test_settings_are_the_instagram_master():
    s = deliver.settings(SPEC, Path("E:/EXPORTS/X"), "X_v3")
    assert (s["FormatWidth"], s["FormatHeight"], s["FrameRate"]) == (1080, 1920, "29.97")
    assert s["VideoQuality"] == 20000 and s["EncodingProfile"] == "High" and s["MultiPassEncode"] is True
    assert s["AudioCodec"] == "aac" and s["AudioSampleRate"] == 48000
    assert s["ColorSpaceTag"] == "Rec.709" and s["GammaTag"] == "Rec.709"      # 1-1-1 tags
    assert s["CustomName"] == "X_v3" and s["TargetDir"] == str(Path("E:/EXPORTS/X"))


def test_good_file_is_ready():
    items = deliver.checks(GOOD, SPEC, (-14.2, -1.3), graded=7, shots=7, manual=2, stabilized=3, music="dune")
    assert all(c.ok for c in items)
    text = deliver.report(Path("x.mp4"), items, deliver.NEXT_PHONE)
    assert text.startswith("==================== READY FOR EXPORT") and "music baked in (dune)" in text
    assert "7/7 shots graded, 2 by hand, Gyroflow on 3" in text and "Next:" in text


def test_main_profile_passes_baseline_does_not():
    assert deliver.checks({**GOOD, "profile": "Main"}, SPEC)[0].ok          # what Resolve 21.1 actually writes
    assert not deliver.checks({**GOOD, "profile": "Constrained Baseline"}, SPEC)[0].ok


def test_problems_are_named():
    bad = {**GOOD, "tags": ("bt709", "bt709", None), "seconds": 95.0, "bitrate_kbps": 4000}
    items = deliver.checks(bad, SPEC, (-21.0, 0.5), graded=6, shots=7)
    failed = {c.name for c in items if not c.ok}
    assert failed == {"picture", "color", "loudness", "length", "grades"}
    text = deliver.report(Path("x.mp4"), items)
    assert text.startswith("==================== NOT READY FOR EXPORT - 5 problem(s)")
    assert "[!!] color" in text and "Gamma tag = Rec.709" in text and "1 UNGRADED" in text
    assert "Next:" not in text


def test_no_music_master_skips_loudness_and_says_so():
    items = deliver.checks(GOOD, SPEC, (-30.0, -20.0), no_music=True)   # motor audio only: quiet is expected
    assert all(c.ok for c in items)
    assert any("add it in Edits" in c.detail for c in items)
    assert not any(c.name == "grades" for c in items)                   # unknown without a build
    assert deliver.checks({**GOOD, "audio": None}, SPEC)[2] == deliver.Check(False, "audio", "no audio stream")
    plain = deliver.checks(GOOD, SPEC)[2].detail                        # --check on a bare file: no music claim
    assert plain == "AAC 48 kHz stereo 320 kbps"
    assert deliver.checks(GOOD, SPEC, music="")[2].detail.endswith("no music in the cut list")
