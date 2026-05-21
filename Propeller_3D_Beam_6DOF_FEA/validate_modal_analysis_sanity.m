function validate_modal_analysis_sanity()
%VALIDATE_MODAL_ANALYSIS_SANITY Basic modal analysis checks.

this_dir = fileparts(mfilename('fullpath'));
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end

base = uniformBeamCase(1.0,70e9,2700,21);
assert(symmetryError(base.K) < 1e-10,'K must be symmetric.');
assert(symmetryError(base.M) < 1e-10,'M must be symmetric.');
modal = solveModalEigenproblem(base.K,base.M,base.bc,6,base.mesh,base.dof,struct());
assert(all(modal.frequencies_hz > 0),'Modal frequencies must be positive.');
assert(any(strcmp(modal.modeType(1:min(3,end)),'flapwise bending')) || ...
    any(strcmp(modal.modeType(1:min(3,end)),'edgewise bending')), ...
    'A bending mode should be among the first modes for a cantilever beam.');

stiff = uniformBeamCase(1.0,140e9,2700,21);
modal_stiff = solveModalEigenproblem(stiff.K,stiff.M,stiff.bc,1,stiff.mesh,stiff.dof,struct());
assert(modal_stiff.frequencies_hz(1) > modal.frequencies_hz(1), ...
    'Increasing E should increase frequency.');

dense = uniformBeamCase(1.0,70e9,5400,21);
modal_dense = solveModalEigenproblem(dense.K,dense.M,dense.bc,1,dense.mesh,dense.dof,struct());
assert(modal_dense.frequencies_hz(1) < modal.frequencies_hz(1), ...
    'Increasing density should decrease frequency.');

longer = uniformBeamCase(1.5,70e9,2700,21);
modal_longer = solveModalEigenproblem(longer.K,longer.M,longer.bc,1,longer.mesh,longer.dof,struct());
assert(modal_longer.frequencies_hz(1) < modal.frequencies_hz(1), ...
    'Increasing blade length should decrease frequency.');

free_root = base;
bc_none = struct('fixed_dofs',zeros(0,1),'free_dofs',(1:free_root.dof.total_dofs)', ...
    'values',zeros(0,1));
K_weak = free_root.K;
K_strong = free_root.K;
root_dofs = 1:6;
K_weak(root_dofs,root_dofs) = K_weak(root_dofs,root_dofs) + 1e5*eye(6);
K_strong(root_dofs,root_dofs) = K_strong(root_dofs,root_dofs) + 1e7*eye(6);
modal_weak = solveModalEigenproblem(K_weak,free_root.M,bc_none,1,free_root.mesh,free_root.dof,struct());
modal_strong = solveModalEigenproblem(K_strong,free_root.M,bc_none,1,free_root.mesh,free_root.dof,struct());
assert(modal_strong.frequencies_hz(1) > modal_weak.frequencies_hz(1), ...
    'Increasing root stiffness should increase frequency.');

fprintf('Modal analysis sanity checks passed.\n');

end

function err = symmetryError(A)

err = norm(A - A','fro')/max(norm(A,'fro'),eps);

end

function data = uniformBeamCase(L,E,rho,n_nodes)

mesh = struct();
mesh.n_nodes = n_nodes;
mesh.n_elements = n_nodes - 1;
mesh.radius_m = linspace(0,L,n_nodes)';
mesh.mu = mesh.radius_m/max(mesh.radius_m);
mesh.chord_m = 0.05*ones(n_nodes,1);
mesh.twist_deg = zeros(n_nodes,1);
mesh.thickness_ratio_from_csv = 0.10*ones(n_nodes,1);
mesh.nodes_global = [mesh.radius_m, zeros(n_nodes,1), zeros(n_nodes,1)];
mesh.connectivity = [(1:mesh.n_elements)', (2:mesh.n_nodes)'];
mesh.element_length_m = diff(mesh.radius_m);

material = struct('E',E,'nu',0.33,'rho',rho);
material.G = material.E/(2*(1 + material.nu));
props = section_properties_from_geometry(mesh,material,0.10);
[K,element_data,dof] = assemble_global_stiffness(mesh,props,material);
M = assembleGlobalMassMatrix(mesh,props,element_data,dof);
bc = apply_boundary_conditions(mesh,dof,1);

data = struct('mesh',mesh,'props',props,'material',material, ...
    'K',K,'M',M,'bc',bc,'dof',dof);

end
