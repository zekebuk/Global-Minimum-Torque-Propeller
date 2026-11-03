# Transitions — how cuts land, and what was learned building them

`fpvpipe/transitions.py` decides and renders what happens at each cut. This page is the rule set, the mechanics
(ffmpeg + Resolve), and the notebook of what worked and what did not on the MOAB reel (2026-11-03).

## 1. The rule (what `review.py fast --transitions auto` does)

The editor's taste, in order of preference:

1. **Hard cut, carried by motion.** The default. A beat with any weight — onset ≥ 0.55 of the strongest cut in the
   reel, or a loudness jump ≥ +2 dB across the cut (a drop) — is never softened.
2. **Blur-through, 0.2 s** — only on a soft moment in the music when *both* shots are fast (motion ≥ 0.6 of the reel's
   max on each side). The blur hides the join; the motion continues.
3. **Super slight dissolve, 0.12 s** — soft moment, calm outgoing picture (motion < 0.3).
4. Anything in between stays a hard cut.

Signals per cut (`transitions.analyse`): music onset strength (spectral flux at the cut, ±70 ms) and RMS jump
(0.5 s after vs before, dB) from the song at `music_in_s + cut time`; picture motion (mean frame-to-frame change over
the outgoing 0.4 s and the incoming 0.4 s, at 96 px) and the outgoing direction of travel (phase correlation).

What was tried and rejected by the editor, in order:
- 0.5 s dissolves, a 0.2 s **flash to white** on fast cuts, 0.33 s **directional soft wipes** (xfade `smooth*`) in the
  direction of travel: "really don't like the fade".
- Shorter dissolve (0.2 s) and a partial flash (35 % lift): "I prefer hard cuts that use the motion of the shots to
  cleanly transition. Occasional super slight dissolve or blur."
- A **sandstorm** (sand front blowing across the cut): three looks were built and none read as sand at preview size
  (see §4); replaced by the 0.12 s dissolve.

So: no fades, no flashes, no wipes, no particle effects. The cut itself is the transition.

## 2. Data

On the *incoming* shot: `"trans_in": {"type": "blur" | "dissolve" | "cut" | "flash" | "flow" | "sandstorm",
"dur": seconds, ...}`. `"manual": true` keeps `auto` from overwriting a hand-placed one. `Shot.trans_dur` is 0 for a
cut. Everything is **centred on the cut**, so the beat grid never moves.

## 3. Mechanics

**Fast preview (ffmpeg).** Each shot's segment is rendered with *handles*: half the transition before the in-point
and after the out-point, from real source frames, at the speed the shot holds there (a speed ramp keeps running
through the handles — `ramp.speed_at` is held outside the control points). The segments are then joined in one
filter graph: `xfade` where a transition exists, `concat` at hard cuts, every input first passed through
`settb=AVTB` (concat emits 1/1000000, the segments carry 1/11988, and xfade refuses mixed timebases). Total length =
Σ segment lengths − Σ transition durations = the original cut, exactly. Titles overlay and the end fade run after.

**Resolve.** `TimelineItem.AddTransition({"type": name, "category": "simple", "position": "start",
"alignment": "center", "duration": frames})` on the incoming clip, after grades, Gyroflow, ramps and the Fusion-comp
persistence step. Resolve pulls the handles from the source itself. Mapping: blur → *Blur Dissolve*, dissolve →
*Cross Dissolve*, flash → *Additive Dissolve*, flow → *Cross Dissolve* (Push/Wipe directions are not settable),
sandstorm → *Non-Additive Dissolve* (stand-in). `clips_on()` filters transition items out of the track listing so the
cut list ↔ clip zip stays aligned. A ramped clip's TimeStretcher keys are padded 0.5 s past both ends so the handles do
not freeze.

## 4. xfade custom expressions — what you need to know

Anything beyond xfade's built-in names is `transition=custom:expr='...'`, evaluated per pixel, per plane.

- Variables: `A`, `B` (the two inputs' pixel values), `P` (progress, **runs 1 → 0**), `X`, `Y`, `W`, `H` (all in
  the *current plane's* pixels — chroma planes of yuv420p are half size), `PLANE` (0 = Y, 1 = U, 2 = V).
- Commas inside the filtergraph must be escaped `\,`; wrap the expression in single quotes.
- `st(n, v)` / `ld(n)` registers (0–9) make long expressions readable, **but they are shared between xfade's slice
  threads**. With threading on, planes and slices race each other: a `PLANE`-dependent register flips per pixel inside
  one plane, gates come out scrambled, and the result looks like random static. Measured directly (output of
  `ld(6)*100` was {100, 200} within the luma plane). Any expression that uses `st/ld` needs **`:threads=1`** on the
  xfade filter. Register-free expressions (the flash) are fine threaded.
- To match grain between luma and chroma, key noise on chroma coordinates (`X/2`, `Y/2` for luma) — otherwise every
  grain gets a coloured fringe.
- Confining an additive effect to the picture inside a letterbox: compare luma rows, `gte(Y*2/ld6, top) *
  lt(Y*2/ld6, bottom)` with `ld6 = 1 + eq(PLANE, 0)`; use `lt` (not `lte`) on the bottom edge or the last chroma row
  bleeds two luma rows into the bar. The preview passes the band in pixels (`fastpreview.build`).
- Hash noise: `n = frac(abs(sin(ix*12.9898 + iy*78.233 + t*3.7)) * 43758.5453)`; drift it with the wind by offsetting
  `ix` with progress.
- **Looks that did not read as sand at 540 px:** (1) switching each grain from A to B along a noisy front = salt-and-
  pepper static of two pictures; (2) the same with a warm tint band = magenta/grey speckle; (3) a near-opaque warm
  haze with hard 24×12 hash "clouds" on the front = a pixel mosaic. A wavy sinusoidal front with the haze doing the
  work was the next step when the editor called it; the honest conclusion is that a convincing dust storm needs real
  particles / motion blur (a Fusion comp or a pre-rendered overlay with alpha), not a per-pixel expression.

## 5. Adding a transition type

1. `DUR[type]`, and a name in `XFADE` (built-in) or a branch in `xfade_spec` (custom expression; add `:threads=1` if it
   uses registers; take `band` if it adds light to the frame).
2. `RESOLVE[type]`: the closest built-in; say so in the docstring if it is only a stand-in.
3. Decide whether `choose()` may pick it. The editor's rule set is small on purpose; new types should default to
   hand use (`"manual": true`).
4. Test the expression on two solid colours before footage (`color=c=red … color=c=blue … xfade=…`, read the raw
   yuv420p planes back with numpy) — a transition that is wrong in a flat test will not be rescued by real footage,
   and real footage hides which part is wrong.
5. Tile the transition frames (`fps=8 … tile=5x2`) and look before sending; at phone size the first impression is all
   that matters.
