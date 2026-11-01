# Scope-driven automatic grading

"Perfect" isn't defined for taste, but it is for the technical pass. A colorist balances every shot to the same
measurable scope targets before any look goes on. `auto_grade.py` does exactly that pass, for every clip, and
writes it into Resolve.

## 1. The node template (build once, export as .drx)

| Node | Name | Contents | Set by |
|---|---|---|---|
| 1 | **BAL** | CDL offset only (log space) | `auto_grade.py`: exposure + white balance |
| 2 | **LUT** | Camera log → Rec.709 LUT | `auto_grade.py` (per camera profile) |
| 3 | **CONT** | CDL slope/offset/power + saturation (display space) | `auto_grade.py`: black / mid / white, saturation |
| 4 | **COLOR** | Hue vs Sat, Color Slice | you |
| 5 | **SKY** | Window/qualifier | you (skip on rolls/flips) |
| 6 | **NR** | Temporal NR 2 frames, Better | template default |
| 7 | **SHARP** | Light sharpening | template default |
| Timeline | LOOK → FILM → LEGAL | Creative look, halation/vignette, soft clip | you, once per project |

To build it: on any clip, add 7 serial nodes (Alt+S), label them as above (right-click ▸ Node Label), set the NR and SHARP
defaults, then grab a still ▸ Gallery ▸ right-click ▸ Export `.drx`. Set `[color].base_grade_drx` to it. `--template` then
applies the template to every clip before writing the numbers.

## 2. Targets (what "correct" means, on the scopes)

| Scope | Measure (BT.709, display space) | YouTube | Reel |
|---|---|---|---|
| Waveform | median luma | 0.42 | 0.44 |
| Waveform | black point (0.5th percentile) | 0.04 (open shadows) | 0.025 (deeper) |
| Waveform | white point (99.5th percentile) | 0.93 | 0.95 |
| Parade / vectorscope | mean chroma of the detected neutral surfaces | 0 (centre) | 0 |
| Vectorscope | mean saturation | ×1.05 | ×1.18, capped |

Both looks are defined in `fpvpipe/colorsci.py` (`LOOKS`). Change the numbers to move your house style.
`--match <clip>` replaces the absolute targets with a hero clip's measured result, which is shot matching.

## 3. The algorithm, from first principles

1. **Sample** 7 frames across the part of the clip that's on the timeline, decoded as 16-bit full-range RGB (what Resolve sees).
2. **Find neutrals in log space.** In a log encoding, a white-balance error is a per-channel gain, so it's a constant shift
   of (R−G, B−G) at every brightness. Every neutral surface (shadowed rock, concrete, cloud) lands on one point across many
   stops. A colored surface (sky, grass) is one point at one brightness band. So the scanner picks the chromaticity cluster
   with the widest brightness spread, plus a vote for the brightest unclipped pixels (the white patch).
   *Why not "least saturated pixels"?* Under a warm cast the blue sky becomes the least saturated thing in frame. That's
   the classic auto-white-balance failure, and it's in the tests: that method selected 0% true neutrals, while this one
   selects 91%.
3. **White balance**: an R and B offset on node 1 that cancels the cluster's shift. In log this is physically exact.
   If no clear neutral cluster exists (forest, sunset), it applies half and says so, rather than turning the scene grey.
4. **Exposure**: one common offset on node 1 that puts the median on target (an exact exposure change in log).
5. **Contrast**: one slope/offset/power on node 3, identical for R, G and B so the balance is untouched. The fit targets mid, then white, then black,
   with **safe limits enforced inside the optimization**. A flat or hazy scene gets the best curve within the limits, with
   the mid still exact, and a note. It isn't crushed to fake full range.
6. **Saturation**: scaled to the look's level and capped by the 95th-percentile chroma, so strong colors don't bloom.
7. **Write**: node 1 and node 3 via Resolve's `SetCDL`, the LUT on node 2 via `SetLUT`. It also saves a before/after PNG per clip
   (picture | luma waveform | RGB parade | vectorscope, with the neutral point in magenta).

## 4. Verified (`tests/test_color.py`, synthetic scenes where the right answer is known)

- LUT engine matches the analytic transform (trilinear on 33³: mean error under 0.002).
- 1 stop under plus a strong warm cast: neutral cast 0.047 → 0.0002, white-balance offsets within 0.005 of the physical truth, median/black/white on target.
- Two cameras with opposite exposure and white-balance errors, matched: median, black and white points within 0.025, saturation within 0.01.
- No neutrals in frame: white balance applied with caution, colors preserved, warning given.
- Flat scene: median exact, honest warning about range.
- End to end on an encoded 10-bit ProRes clip: decode → solve → scopes PNG.

## 5. Running it

```powershell
python scripts\auto_grade.py --clip <file> --look reel                      # offline: numbers + scopes, no Resolve
python scripts\auto_grade.py --from-resolve REEL_final --session <s> --look reel --template
python scripts\auto_grade.py --from-resolve YT_final --session <s> --look youtube --match GX010042
```
Add `--dry-run` to compute and save scopes without touching Resolve. Results go in `Exports\grades\<timeline>\grades.json`.

**Limits** (it's a balance pass, not a colorist):
- It's one static grade per clip. For a shot that flies from shade into sun, add keyframes by hand or split the clip.
- It doesn't touch sky hue, skin or creative looks: nodes 4, 5 and the timeline nodes are yours.
- It's only as accurate as the LUT. An incorrect conversion LUT is the first thing to suspect if every clip needs the same large fix.
- Verified so far on synthetic scenes and an encoded test clip. The first real GoPro, Mission 1 and Avata 2 clips will tell us how
  well the neutral finder works on real scenes. Send me the before/after PNGs.
