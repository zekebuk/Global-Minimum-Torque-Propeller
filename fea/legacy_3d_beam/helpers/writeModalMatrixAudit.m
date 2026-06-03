function paths = writeModalMatrixAudit(K_modal,M,bc,modal,mesh,props,element_mass_data,dof,params,results_root,K_base,Kg)
%WRITEMODALMATRIXAUDIT Save modal stiffness/mass matrix validation reports.

if nargin < 10 || isempty(results_root)
    results_root = pwd;
end
if nargin < 11 || isempty(K_base)
    K_base = K_modal;
end
if nargin < 12 || isempty(Kg)
    Kg = zeros(size(K_modal));
end
if ~exist(results_root,'dir')
    mkdir(results_root);
end

tol = 1e-8;
near_zero_frequency_tolerance_hz = 1e-3;
limitation_statement = ['This modal analysis is a reduced-order beam screening model. ' ...
    'It may include centrifugal stiffening if enabled, but gyroscopic effects are neglected. ' ...
    'Campbell diagram results should therefore be interpreted as modal separation screening, ' ...
    'not full rotating-blade dynamics.'];

free = bc.free_dofs(:);
fixed = bc.fixed_dofs(:);
total_dofs = size(K_modal,1);
Kff = K_modal(free,free);
Mff = M(free,free);
Kff_sym = (Kff + Kff')/2;
Mff_sym = (Mff + Mff')/2;

warnings = {};
K_symmetry_error = relativeSymmetryError(K_modal);
M_symmetry_error = relativeSymmetryError(M);
if K_symmetry_error > tol
    warnings{end+1} = sprintf('K symmetry error %.3g exceeds %.3g.',K_symmetry_error,tol); %#ok<AGROW>
end
if M_symmetry_error > tol
    warnings{end+1} = sprintf('M symmetry error %.3g exceeds %.3g.',M_symmetry_error,tol); %#ok<AGROW>
end

[mass_positive_definite,mass_min_eigenvalue] = massPositiveDefinite(Mff_sym);
if ~mass_positive_definite
    warnings{end+1} = sprintf('Mff is not positive definite; min eigenvalue %.12g.',mass_min_eigenvalue); %#ok<AGROW>
end

mass_integrated = integratedBladeMass(mesh,props,element_mass_data);
[mass_implied,mass_implied_by_direction,mass_implied_spread_pct] = massFromMassMatrix(M,dof,mesh);
mass_percent_difference = percentDifference(mass_implied,mass_integrated);
if isfinite(mass_percent_difference) && abs(mass_percent_difference) > 1.0
    warnings{end+1} = sprintf('Mass implied by M differs from integrated mass by %.3g%%.',mass_percent_difference); %#ok<AGROW>
end

eigen_audit = auditEigenvalues(Kff_sym,Mff_sym,near_zero_frequency_tolerance_hz);
warnings = [warnings(:); eigen_audit.warnings(:)];

modal_freq = modal.frequencies_hz(:);
first_six_frequencies = modal_freq(1:min(6,numel(modal_freq)));
frequencies_finite = ~isempty(first_six_frequencies) && all(isfinite(first_six_frequencies));
frequencies_positive = ~isempty(first_six_frequencies) && all(first_six_frequencies > 0);
first_frequency_near_zero = ~isempty(first_six_frequencies) && ...
    first_six_frequencies(1) < near_zero_frequency_tolerance_hz;
if first_frequency_near_zero
    warnings{end+1} = sprintf('First modal frequency %.12g Hz is near zero.',first_six_frequencies(1)); %#ok<AGROW>
end

uses_reduced_free_dofs = size(Kff,1) == numel(free) && size(Mff,1) == numel(free) && ...
    numel(fixed) > 0 && numel(free) < total_dofs;
uses_full_matrices_with_constrained_dofs = ~uses_reduced_free_dofs;
if uses_full_matrices_with_constrained_dofs
    warnings{end+1} = 'Modal eigenproblem appears to use full K and M with constrained DOFs included.'; %#ok<AGROW>
end

include_centrifugal_stiffening = logicalField(params,'include_centrifugal_stiffening',false);
centrifugal = auditCentrifugalStiffening(K_base,Kg,M,bc,modal,mesh,dof,params,include_centrifugal_stiffening);
warnings = [warnings(:); centrifugal.warnings(:)];

checks = struct();
checks.matrix_sizes_match = isequal(size(K_modal),size(M)) && size(K_modal,1) == total_dofs;
checks.free_dof_eigenproblem = uses_reduced_free_dofs;
checks.K_symmetric = K_symmetry_error <= tol;
checks.M_symmetric = M_symmetry_error <= tol;
checks.Mff_positive_definite = mass_positive_definite;
checks.eigenvalues_real = eigen_audit.eigenvalues_real;
checks.eigenvalues_positive = eigen_audit.eigenvalues_positive;
checks.eigenvalues_finite = eigen_audit.eigenvalues_finite;
checks.frequencies_finite = frequencies_finite;
checks.frequencies_positive = frequencies_positive;
checks.no_nan_or_inf = frequencies_finite && eigen_audit.eigenvalues_finite;
checks.no_near_zero_rigid_body_modes = ~first_frequency_near_zero && eigen_audit.near_zero_mode_count == 0;
checks.centrifugal_frequency_stability = centrifugal.frequency_stability_pass;

pass = all(struct2arrayLogical(checks));
summary = 'PASS';
if ~pass
    summary = 'FAIL';
end

report = struct();
report.report_name = 'Modal Matrix Audit';
report.limitation_statement = limitation_statement;
report.K_size = size(K_modal);
report.M_size = size(M);
report.total_dofs = total_dofs;
report.constrained_dofs = numel(fixed);
report.free_dofs = numel(free);
report.fixed_dofs = fixed;
report.K_symmetry_error = K_symmetry_error;
report.M_symmetry_error = M_symmetry_error;
report.symmetry_tolerance = tol;
report.M_positive_definite_on_free_dofs = mass_positive_definite;
report.Mff_min_eigenvalue = mass_min_eigenvalue;
report.eigenproblem = 'Kff phi = omega_n^2 Mff phi';
report.uses_reduced_free_dofs = uses_reduced_free_dofs;
report.uses_full_matrices_with_constrained_dofs = uses_full_matrices_with_constrained_dofs;
report.first_6_natural_frequencies_Hz = first_six_frequencies;
report.modal_solver = fieldOrDefault(modal,'solver','');
report.modal_boundary_condition = fieldOrDefault(modal,'boundary_condition','');
report.eigenvalue_audit = eigen_audit;
report.mass_integrated_kg = mass_integrated;
report.mass_implied_by_M_kg = mass_implied;
report.mass_implied_by_direction_kg = mass_implied_by_direction;
report.mass_implied_direction_spread_percent = mass_implied_spread_pct;
report.mass_percent_difference = mass_percent_difference;
report.centrifugal_stiffening_enabled = include_centrifugal_stiffening;
report.centrifugal_stiffening = centrifugal;
report.gyroscopic_effects_included = false;
report.gyroscopic_effects_status = 'neglected';
report.checks = checks;
report.warnings = warnings;
report.summary = summary;

txt_path = fullfile(results_root,'modal_matrix_audit.txt');
json_path = fullfile(results_root,'modal_matrix_audit.json');
writeTextReport(txt_path,report);
writeJsonReport(json_path,report);

paths = struct();
paths.text = txt_path;
paths.json = json_path;
paths.report = report;

end

function value = relativeSymmetryError(A)

value = norm(A - A','fro')/max(norm(A,'fro'),eps);

end

function [is_pd,min_eig] = massPositiveDefinite(Mff)

try
    chol(Mff);
    is_pd = true;
catch
    is_pd = false;
end
try
    eig_values = eig(full(Mff));
    min_eig = min(real(eig_values));
catch
    min_eig = NaN;
end

end

function mass_kg = integratedBladeMass(mesh,props,element_mass_data)

if nargin >= 3 && ~isempty(element_mass_data) && isfield(element_mass_data,'mass_kg')
    mass_values = [element_mass_data.mass_kg];
    if ~isempty(mass_values)
        mass_kg = sum(mass_values(:));
        return
    end
end

rho = props.rho_kg_m3;
mass_kg = 0;
for e = 1:mesh.n_elements
    n1 = mesh.connectivity(e,1);
    n2 = mesh.connectivity(e,2);
    A_element = mean(props.A_m2([n1 n2]));
    L_element = norm(mesh.nodes_global(n2,:) - mesh.nodes_global(n1,:));
    mass_kg = mass_kg + rho*A_element*L_element;
end

end

function [mass_kg,mass_by_direction,spread_pct] = massFromMassMatrix(M,dof,mesh)

direction_vectors = zeros(dof.total_dofs,3);
for node = 1:mesh.n_nodes
    idx = (node-1)*dof.ndof_node + (1:3);
    direction_vectors(idx(1),1) = 1;
    direction_vectors(idx(2),2) = 1;
    direction_vectors(idx(3),3) = 1;
end

mass_by_direction = NaN(1,3);
for i = 1:3
    v = direction_vectors(:,i);
    mass_by_direction(i) = v'*M*v;
end
mass_kg = mean(mass_by_direction,'omitnan');
spread_pct = 100*(max(mass_by_direction) - min(mass_by_direction))/max(abs(mass_kg),eps);

end

function pct = percentDifference(value,reference)

if ~isfinite(value) || ~isfinite(reference) || abs(reference) <= eps
    pct = NaN;
else
    pct = 100*(value - reference)/reference;
end

end

function eigen_audit = auditEigenvalues(Kff,Mff,near_zero_frequency_tolerance_hz)

eigen_audit = struct();
eigen_audit.raw_eigen_solve_success = false;
eigen_audit.eigenvalues_real = false;
eigen_audit.eigenvalues_positive = false;
eigen_audit.eigenvalues_finite = false;
eigen_audit.frequency_values_finite = false;
eigen_audit.near_zero_frequency_tolerance_Hz = near_zero_frequency_tolerance_hz;
eigen_audit.near_zero_mode_count = NaN;
eigen_audit.min_real_eigenvalue = NaN;
eigen_audit.max_imaginary_eigenvalue_abs = NaN;
eigen_audit.warnings = {};

try
    lambda = eig(full(Kff),full(Mff));
    eigen_audit.raw_eigen_solve_success = true;
catch ME
    eigen_audit.warnings{end+1} = sprintf('Dense audit eigen solve failed: %s',ME.message);
    return
end

real_part = real(lambda);
imag_part = imag(lambda);
finite_values = isfinite(real_part) & isfinite(imag_part);
imag_tol = 1e-8*max(1,max(abs(real_part(finite_values))));
if isempty(imag_tol) || ~isfinite(imag_tol)
    imag_tol = 1e-8;
end

eigen_audit.eigenvalues_finite = all(finite_values);
eigen_audit.eigenvalues_real = all(abs(imag_part(finite_values)) <= imag_tol);
eigen_audit.eigenvalues_positive = eigen_audit.eigenvalues_real && ...
    all(real_part(finite_values) > 0);
if any(finite_values)
    eigen_audit.min_real_eigenvalue = min(real_part(finite_values));
    eigen_audit.max_imaginary_eigenvalue_abs = max(abs(imag_part(finite_values)));
end

positive_real = real_part(finite_values) > 0 & abs(imag_part(finite_values)) <= imag_tol;
freq = sqrt(real_part(finite_values));
freq(~positive_real) = NaN;
freq = freq/(2*pi);
eigen_audit.frequency_values_finite = all(isfinite(freq(positive_real)));
zero_lambda_threshold = (2*pi*near_zero_frequency_tolerance_hz)^2;
near_zero = finite_values & abs(imag_part) <= imag_tol & abs(real_part) <= zero_lambda_threshold;
eigen_audit.near_zero_mode_count = sum(near_zero);

if ~eigen_audit.eigenvalues_finite
    eigen_audit.warnings{end+1} = 'One or more audit eigenvalues are NaN or Inf.';
end
if ~eigen_audit.eigenvalues_real
    eigen_audit.warnings{end+1} = 'One or more audit eigenvalues have non-negligible imaginary parts.';
end
if ~eigen_audit.eigenvalues_positive
    eigen_audit.warnings{end+1} = 'One or more audit eigenvalues are non-positive.';
end
if eigen_audit.near_zero_mode_count > 0
    eigen_audit.warnings{end+1} = sprintf('%d near-zero modal eigenvalue(s) detected after root clamp.',eigen_audit.near_zero_mode_count);
end

end

function centrifugal = auditCentrifugalStiffening(K_base,Kg,M,bc,modal,mesh,dof,params,enabled)

centrifugal = struct();
centrifugal.enabled = enabled;
centrifugal.Kg_norm_over_K_norm = NaN;
centrifugal.zero_RPM_first_3_frequencies_Hz = [];
centrifugal.operating_RPM_first_3_frequencies_Hz = [];
centrifugal.operating_RPM = fieldOrDefault(params,'RPM',NaN);
centrifugal.frequency_change_percent = [];
centrifugal.frequency_stability_pass = true;
centrifugal.warnings = {};

if ~enabled
    return
end

centrifugal.Kg_norm_over_K_norm = norm(Kg,'fro')/max(norm(K_base,'fro'),eps);
modal_options = struct('normalization',fieldOrDefault(params,'modal_normalization','max_displacement'));
try
    modal_zero = solveModalEigenproblem(K_base,M,bc,3,mesh,dof,modal_options);
    zero_freq = modal_zero.frequencies_hz(:);
    operating_freq = modal.frequencies_hz(:);
    n = min([3,numel(zero_freq),numel(operating_freq)]);
    centrifugal.zero_RPM_first_3_frequencies_Hz = zero_freq(1:n);
    centrifugal.operating_RPM_first_3_frequencies_Hz = operating_freq(1:n);
    centrifugal.frequency_change_percent = 100*(operating_freq(1:n) - zero_freq(1:n))./max(abs(zero_freq(1:n)),eps);
    decrease_tol = 1e-6;
    if any(operating_freq(1:n) < zero_freq(1:n)*(1 - decrease_tol))
        centrifugal.frequency_stability_pass = false;
        centrifugal.warnings{end+1} = 'One or more first three modal frequencies decreased with centrifugal stiffening enabled.';
    end
catch ME
    centrifugal.frequency_stability_pass = false;
    centrifugal.warnings{end+1} = sprintf('0 RPM versus operating RPM modal comparison failed: %s',ME.message);
end

end

function value = logicalField(s,name,default_value)

value = default_value;
if isstruct(s) && isfield(s,name) && ~isempty(s.(name))
    value = logical(s.(name));
end

end

function value = fieldOrDefault(s,name,default_value)

if isstruct(s) && isfield(s,name) && ~isempty(s.(name))
    value = s.(name);
else
    value = default_value;
end

end

function values = struct2arrayLogical(s)

names = fieldnames(s);
values = false(numel(names),1);
for i = 1:numel(names)
    values(i) = logical(s.(names{i}));
end

end

function writeTextReport(path,report)

fid = fopen(path,'w');
if fid < 0
    error('Could not open modal matrix audit text report: %s',path);
end
cleanup = onCleanup(@() fclose(fid));

fprintf(fid,'Modal Matrix Audit\n');
fprintf(fid,'==================\n\n');
fprintf(fid,'%s\n\n',report.limitation_statement);
fprintf(fid,'Summary: %s\n\n',report.summary);

fprintf(fid,'Matrix sizes and DOFs\n');
fprintf(fid,'  K size: %d x %d\n',report.K_size(1),report.K_size(2));
fprintf(fid,'  M size: %d x %d\n',report.M_size(1),report.M_size(2));
fprintf(fid,'  Total DOFs: %d\n',report.total_dofs);
fprintf(fid,'  Constrained DOFs: %d\n',report.constrained_dofs);
fprintf(fid,'  Free DOFs: %d\n\n',report.free_dofs);

fprintf(fid,'Eigenproblem\n');
fprintf(fid,'  Form: %s\n',report.eigenproblem);
fprintf(fid,'  Uses reduced free DOFs: %d\n',report.uses_reduced_free_dofs);
fprintf(fid,'  Uses full K/M with constrained DOFs: %d\n',report.uses_full_matrices_with_constrained_dofs);
fprintf(fid,'  Solver: %s\n',char(string(report.modal_solver)));
fprintf(fid,'  Boundary condition: %s\n\n',char(string(report.modal_boundary_condition)));

fprintf(fid,'Matrix checks\n');
fprintf(fid,'  K symmetry error: %.12g\n',report.K_symmetry_error);
fprintf(fid,'  M symmetry error: %.12g\n',report.M_symmetry_error);
fprintf(fid,'  Symmetry tolerance: %.12g\n',report.symmetry_tolerance);
fprintf(fid,'  M positive definite on free DOFs: %d\n',report.M_positive_definite_on_free_dofs);
fprintf(fid,'  Mff min eigenvalue: %.12g\n\n',report.Mff_min_eigenvalue);

fprintf(fid,'Mass check\n');
fprintf(fid,'  Integrated mass: %.12g kg\n',report.mass_integrated_kg);
fprintf(fid,'  Mass implied by M: %.12g kg\n',report.mass_implied_by_M_kg);
fprintf(fid,'  Mass implied by M directions [x y z]: %.12g %.12g %.12g kg\n', ...
    report.mass_implied_by_direction_kg(1),report.mass_implied_by_direction_kg(2), ...
    report.mass_implied_by_direction_kg(3));
fprintf(fid,'  Direction spread: %.12g %%\n',report.mass_implied_direction_spread_percent);
fprintf(fid,'  Percent difference: %.12g %%\n\n',report.mass_percent_difference);

fprintf(fid,'Modal sanity checks\n');
fprintf(fid,'  Eigenvalues real: %d\n',report.eigenvalue_audit.eigenvalues_real);
fprintf(fid,'  Eigenvalues positive: %d\n',report.eigenvalue_audit.eigenvalues_positive);
fprintf(fid,'  Eigenvalues finite: %d\n',report.eigenvalue_audit.eigenvalues_finite);
fprintf(fid,'  Near-zero mode count: %d\n',report.eigenvalue_audit.near_zero_mode_count);
fprintf(fid,'  Near-zero frequency tolerance: %.12g Hz\n',report.eigenvalue_audit.near_zero_frequency_tolerance_Hz);
fprintf(fid,'  First 6 natural frequencies [Hz]:');
for i = 1:numel(report.first_6_natural_frequencies_Hz)
    fprintf(fid,' %.12g',report.first_6_natural_frequencies_Hz(i));
end
fprintf(fid,'\n\n');

fprintf(fid,'Rotating effects\n');
fprintf(fid,'  Centrifugal stiffening enabled: %d\n',report.centrifugal_stiffening_enabled);
fprintf(fid,'  Kg norm / K norm: %.12g\n',report.centrifugal_stiffening.Kg_norm_over_K_norm);
fprintf(fid,'  Operating RPM: %.12g\n',report.centrifugal_stiffening.operating_RPM);
fprintf(fid,'  Gyroscopic effects included: %d\n',report.gyroscopic_effects_included);
fprintf(fid,'  Gyroscopic effects status: %s\n',report.gyroscopic_effects_status);
if report.centrifugal_stiffening.enabled
    fprintf(fid,'  0 RPM first 3 frequencies [Hz]:');
    for i = 1:numel(report.centrifugal_stiffening.zero_RPM_first_3_frequencies_Hz)
        fprintf(fid,' %.12g',report.centrifugal_stiffening.zero_RPM_first_3_frequencies_Hz(i));
    end
    fprintf(fid,'\n  Operating RPM first 3 frequencies [Hz]:');
    for i = 1:numel(report.centrifugal_stiffening.operating_RPM_first_3_frequencies_Hz)
        fprintf(fid,' %.12g',report.centrifugal_stiffening.operating_RPM_first_3_frequencies_Hz(i));
    end
    fprintf(fid,'\n  Frequency change [%%]:');
    for i = 1:numel(report.centrifugal_stiffening.frequency_change_percent)
        fprintf(fid,' %.12g',report.centrifugal_stiffening.frequency_change_percent(i));
    end
    fprintf(fid,'\n  Frequency stability pass: %d\n',report.centrifugal_stiffening.frequency_stability_pass);
end
fprintf(fid,'\n');

fprintf(fid,'Checks\n');
names = fieldnames(report.checks);
for i = 1:numel(names)
    fprintf(fid,'  %s: %d\n',names{i},logical(report.checks.(names{i})));
end
fprintf(fid,'\nWarnings:\n');
if isempty(report.warnings)
    fprintf(fid,'  none\n');
else
    for i = 1:numel(report.warnings)
        fprintf(fid,'  - %s\n',report.warnings{i});
    end
end

end

function writeJsonReport(path,report)

try
    text = jsonencode(report,'PrettyPrint',true);
catch
    text = jsonencode(report);
end

fid = fopen(path,'w');
if fid < 0
    error('Could not open modal matrix audit JSON report: %s',path);
end
cleanup = onCleanup(@() fclose(fid));
fprintf(fid,'%s',text);

end
