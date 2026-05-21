"""
Comprehensive pressure test suite for Geometry_optimization.py
Tests boundary conditions, extreme values, error handling, and numerical stability.
"""

import math
import sys
import time
from pathlib import Path

PROJECT_ROOT = None
for parent in Path(__file__).resolve().parents:
    if (parent / "Geometry_optimization.py").is_file():
        PROJECT_ROOT = parent
        break
if PROJECT_ROOT is None:
    raise RuntimeError("Could not find Geometry_optimization.py")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Import the module under test
from Geometry_optimization import (
    DesignInputs,
    AirfoilModel,
    AIRFOIL_LIBRARY,
    REFERENCE_MU,
    validate_user_inputs,
    diameter_in_to_radius_m,
    chord_in_to_fraction,
    normalized_spanload,
    annulus_widths,
    smoothness_penalty,
    reference_chord_penalty,
    thrust_to_induced_velocity,
    assign_target_alpha,
    solve_geometry_for_loading,
    solve_geometry,
    optimize_geometry,
)


class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []
    
    def record_pass(self, test_name):
        self.passed += 1
        print(f"✓ {test_name}")
    
    def record_fail(self, test_name, reason):
        self.failed += 1
        self.errors.append((test_name, reason))
        print(f"✗ {test_name}: {reason}")
    
    def summary(self):
        print(f"\n{'='*70}")
        print(f"PRESSURE TEST RESULTS: {self.passed} passed, {self.failed} failed")
        if self.errors:
            print(f"\nFailed Tests:")
            for name, reason in self.errors:
                print(f"  - {name}: {reason}")
        print(f"{'='*70}\n")


def test_unit_conversions(results):
    """Test unit conversion functions with edge cases."""
    print("\n--- UNIT CONVERSION TESTS ---")
    
    # Test diameter conversion
    try:
        radius = diameter_in_to_radius_m(22.0)
        assert 0.279 < radius < 0.281, f"Expected ~0.28m, got {radius}"
        results.record_pass("diameter_in_to_radius_m (normal)")
    except Exception as e:
        results.record_fail("diameter_in_to_radius_m (normal)", str(e))
    
    # Test zero diameter
    try:
        radius = diameter_in_to_radius_m(0.0)
        assert radius == 0.0
        results.record_pass("diameter_in_to_radius_m (zero)")
    except Exception as e:
        results.record_fail("diameter_in_to_radius_m (zero)", str(e))
    
    # Test very small diameter
    try:
        radius = diameter_in_to_radius_m(0.001)
        assert 0.0 < radius < 0.001, f"Got {radius}"
        results.record_pass("diameter_in_to_radius_m (very small)")
    except Exception as e:
        results.record_fail("diameter_in_to_radius_m (very small)", str(e))
    
    # Test very large diameter
    try:
        radius = diameter_in_to_radius_m(1000.0)
        assert radius > 10.0
        results.record_pass("diameter_in_to_radius_m (very large)")
    except Exception as e:
        results.record_fail("diameter_in_to_radius_m (very large)", str(e))
    
    # Test chord_in_to_fraction with None
    try:
        frac = chord_in_to_fraction(None, 22.0)
        assert math.isinf(frac)
        results.record_pass("chord_in_to_fraction (None)")
    except Exception as e:
        results.record_fail("chord_in_to_fraction (None)", str(e))
    
    # Test chord_in_to_fraction with zero
    try:
        frac = chord_in_to_fraction(0.0, 22.0)
        assert math.isinf(frac)
        results.record_pass("chord_in_to_fraction (zero)")
    except Exception as e:
        results.record_fail("chord_in_to_fraction (zero)", str(e))
    
    # Test chord_in_to_fraction with negative
    try:
        frac = chord_in_to_fraction(-1.0, 22.0)
        assert math.isinf(frac)
        results.record_pass("chord_in_to_fraction (negative)")
    except Exception as e:
        results.record_fail("chord_in_to_fraction (negative)", str(e))


def test_validation(results):
    """Test input validation with invalid configurations."""
    print("\n--- INPUT VALIDATION TESTS ---")
    
    # Valid config
    try:
        config = DesignInputs(rpm=4500.0, radius_m=0.28)
        validate_user_inputs(config)
        results.record_pass("validate_user_inputs (valid config)")
    except Exception as e:
        results.record_fail("validate_user_inputs (valid config)", str(e))
    
    # Negative RPM
    try:
        config = DesignInputs(rpm=-100.0)
        validate_user_inputs(config)
        results.record_fail("validate_user_inputs (negative RPM)", "Should have raised ValueError")
    except ValueError as e:
        results.record_pass("validate_user_inputs (negative RPM)")
    except Exception as e:
        results.record_fail("validate_user_inputs (negative RPM)", f"Wrong error: {e}")
    
    # Zero RPM
    try:
        config = DesignInputs(rpm=0.0)
        validate_user_inputs(config)
        results.record_fail("validate_user_inputs (zero RPM)", "Should have raised ValueError")
    except ValueError:
        results.record_pass("validate_user_inputs (zero RPM)")
    except Exception as e:
        results.record_fail("validate_user_inputs (zero RPM)", f"Wrong error: {e}")
    
    # Negative radius
    try:
        config = DesignInputs(rpm=4500.0, radius_m=-0.28)
        validate_user_inputs(config)
        results.record_fail("validate_user_inputs (negative radius)", "Should have raised ValueError")
    except ValueError:
        results.record_pass("validate_user_inputs (negative radius)")
    except Exception as e:
        results.record_fail("validate_user_inputs (negative radius)", f"Wrong error: {e}")
    
    # Invalid blade count
    try:
        config = DesignInputs(blades=0)
        validate_user_inputs(config)
        results.record_fail("validate_user_inputs (zero blades)", "Should have raised ValueError")
    except ValueError:
        results.record_pass("validate_user_inputs (zero blades)")
    except Exception as e:
        results.record_fail("validate_user_inputs (zero blades)", f"Wrong error: {e}")
    
    # Invalid hub radius
    try:
        config = DesignInputs(hub_radius_fraction=1.5)
        validate_user_inputs(config)
        results.record_fail("validate_user_inputs (hub_radius > 1)", "Should have raised ValueError")
    except ValueError:
        results.record_pass("validate_user_inputs (hub_radius > 1)")
    except Exception as e:
        results.record_fail("validate_user_inputs (hub_radius > 1)", f"Wrong error: {e}")
    
    # Negative air density
    try:
        config = DesignInputs(rho=-1.225)
        validate_user_inputs(config)
        results.record_fail("validate_user_inputs (negative rho)", "Should have raised ValueError")
    except ValueError:
        results.record_pass("validate_user_inputs (negative rho)")
    except Exception as e:
        results.record_fail("validate_user_inputs (negative rho)", f"Wrong error: {e}")
    
    # Zero thrust target
    try:
        config = DesignInputs(thrust_target_n=0.0)
        validate_user_inputs(config)
        results.record_fail("validate_user_inputs (zero thrust)", "Should have raised ValueError")
    except ValueError:
        results.record_pass("validate_user_inputs (zero thrust)")
    except Exception as e:
        results.record_fail("validate_user_inputs (zero thrust)", f"Wrong error: {e}")


def test_spanload_functions(results):
    """Test spanload and annulus width calculations."""
    print("\n--- SPANLOAD & ANNULUS TESTS ---")
    
    # Test normalized spanload - minimum induced loss
    try:
        spanload = normalized_spanload(REFERENCE_MU, "minimum_induced_loss", 0.20)
        assert len(spanload) == len(REFERENCE_MU)
        assert abs(sum(spanload) - 1.0) < 1e-6, f"Sum not normalized: {sum(spanload)}"
        assert all(0.0 <= val <= 1.0 for val in spanload), "Values out of range"
        results.record_pass("normalized_spanload (minimum_induced_loss)")
    except Exception as e:
        results.record_fail("normalized_spanload (minimum_induced_loss)", str(e))
    
    # Test normalized spanload - gmt bell
    try:
        spanload = normalized_spanload(REFERENCE_MU, "gmt_bell", 0.20)
        assert len(spanload) == len(REFERENCE_MU)
        assert abs(sum(spanload) - 1.0) < 1e-6, f"Sum not normalized: {sum(spanload)}"
        assert all(0.0 <= val <= 1.0 for val in spanload), "Values out of range"
        results.record_pass("normalized_spanload (gmt_bell)")
    except Exception as e:
        results.record_fail("normalized_spanload (gmt_bell)", str(e))
    
    # Test invalid spanload type
    try:
        spanload = normalized_spanload(REFERENCE_MU, "invalid_type", 0.20)
        results.record_fail("normalized_spanload (invalid type)", "Should have raised ValueError")
    except ValueError:
        results.record_pass("normalized_spanload (invalid type)")
    except Exception as e:
        results.record_fail("normalized_spanload (invalid type)", f"Wrong error: {e}")
    
    # Test annulus widths
    try:
        dr_values = annulus_widths(REFERENCE_MU, 0.28)
        assert len(dr_values) == len(REFERENCE_MU)
        assert all(val > 0.0 for val in dr_values), "All widths must be positive"
        results.record_pass("annulus_widths")
    except Exception as e:
        results.record_fail("annulus_widths", str(e))
    
    # Test annulus with single point
    try:
        dr_values = annulus_widths([0.5], 0.28)
        assert len(dr_values) == 1
        assert dr_values[0] > 0.0
        results.record_pass("annulus_widths (single point)")
    except Exception as e:
        results.record_fail("annulus_widths (single point)", str(e))


def test_aerodynamic_functions(results):
    """Test aerodynamic calculations."""
    print("\n--- AERODYNAMIC TESTS ---")
    
    # Test thrust to induced velocity
    try:
        vi = thrust_to_induced_velocity(100.0, 1.225, 0.28, 0.01, 0.0)
        assert vi >= 0.0, "Induced velocity must be non-negative"
        results.record_pass("thrust_to_induced_velocity (normal)")
    except Exception as e:
        results.record_fail("thrust_to_induced_velocity (normal)", str(e))
    
    # Test with zero thrust
    try:
        vi = thrust_to_induced_velocity(0.0, 1.225, 0.28, 0.01, 0.0)
        assert vi == 0.0, f"Expected 0.0, got {vi}"
        results.record_pass("thrust_to_induced_velocity (zero thrust)")
    except Exception as e:
        results.record_fail("thrust_to_induced_velocity (zero thrust)", str(e))
    
    # Test with very small area
    try:
        vi = thrust_to_induced_velocity(10.0, 1.225, 0.28, 1e-6, 0.0)
        assert vi > 0.0
        results.record_pass("thrust_to_induced_velocity (tiny area)")
    except Exception as e:
        results.record_fail("thrust_to_induced_velocity (tiny area)", str(e))
    
    # Test airfoil CL calculation
    try:
        airfoil = AIRFOIL_LIBRARY["MH115"]
        cl = airfoil.cl(5.0)
        assert isinstance(cl, float)
        results.record_pass("AirfoilModel.cl (normal)")
    except Exception as e:
        results.record_fail("AirfoilModel.cl (normal)", str(e))
    
    # Test airfoil CL saturation at high angle
    try:
        airfoil = AIRFOIL_LIBRARY["MH115"]
        cl_high = airfoil.cl(30.0)
        assert cl_high <= airfoil.cl_max, "CL should be limited"
        results.record_pass("AirfoilModel.cl (saturation)")
    except Exception as e:
        results.record_fail("AirfoilModel.cl (saturation)", str(e))
    
    # Test airfoil CL at negative angle
    try:
        airfoil = AIRFOIL_LIBRARY["MH115"]
        cl_neg = airfoil.cl(-30.0)
        assert cl_neg >= -0.8 * airfoil.cl_max, "CL should be limited"
        results.record_pass("AirfoilModel.cl (negative saturation)")
    except Exception as e:
        results.record_fail("AirfoilModel.cl (negative saturation)", str(e))
    
    # Test airfoil CD
    try:
        airfoil = AIRFOIL_LIBRARY["MH115"]
        cd = airfoil.cd(0.5)
        assert cd >= airfoil.cd0, "CD should be >= CD0"
        results.record_pass("AirfoilModel.cd")
    except Exception as e:
        results.record_fail("AirfoilModel.cd", str(e))
    
    # Test assign_target_alpha
    try:
        airfoil = AIRFOIL_LIBRARY["MH115"]
        alpha = assign_target_alpha(airfoil, 5.0, 0.5, "gmt_bell")
        assert alpha >= 1.5, "Alpha should be >= 1.5"
        results.record_pass("assign_target_alpha")
    except Exception as e:
        results.record_fail("assign_target_alpha", str(e))


def test_penalty_functions(results):
    """Test penalty calculations."""
    print("\n--- PENALTY TESTS ---")
    
    # Test smoothness penalty
    try:
        chords = [0.1, 0.15, 0.12, 0.14, 0.11]
        twists = [45.0, 43.0, 41.0, 39.0, 37.0]
        penalty = smoothness_penalty(chords, twists, 0.28)
        assert penalty >= 0.0, "Penalty should be non-negative"
        results.record_pass("smoothness_penalty")
    except Exception as e:
        results.record_fail("smoothness_penalty", str(e))
    
    # Test smoothness penalty with perfect smoothness
    try:
        chords = [0.1, 0.1, 0.1]
        twists = [45.0, 45.0, 45.0]
        penalty = smoothness_penalty(chords, twists, 0.28)
        assert penalty == 0.0, f"Perfect smoothness should give 0 penalty, got {penalty}"
        results.record_pass("smoothness_penalty (ideal)")
    except Exception as e:
        results.record_fail("smoothness_penalty (ideal)", str(e))
    
    # Test reference chord penalty
    try:
        chords = [0.054, 0.055, 0.056]  # Near reference values
        penalty = reference_chord_penalty(chords, 0.28)
        assert penalty >= 0.0, "Penalty should be non-negative"
        results.record_pass("reference_chord_penalty")
    except Exception as e:
        results.record_fail("reference_chord_penalty", str(e))


def test_solver_functions(results):
    """Test geometry solver with various configurations."""
    print("\n--- SOLVER TESTS ---")
    
    # Test with standard config
    try:
        config = DesignInputs()
        result = solve_geometry_for_loading(config, 1.0)
        assert len(result.stations) == len(REFERENCE_MU)
        assert result.thrust_n > 0.0
        assert result.torque_nm > 0.0
        results.record_pass("solve_geometry_for_loading (standard)")
    except Exception as e:
        results.record_fail("solve_geometry_for_loading (standard)", str(e))
    
    # Test with very high loading scale
    try:
        config = DesignInputs()
        result = solve_geometry_for_loading(config, 5.0)
        assert result.thrust_n > 0.0
        results.record_pass("solve_geometry_for_loading (high loading)")
    except Exception as e:
        results.record_fail("solve_geometry_for_loading (high loading)", str(e))
    
    # Test with very low loading scale
    try:
        config = DesignInputs()
        result = solve_geometry_for_loading(config, 0.1)
        assert result.thrust_n >= 0.0
        results.record_pass("solve_geometry_for_loading (low loading)")
    except Exception as e:
        results.record_fail("solve_geometry_for_loading (low loading)", str(e))
    
    # Test binary search solver
    try:
        config = DesignInputs()
        result = solve_geometry(config)
        assert len(result.stations) == len(REFERENCE_MU)
        # Check thrust is within 0.1% of target
        thrust_error = abs(result.thrust_n - config.thrust_target_n) / config.thrust_target_n
        assert thrust_error < 0.001, f"Thrust error too large: {thrust_error:.4f}"
        results.record_pass("solve_geometry (binary search)")
    except Exception as e:
        results.record_fail("solve_geometry (binary search)", str(e))
    
    # Test with different thrust targets
    try:
        config = DesignInputs(thrust_target_n=50.0)
        result = solve_geometry(config)
        thrust_error = abs(result.thrust_n - config.thrust_target_n) / config.thrust_target_n
        assert thrust_error < 0.001, f"Thrust error too large: {thrust_error:.4f}"
        results.record_pass("solve_geometry (high thrust)")
    except Exception as e:
        results.record_fail("solve_geometry (high thrust)", str(e))
    
    # Test optimizer
    try:
        config = DesignInputs()
        start = time.time()
        result = optimize_geometry(config)
        elapsed = time.time() - start
        assert result is not None
        assert result.objective > 0.0
        results.record_pass(f"optimize_geometry (took {elapsed:.2f}s)")
    except Exception as e:
        results.record_fail("optimize_geometry", str(e))


def test_extreme_values(results):
    """Test with extreme input values."""
    print("\n--- EXTREME VALUE TESTS ---")
    
    # Very high RPM
    try:
        config = DesignInputs(rpm=100000.0)
        validate_user_inputs(config)
        result = solve_geometry_for_loading(config, 1.0)
        assert result.torque_nm > 0.0
        results.record_pass("extreme RPM (100k)")
    except Exception as e:
        results.record_fail("extreme RPM (100k)", str(e))
    
    # Very low RPM
    try:
        config = DesignInputs(rpm=1.0)
        validate_user_inputs(config)
        results.record_pass("extreme RPM (1 rpm)")
    except Exception as e:
        results.record_fail("extreme RPM (1 rpm)", str(e))
    
    # Very large diameter
    try:
        config = DesignInputs(radius_m=10.0)
        validate_user_inputs(config)
        results.record_pass("extreme diameter (20m)")
    except Exception as e:
        results.record_fail("extreme diameter (20m)", str(e))
    
    # Very small diameter
    try:
        config = DesignInputs(radius_m=0.001)
        validate_user_inputs(config)
        results.record_pass("extreme diameter (2mm)")
    except Exception as e:
        results.record_fail("extreme diameter (2mm)", str(e))
    
    # High freestream velocity
    try:
        config = DesignInputs(freestream_mps=100.0)
        validate_user_inputs(config)
        results.record_pass("extreme velocity (100 m/s)")
    except Exception as e:
        results.record_fail("extreme velocity (100 m/s)", str(e))
    
    # Very high air density (underwater?)
    try:
        config = DesignInputs(rho=1000.0)
        validate_user_inputs(config)
        results.record_pass("extreme density (water)")
    except Exception as e:
        results.record_fail("extreme density (water)", str(e))
    
    # Very low air density (altitude)
    try:
        config = DesignInputs(rho=0.01)
        validate_user_inputs(config)
        results.record_pass("extreme density (high altitude)")
    except Exception as e:
        results.record_fail("extreme density (high altitude)", str(e))
    
    # Many blades
    try:
        config = DesignInputs(blades=100)
        validate_user_inputs(config)
        results.record_pass("extreme blade count (100)")
    except Exception as e:
        results.record_fail("extreme blade count (100)", str(e))
    
    # Single blade
    try:
        config = DesignInputs(blades=1)
        validate_user_inputs(config)
        results.record_pass("extreme blade count (1)")
    except Exception as e:
        results.record_fail("extreme blade count (1)", str(e))


def test_numerical_stability(results):
    """Test for numerical stability and division by zero handling."""
    print("\n--- NUMERICAL STABILITY TESTS ---")
    
    # Test with minimal airfoil parameters
    try:
        airfoil = AirfoilModel(
            name="test",
            cl_alpha_per_rad=1e-6,
            alpha_l0_deg=0.0,
            cl_max=1e-6,
            cd0=1e-6,
            induced_drag_k=1e-6,
            thickness_to_chord=1e-6
        )
        cl = airfoil.cl(5.0)
        cd = airfoil.cd(cl)
        assert not math.isnan(cl) and not math.isinf(cl)
        assert not math.isnan(cd) and not math.isinf(cd)
        results.record_pass("numerical stability (tiny airfoil params)")
    except Exception as e:
        results.record_fail("numerical stability (tiny airfoil params)", str(e))
    
    # Test thrust calculation with minimal area
    try:
        vi = thrust_to_induced_velocity(1.0, 1.225, 0.001, 1e-9, 0.0)
        assert not math.isnan(vi) and not math.isinf(vi)
        results.record_pass("numerical stability (minimal area)")
    except Exception as e:
        results.record_fail("numerical stability (minimal area)", str(e))
    
    # Test normalized spanload with hub at tip
    try:
        spanload = normalized_spanload([0.5], "minimum_induced_loss", 0.5)
        assert abs(sum(spanload) - 1.0) < 1e-6
        results.record_pass("numerical stability (hub=root)")
    except Exception as e:
        results.record_fail("numerical stability (hub=root)", str(e))
    
    # Test with hub very close to tip
    try:
        spanload = normalized_spanload(REFERENCE_MU, "gmt_bell", 0.99)
        assert abs(sum(spanload) - 1.0) < 1e-6
        assert not any(math.isnan(v) or math.isinf(v) for v in spanload)
        results.record_pass("numerical stability (hub near tip)")
    except Exception as e:
        results.record_fail("numerical stability (hub near tip)", str(e))


def test_data_consistency(results):
    """Test that outputs are self-consistent."""
    print("\n--- DATA CONSISTENCY TESTS ---")
    
    try:
        config = DesignInputs()
        result = solve_geometry(config)
        
        # Check station data consistency
        total_thrust = sum(s.dthrust_n for s in result.stations)
        assert abs(total_thrust - result.thrust_n) < 1e-3, "Thrust mismatch"
        
        # Check torque consistency
        omega = config.rpm * 2.0 * math.pi / 60.0
        calc_power = result.torque_nm * omega
        assert abs(calc_power - result.power_w) < 1e-3, "Power mismatch"
        
        # Check all stations have valid data
        for station in result.stations:
            assert 0.0 < station.mu <= 1.0, f"Invalid mu: {station.mu}"
            assert station.radius_m > 0.0, "Invalid radius"
            assert station.chord_m > 0.0, "Invalid chord"
            assert not math.isnan(station.twist_deg), "NaN twist"
            assert not math.isnan(station.cl), "NaN CL"
            assert not math.isnan(station.cd), "NaN CD"
        
        results.record_pass("data consistency checks")
    except Exception as e:
        results.record_fail("data consistency checks", str(e))


def main():
    """Run all pressure tests."""
    results = TestResults()
    
    print("\n" + "="*70)
    print("GEOMETRY OPTIMIZATION PRESSURE TEST SUITE")
    print("="*70)
    
    test_unit_conversions(results)
    test_validation(results)
    test_spanload_functions(results)
    test_aerodynamic_functions(results)
    test_penalty_functions(results)
    test_extreme_values(results)
    test_numerical_stability(results)
    test_solver_functions(results)
    test_data_consistency(results)
    
    results.summary()
    
    return 0 if results.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
