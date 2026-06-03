function bc = apply_boundary_conditions(mesh,dof,fixed_node_ids)
%APPLY_BOUNDARY_CONDITIONS Fully fix selected nodes.
%
% Default is a clamped root node with all 6 DOF fixed.

if nargin < 3 || isempty(fixed_node_ids)
    fixed_node_ids = 1;
end

fixed_dofs = [];
for i = 1:numel(fixed_node_ids)
    node_id = fixed_node_ids(i);
    fixed_dofs = [fixed_dofs, (node_id-1)*dof.ndof_node + (1:dof.ndof_node)]; %#ok<AGROW>
end

all_dofs = 1:dof.total_dofs;
free_dofs = setdiff(all_dofs,unique(fixed_dofs));

bc = struct();
bc.fixed_node_ids = fixed_node_ids(:);
bc.fixed_dofs = unique(fixed_dofs(:));
bc.free_dofs = free_dofs(:);
bc.values = zeros(numel(bc.fixed_dofs),1);
bc.description = 'Fully clamped root node: ux=uy=uz=rx=ry=rz=0';
bc.root_radius_m = mesh.radius_m(fixed_node_ids(1));

end

