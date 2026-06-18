function [F,loads,element_data] = apply_propeller_loads_3d(mesh,props,material,params,geom,element_data,dof)
%APPLY_PROPELLER_LOADS_3D Assemble equivalent nodal loads.
%
% Distributed loads are defined in each element's local frame:
%   qx = centrifugal spanwise load, rho*A*omega^2*r
%   qy = edgewise/tangential load
%   qz = thrust/flapwise load
%   mx = optional distributed blade torsional moment per length
%
% load_source options:
%   legacy_uniform  - existing analytic/csv thrust shapes for comparison
%   aero_solver_csv - import distributed qz/qy from aero solver station CSV

if nargin < 7 || isempty(dof)
    dof.ndof_node = 6;
    dof.total_dofs = 6*mesh.n_nodes;
end

F = zeros(dof.total_dofs,1);
F0 = zeros(dof.total_dofs,1);

r = mesh.radius_m(:);
load_source = lower(char(string(param_or_default(params,'load_source','legacy_uniform'))));
torque_loading = zeros(size(r));
aero_loads = [];
load_warnings = {};
torsion_note = ['Total aerodynamic shaft torque is represented through tangential ', ...
    'distributed loading qy and dQ/dr. It is not directly applied as blade ', ...
    'spanwise torsion unless a separate pitching moment or shear-center offset model is provided.'];

switch load_source
    case 'legacy_uniform'
        omega = params.RPM*2*pi/60;
        q_z = thrust_distribution(mesh,params,geom);
        q_y = zeros(size(r));
        m_x = zeros(size(r));

        if isfield(params,'include_edgewise_load') && params.include_edgewise_load
            q_y = edgewise_distribution(mesh,params,geom);
        end

        if isfield(params,'include_torsional_moment') && params.include_torsional_moment
            m_x = torsional_moment_distribution(mesh,params,geom);
        end

        if isfield(params,'include_aero_torsion_from_offset') && params.include_aero_torsion_from_offset
            elastic_axis = param_or_default(params,'elastic_axis_fraction_chord',0.25);
            aero_center = param_or_default(params,'aero_center_fraction_chord',elastic_axis);
            lever_arm_y = (aero_center - elastic_axis)*mesh.chord_m(:);
            m_x = m_x + lever_arm_y.*q_z;
        end

    case 'aero_solver_csv'
        station_csv = require_param(params,'aero_station_csv_path');
        summary_json = require_param(params,'aero_summary_json_path');
        aero_loads = load_aero_loads_for_fea(station_csv,summary_json);

        if isfield(params,'omega_override_rad_s') && ~isempty(params.omega_override_rad_s)
            omega = params.omega_override_rad_s;
        elseif isfinite(aero_loads.omega_rad_s)
            omega = aero_loads.omega_rad_s;
        elseif isfinite(aero_loads.rpm)
            omega = aero_loads.rpm*2*pi/60;
        else
            omega = params.RPM*2*pi/60;
            load_warnings{end+1} = 'Aero summary omega/rpm missing; using params.RPM for centrifugal loading.';
        end

        q_z = interp1(aero_loads.r_m,aero_loads.qz_N_per_m,r,'linear','extrap');
        q_y = interp1(aero_loads.r_m,aero_loads.qy_tangential_N_per_m,r,'linear','extrap');
        torque_loading = interp1(aero_loads.r_m,aero_loads.torque_loading_Nm_per_m,r,'linear','extrap');
        m_x = zeros(size(r));
        load_warnings = [load_warnings; aero_loads.warnings(:)];
        load_warnings{end+1} = 'Distributed blade torsional moment not modeled.';

    otherwise
        error('Unknown load_source: %s',load_source);
end

q_x = material.rho.*props.A_m2(:).*omega.^2.*r;
consistency = compute_load_consistency(r,q_x,q_y,q_z,torque_loading,omega,aero_loads,load_source,load_warnings);

for e = 1:mesh.n_elements
    n1 = mesh.connectivity(e,1);
    n2 = mesh.connectivity(e,2);
    L = element_data(e).L_m;
    edofs = element_data(e).edofs;

    [fe_local,f0e_local,load_components] = local_equivalent_load_3d_frame( ...
        [q_x(n1); q_x(n2)], [q_y(n1); q_y(n2)], ...
        [q_z(n1); q_z(n2)], [m_x(n1); m_x(n2)], L);

    fe_global = element_data(e).T'*fe_local;
    F0e_global = element_data(e).T'*f0e_local;
    F(edofs) = F(edofs) + fe_global;
    F0(edofs) = F0(edofs) + F0e_global;

    element_data(e).applied_load_local = fe_local;
    element_data(e).applied_load_global = fe_global;
    element_data(e).fe_local = fe_local;
    element_data(e).fe_global = fe_global;
    element_data(e).f0e_local = f0e_local;
    element_data(e).F0e_global = F0e_global;
    element_data(e).load_components = load_components;
end

loads = struct();
loads.omega_rad_s = omega;
loads.rpm = omega*60/(2*pi);
loads.q_x_centrifugal_N_m = q_x;
loads.q_y_edgewise_N_m = q_y;
loads.q_z_thrust_N_m = q_z;
loads.torque_loading_Nm_m = torque_loading;
loads.m_x_torsion_Nm_m = m_x;
loads.applied_equivalent_load_global = F;
loads.F0_global = F0;
loads.F0_convention = ['Chapter 10 restoring fixed-end load vector. ', ...
    'For distributed loads only, F0_global = -applied_equivalent_load_global.'];
loads.total_thrust_per_blade_N = trapz(r,q_z);
loads.integrated_thrust_N = consistency.FEA_integrated_thrust_N;
loads.integrated_torque_from_qy_Nm = consistency.FEA_integrated_torque_from_qy_Nm;
loads.integrated_torque_from_torque_loading_Nm = consistency.FEA_integrated_torque_from_torque_loading_Nm;
loads.power_from_qy_W = consistency.power_from_qy_W;
loads.load_source = load_source;
loads.load_distribution_type = param_or_default(params,'load_distribution_type','');
loads.material_density_kg_m3 = material.rho;
loads.elastic_axis_fraction_chord = param_or_default(params,'elastic_axis_fraction_chord',0.25);
loads.aero_center_fraction_chord = param_or_default(params,'aero_center_fraction_chord',loads.elastic_axis_fraction_chord);
loads.aero_loads = aero_loads;
loads.load_consistency = consistency;
loads.warnings = consistency.warnings;
loads.torsion_note = torsion_note;
loads.distributed_torsional_moment_modeled = any(abs(m_x) > 0);

end

function value = param_or_default(params,name,default_value)

if isfield(params,name) && ~isempty(params.(name))
    value = params.(name);
else
    value = default_value;
end

end

function value = require_param(params,name)

if ~isfield(params,name) || isempty(params.(name))
    error('params.%s is required for aero_solver_csv loading.',name);
end
value = params.(name);

end

function consistency = compute_load_consistency(r,q_x,q_y,q_z,torque_loading,omega,aero_loads,load_source,warnings_in)

warnings_out = warnings_in(:);
integrated_thrust = trapz(r,q_z);
integrated_torque_qy = trapz(r,r.*q_y);
integrated_torque_loading = trapz(r,torque_loading);
power_qy = integrated_torque_qy*omega;

summary_thrust = NaN;
summary_torque = NaN;
summary_power = NaN;
target_thrust = NaN;
summary_rpm = omega*60/(2*pi);
summary_omega = omega;
valid_for_thesis = true;
polar_is_placeholder = false;
target_matched = true;
aero_station_csv = '';
aero_summary_json = '';
case_name = '';
has_qy = true;

if isstruct(aero_loads) && ~isempty(fieldnames(aero_loads))
    summary_thrust = aero_loads.thrust_N;
    summary_torque = aero_loads.torque_Nm;
    summary_power = aero_loads.power_W;
    target_thrust = aero_loads.target_thrust_N;
    summary_rpm = aero_loads.rpm;
    summary_omega = aero_loads.omega_rad_s;
    valid_for_thesis = aero_loads.valid_for_thesis;
    polar_is_placeholder = aero_loads.polar_is_placeholder;
    target_matched = aero_loads.target_matched;
    aero_station_csv = aero_loads.station_csv_path;
    aero_summary_json = aero_loads.summary_json_path;
    case_name = aero_loads.case_name;
    has_qy = aero_loads.has_qy_tangential_N_per_m;

    thrust_error_pct = percent_error(integrated_thrust,summary_thrust);
    torque_error_pct = percent_error(integrated_torque_qy,summary_torque);
    torque_loading_error_pct = percent_error(integrated_torque_loading,summary_torque);

    if isfinite(thrust_error_pct) && abs(thrust_error_pct) > 2
        warnings_out{end+1} = sprintf('FEA-integrated thrust differs from aero summary by %.3g%%.',thrust_error_pct);
    end
    if isfinite(torque_error_pct) && abs(torque_error_pct) > 2
        warnings_out{end+1} = sprintf('FEA-integrated tangential torque differs from aero summary by %.3g%%.',torque_error_pct);
    end
    if isfinite(torque_loading_error_pct) && abs(torque_loading_error_pct) > 2
        warnings_out{end+1} = sprintf('FEA-integrated torque_loading differs from aero summary by %.3g%%.',torque_loading_error_pct);
    end
    if ~has_qy && isfinite(summary_torque) && abs(summary_torque) > 0
        warnings_out{end+1} = 'qy is missing but aero summary torque is nonzero.';
    end
    if ~valid_for_thesis
        warnings_out{end+1} = 'Aero result is not valid_for_thesis.';
    end
else
    thrust_error_pct = NaN;
    torque_error_pct = NaN;
    torque_loading_error_pct = NaN;
end

consistency = struct();
consistency.case_name = case_name;
consistency.aero_station_file = aero_station_csv;
consistency.aero_summary_file = aero_summary_json;
consistency.load_source = load_source;
consistency.target_thrust_N = target_thrust;
consistency.aero_summary_thrust_N = summary_thrust;
consistency.FEA_integrated_thrust_N = integrated_thrust;
consistency.aero_summary_torque_Nm = summary_torque;
consistency.FEA_integrated_torque_from_qy_Nm = integrated_torque_qy;
consistency.FEA_integrated_torque_from_torque_loading_Nm = integrated_torque_loading;
consistency.aero_summary_power_W = summary_power;
consistency.power_from_qy_W = power_qy;
consistency.omega_rad_s = omega;
consistency.rpm = omega*60/(2*pi);
consistency.aero_summary_omega_rad_s = summary_omega;
consistency.aero_summary_rpm = summary_rpm;
consistency.target_matched = target_matched;
consistency.valid_for_thesis = valid_for_thesis;
consistency.polar_is_placeholder = polar_is_placeholder;
consistency.max_qz_N_per_m = max(abs(q_z));
consistency.max_qy_N_per_m = max(abs(q_y));
consistency.max_qx_N_per_m = max(abs(q_x));
consistency.max_torque_loading_Nm_per_m = max(abs(torque_loading));
consistency.thrust_error_pct = thrust_error_pct;
consistency.torque_from_qy_error_pct = torque_error_pct;
consistency.torque_from_torque_loading_error_pct = torque_loading_error_pct;
consistency.has_qy_tangential_N_per_m = has_qy;
consistency.torsion_note = ['Total aerodynamic shaft torque is represented through tangential ', ...
    'distributed loading qy and dQ/dr. It is not directly applied as blade spanwise torsion ', ...
    'unless a separate pitching moment or shear-center offset model is provided.'];
consistency.distributed_torsional_moment_note = 'Distributed blade torsional moment not modeled.';
consistency.warnings = unique_preserve_order(warnings_out);

end

function err = percent_error(value,reference)

if ~isfinite(value) || ~isfinite(reference) || abs(reference) < eps
    err = NaN;
else
    err = 100*(value - reference)/reference;
end

end

function out = unique_preserve_order(in)

out = {};
for i = 1:numel(in)
    text = char(string(in{i}));
    if isempty(text)
        continue
    end
    if ~any(strcmp(out,text))
        out{end+1,1} = text; %#ok<AGROW>
    end
end

end

function q = thrust_distribution(mesh,params,geom)

r = mesh.radius_m(:);
x = (r - r(1))/(r(end) - r(1));
kind = lower(char(params.load_distribution_type));

switch kind
    case 'uniform'
        shape = ones(size(r));
    case 'elliptical'
        shape = sqrt(max(0,1 - x.^2));
    case 'bell'
        shape = sin(pi*x).^2;
    case 'csv'
        [q,ok] = csv_thrust_distribution(mesh,params,geom);
        if ok
            q = normalize_to_thrust(r,q,params.total_thrust_per_blade_N);
            return
        end
        warning('CSV thrust load unavailable. Falling back to bell distribution.');
        shape = sin(pi*x).^2;
    otherwise
        error('Unknown load_distribution_type: %s',params.load_distribution_type);
end

q = normalize_to_thrust(r,shape,params.total_thrust_per_blade_N);

end

function q = normalize_to_thrust(r,shape,total_thrust)

area = trapz(r,shape);
if area <= 0
    error('Load distribution has zero integral.');
end
q = total_thrust*shape/area;

end

function [q,ok] = csv_thrust_distribution(mesh,params,geom)

q = zeros(mesh.n_nodes,1);
ok = false;

if isfield(params,'thrust_distribution_csv_path') && ~isempty(params.thrust_distribution_csv_path) ...
        && isfile(params.thrust_distribution_csv_path)
    thrust_geom = read_prop_geometry(params.thrust_distribution_csv_path,params.radius_m);
    if isfield(thrust_geom,'dthrust_N')
        [q,ok] = station_increment_to_q(mesh,thrust_geom.radius_m,thrust_geom.dthrust_N,thrust_geom);
    end
    return
end

if isfield(geom,'dthrust_N') && ~isempty(geom.dthrust_N)
    [q,ok] = station_increment_to_q(mesh,geom.radius_m,geom.dthrust_N,geom);
end

end

function q_y = edgewise_distribution(mesh,params,geom)

q_y = zeros(mesh.n_nodes,1);

if isfield(params,'edgewise_load_N_m')
    q_y = expand_station_value(params.edgewise_load_N_m,mesh.n_nodes);
elseif isfield(geom,'dtorque_Nm') && ~isempty(geom.dtorque_Nm)
    [q_torque,ok] = station_increment_to_q(mesh,geom.radius_m,geom.dtorque_Nm,geom);
    if ok
        r_safe = max(mesh.radius_m(:),0.05*params.radius_m);
        q_y = q_torque./r_safe;
    end
end

end

function m_x = torsional_moment_distribution(mesh,params,geom)

m_x = zeros(mesh.n_nodes,1);
if isfield(params,'torsional_moment_Nm_m')
    m_x = expand_station_value(params.torsional_moment_Nm_m,mesh.n_nodes);
elseif isfield(geom,'dtorsion_Nm') && ~isempty(geom.dtorsion_Nm)
    [m_x,~] = station_increment_to_q(mesh,geom.radius_m,geom.dtorsion_Nm,geom);
end

end

function values = expand_station_value(value,n)

if isscalar(value)
    values = repmat(value,n,1);
else
    values = value(:);
    if numel(values) ~= n
        error('Distributed load vector length must match mesh node count.');
    end
end

end

function [q_mesh,ok] = station_increment_to_q(mesh,r_station,dload,geom)

r_station = r_station(:);
dload = dload(:);

if isfield(geom,'dr_m') && numel(geom.dr_m) == numel(dload)
    dr = geom.dr_m(:);
else
    dr = gradient(r_station);
end

valid = isfinite(r_station) & isfinite(dload) & isfinite(dr) & dr > 0;
ok = nnz(valid) >= 2;
if ~ok
    q_mesh = zeros(mesh.n_nodes,1);
    return
end

q_station = dload(valid)./dr(valid);
q_mesh = interp1(r_station(valid),q_station,mesh.radius_m,'linear','extrap');
q_mesh = max(q_mesh,0);

end

function fe = consistent_bar_load(q1,q2,L)

fe = L/6*[2*q1 + q2; q1 + 2*q2];

end

function fe = consistent_beam_load_y(q1,q2,L)

fe = consistent_hermite_load(q1,q2,L);

end

function fe = consistent_beam_load_z(q1,q2,L)

base = consistent_hermite_load(q1,q2,L);
fe = [base(1); -base(2); base(3); -base(4)];

end

function fe = consistent_hermite_load(q1,q2,L)

xi = [0.1127016653792583; 0.5; 0.8872983346207417];
w = [5/18; 8/18; 5/18];
fe = zeros(4,1);
for i = 1:3
    s = xi(i);
    H = [1 - 3*s^2 + 2*s^3;
         L*(s - 2*s^2 + s^3);
         3*s^2 - 2*s^3;
         L*(-s^2 + s^3)];
    q = (1 - s)*q1 + s*q2;
    fe = fe + H*q*L*w(i);
end

end
