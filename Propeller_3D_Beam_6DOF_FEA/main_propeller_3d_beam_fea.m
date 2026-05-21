%% main_propeller_3d_beam_fea
% Reduced-order 3D beam/frame FEA model for a propeller blade.
%
% Each radial station is a 6 DOF node:
%   ux, uy, uz, rx, ry, rz
% Each element is a 2-node, 12 DOF 3D Euler-Bernoulli frame element.
%
% This is a beam screening model, not a 3D solid stress model.

clear; clc; close all;

this_dir = fileparts(mfilename('fullpath'));
repo_root = fileparts(this_dir);
addpath(this_dir,'-begin');
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end
applyPlotStyle();

%% ------------------------- Input section ------------------------------

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

geometry_csv_path = default_geometry;
output_folder = fullfile('geometry','reference','reference_gmt');
output_name = 'reference_gmt';

diameter_in = 22;
radius_m = (diameter_in/2)*0.0254;
RPM = 4000;

total_thrust_per_propeller_N = 48;
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
num_nodes = [];                   % [] uses the CSV stations directly

load_distribution_type = 'bell';  % 'uniform', 'elliptical', 'bell', or 'csv'
include_edgewise_load = false;
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
include_aero_torsion_from_offset = true;

%% ------------------------- Case setup ---------------------------------

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

params = struct();
params.geometry_csv_path = geometry_csv_path;
params.output_name = output_name;
params.radius_m = radius_m;
params.RPM = RPM;
params.total_thrust_per_blade_N = total_thrust_per_blade_N;
params.total_thrust_per_propeller_N = total_thrust_per_propeller_N;
params.number_of_blades = number_of_blades;
params.thickness_ratio = thickness_ratio;
params.num_nodes = num_nodes;
params.load_distribution_type = load_distribution_type;
params.include_edgewise_load = include_edgewise_load;
params.include_torsional_moment = include_torsional_moment;
params.deflection_scale_factor = deflection_scale_factor;
params.run_modal_analysis_enabled = run_modal_analysis_enabled;
params.number_of_modes = number_of_modes;
params.modal_normalization = modal_normalization;
params.modal_mass_matrix_type = modal_mass_matrix_type;
params.mode_shape_scale_factor = mode_shape_scale_factor;
params.include_centrifugal_stiffening = include_centrifugal_stiffening;
params.run_campbell_diagram_enabled = run_campbell_diagram_enabled;
params.campbell_max_RPM = campbell_max_RPM;
params.campbell_num_points = campbell_num_points;
params.campbell_warning_tolerance = campbell_warning_tolerance;
params.elastic_axis_fraction_chord = elastic_axis_fraction_chord;
params.aero_center_fraction_chord = aero_center_fraction_chord;
params.include_aero_torsion_from_offset = include_aero_torsion_from_offset;
params.scale_csv_thrust_to_total = true;
params.thrust_distribution_csv_path = '';

results_dir = prepareResultsDirectory(this_dir,output_folder,true);

%% ------------------------- FEA workflow -------------------------------

geom = read_prop_geometry(geometry_csv_path,radius_m);
mesh = build_3d_beam_mesh(geom,radius_m,num_nodes);
props = section_properties_from_geometry(mesh,material,thickness_ratio);

[K,element_data,dof] = assemble_global_stiffness(mesh,props,material);
[F,loads,element_data] = apply_propeller_loads_3d(mesh,props,material,params,geom,element_data,dof);

bc = apply_boundary_conditions(mesh,dof,1);
solution = solve_static_fea(K,zeros(size(F)),bc,dof,loads.F0_global);
solution.applied_equivalent_load_global = F;

forces = recover_internal_forces_3d(mesh,props,material,element_data,solution,loads,dof);
stress = recover_beam_stresses(mesh,props,forces);

modal = [];
modal_paths = [];
if run_modal_analysis_enabled
    mass_options = struct('mass_matrix_type',modal_mass_matrix_type, ...
        'include_rotary_inertia',true);
    [M,element_mass_data] = assembleGlobalMassMatrix(mesh,props,element_data,dof,mass_options); %#ok<NASGU>
    K_modal = K;
    Kg = zeros(size(K));
    if include_centrifugal_stiffening
        Kg = assembleGeometricStiffnessMatrix(mesh,forces,element_data,dof);
        K_modal = K + Kg;
    end
    modal_options = struct('normalization',modal_normalization);
    modal = solveModalEigenproblem(K_modal,M,bc,number_of_modes,mesh,dof,modal_options);
    modal.mass_matrix_type = modal_mass_matrix_type;
    modal.include_centrifugal_stiffening = include_centrifugal_stiffening;
    modal.centrifugal_stiffening_note = 'Geometric stiffness from centrifugal axial force only; gyroscopic effects neglected.';
    modal.excitation = computeRotorExcitationFrequencies(RPM,number_of_blades,modal.frequencies_hz,0.10);
    modal.warnings = [modal.warnings(:); modal.excitation.warnings(:)];
    modal_paths = exportModalResultsCSV(modal,mesh,dof,results_dir,output_name);
    for mode_id = 1:min(number_of_modes,numel(modal.frequencies_hz))
        fig = plotModeShape3D(mesh,modal,mode_id,mode_shape_scale_factor,'3D',results_dir,output_name);
        close(fig);
    end
    if run_campbell_diagram_enabled
        params.campbell_modal_frequencies_hz = modal.frequencies_hz;
        plotCampbellDiagram(K,M,Kg,bc,mesh,dof,params,results_dir,output_name);
    end
end

plot_files = plot_3d_beam_results(mesh,props,loads,solution,forces,stress,params,results_dir,output_name);
results_csv = export_results_table(mesh,props,solution,forces,stress,results_dir,output_name);

fprintf('\nReduced-order 3D beam propeller FEA complete.\n');
fprintf('Geometry: %s\n',geometry_csv_path);
fprintf('Results directory: %s\n',results_dir);
fprintf('Results CSV: %s\n',results_csv);
fprintf('Plots written: %d\n',numel(plot_files));
fprintf('Max normal stress envelope: %.3f MPa\n',max(abs(stress.sigma_combined_Pa))/1e6);
fprintf('Tip displacement magnitude: %.6g m\n',norm(solution.displacements(end,1:3)));
if ~isempty(modal)
    fprintf('\nModal analysis complete (%s mass matrix).\n',modal_mass_matrix_type);
    for i = 1:numel(modal.frequencies_hz)
        fprintf('  Mode %d: %.4f Hz, %s\n',i,modal.frequencies_hz(i),modal.modeType{i});
    end
    fprintf('Modal frequencies CSV: %s\n',modal_paths.frequency_csv);
    fprintf('Mode shapes CSV: %s\n',modal_paths.mode_shapes_csv);
    for i = 1:numel(modal.excitation.warnings)
        fprintf('%s\n',modal.excitation.warnings{i});
    end
end
