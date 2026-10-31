# Stabilizing with GoPro Player + HyperSmooth Pro (Windows)

## What can and can't be automated

GoPro Player has **no command line, scripting API or watch folder**. What it does have is a **batch export queue with
saved presets**. That's what makes this efficient: one setup, then the whole session renders unattended.
So the pipeline automates everything around the app:

```
find_highlights.py ─▶ stage_for_stabilization.py ─▶ [GoPro Player: select all ▸ preset ▸ Batch Export] ─▶ check_stabilized.py ─▶ build_resolve_project.py
  ranks moments        _queue/ holds only clips        ~4 clicks, then it runs on its own                   verify + rename         uses stabilized clips,
                       worth stabilizing                                                                                            originals for the rest
```

To remove those last clicks too, run `scripts\windows\inspect_gopro_player.py` (read-only). It dumps the app's
Windows UI Automation tree. If the controls have stable names, a click-automation script (pywinauto) can drive the
queue. Send me the output file and I'll build it. Whether the app exposes its controls this way hasn't been checked yet.

## Which footage

- **GoPro HERO13 (and HERO8+)**: yes. GoPro says it works in all modes, even clips shot with HyperSmooth on.
- **Mission 1 Pro**: not confirmed. Try one clip. If the stabilization option is greyed out, use Gyroflow.
- **DJI Avata 2**: no. Use Gyroflow (EIS off + Wide), or RockSteady in the drone.
- `stage_for_stabilization.py` only queues GoPro clips. It also queues **every chapter** of a split recording (GX01…, GX02…), so the stabilization runs on the whole flight.

## One-time setup: the preset

Make it on one representative clip, then save it as a preset. Setting names below follow the ReelSteady lineage.
Your version's labels may differ slightly.

| Setting | Reels / freestyle | YouTube / cinematic | Why |
|---|---|---|---|
| Smoothness | low–medium | medium–high | Some pilot motion reads as "real FPV". Cinematic lines want glide. |
| Horizon leveling | off | on, for clips without rolls | Locked horizon fights flips and rolls |
| Output FOV / lens | Wide (or SuperView for drama) | Wide | Wider output = less crop = less zoom-in |
| Resolution | same as source | same as source | Downscaling now limits the 9:16 reframe later |
| Frame rate | same as source | same as source | Keeps 60p for slow motion |
| **Codec** | **CineForm** (10-bit, edits smoothly in Resolve) | CineForm | HEVC also works but plays back slower. **Avoid H.264**: it's 8-bit and throws away log grading latitude |
| Trim | **none** | none | Full length keeps the highlight markers on the right frames |

Save one preset for each column (e.g. `FPV Reel` / `FPV Cine`).

## Each session

```bat
python scripts\find_highlights.py   %FPV%\Library\2026-10-31_quarry\raw
python scripts\stage_for_stabilization.py 2026-10-31_quarry        :: add --all to queue every clip
```
In GoPro Player, open `Stabilized\2026-10-31_quarry\_queue`, select all, apply the preset, and Batch Export to
`Stabilized\2026-10-31_quarry\`. Then:
```bat
python scripts\check_stabilized.py 2026-10-31_quarry
python scripts\build_resolve_project.py 2026-10-31_quarry --music %FPV%\Music\track.mp3
```
`check_stabilized.py` matches each export to its original (the export name must **start with** the original's name,
which is GoPro Player's default). It confirms the export is still 10-bit, has the same frame rate and the same length,
then renames it `<ORIGINAL>_stabilized.<ext>`. It flags anything else and leaves those files alone.
The Resolve builder uses a stabilized clip where one exists and the original everywhere else.

`_queue` holds hard links, not copies, so it uses no extra disk space. Delete it after exporting.

**Notes**
- Run `find_highlights.py` on the **originals**. Exports usually drop the gyro telemetry.
- Need horizon lock on some clips but not others? Run two batches with the two presets, staging each batch separately.
- GoPro says HyperSmooth Pro needs a strong GPU. For a long session, queue it overnight.
