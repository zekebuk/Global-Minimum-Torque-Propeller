# Cameras: HERO13 Black, Mission 1 Pro, DJI Avata 2

## 1. Reading what shot a clip — `scripts/camera_report.py`

```bash
python scripts/camera_report.py ~/FPV/Library/2026-10-31_quarry/raw          # whole session
python scripts/camera_report.py GX010042.MP4 --nd ND16                        # you know which filter was on
python scripts/camera_report.py DJI_..._D.MP4 --lat 47.6 --lon -122.3 --utc-offset -7
python scripts/camera_report.py <raw> --log flight_log.csv   # per-clip filters you logged: estimate error + implied aperture
```

Everything comes from the file itself. File names are only used when nothing else is available.

| | GoPro HERO13 / Mission 1 Pro | DJI Avata 2 |
|---|---|---|
| Where | `moov/udta/GPMF` settings box + GPMF telemetry track | `djmd` protobuf metadata track (+ optional `.SRT` sidecar) |
| Model / firmware | `MINF`, `FMWR` | clip header `product_name`, `product_firmware_version` |
| Color profile | `PTCL` (e.g. `GOPRO`, `FLAT`, log) | **not recorded**; inferred: 10-bit + low-contrast image ⇒ D-Log M |
| Lens / FOV | `VFOV` (W, L, S, H …) | — |
| In-camera stabilization | `EISE` / `EISA` (HyperSmooth on/off) | `eis_status` (off, RockSteady, HorizonSteady, …) |
| Shutter, ISO, WB per frame | `SHUT` (s), `ISOE`, `WBAL` (K) | `exposure_time` (fraction), `iso`, `white_balance_cct` |
| GPS + UTC time | `GPS9` (HERO11+) / `GPS5`+`GPSU` | `.SRT` if recorded, else pass `--lat/--lon` |
| Aperture | fixed lens, from the camera table | fixed lens, from the camera table |

Tested so far against GoPro's own HERO6 and HERO8 sample files (real data) and synthetic DJI messages that follow the
open-source [telemetry-parser](https://github.com/AdrianEddy/telemetry-parser) schema for the Avata 2.
**HERO13, Mission 1 Pro and Avata 2 files haven't been run yet.** If a field comes out blank on your clips,
send me a short clip from each camera (a 3–5 s clip is enough) and I'll fix the mapping.

## 2. Estimating the ND filter (first principles)

**What the camera saw.** The light reaching the sensor, in EV at ISO 100, is set by aperture N, shutter t and ISO S:

    EV_camera = log2(N² / t) − log2(S / 100)

**What was there.** In daylight the scene's brightness is mostly set by the sun's height. From GPS position and UTC
time the script computes the sun elevation h, then the clear-sky illuminance E ≈ 128 000·sin h·e^(−0.2/sin h) lux, which
gives EV_scene = log2(E / 2.5). At h = 60° that's ≈ EV 15, the "sunny 16" rule.

**The ND that was on** ≈ EV_scene − EV_camera, rounded to ND2/4/8/16/32/64.
- It's an **upper bound**. Cloud cover lowers EV_scene by 1–3 stops, and the model assumes a clear sky.
- Snow, water, or a frame full of sky makes auto-exposure stop down, so the estimate reads low. The report warns when highlights clip.
- Expect ±1 stop. Use it to sanity-check yourself, not as a measurement.

**The ND to use next time** doesn't need the sun model. Going from the recorded exposure (t, S) to the target shutter t* =
1/(2·fps) at base ISO, with the same brightness, takes:

    extra stops = log2(t* / t) + log2(S / 100)

Add that to the filter you had on. Example: at 60p, auto-exposure gave 1/1920 at ISO 100 with no filter. That's
log2((1/120)/(1/1920)) = 4 stops, so **ND16**. If you pass `--nd`, the answer is exact. Without it, the report shows both
"if no filter was on" and "if the estimated filter was on".

**Field shortcut without a computer:** fly 10 s in auto with your usual filter. On playback, note the shutter speed.
Each doubling between that shutter and 1/(2·fps) is one more stop of ND.

| 60p, auto-exposure shows, at ISO 100 | Add |
|---|---|
| 1/120 | nothing (correct) |
| 1/240 | +1 stop (ND8 → ND16) |
| 1/500 | +2 stops |
| 1/1000 | +3 stops |
| 1/2000 | +4 stops |
| ISO above 100 at 1/120 | too much ND: remove stops equal to log2(ISO / 100) |

## 3. Capture settings per camera

| | HERO13 Black | Mission 1 Pro | DJI Avata 2 |
|---|---|---|---|
| Log profile | GP-Log, 10-bit | **GP-Log2**, 10-bit (LUTs ship with the camera) | **D-Log M**, 10-bit |
| Lens / FOV | Wide | widest FOV (lens range 112–157°) | **Wide** |
| In-camera stabilization | HyperSmooth **off** | off | **EIS off**. DJI says Gyroflow needs EIS off + Wide FOV. |
| Shutter | 1/120 at 60p (1/240 for crisp freestyle) | same | same, set manually in the goggles |
| ISO | 100 (max 400) | 100 | 100, locked |
| WB | fixed 5500K | fixed | fixed |
| ND kit | HB-series or third-party ND set | GoPro ND8/16/32/64 twist-lock (camera reportedly detects them) | DJI Avata 2 ND set |
| Aperture (for the ND math) | f/2.5 | **unconfirmed**, set `[cameras."Mission 1 Pro"] f_number` in `config.toml` | f/2.8 *(verify on spec sheet)* |
| Gyroflow | supported (GPMF) | expected via GPMF, but a lens profile may not exist yet for a new camera *(verify)* | supported with EIS off + Wide |
| Highlight finder | ✅ raw gyro + accel | ✅ if the file carries GYRO/ACCL | ❌ not yet. DJI stores fused attitude quaternions, not raw gyro. |

## 4. Grading a mixed-camera session

Each log curve is different. A GP-Log LUT on D-Log M footage gives the wrong contrast and color, not just a
"different look". So:

1. Drop each camera's own conversion LUT into `luts/` and map it in `config.toml` → `[color.luts]`:
   `"GP-Log"`, `"GP-Log2"`, `"D-Log M"`. Use GoPro's LUTs for GP-Log and GP-Log2, and DJI's official D-Log M → Rec.709 LUT for
   the Avata 2. Also check whether your Resolve version lists these in Color Space Transform, which would be better than a LUT.
2. `ingest.py` records each clip's profile in `manifest.csv`. `build_resolve_project.py` then applies the matching LUT and
   color-codes the clips: **orange = GP-Log, yellow = GP-Log2, teal = D-Log M**.
3. On the Color page, filter by clip color and put each camera in its own **Group**. Put balance + conversion in that group's
   **Pre-Clip** grade, so every camera lands in the same Rec.709 starting point.
4. Use a shared **Timeline** node for the look, so all three cameras get one grade.
5. Match by eye with the vectorscope. Line up the sky hue and a neutral (concrete, clouds) across cameras before any creative
   grading. The Avata 2's smaller sensor will show more noise in shadows, so give it a little more temporal NR.
