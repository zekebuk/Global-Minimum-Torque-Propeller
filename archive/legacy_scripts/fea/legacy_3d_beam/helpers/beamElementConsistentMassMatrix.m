function M_local = beamElementConsistentMassMatrix(rho,A,Iy,Iz,J,L,options)
%BEAMELEMENTCONSISTENTMASSMATRIX 12x12 local mass matrix for 3D beam element.
%
% DOF order:
%   [ux uy uz rx ry rz] at node i, then [ux uy uz rx ry rz] at node j.
%
% The translational mass uses standard Euler-Bernoulli consistent mass
% terms for axial and transverse bending DOFs. Rotary inertia is included as
% a simple consistent two-node interpolation for rx, ry, and rz using rho*J,
% rho*Iy, and rho*Iz per unit length. Gyroscopic effects are neglected in
% this reduced modal analysis.

if nargin < 7 || isempty(options)
    options = struct();
end
if ~isfield(options,'include_rotary_inertia')
    options.include_rotary_inertia = true;
end

if L <= 0
    error('Element length must be positive.');
end

rhoA = rho*A;
M_local = zeros(12,12);

% Axial translation, ux.
M_axial = rhoA*L/6*[2 1; 1 2];
M_local([1 7],[1 7]) = M_local([1 7],[1 7]) + M_axial;

% Bending in local x-y plane: [uy1 rz1 uy2 rz2].
M_bend = rhoA*L/420*[ ...
    156,    22*L,   54,   -13*L; ...
    22*L, 4*L^2, 13*L, -3*L^2; ...
     54,    13*L,  156,   -22*L; ...
   -13*L, -3*L^2, -22*L, 4*L^2];
M_local([2 6 8 12],[2 6 8 12]) = M_local([2 6 8 12],[2 6 8 12]) + M_bend;

% Bending in local x-z plane: [uz1 ry1 uz2 ry2].
% The ry sign convention in local_stiffness_3d_beam is opposite the common
% textbook slope convention, so the standard mass matrix is sign-adjusted.
S = diag([1 -1 1 -1]);
M_bend_z = S*M_bend*S;
M_local([3 5 9 11],[3 5 9 11]) = M_local([3 5 9 11],[3 5 9 11]) + M_bend_z;

if options.include_rotary_inertia
    M_rx = rho*J*L/6*[2 1; 1 2];
    M_ry = rho*Iy*L/6*[2 1; 1 2];
    M_rz = rho*Iz*L/6*[2 1; 1 2];
    M_local([4 10],[4 10]) = M_local([4 10],[4 10]) + M_rx;
    M_local([5 11],[5 11]) = M_local([5 11],[5 11]) + M_ry;
    M_local([6 12],[6 12]) = M_local([6 12],[6 12]) + M_rz;
end

M_local = (M_local + M_local')/2;

end
