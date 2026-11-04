# FPV GoPro LOG → DaVinci Resolve Studio → Reels / YouTube

The full playbook, from setting up the camera to publishing. Each stage says **what** to do, **why** (from first
principles), and which script automates it. Numbers marked *(verify)* come from community sources that
disagree with each other or that I could not check against official docs.

## Assumptions

| Assumption | If it's wrong |
|---|---|
| Cameras: **GoPro HERO13 Black** (GP-Log), **GoPro Mission 1 Pro** (GP-Log2), **DJI Avata 2** (D-Log M), all 10-bit | Per-camera settings, detection, ND estimation and mixed-camera grading: [CAMERAS.md](CAMERAS.md) |
| The GoPro is mounted on the quad (not a "naked" GoPro) and nothing re-encodes the files before ingest | Gyroflow and the highlight finder need the original MP4. |
| DaVinci Resolve **Studio** 19 or later (20/21 fine), on macOS or Windows | The free version blocks external scripting and is weaker at HEVC decoding. |
| You use licensed music (Artlist, Epidemic, YouTube Audio Library, etc.) | Commercial tracks get Content ID claims on YouTube and may be muted on Instagram. |
| Source fps 59.94 (or 50 in PAL regions); reels delivered at 29.97, YouTube at 59.94 | Set `source_fps` and the `fps` values in `config.toml`. |

---

## 0. The pipeline at a glance

```
 SD card ─▶ ingest.py ─▶ find_highlights.py ─▶ Gyroflow (stabilize selects) ─▶ build_resolve_project.py
 (raw MP4)   copy+verify   gyro/accel → ranked     ProRes/DNxHR 10-bit            project, bins, markers,
             manifest      "epic moment" markers   intermediates                  YT + REEL timelines, base grade
                                                                                         │
            beat_map.py (music) ─────────────────────────────────────────────────────────┤
                                                                                         ▼
                                              Color page: balance → GP-Log→709 → look → NR/sharpen
                                                                                         ▼
                                              Edit: REEL (9:16, beat-cut, ramps)  |  YT (16:9 4K, long takes)
                                                                                         ▼
                                              render.py reel / youtube ─▶ publish checklist (section 7)
```

---

## 1. Capture (on the GoPro)

The edit can only be as good as the source. These settings follow from three facts: Gyroflow needs the
**unprocessed** sensor geometry, a log curve needs **consistent exposure** to grade cleanly, and motion blur
from shutter speed is **baked in**, so stabilization can't remove it.

| Setting | Value | Why |
|---|---|---|
| Profile / color | **GP-Log**, 10-bit, bitrate **High** | Log + 10-bit keeps highlight and shadow detail for grading. 8-bit log bands in skies. |
| Resolution / aspect | Highest **8:7 or 4:3** mode that offers 50/60p *(verify mode list per model)* | Full sensor height gives room to crop a 9:16 reel and to stabilize without losing the frame. |
| Frame rate | **60p** (50p in PAL regions) | Allows 2x slow motion on a 30p reel and clean speed ramps. |
| Lens | **Wide** (not Linear, not SuperView) | Linear and SuperView are in-camera warps. Gyroflow's lens profile expects the native fisheye. |
| HyperSmooth | **Off** | Gyroflow does a better, adjustable job from the gyro data. Gyroflow does support HyperSmooth-on files from HERO8 and later, but off gives the most control. |
| Horizon leveling | Off | Same reason. Set horizon lock in Gyroflow per clip instead. |
| Shutter | **1/120 at 60p** (180° rule) with ND, or 1/240 (90°) for punchier, crisper freestyle | Some blur makes motion smooth. Too much blur smears after stabilization, which shows up as "jello-blur" in rolls. Gyroflow's own docs suggest 45–90° shutter for FPV. |
| ND filter | ND8 overcast, ND16 sun, ND32 bright sun/snow *(start points, verify with your scopes)* | ND keeps the shutter fixed while the iris can't change. |
| ISO | Min 100 / Max 100–400 | Small sensor: noise rises fast and HEVC wastes bits on it. |
| White balance | **Fixed** (e.g. 5500K). Never Auto. | Auto WB drifts mid-flight. Fixed WB means one correction per location. |
| Sharpness | Low | Sharpen in Resolve after noise reduction. In-camera sharpening adds halos you can't remove. |
| Audio | On (RAW audio if offered) | Prop and motor sound is great SFX for reels. |

**Flight habits that save edit time:**
- Sit still on the ground for **3 seconds** after arming and before takeoff. Gyroflow uses this still section to estimate gyro bias, and it's a clean in-point.
- Plan reels as **moves**: a dive, a gap, a flip, a proximity pass. Plan YouTube as **lines**: long, smooth, continuous paths with a reveal.
- Fly each line two or three times. You'll want the cleanest take.

## 2. Ingest — `scripts/ingest.py` + `scripts/camera_report.py`

```bash
python scripts/ingest.py /Volumes/GOPRO/DCIM --session 2026-10-31_quarry
```

- Copies the MP4s into `<library>/<session>/raw/`, checks a SHA-1 of every file against the card, and only then tells you it's safe to format.
- Writes `manifest.csv`. It flags **8-bit** clips (you forgot Log) and clips with **no gyro** (Gyroflow can't stabilize those).
- Shows **chaptered recordings**. GoPro splits recordings at about 4 GB into `GX01xxxx`, `GX02xxxx`, and so on. Keep chapters together.
- Records the **camera and color profile** of every clip, and copies DJI `.SRT` sidecars along with the clips.
- Then run `python scripts/camera_report.py <session>/raw` for shutter, ISO, shutter angle, and the **estimated and recommended ND filter** ([CAMERAS.md](CAMERAS.md)).
- LRV/THM files are skipped by default. Turn on `keep_lrv` to keep the LRVs as low-res proxies.

## 3. Shortlist + stabilize

### 3a. Find the best moments — `scripts/find_highlights.py`

```bash
python scripts/find_highlights.py ~/FPV/Library/2026-10-31_quarry/raw
```

FPV "epic" is mostly **physical**, and the GoPro already logs that physics at about 200 Hz:

| Signal from GPMF telemetry | What it means | Marker |
|---|---|---|
| ≥300° net rotation on one axis within the window | flip, roll, power loop | 🔴 red |
| \|accel\| < 0.4 g for 30% or more of the window | dive, hang time, throttle cut (near free fall) | 🟣 fuchsia |
| 95th-percentile angular rate ≥ 400°/s | aggressive, snappy flying | 🟡 yellow |
| ≥8 s of calm flying (< 250°/s) | smooth line, a YouTube candidate | 🟢 green |

It writes `highlights.json`. The Resolve builder turns it into clip markers and a pre-cut reel string-out.
It **can't see composition** (a gap, a sunset), so treat it as a shortlist and still scrub your favorite flights.
Run it on the **original** files, because Gyroflow intermediates no longer contain telemetry.

### 3b. Stabilize with Gyroflow

Gyroflow uses the GoPro's own gyro data plus a lens profile it detects automatically. That's why it beats image-based
stabilizers on FPV footage, where fast rotation and motion blur break feature tracking.

**Default route (`[gyroflow] mode = "ofx"`): the Gyroflow OpenFX plugin inside Resolve, added by script.** No
intermediates and no clicks. `build_resolve_project.py` and `build_reel_project.py` put the plugin on every timeline
clip whose source still has gyro data. For a timeline that already exists, run:

```bash
python scripts/gyroflow_resolve.py --timeline REEL_final --preset reel         # --copy works on a duplicate, --remove undoes it
```

- Install once: download `Gyroflow-OpenFX-windows.zip` from github.com/gyroflow/gyroflow-plugins/releases, copy
  `Gyroflow.ofx.bundle` to `C:\Program Files\Common Files\OFX\Plugins` (macOS `/Library/OFX/Plugins`), and restart
  Resolve. `doctor.py` checks it.
- How it's wired: Resolve's API can't put an OFX on a Color page node, so each clip gets a Fusion comp
  `MediaIn → Gyroflow → MediaOut`. Fusion runs before the Color page, so the five-node grade sees the stabilized log
  image. The plugin is pointed at the source file and finds the gyro data and lens profile itself. Output size is set
  to the source size (the plugin's 3840x2160 default would squash 8:7); Video speed stays 100 because a clip's Fusion
  comp runs in un-retimed source frames (the Edit-page speed is applied to the comp's output afterwards).
- Settings come from `[gyroflow.reel]` / `[gyroflow.youtube]` (the table below, in plugin units). Per-clip changes:
  Fusion page ▸ Gyroflow tool, or **Open Gyroflow** from the tool to fine-tune and save a `.gyroflow` project.
- Skipped on purpose: clips without gyro data (stabilized exports, re-encodes; stabilizing twice warps the image)
  and clips that already have a Fusion comp of their own.
- Check one clip on the Fusion page: the tool's **Status** reads `OK` and **Loaded lens profile** names your camera.
- Don't call `comp.Render()` from a script: Fusion opens a modal "Render completed!" dialog, and Resolve stops
  answering the scripting API until someone clicks OK.
- Cost: 5.3K/4K playback with the plugin is heavy. Use render cache (Fusion output) or the intermediates route below
  for long YouTube edits.

**Alternative: Gyroflow app → 10-bit intermediates, for the selected clips only** (`mode = "intermediates"`).
Intermediates are large. ProRes 422 at 4K60 is roughly 6–7 GB per minute. Stabilize only the clips that have markers.

| Gyroflow setting | Freestyle / Reels | Cinematic / YouTube |
|---|---|---|
| Smoothing algorithm | Default | Default |
| Smoothness | 0.3–0.5 (keep some "pilot feel") | 0.6–1.0 |
| Horizon lock | **Off** (rolls need to roll) | 100% on cruise lines with no rolls, off otherwise |
| Zoom | Dynamic, limit about 1.2–1.3x | Dynamic, limit about 1.15x (a wide FOV feels bigger) |
| Lens correction | 100% (rectilinear), or 50–70% to keep some FPV fisheye | 100% |
| Rolling shutter | Leave the auto value from metadata | same |
| Output codec | **ProRes 422** (macOS) / **DNxHR HQX** (Windows, 10-bit). Not H.264, which drops to 8-bit. | same |
| Output folder | `<stabilized>/<session>/`, keep the default `_stabilized` suffix | same |

Set up one clip, save it as a **preset**, then add the rest to the **Render Queue** and apply the preset to all.
`build_resolve_project.py` automatically uses the stabilized folder when it exists.
Keep the full clip length (don't trim in Gyroflow), so highlight timestamps still line up.

**Alternative: GoPro Player + HyperSmooth Pro** for GoPro clips (batch export with presets, CineForm output):
see [HYPERSMOOTH_PRO.md](HYPERSMOOTH_PRO.md). Gyroflow remains the route for DJI Avata 2 footage.

Resolve's own stabilizer (Color page ▸ Tracker ▸ Stabilizer) is a fallback for clips without gyro data.

## 4. Color grade

> **Current layout (2026-11):** five nodes, in this order: **1 CST in** (GP-Log → DaVinci Wide Gamut / Intermediate),
> **2 CST out** (→ Rec.709 gamma 2.4), **3 Primary** (exposure, white balance, contrast on the Lift / Gamma / Gain /
> Offset wheels), **4 Tone + sat** (custom curves and HSL), **5 Look** (creative). `fpvpipe/grade5.py` solves nodes
> 3–5 from the scopes and `fpvpipe/cst.py` holds the exact transforms; `review.py fast --look cst5` previews it
> and writes one `.cube` per node (plus the wheel values) under the preview cache for Resolve. The reasoning behind
> every rule is in [COLOR_LESSONS.md](COLOR_LESSONS.md). The sections below describe the earlier LUT-based setup.
>
> **In Resolve, every node is editable** (`build_reel_project.py`, default since 2026-11-03): node 1 is a CDL on the
> Lift / Gamma / Gain / Offset wheels; nodes 2, 3 and 5 are per-shot DCTLs (`fpvpipe/dctl.py`) whose sliders *are*
> the solved values (Color page ▸ OpenFX panel ▸ DCTL: shadow lift, S-curve, saturation, blue/green/sky keys,
> vibrance, the unify match); node 4 is the exact conversion `.cube`. The DCTL chain renders within ~1/255 of the
> baked cubes. Tweak the sliders freely; to keep a hand-tuned shot through later `--regrade` runs, give it
> `"manual": true` in the cut list. `--lut-primaries` / `--lut-nodes` fall back to the cubes. Gyroflow's controls for
> a stabilized clip are on its Fusion page (Gyroflow1 node), not in the Edit Inspector.

> **Speed ramps.** A shot's `"ramp": [[source_s, speed], ...]` is a speed curve over source time, eased between the
> points (`fpvpipe/ramp.py`). `CutList.set_ramp(n, points)` moves the out-point so the shot keeps its length (its beats);
> `ramp.fit_shape` keeps a shape such as `ramp.three_point(in, out, hero_s, fast, slow)` spanning the whole shot while it
> does, and `ramp.hero_time(src, in, out)` finds the moment of most motion to slow on. The fast preview renders the
> curve with `setpts` (motion-interpolated where a slowed 60p source runs out of frames). In Resolve the API has no retime
> keyframes, so the clip sits at its average speed (Optical Flow / Speed Warp) and the curve is a `SpeedRamp`
> TimeStretcher in its Fusion comp after Gyroflow, one SourceTime key per frame: drag the keys or delete the node to
> change it by hand (`fpvpipe/retime.py`). Keep ramps near 1× with long eases and no holds; two slow landings in a
> row read as the edit dragging.

> **Transitions.** `review.py fast --transitions auto` reads each cut from the music and the picture
> (`fpvpipe/transitions.py`). The default is a **hard cut** that lets the motion of the two shots carry the join; only
> a soft moment in the music (onset < 0.55 of the strongest cut and no loudness jump) gets something, and barely: a
> **blur**-through (0.2 s, `hblur`) when both shots are fast, a **super slight dissolve** (0.12 s) when the outgoing
> picture is calm. (`flash` = burst to white and `flow` = directional soft wipe exist for hand use; auto never picks
> them.) Each is stored on the incoming shot as `"trans_in": {"type": "blur", "dur": 0.2}`, centred on the cut so the
> beat grid never moves: the preview extends both segments by half the transition (ramps keep running through the
> handles) and overlaps them with ffmpeg `xfade`; Resolve gets the matching built-in transition (`AddTransition`:
> blur → Blur Dissolve, dissolve → Cross Dissolve). `--transitions cut` removes them all; `"manual": true` keeps a
> hand-placed one. The full rule set, the xfade-expression mechanics and what was tried and rejected (fades, flashes,
> wipes, a sandstorm) are in [TRANSITIONS.md](TRANSITIONS.md).

> **Titles in the landscape.** The default and preferred title is flat 2D; see [TITLES.md](TITLES.md) for why. These
> options remain for hand use. A title with `"track": true` (plus `"anchor": [x, y]` on the picture and `"tilt"`)
> rides the ground of the shot under it: `titles.track` measures the terrain's translation and expansion under the
> anchor by sub-pixel phase correlation, `titles.render_tracked` lays the word back onto the ground plane (keystone +
> foreshortening), moves and scales it with that motion (scale softened, word kept inside the frame at its largest)
> and bakes the fades, writing a PNG sequence. The preview overlays the sequence; the builder imports it as an
> image-sequence clip on V2, so Resolve shows the same thing. The reference look (text as a true 3D object — Fusion
> Camera Tracker → Text3D, lit, occluded by the terrain) needs a solved camera and stays a hand step on the Fusion
> page; the 2D track holds up for slow forward reveals like the MOAB opener.
>
> `"fly": true` goes further (`titles.render_flythrough`): each letter is an extruded quad in camera space, projected
> through a wide lens. The letters rise from behind the terrain one after another, the word drifts with the ground and
> swings slowly with a band of sun across it, then the camera pushes through it and the letters leave past the lens
> with motion blur, on the cut. `"occlude": true` hides letters only behind rock that stands well above its
> neighbours (a spire, not a broad wall) and only while the word is farther away than the towers. It is still a
> rendered PNG sequence in Resolve, not a live Fusion comp; `"weight"`, `"pitch"`, `"fly_out"` and the anchor tune it.
>
> `"seated": true` (with `"fly": true`) is the calm version the MOAB reel uses: the word lies back on the ground plane
> (`"pitch": 55`), follows the terrain (`"follow"`), fades in and out in place (`"fade_in"`, `"fade_out"`) and casts a
> soft shadow away from the sun, with no push, swing or occlusion. It is the calmer of the two 3D options.
>
> **Live Fusion 3D title.** `scripts/fusion_title.py` copies a timeline and puts a real 3D scene on its V2 title clip:
> Text3D (extruded, bevelled), Camera3D, a directional sun and an ambient fill through Merge3D and Renderer3D. V2 sits
> above the grade and is the 8:7 picture, so the text skips the log nodes and cannot reach the bars. The camera's
> position is keyframed from `titles.track`, so the word holds its place over the terrain while its perspective changes.
> The word leans back onto the land and casts a soft 2D shadow (`ShadowDark` / `ShadowSoft` / `ShadowCast`). A matte
> sequence keyed from the opener's graded segment in the last `review.py fast` run (spires only) drives `TowerMatte`, so
> rock standing in front of the word hides it; run the fast preview first, or the title builds without occlusion.
> Nudge the anchor so a spire falls between letters rather than through one.
> Tune it on the Fusion page or with `--set size=0.7`. Not used: `InsertFusionCompositionIntoTimeline`, which lands on
> V1 and ripples the cut. The font must be known to Fusion: the script registers Dune Rise from `E:/ASSETS/Fonts` with
> `FontManager.AddFont` for the session; install it in Windows to make it permanent.

### 4a. Project color setup (done by `build_resolve_project.py`)

- Color science **DaVinci YRGB** (not managed), timeline color space Rec.709 Gamma 2.4.
- Why not color-managed? Resolve's Color Space Transform has no GP-Log entry that I could confirm. The reliable
  conversion is a **GP-Log → Rec.709 LUT**: GoPro's own if you have it, or a calibrated third-party one such as
  Leeming LUT Pro. Check the CST "Input Gamma" list in your Resolve version. If GoPro Log is listed there, you can
  use CST to DaVinci Wide Gamut instead.
- Put the LUT at `luts/GoPro_GP-Log_to_Rec709.cube` (or edit `config.toml`).

### 4b. Clip node tree (build it once on a hero clip, then save it as a `.drx`)

```
 [01 BAL] ─▶ [02 GP-Log→709 LUT] ─▶ [03 CONTRAST] ─▶ [04 COLOR] ─▶ [05 SKY] ─▶ [06 NR] ─▶ [07 SHARP]
  in log      conversion only        curve/pivot      sat/hue       gradient    temporal    light
```

| Node | What to do | Why this order |
|---|---|---|
| 01 BAL | Offset wheel for exposure, temp/tint for white balance, in **log** | Exposure and WB act linearly on scene light. Fixing them before the LUT lets the LUT's highlight roll-off work as designed. |
| 02 LUT | GP-Log→709 LUT only, no other adjustments | Keep the conversion isolated so it can be swapped later. |
| 03 CONTRAST | Custom curve or Contrast + Pivot (~0.435); lift shadows slightly if crushed | Shape the image in display space where you see it. |
| 04 COLOR | Saturation, Hue vs Sat (pull down cyan in the sky, push foliage and warm tones), Color Slice on 19+ | GoPro skies go electric cyan. Restrained saturation reads as "expensive". |
| 05 SKY | Large soft gradient window to darken and enrich the sky | **Skip on clips with flips or rolls**: the horizon moves and the window won't follow. Use a luma qualifier instead. |
| 06 NR | Temporal NR: 2 frames, Better, luma ~5–8, chroma ~10–15; spatial very light | Small sensor + HEVC = noise. Clean before sharpening, or you sharpen noise. NR also makes Instagram's encoder spend bits on detail rather than noise. |
| 07 SHARP | Blur/Sharpen radius ~0.45–0.48, or Studio's Detail tools | Instagram's re-encode softens the image. A little pre-sharpening survives it. |

**Timeline (whole-edit) nodes:** `[LOOK] ─▶ [FILM] ─▶ [LEGAL]`
- LOOK: your creative look (a creative LUT at 30–60% via Key Output Gain, or Film Look Creator in Studio 19+).
- FILM: subtle halation and vignette. **No grain on reels**. Grain is random data, so the platform encoder spends its small bitrate on grain and turns it into mush.
- LEGAL: soft clip at 0–100 to keep highlights clean after the platform re-encode.

**Look per platform**
- *Reels:* more contrast, saturation +10–20%, deeper blacks, a stronger look. It has to survive a small phone screen and heavy compression.
- *YouTube:* lighter, natural, slightly lower contrast. It's watched big and for a long time, and heavy grades get tiring.

**Matching shots:** group clips by location and light (Color page ▸ right-click ▸ Add into a New Group). Set
**Pre-Clip** for the location's balance, keep Clip for per-shot tweaks, then check parade and vectorscope side by side.

**Make it reusable:** after grading the hero clip, right-click the viewer ▸ Grab Still, then in the Gallery right-click ▸
Export as `.drx`. Set `base_grade_drx` in `config.toml`, and every future session's clips start from that grade automatically.

## 5. Edit

### 5a. Instagram Reel — punchy and epic (`REEL_selects` timeline, 1080×1920)

1. `python scripts/beat_map.py ~/FPV/Music/track.mp3` gives the tempo, beat, downbeat, and drop markers. A bar at 128 BPM is about 1.9 s.
2. Rebuild the project with `--music` so the beat markers land on the reel timeline and the highlight string-out is already there, best first.
3. Cut to the structure below. Full beat sheet and techniques: [`STYLE_GUIDE.md`](STYLE_GUIDE.md).

```
 0.0 ─ 1.5s   HOOK      best move, mid-action, no intro (most viewers decide here)
 1.5 ─ ~8s    BUILD     1-bar cuts, new location/angle each cut, ramp into each move
 DROP         PAYOFF    hardest flip/gap ON the drop marker, speed ramp + whoosh + sub-hit
 → 15–30s     RIDE      ½-bar cuts alternating fast/slow, a couple of 2x slow-mo beats
 END          LOOP      last frame flows into the first (replays count as watch time)
```

**Vertical framing:** 4:3 and 8:7 footage fills the 9:16 frame through "scale to fill". On the Edit page,
keyframe Transform ▸ Position X to follow the gap or subject. Studio's **Smart Reframe** gives you a first pass.

### 5b. YouTube — long-form nice flights (`YT_selects` timeline, 3840×2160)

- Length 3–10 min. Structure: **cold open** (20–30 s of the best moments) ▸ title card ▸ flights grouped by location ▸ a calm outro.
- Let lines **breathe**: shots of 10–40 s, cut on natural motion (exiting a gap, a horizon reveal), with straight cuts or match cuts.
- Use the green "smooth" markers to find long, calm stretches fast.
- Two or three tracks with mood changes per location. Fade music under any narration.
- Add chapter markers on the timeline as you go (location changes) and copy them into the description.

## 6. Deliver — `scripts/render.py`

```bash
python scripts/render.py reel    --timeline REEL_final
python scripts/render.py youtube --timeline YT_final
python scripts/build_reel_project.py reel.json --name MOAB_reel --regrade MOAB_reel_v10 --final   # cut-list reels
python scripts/render.py reel --check E:/EXPORTS/MOAB_reel/MOAB_reel_v10.mp4                     # checklist only
```

| | Reel | YouTube |
|---|---|---|
| Resolution | 1080×1920 | 3840×2160, even from a 2.7K/5.3K source (4K uploads get YouTube's higher-bitrate VP9/AV1 encodes) |
| Frame rate | 29.97 (or 59.94 for very fast content) | 59.94 (or match source) |
| Codec | H.264 High, 2-pass, ~20 Mbps (Instagram re-encodes to 3–6 Mbps; more than ~25 buys nothing) | H.265 ~80 Mbps (YouTube's own SDR 4K60 guideline is ~53–68 Mbps for H.264) |
| Audio | AAC 320 kbps, 48 kHz, −14 LUFS / −1 dBTP (Instagram normalises *down* only) | AAC 320 kbps, 48 kHz |
| Color | Rec.709, 1-1-1 tags | Rec.709, 1-1-1 tags |

A reel render ends with the **READY FOR EXPORT** block (`fpvpipe/deliver.py`): picture / colour tags / audio / loudness /
length / size / grades, each read back from the finished file with ffprobe. `NOT READY` names what to fix. `--no-music`
masters (song added in Edits) skip the loudness gate.

If an export looks washed out in QuickTime on a Mac, that's the macOS gamma-tag difference, not your grade. Judge it in the
YouTube or Instagram app.

## 7. Publish checklist

**Instagram Reel**
- [ ] 9:16, 1080×1920, H.264, 30p; length 15–30 s works best for this style (the maximum allowed length varies by account, so check in the app *(verify)*)
- [ ] Text and logos inside the centered safe area, about 1080×1420. Keep roughly the top 250 px and bottom 420 px clear of UI *(verify, sources disagree)*
- [ ] In Instagram settings, turn on **Upload at highest quality** under Data usage and media quality
- [ ] Transfer the file to your phone at full quality (AirDrop, a cable, or a cloud drive at original quality, not a messenger app)
- [ ] Cover frame: the most dramatic still, with no text over the subject
- [ ] Caption: hook line, location, gear (quad, GoPro, settings), 3–5 specific hashtags (#fpv #cinematicfpv #gopro + location)
- [ ] Music: either baked into the file (licensed), or posted through **Edits** with in-app music: `render.py --no-music` + `reel_handoff.py` give you the file on your phone and the exact song start time ([AUDIO_AND_PUBLISHING.md](AUDIO_AND_PUBLISHING.md)). Instagram's audio library lets the reel surface on that sound's page

**YouTube**
- [ ] 4K upload. HD versions appear first and 4K follows after processing, so don't judge quality in the first hour
- [ ] Title: what + where + style ("Cinematic FPV over <place> | GoPro HERO13 GP-Log")
- [ ] Chapters in the description, starting at `00:00`
- [ ] Thumbnail: a graded still, high contrast, 2–3 words at most
- [ ] Music credits and licenses in the description; check the **Copyright** checks in YouTube Studio before publishing
- [ ] Cut a vertical Short from the reel with a link to the full video

## 8. Archive

Keep `raw/` (originals) and the Resolve project export (`File ▸ Export Project` → `.drp`). Gyroflow intermediates and render
cache can be deleted, because they're reproducible from the originals plus the Gyroflow preset.
