function solution = solve_static_fea(K,F,bc,dof,F0)
%SOLVE_STATIC_FEA Solve the partitioned Chapter 10 static frame equations.
%
% Governing form:
%   K*U + F0 = F
%
% Partitioned solution:
%   Uf = Kff \ (Ff - F0f - Kfr*Ur)
%
% If F0 is omitted, it is treated as zero. Existing calls that assemble all
% distributed loads directly into F remain valid.

if nargin < 4 || isempty(dof)
    dof.ndof_node = 6;
    dof.total_dofs = size(K,1);
end
if nargin < 5 || isempty(F0)
    F0 = zeros(size(F));
end

U = zeros(dof.total_dofs,1);
free = bc.free_dofs;
fixed = bc.fixed_dofs;
Ur = zeros(numel(fixed),1);
if isfield(bc,'values') && ~isempty(bc.values)
    Ur = bc.values(:);
end

Kff = K(free,free);
Kfr = K(free,fixed);
Ff = F(free);
F0f = F0(free);

U(fixed) = Ur;
U(free) = Kff\(Ff - F0f - Kfr*Ur);

reactions = K*U + F0 - F;

solution = struct();
solution.U = U;
solution.displacements = reshape(U,dof.ndof_node,[])';
solution.reactions = reactions;
solution.fixed_reactions = reactions(fixed);
solution.free_residual_norm = norm(K(free,:)*U + F0(free) - F(free));
solution.condition_estimate = condest(sparse(Kff));
solution.F_external = F;
solution.F0_global = F0;
solution.partition = struct('free_dofs',free(:),'fixed_dofs',fixed(:), ...
    'Kff',Kff,'Kfr',Kfr,'Ff',Ff,'F0f',F0f,'Ur',Ur);
solution.solve_note = ...
    'Solved with Uf = Kff \\ (Ff - F0f - Kfr*Ur); reactions = K*U + F0 - F.';

end
