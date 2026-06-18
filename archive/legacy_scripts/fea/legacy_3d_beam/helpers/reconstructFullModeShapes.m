function full_modes = reconstructFullModeShapes(reduced_modes,free_dofs,total_dofs)
%RECONSTRUCTFULLMODESHAPES Insert zero rows at constrained DOFs.

full_modes = zeros(total_dofs,size(reduced_modes,2));
full_modes(free_dofs,:) = reduced_modes;

end
