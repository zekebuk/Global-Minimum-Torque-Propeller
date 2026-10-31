# FPV Video Pipeline: GoPro LOG → DaVinci Resolve Studio → Instagram Reels + YouTube

A repeatable pipeline for turning raw GoPro HERO13 (GP-Log), Mission 1 Pro (GP-Log2) and DJI Avata 2 (D-Log M) FPV footage into **punchy 9:16 reels** and **long-form 4K YouTube
flights**. It's built around Gyroflow (gyro stabilization) and DaVinci Resolve Studio (grade, edit, deliver), and it
works **today** through Resolve's scripting API, without waiting on the MCP connection.

| Step | Tool | What it does |
|---|---|---|
| 1. Ingest | `scripts/ingest.py` | Copies the card, checks checksums, writes a manifest; flags 8-bit or no-gyro clips and chaptered recordings |
| 1b. Camera check | `scripts/camera_report.py` | Camera (HERO13 / Mission 1 Pro / Avata 2), log profile, shutter angle, ISO, ND used (est.) and ND for next time: [docs/CAMERAS.md](docs/CAMERAS.md) |
| 2. Shortlist | `scripts/find_highlights.py` | Reads the GoPro's gyro/accelerometer telemetry and ranks flips, rolls, dives, and smooth lines |
| 3. Stabilize | Gyroflow (app or OFX) | Preset settings for freestyle vs. cinematic: [docs/PIPELINE.md §3b](docs/PIPELINE.md) |
| 4. Music | `scripts/beat_map.py` | Tempo, beats, downbeats, and drops → Resolve markers to cut on |
| 5. Build | `scripts/build_resolve_project.py` | Project, bins, colored markers, `YT_selects` 4K + `REEL_selects` 9:16 timelines, base grade |
| 6. Grade + edit | Resolve | Node tree and look per platform, reel beat sheet: [PIPELINE §4–5](docs/PIPELINE.md), [STYLE_GUIDE](docs/STYLE_GUIDE.md) |
| 7. Deliver | `scripts/render.py` | Platform-tuned renders |
| 8. Publish | Checklist | [PIPELINE §7](docs/PIPELINE.md) |

## Quick start (on the editing computer)

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
