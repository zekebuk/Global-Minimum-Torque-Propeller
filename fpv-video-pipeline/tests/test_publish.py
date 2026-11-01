"""ElevenLabs request building and YouTube metadata rules, offline."""
import io
import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from fpvpipe import elevenlabs, youtube  # noqa: E402


def test_elevenlabs_bodies_clamp_to_api_ranges():
    b = elevenlabs.sound_effect_body("whoosh", 45, loop=True)
    assert b["duration_seconds"] == 30 and b["loop"] is True and b["model_id"] == "eleven_text_to_sound_v2"
    assert elevenlabs.sound_effect_body("hit", 0.1)["duration_seconds"] == 0.5
    assert "duration_seconds" not in elevenlabs.sound_effect_body("x", None)
    m = elevenlabs.music_body("p" * 5000, 1.0, "music_v2_5", seed=7)
    assert m["music_length_ms"] == 3000 and len(m["prompt"]) == 4100 and m["seed"] == 7 and m["force_instrumental"]


def test_elevenlabs_request_shape(monkeypatch):
    seen = {}

    class Resp(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass

    def fake_urlopen(req, timeout):
        seen.update(url=req.full_url, headers=dict(req.header_items()), body=json.loads(req.data))
        return Resp(b"ID3audio")

    monkeypatch.setenv("ELEVENLABS_API_KEY", "k123")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert elevenlabs.sound_effect("whoosh", 1.0) == b"ID3audio"
    assert seen["url"] == "https://api.elevenlabs.io/v1/sound-generation?output_format=mp3_44100_192"
    assert seen["headers"]["Xi-api-key"] == "k123" and seen["body"]["text"] == "whoosh"
    elevenlabs.music("track", 24)
    assert seen["url"].startswith("https://api.elevenlabs.io/v1/music?") and seen["body"]["music_length_ms"] == 24000


def test_missing_key_is_clear(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    try:
        elevenlabs.sound_effect("x")
    except elevenlabs.ElevenLabsError as e:
        assert "ELEVENLABS_API_KEY" in str(e)
    else:
        raise AssertionError("expected error")


def test_sfx_riser_fits_bars():
    from make_sfx_library import duration
    assert abs(duration(("bars", 2), 128) - 3.75) < 1e-9 and duration(1.5, 128) == 1.5


def test_chapter_rules():
    ch, notes = youtube.fix_chapters([(5, "Ridge"), (12, "Too close"), (60, "Quarry"), (200, "Sunset"), (295, "End")], 300)
    assert ch == [(0.0, "Ridge"), (12, "Too close"), (60, "Quarry"), (200, "Sunset")]
    assert any("moved 'Ridge'" in n for n in notes) and any("End" in n for n in notes)
    late, notes = youtube.fix_chapters([(30, "A"), (90, "B"), (150, "C")], 200)
    assert late[0] == (0.0, "Intro") and len(late) == 4
    tight, notes = youtube.fix_chapters([(0, "A"), (4, "B"), (40, "C"), (80, "D")], 200)
    assert [n for _, n in tight] == ["A", "C", "D"] and any("dropped 'B'" in n for n in notes)
    few, notes = youtube.fix_chapters([(0, "A"), (30, "B")], 100)
    assert len(few) == 2 and any("3+" in n for n in notes)


def test_chapter_file_and_description():
    parsed = youtube.parse_chapter_file("0:00 Intro\n1:05 - Ridge line\n1:02:03 Night\njunk")
    assert parsed == [(0, "Intro"), (65, "Ridge line"), (3723, "Night")]
    d = youtube.description({"description_intro": "Hi", "gear": ["GoPro"], "hashtags": ["fpv", "#drone"]},
                            parsed, 4000, ["track - generated with ElevenLabs Music"])
    assert "0:00:00 Intro" in d and "1:02:03 Night" in d and "#fpv #drone" in d and "- GoPro" in d
    t = youtube.tags({"tags": ["fpv", "FPV", "drone"]}, ["x" * 600])
    assert t == ["fpv", "drone"]


def test_edits_cue_math():
    from reel_handoff import cue_from_drops, cue_from_timeline, fmt, rounding_report
    # Song clip trimmed to start 47.3 s in, placed at reel 0:00 -> start song at 0:47.3
    assert abs(cue_from_timeline(47.3, 0.0) - 47.3) < 1e-9
    # Same trim but placed 1 s into the reel -> the song must already be at 46.3 s at reel 0:00
    assert abs(cue_from_timeline(47.3, 1.0) - 46.3) < 1e-9
    # Hook of 7.5 s, song drops at 5 s (too early) and 52.5 s -> start at 45.0
    assert cue_from_drops([5.0, 52.5], 7.5) == 45.0
    assert cue_from_drops([3.0], 7.5) is None
    assert fmt(47.3) == "0:47.3" and fmt(125.04) == "2:05.0" and fmt(-1.5) == "-0:01.5"
    r = rounding_report(47.3, 30)
    assert "0:47.5" in r[0] and "visible" in r[0] and "0:47.0" in r[1]
    assert "fine" in rounding_report(45.02, 30)[1]
