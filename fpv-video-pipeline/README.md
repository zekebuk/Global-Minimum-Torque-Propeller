# FPV Video Pipeline: GoPro LOG → DaVinci Resolve Studio → Instagram Reels + YouTube

A repeatable pipeline for turning raw GoPro HERO13 (GP-Log), Mission 1 Pro (GP-Log2) and DJI Avata 2 (D-Log M) FPV footage into **punchy 9:16 reels** and **long-form 4K YouTube
flights**. It's built around Gyroflow (gyro stabilization) and DaVinci Resolve Studio (grade, edit, deliver), and it
works **today** through Resolve's scripting API, without waiting on the MCP connection.

| Step | Tool | What it does |
|---|---|---|
| 1. Ingest | `scripts/ingest.py` | Copies the card, checks checksums, writes a manifest; flags 8-bit or no-gyro clips and chaptered recordings |
| 1b. Camera check | `scripts/camera_report.py` | Camera (HERO13 / Mission 1 Pro / Avata 2), log profile, shutter angle, ISO, ND used (est.) and ND for next time: [docs/CAMERAS.md](docs/CAMERAS.md) |
| 2. Shortlist | `scripts/find_highlights.py` | Reads the GoPro's gyro/accelerometer telemetry and ranks flips, rolls, dives, and smooth lines |
| 3. Stabilize | GoPro Player + HyperSmooth Pro **or** Gyroflow | `stage_for_stabilization.py` queues only clips worth stabilizing, `check_stabilized.py` verifies the exports: [docs/HYPERSMOOTH_PRO.md](docs/HYPERSMOOTH_PRO.md), [PIPELINE §3b](docs/PIPELINE.md) |
| 4. Music | `scripts/beat_map.py` | Tempo, beats, downbeats, and drops → Resolve markers to cut on |
| 5. Build | `scripts/build_resolve_project.py` | Project, bins, colored markers, `YT_selects` 4K + `REEL_selects` 9:16 timelines, base grade |
| 6a. Auto grade | `scripts/auto_grade.py` | Scope-driven balance pass per clip (exposure, white balance from detected neutrals, black/mid/white, saturation) written into Resolve's node template + before/after scope PNGs: [docs/COLOR_ALGORITHM.md](docs/COLOR_ALGORITHM.md), research: [docs/GRADING_RESEARCH.md](docs/GRADING_RESEARCH.md) |
| 6b. Light changes | `scripts/analyze_dynamics.py` | Finds clips that need a time-varying grade (AE lag, sun/shade) while ignoring sky/ground content changes; marks keyframe points with exact offsets ([§6](docs/COLOR_ALGORITHM.md)) |
| 6. Grade + edit | Resolve | Node tree and look per platform, reel beat sheet: [PIPELINE §4–5](docs/PIPELINE.md), [STYLE_GUIDE](docs/STYLE_GUIDE.md) |
| 7. Deliver | `scripts/render.py` | Platform-tuned renders |
| 7b. Sound | `scripts/make_sfx_library.py`, `scripts/make_music.py`, `scripts/build_soundscape.py` (music-free: ambience + motor + whooshes driven by the gyro) | ElevenLabs SFX library (whooshes, sub-hits, tempo-sized risers) and beat-mapped music that fits the edit: [docs/AUDIO_AND_PUBLISHING.md](docs/AUDIO_AND_PUBLISHING.md) |
| 8. Publish | `scripts/youtube_publish.py` + checklist | Chapters from Resolve markers, description, tags, thumbnail, optional API upload: [AUDIO_AND_PUBLISHING](docs/AUDIO_AND_PUBLISHING.md), [PIPELINE §7](docs/PIPELINE.md) |

**Setting up?** Follow [docs/SETUP_DAY.md](docs/SETUP_DAY.md). On Windows it's one script, `scripts\windows\setup_windows.ps1 -Root D:\FPV`,
then `scripts/setup_check.py` tells you what's left.

**Existing footage:** `scripts/scan_archive.py --root SSD=E:\ --root HDD=F:\` catalogues everything read-only,
ranks the best moments across the whole archive, and audits which files aren't backed up: [docs/ARCHIVE.md](docs/ARCHIVE.md).

**Before real projects:** fly the calibration set in [docs/TEST_FLIGHTS.md](docs/TEST_FLIGHTS.md). It measures the
ND estimate, the Mission 1 Pro aperture, the highlight thresholds and the stabilizer choice on your own footage.

**Every session after that:** `python scripts/run_session.py <card> --session <name> --music <track>` chains all the
steps below and pauses only for the GoPro Player export.

**Reference reels:** `scripts/analyze_references.py <folder>` measures cut pace, cuts on the beat and grade of the
reels you like, and `--compare` checks your own reel against them.

## Quick start (step by step)

```bash
pip install -r requirements.txt        # numpy; also needs ffmpeg/ffprobe on PATH
# edit config.toml paths, drop your GP-Log LUT in luts/
python scripts/ingest.py /Volumes/GOPRO/DCIM --session 2026-10-31_quarry
python scripts/camera_report.py ~/FPV/Library/2026-10-31_quarry/raw
python scripts/find_highlights.py ~/FPV/Library/2026-10-31_quarry/raw
#   → Gyroflow: stabilize the clips with markers into ~/FPV/Stabilized/2026-10-31_quarry/
python scripts/beat_map.py ~/FPV/Music/track.mp3
python scripts/doctor.py               # Resolve Studio open, External scripting = Local
python scripts/build_resolve_project.py 2026-10-31_quarry --music ~/FPV/Music/track.mp3
#   → grade + cut in Resolve
python scripts/render.py reel --timeline REEL_final
python scripts/render.py youtube --timeline YT_final
```

MCP connection troubleshooting: [docs/MCP_SETUP.md](docs/MCP_SETUP.md). Tests: `python -m pytest -q tests`.
