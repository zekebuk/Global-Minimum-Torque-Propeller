#!/usr/bin/env python3
"""A live Fusion 3D title on a reel timeline (Resolve Studio must be open): real extruded, lit text, editable on the
Fusion page.

    python scripts/fusion_title.py E:/EXPORTS/Review/2026-03_moab_cine/reel_dune2_arch.json --name MOAB_reel \
        --timeline MOAB_reel_v29 --out MOAB_reel_v29_fusion --render

Duplicates the timeline and adds a Fusion comp to the title clip on V2 (Text3D, Camera3D, two lights, Merge3D ->
Renderer3D -> a soft cast shadow -> MediaOut). V2 sits above the grade, so the text is not pushed through the footage's log nodes, and the
clip is the 8:7 picture, so the text can never reach the black bars. InsertFusionCompositionIntoTimeline is not used:
it always lands on V1 and ripples the cut.

The camera carries the motion: it flies in and drops with the terrain under the title (titles.track), with the word
above the lens axis, so the word is seen from below through a wide lens and its depth changes as the shot moves.
Rock that stands up into the word (a spire) hides the letters behind it: a matte sequence made from the graded shot in
the last `review.py fast` run, loaded by a Loader into the render's mask. Everything is a normal keyframe or slider on
the Fusion page (select the V2 clip, open Fusion); bypass TowerMatte to drop the occlusion.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, cutlist as cl, deliver, fusioncomp, resolve as rlib, titles  # noqa: E402

FONT_FILE = "E:/ASSETS/Fonts/Dune_Rise.ttf"
PREVIEW_DIR = Path(".")     # the cut list's folder, where the fast preview and its cache live
LOOK = {
    "font": "Dune Rise", "style": "Regular", "fallback": ("Century Gothic", "Bold"),
    "aov": 70.0,            # a wide lens, like the footage; degrees across the frame
    "distance": 2.0,        # camera to word at the first frame
    "anchor": (0.525, 0.30),  # on the picture: just right of centre puts the spire in the gap between M and O
    "size": 0.54, "spacing": 1.2,
    "extrude": 0.09, "bevel": 0.008,
    "yaw": -4.0, "pitch": -28.0,   # leans back onto the land
    "follow": 0.8,          # how much of the ground's expansion the camera takes
    "colour": (0.91, 0.86, 0.78),  # weathered sandstone, not gold
    "sun": (1.0, 0.94, 0.86), "sun_intensity": 0.9, "sun_rotate": (-40.0, 22.0),
    "ambient": (0.72, 0.62, 0.54), "ambient_intensity": 0.42,
    "shadow": 0.3, "shadow_offset": (-0.008, -0.012), "shadow_blur": 9.0,   # cast away from the low sun
    "occlude": 0.9,
    "fade": 1.0,            # seconds, on the clip's own fade handles
}


def key(tool, input_id: str, values: dict[float, float]):
    tool.AddModifier(input_id, "BezierSpline")
    for f, v in values.items():
        tool.SetInput(input_id, float(v), float(f))


def write_matte(c: cl.CutList, path: np.ndarray, anchor, start_s: float, dur: float, strength: float, folder: Path) -> Path | None:
    """White = keep the text, dark = rock in front of it, one PNG per frame: titles.occlusion_mask (spires only) on the
    opener's graded segment from the last fast preview. None when no preview has been run."""
    info = Path(c_preview_json(c))
    if not info.exists():
        return None
    meta = json.loads(info.read_text())
    if not meta.get("segments"):
        return None
    w, h = 1080, 944
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{meta['seg_head'][0] + start_s:.3f}", "-t", f"{dur + 0.1:.3f}", "-i",
                          meta["segments"][0], "-vf", f"crop=iw:ih*944/1920:0:ih*488/1920,scale={w // 2}:{h // 2},format=rgb24",
                          "-f", "rawvideo", "-"], capture_output=True).stdout
    frames = np.frombuffer(raw, np.uint8).reshape(-1, h // 2, w // 2, 3)
    folder.mkdir(parents=True, exist_ok=True)
    for k in range(len(path)):
        y = (anchor[1] + path[k, 1]) * h
        text = np.zeros((h, w), bool)
        text[max(0, int(y - 0.11 * h)):int(y + 0.09 * h), int(0.08 * w):int(0.92 * w)] = True
        m = titles.occlusion_mask(frames[min(k, len(frames) - 1)], text, (w, h), spires_only=True)
        keep = np.clip(1 - strength * m, 0, 1)
        Image.fromarray((keep * 255).astype(np.uint8), "L").save(folder / f"f_{k:04d}.png", compress_level=1)
    return folder / "f_0000.png"


def c_preview_json(c: cl.CutList) -> str:
    return str(PREVIEW_DIR / f"{c.name}_v{c.version}_fast.json")


def build(item, path: np.ndarray, title: dict, fps: float, look: dict, fu, matte: Path | None = None) -> dict:
    comp = item.GetFusionCompByIndex(1) if item.GetFusionCompCount() else item.AddFusionComp()
    tools = {t.GetAttrs()["TOOLS_RegID"]: t for t in (comp.GetToolList(False) or {}).values()}
    mo = tools["MediaOut"]
    start = comp.GetAttrs()["COMPN_RenderStart"]
    fonts = fu.FontManager.GetFontList()
    if look["font"] not in fonts and Path(FONT_FILE).exists():   # not installed in Windows: register it for this session
        fu.FontManager.AddFont(FONT_FILE)
        fonts = fu.FontManager.GetFontList()
    font, style = (look["font"], look["style"]) if look["font"] in fonts else look["fallback"]
    comp.Lock()
    text = comp.AddTool("Text3D", -32768, -32768)
    cam = comp.AddTool("Camera3D", -32768, -32768)
    sun = comp.AddTool("LightDirectional", -32768, -32768)
    amb = comp.AddTool("LightAmbient", -32768, -32768)
    merge = comp.AddTool("Merge3D", -32768, -32768)
    ren = comp.AddTool("Renderer3D", -32768, -32768)
    for t, name in ((text, "MOAB_Text3D"), (cam, "Lens"), (sun, "Sun"), (amb, "SkyFill"), (merge, "Scene"), (ren, "Render")):
        t.SetAttrs({"TOOLS_Name": name})
    for k, v in {"StyledText": title["text"], "Font": font, "Style": style, "Size": look["size"],
                 "CharacterSpacing": look["spacing"], "ExtrusionDepth": look["extrude"], "BevelDepth": look["bevel"],
                 "BevelWidth": look["bevel"], "Red1": look["colour"][0], "Green1": look["colour"][1],
                 "Blue1": look["colour"][2], "SpecularIntensity": 0.25, "SpecularExponent": 18.0,
                 "Transform3DOp.Rotate.Y": look["yaw"], "Transform3DOp.Rotate.X": look["pitch"]}.items():
        text.SetInput(k, v)
    cam.SetInput("AoV", look["aov"])
    sun.SetInput("Red", look["sun"][0]); sun.SetInput("Green", look["sun"][1]); sun.SetInput("Blue", look["sun"][2])
    sun.SetInput("Intensity", look["sun_intensity"])
    sun.SetInput("Transform3DOp.Rotate.X", look["sun_rotate"][0])
    sun.SetInput("Transform3DOp.Rotate.Y", look["sun_rotate"][1])
    amb.SetInput("Red", look["ambient"][0]); amb.SetInput("Green", look["ambient"][1]); amb.SetInput("Blue", look["ambient"][2])
    amb.SetInput("Intensity", look["ambient_intensity"])
    merge.ConnectInput("SceneInput1", text)
    merge.ConnectInput("SceneInput2", cam)
    merge.ConnectInput("SceneInput3", sun)
    merge.ConnectInput("SceneInput4", amb)
    ren.ConnectInput("SceneInput", merge)
    w, h = 2160, 1888                                       # twice the picture: Resolve's fit is the antialiasing
    ren.SetInput("Width", w)
    ren.SetInput("Height", h)
    ren.SetInput("UseFrameFormatSettings", 0)
    ren.SetInput("RendererType", "RendererOpenGL")
    ren.SetInput("RendererOpenGL.LightingEnabled", 1)
    ren.SetInput("RendererOpenGL.AntiAliasing.Channels.RGBA.HighQuality.Enable", 1)
    # the cast shadow: the render's own alpha, black, soft and offset, under it
    gain = comp.AddTool("ColorGain", -32768, -32768)
    gain.SetAttrs({"TOOLS_Name": "ShadowDark"})
    for ch in ("GainRed", "GainGreen", "GainBlue"):
        gain.SetInput(ch, 0.0)
    gain.SetInput("GainAlpha", look["shadow"])
    gain.ConnectInput("Input", ren)
    blur = comp.AddTool("Blur", -32768, -32768)
    blur.SetAttrs({"TOOLS_Name": "ShadowSoft"})
    blur.SetInput("XBlurSize", look["shadow_blur"])
    blur.ConnectInput("Input", gain)
    move = comp.AddTool("Transform", -32768, -32768)
    move.SetAttrs({"TOOLS_Name": "ShadowCast"})
    move.Center = [0.5 + look["shadow_offset"][0], 0.5 + look["shadow_offset"][1]]
    move.ConnectInput("Input", blur)
    over = comp.AddTool("Merge", -32768, -32768)
    over.SetAttrs({"TOOLS_Name": "TextOverShadow"})
    over.ConnectInput("Background", move)
    over.ConnectInput("Foreground", ren)
    mo.ConnectInput("Input", over)
    matte_ok = False
    if matte is not None:
        ld = comp.AddTool("Loader", -32768, -32768)
        ld.SetAttrs({"TOOLS_Name": "TowerMatteFrames"})
        ld.Clip = str(matte).replace("/", "\\")
        mask = comp.AddTool("BitmapMask", -32768, -32768)
        mask.SetAttrs({"TOOLS_Name": "TowerMatte"})
        mask.ConnectInput("Image", ld)
        mask.SetInput("Channel", "Luminance")
        matte_ok = bool(ren.ConnectInput("EffectMask", mask))
    # the camera: word at the origin, the lens below and in front of it, moving with the terrain
    k = 1 / (2 * np.tan(np.radians(look["aov"]) / 2))
    aspect = h / w
    ax, ay = look["anchor"]
    xs, ys, zs = {}, {}, {}
    n = len(path)
    for f in list(range(0, n, 4)) + [n - 1]:
        dx, dy, sc = path[f]
        z = look["distance"] / sc ** look["follow"]
        sx, sy = (ax - 0.5) + dx, ((0.5 - ay) - dy) * aspect
        xs[start + f], ys[start + f], zs[start + f] = -sx * z / k, -sy * z / k, z
    key(cam, "Transform3DOp.Translate.X", xs)
    key(cam, "Transform3DOp.Translate.Y", ys)
    key(cam, "Transform3DOp.Translate.Z", zs)
    comp.Unlock()
    frames = int(round(look["fade"] * fps))
    item.SetFades({"FadeIn": frames, "FadeOut": frames})
    return {"font": font, "keys": len(zs), "renderer": ren.GetInput("RendererType"), "matte": matte_ok}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist", type=Path)
    ap.add_argument("--name", required=True, help="project")
    ap.add_argument("--timeline", required=True, help="timeline to copy; its V2 clip carries the comp")
    ap.add_argument("--out", required=True, help="name of the copy (replaced if it exists)")
    ap.add_argument("--render", action="store_true", help="render the [reel] master and print the export checklist")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a LOOK value (numbers)")
    args = ap.parse_args()
    cfg = config.load()
    look = dict(LOOK)
    for kv in args.set:
        k, v = kv.split("=", 1)
        look[k] = float(v)
    c = cl.CutList.load(args.cutlist)
    global PREVIEW_DIR
    PREVIEW_DIR = args.cutlist.parent
    t, s = c.titles[0], c.shots[0]
    resolve = rlib.connect()
    pm = resolve.GetProjectManager()
    p = pm.GetCurrentProject()
    if not p or p.GetName() != args.name:
        pm.GotoRootFolder()
        p = pm.LoadProject(args.name)
    tls = {p.GetTimelineByIndex(i).GetName(): p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount() + 1)}
    p.SetCurrentTimeline(tls[args.timeline])
    if args.out in tls:
        p.GetMediaPool().DeleteTimelines([tls[args.out]])
    tl = tls[args.timeline].DuplicateTimeline(args.out)
    p.SetCurrentTimeline(tl)
    v2 = tl.GetItemListInTrack("video", 2) or []
    if not v2:
        raise SystemExit(f"{args.timeline} has no title clip on V2")
    fps = float(c.fps)
    dur = v2[0].GetDuration() / fps
    start_s = (v2[0].GetStart() - tl.GetStartFrame()) / fps
    path = titles.track(Path(s.src), s.in_s + start_s, dur, fps, anchor=look["anchor"])
    matte = None
    if look["occlude"] > 0:
        matte = write_matte(c, path, look["anchor"], start_s, dur, look["occlude"],
                            PREVIEW_DIR / ".preview_cache" / "title_fusion_matte")
        if matte is None:
            print("  ! no fast preview to key the towers from: run review.py fast first; building without occlusion", flush=True)
    info = build(v2[0], path, t, fps, look, resolve.Fusion(), matte)
    print(f"{tl.GetName()}: {info}  terrain over the title: dy {path[-1, 1]:+.3f}, scale {path[-1, 2]:.3f}", flush=True)
    if not fusioncomp.persist(v2[0]):
        print("  ! the comp could not be saved into the project", flush=True)
    v2 = tl.GetItemListInTrack("video", 2)                  # the import replaced the comp; fades stay on the clip
    pm.SaveProject()
    if args.render:
        out = deliver.render(p, tl, cfg["reel"], cfg["paths"]["exports"] / p.GetName(), tl.GetName(),
                             music_track=int(cfg["reel"].get("music_track", 2)))
        ok, text = deliver.verdict(out, cfg["reel"], music=Path(c.music).stem if c.music else "")
        print(text, flush=True)
        return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
