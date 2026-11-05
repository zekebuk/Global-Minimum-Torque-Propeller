"""Production cost model: physics pieces and the volume trends."""

from __future__ import annotations

import math

import pytest

from prandtl_prop.manufacturing.cost import CostAssumptions, Part, cooling_time_s, injection, part_cost, projected_area_from_stl


def test_cooling_time_follows_the_slab_solution() -> None:
    a = CostAssumptions()
    t3 = cooling_time_s(0.003, a)
    expected = 0.003**2 / (math.pi**2 * a.diffusivity_m2_s) * math.log((4 / math.pi) * (a.melt_C - a.mold_C) / (a.eject_C - a.mold_C))
    assert t3 == pytest.approx(expected)
    assert cooling_time_s(0.006, a) == pytest.approx(4.0 * t3)  # wall squared


def test_projected_area_of_a_box(tmp_path) -> None:
    cq = pytest.importorskip("cadquery")
    box = cq.Workplane("XY").box(0.04, 0.02, 0.01)
    path = tmp_path / "box.stl"
    cq.exporters.export(box, str(path))
    assert projected_area_from_stl(path) == pytest.approx(0.04 * 0.02, rel=1e-6)


def test_cost_per_part_falls_with_volume_and_routes_are_flagged() -> None:
    a = CostAssumptions()
    part = Part("p", 10e-6, 0.008, 0.003)
    totals = [min(r["total"] for r in part_cost(part, v, a) if r["process"].startswith("injection")) for v in (1e3, 1e4, 1e5, 1e6)]
    assert totals == sorted(totals, reverse=True)
    big = Part("big", 45e-6, 0.012, 0.008)
    bench = next(r for r in part_cost(big, 1e3, a) if r["process"].startswith("benchtop"))
    assert not bench["feasible"]  # 45 cm^3 + runner exceeds a 30 cm^3 shot
    two = injection(part, 1e5, a, tool="hardened", cavities=2)
    four = injection(part, 1e5, a, tool="hardened", cavities=4)
    assert four["press_tonnes"] == pytest.approx(2.0 * two["press_tonnes"])
    assert four["machine"] < two["machine"]
