# Editing from your phone

You give notes in the Claude app. Claude drives Resolve on your desktop, renders a small preview, and sends it back
to the app with a numbered shot sheet. Each round takes a few minutes, mostly render time (about 30–90 s for a reel
preview).

## One-time setup (at the desktop)
1. Everything in [SETUP_DAY.md](SETUP_DAY.md) (Resolve Studio, scripting = Local, pipeline installed).
2. **Remote Control:** Claude desktop app ▸ Settings ▸ Claude Code ▸ Remote Control ▸ add the `fpv-video-pipeline`
   folder. Or run `claude remote-control` in that folder. Leave the desktop on, with Resolve open and the SSD mounted.
3. From the phone, start a session and ask Claude to use that folder. You approve the connection on the phone.

## Overnight: plug in the card, review in the morning
One-time: `powershell -ExecutionPolicy Bypass -File scripts\windows\install_card_watcher.ps1`. A background watcher
starts at login. When a card with a `DCIM` folder is mounted and its file list has settled, it runs
`run_session.py --overnight` (one run at a time, each card only once, the card is only read):

```
card in ─▶ ingest + verify ─▶ camera report ─▶ highlights ─▶ beat grid ─▶ first cut ─▶ graded preview (ffmpeg) ─▶ notify
```
- **No Resolve needed.** The preview applies the same grade auto_grade would write into Resolve, baked into a per-shot LUT.
- **Music:** the newest beat-mapped track in your Music folder, or a 128 BPM guide click (swap the song in Edits).
- **No highlights** (calm flight, DJI): v1 uses evenly spaced shots marked "filler" on the shot sheet.
- **Notify:** the preview, shot sheet and shot list are copied to `[notify].phone_folder` (a Drive/OneDrive-synced folder),
  with an optional ntfy push (`[notify].ntfy_topic`; text only). `Review/LATEST.json` points at it, so in the morning
  you just tell Claude "review last night's preview".
- Run it by hand: `python scripts\run_session.py E:\DCIM --session <name> --overnight`.

## Review rounds: seconds, not minutes
`review.py fast` builds the preview straight from the cut list with ffmpeg. Segments are cached per shot, so after
a note only the changed shots re-render (measured on test clips: 14.7 s first build, 0.2 s unchanged, 3.9 s after
trimming one shot). The Resolve render (`review.py preview`) is still there for checking the real timeline.

## The loop
```
new footage ──▶ run_session.py (ingest, highlights, grade-ready project)  ── you: GoPro Player batch export (one click)
             ──▶ review.py firstcut   (automatic v1: best moves on the beat grid, best one ON the drop)
             ──▶ review.py fast       (graded preview, no Resolve)       [overnight does all of the above]
             ──▶ 📱 preview MP4 + shot sheet in the app
you reply    ──▶ Claude edits the cut list ──▶ review.py fast (changed shots only) ──▶ 📱 …
approved     ──▶ stabilize the used shots ──▶ review.py build (Resolve timeline) ──▶ auto_grade ──▶ finishing
done         ──▶ render.py reel --no-music ──▶ reel_handoff.py ──▶ Edits (song start time on the card)
```

## Notes Claude understands (examples)
| You say | Edit |
|---|---|
| "shot 3 shorter, cut 6 frames off the end" | `trim(3, tail_frames=6)` |
| "start shot 2 later, after the tree" | `slip(2, frames)` or `trim(2, head_frames=…)` |
| "swap 4 and 5" / "move 7 to the start" | `swap` / `move` |
| "drop shot 6" | `delete` |
| "use the gap from yesterday's flight instead of shot 3" | `replace(3, …)`. The slot length stays, so the beat stays |
| "make it all cut on the beat" | `snap_to_beats()` |
| "shot 4 too dark / too blue" | adjust that clip's BAL node (or re-run `auto_grade.py --match`) |
| "more contrast overall" | change the look targets and regrade |

The shot numbers are the ones printed on the shot sheet. Times are reel time.

## What still needs the desk (Resolve API limits)
Speed ramps and retiming, transitions, titles, Fusion effects, and keyframed grades. The API can't create these,
so they stay as finishing touches. Everything else (selection, order, trims, beat sync, grade, sound, export,
handoff) runs from the phone.

## Keeping it fast
- Previews are 540×960 (reel) or 960×540 (YouTube) at about 4 Mbps. A 30 s reel is about 15 MB.
- For long YouTube edits, ask for one section at a time ("preview chapter 2").
- Grades live on timeline items, so every rebuild re-runs `auto_grade.py` (seconds per clip).
