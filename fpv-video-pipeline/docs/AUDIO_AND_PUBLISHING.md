# Sound design, music, and YouTube publishing

## ElevenLabs: one-time setup
1. Get a **paid** plan (the free tier has no commercial license). Read the current terms for sound effects and music on your plan. They decide whether you can monetize.
2. Copy your API key from ElevenLabs ▸ Developers ▸ API keys, then store it as an environment variable. **Never put it in the repo.**
   ```powershell
   setx ELEVENLABS_API_KEY "sk_..."      # then open a new terminal
   ```

## SFX library (once, then reuse forever)
```powershell
python scripts\make_sfx_library.py --dry-run                 # 14 sounds: whooshes, sub-hits, risers, drone, wind
python scripts\make_sfx_library.py --bpm 128 --variants 3    # 3 takes each; risers sized to 1 and 2 bars at 128 BPM
```
This writes `D:\FPV\SFX\<category>\*.mp3` plus `library.json`, a record of the prompt, model and date for each file. Re-running
only fills gaps. Run it again with `--only riser --bpm 140` to get risers for a different tempo. Import the SFX folder into
Resolve's `03_SFX` bin once.

How to use them in a reel (see [STYLE_GUIDE.md](STYLE_GUIDE.md)):

| Moment | Sound | Placement |
|---|---|---|
| Speed ramp / whip | `whoosh_short` / `whoosh_long` | Starts 6–8 frames before the ramp's fastest point |
| Into the drop | `riser_1bar` / `riser_2bar` | Ends exactly on the DROP marker |
| The drop / hardest move | `sub_hit` | On the frame |
| Under everything | `prop_wash_loop`, `wind_rush_loop` | About −18 dB, for realism |
| Dives | `dive_wind` | Follows the free-fall section |

## Music made to fit the edit
```powershell
python scripts\make_music.py reel --bpm 128 --seconds 24 --mood "dark aggressive phonk"
python scripts\make_music.py youtube --bpm 100 --seconds 240 --mood "uplifting cinematic, warm synths"
```
- The reel prompt asks for a 1-bar intro, a build, a **hard drop at about 30% in** (right after your hook), and a clean ending that loops. The YouTube prompt asks for evolving sections with no hard drops.
- After generating, the script beat-maps the track and reports the detected tempo and drop time against what you asked for. If they're off, regenerate with `--seed 2`, `--seed 3` and so on.
- It writes `.mp3`, `.beats.json` (ready for the Resolve build) and `.license.json` (your generation record).
- Pass the file to the session: `run_session.py ... --music D:\FPV\Music\reel_128bpm_....mp3`

**Instagram trending audio:** if you'd rather use in-app music for reach, export the reel **without** music and add the
sound in the app. Your cuts only line up if you edited to that same song.

## YouTube publishing
**Always available, no API needed:**
```powershell
python scripts\youtube_publish.py D:\FPV\Exports\FPV_quarry\YT_final.mp4 --title "Cinematic FPV | Quarry Ridge" `
    --chapters-from-resolve YT_final --thumb-at 42.5 --music-used D:\FPV\Music\yt_100bpm_....mp3
```
- **Chapters:** on the YouTube timeline, drop a **Cream** marker where each location or segment starts and name it. The script reads those markers and enforces YouTube's rules: first chapter at 0:00 (a first marker in the opening 10 s is moved there), at least 3 chapters, each at least 10 s long. It reports every fix it makes. You can use `--chapters file.txt` with `0:00 Name` lines instead.
- It writes `YT_final.youtube.txt` (title, description with chapters, gear, music credits, hashtags, tags) and `YT_final.thumb.jpg` (1280×720 from the chosen frame). Paste these into YouTube Studio when you upload.
- Set your defaults once in `config.toml` under `[youtube_meta]`: intro text, gear list, hashtags, tags, category.

**API upload (optional):**
1. In Google Cloud Console: create a project, enable **YouTube Data API v3**, and create an OAuth client of type **Desktop app**. Download the JSON to `secrets\youtube_client.json`. The `secrets/` folder is git-ignored.
2. `pip install google-api-python-client google-auth-oauthlib`
3. Add `--upload` (and optionally `--publish-at 2026-11-08T17:00:00Z` to schedule it). The first run opens a browser to sign in.

⚠️ **New API projects can only upload private videos.** Since July 2020, YouTube keeps every video uploaded through an
unaudited API project private, whatever visibility you ask for. Until your project passes YouTube's API compliance audit,
treat `--upload` as "stage a private draft", or upload by hand with the `.youtube.txt` package. An upload also uses a large
share of the daily API quota, so expect only a handful of uploads per day on the default quota.
Custom thumbnails through the API also need a phone-verified channel.
