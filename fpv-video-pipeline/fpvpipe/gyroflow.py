"""Gyroflow inside Resolve: the OpenFX plugin as a Fusion comp on each timeline clip (no intermediates).

Resolve's API cannot put an OFX on a Color page node, but the plugin is also a Fusion tool (ofx.xyz.gyroflow), and
a clip's Fusion comp runs before the Color page, so the 5-node grade sees the stabilized log image.
Per clip: MediaIn -> Gyroflow -> MediaOut, with
  ProjectPath = the source file (the plugin reads the embedded gyro and picks the lens profile itself),
  output size = source size (the plugin defaults to 3840x2160, which would squash an 8:7 or 4:3 frame),
  VideoSpeed  = 100: a clip's Fusion comp runs in un-retimed source frames (the Edit-page retime is applied to the
                comp's output afterwards - measured 2026-11-03 with TimeStretcher keys), so the plugin already sees
                real source time. Telling it the clip's retime would mis-map the gyro on every sped-up clip.
Clips without gyro (stabilized exports, re-encodes) are skipped: stabilizing twice warps the image.

Never call comp.Render() from a script: Fusion opens a modal "Render completed!" dialog and Resolve stops answering
the scripting API until someone clicks OK.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import media

PLUGIN_ID = "ofx.xyz.gyroflow"
BUNDLE = {
    "win32": Path(r"C:\Program Files\Common Files\OFX\Plugins\Gyroflow.ofx.bundle"),
    "darwin": Path("/Library/OFX/Plugins/Gyroflow.ofx.bundle"),
    "linux": Path("/usr/OFX/Plugins/Gyroflow.ofx.bundle"),
}
# Gyroflow app defaults; config.toml [gyroflow.<preset>] overrides per key. Zoom limit is in percent (130 = 1.3x).
DEFAULTS = {"smoothness": 50.0, "zoom_limit": 130.0, "lens_correction": 100.0, "horizon_lock": 0.0}
INPUTS = {"smoothness": "Smoothness", "zoom_limit": "ZoomLimit", "lens_correction": "LensCorrectionStrength",
          "horizon_lock": "HorizonLockAmount"}


def plugin_installed() -> bool:
    return BUNDLE.get(sys.platform, BUNDLE["linux"]).exists()


def mode(cfg: dict) -> str:
    """"ofx" (plugin on each clip in Resolve), "intermediates" (stabilized exports only) or "off"."""
    return cfg.get("gyroflow", {}).get("mode", "intermediates")


def preset(cfg: dict, name: str) -> dict:
    return {**DEFAULTS, **cfg.get("gyroflow", {}).get(name, {})}


def has_gyro(path: Path) -> bool:
    """GoPro: a GPMF stream. DJI (Avata 2, EIS off): a 'dbgi' data stream (verify on your own files)."""
    try:
        probe = media.ffprobe(path)
    except Exception:
        return False
    return media.gpmf_stream_index(probe) is not None or any(
        s.get("codec_tag_string") == "dbgi" for s in probe.get("streams", []))


def find(item):
    """The Gyroflow tool already on this timeline item, or None."""
    for i in range(1, (item.GetFusionCompCount() or 0) + 1):
        comp = item.GetFusionCompByIndex(i)
        for t in (comp.GetToolList(False) or {}).values():
            if t.GetAttrs().get("TOOLS_RegID") == PLUGIN_ID:
                return t
    return None


def apply(item, settings: dict, gyro_cache: dict[str, bool] | None = None) -> str:
    """Put Gyroflow on one timeline item. Returns what happened: added / updated / no gyro / has comp / failed."""
    mpi = item.GetMediaPoolItem()
    src = mpi.GetClipProperty("File Path") if mpi else ""
    if not src:
        return "no source"
    cache = gyro_cache if gyro_cache is not None else {}
    if src not in cache:
        cache[src] = has_gyro(Path(src))
    if not cache[src]:
        return "no gyro"
    gf = find(item)
    status = "updated"
    if gf is None:
        if item.GetFusionCompCount():
            return "has comp"             # someone's own Fusion work: leave it, add Gyroflow by hand
        comp = item.AddFusionComp()
        tools = {t.GetAttrs()["TOOLS_RegID"]: t for t in (comp.GetToolList(False) or {}).values()}
        mi, mo = tools.get("MediaIn"), tools.get("MediaOut")
        if not (mi and mo):
            return "failed"
        comp.Lock()
        gf = comp.AddTool(PLUGIN_ID, -32768, -32768)
        if gf:
            gf.Source = mi.Output
            mo.Input = gf.Output
        comp.Unlock()
        if not gf:
            return "failed"
        status = "added"
    comp = gf.Comp()
    w, _, h = (mpi.GetClipProperty("Resolution") or "").partition("x")
    comp.Lock()
    gf.ProjectPath = src
    if w and h:
        gf.OutputWidth, gf.OutputHeight = float(w), float(h)
    gf.VideoSpeed = 100.0                 # Fusion time is source time; the Edit-page retime comes after the comp
    for key, inp in INPUTS.items():
        gf.SetInput(inp, float(settings[key]))
    comp.Unlock()
    return status


def remove(item) -> bool:
    """Delete the Fusion comp that holds Gyroflow (only comps this module made: MediaIn, Gyroflow, MediaOut)."""
    for i in range(1, (item.GetFusionCompCount() or 0) + 1):
        c = item.GetFusionCompByIndex(i)
        regs = {t.GetAttrs()["TOOLS_RegID"] for t in (c.GetToolList(False) or {}).values()}
        if PLUGIN_ID in regs and regs <= {"MediaIn", "MediaOut", PLUGIN_ID, "AudioDisplay"}:
            return bool(item.DeleteFusionCompByName(item.GetFusionCompNameList()[i - 1]))
    return False


def stabilize_timeline(tl, settings: dict, track: int = 1,
                       overrides: list[dict] | None = None) -> dict[str, list[str]]:
    """apply() on every clip of one video track; overrides[i] (e.g. a cut list shot's "gyroflow") tweaks clip i.
    Returns {status: [clip names]}."""
    out: dict[str, list[str]] = {}
    cache: dict[str, bool] = {}
    for i, it in enumerate(tl.GetItemListInTrack("video", track) or []):
        extra = overrides[i] if overrides and i < len(overrides) else {}
        out.setdefault(apply(it, {**settings, **(extra or {})}, cache), []).append(it.GetName())
    return out
