# What professional colorists do, and what changed in the pipeline because of it

Research pass (Nov 2026) over colorist tutorials, courses and forums. Most sources are secondary (blogs, course pages,
forum threads). Where they disagree, that's noted. The YouTube channels worth watching directly are listed at the end.

## Findings → changes

| # | What the pros do | Evidence | Pipeline change |
|---|---|---|---|
| 1 | **Balance first, neutral, on the scopes**: exposure and white balance with **Offset / printer lights on log, before the conversion**. Offset works like per-channel exposure | [Lowepost course](https://lowepost.com/courses/davinci-resolve/professional-color-grading-techniques-in-davinci-resolve-r34/), [Mixing Light: Offset](https://mixinglight.com/color-tutorial/offset-controls-introduction-offsets), [Waqas Qazi on Offset (forum summary)](https://www.vegascreativesoftware.info/us/forum/qazi-on-offset-color-balancing--133957), [Lowepost forum: printer lights only on log before the LUT](https://lowepost.com/forums/topic/5600-printerlights-look-creation-workflow/) | Already the design: BAL node = log offsets ✓ |
| 2 | **Scene-referred**: grade in a wide/log working space, convert to Rec.709 **at the end** of the tree (or work in DaVinci Wide Gamut Intermediate with an output transform last) | [Frame.io: color management with nodes](https://blog.frame.io/2024/01/08/color-management-nodes-davinci-resolve/), [Frame.io: pro grading of RED footage](https://blog.frame.io/2023/09/04/how-to-color-grade-red-camera-footage/), [Blackmagic color-management workshop](https://dvresolve.com/news/color-management-online-workshop-0321/) | New default `order = "scene"`: contrast + saturation in log, LUT as the output transform on node 6 |
| 3 | **YouTube gamma shift**: a Gamma 2.4 grade exports tagged 1‑2‑1, while platforms expect 1‑1‑1, so uploads look washed out. Fix: Rec.709 gamma tag (or a Rec.709-A timeline) | [Creative Video Tips](https://creativevideotips.com/tutorials/best-youtube-settings), [Blackmagic forum](https://forum.blackmagicdesign.com/viewtopic.php?p=1004345), [wolfcrow](https://wolfcrow.com/the-best-settings-in-davinci-resolve-to-upload-your-film-to-youtube-or-vimeo/). The exact gamma math is disputed between sources | `render.py` sets `ColorSpaceTag`/`GammaTag` = Rec.709 ([Resolve render API](https://resolvedevdoc.readthedocs.io/en/latest/API_render.html)) **and verifies 1‑1‑1 on the rendered file** with ffprobe |
| 4 | **Don't push sky saturation**: bright, saturated skies break into blocks and banding after H.264/H.265. Back saturation off and add luminance instead | [Mavic Pilots thread](https://mavicpilots.com/threads/getting-pixelation-in-sky-when-adding-saturation-in-davinci-resolve-17.122998/post-1391594), [Oscar Liang: grading FPV](https://oscarliang.com/color-grade-fpv-videos/), [RawFilm: drone sky grading](https://blog.raw.film/?p=973) | Saturation solver caps **highlight** chroma (`hi_sat_max`), with a test |
| 5 | Node trees are **fixed, labeled, reused** (template / PowerGrade), with secondaries after the primaries | [Building node trees](https://dvresolve.com/tutorial/building-node-trees-davinci-resolve/), Volpatto and Kelly node-tree tutorials (referenced, not verified) | `.drx` template with numbered roles in `[color.nodes]` |

## Camera transforms (affects whether a LUT or a CST does node 6)
- **DJI**: Resolve's Color Space Transform has DJI D-Gamut / D-Log entries ([forum, 2021](https://mavicpilots.com/threads/grading-d-log-m-in-resolve.141460/post-1593331)). Whether **D-Log M** specifically is listed depends on your Resolve version, so check the CST dropdown. DJI hasn't published the D-Log M curve ([DJI forum](https://forum.dji.com/thread-302198-1-1.html)), so DJI's LUT remains the safe choice.
- **GoPro GP-Log / GP-Log2**: no native CST entry found. Community DCTL input transforms exist for both into DaVinci Wide Gamut ([protune-transforms](https://github.com/xtremestuff/protune-transforms)). They're third-party, so A/B them against GoPro's LUT before adopting.
- If you move to a fully color-managed DWG timeline later, `auto_grade.py` needs no changes in principle. The "LUT" stage becomes your output transform. Ask me and I'll add a CST/DCTL mode.

## Disagreements / open questions
- **Gamma tag vs Rec.709-A timeline**: both produce 1‑1‑1. Sources disagree on the underlying gamma (1.96 vs 2.2 vs 2.4). The verification in `render.py` is what counts: upload a 10 s test and compare it with Resolve's viewer.
- **Printer-light placement**: most put it first (on log). One course puts a balance pass first and printer lights late. Our BAL node does both jobs mathematically (a log offset *is* a printer-light move).

## Worth watching directly (YouTube)
- **Cullen Kelly**: color management, scene-referred workflow, look development (also his Blackmagic workshops).
- **Walter Volpatto**: node structure (referenced in the course material above).
- **Waqas Qazi**: offset-based balancing and fast look building.
- **Mononodes (Stefan Ringelschwandtner)**: scene-referred node trees and DCTLs.
- **Oscar Liang**: FPV-specific grading and camera settings.

I haven't watched these videos. The pointers come from the written sources above, so treat specific claims about their content as unverified.
