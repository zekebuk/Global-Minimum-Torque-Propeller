function [M,element_mass_data] = assembleGlobalMassMatrix(mesh,props,element_data,dof,options)
%ASSEMBLEGLOBALMASSMATRIX Assemble global mass matrix using beam DOF order.

if nargin < 4 || isempty(dof)
    dof.ndof_node = 6;
    dof.total_dofs = 6*mesh.n_nodes;
end
if nargin < 5 || isempty(options)
    options = struct();
end
if ~isfield(options,'mass_matrix_type')
    options.mass_matrix_type = 'consistent';
end
if ~isfield(options,'include_rotary_inertia')
    options.include_rotary_inertia = true;
end

M = zeros(dof.total_dofs,dof.total_dofs);
element_mass_data = repmat(struct('node_ids',[],'edofs',[],'L_m',[], ...
    'mass_kg',[],'M_local',[],'M_global',[],'mass_matrix_type',''),mesh.n_elements,1);

for e = 1:mesh.n_elements
    n1 = mesh.connectivity(e,1);
    n2 = mesh.connectivity(e,2);
    edofs = element_data(e).edofs;
    L = element_data(e).L_m;

    rho = props.rho_kg_m3;
    A = mean(props.A_m2([n1 n2]));
    Iy = mean(props.Iy_m4([n1 n2]));
    Iz = mean(props.Iz_m4([n1 n2]));
    J = mean(props.J_m4([n1 n2]));

    if strcmpi(options.mass_matrix_type,'lumped')
        M_local = lumpedElementMassMatrix(rho,A,Iy,Iz,J,L,options);
        mass_type = 'lumped translational with lumped rotary inertia';
    else
        M_local = beamElementConsistentMassMatrix(rho,A,Iy,Iz,J,L,options);
        mass_type = 'consistent translational with simple consistent rotary inertia';
    end

    M_global = element_data(e).T'*M_local*element_data(e).T;
    M(edofs,edofs) = M(edofs,edofs) + M_global;

    element_mass_data(e).node_ids = [n1 n2];
    element_mass_data(e).edofs = edofs;
    element_mass_data(e).L_m = L;
    element_mass_data(e).mass_kg = rho*A*L;
    element_mass_data(e).M_local = M_local;
    element_mass_data(e).M_global = M_global;
    element_mass_data(e).mass_matrix_type = mass_type;
end

M = (M + M')/2;

end

function M_local = lumpedElementMassMatrix(rho,A,Iy,Iz,J,L,options)

M_local = zeros(12,12);
mass = rho*A*L;
for node = 1:2
    idx = (node-1)*6 + (1:6);
    M_local(idx(1),idx(1)) = mass/2;
    M_local(idx(2),idx(2)) = mass/2;
    M_local(idx(3),idx(3)) = mass/2;
    if options.include_rotary_inertia
        M_local(idx(4),idx(4)) = rho*J*L/2;
        M_local(idx(5),idx(5)) = rho*Iy*L/2;
        M_local(idx(6),idx(6)) = rho*Iz*L/2;
    end
end

end
