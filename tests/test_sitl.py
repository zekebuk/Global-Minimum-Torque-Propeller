"""SITL bridge: tables vs the BEMT, rotor wrench signs, replay round trip, motor dynamics, C++ header, ULog ingest."""

from __future__ import annotations

import math
import shutil
import struct
import subprocess
from dataclasses import replace

import numpy as np
import pytest

from prandtl_prop import multirotor
from prandtl_prop.aero.bemt import BladeStations, OperatingPoint
from prandtl_prop.aero.polars import AnalyticPolar
from prandtl_prop.multirotor import MultirotorSpec
from prandtl_prop.sitl import missions, px4, tables
from prandtl_prop.sitl import motor as mo
from prandtl_prop.sitl import vehicle as vh
from prandtl_prop.sitl.replay import Trace, from_rpm, replay
from prandtl_prop.sitl.spec import SitlSpec

POLAR = AnalyticPolar(4.22, -5.19, 0.0178, 0.029, 1.2, -1.0)
RADIUS = 0.127
SPEC = SitlSpec(lambda_grid=(-0.2, -0.1, 0.0, 0.1, 0.2, 0.3), mu_grid=(0.0, 0.1, 0.2, 0.3, 0.4))


def _blade() -> BladeStations:
    edges = np.linspace(0.2, 1.0, 13) * RADIUS
    r = 0.5 * (edges[1:] + edges[:-1])
    pitch = 0.11
    return BladeStations(
        r, np.diff(edges), np.full(len(r), 0.016), np.degrees(np.arctan(pitch / (2 * math.pi * r))), ("NACA4410",) * len(r)
    )


@pytest.fixture(scope="module")
def rig():
    op = OperatingPoint(RADIUS, 3, 7500.0)
    table = tables.build_table("test", _blade(), op, POLAR, SPEC, azimuth_points=24)
    spec = MultirotorSpec(mass_kg=1.4)
    vehicle = vh.from_specs(spec, SPEC, 1.225)
    motor, battery = mo.from_vehicle(spec, 750.0, 3.0e-5, SPEC)
    return {"op": op, "table": table, "vehicle": vehicle, "motor": motor, "battery": battery, "spec": spec}


def test_table_is_exact_at_nodes_at_any_rpm(rig):
    """Reynolds-independent polar: coefficients at (lambda, mu) nodes reproduce the BEMT at another rpm."""

    table, op = rig["table"], rig["op"]
    for rpm, lam, mu in ((5000.0, 0.1, 0.2), (11000.0, -0.1, 0.3), (7500.0, 0.0, 0.0)):
        tip = rpm * math.pi / 30 * RADIUS
        direct = multirotor.rotor_forward(
            _blade(), replace(op, rpm=rpm), POLAR, v_edge_m_s=mu * tip, v_axial_m_s=lam * tip, azimuth_points=24
        )
        loads = table.evaluate(rpm, lam * tip, mu * tip)
        assert float(loads["thrust_N"]) == pytest.approx(direct["thrust_N"], rel=1e-9, abs=1e-12)
        assert float(loads["torque_Nm"]) == pytest.approx(direct["torque_Nm"], rel=1e-9, abs=1e-12)
        assert float(loads["roll_moment_Nm"]) == pytest.approx(direct["hub_roll_moment_Nm"], rel=1e-9, abs=1e-12)


def test_wrench_signs(rig):
    """Equal rotors in hover: no moments. A CCW rotor's torque yaws the body nose-right (+N in FRD)."""

    v = rig["vehicle"]
    zero = np.zeros((1, 4))
    loads = {"thrust_N": np.full((1, 4), 3.0), "torque_Nm": np.full((1, 4), 0.04), "h_force_N": zero, "roll_moment_Nm": zero}
    e_f = np.tile([1.0, 0.0, 0.0], (1, 4, 1))
    w = vh.wrench4(v, loads, e_f)[0]
    assert w[0] == pytest.approx(12.0) and np.allclose(w[1:], 0.0, atol=1e-12)
    loads["torque_Nm"] = np.array([[0.05, 0.05, 0.04, 0.04]])  # rotors 0, 1 are CCW
    assert vh.wrench4(v, loads, e_f)[0, 3] == pytest.approx(0.02)
    # Forward flight: the advancing side of a CCW rotor is on the right (+y), so its lift rolls the body left (-L).
    loads = {"thrust_N": zero, "torque_Nm": zero, "h_force_N": zero, "roll_moment_Nm": np.array([[0.1, 0.0, 0.0, 0.0]])}
    assert vh.wrench4(v, loads, e_f)[0, 1] == pytest.approx(-0.1)


def test_replay_recovers_logged_rpm(rig):
    t = np.arange(0.0, 4.0, 0.02)
    n = len(t)
    v_air = np.column_stack([6.0 + np.sin(t), 0.5 * np.cos(t), -0.3 * np.sin(2 * t)])
    rate = np.column_stack([0.2 * np.sin(3 * t), 0.1 * np.cos(2 * t), 0.3 * np.sin(t)])
    rpm = 7400.0 + np.column_stack([300 * np.sin(t + k) for k in range(4)])
    trace = from_rpm("synthetic", t, v_air, rate, rpm, rig["table"], rig["vehicle"])
    result = replay(trace, rig["vehicle"], rig["table"], rig["motor"], rig["battery"])
    assert np.max(np.abs(result["_series"]["rpm"] - rpm) / rpm) < 1e-4
    assert result["saturated_fraction"] == 0.0
    assert n == len(result["_series"]["duty"])


def test_motor_forward_dynamics_track_the_replay(rig):
    """Integrating J domega/dt = Kt (I - I0) - Q with the replay's duty and bus voltage reproduces its rotor speeds."""

    table, motor, battery, vehicle = rig["table"], rig["motor"], rig["battery"], rig["vehicle"]
    trace = missions.fly(missions.Mission("hover", (missions.Segment(6.0, yaw_rate_rad_s=0.3),), gust_m_s=1.0, seed=3), vehicle, 100.0)
    result = replay(trace, vehicle, table, motor, battery)
    s = result["_series"]
    flow = vh.inflow(vehicle, trace.v_air_body_m_s, trace.body_rate_rad_s)
    omega = s["rpm"][0] * math.pi / 30
    worst = 0.0
    for k in range(len(trace.t_s) - 1):
        for _ in range(20):
            dt = (trace.t_s[k + 1] - trace.t_s[k]) / 20
            aero = table.evaluate(omega * 30 / math.pi, flow["v_axial"][k], flow["v_edge"][k])["torque_Nm"]
            omega = omega + dt * (motor.torque(motor.current(s["duty"][k], s["v_bus_V"][k], omega)) - aero) / motor.inertia_kg_m2
        worst = max(worst, float(np.max(np.abs(omega * 30 / math.pi - s["rpm"][k + 1]) / s["rpm"][k + 1])))
    assert worst < 0.01


def test_static_motor_matches_lumped_model(rig):
    """With no battery resistance the duty-cycle model and multirotor.motor_point agree at a steady point."""

    motor = rig["motor"]
    battery = replace(rig["battery"], resistance_ohm=0.0)
    omega, torque = 800.0, 0.05
    current, terminal = mo.required_drive(motor, omega, torque)
    spec = replace(rig["spec"], battery_resistance_ohm=0.0)
    lumped = multirotor.motor_point(spec, motor.kv_rpm_per_V, torque, omega)
    assert float(current) == pytest.approx(lumped["current_A"])
    assert float(terminal) == pytest.approx(lumped["voltage_V"])
    v_bus = mo.bus_voltage(battery, 0.6, motor, np.array([terminal]), np.array([current]))
    assert v_bus == pytest.approx(float(battery.open_circuit_V(0.6)))


def test_px4_fit(rig):
    fit = px4.fit_parameters(rig["motor"], rig["table"], rig["battery"], rig["vehicle"], 3.4)
    p = fit["params"]
    assert 0.0 <= p["THR_MDL_FAC"] <= 1.0 and fit["thr_mdl_fac_fit_max_error"] < 0.03
    assert p["CA_ROTOR0_KM"] > 0 > p["CA_ROTOR2_KM"]
    assert p["MPC_THR_HOVER"] == pytest.approx(3.4 / p["CA_ROTOR0_CT"], abs=1e-3)


def test_reference_mission_hovers_at_weight(rig):
    v = rig["vehicle"]
    trace = missions.fly(missions.mission("hover"), v)
    assert np.mean(trace.wrench[:, 0]) == pytest.approx(v.mass_kg * vh.G, rel=0.02)


@pytest.mark.skipif(shutil.which("g++") is None, reason="needs g++")
def test_cpp_header_matches_python(rig, tmp_path):
    table = rig["table"]
    tables.write_cpp_tables(table, tmp_path / "rotor_tables.hpp")
    shutil.copyfile(px4.Path(tables.__file__).with_name("cpp") / "prandtl_rotor.hpp", tmp_path / "prandtl_rotor.hpp")
    points = [(7400.0, 1.2, 8.0), (5200.0, -2.5, 3.0), (11000.0, 6.0, 15.0), (300.0, -9.0, 25.0)]
    main = [
        "#include <cstdio>",
        '#include "rotor_tables.hpp"',
        "int main() {",
        "  const auto& t = prandtl::tables::test::table;",
    ]
    for rpm, va, ve in points:
        main.append(f"  {{ auto l = prandtl::evaluate(t, {rpm}, {va}, {ve});")
        main.append(
            '    std::printf("%.12g %.12g %.12g %.12g %d\\n", l.thrust_N, l.torque_Nm, l.h_force_N, l.roll_moment_Nm, (int)l.vrs); }'
        )
    main += [
        "  prandtl::Inflow f = prandtl::inflow({6.0, 1.0, -0.5}, {0.1, -0.2, 0.3}, {0.15, 0.15, 0.0});",
        "  auto l = prandtl::evaluate(t, 7400.0, f.v_axial, f.v_edge);",
        "  auto w = prandtl::rotor_wrench(l, {0.15, 0.15, 0.0}, 1, f);",
        '  std::printf("%.12g %.12g %.12g %.12g %.12g %.12g\\n", w.force[0], w.force[1], w.force[2], w.moment[0], w.moment[1], w.moment[2]);',
        "  return 0;",
        "}",
    ]
    (tmp_path / "main.cpp").write_text("\n".join(main))
    subprocess.run(["g++", "-std=c++17", "-O1", "-o", str(tmp_path / "main"), str(tmp_path / "main.cpp")], check=True)
    lines = subprocess.run([str(tmp_path / "main")], check=True, capture_output=True, text=True).stdout.split("\n")
    for (rpm, va, ve), line in zip(points, lines, strict=False):
        got = [float(x) for x in line.split()]
        want = table.evaluate(rpm, va, ve)
        expect = [float(want[k]) for k in ("thrust_N", "torque_Nm", "h_force_N", "roll_moment_Nm")] + [float(bool(want["vrs"]))]
        assert np.allclose(got, expect, rtol=1e-7, atol=1e-12)  # header stores 9 significant digits
    v = rig["vehicle"]
    single = replace(v, positions_m=np.array([[0.15, 0.15]]), spins=np.array([1]))
    flow = vh.inflow(single, np.array([[6.0, 1.0, -0.5]]), np.array([[0.1, -0.2, 0.3]]))
    loads = table.evaluate(7400.0, flow["v_axial"], flow["v_edge"])
    parts = vh.rotor_wrench4(single, loads, flow["e_f"])[0, 0]
    got = [float(x) for x in lines[len(points)].split()]
    assert got[2] == pytest.approx(-parts[0], rel=1e-7) and np.allclose(got[3:], parts[1:], rtol=1e-7, atol=1e-12)


def _ulog(path, topics: dict[str, tuple[list[tuple[str, str]], np.ndarray, list[np.ndarray]]]) -> None:
    """Minimal ULog v1 writer: header, flag bits, formats, add-logged, data (see the ULog file format spec)."""

    codes = {"uint64_t": "Q", "float": "f"}

    def message(kind: str, payload: bytes) -> bytes:
        return struct.pack("<HB", len(payload), ord(kind)) + payload

    out = bytearray(b"ULog\x01\x12\x35\x01" + struct.pack("<Q", 0))
    out += message("B", bytes(40))
    for name, (fields, _, _) in topics.items():
        out += message("F", f"{name}:uint64_t timestamp;{''.join(f'{kind} {field};' for kind, field in fields)}".encode())
    for msg_id, name in enumerate(topics):
        out += message("A", struct.pack("<BH", 0, msg_id) + name.encode())
    for msg_id, (_, (fields, stamps, columns)) in enumerate(topics.items()):
        for k, stamp in enumerate(stamps):
            payload = struct.pack("<HQ", msg_id, int(stamp))
            for (kind, _field), column in zip(fields, columns, strict=True):
                base = kind.split("[")[0]
                count = int(kind.split("[")[1][:-1]) if "[" in kind else 1
                values = np.atleast_1d(column[k])
                payload += struct.pack("<" + codes[base] * count, *values[:count])
            out += message("D", payload)
    path.write_bytes(bytes(out))


def test_ulog_ingest_reconstructs_rpm(rig, tmp_path):
    pytest.importorskip("pyulog")
    table, vehicle, motor = rig["table"], rig["vehicle"], rig["motor"]
    t = np.arange(0.0, 3.0, 0.02)
    stamps = (1.0e6 + t * 1.0e6).astype(np.int64)
    n = len(t)
    duty = np.column_stack([0.42 + 0.02 * np.sin(t + k) for k in range(4)])
    yaw = 0.3 * t
    q = np.column_stack([np.cos(yaw / 2), np.zeros(n), np.zeros(n), np.sin(yaw / 2)])
    vel = np.column_stack([5.0 * np.ones(n), np.zeros(n), np.zeros(n)])
    v_bus = np.full(n, 24.0)
    _ulog(
        tmp_path / "flight.ulg",
        {
            "actuator_motors": ([("float[12]", "control")], stamps, [np.column_stack([duty, np.zeros((n, 8))])]),
            "vehicle_attitude": ([("float[4]", "q")], stamps, [q]),
            "vehicle_local_position": ([("float", "vx"), ("float", "vy"), ("float", "vz")], stamps, [vel[:, 0], vel[:, 1], vel[:, 2]]),
            "vehicle_angular_velocity": ([("float[3]", "xyz")], stamps, [np.column_stack([np.zeros(n), np.zeros(n), np.full(n, 0.3)])]),
            "battery_status": ([("float", "voltage_v")], stamps, [v_bus]),
        },
    )
    trace = px4.read_ulog(tmp_path / "flight.ulg", table, vehicle, motor)
    assert isinstance(trace, Trace) and len(trace.t_s) == n
    rot = vh.rotation_from_quaternion(q.astype(np.float32).astype(float))
    assert np.allclose(trace.v_air_body_m_s, np.einsum("kji,kj->ki", rot, vel), atol=1e-5)
    flow = vh.inflow(vehicle, trace.v_air_body_m_s, trace.body_rate_rad_s)
    for k in (0, n // 2, n - 1):
        for i in range(4):
            omega = trace.rpm[k, i] * math.pi / 30
            balance = (
                motor.torque(motor.current(duty[k, i], 24.0, omega))
                - table.evaluate(trace.rpm[k, i], flow["v_axial"][k, i], flow["v_edge"][k, i])["torque_Nm"]
            )
            assert abs(float(balance)) < 1e-6
