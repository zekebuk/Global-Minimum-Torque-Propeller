function modal = run_modal_analysis(K,M,bc,num_modes,mesh,dof,options)
%RUN_MODAL_ANALYSIS Solve K*phi = omega^2*M*phi for free DOFs.
%
% Backward-compatible wrapper around solveModalEigenproblem.m. Gyroscopic
% effects are neglected in this reduced modal analysis.

this_dir = fileparts(mfilename('fullpath'));
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end

if nargin < 4 || isempty(num_modes)
    num_modes = 6;
end

if nargin < 5 || isempty(mesh)
    mesh = fallbackMesh(size(K,1));
end
if nargin < 6 || isempty(dof)
    dof.ndof_node = 6;
    dof.total_dofs = size(K,1);
end
if nargin < 7
    options = struct();
end

modal = solveModalEigenproblem(K,M,bc,num_modes,mesh,dof,options);
modal.note = 'Modal result from 3D beam mass matrix; validate before design use.';

fprintf('\nBasic modal analysis frequencies:\n');
for i = 1:numel(modal.frequencies_hz)
    fprintf('  mode %d: %.4f Hz (%s)\n',i,modal.frequencies_hz(i),modal.modeType{i});
end

end

function mesh = fallbackMesh(total_dofs)

n = total_dofs/6;
mesh = struct();
mesh.n_nodes = n;
mesh.radius_m = (1:n)';
mesh.mu = mesh.radius_m/max(mesh.radius_m);

end
