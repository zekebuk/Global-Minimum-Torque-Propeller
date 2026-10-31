"""Offline tests: GPMF parsing, highlight scoring, beat tracking, GoPro naming. No Resolve needed."""
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import beats, gpmf, highlights, media  # noqa: E402


def klv(key: str, typ: str, size: int, repeat: int, payload: bytes) -> bytes:
    body = struct.pack(">4scBH", key.encode(), typ.encode() if typ != "\x00" else b"\x00", size, repeat) + payload
    return body + b"\x00" * ((-len(payload)) % 4)


def nest(key: str, children: bytes) -> bytes:
    # Nested containers use size=1 and repeat=len so size*repeat covers the children.
    return struct.pack(">4scBH", key.encode(), b"\x00", 1, len(children)) + children


def make_stream(key, samples_si, scale, stamp_us):
    raw = np.round(np.asarray(samples_si) * scale).astype(">i2")
    data = (klv("STMP", "J", 8, 1, struct.pack(">Q", stamp_us))
            + klv("SCAL", "s", 2, 1, struct.pack(">h", scale))
            + klv("ORIN", "c", 1, 3, b"ZXY")
            + klv(key, "s", 6, len(raw), raw.tobytes()))
    return nest("STRM", data)


def test_gpmf_roundtrip_with_scale_and_timestamps():
    gyro1 = np.array([[1.0, 0.0, -1.0], [0.5, 0.25, 0.0]])
    gyro2 = np.array([[2.0, 2.0, 2.0]])
    acc = np.array([[0.0, 9.8, 0.0]])
    payload1 = nest("DEVC", make_stream("GYRO", gyro1, 100, 0) + make_stream("ACCL", acc, 10, 0))
    payload2 = nest("DEVC", make_stream("GYRO", gyro2, 100, 1_000_000))
    tracks = gpmf.parse(payload1 + payload2)
    g = tracks["GYRO"].array()
    np.testing.assert_allclose(g, np.vstack([gyro1, gyro2]), atol=0.01)
    np.testing.assert_allclose(tracks["ACCL"].array(), acc, atol=0.1)
    assert tracks["GYRO"].order == "ZXY"
    t = gpmf.timeline(tracks["GYRO"], duration_s=2.0)
    np.testing.assert_allclose(t, [0.0, 0.5, 1.0])


def test_highlights_find_flip_and_dive():
    rate = 200.0
    t = np.arange(0, 30, 1 / rate)
    gyro = np.zeros((len(t), 3))
    acc = np.tile([0.0, 9.81, 0.0], (len(t), 1))
    flip = (t >= 10) & (t < 11)           # 360 deg roll in 1 s
    gyro[flip, 0] = np.radians(360.0)
    dive = (t >= 20) & (t < 22.5)         # near free fall
    acc[dive] = [0.0, 1.0, 0.0]
    w = highlights.score_windows(t, gyro, t, acc, window_s=3.0)
    best = highlights.pick(w, top_k=2, min_gap_s=4.0)
    labels = {b.label: b for b in best}
    assert "flip/roll" in labels and 8 <= labels["flip/roll"].start_s <= 10.5
    assert "dive/hang" in labels and 18.5 <= labels["dive/hang"].start_s <= 21.5
    assert highlights.smooth_sections(w, min_len_s=5.0)  # quiet stretches exist for YouTube


def click_track(bpm, seconds, sr=22050, accent_every=4):
    y = np.zeros(int(seconds * sr), dtype=np.float32)
    period = 60.0 / bpm
    click = np.sin(2 * np.pi * 1000 * np.arange(int(0.02 * sr)) / sr) * np.hanning(int(0.02 * sr))
    for k, start in enumerate(np.arange(0.5, seconds - 0.1, period)):
        a = int(start * sr)
        y[a:a + len(click)] += click * (1.0 if k % accent_every == 0 else 0.5)
    return y, sr


def test_beat_tracking_on_click_track():
    y, sr = click_track(128, 20)
    bm = beats.analyse(y, sr)
    assert abs(bm.tempo_bpm - 128) < 2, bm.tempo_bpm
    intervals = np.diff(bm.beats_s)
    assert abs(np.median(intervals) - 60 / 128) < 0.02
    # beats land on clicks (within one STFT hop)
    expected = np.arange(0.5, 19.9, 60 / 128)
    err = [np.min(np.abs(expected - b)) for b in bm.beats_s[2:-2]]
    assert np.median(err) < 0.03


def test_drop_detection():
    y, sr = click_track(120, 24)
    y[int(12 * sr):] *= 6.0               # the drop at 12 s
    bm = beats.analyse(y, sr)
    assert any(abs(d - 12.0) < 0.6 for d in bm.drops_s), bm.drops_s


def test_gopro_name_parsing():
    n = media.parse_gopro_name("GX020042.MP4")
    assert (n.encoding, n.chapter, n.recording, n.ext) == ("X", 2, 42, "MP4")
    assert media.parse_gopro_name("IMG_0001.MOV") is None
