"""The Instagram master: one render spec ([reel] in config.toml) and one verdict, READY FOR EXPORT or not.

Instagram re-encodes every upload to ~3-6 Mbps, so what matters is a clean 1080x1920 H.264 High master at 15-20 Mbps
with 1-1-1 colour tags (anything else reads washed out on iPhones), AAC 48 kHz stereo, loud enough that Instagram's
normaliser (-14 LUFS) does not have to turn it up, and under the length limit. `render()` sets exactly that on the
Deliver page and renders; `checks()` then reads the FILE back with ffprobe/ffmpeg, so the verdict is about what is on
disk, not about what was asked for. Used by scripts/render.py reel and build_reel_project.py --final.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

IG_MAX_MB = 1000            # Instagram allows more, but a reel master this size means something went wrong
IG_MIN_SECONDS = 3.0
LOUDNESS_TARGET = -14.0     # LUFS integrated: Instagram's normalisation point; quieter gets turned up (hiss, pumping)
LOUDNESS_FLOOR = -18.0
TRUE_PEAK_MAX = -1.0        # dBTP; hotter clips when the AAC re-encode rings


def settings(spec: dict, out_dir: Path, name: str) -> dict:
    """Resolve RenderSettings for the reel master (keys per the scripting API's RenderSettings)."""
    s = {
        "SelectAllFrames": True,
        "TargetDir": str(out_dir),
        "CustomName": name,
        "FormatWidth": int(spec["width"]),
        "FormatHeight": int(spec["height"]),
        "FrameRate": str(spec["fps"]),
        "VideoQuality": int(spec["bitrate_kbps"]),
        "ExportVideo": True,
        "ExportAudio": True,
        "AudioCodec": "aac",
        "AudioSampleRate": 48000,
        "AudioBitDepth": 16,
    }
    if spec.get("profile"):                      # H.264 High: 8-bit 4:2:0 with CABAC + B-frames, what phones decode best
        s["EncodingProfile"] = spec["profile"]
    if spec.get("multipass"):
        s["MultiPassEncode"] = True
    # 1-1-1 colour tags (Rec.709 primaries / transfer / matrix). Resolve's default Gamma 2.4 tag is 1-2-1, which
    # YouTube/Instagram/QuickTime interpret differently -> washed-out uploads.
    for key, cfg_key in (("ColorSpaceTag", "color_space_tag"), ("GammaTag", "gamma_tag")):
        if spec.get(cfg_key):
            s[key] = spec[cfg_key]
    return s


def render(project, tl, spec: dict, out_dir: Path, name: str, no_music: bool = False, music_track: int = 2,
           log=print) -> Path:
    """Render `tl` with the reel spec to <out_dir>/<name>.mp4 (name gets _nomusic when the song track is muted).
    Blocks until Resolve finishes; raises RuntimeError with Resolve's message when the job fails."""
    out_dir.mkdir(parents=True, exist_ok=True)
    name = name + ("_nomusic" if no_music else "")
    project.SetCurrentTimeline(tl)
    if not project.SetCurrentRenderFormatAndCodec("mp4", spec["codec"]):
        raise RuntimeError(f"Resolve rejected mp4/{spec['codec']} - check project.GetRenderCodecs('mp4')")
    if not project.SetRenderSettings(settings(spec, out_dir, name)):
        log("warning: some render settings were rejected; the checks below read the file, trust those")
    if no_music and not tl.SetTrackEnable("audio", music_track, False):
        raise RuntimeError(f"could not mute audio track A{music_track}; mute it by hand and render without --no-music")
    try:
        job = project.AddRenderJob()
        if not job:
            raise RuntimeError("AddRenderJob failed (is the timeline empty or the output path unwritable?)")
        project.StartRendering([job])
        t0 = time.time()
        while project.IsRenderingInProgress():
            st = project.GetRenderJobStatus(job)
            log(f"\r{st.get('JobStatus')} {st.get('CompletionPercentage', 0)}%", end="", flush=True)
            time.sleep(2)
        st = project.GetRenderJobStatus(job)
        log(f"\n{st.get('JobStatus')} in {time.time() - t0:.0f}s")
        if st.get("JobStatus") != "Complete":
            raise RuntimeError(st.get("Error") or "render failed: check the Deliver page")
    finally:
        if no_music:
            tl.SetTrackEnable("audio", music_track, True)
    return out_dir / f"{name}.mp4"


def set_loudness(path: Path, target_lufs: float = -14.0, peak_max: float = -1.0) -> tuple[float, float]:
    """Linear gain on the audio to reach target_lufs (never past peak_max), 48 kHz AAC, picture stream-copied. In place.
    A mastered song sits near -8 LUFS and clips the AAC encode; the platforms turn it down anyway. Returns (before, gain)."""
    now = loudness(path)
    if now is None:
        return float("nan"), 0.0
    lufs, peak = now
    gain = min(target_lufs - lufs, peak_max - peak)
    tmp = path.with_name(path.stem + "_lufs" + path.suffix)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0:v", "-map", "0:a", "-c:v", "copy",
                    "-af", f"volume={gain:.2f}dB,aresample=48000", "-c:a", "aac", "-b:a", "256k", "-ac", "2",
                    "-movflags", "+faststart", str(tmp)], check=True)
    tmp.replace(path)
    return lufs, gain


# ---------------------------------------------------------------------------------------------- reading the file back
def probe(path: Path) -> dict:
    """What is actually in the file: picture, codec/profile, bitrate, colour tags, audio, length, size."""
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True).stdout
    d = json.loads(out)
    v = next((s for s in d["streams"] if s["codec_type"] == "video"), {})
    a = next((s for s in d["streams"] if s["codec_type"] == "audio"), None)
    num, _, den = (v.get("r_frame_rate") or "0/1").partition("/")
    fps = float(num) / float(den or 1)
    return {
        "width": int(v.get("width", 0)), "height": int(v.get("height", 0)), "fps": fps,
        "codec": v.get("codec_name", "?"), "profile": v.get("profile", "?"),
        "bitrate_kbps": int(v.get("bit_rate") or d["format"].get("bit_rate") or 0) // 1000,
        "tags": (v.get("color_primaries"), v.get("color_transfer"), v.get("color_space")),
        "seconds": float(d["format"].get("duration") or 0),
        "mb": Path(path).stat().st_size / 1e6,
        "audio": None if a is None else {"codec": a.get("codec_name"), "rate": int(a.get("sample_rate") or 0),
                                         "channels": int(a.get("channels") or 0),
                                         "bitrate_kbps": int(a.get("bit_rate") or 0) // 1000},
    }


def loudness(path: Path) -> tuple[float, float] | None:
    """(integrated LUFS, true peak dBTP) from ffmpeg's ebur128 meter; None when the file has no audio."""
    r = subprocess.run(["ffmpeg", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"],
                       capture_output=True, text=True)
    # the meter prints a running "I:" every 100 ms and the whole-file value in its Summary at the end: take the last
    i = re.findall(r"I:\s+(-?[\d.]+) LUFS", r.stderr)
    p = re.findall(r"Peak:\s+(-?[\d.]+) dBFS", r.stderr)
    if not i or not p:
        return None
    return float(i[-1]), float(p[-1])


@dataclass
class Check:
    ok: bool
    name: str
    detail: str


def checks(info: dict, spec: dict, lufs: tuple[float, float] | None = None, graded: int | None = None,
           shots: int | None = None, manual: int = 0, stabilized: int = 0, music: str | None = None,
           no_music: bool = False) -> list[Check]:
    """Instagram readiness, strictest first. `graded`/`shots`/`manual`/`stabilized` come from the build (None = unknown);
    `music` is the song's name when the cut list has one ("" = cut list without music, None = no cut list to ask);
    `no_music` when it was deliberately left out for Edits."""
    out = []
    w, h, fps = int(spec["width"]), int(spec["height"]), float(spec["fps"])
    pic_ok = (info["width"], info["height"]) == (w, h) and abs(info["fps"] - fps) < 0.05
    # Resolve 21.1 ignores EncodingProfile for H.264 and writes Main; Instagram takes Main and High alike, so the
    # profile is reported, not gated
    codec_ok = info["codec"] in ("h264", "hevc") and info["profile"] in ("High", "Main", spec.get("profile", "High"))
    kb = info["bitrate_kbps"]
    # Resolve's encoder lands anywhere from ~half to the requested rate on clean footage; what must not happen is a
    # starved file (< 8 Mbps at 1080p) or one Instagram refuses outright
    rate_ok = 8000 <= kb <= spec.get("max_kbps", 60000)      # a 4K YouTube master carries far more than a reel
    out.append(Check(pic_ok and codec_ok and rate_ok, "picture",
                     f"{info['width']}x{info['height']} {info['fps']:.2f}p {info['codec'].upper()} {info['profile']}, {kb / 1000:.1f} Mbps"
                     + ("" if pic_ok else f"  (want {w}x{h} {fps}p)") + ("" if codec_ok else "  (want H.264 High or Main)")
                     + ("" if rate_ok else "  (want 15-20 Mbps)")))
    tags = info["tags"]
    out.append(Check(tags == ("bt709", "bt709", "bt709"), "color",
                     "tags 1-1-1 (bt709)" if tags == ("bt709", "bt709", "bt709") else
                     f"tags {'/'.join(str(t) for t in tags)} -> set Deliver > Advanced > Gamma tag = Rec.709 and re-render"))
    a = info["audio"]
    if a is None:
        out.append(Check(False, "audio", "no audio stream"))
    else:
        a_ok = a["codec"] == "aac" and a["rate"] == 48000 and a["channels"] == 2
        song = ("song left out: add it in Edits / Instagram" if no_music else
                f"music baked in ({music})" if music else "no music in the cut list" if music == "" else "")
        out.append(Check(a_ok, "audio", f"{(a['codec'] or '?').upper()} {a['rate'] // 1000} kHz "
                         f"{'stereo' if a['channels'] == 2 else str(a['channels']) + 'ch'}"
                         + (f" {a['bitrate_kbps']} kbps" if a["bitrate_kbps"] else "") + (f", {song}" if song else "")))
        if lufs is not None:
            li, pk = lufs
            l_ok = LOUDNESS_FLOOR <= li <= LOUDNESS_TARGET + 2 and pk <= TRUE_PEAK_MAX + 0.5
            why = ("" if l_ok else f"  (want {LOUDNESS_FLOOR:.0f}..{LOUDNESS_TARGET:.0f} LUFS, peak <= {TRUE_PEAK_MAX:.0f} dBTP)")
            out.append(Check(l_ok or no_music, "loudness", f"{li:.1f} LUFS, peak {pk:.1f} dBTP{why}"))
    sec, limit = info["seconds"], float(spec.get("max_seconds", 90))
    out.append(Check(IG_MIN_SECONDS <= sec <= limit, "length", f"{sec:.1f} s (limit {limit:.0f})"))
    out.append(Check(info["mb"] <= spec.get("max_mb", IG_MAX_MB), "size", f"{info['mb']:.0f} MB"))
    if graded is not None and shots is not None:
        out.append(Check(graded == shots, "grades", f"{graded}/{shots} shots graded"
                         + (f", {manual} by hand" if manual else "") + (f", Gyroflow on {stabilized}" if stabilized else "")
                         + ("" if graded == shots else f"  ({shots - graded} UNGRADED: run review.py fast --look cst5, then --regrade)")))
    return out


def report(path: Path, items: list[Check], next_step: str | None = None) -> str:
    """The block printed at the end of a final render: a loud verdict, then one line per check."""
    bad = [c for c in items if not c.ok]
    head = "READY FOR EXPORT" if not bad else f"NOT READY FOR EXPORT - {len(bad)} problem(s)"
    bar = "=" * 20
    lines = [f"{bar} {head} {bar}", f"  {path}"]
    for c in items:
        lines.append(f"  [{'ok' if c.ok else '!!'}] {c.name:<9}{c.detail}")
    if next_step and not bad:
        lines.append(f"  Next: {next_step}")
    lines.append("=" * (len(lines[0])))
    return "\n".join(lines)


NEXT_PHONE = ("AirDrop / Files to the phone (not Messages), Instagram > Reel. Once: Settings > Media quality > "
              "'Upload at highest quality' on. Keep titles out of the top 220 / bottom 420 px.")
NEXT_EDITS = ("import into Edits, add the song from the library at the cue card's start time "
              "(reel_handoff.py), then post from Edits.")


def verdict(path: Path, spec: dict, no_music: bool = False, **build) -> tuple[bool, str]:
    """probe + loudness + checks + report in one call. **build = graded/shots/manual/stabilized/music."""
    info = probe(path)
    items = checks(info, spec, loudness(path) if info["audio"] else None, no_music=no_music, **build)
    ok = all(c.ok for c in items)
    return ok, report(path, items, NEXT_EDITS if no_music else NEXT_PHONE)
