"""Sound design on a cut list: which ElevenLabs sound goes where, placed from the music and the picture.

cutlist.json: "sfx": [{"file": path, "at": timeline seconds, "gain_db": -9, "dur": seconds (optional), "loop": false,
                       "fade_out": seconds (optional), "track": 1 (Resolve audio track)}, ...]

The rules (docs/AUDIO_AND_PUBLISHING.md):
  whoosh    a cut with real motion on either side, its loudest point on the cut; never closer than MIN_GAP_S
  sub hit   the hardest cuts in the music, and any shot whose note starts with "DROP"
  riser     ends exactly on a DROP that has room before it
  dive wind under the shot with the most motion
  beds      wind and prop wash far under everything, for air
Sound is there to sell speed. Music stays on top, so everything is well below it: a reel that sounds like a sound pack is
worse than one with no effects. Variants are used in turn, so repeated whooshes are not the same file.
"""
from __future__ import annotations

from pathlib import Path

from . import cutlist as cl

MIN_GAP_S = 1.2
MOTION_MIN = 0.45
LEAD_S = {"whoosh_short": 0.25, "whoosh_long": 0.8, "whoosh_reverse": 1.05}   # start this long before the cut
GAIN = {"whoosh_short": -9.0, "whoosh_long": -11.0, "whoosh_reverse": -12.0, "sub_hit": -7.0, "riser_1bar": -15.0,
        "riser_2bar": -15.0, "dive_wind": -17.0, "wind_rush_loop": -27.0, "prop_wash_loop": -31.0}
TRACK_FX, TRACK_BED = 1, 3                # the song is on the [reel] music_track, A2


def library(root: Path) -> dict[str, list[Path]]:
    """{sound name: its variants} from <SFX>/<category>/<name>_<n>.mp3."""
    out: dict[str, list[Path]] = {}
    for p in sorted(Path(root).glob("*/*.mp3")):
        out.setdefault(p.stem.rsplit("_", 1)[0], []).append(p)
    return out


class _Takes:
    def __init__(self, lib):
        self.lib, self.n = lib, {}

    def next(self, name: str) -> Path | None:
        v = self.lib.get(name)
        if not v:
            return None
        k = self.n.get(name, 0)
        self.n[name] = k + 1
        return v[k % len(v)]


def plan(c: cl.CutList, lib: dict[str, list[Path]], cuts: list, bar_s: float | None = None) -> list[dict]:
    """Placements for a cut list. cuts = transitions.analyse(c): one per cut, with music onset, loudness jump and motion."""
    takes, out, total = _Takes(lib), [], c.duration()

    def add(name, at, **kw):
        f = takes.next(name)
        if f is not None and at < total:
            out.append({"file": str(f), "at": round(max(0.0, at), 3), "gain_db": GAIN.get(name, -12.0), "name": name,
                        "track": TRACK_FX, **kw})
    starts = c.starts()
    drops = [t for t, s in zip(starts, c.shots) if s.note.startswith("DROP")]
    strongest = sorted(cuts, key=lambda k: (k.jump_db, k.onset), reverse=True)[:1]
    hits = sorted(set(drops) | {k.t for k in strongest if k.jump_db >= 2.0})
    last = -9.0
    for k in cuts:
        if max(k.motion_out, k.motion_in) < MOTION_MIN or k.t - last < MIN_GAP_S:
            continue
        name = "whoosh_short"
        add(name, k.t - LEAD_S[name])
        last = k.t
    for t in hits:
        add("sub_hit", t)
    for t in drops:                         # a riser needs a bar of room, so a drop on the first frame goes without
        room = t - (bar_s or 2.7)
        if room >= 0:
            add("riser_1bar", room, dur=bar_s or 2.7)
    if len(c.shots) > 1 and cuts:
        k = max(range(len(cuts)), key=lambda j: cuts[j].motion_in)
        add("dive_wind", cuts[k].t, fade_out=0.5, dur=min(3.0, c.shots[k + 1].dur))
    add("wind_rush_loop", 0.0, loop=True, dur=total, fade_out=1.0, track=TRACK_BED)
    return out


def mix_inputs(sfx: list[dict], total: float, first_input: int, music: str | None) -> tuple[list[str], list[str], str]:
    """ffmpeg inputs and filter lines mixing the placements with the music label. Returns (inputs, lines, out label).
    normalize=0 keeps the music at its level; loudness is set on the master afterwards."""
    inputs, lines, labels = [], [], [music] if music else []
    for k, s in enumerate(sfx):
        i = first_input + k
        inputs += (["-stream_loop", "-1"] if s.get("loop") else []) + ["-i", s["file"]]
        ms = int(round(s["at"] * 1000))
        f = "aresample=48000,aformat=channel_layouts=stereo"
        if s.get("dur"):
            f += f",atrim=0:{float(s['dur']):.3f},asetpts=PTS-STARTPTS"
        f += f",volume={float(s.get('gain_db', -12)):.1f}dB"
        if s.get("fade_out") and s.get("dur"):
            f += f",afade=t=out:st={max(0.0, float(s['dur']) - float(s['fade_out'])):.3f}:d={float(s['fade_out']):.3f}"
        lines.append(f"[{i}:a]{f},adelay={ms}|{ms}[fx{k}]")
        labels.append(f"[fx{k}]")
    lines.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:duration=longest,atrim=0:{total:.3f}[mix]")
    return inputs, lines, "[mix]"
