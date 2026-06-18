function [fe_local,f0e_local,components] = local_equivalent_load_3d_frame(wx,wy,wz,mx,L)
%LOCAL_EQUIVALENT_LOAD_3D_FRAME Local equivalent loads for a 3D frame element.
%
% DOF order:
%   [ux uy uz rx ry rz] at node i, then [ux uy uz rx ry rz] at node j.
%
% wx, wy, and wz are distributed forces per unit length in local x, y, and z.
% mx is a custom distributed torsional moment per unit length about local x.
% Each input can be scalar or a two-entry vector [node_i node_j]. Scalars are
% treated as constant along the element. The returned fe_local is the applied
% equivalent nodal load vector. The Chapter 10 restoring/fixed-end vector is
% f0e_local = -fe_local, so local force recovery is:
%
%   f_local = k_local*u_local + f0e_local

if L <= 0
    error('Element length must be positive.');
end

wx = end_values(wx);
wy = end_values(wy);
wz = end_values(wz);
mx = end_values(mx);

fe_local = zeros(12,1);
fe_local([1 7]) = consistent_bar_load(wx(1),wx(2),L);
fe_local([2 6 8 12]) = consistent_beam_load_y(wy(1),wy(2),L);
fe_local([3 5 9 11]) = consistent_beam_load_z(wz(1),wz(2),L);

% Chapter 10 does not include a distributed torsional moment. The propeller
% model needs it for aerodynamic-center offsets, so treat mx as a consistent
% line moment applied to local rx at both element nodes.
fe_local([4 10]) = consistent_bar_load(mx(1),mx(2),L);

f0e_local = -fe_local;

components = struct();
components.wx_N_m = wx;
components.wy_N_m = wy;
components.wz_N_m = wz;
components.mx_Nm_m = mx;
components.torsion_load_note = ...
    'mx is a custom equivalent nodal torsion load, not part of the Chapter 10 wx/wy/wz vector.';

end

function values = end_values(value)

if isempty(value)
    values = [0; 0];
elseif isscalar(value)
    values = [value; value];
else
    value = value(:);
    if numel(value) ~= 2
        error('Distributed load input must be scalar or two end values.');
    end
    values = value;
end

end

function fe = consistent_bar_load(q1,q2,L)

fe = L/6*[2*q1 + q2; q1 + 2*q2];

end

function fe = consistent_beam_load_y(q1,q2,L)

fe = consistent_hermite_load(q1,q2,L);

end

function fe = consistent_beam_load_z(q1,q2,L)

base = consistent_hermite_load(q1,q2,L);
fe = [base(1); -base(2); base(3); -base(4)];

end

function fe = consistent_hermite_load(q1,q2,L)

xi = [0.1127016653792583; 0.5; 0.8872983346207417];
w = [5/18; 8/18; 5/18];
fe = zeros(4,1);
for i = 1:3
    s = xi(i);
    H = [1 - 3*s^2 + 2*s^3;
         L*(s - 2*s^2 + s^3);
         3*s^2 - 2*s^3;
         L*(-s^2 + s^3)];
    q = (1 - s)*q1 + s*q2;
    fe = fe + H*q*L*w(i);
end

end
