"""The edit as data: an ordered list of shots, so phone feedback becomes exact, repeatable edits.

Resolve's scripting API can append clips with exact source in/out points but cannot trim, move or retime items on an
existing timeline. So the cut list is the source of truth and every revision rebuilds the timeline from it
(REEL_v1, REEL_v2, ...). Shot numbers are 1-based and match the contact sheet sent to the phone.

cutlist.json:
{ "name": "REEL", "fps": 29.97, "music": "D:/FPV/Music/x.mp3" | null, "music_in_s": 0.0,
  "beats_s": [...optional beat grid in timeline seconds...],
  "shots": [ {"src": "D:/FPV/Stabilized/.../GX010042_stabilized.mov", "in_s": 12.4, "out_s": 14.3, "note": "",
               "grade": {"mid": 0.36, "clarity": 0.35}}, ... ] }
"""
from __future__ import annotations

import json

import numpy as np
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class Shot:
    src: str
    in_s: float
    out_s: float
    note: str = ""
    grade: dict = field(default_factory=dict)   # per-shot grade overrides (fivenode targets, "clarity", "sky_grad")
    speed: float = 1.0                           # playback speed: 1.25 = 25% faster (source span / speed on the timeline)
    gyroflow: dict = field(default_factory=dict)  # per-shot Gyroflow plugin overrides, e.g. {"smoothness": 35}
    manual: bool = False                          # graded by hand in Resolve: --regrade leaves this shot's nodes alone
    target: dict = field(default_factory=dict)    # what scripts/converge_grade.py steers to: {"med", "p1", "chroma", "clip"}
    frame: dict = field(default_factory=dict)     # 9:16 reframe from the full sensor: {"x": 0.5, "x_end": 0.6, "zoom": 1.0}
                                                  # x = window centre as a fraction of source width (fastpreview.reframe)
    ramp: list = field(default_factory=list)      # speed ramp [[source_s, speed], ...] eased between points (fpvpipe/ramp.py);
                                                  # when set, `speed` is ignored and the shot lasts the integral of 1/speed
    trans_in: dict = field(default_factory=dict)  # transition INTO this shot, centred on the cut: {"type": "flash" | "flow" |
                                                  # "dissolve" | "cut", "dur": s, "dir": "left"} (fpvpipe/transitions.py)

    @property
    def trans_dur(self) -> float:                # seconds of transition into this shot (0 = hard cut)
        return float(self.trans_in.get("dur", 0.0)) if self.trans_in.get("type", "cut") != "cut" else 0.0

    @property
    def dur(self) -> float:                      # duration on the timeline
        if self.ramp:
            from fpvpipe import ramp as _ramp
            return _ramp.duration(self.ramp, self.in_s, self.out_s)
        return (self.out_s - self.in_s) / self.speed

    @property
    def avg_speed(self) -> float:                # the constant speed with the same source span and timeline length
        return self.span / self.dur

    @property
    def span(self) -> float:                     # duration of source used
        return self.out_s - self.in_s


@dataclass
class CutList:
    name: str
    fps: float
    shots: list[Shot] = field(default_factory=list)
    music: str | None = None
    music_in_s: float = 0.0
    beats_s: list[float] = field(default_factory=list)
    version: int = 1
    titles: list[dict] = field(default_factory=list)   # text overlays, see fpvpipe/titles.py
    sfx: list[dict] = field(default_factory=list)      # placed sound effects, see fpvpipe/sfx.py
    fade_out_s: float = 0.0                              # fade picture and music to black over the last N seconds

    # ---------------------------------------------------------------- io
    @classmethod
    def load(cls, path: Path) -> "CutList":
        d = json.loads(Path(path).read_text())
        d["shots"] = [Shot(**s) for s in d.get("shots", [])]
        return cls(**d)

    def save(self, path: Path):
        Path(path).write_text(json.dumps(asdict(self), indent=2))

    # ---------------------------------------------------------------- helpers
    def _i(self, n: int) -> int:
        if not 1 <= n <= len(self.shots):
            raise IndexError(f"shot {n} doesn't exist (1..{len(self.shots)})")
        return n - 1

    def frames(self, s: float) -> int:
        return int(round(s * self.fps))

    def starts(self) -> list[float]:
        t, out = 0.0, []
        for s in self.shots:
            out.append(t)
            t += s.dur
        return out

    def duration(self) -> float:
        return sum(s.dur for s in self.shots)

    def table(self) -> str:
        rows = [f"{'#':>2}  {'at':>6}  {'len':>5}  source"]
        for n, (s, t) in enumerate(zip(self.shots, self.starts()), 1):
            rows.append(f"{n:>2}  {t:6.2f}  {s.dur:5.2f}  {Path(s.src).name} [{s.in_s:.2f}-{s.out_s:.2f}]"
                        + (f"  ramp {' '.join(f'{v:g}x' for _, v in s.ramp)}" if s.ramp else
                           f"  {s.speed:g}x" if s.speed != 1.0 else "")
                        + (f"  ({s.note})" if s.note else ""))
        rows.append(f"total {self.duration():.2f}s")
        return "\n".join(rows)

    # ---------------------------------------------------------------- edit operations (what phone notes map to)
    def trim(self, n: int, head_frames: int = 0, tail_frames: int = 0):
        """Positive = shorten (head: start later, tail: end earlier); negative = extend."""
        s = self.shots[self._i(n)]
        new_in, new_out = s.in_s + head_frames / self.fps, s.out_s - tail_frames / self.fps
        if new_out - new_in < 2 / self.fps:
            raise ValueError(f"shot {n} would be shorter than 2 frames")
        s.in_s, s.out_s = max(0.0, new_in), new_out

    def slip(self, n: int, frames: int):
        """Same length, different moment: positive = later in the source."""
        s = self.shots[self._i(n)]
        d = frames / self.fps
        if s.in_s + d < 0:
            raise ValueError("can't slip before the start of the source")
        s.in_s, s.out_s = s.in_s + d, s.out_s + d

    def move(self, n: int, to: int):
        s = self.shots.pop(self._i(n))
        self.shots.insert(max(0, min(len(self.shots), to - 1)), s)

    def swap(self, a: int, b: int):
        i, j = self._i(a), self._i(b)
        self.shots[i], self.shots[j] = self.shots[j], self.shots[i]

    def delete(self, n: int):
        self.shots.pop(self._i(n))

    def insert(self, at: int, src: str, in_s: float, out_s: float, note: str = ""):
        self.shots.insert(max(0, min(len(self.shots), at - 1)), Shot(src, in_s, out_s, note))

    def replace(self, n: int, src: str, in_s: float, out_s: float | None = None, note: str = ""):
        """Keep the slot length unless out_s is given (so the beat structure stays intact)."""
        old = self.shots[self._i(n)]
        self.shots[self._i(n)] = Shot(src, in_s, out_s if out_s is not None else in_s + old.dur, note)

    def set_ramp(self, n: int, points: list, keep_dur: bool = True, source_end_s: float | None = None) -> float:
        """Speed ramp on shot n ([[source_s, speed], ...]); keep_dur moves the out-point so the shot keeps its timeline
        length (its beats). Returns the new out_s. An empty list removes the ramp (back to `speed`)."""
        from fpvpipe import ramp as _ramp
        s = self.shots[self._i(n)]
        if not points:
            s.ramp = []
            return s.out_s
        pts = sorted([float(a), float(b)] for a, b in points)
        if min(v for _, v in pts) <= 0:
            raise ValueError("ramp speeds must be positive")
        if keep_dur:
            s.out_s = _ramp.fit_out(pts, s.in_s, s.dur, source_end_s)
        s.ramp = pts
        return s.out_s

    def set_length(self, n: int, seconds: float, keep: str = "head"):
        """keep='head' trims/extends the tail; keep='tail' the head; keep='center' both sides."""
        s = self.shots[self._i(n)]
        d = seconds - s.dur
        if keep == "head":
            s.out_s += d
        elif keep == "tail":
            s.in_s = max(0.0, s.in_s - d)
        else:
            s.in_s, s.out_s = max(0.0, s.in_s - d / 2), s.out_s + d / 2

    def snap_to_beats(self, keep: str = "head", min_beats: int = 1) -> list[str]:
        """Make every cut land on the nearest beat (lengths rounded to whole frames). Returns change notes."""
        if len(self.beats_s) < 2:
            raise ValueError("no beat grid (beats_s) in the cut list")
        notes, t = [], 0.0
        for n, s in enumerate(self.shots, 1):
            end = t + s.dur
            cands = [b for b in self.beats_s if b > t + 0.5 / self.fps]
            if not cands:
                break
            target = min(cands, key=lambda b: abs(b - end))
            beats_used = sum(1 for b in self.beats_s if t < b <= target + 1e-6)
            if beats_used < min_beats and len(cands) >= min_beats:
                target = cands[min_beats - 1]
            new_len = round((target - t) * self.fps) / self.fps
            if abs(new_len - s.dur) >= 0.5 / self.fps:
                notes.append(f"shot {n}: {s.dur:.2f}s -> {new_len:.2f}s")
                self.set_length(n, new_len, keep)
            t += self.shots[n - 1].dur
        return notes


def first_cut(moments: list[dict], beats_s: list[float], downbeats_s: list[float], drop_s: float | None,
              fps: float, target_s: float = 24.0, name: str = "REEL") -> CutList:
    """Automatic v1 from ranked highlight moments and the music's beat grid (STYLE_GUIDE.md beat sheet).

    moments: [{"src": path, "peak_s": source time of the move's peak, "score": ...}, ...] best first.
    Slots: bar-length shots up to the drop (hook = best non-drop move first), half-bar shots after it.
    Each move's peak is centred in its slot; the best move's peak lands exactly ON the drop."""
    if len(downbeats_s) < 2:
        raise ValueError("need a beat grid with downbeats (run beat_map.py / guide_track.py)")
    bar = float(np.median(np.diff(downbeats_s)))
    drop = drop_s if drop_s is not None else downbeats_s[0] + 4 * bar
    # Start the reel a whole number of bars BEFORE the drop, so the drop always falls exactly on a cut
    # (a song's drop is rarely a whole number of bars after its first detected downbeat).
    n_pre = max(0, int(np.floor((drop - downbeats_s[0]) / bar + 1e-6)))
    t0 = drop - n_pre * bar
    drop_rel = drop - t0
    edges, t = [0.0], 0.0
    while t < target_s - 1e-6:
        t = round(t + (bar if t < drop_rel - 1e-6 else bar / 2), 6)
        edges.append(min(t, target_s))
    slots = [(a_, b_) for a_, b_ in zip(edges[:-1], edges[1:]) if b_ - a_ >= 2 / fps]   # no sliver shots
    if not moments:
        raise ValueError("no highlight moments")
    best, rest = moments[0], moments[1:] or moments[:1]
    c = CutList(name, fps, beats_s=[round(b - t0, 4) for b in beats_s if b >= t0 - 1e-6])
    c.music_in_s = t0
    k = 0
    for a_, b_ in slots:
        dur = b_ - a_
        if abs(a_ - drop_rel) < 1e-6:
            m, kind = best, "drop"
            in_s = max(0.0, m["peak_s"] - min(dur / 2, 0.25))   # peak a few frames after the cut reads as ON the drop
        else:
            m, kind = rest[k % len(rest)], "slot"
            reuse = k // len(rest)                               # reused moment: show a later part of it, not the same frames
            in_s = max(0.0, m["peak_s"] - dur / 2 + reuse * dur)
            k += 1
        in_s = round(in_s * fps) / fps
        c.shots.append(Shot(m["src"], in_s, in_s + round(dur * fps) / fps,
                            f"{kind} {m.get('label', '')} score {m.get('score', '')}".strip()))
    return c
