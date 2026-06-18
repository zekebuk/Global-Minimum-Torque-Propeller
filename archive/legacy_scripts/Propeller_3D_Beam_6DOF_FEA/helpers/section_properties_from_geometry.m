function props = section_properties_from_geometry(mesh,material,default_thickness_ratio,section)
%SECTION_PROPERTIES_FROM_GEOMETRY Approximate reduced-order beam properties.
%
% Assumption:
% The airfoil is approximated as a solid-equivalent rectangular section
% with chord c in local y and thickness t in local z.
%
%   A  ~= area_factor*c*t
%   Iy ~= inertia_factor*c*t^3/12   (flapwise bending, weak axis)
%   Iz ~= inertia_factor*t*c^3/12   (edgewise bending, strong axis)
%   J  ~= torsion_factor*thin-rectangle torsion estimate
%
% These are screening-level section properties and are not local 3D stress
% or composite laminate stress predictions.

if nargin < 3 || isempty(default_thickness_ratio)
    default_thickness_ratio = 0.10;
end
if nargin < 4 || isempty(section)
    section = struct();
end
if ~isfield(section,'area_factor')
    section.area_factor = 0.65;
end
if ~isfield(section,'inertia_factor')
    section.inertia_factor = 0.75;
end
if ~isfield(section,'torsion_factor')
    section.torsion_factor = 0.50;
end

c = mesh.chord_m(:);
t_over_c = mesh.thickness_ratio_from_csv(:);
missing_tc = ~isfinite(t_over_c) | t_over_c <= 0;
t_over_c(missing_tc) = default_thickness_ratio;
t = t_over_c.*c;

A = section.area_factor*c.*t;
Iy = section.inertia_factor*c.*t.^3/12;
Iz = section.inertia_factor*t.*c.^3/12;

aspect = max(t./c,1e-6);
J_rect = (1/3)*c.*t.^3.*max(0.05,1 - 0.63*aspect + 0.052*aspect.^5);
J = section.torsion_factor*J_rect;

props = struct();
props.thickness_ratio = t_over_c;
props.thickness_m = t;
props.A_m2 = A;
props.Iy_m4 = Iy;
props.Iz_m4 = Iz;
props.J_m4 = J;
props.mass_per_length_kg_m = material.rho*A;
props.y_max_m = c/2;
props.z_max_m = t/2;
props.c_max_m = 0.5*sqrt(c.^2 + t.^2);
props.E_Pa = material.E;
props.nu = material.nu;
props.G_Pa = material.G;
props.rho_kg_m3 = material.rho;
props.section_assumption = 'solid-equivalent airfoil rectangle with empirical area/inertia/torsion factors';

end

