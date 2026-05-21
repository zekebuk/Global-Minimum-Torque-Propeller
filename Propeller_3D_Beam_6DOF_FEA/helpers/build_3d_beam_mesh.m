function mesh = build_3d_beam_mesh(geom,radius_m,num_nodes)
%BUILD_3D_BEAM_MESH Build a spanwise 3D beam mesh from geometry stations.
%
% Nodes are placed along the global x axis at the blade radial stations.
% The local element x axis points from root to tip. Local y/z axes are
% assigned during element transformation using average blade twist.

if nargin < 2 || isempty(radius_m)
    radius_m = geom.radius_tip_m;
end
if nargin < 3
    num_nodes = [];
end

r_in = geom.radius_m(:);
chord_in = geom.chord_m(:);
twist_in = geom.twist_deg(:);
tc_in = geom.thickness_ratio(:);

if isempty(num_nodes) || num_nodes <= 0 || num_nodes == numel(r_in)
    r = r_in;
    chord = chord_in;
    twist = twist_in;
    tc = tc_in;
else
    num_nodes = max(2,round(num_nodes));
    r = linspace(min(r_in),max(r_in),num_nodes)';
    chord = interp1(r_in,chord_in,r,'linear','extrap');
    twist = interp1(r_in,twist_in,r,'linear','extrap');
    tc = interp1(r_in,tc_in,r,'nearest','extrap');
end

mesh = struct();
mesh.radius_tip_m = radius_m;
mesh.radius_m = r(:);
mesh.mu = mesh.radius_m/radius_m;
mesh.chord_m = chord(:);
mesh.twist_deg = twist(:);
mesh.thickness_ratio_from_csv = tc(:);
mesh.nodes_global = [mesh.radius_m, zeros(numel(r),1), zeros(numel(r),1)];
mesh.n_nodes = numel(r);
mesh.n_elements = mesh.n_nodes - 1;
mesh.connectivity = [(1:mesh.n_elements)', (2:mesh.n_nodes)'];
mesh.element_length_m = diff(mesh.radius_m);
mesh.root_radius_m = mesh.radius_m(1);
mesh.blade_span_modeled_m = mesh.radius_m(end) - mesh.radius_m(1);

if isfield(geom,'airfoil')
    mesh.airfoil = interpolate_text_station(geom.radius_m(:),geom.airfoil,r);
else
    mesh.airfoil = repmat({''},mesh.n_nodes,1);
end

if any(mesh.element_length_m <= 0)
    error('Radial stations must increase monotonically from root to tip.');
end

end

function out = interpolate_text_station(r_old,text_old,r_new)

out = cell(numel(r_new),1);
for i = 1:numel(r_new)
    [~,idx] = min(abs(r_old - r_new(i)));
    out{i} = text_old{idx};
end

end

