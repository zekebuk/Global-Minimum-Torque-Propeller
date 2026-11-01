# Working from the existing archive (1 TB SSD + 5 TB HDD)

## 1. Catalogue everything, read-only (first run: minutes to an hour, depending on file count)
```powershell
python scripts\scan_archive.py --root SSD=E:\ --root HDD=F:\ --out D:\FPV\Catalog
```
- It never writes to the scanned drives. Output goes only to `--out`. Telemetry is read through the MP4 index (a few hundred
  KB per file, byte-identical to what ffmpeg extracts), so the HDD isn't read end to end.
- Re-runs only scan new or changed files.
- Outputs:

| File | Use it for |
|---|---|
| `catalog.csv` | Every clip: camera, log profile, 10-bit, resolution/fps, length, HyperSmooth on/off, lens, date, GPS, shutter/ISO, highlight counts |
| `best_moments.csv` | Top 100 flips, rolls and dives across **all** footage, with file + timestamp. This is your reel backlog |
| `sessions.csv` | Shoot days × camera × place, ranked by good moments. Pick projects from the top |
| `backup_audit.txt` | Files on only one drive. Copies are matched by size + sampled hash, so renamed copies still count |

## 2. What works on archive footage, and what doesn't

| Works as-is | Limited | Not possible |
|---|---|---|
| Camera/profile detection, highlight finder, best-moments ranking, HyperSmooth Pro / Gyroflow (HyperSmooth-off GoPro clips, HERO8+), grading per camera, beat-cut reels, soundscapes, YouTube/Edits handoff | Clips shot with HyperSmooth on: already cropped and stabilized, so expect less from post stabilization. 8-bit / non-log clips: less grading room. No GPS lock: no sun-model ND estimate | Measuring the ND estimate's accuracy (needs to know which filter was on). Use the new calibration flights for that |

A reel cut purely from `best_moments.csv` is a good end-to-end test, and it doesn't need a flying day.

## 3. Pull one session into the working library
```powershell
python scripts\run_session.py "F:\old_backup\2024-06 quarry" --session 2024-06_quarry --music <track or guide>
```
`ingest.py` copies the session from the archive to the SSD library with checksum verification. The archive copy is never touched.

## 4. Storage plan (first principles)
- **SSD = working set only.** Raw 5.3K60 HEVC is about 0.9 GB/min. CineForm/ProRes intermediates are about 6–8 GB/min, so stabilize
  highlight clips only. One active project (about 30 min of selects + about 10 min stabilized + cache) is about 100–120 GB.
  Keep the SSD under about 75% full, because SSDs slow down and wear faster when nearly full.
- **The HDD is one copy, not a backup**, for anything that lives only there. After you clear originals off the SSD, the HDD holds
  the **only** copy. For footage you can't reshoot, keep a second copy: another drive, or cloud such as Backblaze or Google Drive.
  Run `scan_archive.py` with both roots to see exactly which files are single-copy.
- **Delete freely:** Gyroflow / HyperSmooth Pro intermediates, the Resolve cache, proxies, `_queue` folders. They can all be rebuilt
  from the originals. **Never delete:** files in `raw/`, plus Resolve project exports (`.drp`) and your `.drx` grades.
