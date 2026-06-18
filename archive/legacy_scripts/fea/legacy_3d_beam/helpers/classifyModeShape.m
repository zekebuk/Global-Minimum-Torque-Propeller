function info = classifyModeShape(mode_shape,mesh,dof)
%CLASSIFYMODESHAPE Approximate modal family from DOF participation.

if nargin < 3 || isempty(dof)
    dof.ndof_node = 6;
end

U = reshape(mode_shape,dof.ndof_node,[])';
span = max(mesh.radius_m) - min(mesh.radius_m);
if ~isfinite(span) || span <= 0
    span = 1;
end

axial = sum(U(:,1).^2);
edgewise = sum(U(:,2).^2) + sum((span*U(:,6)).^2);
flapwise = sum(U(:,3).^2) + sum((span*U(:,5)).^2);
torsion = sum((span*U(:,4)).^2);

values = [flapwise, edgewise, torsion, axial];
names = {'flapwise bending','edgewise bending','torsion','axial'};
[dominant,idx] = max(values);
total = sum(values);
if total <= eps
    mode_type = 'mixed mode';
    participation = zeros(1,4);
else
    participation = values/total;
    if dominant/total < 0.55
        mode_type = 'mixed mode';
    else
        mode_type = names{idx};
    end
end

disp_mag = sqrt(sum(U(:,1:3).^2,2));
[max_disp,node_idx] = max(disp_mag);
[~,component_idx] = max(abs(U(node_idx,1:6)));
dof_labels = {'ux','uy','uz','rx','ry','rz'};

info = struct();
info.mode_type = mode_type;
info.participation = participation;
info.energy_names = names;
info.max_displacement = max_disp;
info.max_displacement_node = node_idx;
info.max_displacement_r_over_R = mesh.mu(node_idx);
info.max_displacement_dof = dof_labels{component_idx};
info.max_component_value = U(node_idx,component_idx);

end
