function csv_path = export_results_table(mesh,props,solution,forces,stress,results_dir,output_name)
%EXPORT_RESULTS_TABLE Export station-level FEA results to CSV.

if ~exist(results_dir,'dir')
    mkdir(results_dir);
end

csv_path = fullfile(results_dir,[output_name '_results.csv']);
if exist(csv_path,'file')
    delete(csv_path);
end

U = solution.displacements;
displacement_magnitude = sqrt(sum(U(:,1:3).^2,2));
twist_angle_deg = U(:,4)*180/pi;

sigma_combined_signed = optional_stress(stress,'sigma_combined_signed_Pa', ...
    stress.sigma_axial_Pa(:) + stress.sigma_flap_Pa(:) + stress.sigma_edge_Pa(:));

fallback_input = struct();
fallback_input.N = forces.N_N(:);
fallback_input.My = forces.My_bending_Nm(:);
fallback_input.Mz = forces.Mz_bending_Nm(:);
fallback_input.T = forces.Mx_torsion_Nm(:);
fallback_input.A = props.A_m2(:);
fallback_input.Iy = props.Iy_m4(:);
fallback_input.Iz = props.Iz_m4(:);
fallback_input.J = props.J_m4(:);
fallback_envelope = computeSectionStressEnvelope(fallback_input,props,[]);

sigma_vm_max = optional_stress(stress,'sigma_vm_max_Pa', ...
    fallback_envelope.sigma_vm_max_Pa);
sigma_vm_min = optional_stress(stress,'sigma_vm_min_Pa',sigma_vm_max);
sigma_x_max_tension = optional_stress(stress,'sigma_x_max_tension_Pa', ...
    fallback_envelope.sigma_x_max_tension_Pa);
sigma_x_max_compression = optional_stress(stress,'sigma_x_max_compression_Pa', ...
    fallback_envelope.sigma_x_max_compression_Pa);
sigma_abs_normal_max = optional_stress(stress,'sigma_abs_normal_max_Pa', ...
    fallback_envelope.sigma_abs_normal_max_Pa);
tau_torsion_envelope = optional_stress(stress,'tau_torsion_envelope_Pa', ...
    fallback_envelope.tau_torsion_max_Pa);
critical_section_point = optional_text(stress,'critical_section_point',mesh.n_nodes, ...
    fallback_envelope.critical_section_point);
torsional_shear_assumed_zero = optional_logical(stress,'torsional_shear_assumed_zero', ...
    mesh.n_nodes,fallback_envelope.torsional_shear_assumed_zero);

sigma_von_mises = sigma_vm_max;
sigma_max_tensile = sigma_x_max_tension;
sigma_max_compressive = sigma_x_max_compression;

q_x = optional_force(forces,'q_x_centrifugal_N_m',NaN(mesh.n_nodes,1));
q_y = optional_force(forces,'q_y_edgewise_N_m',NaN(mesh.n_nodes,1));
q_z = optional_force(forces,'q_z_thrust_N_m',NaN(mesh.n_nodes,1));
m_x = optional_force(forces,'m_x_torsion_Nm_m',NaN(mesh.n_nodes,1));
Vy = optional_force(forces,'Vy_edgewise_N',NaN(mesh.n_nodes,1));
Vz = optional_force(forces,'Vz_flapwise_N',NaN(mesh.n_nodes,1));

data = [mesh.mu(:), mesh.radius_m(:), mesh.chord_m(:), mesh.twist_deg(:), ...
    props.A_m2(:), props.Iy_m4(:), props.Iz_m4(:), props.J_m4(:), ...
    props.mass_per_length_kg_m(:), ...
    U(:,1), U(:,2), U(:,3), U(:,4), U(:,5), U(:,6), ...
    displacement_magnitude, twist_angle_deg, ...
    forces.N_N(:), Vy(:), Vz(:), forces.Mx_torsion_Nm(:), ...
    forces.My_bending_Nm(:), forces.Mz_bending_Nm(:), ...
    q_x(:), q_y(:), q_z(:), m_x(:), ...
    stress.sigma_axial_Pa(:), ...
    stress.sigma_flap_Pa(:), stress.sigma_edge_Pa(:), ...
    stress.tau_torsion_Pa(:), sigma_abs_normal_max(:), ...
    sigma_combined_signed(:), sigma_von_mises(:), ...
    sigma_max_tensile(:), sigma_max_compressive(:), ...
    sigma_vm_max(:), sigma_vm_min(:), sigma_x_max_tension(:), ...
    sigma_x_max_compression(:), sigma_abs_normal_max(:), ...
    tau_torsion_envelope(:), torsional_shear_assumed_zero(:)];

headers = {'r/R','r_m','chord_m','twist_deg','A','Iy','Iz','J', ...
    'mass_per_length','ux','uy','uz','rx','ry','rz', ...
    'displacement_magnitude','twist_angle_deg','N','Vy_edgewise', ...
    'Vz_flapwise','Mx_torsion','My_bending','Mz_bending', ...
    'q_centrifugal','q_edgewise','q_thrust','m_torsion_dist', ...
    'sigma_axial','sigma_flap','sigma_edge','tau_torsion', ...
    'sigma_combined','sigma_combined_signed','sigma_von_mises', ...
    'sigma_max_tensile','sigma_max_compressive', ...
    'sigma_vm_max','sigma_vm_min','sigma_x_max_tension', ...
    'sigma_x_max_compression','sigma_abs_normal_max', ...
    'tau_torsion_envelope','torsional_shear_assumed_zero', ...
    'critical_section_point'};

fid = fopen(csv_path,'w');
if fid < 0
    error('Could not open results CSV for writing: %s',csv_path);
end

function values = optional_stress(stress,name,default_values)

if isfield(stress,name) && ~isempty(stress.(name))
    tmp = stress.(name);
    values = tmp(:);
else
    values = default_values(:);
end

end

function values = optional_force(forces,name,default_values)

if isfield(forces,name) && ~isempty(forces.(name))
    tmp = forces.(name);
    values = tmp(:);
else
    values = default_values(:);
end

end

function values = optional_logical(stress,name,n,default_values)

if isfield(stress,name) && ~isempty(stress.(name))
    tmp = stress.(name);
    values = double(tmp(:));
elseif nargin >= 4 && ~isempty(default_values)
    values = double(default_values(:));
else
    values = zeros(n,1);
end

end

function values = optional_text(stress,name,n,default_values)

if isfield(stress,name) && ~isempty(stress.(name))
    tmp = stress.(name);
    if isstring(tmp)
        values = cellstr(tmp(:));
    elseif iscell(tmp)
        values = tmp(:);
    elseif ischar(tmp)
        values = repmat({tmp},n,1);
    else
        values = arrayfun(@(v) sprintf('%g',v),tmp(:),'UniformOutput',false);
    end
elseif nargin >= 4 && ~isempty(default_values)
    if isstring(default_values)
        values = cellstr(default_values(:));
    elseif iscell(default_values)
        values = default_values(:);
    else
        values = repmat({''},n,1);
    end
else
    values = repmat({''},n,1);
end
if numel(values) < n
    values(end+1:n,1) = {''};
end
values = values(1:n);

end

cleanup = onCleanup(@() fclose(fid));
fprintf(fid,'%s',headers{1});
for j = 2:numel(headers)
    fprintf(fid,',%s',headers{j});
end
fprintf(fid,'\n');

fmt = [repmat('%.12g,',1,size(data,2)) '%s\n'];
for i = 1:size(data,1)
    fprintf(fid,fmt,data(i,:),critical_section_point{i});
end

end
