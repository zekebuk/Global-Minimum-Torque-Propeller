function sigma = beam_section_normal_stress(N,My,Mz,A,Iy,Iz,y,z)
%BEAM_SECTION_NORMAL_STRESS Normal stress from 3D frame resultants.
%
% Chapter 10 beam-section convention used in this project:
%   sigma = N/A + My*z/Iy + Mz*y/Iz

if A <= 0 || Iy <= 0 || Iz <= 0
    sigma = NaN(size(y));
    return
end

sigma = N./A + My.*z./Iy + Mz.*y./Iz;

end
