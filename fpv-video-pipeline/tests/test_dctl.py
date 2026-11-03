"""fpvpipe/dctl.py: the node DCTLs carry every solved value as a slider default. Compiling them needs Resolve
(build_reel_project loads them through SetLUT); this checks the generated source offline."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fpvpipe import dctl  # noqa: E402

META = {"3_primary": {"lift": [0, 0, 0], "gain": [1, 1, 1], "gamma": 1.0, "offset": 0.0, "lift_stops": 1.5, "lift_at": 0.31},
        "4_tone_sat": {"pivot": 0.52, "s": 0.2, "sat": 1.3, "shadow_sat": 1.2, "hi_limit": 0.9, "blue": 1.3,
                       "sky_hue": 8.0, "sky_lift": 0.0, "rock_gain": [1, 1, 1], "green": 1.0, "warm_split": 0.0},
        "5_look": {"vibrance": 0.5}, "unify": {"lift": -0.01, "gain": 1.16, "power": 1.17, "rock_rot": 0.4,
                                               "rock_scale": 1.4, "chroma_scale": 1.25},
        "notes": ["4 CST: GPLOG_Creative_EV0.cube"]}


def sliders(src: str) -> dict[str, float]:
    return {m.group(1): float(m.group(2)) for m in re.finditer(r"DEFINE_UI_PARAMS\((\w+), [^,]+, DCTLUI_SLIDER_FLOAT, ([-\d.e]+)", src)}


def test_tone_sat_defaults_are_the_solved_values():
    src = dctl.tone_sat(dctl.ToneSatParams.from_meta(META), "n4.cube")
    s = sliders(src)
    assert s["liftStops"] == 1.5 and s["liftAt"] == 0.31 and s["sCurve"] == 0.2 and s["pivot"] == 0.52
    assert s["sat"] == 1.3 and s["blueGain"] == 1.3 and s["skyHue"] == 8.0 and s["hiLimit"] == 0.9
    assert "DEFINE_LUT(keyLut, n4.cube)" in src and "__DEVICE__ float3 transform(" in src   # unquoted path
    assert "return make_float3(" in src.splitlines()[-2]      # Resolve's parser wants a literal make_float3 there


def test_look_and_unify():
    assert sliders(dctl.look(0.5, "n4.cube"))["vibrance"] == 0.5
    u = sliders(dctl.unify(META["unify"]))
    assert u == {"lift": -0.01, "gain": 1.16, "power": 1.17, "rockRot": 0.4, "rockScale": 1.4, "chromaScale": 1.25}


def test_write_all_skips_unify_when_exempt(tmp_path):
    out = dctl.write_all(META, tmp_path, "n4.cube")
    assert set(out) == {2, 3, 5} and all((tmp_path / f).exists() for f in out.values())
    out2 = dctl.write_all({**META, "unify": None}, tmp_path, "n4.cube")
    assert 5 not in out2 and not (tmp_path / "n5_UNIFY.dctl").exists()
