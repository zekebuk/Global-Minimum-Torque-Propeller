# Editing from your phone

You give notes in the Claude app. Claude drives Resolve on your desktop, renders a small preview, and sends it back
to the app with a numbered shot sheet. Each round takes a few minutes, mostly render time (about 30–90 s for a reel
preview).

## One-time setup (at the desktop)
1. Everything in [SETUP_DAY.md](SETUP_DAY.md) (Resolve Studio, scripting = Local, pipeline installed).
2. **Remote Control:** Claude desktop app ▸ Settings ▸ Claude Code ▸ Remote Control ▸ add the `fpv-video-pipeline`
   folder. Or run `claude remote-control` in that folder. Leave the desktop on, with Resolve open and the SSD mounted.
3. From the phone, start a session and ask Claude to use that folder. You approve the connection on the phone.

## The loop
```
new footage ──▶ run_session.py (ingest, highlights, grade-ready project)  ── you: GoPro Player batch export (one click)
             ──▶ review.py firstcut   (automatic v1: best moves on the beat grid, best one ON the drop)
             ──▶ review.py build      (timeline REEL_v1)  ──▶ auto_grade.py  ──▶ review.py preview
             ──▶ 📱 preview MP4 + shot sheet in the app
you reply    ──▶ Claude edits the cut list ──▶ build v2 ──▶ regrade ──▶ preview ──▶ 📱 …
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
