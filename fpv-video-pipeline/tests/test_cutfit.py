import numpy as np

from fpvpipe import cutfit, cutlist as cl


def _texture(seed=1):
    rng = np.random.default_rng(seed)
    x = rng.random((cutfit.H, cutfit.W)).astype(np.float32)
    f = np.fft.fft2(x)
    fy, fx = np.meshgrid(np.fft.fftfreq(cutfit.H), np.fft.fftfreq(cutfit.W), indexing="ij")
    return np.fft.ifft2(f * np.exp(-(fx ** 2 + fy ** 2) * 200)).real.astype(np.float32) * 255


def _rotated(img, deg):
    y, x = np.mgrid[0:cutfit.H, 0:cutfit.W].astype(np.float32)
    y, x = y - cutfit.H / 2, x - cutfit.W / 2
    a = np.radians(-deg)
    xs = (np.cos(a) * x - np.sin(a) * y + cutfit.W / 2).astype(int).clip(0, cutfit.W - 1)
    ys = (np.sin(a) * x + np.cos(a) * y + cutfit.H / 2).astype(int).clip(0, cutfit.H - 1)
    return img[ys, xs]


def test_rotation_reads_the_angle_and_its_sign():
    a = _texture()
    assert abs(cutfit.rotation(a, _rotated(a, 20)) - 20) <= 4
    assert abs(cutfit.rotation(a, _rotated(a, -12)) + 12) <= 4
    assert abs(cutfit.rotation(a, a)) <= 2


def test_shift_reads_the_direction():
    a = _texture()
    dx, dy = cutfit.shift(a, np.roll(a, (3, 7), axis=(0, 1)))
    assert (dx, dy) == (7, 3)


def _profile(peak_s, roll_at=None):
    n = cutfit.HZ * 12
    t = np.arange(n) / cutfit.HZ
    m = 2 + 20 * np.exp(-((t - peak_s) / .15) ** 2)
    r = np.zeros(n) if roll_at is None else 200 * np.exp(-((t - roll_at) / .3) ** 2)
    return cutfit.Profile(0.0, m, r, np.ones(n), np.zeros(n))


def test_option_prefers_the_motion_peak_on_a_beat():
    s = cl.Shot(src="x.mp4", in_s=2.0, out_s=4.0)
    p, beats = _profile(5.0), np.array([1.0])
    good = cutfit.option(s, p, 4.0, 2.0, beats)        # the peak at 5.0 falls exactly on the beat
    off = cutfit.option(s, p, 3.6, 2.0, beats)
    assert good.peak_off < .1 and good.score > off.score


def test_option_penalises_a_cut_in_a_roll():
    s = cl.Shot(src="x.mp4", in_s=2.0, out_s=4.0)
    clean = cutfit.option(s, _profile(5.0), 4.0, 2.0, np.array([1.0]))
    rolled = cutfit.option(s, _profile(5.0, roll_at=6.0), 4.0, 2.0, np.array([1.0]))
    assert rolled.roll_out > cutfit.ROLL_OK and rolled.score < clean.score


def test_carry_sign():
    o = lambda vi, vo: cutfit.Option(0, 0, 0, 0, 0, 0, vi, vo)
    assert cutfit.carry(o((0, 0), (3, 0)), o((3, 0), (0, 0))) > .4
    assert cutfit.carry(o((0, 0), (3, 0)), o((-3, 0), (0, 0))) < -.4
    assert cutfit.carry(o((0, 0), (0.1, 0)), o((3, 0), (0, 0))) == 0


def test_moving_a_ramped_shot_keeps_its_length():
    from fpvpipe import ramp
    s = cl.Shot(src="x.mp4", in_s=10.0, out_s=12.0, ramp=[[10.0, 1.25], [11.2, 0.9]])
    pts, out = cutfit._moved(s, 11.0, 2.0)
    assert pts[0][0] == 11.0 and abs(ramp.duration(pts, 11.0, out) - 2.0) < .01
