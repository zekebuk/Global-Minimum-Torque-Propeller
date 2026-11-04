"""Library integrity, matching sweep, and CLI smoke tests."""

from __future__ import annotations

import csv

import pytest

from bldc_motor import cli, matching
from bldc_motor.library import bundled_library_json, load_library, save_components

LIB = load_library()


def test_bundled_library_is_labelled_illustrative() -> None:
    assert "ILLUSTRATIVE" in bundled_library_json()["_about"]
    for table in (LIB.motors, LIB.propellers, LIB.batteries, LIB.escs):
        assert table
        assert all(item.source == "illustrative" for item in table.values())


def test_library_save_and_reload(tmp_path) -> None:
    rewound = LIB.motor("generic_2207_1950kv").rewound(2400.0, name="my_rewind")
    path = save_components(tmp_path / "lib.json", motors=[rewound], propellers=[LIB.propeller("generic_3x3_tri")])
    reloaded = load_library(path)
    assert reloaded.motor("my_rewind") == rewound
    assert reloaded.propeller("generic_3x3_tri") == LIB.propeller("generic_3x3_tri")


def test_matching_flags_overpropped_combo() -> None:
    settings = matching.MatchSettings(dry_mass_g=250.0)
    good = matching.evaluate_combination(
        LIB.motor("generic_2808_1300kv"),
        LIB.propeller("generic_7x3.5_bi"),
        LIB.battery("lipo_6s_2200"),
        LIB.esc("generic_esc"),
        settings,
    )
    bad = matching.evaluate_combination(
        LIB.motor("generic_2207_2550kv"),
        LIB.propeller("generic_7x3.5_bi"),
        LIB.battery("lipo_6s_1300"),
        LIB.esc("generic_esc"),
        settings,
    )
    assert not good.flags
    assert "motor_overcurrent" in bad.flags
    assert good.row["auw_g"] == pytest.approx(250.0 + 4 * (46.0 + 6.5) + 340.0)
    assert 0.0 < good.row["hover_throttle"] < 1.0


def test_matching_requires_masses() -> None:
    with pytest.raises(ValueError):
        matching.MatchSettings().all_up_mass_g(
            LIB.motor("generic_2207_1950kv"), LIB.propeller("generic_3x3_tri"), LIB.battery("lipo_4s_650")
        )


def test_cli_list_point_match(tmp_path, capsys) -> None:
    assert cli.main(["list"]) == 0
    out_csv = tmp_path / "points.csv"
    assert (
        cli.main(
            [
                "point",
                "--motor",
                "generic_1404_3800kv",
                "--prop",
                "generic_3x3_tri",
                "--battery",
                "lipo_4s_650",
                "--throttle",
                "0.5",
                "1.0",
                "--out",
                str(out_csv),
            ]
        )
        == 0
    )
    assert len(list(csv.DictReader(out_csv.open()))) == 2
    assert (
        cli.main(
            [
                "match",
                "--auw-g",
                "300",
                "--motors",
                "generic_1404_3800kv",
                "--props",
                "generic_3x3_tri",
                "--batteries",
                "lipo_4s_650",
                "--out",
                str(tmp_path / "m"),
            ]
        )
        == 0
    )
    assert (tmp_path / "m" / "match_report.md").exists()
    assert "generic_1404_3800kv" in capsys.readouterr().out


def test_cli_synth_fit_pipeline(tmp_path) -> None:
    syn = tmp_path / "syn"
    assert (
        cli.main(["synth", "--motor", "generic_2207_1950kv", "--prop", "generic_5.1x4.6_tri", "--out", str(syn)]) == 0
    )
    first_line = (syn / "synthetic_loaded.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "SYNTHETIC" in first_line
    fit_dir = tmp_path / "fit"
    args = [
        "fit",
        str(syn / "synthetic_loaded.csv"),
        "--r-ll-mohm",
        "65",
        "--name",
        "fitted",
        "--diameter-in",
        "5.1",
        "--out",
        str(fit_dir),
    ]
    assert cli.main(args) == 0
    fitted = load_library(fit_dir / "fitted_library.json")
    assert fitted.motor("fitted").kv_rpm_per_v == pytest.approx(1950.0, rel=0.01)
