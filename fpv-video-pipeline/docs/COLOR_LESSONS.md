# Colour: what the Moab grading rounds taught us

Seven rounds of auto-grading the Moab reel (2026-11-02), each checked with screenshots, RGB parade, vectorscope and
histogram by a critic agent and then by eye. Every lesson below is a rule in `fpvpipe/grade5.py` (the current
pipeline), not a note to remember.

## The node layout (fixed)

| # | Node | Job | Where it lives |
|---|------|-----|----------------|
| 1 | CST in | GP-Log → DaVinci Wide Gamut / DaVinci Intermediate (exact, published maths) | `cst.cst_in` |
| 2 | CST out | DWG / DI → Rec.709 gamma 2.4, tone mapping none | `cst.cst_out` |
| 3 | Primary | exposure, white balance, contrast with Lift / Gamma / Gain / Offset | `grade5.Primary` |
| 4 | Tone + sat | custom curve (S on luma) and HSL (Sat vs Lum, Hue vs Sat / Hue / Lum) | `grade5.ToneSat` |
| 5 | Look | vibrance, split tone, warm highlights, soft shoulder | `grade5.Look` |

Nodes 3–5 are display-referred (after the output transform), as specified. Values above 1.0 pass through nodes 3
and 4 unclipped, as Resolve does in float, and land in node 5's shoulder.

## Transforms

- **HERO13 on H24 firmware shoots GP-Log, not GP-Log2.** GP-Log2 LUTs on GP-Log footage turn sandstone pink and
  skies cyan (the vectorscope shows it at once). `camera.identify` reads `PTCL` from the file; stabilized exports
  carry no metadata, so the session's profile must be stated (`review.py fast --profile GP-Log`).
- **GoPro's GP-Log "Creative" LUT is a look, not a conversion:** it is orange. Do not use it as node 1.
- **The circulating "Protune native" matrix (IMX117) is wrong for this camera:** sandstone goes pink. Treating
  GP-Log as Rec.709 primaries rendered natural colour in every test; it is the default (`camera_gamut`).
- **GP-Log's black is not at code 0:** the darkest footage sits at ~0.15 display after a plain CST. Node 3's lift
  has to pull it down; that is why lift, gain and gamma are solved together, not one after another.

## Exposure and contrast (node 3)

- **Solve on the delivered framing.** The 8:7 sensor frame carries sky and lit ground that a 16:9 cut never
  shows; sampling the full frame graded one shot a third too dark for what the viewer saw. `sample_frames(crop=)`
  takes the preview's crop.
- **Meter the median without the brightest 10%.** Sky and sun otherwise drag exposure down.
- **Never let two nodes fight over exposure.** v1–v6 darkened in node 1 (into a shoulder) and stretched back in
  node 3 (×2.2): squash-then-stretch reads gray and dead no matter what saturation is added. Now one node owns
  exposure, and nothing it measures changes afterwards.
- **A shadow lift must be a gain on scene light, never a log offset that fades with brightness.** A lift that
  faded out faster than the image got brighter reversed tones over log 0.48–0.55 (sky and mid rock) and turned the
  arch's sky into blotches. Every node is checked monotonic on a grey ramp in the tests.
- **Desert shadows are sky-lit:** true-black crushing (Senja's 0.0) reads as night. p0.5 → 0.03, and a floor keeps
  p10 ≥ 0.06 by lifting blacks when a big shadow area would otherwise block up.
- **Highlights:** the gain also brings p99.5 up to 0.88 (sunlit rock should be bright), limited so p95 goes no
  more than 10% into the shoulder.

## Colour (nodes 3–5)

- **White balance guard:** the neutral finder picks red sandstone as "grey" and cools the whole frame to cyan.
  A cluster with log chroma > 0.05 is a coloured surface, not a light cast; WB fades to zero by 0.09.
- **Saturation level:** Senja's mean chroma (0.082) reads dead on desert. 0.105 mean, with a p95 ceiling of 0.26
  and a highlight ceiling of 0.16 (compression banding), looked alive without going neon.
- **Vibrance before saturation:** boosting muted colours first (and not already-saturated ones) is what made it
  look natural rather than cooked.
- **Deep shadows lose chroma** (×0.8 below luma 0.12) or they go brown-red mud.
- **Sunlit sandstone keeps its warmth:** a highlight path-to-white or chroma cap turns it cream/pink; warm hues
  above luma 0.6 get ×1.15 chroma instead.
- **Skies:** desert evening sky is a clean blue, not steel. Bright cyans rotate 8° toward blue, blues ×1.3.
  A sky-only lift (Hue vs Lum) is the right tool when the sky is darker than the sunlit rock.
- **Shot-to-shot consistency matters more than any single frame:** rock hue drifted 124°–138° across the set in
  v5 and cut badly. Node 4's solver aims every shot at the same vectorscope targets.

## Process

- **Reference videos are visual guides only.** Deriving numeric presets from an exported post (no Resolve info)
  produced a gray, matte copy of the wrong thing. Compare by eye and by scopes; do not fit to them.
- **Fix the node, not the shot.** Stacking per-shot overrides to fight a node bug (v4–v6 on the arch) made every
  later change unpredictable. Per-shot values are allowed, but only after the nodes behave on their own.
- **Cache keys must include a code version** (`cst5.1`), or a renderer change silently shows the old cube.
- **The critic loop works:** three frames per shot + parade/vectorscope/histogram, compared against a reference,
  with concrete knob changes. Its best calls: the crushed arch wall, the sepia slickrock, the brown shadows.
- **Resolve scripting:** `ExportLUT` works from the Color page with the clip selected; cumulative node exports
  (nodes 1..k enabled) measure what each node does. The API cannot add nodes, and `ApplyGradeFromDRX` hung the
  app, so the 5-node tree is built as one LUT per node (`write_node_cubes`) for Resolve to load.

## Closed-loop grading in Resolve (Senja, 2026-11-04)

`scripts/converge_grade.py` grades on what Resolve outputs, not on a model of it: solve the 5-node grade, load it, export
stills from the Color page, read the scopes, adjust, repeat. Then a unifying node with anchors measured from the reel.

- **Measure real output.** Gyroflow, sizing and every node are in the stills. A title on V2 is not: its track is off.
- **Black has a cliff.** With a conversion cube's toe, p1 can jump 0.05 for a black change of 0.004, so a proportional step
  flips between two states forever. Bracket the target and bisect.
- **Exposure can't fix clipped highlights.** Chasing them with exposure crushed a shot to a median of 0.11. `log_gain` < 1
  pulls down the top of the log range and leaves the median to the solver.
- **Deep shadows need their own tool.** `shadow_lift` holds texture in a crack without lifting the shot.
- **Anchors come from the reel.** The stock unify anchors are Moab's sandstone. Median p5 / mid / p95 / chroma and warm hue of
  the reel itself, at strength 0.35, nearly halved the chroma spread without flattening any shot.
- **A hand grade as the CST works.** The Mefjord grade baked to a cube, with exposure and saturation solved under it, carries
  that look across lighting. It tints overcast skies magenta on this footage: trim `wb_g` on those shots.
- **Targets are per shot.** A mirror lake at dusk should not have the median of a sunlit ridge.
