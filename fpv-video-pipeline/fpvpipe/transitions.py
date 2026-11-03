"""Transitions between shots, chosen from the music and the motion.

A cut lands on a beat; HOW it lands follows what the music does there and what the picture is doing. The default is
a HARD CUT that lets the motion of the two shots carry the join (the user's taste: "hard cuts that use the motion of
the shots to cleanly transition, occasional super slight dissolve or blur"):
  a beat with any weight (onset, or a loudness jump)  -> "cut"      nothing in the way of the hit
  soft moment, both shots moving fast                 -> "blur"     a 0.2 s blur-through: the blur hides the join,
                                                                    the motion continues
  soft moment, calm outgoing picture                  -> "dissolve" a super slight 0.12 s cross-dissolve
  ("flash" = burst to white, "flow" = directional soft wipe and "sandstorm" = an experimental sand front exist for
  hand use only; auto never picks them, and the editor rejected all three on the MOAB reel - see docs/TRANSITIONS.md)
Stored on the INCOMING shot as "trans_in": {"type": "blur", "dur": 0.2} (dur in seconds, centred on the cut so the
beat stays where it was). The fast preview renders them with ffmpeg's xfade over extended segments;
Resolve gets the matching simple transition (fpvpipe/retime keeps a ramp's curve alive through the handles).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import beats, cutlist as cl, media

# seconds; auto picks only cut / blur / dissolve ("super slight"); flash, flow and sandstorm are for hand use
DUR = {"cut": 0.0, "blur": 0.2, "dissolve": 0.12, "flash": 0.2, "flow": 0.3, "sandstorm": 0.5}
FLASH_STRENGTH = 0.35      # how far a flash lifts towards white at its peak (1.0 = full white, the classic FPV flash)
# ffmpeg xfade transition names; flow's direction = where the picture is travelling TO
XFADE = {"dissolve": "fade", "blur": "hblur",
         "flow": {"left": "smoothleft", "right": "smoothright", "up": "smoothup", "down": "smoothdown"}}
# Resolve's built-in (simple) transitions; Push/Wipe directions cannot be set through the API, so flow falls back
RESOLVE = {"blur": "Blur Dissolve", "dissolve": "Cross Dissolve", "flash": "Additive Dissolve", "flow": "Cross Dissolve",
           "sandstorm": "Non-Additive Dissolve"}      # stand-in: the sand itself is an ffmpeg expression (below)


@dataclass
class CutInfo:
    t: float            # timeline time of the cut
    onset: float        # 0..1 onset strength of the music at the cut, relative to the loudest cut
    jump_db: float      # loudness change across the cut (0.5 s after vs 0.5 s before)
    motion_out: float   # picture change per frame at the end of the outgoing shot (0..1 of the reel's max)
    motion_in: float    # ... at the start of the incoming shot
    direction: str      # where the outgoing picture is travelling: left/right/up/down/none


def music_at_cuts(c: cl.CutList, times: list[float]) -> tuple[list[float], list[float]]:
    """Onset strength (0..1 relative to the strongest cut) and loudness jump in dB at each timeline time."""
    if not (c.music and Path(c.music).exists()):
        return [1.0] * len(times), [0.0] * len(times)
    y, sr = media.decode_audio(Path(c.music))
    env = beats.onset_envelope(y, sr)
    hop = beats.HOP / sr
    ons, jumps = [], []
    for t in times:
        m = c.music_in_s + t
        i = int(round(m / hop))
        ons.append(float(env[max(0, i - 3): i + 4].max()) if i < len(env) else 0.0)
        a, b = int((m - 0.5) * sr), int(m * sr)
        before = float(np.sqrt(np.mean(y[max(0, a):b] ** 2)) + 1e-6)
        after = float(np.sqrt(np.mean(y[b:b + int(0.5 * sr)] ** 2)) + 1e-6)
        jumps.append(20 * np.log10(after / before))
    top = max(ons) or 1.0
    return [o / top for o in ons], jumps


def motion(src: Path, t0: float, t1: float) -> tuple[float, str]:
    """Mean frame-to-frame change over [t0, t1] and the dominant direction of travel (phase correlation of the
    first and last sampled frame), for the hand-over between shots."""
    from . import grading
    frames = grading.sample_frames(src, t0, max(0.05, t1 - t0), n=6, width=96)
    g = [f.astype(np.float32).mean(axis=2) for f in frames]
    e = float(np.mean([np.abs(b - a).mean() for a, b in zip(g, g[1:])])) if len(g) > 1 else 0.0
    if len(g) < 2:
        return e, "none"
    a, b = g[0] - g[0].mean(), g[-1] - g[-1].mean()
    fa, fb = np.fft.fft2(a), np.fft.fft2(b)
    r = fb * np.conj(fa)
    corr = np.fft.ifft2(r / (np.abs(r) + 1e-6)).real
    dy, dx = np.unravel_index(int(np.argmax(corr)), corr.shape)
    h, w = corr.shape
    dy, dx = (dy if dy <= h // 2 else dy - h), (dx if dx <= w // 2 else dx - w)
    if max(abs(dx), abs(dy)) < 2:
        return e, "none"
    # the scene shifts by (dx, dy) on screen; the picture travels the opposite way to where the camera looks
    if abs(dx) >= abs(dy):
        return e, "left" if dx < 0 else "right"
    return e, "up" if dy < 0 else "down"


def analyse(c: cl.CutList) -> list[CutInfo]:
    starts = c.starts()
    cut_times = starts[1:]
    ons, jumps = music_at_cuts(c, cut_times)
    out_m, in_m, dirs = [], [], []
    for prev, nxt in zip(c.shots, c.shots[1:]):
        e_out, d = motion(Path(prev.src), max(prev.in_s, prev.out_s - 0.4), prev.out_s)
        e_in, _ = motion(Path(nxt.src), nxt.in_s, nxt.in_s + 0.4)
        out_m.append(e_out)
        in_m.append(e_in)
        dirs.append(d)
    top = max(out_m + in_m) or 1.0
    return [CutInfo(t, o, j, mo / top, mi / top, d)
            for t, o, j, mo, mi, d in zip(cut_times, ons, jumps, out_m, in_m, dirs)]


def choose(info: CutInfo, soft_onset: float = 0.55, hard_jump_db: float = 2.0) -> dict:
    """Hard cuts by default - the motion of the shots carries the cut. Only a soft moment in the music (weak onset,
    no loudness jump) gets something else, and then barely: a super slight dissolve when the outgoing picture is
    calm, a short blur-through when both shots are fast (the blur hides the mismatch, the motion continues)."""
    if info.onset >= soft_onset or info.jump_db >= hard_jump_db:
        return {"type": "cut", "dur": 0.0}
    if min(info.motion_out, info.motion_in) >= 0.6:
        return {"type": "blur", "dur": DUR["blur"]}
    if info.motion_out < 0.3:
        return {"type": "dissolve", "dur": DUR["dissolve"]}
    return {"type": "cut", "dur": 0.0}


def auto(c: cl.CutList, log: list[str] | None = None) -> list[dict]:
    """Set trans_in on shots 2..n from the music and the motion; returns the chosen transitions."""
    infos = analyse(c)
    chosen = []
    for s, info in zip(c.shots[1:], infos):
        if s.trans_in.get("manual"):             # placed by hand (e.g. a sandstorm): auto leaves it alone
            chosen.append(s.trans_in)
            if log is not None:
                log.append(f"cut at {info.t:5.2f}s: kept by hand -> {s.trans_in['type']} {s.trans_in.get('dur', 0)}s")
            continue
        tr = choose(info)
        s.trans_in = tr
        chosen.append(tr)
        if log is not None:
            log.append(f"cut at {info.t:5.2f}s: onset {info.onset:.2f} jump {info.jump_db:+.1f} dB "
                       f"motion {info.motion_out:.2f}->{info.motion_in:.2f} {info.direction:5s} -> {tr['type']}"
                       + (f" {tr['dur']}s" if tr["dur"] else ""))
    return chosen


def xfade_spec(tr: dict, band: tuple[float, float] | None = None) -> str:
    """The xfade options for a transition ("transition=...", plus the custom expression for a flash or a sandstorm).
    band = (top, bottom) luma pixel rows where the picture is (letterbox): effects that add something to the
    frame - the sand - stay inside it and leave the black bars black."""
    if tr.get("type") == "flow":
        return "transition=" + XFADE["flow"].get(tr.get("dir", "left"), "fade")
    if tr.get("type") == "flash":
        # a dissolve with a lift towards white that peaks mid-way (P runs 1 -> 0): luma only, so colour holds
        lift = int(round(255 * float(tr.get("strength", FLASH_STRENGTH))))
        return (f"transition=custom:expr='min(255\\,A*P+B*(1-P)+{lift}*(1-abs(2*P-1))*eq(PLANE\\,0))'")
    if tr.get("type") == "sandstorm":
        # threads=1: the expression's st()/ld() registers are shared between xfade's slice threads, so with
        # threading the planes race each other and the gate / grain come out scrambled (measured: PLANE-dependent
        # registers flipping per pixel inside one plane)
        return ("transition=custom:expr='"
                + sandstorm_expr(tr.get("dir", "left"), float(tr.get("ease", 2.5)), band) + "':threads=1")
    return "transition=" + XFADE.get(tr.get("type", "cut"), "fade")


def sandstorm_expr(direction: str = "left", ease: float = 2.5, band: tuple[float, float] | None = None) -> str:
    """A sand front blowing across the cut: a noisy, grainy edge sweeps over the frame (left -> right by default),
    switching each 2x2 grain from the outgoing to the incoming shot, with a warm, bright band of airborne sand on the
    front itself. xfade custom expression, evaluated per plane; grains are keyed on 2x2 luma blocks so the chroma
    planes (half size) switch with their luma. P runs 1 -> 0 over the transition.
      ld(0) progress 0..1, eased: a gust starts slow and ramps fast (progress ** ease)
      ld(1) fine grain  ld(8) coarse clouds  ld(2) front position: > 0 means the incoming shot has arrived here
    band keeps the airborne sand inside the picture rows (letterbox bars stay black)."""
    sweep = "(X/W)" if direction == "right" else "(1-X/W)" if direction == "left" else "(Y/H)" if direction == "down" else "(1-Y/H)"
    wind = "-" if direction in ("right", "down") else "+"
    # band is in LUMA pixel rows; chroma planes (half height) are compared at 2*Y (ld(6) = 2 for luma, 1 for chroma)
    inside = f"*gte(Y*2/ld(6)\\,{band[0]:.1f})*lt(Y*2/ld(6)\\,{band[1]:.1f})" if band else ""
    # sand colour, Rec.709 video range: a light orange (R225 G175 B110) -> Y 171 Cb 94 Cr 152
    e = (f"st(0\\,pow(1-P\\,{ease:.3f}));"
         "st(6\\,1+eq(PLANE\\,0));"                                   # luma coords -> chroma units, so planes agree
         "st(3\\,X/ld(6));st(4\\,Y/ld(6));st(7\\,W/ld(6));"
         # fine streaks (6x2 luma px) streaming with the wind, re-rolled 20x a second of progress
         f"st(1\\,abs(sin(floor((ld(3){wind}ld(0)*ld(7)*0.6)/3)*12.9898+floor(ld(4))*78.233+floor(ld(0)*20)*3.7))*43758.5453);"
         "st(1\\,ld(1)-floor(ld(1)));"
         # the front: a smooth, wavy edge (two slow sine undulations that drift with the gust); hashed block noise on
         # the edge read as a mosaic, and a per-grain mix of two different pictures reads as static
         "st(8\\,Y/H);"
         f"st(2\\,ld(0)*2.0-{sweep}*0.8-0.12*sin(ld(8)*14+ld(0)*9)-0.08*sin(ld(8)*37-ld(0)*5)-0.1);"
         # a thick, nearly opaque body of airborne sand on the front, its density streaked by the fine grain
         "st(5\\,max(0\\,1-abs(ld(2))/0.6));st(5\\,pow(ld(5)\\,0.7)*(0.72+0.28*ld(1)));"
         f"st(5\\,min(0.95\\,ld(5)){inside});"
         "if(eq(PLANE\\,0)\\,if(gt(ld(2)\\,0)\\,B\\,A)*(1-ld(5))+171*ld(5)\\,"
         "if(eq(PLANE\\,1)\\,if(gt(ld(2)\\,0)\\,B\\,A)*(1-ld(5))+94*ld(5)\\,if(gt(ld(2)\\,0)\\,B\\,A)*(1-ld(5))+152*ld(5)))")
    return e


def xfade_name(tr: dict) -> str:
    return xfade_spec(tr).split(":")[0].split("=", 1)[1]
