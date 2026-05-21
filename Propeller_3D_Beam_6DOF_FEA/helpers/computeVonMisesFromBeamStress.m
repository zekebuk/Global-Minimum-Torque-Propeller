function point_stress = computeVonMisesFromBeamStress(N,My,Mz,T,A,Iyy,Izz,J,sectionPoints)
%COMPUTEVONMISESFROMBEAMSTRESS Section-point normal stress and von Mises.
%
% The local beam stress convention is:
%   sigma_x = N/A + My*z/Iyy + Mz*y/Izz
%
% Von Mises is evaluated at each supplied section point from the local normal
% stress plus torsional shear:
%   sigma_vm = sqrt(sigma_x^2 + 3*tau^2)
%
% No transverse normal or shear stresses are invented here.

if nargin < 9 || isempty(sectionPoints)
    sectionPoints = struct('name',{{'center'}},'y_m',0,'z_m',0);
end

y = sectionPoints.y_m(:);
z = sectionPoints.z_m(:);
n_points = numel(y);

if isfield(sectionPoints,'name') && numel(sectionPoints.name) == n_points
    point_names = sectionPoints.name(:);
    if isstring(point_names)
        point_names = cellstr(point_names);
    elseif ischar(point_names)
        point_names = cellstr(point_names);
    end
else
    point_names = arrayfun(@(i) sprintf('point %d',i),(1:n_points)', ...
        'UniformOutput',false);
end

if ~isfinite(A) || A <= 0 || ~isfinite(Iyy) || Iyy <= 0 || ...
        ~isfinite(Izz) || Izz <= 0
    point_stress = emptyPointStress(n_points,point_names);
    point_stress.note = 'Invalid A, Iyy, or Izz; stress envelope unavailable.';
    return
end

N = scalarOrZero(N);
My = scalarOrZero(My);
Mz = scalarOrZero(Mz);

sigma_x = beam_section_normal_stress(N,My,Mz,A,Iyy,Izz,y,z);

torsional_shear_assumed_zero = false;
if ~isfinite(T)
    T = 0;
    torsional_shear_assumed_zero = true;
end
if ~isfinite(J) || J <= 0
    tau = zeros(n_points,1);
    if abs(T) > 0
        torsional_shear_assumed_zero = true;
    end
else
    rho = sqrt(y.^2 + z.^2);
    tau = T*rho/J;
end

sigma_vm = sqrt(sigma_x.^2 + 3*tau.^2);
[sigma_vm_max,critical_idx] = max(sigma_vm);
sigma_vm_min = min(sigma_vm);

point_stress = struct();
point_stress.point_names = point_names;
point_stress.y_m = y;
point_stress.z_m = z;
point_stress.sigma_x_Pa = sigma_x;
point_stress.tau_torsion_Pa = tau;
point_stress.sigma_vm_Pa = sigma_vm;
point_stress.sigma_vm_max_Pa = sigma_vm_max;
point_stress.sigma_vm_min_Pa = sigma_vm_min;
point_stress.sigma_x_max_tension_Pa = max(sigma_x);
point_stress.sigma_x_max_compression_Pa = min(sigma_x);
point_stress.sigma_abs_normal_max_Pa = max(abs(sigma_x));
point_stress.tau_torsion_max_Pa = max(abs(tau));
point_stress.critical_section_point = point_names{critical_idx};
point_stress.critical_section_point_index = critical_idx;
point_stress.torsional_shear_assumed_zero = torsional_shear_assumed_zero;
point_stress.note = 'Von Mises max is computed from section-point sigma_x and torsional shear.';

if sigma_vm(critical_idx) + max(1,abs(sigma_x(critical_idx)))*1e-12 < abs(sigma_x(critical_idx))
    warning('Computed von Mises is smaller than the critical normal stress magnitude.');
end

end

function value = scalarOrZero(value)

if isempty(value) || ~isfinite(value)
    value = 0;
end

end

function point_stress = emptyPointStress(n_points,point_names)

nan_points = NaN(n_points,1);
point_stress = struct();
point_stress.point_names = point_names;
point_stress.y_m = nan_points;
point_stress.z_m = nan_points;
point_stress.sigma_x_Pa = nan_points;
point_stress.tau_torsion_Pa = nan_points;
point_stress.sigma_vm_Pa = nan_points;
point_stress.sigma_vm_max_Pa = NaN;
point_stress.sigma_vm_min_Pa = NaN;
point_stress.sigma_x_max_tension_Pa = NaN;
point_stress.sigma_x_max_compression_Pa = NaN;
point_stress.sigma_abs_normal_max_Pa = NaN;
point_stress.tau_torsion_max_Pa = NaN;
point_stress.critical_section_point = '';
point_stress.critical_section_point_index = NaN;
point_stress.torsional_shear_assumed_zero = true;
point_stress.note = '';

end
