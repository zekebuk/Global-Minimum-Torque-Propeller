function [K,element_data,dof] = assemble_global_stiffness(mesh,props,material)
%ASSEMBLE_GLOBAL_STIFFNESS Assemble global K for 3D beam/frame elements.

dof = struct();
dof.ndof_node = 6;
dof.labels = {'ux','uy','uz','rx','ry','rz'};
dof.total_dofs = dof.ndof_node*mesh.n_nodes;

K = zeros(dof.total_dofs,dof.total_dofs);
element_data = repmat(empty_element_data(),mesh.n_elements,1);

for e = 1:mesh.n_elements
    n1 = mesh.connectivity(e,1);
    n2 = mesh.connectivity(e,2);
    x1 = mesh.nodes_global(n1,:);
    x2 = mesh.nodes_global(n2,:);
    L = norm(x2 - x1);
    beta_avg = 0.5*(mesh.twist_deg(n1) + mesh.twist_deg(n2));

    A = mean(props.A_m2([n1 n2]));
    Iy = mean(props.Iy_m4([n1 n2]));
    Iz = mean(props.Iz_m4([n1 n2]));
    J = mean(props.J_m4([n1 n2]));

    k_local = local_stiffness_3d_beam(material.E,material.G,A,Iy,Iz,J,L);
    [T,R,axes] = transformation_matrix_3d_beam(x1,x2,beta_avg);
    k_global = T'*k_local*T;

    edofs = [(n1-1)*6 + (1:6), (n2-1)*6 + (1:6)];
    K(edofs,edofs) = K(edofs,edofs) + k_global;

    element_data(e).node_ids = [n1 n2];
    element_data(e).edofs = edofs;
    element_data(e).L_m = L;
    element_data(e).beta_deg = beta_avg;
    element_data(e).A_m2 = A;
    element_data(e).Iy_m4 = Iy;
    element_data(e).Iz_m4 = Iz;
    element_data(e).J_m4 = J;
    element_data(e).k_local = k_local;
    element_data(e).k_global = k_global;
    element_data(e).T = T;
    element_data(e).R = R;
    element_data(e).axes = axes;
    element_data(e).applied_load_local = zeros(12,1);
    element_data(e).applied_load_global = zeros(12,1);
    element_data(e).fe_local = zeros(12,1);
    element_data(e).fe_global = zeros(12,1);
    element_data(e).f0e_local = zeros(12,1);
    element_data(e).F0e_global = zeros(12,1);
    element_data(e).chapter10_note = ...
        'Ke_global = B''*ke_local*B, with u_local = B*U_global.';
end

K = (K + K')/2;

end

function e = empty_element_data()

e = struct('node_ids',[],'edofs',[],'L_m',[],'beta_deg',[], ...
    'A_m2',[],'Iy_m4',[],'Iz_m4',[],'J_m4',[], ...
    'k_local',[],'k_global',[],'T',[],'R',[],'axes',[], ...
    'applied_load_local',[],'applied_load_global',[], ...
    'fe_local',[],'fe_global',[],'f0e_local',[],'F0e_global',[], ...
    'load_components',[],'chapter10_note',[]);

end
