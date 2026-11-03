"""Speed ramps in Resolve (Resolve's API has no retime keyframes, so the curve lives in the clip's Fusion comp).

Per ramped shot the builder places the clip at a CONSTANT speed (ramp.base_speed: covers the source span, steps whole
comp frames, clip exactly as long as the ramp makes it) and this module adds a TimeStretcher after Gyroflow (or
MediaIn) whose SourceTime is keyframed from
fpvpipe/ramp.py: one key per timeline frame, so the curve is the same one the ffmpeg preview showed. A clip's Fusion
comp runs in un-retimed SOURCE frames (the source's own frame rate); the Edit-page retime samples the comp's output
afterwards, so the keys sit at comp time k * speed * src_fps / fps (ramp.fusion_keyframes). The Edit-page retime is
switched to Optical Flow / Speed Warp so the constant part is smooth too; inside the comp the stretcher blends.
Everything is a normal Fusion tool: the user can open the comp, drag the SourceTime keys, or delete the node.
"""
from __future__ import annotations

from . import cutlist as cl, gyroflow, ramp

TOOL = "TimeStretcher"
NAME = "SpeedRamp"


def find(item):
    """The SpeedRamp tool in the clip's Fusion comps, or None."""
    for i in range(1, (item.GetFusionCompCount() or 0) + 1):
        for t in (item.GetFusionCompByIndex(i).GetToolList(False) or {}).values():
            if t.GetAttrs().get("TOOLS_Name") == NAME:
                return t
    return None


def apply(resolve, item, s: cl.Shot, fps: float) -> str:
    """Ramp on one timeline item. Returns added / updated / removed / none / failed."""
    ts = find(item)
    if not s.ramp:
        if ts is None:
            return "none"
        comp = ts.Comp()
        comp.Lock()
        up, mo = ts.Input.GetConnectedOutput(), None
        for t in (comp.GetToolList(False) or {}).values():
            if t.GetAttrs().get("TOOLS_RegID") == "MediaOut":
                mo = t
        if mo is not None and up is not None:
            mo.Input = up
        ts.Delete()
        comp.Unlock()
        item.SetProperty("RetimeProcess", resolve.RETIME_USE_PROJECT)
        return "removed"
    status = "updated"
    try:
        if ts is None:
            gf = gyroflow.find(item)
            comp = gf.Comp() if gf else (item.GetFusionCompByIndex(1) if item.GetFusionCompCount() else item.AddFusionComp())
            if not comp:
                return "failed"
            tools = {t.GetAttrs()["TOOLS_RegID"]: t for t in (comp.GetToolList(False) or {}).values()}
            mo = tools.get("MediaOut")
            if mo is None:
                return "failed"
            comp.Lock()
            ts = comp.AddTool(TOOL, -32768, -32768)
            if not ts:
                comp.Unlock()
                return "failed"
            ts.SetAttrs({"TOOLS_Name": NAME})
            ts.Input = mo.Input.GetConnectedOutput()        # after Gyroflow (or the reframe pan) when present
            mo.Input = ts.Output
            comp.Unlock()
            status = "added"
        comp = ts.Comp()
        start = comp.GetAttrs()["COMPN_RenderStart"]
        base = (item.GetSpeed() or {}).get("Percentage", 100.0) / 100.0
        mpi = item.GetMediaPoolItem()
        src_fps = float((mpi.GetClipProperty("FPS") if mpi else 0) or fps)
        keys = ramp.fusion_keyframes(s.ramp, s.in_s, s.out_s, fps, base, src_fps, pad_s=0.5)   # transition handles
        comp.Lock()
        ts.InterpolateBetweenFrames = 0        # real source frames (a 60p source slowed to >= 0.5x has one per frame);
        ts.SourceTime = comp.BezierSpline()    # blending ghosted and read as "laggy"
        for k, v in keys.items():
            ts.SourceTime[start + k] = v
        comp.Unlock()
        # the constant retime underneath steps whole comp frames (ramp.base_speed), so it is a plain frame pick:
        # optical flow on top of the stretcher only added warping between already-chosen frames
        item.SetProperty("RetimeProcess", resolve.RETIME_NEAREST)
        return status
    except Exception:  # noqa: BLE001 - Fusion scripting raises plain errors; keep the build going
        return "failed"


def apply_timeline(resolve, c: cl.CutList, items, log: list[str]) -> dict[str, list[int]]:
    """apply() on every shot; {status: [shot numbers]}."""
    out: dict[str, list[int]] = {}
    for n, (s, it) in enumerate(zip(c.shots, items), 1):
        st = apply(resolve, it, s, float(c.fps))
        out.setdefault(st, []).append(n)
        if st == "failed":
            log.append(f"speed ramp failed on shot {n} ({s.note}): set it by hand with the Retime Curve")
    return out
