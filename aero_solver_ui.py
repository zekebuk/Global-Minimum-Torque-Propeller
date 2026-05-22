from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st

import aero_solver
from airfoil_tools.airfoil_polar_loader import load_airfoil_polars
from airfoil_tools.generate_xfoil_polars import find_xfoil_executable, generate_xfoil_polars


REQUIRED_AIRFOILS = aero_solver.REQUIRED_AIRFOILS
REQUIRED_REYNOLDS = aero_solver.REQUIRED_POLAR_REYNOLDS_NUMBERS
DEFAULT_CONFIG_PATH = aero_solver.PROJECT_ROOT / "configs/aero/thesis_22_static_35N_config.json"

STATION_DISPLAY_COLUMNS = [
    "r_R",
    "r_m",
    "chord_m",
    "twist_deg",
    "airfoil",
    "Re",
    "polar_Re_used",
    "phi_deg",
    "alpha_deg",
    "Cl",
    "Cd",
    "dT_N",
    "dQ_Nm",
    "qz_N_per_m",
    "qy_tangential_N_per_m",
    "torque_loading_Nm_per_m",
    "cumulative_thrust_fraction",
    "cumulative_torque_fraction",
]

PLOT_STEMS = [
    ("thrust_loading", "Thrust Loading"),
    ("torque_loading", "Torque Loading"),
    ("cumulative_thrust", "Cumulative Thrust"),
    ("cumulative_torque", "Cumulative Torque"),
    ("cl_distribution", "Cl Distribution"),
    ("cd_distribution", "Cd Distribution"),
    ("alpha_distribution", "Alpha Distribution"),
    ("reynolds_distribution", "Reynolds Number Distribution"),
]

SUMMARY_METRICS = [
    ("Thrust [N]", "thrust_N"),
    ("Target thrust [N]", "target_thrust_N"),
    ("Thrust error [%]", "thrust_error_pct"),
    ("Torque [N*m]", "torque_Nm"),
    ("Power [W]", "power_W"),
    ("CT [-]", "CT"),
    ("CQ [-]", "CQ"),
    ("CP [-]", "CP"),
    ("Max alpha [deg]", "max_alpha_deg"),
    ("Min alpha [deg]", "min_alpha_deg"),
    ("Max Cl [-]", "max_Cl"),
    ("Max Cd [-]", "max_Cd"),
    ("Re min [-]", "Re_min"),
    ("Re max [-]", "Re_max"),
    ("Collective offset [deg]", "collective_twist_offset_deg"),
    ("Valid for thesis", "valid_for_thesis"),
]

LOAD_PLOT_SPECS = [
    ("qz_N_per_m", "Thrust Loading", "Thrust loading qz [N/m]"),
    ("qy_tangential_N_per_m", "Tangential Loading", "Tangential loading qy [N/m]"),
    ("torque_loading_Nm_per_m", "Torque Loading", "Torque loading [N*m/m]"),
    ("cumulative_thrust_fraction", "Cumulative Thrust", "Cumulative thrust fraction [-]"),
    ("cumulative_torque_fraction", "Cumulative Torque", "Cumulative torque fraction [-]"),
]

COEFFICIENT_PLOT_SPECS = [
    ("Cl", "Lift Coefficient", "Cl [-]"),
    ("Cd", "Drag Coefficient", "Cd [-]"),
    ("Cm", "Moment Coefficient", "Cm [-]"),
]

RE_ALPHA_PLOT_SPECS = [
    ("Re", "Reynolds Number", "Re [-]"),
    ("alpha_deg", "Angle of Attack", "alpha [deg]"),
    ("phi_deg", "Inflow Angle", "phi [deg]"),
]


def main() -> None:
    st.set_page_config(page_title="22-Inch MIL/GMT Aero Solver", layout="wide")
    st.title("22-Inch MIL/GMT Aero Solver")
    st.caption("XFOIL polars + BEMT thrust/torque solver + optional XROTOR comparison")

    if "config" not in st.session_state:
        st.session_state.config = aero_solver.get_default_aero_config()
    if "last_results" not in st.session_state:
        st.session_state.last_results = None

    config = _sidebar_config(st.session_state.config)
    st.session_state.config = config

    _main_controls(config)
    _display_results(config)


def _sidebar_config(base_config: dict[str, Any]) -> dict[str, Any]:
    config = dict(base_config)
    st.sidebar.title("Aero Solver Inputs")
    case_type_options = [
        "scaled_reference_mil_22",
        "scaled_reference_gmt_22",
        "optimized_mil_22",
        "optimized_gmt_22",
        "custom",
    ]

    with st.sidebar.expander("Case Setup", expanded=True):
        case_name = st.text_input("Case name", value=str(config.get("case_name", "thesis_22_static_35N")))
        case_type = st.selectbox(
            "Case type",
            case_type_options,
            index=case_type_options.index(str(config.get("case_type", "optimized_mil_22")))
            if str(config.get("case_type", "optimized_mil_22")) in case_type_options
            else 2,
        )
        default_geometry = aero_solver.CASE_GEOMETRY_PATHS.get(case_type, aero_solver.CASE_GEOMETRY_PATHS["optimized_mil_22"])
        geometry_value = (
            str(default_geometry) if case_type != "custom" else str(config.get("geometry_csv_path", default_geometry))
        )
        geometry_csv_path = st.text_input("Geometry CSV path", value=geometry_value)
        _path_status(geometry_csv_path, "geometry CSV", must_exist=True, expect_dir=False)

        output_dir = st.text_input("Output directory", value=str(config.get("output_dir", "results/aero")))
        _path_status(output_dir, "output directory", must_exist=False, expect_dir=True)

        figure_output_dir = st.text_input(
            "Figure output directory",
            value=str(config.get("figure_output_dir", "figures_paper/aero")),
        )
        _path_status(figure_output_dir, "figure directory", must_exist=False, expect_dir=True)

        config_path = st.text_input("Config JSON path", value=str(config.get("config_path", DEFAULT_CONFIG_PATH)))
        _path_status(config_path, "config JSON", must_exist=False, expect_dir=False)

    with st.sidebar.expander("Operating Condition", expanded=True):
        diameter_in = st.number_input("Diameter [in]", min_value=0.001, value=float(config.get("diameter_in", 22.0)))
        diameter_m = diameter_in * 0.0254
        radius_m = diameter_m / 2.0
        st.text_input("Radius [m]", value=f"{radius_m:.6f}", disabled=True)
        blade_count = st.number_input("Blade count", min_value=1, step=1, value=int(config.get("blade_count", 2)))
        rpm = st.number_input("RPM", min_value=0.0, value=float(config.get("rpm", 4000.0)))
        omega = aero_solver.rpm_to_omega(rpm)
        st.text_input("Omega [rad/s]", value=f"{omega:.3f}", disabled=True)
        rho = st.number_input("Air density rho [kg/m^3]", min_value=0.000001, value=float(config.get("rho", 1.225)))
        mu_air = st.number_input(
            "Dynamic viscosity mu [Pa*s]",
            min_value=1.0e-9,
            value=float(config.get("mu_air_kg_m_s", 1.81e-5)),
            format="%.8e",
        )
        v_inf = st.number_input("Freestream velocity V_inf [m/s]", value=float(config.get("V_inf_m_s", 0.0)))
        target_thrust = st.number_input(
            "Target thrust [N]",
            min_value=0.000001,
            value=float(config.get("target_thrust_N", 35.0)),
        )
        hover_mode = st.checkbox("Hover mode", value=bool(config.get("hover_mode", True)))
        if hover_mode and abs(v_inf) <= 1.0e-12:
            st.info("Static hover: actuator-disk induced velocity is active in the backend.")

    with st.sidebar.expander("Solver Options", expanded=True):
        match_target = st.checkbox("Match target thrust", value=bool(config.get("match_target", True)))
        tolerance_pct = st.number_input(
            "Target match tolerance [%]",
            min_value=0.001,
            value=float(config.get("tolerance_pct", 1.0)),
            format="%.3f",
        )
        generate_plots = st.checkbox("Generate exported plot files", value=bool(config.get("generate_plots", True)))
        run_xrotor = st.checkbox("Run XROTOR comparison", value=bool(config.get("run_xrotor", False)))
        xrotor_tolerance_pct = st.number_input(
            "XROTOR warning tolerance [%]",
            min_value=0.1,
            value=float(config.get("xrotor_tolerance_pct", 5.0)),
            format="%.2f",
        )

    with st.sidebar.expander("Advanced Settings", expanded=False):
        airfoil_dir = st.text_input("Airfoil directory", value=str(config.get("airfoil_dir", aero_solver.DEFAULT_AIRFOIL_DIR)))
        _path_status(airfoil_dir, "polar directory", must_exist=True, expect_dir=True)
        generated_dir = Path(airfoil_dir) / "generated_polars"
        st.text_input("Generated polar directory", value=str(generated_dir), disabled=True)
        _path_status(generated_dir, "generated polar directory", must_exist=True, expect_dir=True)

        xfoil_path = st.text_input("XFOIL executable/root path", value=str(config.get("xfoil_path", aero_solver.DEFAULT_XFOIL_ROOT)))
        allow_debug = st.checkbox(
            "Allow debug placeholder polar",
            value=bool(config.get("allow_debug_placeholder_polar", False)),
        )
        if allow_debug:
            st.error("Debug placeholder polar is not thesis-valid.")

        xrotor_path = st.text_input(
            "XROTOR executable/root path",
            value=str(config.get("xrotor_path", aero_solver.DEFAULT_XROTOR_EXE)),
        )
        xrotor_timeout_s = st.number_input(
            "XROTOR timeout [s]",
            min_value=1.0,
            value=float(config.get("xrotor_timeout_s", 60.0)),
        )

        tip_region_r_R = st.slider(
            "Tip warning region starts at r/R",
            min_value=0.70,
            max_value=0.98,
            value=float(config.get("tip_region_r_R", 0.90)),
            step=0.01,
        )
        negative_loading_fail_fraction_pct = st.number_input(
            "Negative loading FAIL threshold [% stations]",
            min_value=0.0,
            value=float(config.get("negative_loading_fail_fraction_pct", 10.0)),
            format="%.1f",
        )
        polar_extrapolation_fail_fraction_pct = st.number_input(
            "Polar extrapolation FAIL threshold [% stations]",
            min_value=0.0,
            value=float(config.get("polar_extrapolation_fail_fraction_pct", 20.0)),
            format="%.1f",
        )

        tool_cols = st.columns(2)
        with tool_cols[0]:
            if st.button("Check XFOIL", use_container_width=True):
                _check_xfoil(xfoil_path, airfoil_dir)
        with tool_cols[1]:
            if st.button("Check XROTOR", use_container_width=True):
                _check_xrotor(xrotor_path)
        if st.button("Generate XFOIL Polars", use_container_width=True):
            _generate_xfoil_polars(xfoil_path, airfoil_dir)
        st.caption("Required airfoils: " + ", ".join(REQUIRED_AIRFOILS))
        st.dataframe(_polar_status_table(airfoil_dir), use_container_width=True, hide_index=True)

    return {
        **config,
        "case_name": case_name,
        "case_type": case_type,
        "geometry_csv_path": geometry_csv_path,
        "output_dir": output_dir,
        "figure_output_dir": figure_output_dir,
        "config_path": config_path,
        "diameter_in": diameter_in,
        "diameter_m": diameter_m,
        "radius_m": radius_m,
        "blade_count": int(blade_count),
        "rpm": rpm,
        "rho": rho,
        "mu_air_kg_m_s": mu_air,
        "V_inf_m_s": v_inf,
        "target_thrust_N": target_thrust,
        "hover_mode": hover_mode,
        "match_target": match_target,
        "tolerance_pct": tolerance_pct,
        "airfoil_dir": airfoil_dir,
        "xfoil_path": xfoil_path,
        "allow_debug_placeholder_polar": allow_debug,
        "run_xrotor": run_xrotor,
        "xrotor_path": xrotor_path,
        "xrotor_timeout_s": xrotor_timeout_s,
        "xrotor_tolerance_pct": xrotor_tolerance_pct,
        "generate_plots": generate_plots,
        "tip_region_r_R": tip_region_r_R,
        "negative_loading_fail_fraction_pct": negative_loading_fail_fraction_pct,
        "polar_extrapolation_fail_fraction_pct": polar_extrapolation_fail_fraction_pct,
    }


def _main_controls(config: dict[str, Any]) -> None:
    st.subheader("Run Controls")
    cols = st.columns(4)
    with cols[0]:
        validate_clicked = st.button("Validate Inputs", use_container_width=True)
    with cols[1]:
        run_clicked = st.button("Run Aero Solver", type="primary", use_container_width=True)
    with cols[2]:
        plots_clicked = st.button("Generate Plots", use_container_width=True)
    with cols[3]:
        xrotor_clicked = st.button("Run XROTOR Comparison", use_container_width=True)

    config_path = str(config.get("config_path", DEFAULT_CONFIG_PATH))
    file_cols = st.columns(4)
    with file_cols[0]:
        if st.button("Save Config JSON", use_container_width=True):
            _save_config(config, config_path)
    with file_cols[1]:
        if st.button("Load Config JSON", use_container_width=True):
            _load_config(config_path)
    with file_cols[2]:
        open_clicked = st.button("Open Output Folder", use_container_width=True)
    with file_cols[3]:
        st.download_button(
            "Download Config JSON",
            data=json.dumps(config, indent=2),
            file_name=f"{config['case_name']}_config.json",
            mime="application/json",
            use_container_width=True,
        )

    if validate_clicked:
        _show_validation(config)
    if run_clicked:
        _run_solver(config)
    if plots_clicked:
        _generate_plots(config)
    if xrotor_clicked:
        _run_xrotor_only(config)
    if open_clicked:
        _open_output_folder(config["output_dir"])


def _display_results(config: dict[str, Any]) -> None:
    results = st.session_state.last_results
    if results is None:
        results = _load_existing_results_silent(config)
    if results is None:
        st.info("Run the aero solver or load existing outputs to inspect results.")
        return

    summary = results.get("summary", {})
    station_frame = _station_frame_from_results_or_file(results, config)
    tabs = st.tabs(
        [
            "Summary",
            "Station Data",
            "Loads",
            "Coefficients",
            "Reynolds / Alpha",
            "XROTOR Comparison",
            "Files / Exports",
        ]
    )

    with tabs[0]:
        _display_summary_tab(results, station_frame, config)
    with tabs[1]:
        _display_station_data_tab(results, station_frame, config)
    with tabs[2]:
        _display_plot_tab(station_frame, LOAD_PLOT_SPECS, "Loads")
        _display_data_subset(
            station_frame,
            [
                "r_R",
                "qz_N_per_m",
                "qy_tangential_N_per_m",
                "torque_loading_Nm_per_m",
                "dT_N",
                "dQ_Nm",
                "cumulative_thrust_fraction",
                "cumulative_torque_fraction",
            ],
        )
    with tabs[3]:
        _display_plot_tab(station_frame, COEFFICIENT_PLOT_SPECS, "Coefficients")
        _display_data_subset(station_frame, ["r_R", "airfoil", "Cl", "Cd", "Cm", "alpha_deg", "polar_source"])
    with tabs[4]:
        _display_plot_tab(station_frame, RE_ALPHA_PLOT_SPECS, "Reynolds / Alpha")
        _display_data_subset(station_frame, ["r_R", "airfoil", "Re", "polar_Re_used", "alpha_deg", "phi_deg"])
    with tabs[5]:
        _display_xrotor_comparison(config, summary)
    with tabs[6]:
        _display_files_tab(results, station_frame, config)


def _display_summary_tab(results: dict[str, Any], station_frame: pd.DataFrame, config: dict[str, Any]) -> None:
    summary = results.get("summary", {})
    st.subheader("Summary")
    status = _result_status(summary)
    if status == "green":
        st.success("Target matched and valid for thesis.")
    elif status == "yellow":
        st.warning("Result is conditional. Review warnings and comparison notes.")
    else:
        st.error(summary.get("failure_reason") or "Result is not thesis-valid.")

    _metric_grid(SUMMARY_METRICS, summary)
    _display_aero_warning_flags(station_frame, summary, config)
    _display_sanity_checks(results)

    if results.get("warnings"):
        with st.expander("Solver warnings", expanded=True):
            for warning in results["warnings"]:
                st.warning(str(warning))


def _display_station_data_tab(results: dict[str, Any], station_frame: pd.DataFrame, config: dict[str, Any]) -> None:
    st.subheader("Station Data")
    if station_frame.empty:
        st.info("Station data are not available.")
        return
    visible_columns = [column for column in STATION_DISPLAY_COLUMNS if column in station_frame.columns]
    st.dataframe(_format_station_frame(station_frame[visible_columns]), use_container_width=True, hide_index=True)
    station_path = _station_csv_path(results, config)
    if station_path.is_file():
        st.download_button(
            "Download station CSV",
            data=station_path.read_bytes(),
            file_name=station_path.name,
            mime="text/csv",
            use_container_width=True,
        )


def _show_validation(config: dict[str, Any]) -> None:
    errors = aero_solver.validate_aero_config(config, require_polars=not config.get("allow_debug_placeholder_polar", False))
    if errors:
        st.error("Input validation failed.")
        for error in errors:
            st.write(f"- {error}")
    else:
        st.success("Inputs are valid.")


def _run_solver(config: dict[str, Any]) -> None:
    try:
        errors = aero_solver.validate_aero_config(config, require_polars=False)
        if errors:
            st.error("Input validation failed.")
            for error in errors:
                st.write(f"- {error}")
            return
        missing_polars = aero_solver.find_missing_xfoil_polar_csvs(config["airfoil_dir"])
        if missing_polars and not config.get("allow_debug_placeholder_polar", False):
            st.warning("Missing XFOIL polar CSVs. The backend will attempt generation before running.")
        with st.spinner("Running BEMT aero solver..."):
            results = aero_solver.run_aero_from_config(config)
        st.session_state.last_results = results
        st.success("Aero solver finished.")
    except Exception:
        _show_exception("Aero solver run failed")


def _generate_plots(config: dict[str, Any]) -> None:
    try:
        with st.spinner("Generating aero plots..."):
            if st.session_state.last_results is not None:
                aero_solver.plot_results(st.session_state.last_results, config["figure_output_dir"])
            else:
                aero_solver.generate_aero_plots_from_config(config)
        st.success("Plots generated.")
    except Exception:
        _show_exception("Plot generation failed")


def _run_xrotor_only(config: dict[str, Any]) -> None:
    try:
        results = st.session_state.last_results or aero_solver.load_aero_results(config["output_dir"], config["case_name"])
        polar = aero_solver.load_airfoil_polar_directory(config["airfoil_dir"])
        with st.spinner("Running optional XROTOR comparison..."):
            aero_solver.run_xrotor_comparison(
                results,
                polar,
                config["output_dir"],
                xrotor_root=_xrotor_root(config["xrotor_path"]),
                timeout_s=float(config.get("xrotor_timeout_s", 60.0)),
            )
            aero_solver.export_results(results, config["output_dir"], results["case_name"])
        st.session_state.last_results = results
        st.success("XROTOR comparison completed.")
    except Exception:
        _show_exception("XROTOR comparison failed")


def _display_sanity_checks(results: dict[str, Any]) -> None:
    st.subheader("Sanity Checks")
    detailed = _sanity_check_table(results)
    summary_rows = detailed[["check", "status"]].copy()
    st.dataframe(summary_rows, use_container_width=True, hide_index=True)
    with st.expander("Detailed sanity checks", expanded=False):
        st.dataframe(detailed, use_container_width=True, hide_index=True)


def _display_plot_tab(station_frame: pd.DataFrame, plot_specs: list[tuple[str, str, str]], title: str) -> None:
    st.subheader(title)
    if station_frame.empty:
        st.info("Station data are not available.")
        return
    for field, plot_title, ylabel in plot_specs:
        if field not in station_frame.columns:
            continue
        _render_station_plot(station_frame, field, plot_title, ylabel)


def _display_data_subset(station_frame: pd.DataFrame, columns: list[str]) -> None:
    if station_frame.empty:
        return
    visible = [column for column in columns if column in station_frame.columns]
    if visible:
        st.dataframe(_format_station_frame(station_frame[visible]), use_container_width=True, hide_index=True)


def _display_xrotor_comparison(config: dict[str, Any], summary: dict[str, Any]) -> None:
    st.subheader("XROTOR Comparison")
    comparison_path = Path(config["output_dir"]) / "xrotor_comparison_summary.csv"
    if summary.get("xrotor_ran") and summary.get("xrotor_success") and comparison_path.is_file():
        comparison = _xrotor_comparison_table(comparison_path, summary, config)
        if comparison.empty:
            st.info("XROTOR comparison file is present, but no displayable metrics were found.")
        else:
            st.dataframe(comparison, use_container_width=True, hide_index=True)
        if _is_static_case(summary):
            st.info(
                "Static propulsive efficiency is hidden because V_inf is near zero. "
                "Use thrust per power, power loading, or figure of merit for static hover comparison."
            )
    elif summary.get("xrotor_ran") and not summary.get("xrotor_success"):
        st.warning("XROTOR comparison was attempted but did not succeed. BEMT validity is unchanged.")
    else:
        st.info("XROTOR comparison has not been run.")


def _display_files_tab(results: dict[str, Any], station_frame: pd.DataFrame, config: dict[str, Any]) -> None:
    st.subheader("Files / Exports")
    case_name = _results_case_name(results, config)
    clean_name = _clean_case_name(case_name)
    output_dir = Path(config["output_dir"])
    figure_dir = Path(config["figure_output_dir"])
    file_rows = [
        ("Station CSV", output_dir / f"{clean_name}_stations.csv"),
        ("Summary JSON", output_dir / f"{clean_name}_summary.json"),
        ("Summary TXT", output_dir / f"{clean_name}_summary.txt"),
        ("XROTOR comparison CSV", output_dir / "xrotor_comparison_summary.csv"),
        ("XROTOR comparison Markdown", output_dir / "xrotor_comparison_summary.md"),
    ]
    for stem, title in PLOT_STEMS:
        file_rows.extend(
            [
                (f"{title} PDF", figure_dir / f"{clean_name}_{stem}.pdf"),
                (f"{title} PNG", figure_dir / f"{clean_name}_{stem}.png"),
            ]
        )
    files = pd.DataFrame(
        [
            {"file": label, "path": str(path), "status": "Available" if path.is_file() else "Missing"}
            for label, path in file_rows
        ]
    )
    st.dataframe(files, use_container_width=True, hide_index=True)

    station_path = output_dir / f"{clean_name}_stations.csv"
    summary_path = output_dir / f"{clean_name}_summary.json"
    download_cols = st.columns(3)
    with download_cols[0]:
        if station_path.is_file():
            st.download_button(
                "Download Station CSV",
                data=station_path.read_bytes(),
                file_name=station_path.name,
                mime="text/csv",
                use_container_width=True,
            )
    with download_cols[1]:
        if summary_path.is_file():
            st.download_button(
                "Download Summary JSON",
                data=summary_path.read_bytes(),
                file_name=summary_path.name,
                mime="application/json",
                use_container_width=True,
            )
    with download_cols[2]:
        st.download_button(
            "Download Current Config",
            data=json.dumps(config, indent=2),
            file_name=f"{clean_name}_ui_config.json",
            mime="application/json",
            use_container_width=True,
        )

    if station_frame.empty:
        st.info("No station table is loaded for export preview.")


def _path_status(path_value: str | Path, label: str, must_exist: bool, expect_dir: bool) -> None:
    path = Path(path_value)
    exists = path.exists()
    if exists:
        is_expected_type = path.is_dir() if expect_dir else path.is_file()
        if is_expected_type:
            st.caption(f"{label}: found")
        else:
            st.warning(f"{label}: path exists but is the wrong type.")
        return
    if must_exist:
        st.warning(f"{label}: not found.")
    else:
        parent = path if expect_dir else path.parent
        if parent.exists():
            st.caption(f"{label}: will be created or overwritten as needed.")
        else:
            st.warning(f"{label}: parent folder does not exist.")


def _metric_grid(metrics: list[tuple[str, str]], summary: dict[str, Any]) -> None:
    for row_start in range(0, len(metrics), 4):
        cols = st.columns(4)
        for col, (label, key) in zip(cols, metrics[row_start : row_start + 4]):
            col.metric(label, _format_metric_value(key, summary.get(key)))


def _station_frame_from_results_or_file(results: dict[str, Any], config: dict[str, Any]) -> pd.DataFrame:
    stations = results.get("stations")
    if stations:
        return _coerce_numeric_frame(pd.DataFrame(stations))
    station_path = _station_csv_path(results, config)
    if station_path.is_file():
        return _coerce_numeric_frame(pd.read_csv(station_path))
    return pd.DataFrame()


def _coerce_numeric_frame(frame: pd.DataFrame) -> pd.DataFrame:
    coerced = frame.copy()
    for column in coerced.columns:
        if column in {"case_name", "airfoil", "polar_source", "polar_Re_used"}:
            continue
        converted = pd.to_numeric(coerced[column], errors="coerce")
        coerced[column] = converted
    return coerced


def _station_csv_path(results: dict[str, Any], config: dict[str, Any]) -> Path:
    return Path(config["output_dir"]) / f"{_clean_case_name(_results_case_name(results, config))}_stations.csv"


def _results_case_name(results: dict[str, Any], config: dict[str, Any]) -> str:
    summary = results.get("summary", {})
    return str(results.get("case_name") or summary.get("case_name") or config.get("case_name", "aero_case"))


def _format_station_frame(frame: pd.DataFrame) -> pd.DataFrame:
    formatted = frame.copy()
    formats = {
        "r_R": "{:.3f}",
        "r_m": "{:.4f}",
        "chord_m": "{:.4f}",
        "twist_deg": "{:.2f}",
        "Re": "{:.0f}",
        "phi_deg": "{:.2f}",
        "alpha_deg": "{:.2f}",
        "Cl": "{:.3f}",
        "Cd": "{:.4f}",
        "Cm": "{:.4f}",
        "dT_N": "{:.3f}",
        "dQ_Nm": "{:.4f}",
        "qz_N_per_m": "{:.2f}",
        "qy_tangential_N_per_m": "{:.2f}",
        "torque_loading_Nm_per_m": "{:.3f}",
        "cumulative_thrust_fraction": "{:.3f}",
        "cumulative_torque_fraction": "{:.3f}",
    }
    for column, fmt in formats.items():
        if column in formatted.columns:
            formatted[column] = formatted[column].map(lambda value, fmt=fmt: _format_cell(value, fmt))
    for column in {"case_name", "airfoil", "polar_Re_used", "polar_source"}:
        if column in formatted.columns:
            formatted[column] = formatted[column].fillna("").astype(str)
    for column in formatted.columns:
        if formatted[column].dtype == bool:
            formatted[column] = formatted[column].map(_yes_no)
    return formatted


def _format_cell(value: Any, fmt: str) -> str:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return fmt.format(float(value))
    return "" if value is None else str(value)


def _render_station_plot(frame: pd.DataFrame, field: str, title: str, ylabel: str) -> None:
    import matplotlib.pyplot as plt

    if "r_R" not in frame.columns:
        st.info("Cannot plot station data without r/R.")
        return
    plot_frame = frame[["r_R", field]].copy()
    plot_frame["r_R"] = pd.to_numeric(plot_frame["r_R"], errors="coerce")
    plot_frame[field] = pd.to_numeric(plot_frame[field], errors="coerce")
    plot_frame = plot_frame.dropna()
    if plot_frame.empty:
        return

    fig, ax = plt.subplots(figsize=(10.5, 4.8))
    ax.plot(plot_frame["r_R"], plot_frame[field], color="black", linewidth=2.0)
    ax.axhline(0.0, color="0.55", linewidth=0.8)
    ax.set_title(title)
    ax.set_xlabel("r/R [-]")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.35)
    ax.set_xlim(float(plot_frame["r_R"].min()), float(plot_frame["r_R"].max()))
    fig.tight_layout(pad=1.2)
    st.pyplot(fig, use_container_width=True)
    plt.close(fig)


def _sanity_check_table(results: dict[str, Any]) -> pd.DataFrame:
    summary = results.get("summary", {})
    stations = results.get("stations", [])
    frame = _coerce_numeric_frame(pd.DataFrame(stations)) if stations else pd.DataFrame()
    checks = dict(results.get("sanity_checks", {}))
    rows: list[dict[str, str]] = []

    def add_check(name: str, key: str, actual: Any, expected: Any, tolerance: float = 1.0e-8) -> None:
        rel_error = _relative_error(actual, expected)
        passed = checks.get(key)
        if passed is None:
            passed = rel_error is None or rel_error <= tolerance
        rows.append(
            {
                "check": name,
                "status": _pass_fail_na(passed),
                "actual": _format_general_number(actual),
                "expected": _format_general_number(expected),
                "relative_error": "" if rel_error is None else f"{rel_error:.3e}",
                "tolerance": f"{tolerance:.1e}",
            }
        )

    add_check("P = Q * omega", "P_equals_Q_omega", summary.get("power_W"), _safe_product(summary.get("torque_Nm"), summary.get("omega_rad_s")))
    add_check("CP = 2*pi*CQ", "CP_equals_2pi_CQ", summary.get("CP"), _safe_product(2.0 * math.pi, summary.get("CQ")))
    if not frame.empty:
        add_check("sum(dT) equals thrust", "sum_dT_equals_total_thrust", frame.get("dT_N", pd.Series(dtype=float)).sum(), summary.get("thrust_N"))
        add_check("sum(dQ) equals torque", "sum_dQ_equals_total_torque", frame.get("dQ_Nm", pd.Series(dtype=float)).sum(), summary.get("torque_Nm"))
        add_check(
            "qz integrates to thrust",
            "qz_integrates_to_thrust",
            _integrate_frame_fields(frame, "qz_N_per_m", "dr_m"),
            summary.get("thrust_N"),
        )
        add_check(
            "torque loading integrates to torque",
            "torque_loading_integrates_to_torque",
            _integrate_frame_fields(frame, "torque_loading_Nm_per_m", "dr_m"),
            summary.get("torque_Nm"),
        )

    hover_expected = "positive induced velocity" if checks.get("hover_mode_uses_induced_velocity_when_static") is not None else "N/A"
    rows.append(
        {
            "check": "hover mode uses induced velocity",
            "status": _pass_fail_na(checks.get("hover_mode_uses_induced_velocity_when_static")),
            "actual": _format_general_number(summary.get("induced_velocity_m_s")),
            "expected": hover_expected,
            "relative_error": "",
            "tolerance": "",
        }
    )
    rows.append(
        {
            "check": "no NaN/inf",
            "status": _pass_fail_na(checks.get("no_nan_or_inf")),
            "actual": _yes_no(bool(checks.get("no_nan_or_inf"))),
            "expected": "PASS",
            "relative_error": "",
            "tolerance": "",
        }
    )
    rows.append(
        {
            "check": "polar_is_placeholder false",
            "status": _pass_fail_na(not bool(results.get("polar_is_placeholder", summary.get("polar_is_placeholder")))),
            "actual": _yes_no(bool(results.get("polar_is_placeholder", summary.get("polar_is_placeholder")))),
            "expected": "No",
            "relative_error": "",
            "tolerance": "",
        }
    )
    return pd.DataFrame(rows)


def _display_aero_warning_flags(station_frame: pd.DataFrame, summary: dict[str, Any], config: dict[str, Any]) -> None:
    warnings = _aero_warning_rows(station_frame, summary, config)
    if not warnings:
        st.success("No UI warning flags detected in station-level aero outputs.")
        return
    frame = pd.DataFrame(warnings)
    fail_count = int((frame["status"] == "FAIL").sum())
    if fail_count:
        st.error(f"{fail_count} aero warning flag(s) exceed configured thresholds.")
    else:
        st.warning("Aero warning flags detected. Review before using plots in thesis figures.")
    st.dataframe(frame, use_container_width=True, hide_index=True)


def _aero_warning_rows(station_frame: pd.DataFrame, summary: dict[str, Any], config: dict[str, Any]) -> list[dict[str, str]]:
    if station_frame.empty:
        return []
    rows: list[dict[str, str]] = []
    count = max(len(station_frame), 1)
    negative_threshold = float(config.get("negative_loading_fail_fraction_pct", 10.0))
    extrap_threshold = float(config.get("polar_extrapolation_fail_fraction_pct", 20.0))
    tip_start = float(config.get("tip_region_r_R", 0.90))

    if "qz_N_per_m" in station_frame.columns:
        negative_mask = pd.to_numeric(station_frame["qz_N_per_m"], errors="coerce") < 0.0
        negative_fraction = 100.0 * float(negative_mask.sum()) / count
        if negative_mask.any():
            rows.append(
                {
                    "status": "FAIL" if negative_fraction > negative_threshold else "WARN",
                    "flag": "Negative thrust-loading regions",
                    "detail": f"{negative_fraction:.1f}% of stations have qz < 0.",
                    "guidance": "Review loading distribution and station interpolation.",
                }
            )
            tip_negative = negative_mask & (pd.to_numeric(station_frame.get("r_R", 0.0), errors="coerce") >= tip_start)
            if tip_negative.any():
                rows.append(
                    {
                        "status": "WARN",
                        "flag": "Tip loading reversal detected",
                        "detail": "Tip loading reversal detected.",
                        "guidance": (
                            "This may be expected for bell-spanloaded geometry but verify tip twist, "
                            "polar extrapolation, and station interpolation."
                        ),
                    }
                )

    tip_frame = station_frame[pd.to_numeric(station_frame.get("r_R", 0.0), errors="coerce") >= tip_start]
    if not tip_frame.empty:
        negative_cl = "Cl" in tip_frame.columns and (pd.to_numeric(tip_frame["Cl"], errors="coerce") < 0.0).any()
        negative_alpha = "alpha_deg" in tip_frame.columns and (pd.to_numeric(tip_frame["alpha_deg"], errors="coerce") < 0.0).any()
        if negative_cl or negative_alpha:
            rows.append(
                {
                    "status": "WARN",
                    "flag": "Negative Cl or alpha at tip",
                    "detail": f"negative Cl: {_yes_no(negative_cl)}, negative alpha: {_yes_no(negative_alpha)}",
                    "guidance": "Check tip twist and expected bell-spanload unloading.",
                }
            )

    alpha_extrap = pd.Series([False] * len(station_frame))
    if "alpha_clipped" in station_frame.columns:
        alpha_extrap = station_frame["alpha_clipped"].astype(str).str.lower().isin(("true", "1", "yes"))
    elif "polar_alpha_clipped" in station_frame.columns:
        alpha_extrap = station_frame["polar_alpha_clipped"].astype(str).str.lower().isin(("true", "1", "yes"))
    if alpha_extrap.any():
        extrap_fraction = 100.0 * float(alpha_extrap.sum()) / count
        rows.append(
            {
                "status": "FAIL" if extrap_fraction > extrap_threshold else "WARN",
                "flag": "Polar alpha extrapolation",
                "detail": f"{extrap_fraction:.1f}% of stations use polar endpoint values.",
                "guidance": "Extend or regenerate XFOIL polar range if this affects thesis conclusions.",
            }
        )

    re_min = summary.get("Re_min")
    re_max = summary.get("Re_max")
    required_min = min(REQUIRED_REYNOLDS)
    required_max = max(REQUIRED_REYNOLDS)
    if isinstance(re_min, (int, float)) and isinstance(re_max, (int, float)):
        if float(re_min) < required_min or float(re_max) > required_max:
            rows.append(
                {
                    "status": "WARN",
                    "flag": "Reynolds number outside generated polar range",
                    "detail": f"Re range {_format_general_number(re_min)} to {_format_general_number(re_max)}; polar grid {required_min} to {required_max}.",
                    "guidance": "Check whether endpoint Reynolds polar use is acceptable.",
                }
            )
    return rows


def _xrotor_comparison_table(comparison_path: Path, summary: dict[str, Any], config: dict[str, Any]) -> pd.DataFrame:
    raw = pd.read_csv(comparison_path)
    tolerance_pct = float(config.get("xrotor_tolerance_pct", 5.0))
    rows: list[dict[str, str]] = []
    raw_values: dict[str, tuple[float | None, float | None]] = {}
    for _, row in raw.iterrows():
        metric = str(row.get("metric", ""))
        bemt = _optional_float(row.get("bemt"))
        xrotor = _optional_float(row.get("xrotor"))
        raw_values[metric] = (bemt, xrotor)
        if metric == "efficiency" and _is_static_case(summary):
            continue
        rows.append(_comparison_row(metric, bemt, xrotor, tolerance_pct))

    thrust_bemt, thrust_xrotor = raw_values.get("thrust_N", (None, None))
    power_bemt, power_xrotor = raw_values.get("power_W", (None, None))
    if power_bemt and power_xrotor and thrust_bemt is not None and thrust_xrotor is not None:
        rows.append(
            _comparison_row(
                "thrust_per_power_N_per_W",
                thrust_bemt / power_bemt,
                thrust_xrotor / power_xrotor,
                tolerance_pct,
            )
        )
        rows.append(
            _comparison_row(
                "power_loading_N_per_kW",
                1000.0 * thrust_bemt / power_bemt,
                1000.0 * thrust_xrotor / power_xrotor,
                tolerance_pct,
            )
        )
    return pd.DataFrame(rows)


def _comparison_row(metric: str, bemt: float | None, xrotor: float | None, tolerance_pct: float) -> dict[str, str]:
    absolute_difference = None if bemt is None or xrotor is None else xrotor - bemt
    percent_difference = (
        None
        if absolute_difference is None or bemt is None or abs(bemt) <= 1.0e-12
        else absolute_difference / abs(bemt) * 100.0
    )
    status = "N/A" if percent_difference is None else ("PASS" if abs(percent_difference) <= tolerance_pct else "WARN")
    return {
        "metric": _metric_label(metric),
        "BEMT": _format_general_number(bemt),
        "XROTOR": _format_general_number(xrotor),
        "absolute_difference": _format_general_number(absolute_difference),
        "percent_difference": "" if percent_difference is None else f"{percent_difference:.2f}%",
        "status": status,
    }


def _polar_status_table(airfoil_dir: str | Path) -> pd.DataFrame:
    polars = load_airfoil_polars(airfoil_dir)
    rows = []
    for airfoil in REQUIRED_AIRFOILS:
        frame = polars.get(airfoil)
        available_re = []
        if frame is not None and not frame.empty:
            generated = frame[
                frame["polar_source"].astype(str).str.replace("\\", "/", regex=False).str.contains("generated_polars")
            ]
            available_re = sorted({int(round(float(value))) for value in generated["Re"].dropna().unique()})
        rows.append(
            {
                "airfoil": airfoil,
                "Re available": ", ".join(str(value) for value in available_re),
                "complete": all(reynolds in available_re for reynolds in REQUIRED_REYNOLDS),
            }
        )
    return pd.DataFrame(rows)


def _check_xfoil(xfoil_path: str, airfoil_dir: str) -> None:
    path = Path(xfoil_path)
    discovered = find_xfoil_executable(
        explicit_path=path if path.suffix.lower() == ".exe" else None,
        airfoil_dir=airfoil_dir,
        xfoil_root=path if path.suffix.lower() != ".exe" else None,
    )
    if discovered:
        st.sidebar.success(f"XFOIL found: {discovered}")
    else:
        st.sidebar.error("XFOIL executable not found.")


def _generate_xfoil_polars(xfoil_path: str, airfoil_dir: str) -> None:
    try:
        path = Path(xfoil_path)
        kwargs: dict[str, Any] = {"airfoil_dir": airfoil_dir}
        if path.suffix.lower() == ".exe":
            kwargs["xfoil_executable"] = path
        else:
            kwargs["xfoil_root"] = path
        with st.spinner("Generating XFOIL polar CSVs..."):
            results = generate_xfoil_polars(**kwargs)
        successful = sum(1 for result in results if result.success)
        st.sidebar.success(f"XFOIL polar generation complete: {successful}/{len(results)} cases succeeded.")
    except Exception:
        st.sidebar.error("XFOIL polar generation failed.")
        st.sidebar.code(traceback.format_exc())


def _check_xrotor(xrotor_path: str) -> None:
    executable = aero_solver.find_xrotor_executable(_xrotor_root(xrotor_path))
    if executable:
        st.sidebar.success(f"XROTOR found: {executable}")
    else:
        st.sidebar.error("XROTOR executable not found.")


def _xrotor_root(xrotor_path: str) -> Path:
    path = Path(xrotor_path)
    return path.parent if path.suffix.lower() == ".exe" else path


def _load_existing_results_silent(config: dict[str, Any]) -> dict[str, Any] | None:
    try:
        return aero_solver.load_aero_results(config["output_dir"], config["case_name"])
    except Exception:
        return None


def _save_config(config: dict[str, Any], config_path: str | Path) -> None:
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    st.success(f"Saved config: {path}")


def _load_config(config_path: str | Path) -> None:
    path = Path(config_path)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    st.session_state.config = loaded
    st.success(f"Loaded config: {path}")
    st.rerun()


def _open_output_folder(output_dir: str | Path) -> None:
    path = Path(output_dir).resolve()
    if not path.exists():
        st.warning(f"Output folder does not exist: {path}")
        return
    try:
        os.startfile(path)  # type: ignore[attr-defined]
        st.success(f"Opened {path}")
    except Exception:
        st.info(f"Open this folder manually: {path}")


def _result_status(summary: dict[str, Any]) -> str:
    if summary.get("target_matched") and summary.get("valid_for_thesis"):
        return "green"
    if summary.get("target_matched") and not summary.get("polar_is_placeholder"):
        return "yellow"
    return "red"


def _format_metric_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return _yes_no(value)
    if value is None:
        return ""
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not math.isfinite(numeric):
        return str(value)
    if key in {"thrust_N", "target_thrust_N", "thrust_error_pct", "max_alpha_deg", "min_alpha_deg", "collective_twist_offset_deg"}:
        return f"{numeric:.2f}"
    if key == "torque_Nm":
        return f"{numeric:.3f}"
    if key == "power_W":
        return f"{numeric:.1f}"
    if key == "CT":
        return f"{numeric:.4f}"
    if key == "CQ":
        return f"{numeric:.5f}"
    if key == "CP":
        return f"{numeric:.4f}"
    if key in {"max_Cl", "max_Cd"}:
        return f"{numeric:.3f}" if abs(numeric) >= 0.1 else f"{numeric:.4f}"
    if key in {"Re_min", "Re_max"}:
        return f"{numeric:,.0f}"
    return _format_general_number(numeric)


def _format_value(value: Any) -> str:
    if isinstance(value, bool):
        return _yes_no(value)
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return f"{float(value):.6g}"
        return str(value)
    if value is None:
        return ""
    return str(value)


def _clean_case_name(case_name: str) -> str:
    return "".join(character if character.isalnum() or character in ("-", "_") else "_" for character in case_name)


def _yes_no(value: bool) -> str:
    return "Yes" if bool(value) else "No"


def _pass_fail_na(value: Any) -> str:
    if value is None:
        return "N/A"
    return "PASS" if bool(value) else "FAIL"


def _safe_product(left: Any, right: Any) -> float | None:
    left_value = _optional_float(left)
    right_value = _optional_float(right)
    if left_value is None or right_value is None:
        return None
    return left_value * right_value


def _relative_error(actual: Any, expected: Any) -> float | None:
    actual_value = _optional_float(actual)
    expected_value = _optional_float(expected)
    if actual_value is None or expected_value is None:
        return None
    scale = max(abs(expected_value), 1.0e-12)
    return abs(actual_value - expected_value) / scale


def _integrate_frame_fields(frame: pd.DataFrame, value_field: str, width_field: str) -> float | None:
    if value_field not in frame.columns or width_field not in frame.columns:
        return None
    values = pd.to_numeric(frame[value_field], errors="coerce")
    widths = pd.to_numeric(frame[width_field], errors="coerce")
    valid = values.notna() & widths.notna()
    if not valid.any():
        return None
    return float((values[valid] * widths[valid]).sum())


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


def _format_general_number(value: Any) -> str:
    numeric = _optional_float(value)
    if numeric is None:
        return "" if value is None else str(value)
    if abs(numeric) >= 1000.0:
        return f"{numeric:,.0f}"
    if abs(numeric) >= 100.0:
        return f"{numeric:.1f}"
    if abs(numeric) >= 10.0:
        return f"{numeric:.2f}"
    if abs(numeric) >= 1.0:
        return f"{numeric:.3f}"
    if abs(numeric) >= 0.01:
        return f"{numeric:.4f}"
    return f"{numeric:.3e}"


def _metric_label(metric: str) -> str:
    labels = {
        "thrust_N": "Thrust [N]",
        "torque_Nm": "Torque [N*m]",
        "power_W": "Power [W]",
        "CT": "CT [-]",
        "CQ": "CQ [-]",
        "CP": "CP [-]",
        "efficiency": "Propulsive efficiency [-]",
        "thrust_per_power_N_per_W": "Thrust per power [N/W]",
        "power_loading_N_per_kW": "Power loading [N/kW]",
    }
    return labels.get(metric, metric)


def _is_static_case(summary: dict[str, Any]) -> bool:
    velocity = summary.get("xrotor_velocity_m_s")
    target_velocity = summary.get("V_inf_m_s")
    if isinstance(target_velocity, (int, float)):
        return abs(float(target_velocity)) <= 1.0e-9
    if isinstance(velocity, (int, float)):
        return abs(float(velocity)) <= 0.1000001
    return True


def _show_exception(title: str) -> None:
    st.error(title)
    with st.expander("Traceback"):
        st.code(traceback.format_exc())


def _running_under_streamlit() -> bool:
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        return get_script_run_ctx() is not None
    except Exception:
        return False


def _launch_with_streamlit() -> int:
    script_path = Path(__file__).resolve()
    print("Launching Streamlit UI. If a browser does not open, use the Local URL printed below.")
    completed = subprocess.run(
        [sys.executable, "-m", "streamlit", "run", str(script_path)],
        check=False,
    )
    return int(completed.returncode)


if __name__ == "__main__":
    if _running_under_streamlit():
        main()
    else:
        raise SystemExit(_launch_with_streamlit())
