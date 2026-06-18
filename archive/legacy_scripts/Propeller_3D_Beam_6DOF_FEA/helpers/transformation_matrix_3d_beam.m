function [T,R,axes] = transformation_matrix_3d_beam(x_i,x_j,beta_deg)
%TRANSFORMATION_MATRIX_3D_BEAM Build 12x12 local/global transform.
%
% The transform is defined as:
%   u_local = T*u_global
%   k_global = T'*k_local*T
%   f_global = T'*f_local
%
% beta_deg is the Chapter 10 theta angle: a rotation of the local y/z axes
% about local x. The 3x3 block below follows the Chapter 10 direction-cosine
% formula, including the special case for an element parallel to global y.

if nargin < 3 || isempty(beta_deg)
    beta_deg = 0;
end

x_i = x_i(:)';
x_j = x_j(:)';
delta = x_j - x_i;
dx = delta(1);
dy = delta(2);
dz = delta(3);
L = norm(delta);
if L <= 0
    error('Cannot build transformation for zero-length element.');
end

cx = dx/L;
cy = dy/L;
cz = dz/L;
lxz = sqrt(dx^2 + dz^2);

theta = beta_deg*pi/180;
ct = cos(theta);
st = sin(theta);

R = zeros(3,3);
R(1,:) = [cx cy cz];

if lxz < 1e-12*L
    R(2,:) = [-cy*ct, 0, st];
    R(3,:) = [ cy*st, 0, ct];
else
    R(2,1) = -ct*dy*dx/(L*lxz) - st*dz/lxz;
    R(2,2) =  ct*lxz/L;
    R(2,3) = -ct*dy*dz/(L*lxz) + st*dx/lxz;

    R(3,1) =  st*dy*dx/(L*lxz) - ct*dz/lxz;
    R(3,2) = -st*lxz/L;
    R(3,3) =  st*dy*dz/(L*lxz) + ct*dx/lxz;
end

% Remove small numerical drift so B'*k*B remains exactly symmetric enough
% for downstream checks.
[u,~,v] = svd(R);
R = u*v';
if det(R) < 0
    R(3,:) = -R(3,:);
end

T = zeros(12,12);
for node = 1:2
    idx = (node-1)*6 + (1:3);
    ridx = (node-1)*6 + (4:6);
    T(idx,idx) = R;
    T(ridx,ridx) = R;
end

axes = struct();
axes.ex = R(1,:);
axes.ey = R(2,:);
axes.ez = R(3,:);
axes.beta_deg = beta_deg;
axes.chapter10_transform = true;

end
