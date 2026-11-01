# Calibration flights: shoot these before real projects

Each flight checks one part of the pipeline against something you know for sure. Fill in
`templates/flight_log.csv` (copy it into the session folder) as you fly. It takes about 10 seconds per pack and turns
"looks about right" into measured numbers.

**Prep the cameras once:**
- [ ] **Sync clocks.** GoPro Quik sets the GoPro's clock on pairing, and DJI cameras take the phone's time. Synced clocks keep a multi-camera session in shooting order.
- [ ] **HERO13: turn GPS on.** It records position and UTC time, which the sun-brightness ND model needs.
- [ ] Settings from [CAMERAS.md §3](CAMERAS.md): log profile, 10-bit, Wide, in-camera stabilization off, fixed white balance, 1/120 at 60p.
- [ ] **Fresh card per camera, formatted in that camera.**

## A. ND estimate check (HERO13, clear sky, 4 packs or one pack in 4 parts)
Same spot, same direction, sun high, no clouds. Auto shutter, ISO max 100. Fly the same 20 s pass with **no ND, ND8,
ND16, ND32** and log each filter.
→ `camera_report.py <raw> --log flight_log.csv` prints the estimate's error in stops. Send me that line, and I'll
replace the clear-sky constant with one fitted to your footage.

## B. Mission 1 Pro aperture (fly right after A, same light)
The same 20 s pass with **no ND and ND16**, logged.
→ The report back-solves `implied_f_number` from the HERO13-calibrated sky brightness. Put the median into
`[cameras."Mission 1 Pro"] f_number` until GoPro's spec sheet confirms it.

## C. Highlight finder check (any camera with gyro data, one pack)
Fly a **known sequence** and log it in `moves_in_order`:
`3 s still on the ground → hover 5 s → flip → cruise 5 s → roll → cruise 5 s → dive → power loop → smooth cruise 15 s`
→ `find_highlights.py` should mark flip and roll red, dive and power loop fuchsia, and the cruise green. If moves are
missing or extras show up, tell me which, and I'll tune the thresholds (rotation, angular rate, free-fall fraction).

## D. Stabilization and motion blur (HERO13, same line twice)
The same aggressive line: once at **1/120 with ND**, once at **auto shutter with no ND** (around 1/1000+).
→ Stabilize both with HyperSmooth Pro and with Gyroflow. That's 4 results showing the real trade-off on your quad:
motion blur vs. jello, and how the two stabilizers compare. It decides which preset and filter set you default to.

## E. Avata 2 checks
One pass with **EIS off + Wide** and one with **RockSteady on**.
→ The ingest manifest should show `in_camera_stab off` vs. `RockSteady`. That confirms the DJI metadata reader on real
files, and the first pass is your Gyroflow test.

## F. Color matching (each camera, 10 s on the ground)
Film a **white or grey card** (printer paper works) in open shade, then the sky and something green, all in log.
→ Grade reference for building the per-camera groups. It also shows which LUT needs a white-balance trim to match the others.

## G. Long recording (any GoPro)
Record **12+ minutes** in one go (5.3K, or until the file splits) to create chaptered files (GX01…, GX02…).
→ Checks that ingest groups the chapters and that the stabilize queue includes every chapter.

## H. Sound references (optional)
- 30 s of the quad hovering close to the camera on the ground, for its real motor sound.
- 30 s of the location with the quad off.

These show how close the ElevenLabs beds and motor loop are to real life.

## Before the first test, have ready
- [ ] LUTs: GoPro GP-Log, GoPro GP-Log2 (Mission 1 Pro), DJI D-Log M, in `luts/`
- [ ] Apps updated: Resolve Studio (note the version), GoPro Player + HyperSmooth Pro, Gyroflow, Edits desktop (logged in)
- [ ] Accounts: ElevenLabs (paid plan), `ELEVENLABS_API_KEY` set, ambient + reel SFX sets generated
- [ ] SSD: 500 GB+ free. A USB-C 10 Gbps NVMe enclosure is fast enough for CineForm intermediates. GPU driver up to date (NVIDIA Studio driver if NVIDIA)
- [ ] 3–5 saved reference reels in `D:\FPV\References`, and 1–2 songs picked (tap them with `guide_track.py tap`)
- [ ] Optional: Google Drive for desktop (phone handoff), Google Cloud OAuth client (YouTube upload)

## What to send me after the test day
1. The `camera_report.py --log` summary line (ND error) and the `implied_f_number` median
2. Whether calibration C's markers matched your move list (which moves were missed, which were extra)
3. Your pick from D (blur vs. crisp, HyperSmooth Pro vs. Gyroflow)
4. The setup-day items from [SETUP_DAY.md](SETUP_DAY.md#send-me-afterwards)
