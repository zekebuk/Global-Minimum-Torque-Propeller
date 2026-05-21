function modalResults = solveModalEigenproblem(K,M,bc,numModes,mesh,dof,options)
%SOLVEMODALEIGENPROBLEM Solve K*phi = omega^2*M*phi for free DOFs.
%
% Gyroscopic effects are neglected in this reduced modal analysis.

if nargin < 4 || isempty(numModes)
    numModes = 6;
end
if nargin < 7 || isempty(options)
    options = struct();
end
if ~isfield(options,'normalization')
    options.normalization = 'max_displacement';
end

reduced = applyModalBoundaryConditions(K,M,bc);
Kff = (reduced.K + reduced.K')/2;
Mff = (reduced.M + reduced.M')/2;
numModesRequested = max(1,round(numModes));
numFree = size(Kff,1);
numSolve = min(max(numModesRequested + 6, numModesRequested), max(numFree - 2, 1));

warnings = reduced.warnings;

try
    if numFree > 8 && numSolve < numFree
        try
            [V,D] = eigs(Kff,Mff,numSolve,'smallestabs');
        catch
            [V,D] = eigs(Kff,Mff,numSolve,'sm');
        end
        solver_name = 'eigs';
    else
        [V,D] = eig(full(Kff),full(Mff));
        solver_name = 'eig';
    end
catch ME
    warnings{end+1} = sprintf('eigs failed; used full eig instead (%s).',ME.message);
    [V,D] = eig(full(Kff),full(Mff));
    solver_name = 'eig fallback';
end

lambda = diag(D);
valid = isfinite(real(lambda)) & abs(imag(lambda)) < 1e-6*max(1,abs(real(lambda))) & real(lambda) > 0;
lambda = real(lambda(valid));
V = real(V(:,valid));

[lambda,order] = sort(lambda,'ascend');
V = V(:,order);

if isempty(lambda)
    error('No positive real modal eigenvalues were found.');
end

numKeep = min(numModesRequested,numel(lambda));
lambda = lambda(1:numKeep);
V = V(:,1:numKeep);

modeShapes = reconstructFullModeShapes(V,reduced.free_dofs,reduced.total_dofs);
[modeShapes,generalizedMass,generalizedStiffness] = normalizeModes(modeShapes,K,M,options.normalization,dof);

omega = sqrt(lambda(:));
freq = omega/(2*pi);
modeType = cell(numKeep,1);
maxNode = NaN(numKeep,1);
maxMu = NaN(numKeep,1);
maxDof = cell(numKeep,1);
participation = NaN(numKeep,4);
for i = 1:numKeep
    info = classifyModeShape(modeShapes(:,i),mesh,dof);
    modeType{i} = info.mode_type;
    maxNode(i) = info.max_displacement_node;
    maxMu(i) = info.max_displacement_r_over_R;
    maxDof{i} = info.max_displacement_dof;
    participation(i,:) = info.participation;
end

if any(~isfinite(freq) | freq <= 0)
    warnings{end+1} = 'One or more modal frequencies were non-positive or non-finite after filtering.';
end

modalResults = struct();
modalResults.frequencies_hz = freq;
modalResults.frequencies_Hz = freq;
modalResults.omega_rad_s = omega;
modalResults.modeShapes = modeShapes;
modalResults.mode_shapes = modeShapes;
modalResults.modeType = modeType;
modalResults.generalizedMass = generalizedMass;
modalResults.generalizedStiffness = generalizedStiffness;
modalResults.eigenvalues = lambda;
modalResults.normalization = options.normalization;
modalResults.solver = solver_name;
modalResults.boundary_condition = 'clamped root; constrained DOFs removed before eigen solve';
modalResults.gyroscopic_note = 'Gyroscopic effects are neglected in this reduced modal analysis.';
modalResults.mass_positive_definite = reduced.mass_positive_definite;
modalResults.symmetry = reduced.symmetry;
modalResults.max_displacement_node = maxNode;
modalResults.max_displacement_r_over_R = maxMu;
modalResults.max_displacement_dof = maxDof;
modalResults.participation = participation;
modalResults.warnings = warnings;

end

function [modeShapes,generalizedMass,generalizedStiffness] = normalizeModes(modeShapes,K,M,normalization,dof)

numModes = size(modeShapes,2);
generalizedMass = NaN(numModes,1);
generalizedStiffness = NaN(numModes,1);

for i = 1:numModes
    phi = modeShapes(:,i);
    if strcmpi(normalization,'mass')
        gm = phi'*M*phi;
        if gm > 0
            phi = phi/sqrt(gm);
        end
    else
        U = reshape(phi,dof.ndof_node,[])';
        disp_mag = sqrt(sum(U(:,1:3).^2,2));
        scale = max(disp_mag);
        if ~isfinite(scale) || scale <= eps
            scale = max(abs(phi));
        end
        if isfinite(scale) && scale > eps
            phi = phi/scale;
        end
    end
    modeShapes(:,i) = phi;
    generalizedMass(i) = phi'*M*phi;
    generalizedStiffness(i) = phi'*K*phi;
end

end
