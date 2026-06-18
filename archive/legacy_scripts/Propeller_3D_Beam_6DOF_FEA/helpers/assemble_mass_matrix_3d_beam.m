function M = assemble_mass_matrix_3d_beam(mesh,props,element_data,dof)
%ASSEMBLE_MASS_MATRIX_3D_BEAM Backward-compatible modal mass entry point.
%
% The default now uses the consistent 3D beam mass assembly in
% assembleGlobalMassMatrix.m. Pass options.mass_matrix_type = 'lumped' to
% assembleGlobalMassMatrix directly if a lumped fallback is desired.

M = assembleGlobalMassMatrix(mesh,props,element_data,dof);

end
