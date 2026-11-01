"""Phone notes -> exact edits on the cut list."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe.cutlist import CutList, Shot  # noqa: E402


def reel():
    c = CutList("REEL", 30.0, beats_s=[i * 0.5 for i in range(40)])   # 120 BPM grid
    for k in range(5):
        c.shots.append(Shot(f"GX0100{k}.MP4", 10.0, 11.1 + 0.1 * k))
    return c


def test_trim_slip_swap_move_delete(tmp_path):
    c = reel()
    c.trim(1, head_frames=3, tail_frames=6)                 # "shot 1: start 3 frames later, end 6 earlier"
    assert c.shots[0].in_s == pytest.approx(10.1) and c.shots[0].out_s == pytest.approx(10.9)
    c.slip(2, 15)                                            # "shot 2: same length, half a second later"
    assert c.shots[1].in_s == pytest.approx(10.5) and c.shots[1].dur == pytest.approx(1.2)
    c.swap(3, 4)
    assert c.shots[2].src.endswith("3.MP4")
    c.move(5, 1)
    assert c.shots[0].src.endswith("4.MP4")
    c.delete(2)
    assert len(c.shots) == 4
    p = tmp_path / "c.json"
    c.save(p)
    assert CutList.load(p).table() == c.table()
    with pytest.raises(IndexError):
        c.trim(9)
    with pytest.raises(ValueError):
        c.trim(1, head_frames=100)


def test_replace_keeps_slot_length():
    c = reel()
    c.replace(3, "GX010099.MP4", 42.0)
    assert c.shots[2].src == "GX010099.MP4" and c.shots[2].dur == pytest.approx(1.3)


def test_snap_to_beats_puts_every_cut_on_the_grid():
    c = reel()
    notes = c.snap_to_beats()
    assert notes
    for t in c.starts()[1:] + [c.duration()]:
        assert min(abs(t - b) for b in c.beats_s) < 0.5 / c.fps + 1e-9


def test_first_cut_lands_best_move_on_the_drop():
    from fpvpipe.cutlist import first_cut
    beats = [0.25 + i * 0.5 for i in range(80)]                  # 120 BPM, first beat at 0.25 s
    downs = beats[::4]                                           # bar = 2 s
    drop = downs[4]                                              # drop after 4 bars (8 s into the reel)
    moments = [{"src": "best.MP4", "peak_s": 30.0, "score": 3.1, "label": "flip/roll"},
               {"src": "b.MP4", "peak_s": 12.0, "score": 2.0}, {"src": "c.MP4", "peak_s": 5.0, "score": 1.5}]
    c = first_cut(moments, beats, downs, drop, 30.0, target_s=16.0)
    starts = c.starts()
    i = next(k for k, s in enumerate(c.shots) if s.src == "best.MP4")
    assert abs(starts[i] + c.music_in_s - drop) < 1 / 30          # the drop slot starts exactly at the drop
    peak_in_reel = starts[i] + (30.0 - c.shots[i].in_s)
    assert abs(peak_in_reel + c.music_in_s - drop) <= 0.25 + 1 / 30   # peak within a few frames after the cut
    assert all(abs(s.dur - 2.0) < 0.04 for s in c.shots[:4])     # bar-long shots before the drop
    assert all(abs(s.dur - 1.0) < 0.04 for s in c.shots[5:])     # half-bar shots after
    assert abs(c.duration() - 16.0) < 0.05


def test_first_cut_off_grid_drop_and_reuse():
    from fpvpipe.cutlist import first_cut
    beats = [0.42 + i * 60 / 128 for i in range(120)]
    downs = beats[::4]
    drop = 8.92                                                   # NOT a whole number of bars after the first downbeat
    moments = [{"src": "best.MP4", "peak_s": 20.0, "score": 3}, {"src": "a.MP4", "peak_s": 5.0, "score": 2}]
    c = first_cut(moments, beats, downs, drop, 30.0, target_s=16.0)
    i = [s.src for s in c.shots].index("best.MP4")
    assert abs(c.starts()[i] + c.music_in_s - drop) < 1 / 30      # best move is on the drop anyway
    assert all(s.dur >= 2 / 30 for s in c.shots)                  # no sliver shots
    a_ins = [s.in_s for s in c.shots if s.src == "a.MP4"]
    assert len(set(a_ins)) == len(a_ins)                          # reused moment never repeats the same frames
