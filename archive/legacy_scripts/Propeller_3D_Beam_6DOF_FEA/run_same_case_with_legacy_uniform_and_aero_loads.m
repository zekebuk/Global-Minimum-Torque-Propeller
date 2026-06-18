function comparison_csv = run_same_case_with_legacy_uniform_and_aero_loads()
%RUN_SAME_CASE_WITH_LEGACY_UNIFORM_AND_AERO_LOADS Compare FEA load sources.
%
% This is a structural comparison only. The aero solver remains the source
% of aerodynamic qz/qy/dQdr values used by the aero_solver_csv case.

clc; close all;

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

default_geometry = fullfile(repo_root,'Reference_Propeller_Geometry.xlsx');
if ~isfile(default_geometry)
    default_geometry = fullfile(repo_root,'reference_propeller_geometry.xlsx');
end
if ~isfile(default_geometry)
    default_geometry = fullfile(repo_root,'reference_gmt.csv');
end
if ~isfile(default_geometry)
    default_geometry = fullfile(this_dir,'examples','example_22in_gmt_geometry.csv');
end

aero_case_name = 'thesis_22_static_35N';

material = struct();
material.E = 70e9;
material.nu = 0.33;
material.rho = 2700;
material.G = material.E/(2*(1 + material.nu));
material.type = 'aluminum';

params = struct();
params.geometry_csv_path = default_geometry;
params.output_name = aero_case_name;
params.radius_m = 0.2794;
params.RPM = 4000;
params.total_thrust_per_propeller_N = 48;
params.number_of_blades = 2;
params.total_thrust_per_blade_N = params.total_thrust_per_propeller_N/params.number_of_blades;
params.thickness_ratio = 0.10;
params.num_nodes = [];
params.load_distribution_type = 'bell';
params.include_edgewise_load = false;
params.include_torsional_moment = false;
params.deflection_scale_factor = 10;
params.number_of_modes = 6;
params.modal_normalization = 'max_displacement';
params.modal_mass_matrix_type = 'consistent';
params.include_centrifugal_stiffening = false;
params.elastic_axis_fraction_chord = 0.25;
params.aero_center_fraction_chord = 0.25;
params.include_aero_torsion_from_offset = true;
params.scale_csv_thrust_to_total = true;
params.thrust_distribution_csv_path = '';
params.aero_station_csv_path = fullfile(project_root,'results','aero',[aero_case_name '_stations.csv']);
params.aero_summary_json_path = fullfile(project_root,'results','aero',[aero_case_name '_summary.json']);
params.omega_override_rad_s = [];

legacy_params = params;
legacy_params.load_source = 'legacy_uniform';
legacy_params.output_name = [aero_case_name '_legacy_uniform'];
legacy_params.case_label = 'Legacy uniform/bell load';

aero_params = params;
aero_params.load_source = 'aero_solver_csv';
aero_params.include_aero_torsion_from_offset = false;
aero_params.output_name = [aero_case_name '_aero_solver_csv'];
aero_params.case_label = 'Aero solver CSV load';

legacy = run_case(legacy_params,material,this_dir);
aero = run_case(aero_params,material,this_dir);

mode = {'legacy_uniform'; 'aero_solver_csv'};
max_stress_MPa = [max(abs(legacy.stress.sigma_combined_Pa)); max(abs(aero.stress.sigma_combined_Pa))]/1e6;
tip_displacement_m = [norm(legacy.solution.displacements(end,1:3)); norm(aero.solution.displacements(end,1:3))];
root_bending_moment_Nm = [hypot(legacy.forces.My_bending_Nm(1),legacy.forces.Mz_bending_Nm(1)); ...
    hypot(aero.forces.My_bending_Nm(1),aero.forces.Mz_bending_Nm(1))];
root_axial_force_N = [abs(legacy.forces.N_N(1)); abs(aero.forces.N_N(1))];
mode_1_frequency_Hz = [first_mode_frequency(legacy.modal); first_mode_frequency(aero.modal)];

comparison = table(mode,max_stress_MPa,tip_displacement_m,root_bending_moment_Nm, ...
    root_axial_force_N,mode_1_frequency_Hz);

results_root = fullfile(project_root,'results','fea');
if ~exist(results_root,'dir')
    mkdir(results_root);
end
comparison_csv = fullfile(results_root,[aero_case_name '_legacy_vs_aero_load_comparison.csv']);
writetable(comparison,comparison_csv);

fprintf('\nLegacy vs aero-loaded FEA comparison written:\n  %s\n',comparison_csv);
disp(comparison);

end

function out = run_case(params,material,this_dir)

case_dir = prepareResultsDirectory(this_dir,fullfile('load_source_comparison',params.output_name),true);

geom = read_prop_geometry(params.geometry_csv_path,params.radius_m);
mesh = build_3d_beam_mesh(geom,params.radius_m,params.num_nodes);
props = section_properties_from_geometry(mesh,material,params.thickness_ratio);

[K,element_data,dof] = assemble_global_stiffness(mesh,props,material);
[F,loads,element_data] = apply_propeller_loads_3d(mesh,props,material,params,geom,element_data,dof);
write_fea_load_consistency_report(loads,params,case_dir);
plot_fea_imported_loads(mesh,loads,params,this_dir);

bc = apply_boundary_conditions(mesh,dof,1);
solution = solve_static_fea(K,zeros(size(F)),bc,dof,loads.F0_global);
solution.applied_equivalent_load_global = F;

forces = recover_internal_forces_3d(mesh,props,material,element_data,solution,loads,dof);
stress = recover_beam_stresses(mesh,props,forces);

modal = [];
mass_options = struct('mass_matrix_type',params.modal_mass_matrix_type,'include_rotary_inertia',true);
M = assembleGlobalMassMatrix(mesh,props,element_data,dof,mass_options);
K_modal = K;
if isfield(params,'include_centrifugal_stiffening') && params.include_centrifugal_stiffening
    K_modal = K + assembleGeometricStiffnessMatrix(mesh,forces,element_data,dof);
end
modal_options = struct('normalization',params.modal_normalization);
modal = solveModalEigenproblem(K_modal,M,bc,params.number_of_modes,mesh,dof,modal_options);

plot_3d_beam_results(mesh,props,loads,solution,forces,stress,params,case_dir,params.output_name);
export_results_table(mesh,props,solution,forces,stress,case_dir,params.output_name);

out = struct();
out.mesh = mesh;
out.props = props;
out.loads = loads;
out.solution = solution;
out.forces = forces;
out.stress = stress;
out.modal = modal;

end

function value = first_mode_frequency(modal)

if isstruct(modal) && isfield(modal,'frequencies_hz') && ~isempty(modal.frequencies_hz)
    value = modal.frequencies_hz(1);
else
    value = NaN;
end

end
