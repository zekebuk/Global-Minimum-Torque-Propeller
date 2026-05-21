%% validate_torsion
% Compare torsional twist against T*L/(G*J).

clear; clc;
this_dir = fileparts(mfilename('fullpath'));
addpath(this_dir,'-begin');
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end

L = 1.0;
T_applied = 25.0;
E = 70e9;
nu = 0.33;
rho = 2700;
G = E/(2*(1 + nu));
A = 1.0e-3;
Iy = 2.0e-6;
Iz = 5.0e-6;
J = 1.0e-6;
n_nodes = 8;

[mesh,props,material] = validation_model(L,n_nodes,E,G,nu,rho,A,Iy,Iz,J);
[K,~,dof] = assemble_global_stiffness(mesh,props,material);

F = zeros(dof.total_dofs,1);
tip_rx_dof = (mesh.n_nodes-1)*6 + 4;
F(tip_rx_dof) = T_applied;

bc = apply_boundary_conditions(mesh,dof,1);
solution = solve_static_fea(K,F,bc,dof);

theta_fea = solution.displacements(end,4);
theta_exact = T_applied*L/(G*J);
error_pct = abs((theta_fea - theta_exact)/theta_exact)*100;

fprintf('Torsion validation\n');
fprintf('  FEA tip rx        = %.12g rad\n',theta_fea);
fprintf('  analytical theta  = %.12g rad\n',theta_exact);
fprintf('  percent error     = %.6g %%\n',error_pct);
if error_pct < 1e-6
    fprintf('  PASS\n');
else
    fprintf('  CHECK: error exceeds tight numerical tolerance.\n');
end

function [mesh,props,material] = validation_model(L,n_nodes,E,G,nu,rho,A,Iy,Iz,J)

r = linspace(0,L,n_nodes)';
mesh = struct();
mesh.radius_tip_m = L;
mesh.radius_m = r;
mesh.mu = r/L;
mesh.chord_m = 0.1*ones(n_nodes,1);
mesh.twist_deg = zeros(n_nodes,1);
mesh.thickness_ratio_from_csv = 0.1*ones(n_nodes,1);
mesh.nodes_global = [r, zeros(n_nodes,1), zeros(n_nodes,1)];
mesh.n_nodes = n_nodes;
mesh.n_elements = n_nodes - 1;
mesh.connectivity = [(1:mesh.n_elements)', (2:n_nodes)'];
mesh.element_length_m = diff(r);

props = struct();
props.A_m2 = A*ones(n_nodes,1);
props.Iy_m4 = Iy*ones(n_nodes,1);
props.Iz_m4 = Iz*ones(n_nodes,1);
props.J_m4 = J*ones(n_nodes,1);
props.mass_per_length_kg_m = rho*A*ones(n_nodes,1);
props.rho_kg_m3 = rho;

material = struct('E',E,'G',G,'nu',nu,'rho',rho);

end
