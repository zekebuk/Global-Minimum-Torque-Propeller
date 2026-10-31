# Setup day checklist (Windows PC + SSD + Resolve Studio)

About 2–3 hours, most of it waiting on installs and the first export. Do the steps in order: each one is checked
by the next.

## Bring
- [ ] The SSD, plus at least one card or folder with **a short clip from each camera** (HERO13, Mission 1 Pro, Avata 2), ideally one flight with ND and one without
- [ ] The three log→Rec.709 LUTs: GoPro GP-Log, GoPro GP-Log2 (shipped with the Mission 1 Pro), DJI D-Log M
- [ ] One licensed music track
- [ ] 3–5 reference reels you saved (Instagram Download button or screen recordings), in a folder or on Google Drive

## 1. Pipeline install (15 min)
```powershell
git clone -b fpv-video-pipeline https://github.com/zekebuk/Global-Minimum-Torque-Propeller
cd Global-Minimum-Torque-Propeller\fpv-video-pipeline
powershell -ExecutionPolicy Bypass -File scripts\windows\setup_windows.ps1 -Root D:\FPV
```
This installs Python 3.11 and FFmpeg, creates `D:\FPV\{Library,Stabilized,Exports,Music,References,SFX,ResolveCache}`,
writes those paths into `config.toml`, and runs `setup_check.py`. Copy the LUTs into `luts\` with the names it asks for.
**Write down the Python path it prints at the end.** You'll need it for MCP.

## 2. Resolve Studio (20 min)
- [ ] Preferences ▸ System ▸ General ▸ **External scripting using: Local**, then restart Resolve
- [ ] Preferences ▸ System ▸ Media Storage: add `D:\FPV\ResolveCache` at the **top** of the list (cache and gallery go on the fast SSD)
- [ ] Preferences ▸ System ▸ Decode Options: hardware HEVC decode on (NVIDIA/Intel/AMD)
- [ ] Project Settings ▸ Master Settings ▸ Optimized media and render cache format: **DNxHR HQX** (10-bit). This is per project. `build_resolve_project.py` doesn't set it yet, so check it on the first project.
- [ ] Optional for smooth 5.3K editing: right-click clips ▸ Generate Proxy Media (DNxHR LB, quarter resolution). Grade and render still use the full-quality files.
- [ ] `.venv\Scripts\python scripts\doctor.py` shows all `[ok]`

## 3. MCP (10 min)
Follow [MCP_SETUP.md](MCP_SETUP.md) using the venv Python from step 1. Test it: ask Claude "list the timelines in the current Resolve project".

## 4. First real session, end to end (60–90 min)
```powershell
.venv\Scripts\python scripts\run_session.py E:\DCIM --session setup-test --music D:\FPV\Music\track.mp3
```
- [ ] **ingest:** manifest shows the right camera + profile for every clip (GP-Log / GP-Log2 / D-Log M)
- [ ] **report:** shutter, ISO and ND look plausible. Compare "ND used (est.)" against the filter you actually had on, so we know how far off it is on your footage
- [ ] **highlights:** do the red/fuchsia markers line up with the flips and dives you remember?
- [ ] **pause:** GoPro Player ▸ open `Stabilized\setup-test\_queue` ▸ select all ▸ make the `FPV Reel` HyperSmooth Pro preset ([HYPERSMOOTH_PRO.md](HYPERSMOOTH_PRO.md)), **CineForm**, same res and fps ▸ Batch Export to `Stabilized\setup-test`. Note how the export files are named
- [ ] **check:** every export ✓ (10-bit, same fps, same length)
- [ ] **build:** Resolve now has `REEL_selects` and `YT_selects`, with orange/yellow/teal clips (GP-Log / GP-Log2 / D-Log M), markers, beat markers on the reel
- [ ] Mission 1 Pro clip: does HyperSmooth Pro accept it? Is the LUT right?

## 5. Lock in your look (30 min)
- [ ] Grade one hero clip per camera using the node tree in [PIPELINE.md §4](PIPELINE.md). Grab Still ▸ export `.drx` for each
- [ ] Save render presets once:
  ```powershell
  .venv\Scripts\python scripts\render.py reel --timeline REEL_selects --save-preset "FPV Reel" --no-render
  .venv\Scripts\python scripts\render.py youtube --timeline YT_selects --save-preset "FPV YouTube" --no-render
  ```
- [ ] Optional: `pip install pywinauto`, then `python scripts\windows\inspect_gopro_player.py` with the export dialog open, to see whether full GoPro Player click automation is possible

## 6. References (10 min)
```powershell
.venv\Scripts\python scripts\analyze_references.py D:\FPV\References
```
This gives median shot length, time to first cut, cuts on the beat, pace change, and black level, contrast and saturation
for the reels you like. Later, add `--compare <your reel>` to see how close you are.

## 7. Sound + publishing (optional, 20 min)
- [ ] `setx ELEVENLABS_API_KEY "..."`, then `python scripts\make_sfx_library.py --bpm 128 --variants 3` and import `D:\FPV\SFX` into Resolve
- [ ] `python scripts\make_music.py reel --bpm 128 --seconds 24 --mood "<your vibe>"`, then use it as `--music` in the test session
- [ ] Put a few **Cream** markers on `YT_selects`, then run `python scripts\youtube_publish.py <export> --title "..." --chapters-from-resolve YT_selects --thumb-at 10`
- [ ] Optional: Google Cloud OAuth client → `secrets\youtube_client.json` (see [AUDIO_AND_PUBLISHING.md](AUDIO_AND_PUBLISHING.md))

## Send me afterwards
1. Output of `setup_check.py`, plus any ✗ lines from the session run
2. `camera_report.csv`, with the ND you actually had on for each flight (this lets me calibrate the ND estimate)
3. GoPro Player's export file names and codec options (so `check_stabilized.py` matches them exactly)
4. `references_profile.json` (I'll tune the reel beat sheet and highlight thresholds to it)
5. `gopro_player_ui.txt` if you ran the inspector
