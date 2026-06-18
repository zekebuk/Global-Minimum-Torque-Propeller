function k = local_stiffness_3d_beam(E,G,A,Iy,Iz,J,L)
%LOCAL_STIFFNESS_3D_BEAM Chapter 10 12x12 local 3D frame stiffness matrix.
%
% DOF order:
%   [ux uy uz rx ry rz] at node i, then [ux uy uz rx ry rz] at node j.
%
% Local axes:
%   x = spanwise element axis
%   y = chordwise / edgewise axis
%   z = thickness / flapwise axis
%
% Terms follow the standard 3D frame formulation:
%   EA/L, GJ/L, 12EI/L^3, 6EI/L^2, 4EI/L, 2EI/L.

if L <= 0
    error('Element length must be positive.');
end

k = zeros(12,12);

EA_L = E*A/L;
GJ_L = G*J/L;

% Axial extension.
k([1 7],[1 7]) = EA_L*[1 -1; -1 1];

% Torsion about local x.
k([4 10],[4 10]) = GJ_L*[1 -1; -1 1];

% Bending in local x-z plane, moment about local y, stiffness EIy.
ky = E*Iy/L^3 * [ ...
     12,   -6*L,  -12,   -6*L;
    -6*L, 4*L^2,  6*L, 2*L^2;
    -12,    6*L,   12,    6*L;
    -6*L, 2*L^2,  6*L, 4*L^2];
k([3 5 9 11],[3 5 9 11]) = ky;

% Bending in local x-y plane, moment about local z, stiffness EIz.
kz = E*Iz/L^3 * [ ...
      12,    6*L,  -12,    6*L;
     6*L,  4*L^2, -6*L,  2*L^2;
     -12,   -6*L,   12,   -6*L;
     6*L,  2*L^2, -6*L,  4*L^2];
k([2 6 8 12],[2 6 8 12]) = kz;

k = (k + k')/2;

end
