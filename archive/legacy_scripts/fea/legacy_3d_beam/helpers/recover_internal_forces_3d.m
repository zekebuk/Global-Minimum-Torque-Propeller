function forces = recover_internal_forces_3d(mesh,props,material,element_data,solution,loads,dof)
%RECOVER_INTERNAL_FORCES_3D Recover local element and nodal beam resultants.
%
% Element local end force vector order:
%   [Fx Fy Fz Mx My Mz] at node i, then [Fx Fy Fz Mx My Mz] at node j.
%
% Chapter 10 recovery form:
%   f_local = k_local*B_e*Ue_global + f0e_local
%
% Nodal station values below use the adjacent element end with the largest
% absolute value for each component. This is useful for stress envelopes in
% a reduced-order screening model and is a visualization/post-processing
% projection, not an additional finite-element solve.

if nargin < 7 || isempty(dof)
    dof.ndof_node = 6;
end

element_end_forces = zeros(mesh.n_elements,12);
element_local_displacements = zeros(mesh.n_elements,12);
element_left_resultants = zeros(mesh.n_elements,6);
element_right_resultants = zeros(mesh.n_elements,6);
element_mid_resultants = zeros(mesh.n_elements,6);
element_mid_radius = zeros(mesh.n_elements,1);

nodal_values = zeros(mesh.n_nodes,6);
nodal_absmax = -inf(mesh.n_nodes,6);

for e = 1:mesh.n_elements
    edofs = element_data(e).edofs;
    u_global = solution.U(edofs);
    u_local = element_data(e).T*u_global;
    if isfield(element_data,'f0e_local') && ~isempty(element_data(e).f0e_local)
        f0e_local = element_data(e).f0e_local;
    elseif isfield(element_data,'fe_local') && ~isempty(element_data(e).fe_local)
        f0e_local = -element_data(e).fe_local;
    else
        f0e_local = zeros(12,1);
    end
    f_local = element_data(e).k_local*u_local + f0e_local;

    element_local_displacements(e,:) = u_local(:)';
    element_end_forces(e,:) = f_local(:)';

    n1 = element_data(e).node_ids(1);
    n2 = element_data(e).node_ids(2);

    left_internal = -f_local(1:6);
    right_internal = f_local(7:12);
    element_left_resultants(e,:) = left_internal(:)';
    element_right_resultants(e,:) = right_internal(:)';
    element_mid_resultants(e,:) = 0.5*(left_internal(:)' + right_internal(:)');
    element_mid_radius(e) = 0.5*(mesh.radius_m(n1) + mesh.radius_m(n2));

    [nodal_values,nodal_absmax] = update_node(nodal_values,nodal_absmax,n1,left_internal);
    [nodal_values,nodal_absmax] = update_node(nodal_values,nodal_absmax,n2,right_internal);
end

forces = struct();
forces.element_end_forces_local = element_end_forces;
forces.element_local_displacements = element_local_displacements;
forces.element_left_resultants_local = element_left_resultants;
forces.element_right_resultants_local = element_right_resultants;
forces.element_mid_resultants_local = element_mid_resultants;
forces.element_mid_radius_m = element_mid_radius;
forces.element_node_ids = reshape([element_data.node_ids],2,[])';
forces.N_N = nodal_values(:,1);
forces.Vy_edgewise_N = nodal_values(:,2);
forces.Vz_flapwise_N = nodal_values(:,3);
forces.Mx_torsion_Nm = nodal_values(:,4);
forces.My_bending_Nm = nodal_values(:,5);
forces.Mz_bending_Nm = nodal_values(:,6);
forces.q_x_centrifugal_N_m = loads.q_x_centrifugal_N_m;
forces.q_z_thrust_N_m = loads.q_z_thrust_N_m;
forces.q_y_edgewise_N_m = loads.q_y_edgewise_N_m;
forces.m_x_torsion_Nm_m = loads.m_x_torsion_Nm_m;
forces.recovery_note = ['Element recovery uses f_local = k_local*B_e*Ue_global + f0e_local. ', ...
    'Station values select the adjacent local element end force with maximum absolute magnitude. ', ...
    'That station projection is for plotting/stress-envelope visualization only.'];
forces.nodal_projection_note = ...
    'Nodal resultants are projected from adjacent element end forces for visualization only.';
forces.material_E_Pa = material.E;
forces.section_A_m2 = props.A_m2;

end

function [nodal_values,nodal_absmax] = update_node(nodal_values,nodal_absmax,node_id,candidate)

for j = 1:6
    if abs(candidate(j)) > nodal_absmax(node_id,j)
        nodal_absmax(node_id,j) = abs(candidate(j));
        nodal_values(node_id,j) = candidate(j);
    end
end

end
