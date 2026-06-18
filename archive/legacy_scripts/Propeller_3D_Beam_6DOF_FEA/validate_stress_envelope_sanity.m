function validate_stress_envelope_sanity()
%VALIDATE_STRESS_ENVELOPE_SANITY Basic checks for beam stress envelope logic.

this_dir = fileparts(mfilename('fullpath'));
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end

points = struct();
points.name = {'top'; 'bottom'; 'lead'; 'trail'; 'top lead'; 'top trail'; 'bottom lead'; 'bottom trail'}';
points.y_m = [0; 0; 1; -1; 1; -1; 1; -1];
points.z_m = [1; -1; 0; 0; 1; 1; -1; -1];

s = computeVonMisesFromBeamStress(0,0,0,0,1,1,1,1,points);
assert(max(abs(s.sigma_vm_Pa)) == 0,'Zero loads should produce zero von Mises.');

s = computeVonMisesFromBeamStress(10,0,0,0,2,1,1,1,points);
assert(abs(s.sigma_vm_max_Pa - 5) < 1e-12,'Pure axial von Mises should equal abs(N/A).');

s = computeVonMisesFromBeamStress(0,0,7,0,1,1,1,1,points);
assert(abs(s.sigma_vm_max_Pa - 7) < 1e-12,'Pure Mz bending should match max surface stress.');

s = computeVonMisesFromBeamStress(0,-11,0,0,1,1,1,1,points);
assert(abs(s.sigma_vm_max_Pa - 11) < 1e-12,'Pure My bending should match max surface stress.');
assert(all(abs(s.sigma_vm_Pa - abs(s.sigma_x_Pa)) < 1e-12), ...
    'With zero torsion, von Mises should equal abs(sigma_x) pointwise.');

s = computeVonMisesFromBeamStress(11.16e6,-12.80e6,0.48e6,0,1,1,1,1,points);
assert(s.sigma_vm_max_Pa/1e6 > 20, ...
    'Root-style axial/bending case should not collapse to the cancelled algebraic value.');

s = computeVonMisesFromBeamStress(4,3,2,5,1,1,1,1,points);
critical = s.critical_section_point_index;
assert(s.sigma_vm_Pa(critical) >= abs(s.sigma_x_Pa(critical)), ...
    'Critical von Mises must be at least the local normal stress magnitude.');

fprintf('Stress envelope sanity checks passed.\n');

end
