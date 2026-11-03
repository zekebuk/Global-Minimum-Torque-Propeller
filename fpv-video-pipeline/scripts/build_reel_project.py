#!/usr/bin/env python3
"""Build the reel as a real Resolve project from a cut list, ready to edit by hand (Resolve Studio must be open).

    python scripts/build_reel_project.py E:/EXPORTS/Review/2026-03_moab_cine/reel_dune2.json --name MOAB_reel

What it does:
  1. Project <name> (created or opened): 1080x1920 @ the cut list's fps, DaVinci YRGB, mismatched sources
     scaled to fit (so the 8:7 GoPro frame letterboxes exactly like the fast preview).
  2. Bins 01_Footage / 02_Music / 03_Graphics; the shots' sources, the song and the title PNG imported.
  3. Timeline <name>_vN: every shot with its in/out and speed, the song at the cut list's offset on A1,
     the title PNG on V2 over the opener with fades, picture + music fade-out at the end.
  4. Grades: each shot's solved chain (fast preview cache) copied into Resolve's LUT folder as one cube per
     node plus the whole chain, and loaded:
       - with a node template .drx (--template, default E:/ASSETS/fpv_5node_template.drx): one node per stage,
         every one editable in Resolve: 1 PRIMARY = the wheels (a CDL), 2 TONE+SAT / 3 LOOK / 5 UNIFY = DCTLs whose
         sliders are the solved values (fpvpipe/dctl.py), 4 CST = the conversion .cube. --lut-primaries /
         --lut-nodes fall back to the baked cubes. A shot with "manual": true in the cut list is never regraded;
       - without one: the whole chain on node 1 (exact, but one black box). Resolve's API cannot add nodes;
         scripts/make_node_template.py writes the template from the 1-node seed still in templates/.
       Both give the same picture (5-node vs whole chain: ~0.4/255 mean difference); the 5-node tree is editable.
  5. Gyroflow ([gyroflow] mode = "ofx"): the OpenFX plugin, [gyroflow.reel] settings, on every shot whose source
     still has gyro data (fpvpipe/gyroflow.py); a shot's "gyroflow" dict in the cut list overrides per shot, e.g.
     {"smoothness": 35}. Already-stabilized sources are skipped. --no-gyroflow turns it off.
Run `review.py fast --look cst5` first so the cubes exist for the current cut list.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import config, cutlist as cl, dctl, fastpreview, gyroflow, resolve as rlib  # noqa: E402

LUT_ROOT = Path(r"C:\ProgramData\Blackmagic Design\DaVinci Resolve\Support\LUT")
DEFAULT_TEMPLATE = Path("E:/ASSETS/fpv_5node_template.drx")      # 5+ empty serial nodes, made once in the UI
NODE_FILES = ["node_n1", "node_n2", "node_n3", "node_n4", "node_unify"]
NODE_NAMES = ["n1_PRIMARY_log", "n2_TONE_SAT_log", "n3_LOOK_log", "n4_CST_GPLog_to_Rec709", "n5_UNIFY_reel"]
# node labels in the template that receive each cube (substring match, case-insensitive); the template's node
# indices need not be in chain order (a tree built by hand often is not), so cubes are placed by label and
# only fall back to index order when a label is missing
NODE_LABELS = [("primary", "exposure", "wb"), ("tone", "contrast", "sat"), ("look", "auto"), ("cst", "lut"), ("unify",)]
# node 1 as Resolve's own primaries: the CDL values, and node 2's cube with the shadow lift that no CDL can hold
CDL_FILES = {"node_n1_cdl.json": "n1_PRIMARY_cdl.json", "node_n2_after_cdl.cube": "n2_TONE_SAT_after_cdl.cube"}
# nodes 2 / 3 / 5 as DCTLs with sliders (fpvpipe/dctl.py), node 4 the exact conversion .cube they key on
DCTL_NAMES = {2: "n2_TONE_SAT.dctl", 3: "n3_LOOK.dctl", 5: "n5_UNIFY.dctl"}
KEY_CUBE = "n4_CST_GPLog_to_Rec709.cube"


def conversion_cube(meta: dict, cfg: dict) -> Path | None:
    """The .cube the solver used as the CST (its note says '4 CST: <file>'), looked up among the configured LUTs."""
    name = next((n.split(":", 1)[1].strip() for n in meta.get("notes", []) if n.startswith("4 CST:")), None)
    cands = [cfg["color"].get("conversion_lut"), *cfg["color"].get("luts", {}).values()]
    return next((p for p in cands if p and name and Path(p).name == name and Path(p).exists()), None)


def cube_key(s: cl.Shot, crop: str | None) -> str:
    """The fast preview's cache key for this shot's grade."""
    return fastpreview.cst5_key(Path(s.src), s, crop)


def stage_luts(c: cl.CutList, cache: Path, name: str, crop: str | None, cfg: dict | None = None) -> list[str | None]:
    """Copy each shot's cubes (and write its node DCTLs) into Resolve's LUT folder; returns the relative folder per
    shot (None if missing)."""
    out = []
    for i, s in enumerate(c.shots, 1):
        key = cube_key(s, crop)
        src_dir, chain, meta = cache / "cubes" / key, cache / "cubes" / f"{key}.cube", cache / "cubes" / f"{key}.json"
        if not (src_dir.exists() and chain.exists()):
            out.append(None)
            continue
        label = s.note.split(":")[0].replace(" ", "_").replace("/", "")[:24]
        rel = f"FPV/{name}/{i:02d}_{label}"
        d = LUT_ROOT / rel
        d.mkdir(parents=True, exist_ok=True)
        for f, nm in zip(NODE_FILES, NODE_NAMES):
            if (src_dir / f"{f}.cube").exists():
                shutil.copy(src_dir / f"{f}.cube", d / f"{nm}.cube")
            else:                                               # no node_unify when the shot is exempt (unify: false):
                (d / f"{nm}.cube").unlink(missing_ok=True)      # drop a stale one so node 5 stays empty
        for f, nm in CDL_FILES.items():                         # native primaries (node 1 CDL + node 2 to match)
            if (src_dir / f).exists():
                shutil.copy(src_dir / f, d / nm)
            else:
                (d / nm).unlink(missing_ok=True)
        for nm in DCTL_NAMES.values():
            (d / nm).unlink(missing_ok=True)
        if meta.exists() and cfg is not None:                   # nodes 2/3/5 as DCTLs, keyed on the exact CST cube
            m = json.loads(meta.read_text())
            conv = conversion_cube(m, cfg)
            if conv:
                shutil.copy(conv, d / KEY_CUBE)                 # exact file, not the 33^3 resample of node_n4
            if (d / KEY_CUBE).exists():
                dctl.write_all(m, d, KEY_CUBE)
        shutil.copy(chain, d / "whole_chain.cube")
        out.append(rel)
    return out


def load_grades(c: cl.CutList, v1, rel: list[str | None], template: Path | None, log: list[str],
                native: bool = True, dctls: bool = True) -> int:
    """Per clip: the 5-node tree (template .drx + one node per solved stage) or the whole chain on node 1.
    native: node 1 as Resolve's primaries (a CDL on the wheels) instead of a LUT.
    dctls:  nodes 2 / 3 / 5 as DCTLs with sliders instead of cubes, when the shot has them.
    Shots marked "manual" in the cut list are skipped: their nodes stay as the user left them in Resolve."""
    expanded = 0
    use_template = bool(template and template.exists())
    for s, it, r in zip(c.shots, v1, rel):
        if r is None:
            continue
        if s.manual:
            log.append(f"left alone (manual): {s.note}")
            expanded += 1
            continue
        g = it.GetNodeGraph()
        if use_template and g.ApplyGradeFromDRX(str(template), 0):
            g = it.GetNodeGraph()
            n = g.GetNumNodes()
            if n >= 5:
                labels = {k: (g.GetNodeLabel(k) or "").lower() for k in range(1, n + 1)}
                used, order = set(), []
                for keys in NODE_LABELS:                       # by label first
                    hit = next((k for k, lb in labels.items() if k not in used and any(x in lb for x in keys)), None)
                    if hit is not None:
                        used.add(hit)
                    order.append(hit)
                spare = [k for k in range(1, n + 1) if k not in used]
                order = [k if k is not None else spare.pop(0) for k in order]   # then index order for the rest
                names = list(NODE_NAMES)
                cdl_file = LUT_ROOT / r / CDL_FILES["node_n1_cdl.json"]
                if native and cdl_file.exists():
                    v = json.loads(cdl_file.read_text())
                    if it.SetCDL({"NodeIndex": str(order[0]), "Slope": " ".join(map(str, v["slope"])),
                                  "Offset": " ".join(map(str, v["offset"])), "Power": " ".join(map(str, v["power"])),
                                  "Saturation": str(v["saturation"])}):
                        names[0] = None                         # node 1: no LUT, the CDL is the primary
                        names[1] = Path(CDL_FILES["node_n2_after_cdl.cube"]).stem
                    else:
                        log.append(f"CDL refused on {s.note}: node 1 stays a LUT")
                files = [None if nm is None else f"{nm}.cube" for nm in names]
                if dctls and names[0] is None:                  # DCTL nodes expect the CDL in node 1 (no LUT there)
                    for node, fn in DCTL_NAMES.items():
                        if (LUT_ROOT / r / fn).exists():
                            files[node - 1] = fn
                    if (LUT_ROOT / r / KEY_CUBE).exists():
                        files[3] = KEY_CUBE                     # node 4: the exact cube the DCTLs key on
                for k, fn in zip(order, files):
                    if fn is None or not (LUT_ROOT / r / fn).exists():
                        continue                                # CDL node, or unify-exempt node 5: nothing loaded
                    if not g.SetLUT(k, f"{r}/{fn}"):
                        log.append(f"node {k} {fn} refused on {s.note}")
                for k in range(1, n + 1):
                    if k not in order:
                        g.SetNodeEnabled(k, False)             # extra template nodes stay, switched off
                expanded += 1
                continue
            g.ResetAllGrades()
            g = it.GetNodeGraph()
        if not g.SetLUT(1, f"{r}/whole_chain.cube"):
            log.append(f"LUT refused on {s.note}")
    return expanded


def fade_out(c: cl.CutList, tl):
    """Picture + music fade at the end. Call after load_grades: applying a .drx grade resets the clip's fades."""
    if c.fade_out_s:
        fo = int(round(c.fade_out_s * c.fps))
        for track in ("video", "audio"):
            tr = tl.GetItemListInTrack(track, 1) or []
            if tr:
                tr[-1].SetFades({"FadeOut": fo})


def stabilize(c: cl.CutList, tl, cfg: dict, off: bool, log: list[str]) -> dict[str, list[str]]:
    """Gyroflow plugin on every shot with gyro data ([gyroflow.reel] + the shot's "gyroflow" overrides); updates in place."""
    if gyroflow.mode(cfg) != "ofx" or off:
        return {}
    if not gyroflow.plugin_installed():
        log.append("[gyroflow] mode is ofx but the plugin is not installed (docs/PIPELINE.md 3b)")
        return {}
    stab = gyroflow.stabilize_timeline(tl, gyroflow.preset(cfg, "reel"), overrides=[s.gyroflow for s in c.shots])
    log += [f"Gyroflow {k} on {', '.join(v)}" for k, v in stab.items() if k in ("failed", "has comp")]
    return stab


def find_or_import(mp, folder, path: str):
    mp.SetCurrentFolder(folder)
    for clip in folder.GetClipList() or []:
        if clip.GetClipProperty("File Path") == path:
            return clip
    items = mp.ImportMedia([path]) or []
    return items[0] if items else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cutlist", type=Path)
    ap.add_argument("--name", default="REEL")
    ap.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE, help=".drx with 5+ empty serial nodes (one cube per node)")
    ap.add_argument("--regrade", metavar="TIMELINE", help="skip the build; (re)load the grades onto this existing timeline")
    ap.add_argument("--aspect", default="8:7", choices=["8:7", "16:9"], help="picture aspect the grades were solved on")
    ap.add_argument("--no-gyroflow", action="store_true", help="don't add the Gyroflow plugin, whatever config.toml says")
    ap.add_argument("--lut-primaries", action="store_true", help="node 1 as a LUT instead of Resolve's own primaries (CDL)")
    ap.add_argument("--lut-nodes", action="store_true", help="nodes 2/3/5 as .cube LUTs instead of DCTLs with sliders")
    ap.add_argument("--config", type=Path)
    args = ap.parse_args()
    cfg = config.load(args.config)
    c = cl.CutList.load(args.cutlist)
    cache = args.cutlist.parent / ".preview_cache"
    crop = None if args.aspect == "8:7" else "crop=iw:iw*9/16"
    rel = stage_luts(c, cache, args.name, crop, cfg)
    missing = [n for n, r in enumerate(rel, 1) if r is None]
    if missing:
        print(f"no solved grade in the cache for shot(s) {missing}: run review.py fast --look cst5 first", file=sys.stderr)

    resolve = rlib.connect()
    pm = resolve.GetProjectManager()
    pm.GotoRootFolder()
    p = pm.CreateProject(args.name) or pm.LoadProject(args.name)
    if not p:
        raise SystemExit(f"could not create or open project {args.name}")
    log = []
    if args.regrade:
        tl = next((p.GetTimelineByIndex(i) for i in range(1, p.GetTimelineCount() + 1)
                   if p.GetTimelineByIndex(i).GetName() == args.regrade), None)
        if not tl:
            raise SystemExit(f"no timeline {args.regrade!r} in {args.name}")
        p.SetCurrentTimeline(tl)
        p.RefreshLUTList()
        v1 = tl.GetItemListInTrack("video", 1) or []
        if len(v1) != len(c.shots):
            raise SystemExit(f"{args.regrade} has {len(v1)} clips, the cut list {len(c.shots)}")
        expanded = load_grades(c, v1, rel, args.template, log, not args.lut_primaries, not args.lut_nodes)
        fade_out(c, tl)
        stab = stabilize(c, tl, cfg, args.no_gyroflow, log)
        pm.SaveProject()
        print(f"regraded {tl.GetName()}: {expanded} shot(s) as 5-node trees, {sum(r is not None for r in rel) - expanded} whole-chain, {len(v1) - sum(r is not None for r in rel)} ungraded; "
              f"Gyroflow updated on {len(stab.get('updated', [])) + len(stab.get('added', []))}")
        for m in log:
            print("  !", m)
        return 0
    for k, v in {"timelineResolutionWidth": "1080", "timelineResolutionHeight": "1920", "timelineFrameRate": str(c.fps),
                 "colorScienceMode": "davinciYRGB", "timelineInputResMismatchBehavior": "scaleToFit"}.items():
        if not p.SetSetting(k, v):
            log.append(f"could not set {k}={v}")
    p.RefreshLUTList()
    mp = p.GetMediaPool()
    root = mp.GetRootFolder()
    bins = {}
    for b in ("01_Footage", "02_Music", "03_Graphics"):
        bins[b] = next((f for f in root.GetSubFolderList() or [] if f.GetName() == b), None) or mp.AddSubFolder(root, b)
    clips = {src: find_or_import(mp, bins["01_Footage"], src) for src in dict.fromkeys(s.src for s in c.shots)}
    music = find_or_import(mp, bins["02_Music"], c.music) if c.music else None
    title_png = cache / "title_0.png"
    title = find_or_import(mp, bins["03_Graphics"], str(title_png)) if c.titles and title_png.exists() else None
    mp.SetCurrentFolder(root)
    bad = [s for s, m in clips.items() if m is None]
    if bad:
        raise SystemExit(f"could not import: {bad}")

    existing = {p.GetTimelineByIndex(i).GetName() for i in range(1, p.GetTimelineCount() + 1)}
    n = 1
    while f"{args.name}_v{n}" in existing:
        n += 1
    tl = mp.CreateEmptyTimeline(f"{args.name}_v{n}")
    p.SetCurrentTimeline(tl)
    infos = []
    for s in c.shots:
        m = clips[s.src]
        sf = float(m.GetClipProperty("FPS") or c.fps)
        infos.append({"mediaPoolItem": m, "startFrame": int(round(s.in_s * sf)), "endFrame": int(round(s.out_s * sf)) - 1,
                      "mediaType": 1, "trackIndex": 1})
    items = mp.AppendToTimeline(infos) or []
    if len(items) != len(infos):
        raise SystemExit(f"Resolve placed {len(items)} of {len(infos)} clips")
    for s, it in zip(c.shots, items):
        if s.speed != 1.0 and not it.SetSpeed({"Percentage": s.speed * 100.0, "RippleTimeline": True}):
            log.append(f"speed refused on {s.note}")
    start = tl.GetStartFrame()
    if music:
        mf = float(music.GetClipProperty("FPS") or c.fps)
        if not mp.AppendToTimeline([{"mediaPoolItem": music, "mediaType": 2, "trackIndex": 1,
                                     "startFrame": int(round(c.music_in_s * mf)),
                                     "endFrame": int(round((c.music_in_s + c.duration()) * mf)) - 1, "recordFrame": start}]):
            log.append("music refused")
    if title:
        t = c.titles[0]
        tl.AddTrack("video")
        if mp.AppendToTimeline([{"mediaPoolItem": title, "mediaType": 1, "trackIndex": 2, "startFrame": 0,
                                 "endFrame": int(round(t["dur"] * c.fps)) - 1, "recordFrame": start + int(round(t["start"] * c.fps))}]):
            v2 = tl.GetItemListInTrack("video", 2) or []
            if v2:
                v2[0].SetFades({"FadeIn": int(round(0.7 * c.fps)), "FadeOut": int(round(0.7 * c.fps))})
        else:
            log.append("title refused")
    v1 = tl.GetItemListInTrack("video", 1) or []
    expanded = load_grades(c, v1, rel, args.template, log, not args.lut_primaries, not args.lut_nodes)
    fade_out(c, tl)
    stab = stabilize(c, tl, cfg, args.no_gyroflow, log)
    pm.SaveProject()
    graded = sum(r is not None for r in rel)
    print(f"built {p.GetName()} / {tl.GetName()}: {len(v1)} shots, {c.duration():.1f}s, "
          f"{expanded} shot(s) as 5-node trees, {graded - expanded} as a whole-chain LUT on node 1, "
          f"{len(v1) - graded} UNGRADED, "
          f"{len(stab.get('added', []))} stabilized by Gyroflow")
    for m in log:
        print("  !", m)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
