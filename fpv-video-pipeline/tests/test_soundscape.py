"""Soundscape follows the IMU: whoosh on the flip, swell on the dive, motor louder when flying hard."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import soundscape as ss  # noqa: E402

SR = ss.SR


def flight(dur=10.0, fs=200):
    t = np.arange(0, dur, 1 / fs)
    rate = np.full(len(t), 60.0)                 # calm cruising
    rate[(t >= 3.0) & (t < 3.6)] = 900.0         # a snap roll around 3 s
    acc = np.ones(len(t))
    acc[(t >= 6.0) & (t < 7.0)] = 0.1            # one second of free fall
    return t, rate, acc


def test_events_found_where_the_physics_is():
    t, rate, acc = flight()
    seg = ss.Segment(0.0, 10.0, "forest", t, rate, t, acc)
    w = ss.whoosh_times(seg)
    assert len(w) == 1 and 3.0 <= w[0] <= 3.6
    ff = ss.freefall_spans(seg)
    assert len(ff) == 1 and abs(ff[0][0] - 6.0) < 0.02


def test_mix_levels_and_placement():
    t, rate, acc = flight()
    segs = [ss.Segment(0.0, 5.0, "forest", t[t < 5], rate[t < 5], t[t < 5], acc[t < 5]),
            ss.Segment(5.0, 5.0, "river", t[t >= 5] - 5, rate[t >= 5], t[t >= 5] - 5, acc[t >= 5])]
    rng = np.random.default_rng(0)
    beds = {"forest": 0.1 * rng.standard_normal(SR * 2).astype(np.float32),
            "river": 0.1 * rng.standard_normal(SR * 2).astype(np.float32)}
    motor = np.sin(2 * np.pi * 220 * np.arange(SR) / SR).astype(np.float32)
    whoosh = np.zeros(SR // 2, np.float32); whoosh[SR // 4] = 1.0          # click at its loudest point
    dive = np.zeros(SR, np.float32); dive[0] = 1.0
    bus, stats = ss.mix(segs, beds, motor, whoosh, dive, 10.0)
    assert len(bus) == 10 * SR and stats["whooshes"] == 1 and stats["dives"] == 1
    assert set(stats["scenes"]) == {"forest", "river"}
    assert np.max(np.abs(bus)) <= ss.db(-1) + 1e-6
    # whoosh click lands on the roll (3.0-3.6 s), dive swell starts at 6 s
    win = lambda a, b: np.max(np.abs(bus[int(a * SR):int(b * SR)]))
    assert win(3.0, 3.6) > 2 * win(1.0, 2.0)
    assert win(5.99, 6.01) > 2 * win(8.0, 9.0)
    # motor (220 Hz) is louder during the roll than when cruising
    def tone(a, b):
        x = bus[int(a * SR):int(b * SR)]
        f = np.abs(np.fft.rfft(x)); hz = np.fft.rfftfreq(len(x), 1 / SR)
        return f[np.argmin(np.abs(hz - 220))]
    assert tone(3.1, 3.5) > 3 * tone(1.1, 1.5)


def test_no_imu_shot_gets_steady_motor():
    motor = np.sin(2 * np.pi * 220 * np.arange(SR) / SR).astype(np.float32)
    bus, stats = ss.mix([ss.Segment(0.0, 3.0, "x")], {}, motor, np.zeros(0), np.zeros(0), 3.0)
    assert stats["whooshes"] == 0 and np.std(bus[SR:2 * SR]) > 0
