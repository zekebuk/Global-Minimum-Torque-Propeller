// prandtl_rotor.hpp: header-only rotor + motor + battery model for a multirotor simulator (C++17).
//
// The same physics as prandtl_prop.sitl (Python), verified against it by tests/test_sitl.py.
// Tables come from `prandtl run <design> --stages sitl` (rotor_tables.hpp or rotor_table.json).
// Frames: body FRD; each rotor thrusts along z_r = -z_b; spin s = +1 counter-clockwise from above.
// Per physics step, per rotor:
//   1. v_rel = v_air_body + omega_body x p_rotor            (hub velocity relative to the air)
//   2. RotorLoads l = evaluate(table, rpm, v_axial, v_edge)  (v_axial = -v_rel.z, climb > 0)
//   3. Wrench w = rotor_wrench(l, p_rotor, spin, v_rel)       (body force and moment)
//   4. Motor: I = current(u, v_bus, omega); J domega/dt = torque(I) - l.torque
//   5. Battery: v_bus = bus_voltage(...) from all rotors' duty and current; soc -= i_bus dt / capacity
#pragma once

#include <algorithm>
#include <array>
#include <cmath>

namespace prandtl {

struct RotorTable {
  int n_rpm, n_lambda, n_mu;
  const double* rpm;       // [n_rpm]
  const double* lambda_c;  // [n_lambda]  V_axial / (Omega R)
  const double* mu;        // [n_mu]      V_edge / (Omega R)
  const double* ct;        // [n_rpm][n_lambda][n_mu], C order
  const double* cq;
  const double* ch;
  const double* cm;
  const double* vrs;  // cell flags (informational; evaluate() recomputes from the interpolated C_T)
  double radius_m, rho_kg_m3;
};

struct RotorLoads {
  double thrust_N = 0, torque_Nm = 0, h_force_N = 0, roll_moment_Nm = 0;
  bool vrs = false;  // inside the vortex-ring-state boundary: momentum theory invalid
};

namespace detail {
inline void bracket(const double* grid, int n, double x, int& low, double& frac) {
  if (n == 1) { low = 0; frac = 0.0; return; }
  x = std::clamp(x, grid[0], grid[n - 1]);
  low = static_cast<int>(std::upper_bound(grid, grid + n, x) - grid) - 1;
  low = std::clamp(low, 0, n - 2);
  frac = (x - grid[low]) / (grid[low + 1] - grid[low]);
}
}  // namespace detail

inline RotorLoads evaluate(const RotorTable& t, double rpm, double v_axial, double v_edge) {
  constexpr double kPi = 3.14159265358979323846;
  const double omega = std::max(rpm, 1e-9) * kPi / 30.0;
  const double tip = omega * t.radius_m;
  const double lam = v_axial / tip, mu = v_edge / tip;
  int ir, il, im;
  double fr, fl, fm;
  detail::bracket(t.rpm, t.n_rpm, rpm, ir, fr);
  detail::bracket(t.lambda_c, t.n_lambda, lam, il, fl);
  detail::bracket(t.mu, t.n_mu, mu, im, fm);
  double c[4] = {0, 0, 0, 0};
  const double* tabs[4] = {t.ct, t.cq, t.ch, t.cm};
  for (int a = 0; a < (t.n_rpm > 1 ? 2 : 1); ++a)
    for (int b = 0; b < 2; ++b)
      for (int d = 0; d < 2; ++d) {
        const double w = (t.n_rpm > 1 ? (a ? fr : 1 - fr) : 1.0) * (b ? fl : 1 - fl) * (d ? fm : 1 - fm);
        const int index = ((ir + a) * t.n_lambda + (il + b)) * t.n_mu + (im + d);
        for (int k = 0; k < 4; ++k) c[k] += w * tabs[k][index];
      }
  const double scale = t.rho_kg_m3 * kPi * t.radius_m * t.radius_m * tip * tip;
  RotorLoads out;
  out.thrust_N = scale * c[0];
  out.torque_Nm = scale * t.radius_m * c[1];
  out.h_force_N = scale * c[2];
  out.roll_moment_Nm = scale * t.radius_m * c[3];
  if (c[0] > 0) {
    const double vh = std::sqrt(c[0] / 2.0);
    out.vrs = (lam / vh > -2.0) && (lam / vh < -0.3) && (mu / vh < 1.0);
  }
  return out;
}

using Vec3 = std::array<double, 3>;

inline Vec3 cross(const Vec3& a, const Vec3& b) {
  return {a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]};
}

struct Inflow {
  double v_axial, v_edge;
  Vec3 e_f;  // in-plane direction the hub moves through the air
};

inline Inflow inflow(const Vec3& v_air_body, const Vec3& omega_body, const Vec3& p_rotor) {
  const Vec3 w = cross(omega_body, p_rotor);
  const Vec3 v{v_air_body[0] + w[0], v_air_body[1] + w[1], v_air_body[2] + w[2]};
  Inflow f;
  f.v_axial = -v[2];
  f.v_edge = std::hypot(v[0], v[1]);
  f.e_f = f.v_edge > 1e-6 ? Vec3{v[0] / f.v_edge, v[1] / f.v_edge, 0.0} : Vec3{1.0, 0.0, 0.0};
  return f;
}

struct Wrench {
  Vec3 force, moment;  // body FRD, about the CG
};

// force = T z_r - H e_f;  moment = p x force - s M_roll e_f + s Q z_b  (z_r = -z_b).
inline Wrench rotor_wrench(const RotorLoads& l, const Vec3& p_rotor, int spin, const Inflow& f) {
  Wrench w;
  w.force = {-l.h_force_N * f.e_f[0], -l.h_force_N * f.e_f[1], -l.thrust_N};
  w.moment = cross(p_rotor, w.force);
  for (int k = 0; k < 3; ++k) w.moment[k] -= spin * l.roll_moment_Nm * f.e_f[k];
  w.moment[2] += spin * l.torque_Nm;
  return w;
}

struct Motor {
  double kv_rpm_per_V, resistance_ohm, no_load_current_A, inertia_kg_m2, max_current_A;
  double esc_efficiency = 0.97;
  bool active_braking = true;
  double kv_rad() const { return kv_rpm_per_V * 3.14159265358979323846 / 30.0; }
  double current(double duty, double v_bus, double omega) const {
    const double i = (duty * v_bus - omega / kv_rad()) / resistance_ohm;
    return active_braking ? i : std::max(i, 0.0);
  }
  double torque(double current_A) const { return (current_A - no_load_current_A) / kv_rad(); }
  double bus_current(double duty, double current_A) const {
    return duty * (current_A >= 0 ? current_A / esc_efficiency : current_A * esc_efficiency);
  }
  // Explicit rotor-speed step; use dt well below inertia * R / Kt^2 (the electrical-mechanical time constant).
  double step_omega(double omega, double duty, double v_bus, double aero_torque_Nm, double dt) const {
    return std::max(0.0, omega + dt * (torque(current(duty, v_bus, omega)) - aero_torque_Nm) / inertia_kg_m2);
  }
};

struct Battery {
  int cells;
  double capacity_Ah, resistance_ohm;
  double open_circuit_V(double soc) const {
    static constexpr double ocv[21] = {3.27, 3.61, 3.69, 3.71, 3.73, 3.75, 3.77, 3.79, 3.80, 3.82, 3.84,
                                       3.85, 3.87, 3.91, 3.95, 3.98, 4.02, 4.08, 4.11, 4.15, 4.20};
    const double x = std::clamp(soc, 0.0, 1.0) * 20.0;
    const int i = std::min(static_cast<int>(x), 19);
    return cells * (ocv[i] + (x - i) * (ocv[i + 1] - ocv[i]));
  }
  // Bus voltage with every motor's duty u_i and back-EMF e_i = omega_i / Kv (closed form of the shared-bus balance).
  template <std::size_t N>
  double bus_voltage(double soc, const Motor& m, const std::array<double, N>& duty, const std::array<double, N>& omega) const {
    double a = 0, b = 0;
    for (std::size_t i = 0; i < N; ++i) {
      a += duty[i] * duty[i];
      b += duty[i] * omega[i] / m.kv_rad();
    }
    const double g = resistance_ohm / (m.resistance_ohm * m.esc_efficiency);
    return (open_circuit_V(soc) + g * b) / (1.0 + g * a);
  }
};

}  // namespace prandtl
