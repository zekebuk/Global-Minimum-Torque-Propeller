%% validate_chapter10_frame_theory
% Validate the custom propeller beam element against the Chapter 10
% three-dimensional frame formulation.

clear; clc;
this_dir = fileparts(mfilename('fullpath'));
addpath(this_dir,'-begin');
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end

fprintf('Chapter 10 3D frame validation\n');

validate_local_stiffness_matrix();
validate_transform_and_load_vector();
validate_chapter10_printed_example();

fprintf('  PASS: Chapter 10 frame formulation checks complete.\n');

function validate_local_stiffness_matrix()

E = 70e9;
G = 26e9;
A = 1.7e-4;
Iy = 2.1e-8;
Iz = 4.4e-8;
J = 1.2e-8;
L = 0.37;

k = local_stiffness_3d_beam(E,G,A,Iy,Iz,J,L);
k_ref = chapter10_reference_stiffness(E,G,A,Iy,Iz,J,L);

assert_close(k,k_ref,1e-12,'local stiffness matrix does not match Chapter 10 coefficients');
assert_close(k,k',1e-12,'local stiffness matrix is not symmetric');

lambda = sort(eig((k + k')/2));
if nnz(abs(lambda) < 1e-5*max(lambda)) < 6
    error('Local free-free element does not show the six expected rigid-body modes.');
end

fprintf('  local stiffness matrix: PASS\n');

end

function validate_transform_and_load_vector()

x_i = [1.0 2.0 -0.5];
x_j = [1.8 1.7 0.3];
theta_deg = 17;
[B,R] = transformation_matrix_3d_beam(x_i,x_j,theta_deg);

assert_close(R*R',eye(3),1e-12,'local/global direction cosine matrix is not orthonormal');
for block = 1:4
    idx = (block-1)*3 + (1:3);
    assert_close(B(idx,idx),R,1e-12,'12x12 B matrix does not repeat the 3x3 direction cosine matrix');
end

L = norm(x_j - x_i);
wx = 11;
wy = -7;
wz = 5;
mx = 3;
[fe_local,f0e_local] = local_equivalent_load_3d_frame(wx,wy,wz,mx,L);

f0_ref = zeros(12,1);
f0_ref(1) = -wx*L/2;
f0_ref(7) = -wx*L/2;
f0_ref(2) = -wy*L/2;
f0_ref(8) = -wy*L/2;
f0_ref(3) = -wz*L/2;
f0_ref(9) = -wz*L/2;
f0_ref(5) =  wz*L^2/12;
f0_ref(11) = -wz*L^2/12;
f0_ref(6) = -wy*L^2/12;
f0_ref(12) = wy*L^2/12;
f0_ref(4) = -mx*L/2;
f0_ref(10) = -mx*L/2;

assert_close(f0e_local,f0_ref,1e-12,'local fixed-end load vector does not match Chapter 10 plus custom torsion');
assert_close(f0e_local,-fe_local,1e-12,'fixed-end and applied equivalent load vectors are not opposite');

fprintf('  transform and fixed-end loads: PASS\n');

end

function validate_chapter10_printed_example()

% Chapter 10 Example 10.1 units: kip, inch.
E = 29000;
nu = 0;
G = E/(2*(1 + nu));
A = 2.3750;
Iy = 1.129557292;
Iz = 6.174479167;
J = 0.04947916667;

mesh = struct();
mesh.nodes_global = [100 0 0; 100 -100 0; 100 0 -100; 0 0 0];
mesh.n_nodes = 4;
mesh.n_elements = 3;
mesh.connectivity = [1 2; 1 3; 4 1];
mesh.twist_deg = zeros(mesh.n_nodes,1);
mesh.radius_m = mesh.nodes_global(:,1);
mesh.mu = zeros(mesh.n_nodes,1);
mesh.chord_m = ones(mesh.n_nodes,1);
mesh.thickness_ratio_from_csv = 0.1*ones(mesh.n_nodes,1);

props = struct();
props.A_m2 = A*ones(mesh.n_nodes,1);
props.Iy_m4 = Iy*ones(mesh.n_nodes,1);
props.Iz_m4 = Iz*ones(mesh.n_nodes,1);
props.J_m4 = J*ones(mesh.n_nodes,1);
props.mass_per_length_kg_m = zeros(mesh.n_nodes,1);
material = struct('E',E,'G',G,'nu',nu,'rho',0);

[K,~,dof] = assemble_global_stiffness(mesh,props,material);

F = zeros(dof.total_dofs,1);
F(1:6) = [10; 20; 30; 0; 0; 0];

bc.fixed_node_ids = [2; 3; 4];
bc.fixed_dofs = ((2-1)*6 + (1:6))';
bc.fixed_dofs = [bc.fixed_dofs; ((3-1)*6 + (1:6))'; ((4-1)*6 + (1:6))'];
bc.fixed_dofs = unique(bc.fixed_dofs);
bc.free_dofs = setdiff((1:dof.total_dofs)',bc.fixed_dofs);
bc.values = zeros(numel(bc.fixed_dofs),1);

solution = solve_static_fea(K,F,bc,dof);
Uf_expected = [1.444e-02; 2.892e-02; 4.351e-02; -2.655e-04; -2.174e-04; 1.085e-04];
rel_err = max(abs(solution.U(1:6) - Uf_expected)./max(1,abs(Uf_expected)));
if rel_err > 5e-3
    error('Chapter 10 printed example displacement mismatch: max relative error %.3g',rel_err);
end

fprintf('  Chapter 10 Example 10.1 displacement check: PASS\n');

end

function k = chapter10_reference_stiffness(E,G,A,Iy,Iz,J,L)

k = zeros(12,12);
k([1 7],[1 7]) = E*A/L*[1 -1; -1 1];
k([4 10],[4 10]) = G*J/L*[1 -1; -1 1];
k([3 5 9 11],[3 5 9 11]) = E*Iy/L^3 * [ ...
     12,   -6*L,  -12,   -6*L;
    -6*L, 4*L^2,  6*L, 2*L^2;
    -12,    6*L,   12,    6*L;
    -6*L, 2*L^2,  6*L, 4*L^2];
k([2 6 8 12],[2 6 8 12]) = E*Iz/L^3 * [ ...
      12,    6*L,  -12,    6*L;
     6*L,  4*L^2, -6*L,  2*L^2;
     -12,   -6*L,   12,   -6*L;
     6*L,  2*L^2, -6*L,  4*L^2];
k = (k + k')/2;

end

function assert_close(actual,expected,tol,message)

scale = max(1,norm(expected,'fro'));
err = norm(actual - expected,'fro')/scale;
if err > tol
    error('%s. Relative error %.3g exceeds %.3g.',message,err,tol);
end

end
