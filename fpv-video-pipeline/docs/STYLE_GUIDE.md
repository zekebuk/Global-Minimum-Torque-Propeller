# Style guide: punchy reels vs. flowing YouTube

## Why the two are different (first principles)

| | Instagram Reel | YouTube long-form |
|---|---|---|
| Viewer state | Scrolling, thumb on the glass, decides in about 1–2 s | Chose to click, settled in, often on a TV or laptop |
| What keeps them | Novelty every beat, a payoff, a loop | Immersion, flow, a sense of place |
| Unit of edit | The **move** (dive, flip, gap) | The **line** (a continuous path with a reveal) |
| Shot length | 0.5–2 s (½ bar to 1 bar) | 10–40 s |
| Music role | Drives the cuts | Sets the mood under the flight |
| Grade | Bold, contrasty, survives a heavy re-encode | Natural and restful, holds up on a big screen |

Instagram has said publicly that watch time and how often people share a reel are among its main ranking signals.
That's why the hook and the loop matter more than anything in the middle.

## Reel beat sheet (example: 128 BPM, 1 bar ≈ 1.875 s, ~24 s total)

| Bars | Time | Content | Technique |
|---|---|---|---|
| 0–1 | 0–1.9 s | **Hook**: your single best moment, already in motion | Start 2–4 frames before the peak. Punch-in zoom 1.0→1.08 over 4 frames on the first downbeat. |
| 1–4 | 1.9–7.5 s | Build: 3 locations or angles, one per bar | Speed ramp **into** each move: 100% → 300% → 100% using a Retime curve. Optical Flow on, Speed Warp (Studio) on the hero shot. |
| 4 (drop) | ~7.5 s | **Payoff**: flip, gap, or proximity on the red DROP marker | Ramp 100% → 30% through the peak → 100%. Whoosh SFX starts 6–8 frames early, sub-hit on the frame. |
| 4–10 | 7.5–19 s | Ride: ½-bar cuts, alternate fast and slow | Two or three 2x slow-mo beats (60p on a 30p timeline). A camera shake effect on a hard landing or impact. |
| 10–12 | 19–24 s | Outro → loop | The last shot's motion direction matches the first shot's, so the loop is seamless. No end card. |

**Don'ts:** no logo intro, no fades from black, no long title cards, and no more than one or two flash or film-burn transitions.

### Resolve techniques for the reel
- **Retime curve:** select the clip ▸ Ctrl/Cmd+R ▸ clip menu ▸ Retime Curve. Add keyframes and ease them (Bézier).
- **Retime process:** Inspector ▸ Retime And Scaling ▸ Optical Flow, with Motion Estimation set to Speed Warp (Studio) for slow-mo.
- **Motion blur on speed-ups:** Studio's Motion Blur ResolveFX helps 300% sections look smooth instead of strobing.
- **Punch-in:** Inspector ▸ Zoom keyframes, or Dynamic Zoom with Ease In and Out.
- **Snap to beats:** turn on snapping (N). Markers from `beat_map.py` act as snap points. Use M / Shift+↑↓ to jump between markers.
- **Sound design:** three layers. Music, then SFX (whoosh, sub-hit, risers), then about −18 dB of real prop wash audio for realism.
  Mix to about −14 LUFS integrated (Fairlight ▸ Loudness meter). Platforms normalize, but quiet masters sound weak.

## YouTube structure (example 6 min)

| Section | Length | Notes |
|---|---|---|
| Cold open | 0:00–0:25 | 6–10 best moments, cut to the music like a mini reel. This is the promise of the video. |
| Title | 0:25–0:30 | Location name over a calm establishing shot |
| Location A | ~2 min | Wide reveal ▸ close lines ▸ a highlight dive. 3–6 shots. |
| Location B | ~2 min | Different music mood. Hold the best line for 30 s or more without cutting. |
| Outro | ~0:45 | Slow cruise, sunset or pull-back reveal. Music resolves. End screen in the last 20 s. |

**Transitions:** cut while the camera is moving, in the same direction. A "whip" (fast yaw) in clip A into the matching
whip in clip B hides the cut. Use dissolves only for time or location changes, and keep them short (8–12 frames).

## Send me your references

Send 3–5 Instagram reels whose style you want. For each one I'll break down shot lengths, ramp patterns, cut timing
against the beat, and the grade look. From that I can tune `config.toml`, the highlight thresholds, and this beat sheet to match.
