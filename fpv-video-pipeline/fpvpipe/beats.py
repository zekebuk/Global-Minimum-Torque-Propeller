"""Beat, downbeat and drop detection with numpy only (no librosa needed).

Method:
  1. onset envelope = half-wave-rectified spectral flux of a log-magnitude STFT
  2. tempo = autocorrelation peak of the envelope inside a BPM range, weighted toward ~120 BPM
  3. beats = dynamic-programming beat tracker (Ellis 2007): maximise onset strength while
     penalising deviation from the tempo period
  4. downbeats = every 4th beat, with the phase whose beats carry the most onset energy (assumes 4/4)
  5. drops = beats where short-term loudness jumps far above the preceding bars
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

HOP = 512
NFFT = 1024  # ~46 ms at 22.05 kHz: short enough to keep onsets sharp for frame-accurate cuts


@dataclass
class BeatMap:
    tempo_bpm: float
    beats_s: list[float]
    downbeats_s: list[float]
    drops_s: list[float]


def onset_envelope(y: np.ndarray, sr: int) -> np.ndarray:
    # Centre-pad so frame i is centred on sample i*HOP (frame time = i*HOP/sr, no window latency).
    y = np.pad(y, (NFFT // 2, NFFT // 2))
    if len(y) < NFFT:
        y = np.pad(y, (0, NFFT - len(y)))
    frames = 1 + (len(y) - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(frames)[:, None]
    spec = np.abs(np.fft.rfft(y[idx] * np.hanning(NFFT), axis=1))
    logspec = np.log1p(1000.0 * spec)
    flux = np.maximum(0.0, np.diff(logspec, axis=0)).sum(axis=1)
    flux = np.concatenate([[0.0], flux])
    flux -= np.convolve(flux, np.ones(16) / 16, mode="same")  # remove slow trend
    flux = np.maximum(flux, 0.0)
    return flux / (flux.max() or 1.0)


def estimate_tempo(env: np.ndarray, sr: int, bpm_range=(70.0, 180.0)) -> float:
    fps = sr / HOP
    env = env - env.mean()
    ac = np.correlate(env, env, mode="full")[len(env) - 1:]
    lags = np.arange(len(ac))
    with np.errstate(divide="ignore"):
        bpm = 60.0 * fps / lags
    valid = (bpm >= bpm_range[0]) & (bpm <= bpm_range[1])
    # Log-Gaussian prior around 120 BPM resolves half/double tempo ambiguity toward danceable tempi.
    prior = np.exp(-0.5 * (np.log2(np.where(valid, bpm, 1.0) / 120.0) / 1.0) ** 2)
    weighted = np.where(valid, ac * prior, -np.inf)
    best = int(np.argmax(weighted))
    # Parabolic interpolation for sub-frame lag precision.
    if 1 <= best < len(ac) - 1:
        a, b, c = ac[best - 1], ac[best], ac[best + 1]
        denom = a - 2 * b + c
        lag = best + (0.5 * (a - c) / denom if denom else 0.0)
    else:
        lag = best
    return float(60.0 * fps / lag)


def track_beats(env: np.ndarray, sr: int, tempo: float, tightness: float = 100.0) -> np.ndarray:
    fps = sr / HOP
    period = 60.0 * fps / tempo
    n = len(env)
    score = env.copy()
    back = -np.ones(n, dtype=int)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    offsets = np.arange(-hi, -lo + 1)
    penalty = -tightness * np.log(-offsets / period) ** 2
    for i in range(n):
        prev = i + offsets
        ok = prev >= 0
        if not ok.any():
            continue
        cand = score[prev[ok]] + penalty[ok]
        j = int(np.argmax(cand))
        score[i] = env[i] + cand[j]
        back[i] = prev[ok][j]
    # Start from the best-scoring frame in the last beat period and backtrack.
    tail = max(n - int(round(period)), 0)
    i = tail + int(np.argmax(score[tail:]))
    beats = [i]
    while back[i] >= 0:
        i = back[i]
        beats.append(i)
    return np.array(beats[::-1]) / fps


def analyse(y: np.ndarray, sr: int) -> BeatMap:
    env = onset_envelope(y, sr)
    tempo = estimate_tempo(env, sr)
    beats = track_beats(env, sr, tempo)
    fps = sr / HOP
    frames = np.clip((beats * fps).astype(int), 0, len(env) - 1)
    strength = env[frames]
    phase = int(np.argmax([strength[p::4].sum() for p in range(4)])) if len(beats) >= 4 else 0
    downbeats = beats[phase::4]

    # Loudness per beat; a drop is a beat whose loudness is >2x the mean of the preceding 8 beats.
    rms = []
    for k in range(len(beats)):
        a = int(beats[k] * sr)
        b = int(beats[k + 1] * sr) if k + 1 < len(beats) else min(a + sr // 2, len(y))
        seg = y[a:b]
        rms.append(float(np.sqrt(np.mean(seg ** 2))) if len(seg) else 0.0)
    rms = np.asarray(rms)
    drops = []
    for k in range(8, len(beats)):
        before = rms[k - 8:k].mean()
        if before > 0 and rms[k] > 2.0 * before and (not drops or beats[k] - drops[-1] > 8):
            drops.append(float(beats[k]))
    return BeatMap(round(tempo, 2), [round(float(b), 4) for b in beats],
                   [round(float(b), 4) for b in downbeats], drops)
