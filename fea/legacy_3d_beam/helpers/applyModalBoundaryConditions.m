function reduced = applyModalBoundaryConditions(K,M,bc)
%APPLYMODALBOUNDARYCONDITIONS Remove constrained DOFs from K and M.

free = bc.free_dofs(:);
fixed = bc.fixed_dofs(:);

reduced = struct();
reduced.K = K(free,free);
reduced.M = M(free,free);
reduced.free_dofs = free;
reduced.fixed_dofs = fixed;
reduced.total_dofs = size(K,1);
reduced.symmetry.K_error = norm(K - K','fro')/max(norm(K,'fro'),eps);
reduced.symmetry.M_error = norm(M - M','fro')/max(norm(M,'fro'),eps);
reduced.warnings = {};

if reduced.symmetry.K_error > 1e-9
    reduced.warnings{end+1} = sprintf('K symmetry relative error is %.3g.',reduced.symmetry.K_error);
end
if reduced.symmetry.M_error > 1e-9
    reduced.warnings{end+1} = sprintf('M symmetry relative error is %.3g.',reduced.symmetry.M_error);
end

try
    chol((reduced.M + reduced.M')/2);
    reduced.mass_positive_definite = true;
catch
    reduced.mass_positive_definite = false;
    eig_min = min(real(eig(full((reduced.M + reduced.M')/2))));
    reduced.warnings{end+1} = sprintf('Reduced M is not Cholesky positive definite; min eigenvalue %.3g.',eig_min);
end

end
