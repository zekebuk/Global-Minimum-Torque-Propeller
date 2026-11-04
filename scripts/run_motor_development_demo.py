"""End-to-end BLDC motor development pipeline on SYNTHETIC data.

synthetic stand logs -> import -> steady-state extraction -> fit motor + prop -> validate ->
matching sweep -> thermal transient -> efficiency map. Outputs go to outputs/motor/demo/.

The synthetic logs are generated from an illustrative library motor so the fit can be checked
against known truth; they are not measurements.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from bldc_motor import identification, matching, plotting, synthetic, teststand, thermal  # noqa: E402
from bldc_motor.library import load_library, save_components  # noqa: E402
from bldc_motor.powertrain import Powertrain  # noqa: E402
from bldc_motor.propeller import PropellerModel  # noqa: E402

OUT = ROOT / "outputs" / "motor" / "demo"


def main() -> int:
    lib = load_library()
    truth = lib.motor("generic_2207_1950kv")
    prop_truth = lib.propeller("generic_5.1x4.6_tri")
    esc = lib.esc("generic_esc")
    supply = synthetic.bench_supply(24.0, 0.01)

    # 1. Rehearse the bench test with labelled synthetic logs.
    loaded = synthetic.generate_stand_log(Powertrain(truth, prop_truth, supply, esc, n_motors=1), seed=7)
    no_load = synthetic.generate_stand_log(
        Powertrain(truth, synthetic.unloaded(prop_truth), supply, esc, n_motors=1), (1.0,), seed=8
    )
    loaded_csv = synthetic.write_stand_csv(loaded, OUT / "synthetic_loaded.csv", description="demo loaded sweep")
    no_load_csv = synthetic.write_stand_csv(no_load, OUT / "synthetic_no_load.csv", description="demo no-load run")

    # 2. Import + steady-state extraction.
    points = teststand.add_derived_metrics(
        teststand.steady_state_points(teststand.load_stand_csv(loaded_csv)), prop_truth.diameter_m
    )
    no_load_points = teststand.steady_state_points(teststand.load_stand_csv(no_load_csv))
    points.to_csv(OUT / "steady_state_points.csv")

    # 3. Identify motor (with "measured" R) and prop.
    fit = identification.fit_motor_from_stand(
        points, "demo_fitted_2207", r_ll_ohm=truth.r_ll_ohm, no_load=no_load_points, esc=esc, template=truth
    )
    prop, prop_diag = PropellerModel.fit_static(
        "demo_fitted_5in",
        prop_truth.diameter_m,
        points["rpm"],
        points["thrust_n"],
        points["torque_nm"],
        mass_g=prop_truth.mass_g,  # weighed on a scale in a real test
        blade_count=prop_truth.blade_count,
    )
    save_components(OUT / "fitted_library.json", motors=[fit.motor], propellers=[prop])
    d = fit.diagnostics
    print("Identification (truth in brackets):")
    print(f"  Kv  {d['kv_rpm_per_v']:8.1f} ± {d['kv_std']:.1f}   [{truth.kv_rpm_per_v:g}]")
    print(f"  I0  {d['i0_a']:8.3f} ± {d['i0_a_std']:.3f} A [{truth.i0_a:g}]")
    print(f"  dI0 {d['i0_a_per_krpm']:8.4f} A/krpm      [{truth.i0_a_per_krpm:g}]")
    print(f"  CT  {prop.ct():8.4f}  CP {prop.cp():.4f}   [{prop_truth.ct():g}, {prop_truth.cp():g}]")
    print(f"  torque RMS residual {d['torque_rms_nm'] * 1000:.2f} mN·m; prop thrust R² {prop_diag['thrust_r2']:.5f}")
    plotting.model_vs_stand(points, Powertrain(fit.motor, prop, supply, esc, n_motors=1), OUT / "model_vs_stand.png")

    # 4. Use the fitted components in a quad-level matching sweep.
    settings = matching.MatchSettings(dry_mass_g=250.0)
    results = matching.sweep(
        [fit.motor, lib.motor("generic_2207_2550kv")],
        [prop],
        [lib.battery("lipo_6s_1300"), lib.battery("lipo_4s_1500")],
        [esc],
        settings,
    )
    matching.write_results_csv(results, OUT / "match_results.csv")
    print("\n" + matching.results_markdown(results, settings))

    # 5. Thermal transient on a freestyle-like profile and the efficiency map.
    quad = Powertrain(fit.motor, prop, lib.battery("lipo_6s_1300"), esc)
    profile = thermal.ThrottleProfile.parse("0:0.25,5:1.0,8:0.3,15:1.0,18:0.25")
    sim = thermal.simulate(quad, profile, 40.0)
    teststand.write_columns_csv(sim, OUT / "thermal_transient.csv")
    plotting.thermal_trace(
        sim,
        OUT / "thermal_transient.png",
        "demo: freestyle bursts",
        (truth.max_winding_temp_c, truth.max_magnet_temp_c),
    )
    plotting.overlay_prop_line(quad, OUT / "efficiency_map.png")
    print(f"\npeak winding temperature over profile: {sim['winding_temp_c'].max():.1f} °C")
    print(f"outputs in {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
