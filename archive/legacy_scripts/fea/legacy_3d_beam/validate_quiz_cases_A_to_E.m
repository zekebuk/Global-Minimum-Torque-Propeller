%% validate_quiz_cases_A_to_E
% Runnable A-E validation set for the 6DOF 3D frame solver.
%
% Replace the scalar inputs below if the course quiz used different numbers;
% the case structure matches the usual checks: axial, flapwise, edgewise,
% torsion, and distributed-load recovery.

clear; clc;
this_dir = fileparts(mfilename('fullpath'));
addpath(this_dir,'-begin');
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end

fprintf('Quiz-style cases A-E validation\n');

caseA_axial();
caseB_flapwise_tip_load();
caseC_edgewise_tip_load();
caseD_tip_torsion();
caseE_distributed_loads_with_f0();

fprintf('  PASS: quiz-style cases A-E complete.\n');

function caseA_axial()

L = 1.2; P = 800; E = 70e9; G = 26e9; A = 1.4e-3; Iy = 2e-6; Iz = 5e-6; J = 8e-7;
[mesh,props,material,K,dof] = cantilever_model(L,8,E,G,A,Iy,Iz,J);

F = zeros(dof.total_dofs,1);
F((mesh.n_nodes-1)*6 + 1) = P;
solution = solve_static_fea(K,F,apply_boundary_conditions(mesh,dof,1),dof);

expected = P*L/(E*A);
assert_scalar_close(solution.displacements(end,1),expected,1e-10,'Case A axial displacement');
fprintf('  Case A axial: PASS\n');

end

function caseB_flapwise_tip_load()

L = 0.9; P = 120; E = 70e9; G = 26e9; A = 1.4e-3; Iy = 1.8e-6; Iz = 4.5e-6; J = 8e-7;
[mesh,~,~,K,dof] = cantilever_model(L,8,E,G,A,Iy,Iz,J);

F = zeros(dof.total_dofs,1);
F((mesh.n_nodes-1)*6 + 3) = P;
solution = solve_static_fea(K,F,apply_boundary_conditions(mesh,dof,1),dof);

expected = P*L^3/(3*E*Iy);
assert_scalar_close(abs(solution.displacements(end,3)),expected,1e-10,'Case B flapwise displacement');
fprintf('  Case B flapwise tip load: PASS\n');

end

function caseC_edgewise_tip_load()

L = 0.9; P = 95; E = 70e9; G = 26e9; A = 1.4e-3; Iy = 1.8e-6; Iz = 4.5e-6; J = 8e-7;
[mesh,~,~,K,dof] = cantilever_model(L,8,E,G,A,Iy,Iz,J);

F = zeros(dof.total_dofs,1);
F((mesh.n_nodes-1)*6 + 2) = P;
solution = solve_static_fea(K,F,apply_boundary_conditions(mesh,dof,1),dof);

expected = P*L^3/(3*E*Iz);
assert_scalar_close(abs(solution.displacements(end,2)),expected,1e-10,'Case C edgewise displacement');
fprintf('  Case C edgewise tip load: PASS\n');

end

function caseD_tip_torsion()

L = 1.0; T_tip = 21; E = 70e9; G = 26e9; A = 1.4e-3; Iy = 1.8e-6; Iz = 4.5e-6; J = 8e-7;
[mesh,~,~,K,dof] = cantilever_model(L,8,E,G,A,Iy,Iz,J);

F = zeros(dof.total_dofs,1);
F((mesh.n_nodes-1)*6 + 4) = T_tip;
solution = solve_static_fea(K,F,apply_boundary_conditions(mesh,dof,1),dof);

expected = T_tip*L/(G*J);
assert_scalar_close(solution.displacements(end,4),expected,1e-10,'Case D torsional rotation');
fprintf('  Case D tip torsion: PASS\n');

end

function caseE_distributed_loads_with_f0()

L = 1.0; wz = 65; mx = 8; E = 70e9; G = 26e9; A = 1.4e-3; Iy = 1.8e-6; Iz = 4.5e-6; J = 8e-7;
[mesh,~,~,K,dof,element_data] = cantilever_model(L,2,E,G,A,Iy,Iz,J);

[~,f0e_local] = local_equivalent_load_3d_frame(0,0,wz,mx,L);
F0 = zeros(dof.total_dofs,1);
F0(element_data(1).edofs) = element_data(1).T'*f0e_local;

solution = solve_static_fea(K,zeros(dof.total_dofs,1),apply_boundary_conditions(mesh,dof,1),dof,F0);

expected_uz = wz*L^4/(8*E*Iy);
expected_rx = mx*L^2/(2*G*J);
assert_scalar_close(abs(solution.displacements(end,3)),expected_uz,1e-10,'Case E uniform wz displacement');
assert_scalar_close(abs(solution.displacements(end,4)),expected_rx,1e-10,'Case E custom distributed torsion');
fprintf('  Case E distributed f0 loads and custom mx: PASS\n');

end

function [mesh,props,material,K,dof,element_data] = cantilever_model(L,n_nodes,E,G,A,Iy,Iz,J)

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
props.mass_per_length_kg_m = zeros(n_nodes,1);
props.y_max_m = 0.05*ones(n_nodes,1);
props.z_max_m = 0.005*ones(n_nodes,1);
props.c_max_m = 0.05*ones(n_nodes,1);

material = struct('E',E,'G',G,'nu',0.3,'rho',0);
[K,element_data,dof] = assemble_global_stiffness(mesh,props,material);

end

function assert_scalar_close(actual,expected,tol,message)

scale = max(1,abs(expected));
err = abs(actual - expected)/scale;
if err > tol
    error('%s mismatch: actual %.12g, expected %.12g, relative error %.3g', ...
        message,actual,expected,err);
end

end
