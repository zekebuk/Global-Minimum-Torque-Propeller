function stress = recover_beam_stresses(mesh,props,forces)
%RECOVER_BEAM_STRESSES Approximate beam stress recovery.
%
% These are reduced-order beam stresses. Scalar component outputs are kept
% for diagnostic plots:
%   sigma_axial = N/A
%   sigma_flap  = My*z_max/Iy
%   sigma_edge  = Mz*y_max/Iz
%   tau_torsion = T*c_max/J
%
% The reported combined and von Mises stresses are section-point envelopes,
% not algebraic sums of those signed scalar components. This avoids hiding a
% critical surface stress when axial and bending signs cancel at one point.
%
% They do not represent local 3D stress, contact stress, bolt-hole stress,
% root fillet concentration, or detailed composite fiber stress.

A = max(props.A_m2(:),realmin);
Iy = max(props.Iy_m4(:),realmin);
Iz = max(props.Iz_m4(:),realmin);
J = max(props.J_m4(:),realmin);

sigma_axial = forces.N_N(:)./A;
sigma_flap = forces.My_bending_Nm(:).*props.z_max_m(:)./Iy;
sigma_edge = forces.Mz_bending_Nm(:).*props.y_max_m(:)./Iz;
tau_torsion = forces.Mx_torsion_Nm(:).*props.c_max_m(:)./J;
sigma_combined_signed = sigma_axial + sigma_flap + sigma_edge;

stress_input = struct();
stress_input.N = forces.N_N(:);
stress_input.My = forces.My_bending_Nm(:);
stress_input.Mz = forces.Mz_bending_Nm(:);
stress_input.T = forces.Mx_torsion_Nm(:);
stress_input.A = A;
stress_input.Iy = Iy;
stress_input.Iz = Iz;
stress_input.J = J;
envelope = computeSectionStressEnvelope(stress_input,props,[]);

sigma_von_mises = envelope.sigma_vm_max_Pa;
sigma_max_tensile = envelope.sigma_x_max_tension_Pa;
sigma_max_compressive = envelope.sigma_x_max_compression_Pa;
sigma_combined = envelope.sigma_abs_normal_max_Pa;

stress = struct();
stress.sigma_axial_Pa = sigma_axial;
stress.sigma_flap_Pa = sigma_flap;
stress.sigma_edge_Pa = sigma_edge;
stress.tau_torsion_Pa = tau_torsion;
stress.sigma_combined_Pa = sigma_combined;
stress.sigma_combined_signed_Pa = sigma_combined_signed;
stress.sigma_von_mises_Pa = sigma_von_mises;
stress.sigma_vm_max_Pa = envelope.sigma_vm_max_Pa;
stress.sigma_vm_min_Pa = envelope.sigma_vm_min_Pa;
stress.sigma_max_tensile_Pa = sigma_max_tensile;
stress.sigma_max_compressive_Pa = sigma_max_compressive;
stress.sigma_x_max_tension_Pa = envelope.sigma_x_max_tension_Pa;
stress.sigma_x_max_compression_Pa = envelope.sigma_x_max_compression_Pa;
stress.sigma_abs_normal_max_Pa = envelope.sigma_abs_normal_max_Pa;
stress.tau_torsion_envelope_Pa = envelope.tau_torsion_max_Pa;
stress.critical_section_point = envelope.critical_section_point;
stress.critical_section_point_index = envelope.critical_section_point_index;
stress.torsional_shear_assumed_zero = envelope.torsional_shear_assumed_zero;
stress.note = ['Approximate reduced-order beam stress, not local 3D stress. ', ...
    'Combined stress is max(abs(sigma_x)) over critical section points; ', ...
    'von Mises is max(sqrt(sigma_x^2 + 3*tau^2)) over those points.'];
stress.nodal_projection_note = ...
    'Station stresses are projected from adjacent element resultants for smoother visualization only.';
stress.r_over_R = mesh.mu(:);

if isfield(forces,'element_mid_resultants_local') && isfield(forces,'element_node_ids')
    stress.element_one_point = element_one_point_stress(props,forces);
end

end

function element_stress = element_one_point_stress(props,forces)

n_elem = size(forces.element_mid_resultants_local,1);
sigma_center = NaN(n_elem,1);
sigma_top = NaN(n_elem,1);
sigma_bottom = NaN(n_elem,1);
sigma_leading = NaN(n_elem,1);
sigma_trailing = NaN(n_elem,1);
sigma_abs_max = NaN(n_elem,1);
critical_point = repmat({''},n_elem,1);

names = {'center','top surface','bottom surface','leading edge side','trailing edge side'};

for e = 1:n_elem
    node_ids = forces.element_node_ids(e,:);
    A_e = mean(props.A_m2(node_ids));
    Iy_e = mean(props.Iy_m4(node_ids));
    Iz_e = mean(props.Iz_m4(node_ids));
    y_e = mean(props.y_max_m(node_ids));
    z_e = mean(props.z_max_m(node_ids));

    resultants = forces.element_mid_resultants_local(e,:);
    N = resultants(1);
    My = resultants(5);
    Mz = resultants(6);

    y_points = [0; 0; 0; y_e; -y_e];
    z_points = [0; z_e; -z_e; 0; 0];
    sigma = beam_section_normal_stress(N,My,Mz,A_e,Iy_e,Iz_e,y_points,z_points);

    sigma_center(e) = sigma(1);
    sigma_top(e) = sigma(2);
    sigma_bottom(e) = sigma(3);
    sigma_leading(e) = sigma(4);
    sigma_trailing(e) = sigma(5);
    [sigma_abs_max(e),idx] = max(abs(sigma));
    critical_point{e} = names{idx};
end

element_stress = struct();
element_stress.r_mid_m = forces.element_mid_radius_m;
element_stress.sigma_center_Pa = sigma_center;
element_stress.sigma_top_Pa = sigma_top;
element_stress.sigma_bottom_Pa = sigma_bottom;
element_stress.sigma_leading_Pa = sigma_leading;
element_stress.sigma_trailing_Pa = sigma_trailing;
element_stress.sigma_abs_max_Pa = sigma_abs_max;
element_stress.critical_point = critical_point;
element_stress.note = ['One-point element stress evaluation at element midpoint. ', ...
    'Nodal projection remains visualization only.'];

end
