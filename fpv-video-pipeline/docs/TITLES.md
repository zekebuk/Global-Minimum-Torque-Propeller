# Titles: what works and what was learned

The rule from the MOAB reel (2026-11-03): **a clean, flat 2D title wins.** Thin sand-coloured capitals, faded in and out
over the shot, not tracked, not lit, nothing hiding the letters. Every step towards "built into the landscape" was
technically sound and looked worse than this.

The setting to reuse (`titles` in the cut list, rendered by `titles.render_png`):

```json
{"text": "MOAB", "start": 0.3, "dur": 4.0, "font": "dune", "size": 0.19, "tracking": 0.4, "wear": 0.12}
```

## What was tried, in order

| Version | What it was | Editor's verdict |
|---|---|---|
| v21 | Flat 2D: Dune Rise, centred, faded | **Chosen**: "the 2D is cleaner" |
| v22 | Tracked to the canyon floor, keystoned onto the ground | "Right above and slightly baked into the towers" |
| v23 | Standing above the towers, rock hides the letters | "Right idea, but don't cut off so much, and more prominent" |
| v24 | Bigger, brighter, lighter occlusion | "Keep the text centered" |
| v28 | Extruded 3D letters rising from behind the towers, camera pushes through the word on the cut | "Way more chill" |
| v29 | Calm, lying back on the ground plane, no occlusion | "Looks too flattened" |
| v29 fusion | Live Fusion 3D: Text3D, camera, lights, then seated with a shadow and a spire matte | "Go back. The 2D is cleaner" |

## Learnings

- **Show the flat version as the reference.** Each round was judged against the last one rather than against v21, so
  the work drifted further from the look the editor preferred. Compare any new title side by side with the flat one.
- **Effects add things to judge.** Occlusion costs legibility, extrusion and lighting raise questions about colour and
  depth, and a cast shadow on a picture with no real geometry never quite lands. A flat title has none of these problems.
- **A thin face suits flat.** Dune Rise reads as elegant in 2D. Extruded, it reads as tubing.
- **Cheap experiments first.** A single check frame per idea would have settled most of this before building a renderer.
  Same lesson as the sandstorm in [TRANSITIONS.md](TRANSITIONS.md): ask for the simplest version, then escalate only if
  the editor asks.

## Options that remain

These work and stay available by hand. None is the default.

- `"track": true`: follows the terrain with a phase-correlation ground track (`titles.track`, `render_tracked`).
- `"occlude": true`: near rock hides letters (`titles.occlusion_mask`, with `spires_only` to spare broad walls).
- `"fly": true`, optionally `"seated": true`: extruded letters projected in 3D (`titles.render_flythrough`), as a PNG
  sequence. Seated is the calm variant.
- `scripts/fusion_title.py`: a live Fusion comp on the V2 title clip. Leaves the source timeline alone.

## Resolve mechanics worth keeping

- **Comp placement:** `InsertFusionCompositionIntoTimeline` lands on V1 and ripples the cut. A comp on the existing V2
  clip avoids that, skips the footage's grade, and is confined to the 8:7 picture.
- **Fonts:** `FontManager.AddFont(path)` makes an uninstalled font available to Fusion. It was only tested within one
  Resolve session.
- **Keyframing dotted inputs:** `tool.AddModifier(id, "BezierSpline")`, then `tool.SetInput(id, value, frame)`.
- **Saving:** script-made comps need `fusioncomp.persist` to survive a project reload.
- **Modal dialogs:** when API calls start returning `None`, look for one. Loading `SENJA_reel` queues DCTL errors from
  its non-ASCII `11_Husøy` folder, so use another project to test a reload.
