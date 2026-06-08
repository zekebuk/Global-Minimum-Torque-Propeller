%% main_propeller_3d_beam_fea
% Reduced-order 3D beam/frame FEA model for the final locked MIL/GMT
% propeller cases.
%
% Each radial station is a 6 DOF node:
%   ux, uy, uz, rx, ry, rz
% Each element is a 2-node, 12 DOF 3D Euler-Bernoulli frame element.
%
% This is a beam screening model, not a 3D solid stress model.

clear; clc; close all;

this_dir = fileparts(mfilename('fullpath'));
repo_root = fileparts(this_dir);
[project_root,code_folder] = fileparts(repo_root);
if ~strcmpi(code_folder,'Code')
    project_root = repo_root;
end
addpath(this_dir,'-begin');
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end
applyPlotStyle();

%% ------------------------- Locked input section -----------------------

final_geometry_dir = fullfile(project_root,'results','FINAL_THESIS_USE_THESE','01_geometry');
final_aero_dir = fullfile(project_root,'results','FINAL_THESIS_USE_THESE','02_aero_validation');
locked_aero_summary_json = fullfile(final_aero_dir,'locked_aero_summary.json');

fea_cases = struct( ...
    'label',{'MIL','GMT'}, ...
    'output_name',{'final_locked_mil','final_locked_gmt'}, ...
    'geometry_csv_path',{ ...
        fullfile(final_geometry_dir,'final_mil_geometry.csv'), ...
        fullfile(final_geometry_dir,'final_gmt_geometry.csv')}, ...
    'aero_station_csv_path',{ ...
        fullfile(final_aero_dir,'mil_bemt_validation_LOCKED.csv'), ...
        fullfile(final_aero_dir,'gmt_bemt_validation_LOCKED.csv')});

for case_index = 1:numel(fea_cases)
    require_file(fea_cases(case_index).geometry_csv_path,'locked geometry CSV');
    require_file(fea_cases(case_index).aero_station_csv_path,'locked aero station CSV');
end
require_file(locked_aero_summary_json,'locked aero summary JSON');

diameter_in = 22;
radius_m = (diameter_in/2)*0.0254;
RPM = 4000;
total_thrust_per_propeller_N = 35;
number_of_blades = 2;
total_thrust_per_blade_N = total_thrust_per_propeller_N/number_of_blades;

material_type = 'aluminum';       % 'aluminum' or 'forged_carbon_effective'

E = 70e9;                         % Pa
nu = 0.33;
rho = 2700;                       % kg/m^3

E_eff = 45e9;                     % Pa, placeholder effective forged carbon value
nu_eff = 0.28;
rho_eff = 1550;                   % kg/m^3

thickness_ratio = 0.10;
num_nodes = [];                   % [] uses the locked CSV stations directly

load_source = 'aero_solver_csv';
load_distribution_type = 'csv';
include_edgewise_load = true;
include_torsional_moment = false;
deflection_scale_factor = 10;

run_modal_analysis_enabled = true;
number_of_modes = 6;
modal_normalization = 'max_displacement';  % 'max_displacement' or 'mass'
modal_mass_matrix_type = 'consistent';     % 'consistent' or 'lumped'
mode_shape_scale_factor = 0.05;            % m for max-displacement-normalized modes
include_centrifugal_stiffening = false;
run_campbell_diagram_enabled = true;
campbell_max_RPM = RPM;
campbell_num_points = 101;
campbell_warning_tolerance = 0.10;

% Load-line geometry. With no measured spar/shear-center data, the safest
% default is to put the elastic axis and aerodynamic center at quarter chord.
% Change these to create distributed torsion from thrust load offset.
elastic_axis_fraction_chord = 0.25;
aero_center_fraction_chord = 0.25;
include_aero_torsion_from_offset = false;

%% ------------------------- Common case setup --------------------------

if strcmpi(material_type,'forged_carbon_effective')
    material.E = E_eff;
    material.nu = nu_eff;
    material.rho = rho_eff;
else
    material.E = E;
    material.nu = nu;
    material.rho = rho;
end
material.G = material.E/(2*(1 + material.nu));
material.type = material_type;

common_params = struct();
common_params.radius_m = radius_m;
common_params.RPM = RPM;
common_params.total_thrust_per_blade_N = total_thrust_per_blade_N;
common_params.total_thrust_per_propeller_N = total_thrust_per_propeller_N;
common_params.number_of_blades = number_of_blades;
common_params.thickness_ratio = thickness_ratio;
common_params.num_nodes = num_nodes;
common_params.load_source = load_source;
common_params.aero_summary_json_path = locked_aero_summary_json;
common_params.omega_override_rad_s = [];
common_params.load_distribution_type = load_distribution_type;
common_params.include_edgewise_load = include_edgewise_load;
common_params.include_torsional_moment = include_torsional_moment;
common_params.deflection_scale_factor = deflection_scale_factor;
common_params.run_modal_analysis_enabled = run_modal_analysis_enabled;
common_params.number_of_modes = number_of_modes;
common_params.modal_normalization = modal_normalization;
common_params.modal_mass_matrix_type = modal_mass_matrix_type;
common_params.mode_shape_scale_factor = mode_shape_scale_factor;
common_params.include_centrifugal_stiffening = include_centrifugal_stiffening;
common_params.run_campbell_diagram_enabled = run_campbell_diagram_enabled;
common_params.campbell_max_RPM = campbell_max_RPM;
common_params.campbell_num_points = campbell_num_points;
common_params.campbell_warning_tolerance = campbell_warning_tolerance;
common_params.elastic_axis_fraction_chord = elastic_axis_fraction_chord;
common_params.aero_center_fraction_chord = aero_center_fraction_chord;
common_params.include_aero_torsion_from_offset = include_aero_torsion_from_offset;
common_params.scale_csv_thrust_to_total = false;
common_params.thrust_distribution_csv_path = '';

base_output_folder = fullfile('beam_solver','final_locked_mil_gmt');
case_summaries = cell(numel(fea_cases),1);

%% ------------------------- FEA workflow -------------------------------

for case_index = 1:numel(fea_cases)
    case_summaries{case_index} = run_locked_case(fea_cases(case_index), ...
        common_params,material,this_dir,project_root,base_output_folder);
end

summary_dir = fullfile(project_root,'results','fea','beam_solver','final_locked_mil_gmt');
if ~exist(summary_dir,'dir')
    mkdir(summary_dir);
end
summary_csv = fullfile(summary_dir,'final_locked_mil_gmt_fea_summary.csv');
summary_table = case_summary_table([case_summaries{:}]);
writetable(summary_table,summary_csv);

fprintf('\nFinal locked MIL/GMT beam FEA complete.\n');
fprintf('Summary CSV: %s\n',summary_csv);
disp(summary_table);

%% ------------------------- Local functions ----------------------------

function summary = run_locked_case(case_cfg,common_params,material,this_dir,project_root,base_output_folder)

params = common_params;
params.geometry_csv_path = case_cfg.geometry_csv_path;
params.aero_station_csv_path = case_cfg.aero_station_csv_path;
params.output_name = case_cfg.output_name;
params.case_label = case_cfg.label;

results_dir = prepareResultsDirectory(this_dir, ...
    fullfile(base_output_folder,case_cfg.output_name),true);

fprintf('\nRunning final locked %s beam FEA\n',case_cfg.label);
fprintf('Geometry: %s\n',case_cfg.geometry_csv_path);
fprintf('Aero loads: %s\n',case_cfg.aero_station_csv_path);
fprintf('Aero summary: %s\n',params.aero_summary_json_path);

geom = read_prop_geometry(case_cfg.geometry_csv_path,params.radius_m);
mesh = build_3d_beam_mesh(geom,params.radius_m,params.num_nodes);
props = section_properties_from_geometry(mesh,material,params.thickness_ratio);

[K,element_data,dof] = assemble_global_stiffness(mesh,props,material);
[F,loads,element_data] = apply_propeller_loads_3d(mesh,props,material,params,geom,element_data,dof);
load_report_paths = write_fea_load_consistency_report(loads,params,results_dir,case_cfg.output_name);
load_plot_files = plot_fea_imported_loads(mesh,loads,params,this_dir,case_cfg.output_name);

bc = apply_boundary_conditions(mesh,dof,1);
solution = solve_static_fea(K,zeros(size(F)),bc,dof,loads.F0_global);
solution.applied_equivalent_load_global = F;

forces = recover_internal_forces_3d(mesh,props,material,element_data,solution,loads,dof);
stress = recover_beam_stresses(mesh,props,forces);

modal = [];
modal_paths = [];
modal_audit_paths = [];
if params.run_modal_analysis_enabled
    mass_options = struct('mass_matrix_type',params.modal_mass_matrix_type, ...
        'include_rotary_inertia',true);
    [M,element_mass_data] = assembleGlobalMassMatrix(mesh,props,element_data,dof,mass_options);
    K_modal = K;
    Kg = zeros(size(K));
    if params.include_centrifugal_stiffening
        Kg = assembleGeometricStiffnessMatrix(mesh,forces,element_data,dof);
        K_modal = K + Kg;
    end
    modal_options = struct('normalization',params.modal_normalization);
    modal = solveModalEigenproblem(K_modal,M,bc,params.number_of_modes,mesh,dof,modal_options);
    modal.mass_matrix_type = params.modal_mass_matrix_type;
    modal.include_centrifugal_stiffening = params.include_centrifugal_stiffening;
    modal.centrifugal_stiffening_note = 'Geometric stiffness from centrifugal axial force only; gyroscopic effects neglected.';
    modal.excitation = computeRotorExcitationFrequencies(params.RPM,params.number_of_blades,modal.frequencies_hz,0.10);
    modal.warnings = [modal.warnings(:); modal.excitation.warnings(:)];
    modal_audit_paths = writeModalMatrixAudit(K_modal,M,bc,modal,mesh,props, ...
        element_mass_data,dof,params,fullfile(project_root,'results','fea','beam_solver'),K,Kg);
    modal_paths = exportModalResultsCSV(modal,mesh,dof,results_dir,case_cfg.output_name);
    for mode_id = 1:min(params.number_of_modes,numel(modal.frequencies_hz))
        fig = plotModeShape3D(mesh,modal,mode_id,params.mode_shape_scale_factor,'3D',results_dir,case_cfg.output_name);
        close(fig);
    end
    if params.run_campbell_diagram_enabled
        params.campbell_modal_frequencies_hz = modal.frequencies_hz;
        plotCampbellDiagram(K,M,Kg,bc,mesh,dof,params,results_dir,case_cfg.output_name);
    end
end

plot_files = plot_3d_beam_results(mesh,props,loads,solution,forces,stress,params,results_dir,case_cfg.output_name);
results_csv = export_results_table(mesh,props,solution,forces,stress,results_dir,case_cfg.output_name);

summary = struct();
summary.case_name = case_cfg.label;
summary.output_name = case_cfg.output_name;
summary.geometry_csv_path = case_cfg.geometry_csv_path;
summary.aero_station_csv_path = case_cfg.aero_station_csv_path;
summary.results_dir = results_dir;
summary.results_csv = results_csv;
summary.load_report_txt = load_report_paths.text;
summary.max_normal_stress_MPa = max(abs(stress.sigma_combined_Pa))/1e6;
summary.tip_displacement_m = norm(solution.displacements(end,1:3));
summary.integrated_thrust_N = loads.integrated_thrust_N;
summary.integrated_torque_from_qy_Nm = loads.integrated_torque_from_qy_Nm;
summary.integrated_torque_from_torque_loading_Nm = loads.integrated_torque_from_torque_loading_Nm;
summary.first_modal_frequency_Hz = first_modal_frequency(modal);
summary.plot_count = numel(plot_files);
summary.imported_load_plot_count = numel(load_plot_files);

fprintf('\n%s reduced-order 3D beam propeller FEA complete.\n',case_cfg.label);
fprintf('Results directory: %s\n',results_dir);
fprintf('Results CSV: %s\n',results_csv);
fprintf('Plots written: %d\n',numel(plot_files));
fprintf('Load consistency report: %s\n',load_report_paths.text);
fprintf('Imported load plots written: %d\n',numel(load_plot_files));
fprintf('Max normal stress envelope: %.3f MPa\n',summary.max_normal_stress_MPa);
fprintf('Tip displacement magnitude: %.6g m\n',summary.tip_displacement_m);
if ~isempty(modal)
    fprintf('\nModal analysis complete (%s mass matrix).\n',params.modal_mass_matrix_type);
    for i = 1:numel(modal.frequencies_hz)
        fprintf('  Mode %d: %.4f Hz, %s\n',i,modal.frequencies_hz(i),modal.modeType{i});
    end
    fprintf('Modal frequencies CSV: %s\n',modal_paths.frequency_csv);
    fprintf('Mode shapes CSV: %s\n',modal_paths.mode_shapes_csv);
    fprintf('Modal matrix audit: %s\n',modal_audit_paths.text);
    for i = 1:numel(modal.excitation.warnings)
        fprintf('%s\n',modal.excitation.warnings{i});
    end
end

end

function T = case_summary_table(case_summaries)

case_name = {case_summaries.case_name}';
output_name = {case_summaries.output_name}';
geometry_csv_path = {case_summaries.geometry_csv_path}';
aero_station_csv_path = {case_summaries.aero_station_csv_path}';
results_csv = {case_summaries.results_csv}';
max_normal_stress_MPa = [case_summaries.max_normal_stress_MPa]';
tip_displacement_m = [case_summaries.tip_displacement_m]';
integrated_thrust_N = [case_summaries.integrated_thrust_N]';
integrated_torque_from_qy_Nm = [case_summaries.integrated_torque_from_qy_Nm]';
integrated_torque_from_torque_loading_Nm = [case_summaries.integrated_torque_from_torque_loading_Nm]';
first_modal_frequency_Hz = [case_summaries.first_modal_frequency_Hz]';

T = table(case_name,output_name,geometry_csv_path,aero_station_csv_path, ...
    results_csv,max_normal_stress_MPa,tip_displacement_m,integrated_thrust_N, ...
    integrated_torque_from_qy_Nm,integrated_torque_from_torque_loading_Nm, ...
    first_modal_frequency_Hz);

end

function value = first_modal_frequency(modal)

if isstruct(modal) && isfield(modal,'frequencies_hz') && ~isempty(modal.frequencies_hz)
    value = modal.frequencies_hz(1);
else
    value = NaN;
end

end

function require_file(path,description)

if ~isfile(path)
    error('Missing %s: %s',description,path);
end

end
