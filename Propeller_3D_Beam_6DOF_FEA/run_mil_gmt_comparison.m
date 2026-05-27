%% run_mil_gmt_comparison
% Runs both reference and optimized MIL-vs-GMT structural comparisons.
%
% Reference comparison:
%   22-inch NASA/SciTech reference MIL geometry vs reference GMT geometry.
%
% Optimized comparison:
%   optimized MIL geometry vs optimized GMT geometry.

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

%% ------------------------- Input section ------------------------------

reference_mil_geometry = require_geometry({
    fullfile(repo_root,'Reference_Propeller_Geometry.xlsx')
    fullfile(repo_root,'reference_propeller_geometry.xlsx')
    fullfile(repo_root,'reference_mil.csv')
    fullfile(repo_root,'reference_mil_geometry.csv')
    fullfile(this_dir,'examples','example_22in_MIL_geometry.csv')
    },'22-inch reference MIL geometry');

reference_gmt_geometry = require_geometry({
    fullfile(repo_root,'Reference_Propeller_Geometry.xlsx')
    fullfile(repo_root,'reference_propeller_geometry.xlsx')
    fullfile(repo_root,'reference_gmt.csv')
    fullfile(repo_root,'reference_gmt_geometry.csv')
    fullfile(this_dir,'examples','example_22in_gmt_geometry.csv')
    },'22-inch NASA/SciTech reference GMT geometry');

optimized_mil_geometry = require_geometry({
    fullfile(project_root,'results','geometry_optimizer','optimized_mil','optimized_mil_optimizer_geometry.csv')
    fullfile(project_root,'results','geometry_optimizer','optimized_mil','optimized_mil.csv')
    fullfile(repo_root,'outputs','optimized_mil','optimized_mil.csv')
    fullfile(repo_root,'optimized_mil.csv')
    fullfile(repo_root,'outputs','test_mil','optimized_mil.csv')
    },'optimized MIL geometry');

optimized_gmt_geometry = require_geometry({
    fullfile(project_root,'results','geometry_optimizer','optimized_gmt','optimized_gmt_optimizer_geometry.csv')
    fullfile(project_root,'results','geometry_optimizer','optimized_gmt','optimized_gmt.csv')
    fullfile(repo_root,'outputs','optimized_gmt','optimized_gmt.csv')
    fullfile(repo_root,'optimized_gmt.csv')
    fullfile(repo_root,'outputs','test_thesis_cal','optimized_gmt.csv')
    fullfile(this_dir,'examples','example_22in_gmt_geometry.csv')
    },'optimized GMT geometry');

radius_m = 0.2794;
RPM = 4000;
total_thrust_per_propeller_N = 48;
number_of_blades = 2;
total_thrust_per_blade_N = total_thrust_per_propeller_N/number_of_blades;

material.E = 70e9;
material.nu = 0.33;
material.rho = 2700;
material.G = material.E/(2*(1 + material.nu));
material.type = 'aluminum';

params = struct();
params.radius_m = radius_m;
params.RPM = RPM;
params.total_thrust_per_blade_N = total_thrust_per_blade_N;
params.total_thrust_per_propeller_N = total_thrust_per_propeller_N;
params.number_of_blades = number_of_blades;
params.thickness_ratio = 0.10;
params.num_nodes = 41;
params.load_source = 'legacy_uniform';
params.aero_station_csv_path = '';
params.aero_summary_json_path = '';
params.omega_override_rad_s = [];
params.load_distribution_type = 'bell';
params.include_edgewise_load = false;
params.include_torsional_moment = false;
params.deflection_scale_factor = 10;
params.run_modal_analysis_enabled = true;
params.number_of_modes = 6;
params.modal_normalization = 'max_displacement';
params.modal_mass_matrix_type = 'consistent';
params.mode_shape_scale_factor = 0.05;
params.include_centrifugal_stiffening = false;
params.run_campbell_diagram_enabled = true;
params.campbell_max_RPM = RPM;
params.campbell_num_points = 101;
params.campbell_warning_tolerance = 0.10;
params.elastic_axis_fraction_chord = 0.25;
params.aero_center_fraction_chord = 0.25;
params.include_aero_torsion_from_offset = true;
params.scale_csv_thrust_to_total = true;
params.thrust_distribution_csv_path = '';

%% ------------------------- Run comparisons ----------------------------

reference_summary = compare_pair('Reference Geometry', ...
    fullfile('comparisons','reference_mil_vs_gmt'), ...
    'reference_mil_vs_gmt',fullfile('comparisons','reference_mil_vs_gmt'), ...
    'Reference MIL',reference_mil_geometry,'mil_22', ...
    'Reference GMT',reference_gmt_geometry,'gmt_22', ...
    material,params,this_dir,project_root);

optimized_summary = compare_pair('Optimized Geometry', ...
    fullfile('comparisons','optimized_mil_vs_gmt'), ...
    'optimized_mil_vs_gmt',fullfile('comparisons','optimized_mil_vs_gmt'), ...
    'Optimized MIL',optimized_mil_geometry,'', ...
    'Optimized GMT',optimized_gmt_geometry,'', ...
    material,params,this_dir,project_root);

fprintf('\nAll requested MIL/GMT comparisons complete.\n');
disp(reference_summary.metrics);
disp(optimized_summary.metrics);

function summary = compare_pair(title_text,folder_name,output_prefix,figure_category,left_label,left_geometry,left_variant, ...
    right_label,right_geometry,right_variant,material,params,this_dir,project_root)

comparison_dir = prepareResultsDirectory(this_dir,folder_name,true);

fprintf('\nRunning %s\n',title_text);
fprintf('  %s geometry: %s\n',left_label,left_geometry);
fprintf('  %s geometry: %s\n',right_label,right_geometry);

left_id = slug_label(left_label);
right_id = slug_label(right_label);

left = run_case(left_id,left_label,left_geometry,left_variant,material,params,comparison_dir);
right = run_case(right_id,right_label,right_geometry,right_variant,material,params,comparison_dir);

case_name = {left_label; right_label};
geometry_path = {left_geometry; right_geometry};
geometry_variant = {variant_label(left_variant); variant_label(right_variant)};
max_combined_stress_MPa = [max(abs(left.stress.sigma_combined_Pa)); ...
                           max(abs(right.stress.sigma_combined_Pa))]/1e6;
max_flapwise_stress_MPa = [max(abs(left.stress.sigma_flap_Pa)); ...
                           max(abs(right.stress.sigma_flap_Pa))]/1e6;
max_axial_stress_MPa = [max(abs(left.stress.sigma_axial_Pa)); ...
                        max(abs(right.stress.sigma_axial_Pa))]/1e6;
max_tip_deflection_m = [norm(left.solution.displacements(end,1:3)); ...
                        norm(right.solution.displacements(end,1:3))];
root_bending_moment_Nm = [hypot(left.forces.My_bending_Nm(1),left.forces.Mz_bending_Nm(1)); ...
                          hypot(right.forces.My_bending_Nm(1),right.forces.Mz_bending_Nm(1))];
root_axial_force_N = [abs(left.forces.N_N(1)); abs(right.forces.N_N(1))];
estimated_mass_kg = [trapz(left.mesh.radius_m,left.props.mass_per_length_kg_m); ...
                     trapz(right.mesh.radius_m,right.props.mass_per_length_kg_m)];
first_frequency_Hz = [left.modal.frequencies_hz(1); right.modal.frequencies_hz(1)];

comparison_table = table(case_name,geometry_path,geometry_variant,max_combined_stress_MPa, ...
    max_flapwise_stress_MPa,max_axial_stress_MPa,max_tip_deflection_m, ...
    root_bending_moment_Nm,root_axial_force_N,estimated_mass_kg,first_frequency_Hz);

comparison_csv = fullfile(comparison_dir,[output_prefix '_summary_metrics.csv']);
writetable(comparison_table,comparison_csv);

common_mu = left.mesh.mu(:);
left_combined = left.stress.sigma_combined_Pa(:)/1e6;
right_combined = interp1(right.mesh.mu(:),right.stress.sigma_combined_Pa(:)/1e6, ...
    common_mu,'linear','extrap');
difference = right_combined - left_combined;
percent_difference = 100*difference./left_combined;
percent_difference(abs(left_combined) < eps) = NaN;

paired_csv = fullfile(comparison_dir,[output_prefix '_paired_stress_data.csv']);
write_paired_stress_csv(paired_csv,common_mu,left_combined,right_combined,difference,left_label,right_label);

fig = figure('Visible','off','Color','w','Units','inches','Position',[1 1 6.5 4.0]);
tl = tiledlayout(fig,2,1,'TileSpacing','compact','Padding','compact');
ax1 = nexttile(tl);
plot(ax1,common_mu,left_combined,'-o','Color',[0.000 0.447 0.741], ...
    'MarkerFaceColor','w','LineWidth',1.5,'MarkerSize',4); hold(ax1,'on');
plot(ax1,common_mu,right_combined,'--s','Color',[0.850 0.325 0.098], ...
    'MarkerFaceColor','w','LineWidth',1.5,'MarkerSize',4);
grid(ax1,'on'); ylabel(ax1,'Stress [MPa]');
legend(ax1,cleanComparisonLabel(left_label),cleanComparisonLabel(right_label), ...
    'Location','best','Box','off');
title(ax1,sprintf('%s Stress Distribution',title_text));

ax2 = nexttile(tl);
plot(ax2,common_mu,percent_difference,'-o','Color',[0.20 0.20 0.20], ...
    'MarkerFaceColor','w','LineWidth',1.3,'MarkerSize',4); hold(ax2,'on');
yline(ax2,0,'k:','LineWidth',0.8);
grid(ax2,'on'); xlabel(ax2,'r/R [-]'); ylabel(ax2,'\Delta [%]');
max_percent_difference = max(abs(percent_difference),[],'omitnan');
text(ax2,0.02,0.88,sprintf('max |\\Delta| = %.3g%%',max_percent_difference), ...
    'Units','normalized','FontSize',11,'Interpreter','tex');
linkaxes([ax1 ax2],'x');
xlim(ax1,[min(common_mu) max(common_mu)]);
export_figure_set(fig,comparison_dir,[output_prefix '_stress_distribution']);
export_figure_set(fig,fullfile(project_root,'figures_paper','fea',figure_category),[output_prefix '_stress_distribution']);
close(fig);

mode_number = (1:min(numel(left.modal.frequencies_hz),numel(right.modal.frequencies_hz)))';
left_frequency_Hz = left.modal.frequencies_hz(mode_number);
right_frequency_Hz = right.modal.frequencies_hz(mode_number);
percent_difference = 100*(right_frequency_Hz - left_frequency_Hz)./left_frequency_Hz;
left_mode_type = string(left.modal.modeType(mode_number));
right_mode_type = string(right.modal.modeType(mode_number));
modal_comparison_table = table(mode_number,left_frequency_Hz,right_frequency_Hz, ...
    percent_difference,left_mode_type,right_mode_type);
modal_comparison_csv = fullfile(comparison_dir,[output_prefix '_modal_frequency_comparison.csv']);
writetable(modal_comparison_table,modal_comparison_csv);

fig = figure('Visible','off','Color','w');
bar(mode_number,[left_frequency_Hz(:), right_frequency_Hz(:)]);
grid on; xlabel('Mode number [-]'); ylabel('Frequency [Hz]');
legend(cleanComparisonLabel(left_label),cleanComparisonLabel(right_label),'Location','best');
title(sprintf('%s Natural Frequencies',title_text));
export_figure_set(fig,comparison_dir,[output_prefix '_modal_frequency']);
export_figure_set(fig,fullfile(project_root,'figures_paper','fea',figure_category),[output_prefix '_modal_frequency']);
close(fig);

fprintf('\n%s complete.\n',title_text);
fprintf('Comparison directory: %s\n',comparison_dir);
fprintf('Summary metrics: %s\n',comparison_csv);
fprintf('Paired stress data: %s\n',paired_csv);
fprintf('Modal comparison: %s\n',modal_comparison_csv);
disp(comparison_table);
disp(modal_comparison_table);

summary = struct();
summary.metrics = comparison_table;
summary.modal = modal_comparison_table;
summary.directory = comparison_dir;
summary.left = left;
summary.right = right;

end

function out = run_case(case_id,label,geometry_csv,geometry_variant,material,params,comparison_dir)

case_params = params;
case_params.output_name = case_id;
case_params.geometry_csv_path = geometry_csv;
case_params.case_label = label;

case_dir = fullfile(comparison_dir,case_id);
if ~exist(case_dir,'dir')
    mkdir(case_dir);
end

geom = read_prop_geometry(geometry_csv,params.radius_m,geometry_variant);
mesh = build_3d_beam_mesh(geom,params.radius_m,params.num_nodes);
props = section_properties_from_geometry(mesh,material,params.thickness_ratio);

[K,element_data,dof] = assemble_global_stiffness(mesh,props,material);
[F,loads,element_data] = apply_propeller_loads_3d(mesh,props,material,case_params,geom,element_data,dof);
bc = apply_boundary_conditions(mesh,dof,1);
solution = solve_static_fea(K,zeros(size(F)),bc,dof,loads.F0_global);
solution.applied_equivalent_load_global = F;
forces = recover_internal_forces_3d(mesh,props,material,element_data,solution,loads,dof);
stress = recover_beam_stresses(mesh,props,forces);

modal = [];
modal_paths = [];
if isfield(params,'run_modal_analysis_enabled') && params.run_modal_analysis_enabled
    mass_options = struct('mass_matrix_type',params.modal_mass_matrix_type, ...
        'include_rotary_inertia',true);
    M = assembleGlobalMassMatrix(mesh,props,element_data,dof,mass_options);
    K_modal = K;
    Kg = zeros(size(K));
    if isfield(params,'include_centrifugal_stiffening') && params.include_centrifugal_stiffening
        Kg = assembleGeometricStiffnessMatrix(mesh,forces,element_data,dof);
        K_modal = K + Kg;
    end
    modal_options = struct('normalization',params.modal_normalization);
    modal = solveModalEigenproblem(K_modal,M,bc,params.number_of_modes,mesh,dof,modal_options);
    modal.mass_matrix_type = params.modal_mass_matrix_type;
    modal.include_centrifugal_stiffening = params.include_centrifugal_stiffening;
    modal.excitation = computeRotorExcitationFrequencies(params.RPM,params.number_of_blades,modal.frequencies_hz,0.10);
    modal.warnings = [modal.warnings(:); modal.excitation.warnings(:)];
    modal.case_label = label;
    modal_paths = exportModalResultsCSV(modal,mesh,dof,case_dir,case_id);
    for mode_id = 1:min(params.number_of_modes,numel(modal.frequencies_hz))
        fig = plotModeShape3D(mesh,modal,mode_id,params.mode_shape_scale_factor,'3D',case_dir,case_id);
        close(fig);
    end
    if isfield(params,'run_campbell_diagram_enabled') && params.run_campbell_diagram_enabled
        case_params.campbell_modal_frequencies_hz = modal.frequencies_hz;
        plotCampbellDiagram(K,M,Kg,bc,mesh,dof,case_params,case_dir,case_id);
    end
end

plot_3d_beam_results(mesh,props,loads,solution,forces,stress,case_params,case_dir,case_id);
export_results_table(mesh,props,solution,forces,stress,case_dir,case_id);

out = struct();
out.geom = geom;
out.mesh = mesh;
out.props = props;
out.K = K;
out.F = F;
out.loads = loads;
out.solution = solution;
out.forces = forces;
out.stress = stress;
out.modal = modal;
out.modal_paths = modal_paths;

end

function write_paired_stress_csv(csv_path,mu,left_combined,right_combined,difference,left_label,right_label)

fid = fopen(csv_path,'w');
if fid < 0
    error('Could not open paired stress CSV for writing: %s',csv_path);
end
cleanup = onCleanup(@() fclose(fid));

left_header = regexprep(char(string(left_label)),'[^a-zA-Z0-9]+','_');
right_header = regexprep(char(string(right_label)),'[^a-zA-Z0-9]+','_');
left_header = regexprep(left_header,'(^_+|_+$)','');
right_header = regexprep(right_header,'(^_+|_+$)','');
fprintf(fid,'r/R,%s_combined_stress_MPa,%s_combined_stress_MPa,difference_MPa\n',left_header,right_header);
for i = 1:numel(mu)
    fprintf(fid,'%.12g,%.12g,%.12g,%.12g\n',mu(i),left_combined(i),right_combined(i),difference(i));
end

end

function label = cleanComparisonLabel(raw_label)

raw = lower(char(string(raw_label)));
if contains(raw,'gmt')
    label = 'GMT';
elseif contains(raw,'mil')
    label = 'MIL';
elseif contains(raw,'abaqus')
    label = 'Abaqus';
else
    label = char(string(raw_label));
end

end

function export_figure_set(fig,out_dir,stem)

if ~exist(out_dir,'dir')
    mkdir(out_dir);
end

exportgraphics(fig,fullfile(out_dir,[stem '.svg']),'ContentType','vector');
exportgraphics(fig,fullfile(out_dir,[stem '.png']),'Resolution',300);

end

function path = require_geometry(paths,description)

path = first_existing(paths);
if isempty(path)
    message = sprintf('Could not find %s. Checked:',description);
    for i = 1:numel(paths)
        message = sprintf('%s\n  %s',message,paths{i});
    end
    error('%s',message);
end

end

function path = first_existing(paths)

path = '';
for i = 1:numel(paths)
    candidate = paths{i};
    if isfile(candidate)
        path = candidate;
        return
    end
end

end

function slug = slug_label(label)

slug = lower(regexprep(char(string(label)),'[^a-zA-Z0-9]+','_'));
slug = regexprep(slug,'(^_+|_+$)','');

end

function label = variant_label(variant)

if isempty(variant)
    label = '';
else
    label = char(string(variant));
end

end
