%% composite_material_model
% Thesis composite material modeling utility for candidate propeller blade layups.
%
% Thesis framing:
% 1. Classical Lamination Theory (CLT) is appropriate for continuous fiber
%    laminates with known ply orientations, thicknesses, and lamina properties.
% 2. Forged/chopped carbon has discontinuous/random fiber architecture, so CLT
%    assumptions do not strictly apply. Coupon-tested homogenized properties
%    should be used as the primary FEA input for forged carbon.
% 3. These estimated outputs can support reduced-order beam FEA and preliminary
%    Abaqus material cards, but they are not by themselves final proof of
%    propeller blade structural safety.
%
% Units used throughout this file:
%   stiffness/stress/strength: Pa
%   length/thickness/z:        m
%   force resultants:          N/m
%   moment resultants:         N-m/m
%   density:                   kg/m^3
%   mass per area:             kg/m^2
%
% Stacking sequence convention:
%   layup_deg is ordered from the bottom surface (z = -h/2) to the top surface
%   (z = +h/2). Symmetric examples are insensitive to reversal for ABD coupling.

clear;
clc;
close all;

scriptDirectory = fileparts(mfilename('fullpath'));
projectRoot = fileparts(fileparts(scriptDirectory));
resultsDirectory = fullfile(projectRoot, 'results', 'composites');
plotDirectory = fullfile(projectRoot, 'results', 'plots', 'composites');
ensureFolder(resultsDirectory);
ensureFolder(plotDirectory);

fprintf('\nComposite material model for estimated thesis laminate properties\n');
fprintf('Output directory: %s\n', resultsDirectory);
fprintf('Plot directory:   %s\n\n', plotDirectory);

%% Continuous laminate mode demo inputs
continuousMaterial = struct();
continuousMaterial.name = 'Default carbon/epoxy continuous lamina';
continuousMaterial.E1 = 181e9;        % [Pa] longitudinal lamina Young's modulus
continuousMaterial.E2 = 10.3e9;       % [Pa] transverse lamina Young's modulus
continuousMaterial.nu12 = 0.28;       % [-] major Poisson's ratio
continuousMaterial.G12 = 7.17e9;      % [Pa] in-plane lamina shear modulus
continuousMaterial.density = 1600;    % [kg/m^3] lamina density

exampleStrengths = struct();
exampleStrengths.X_t = 1500e6;        % [Pa] longitudinal tensile strength
exampleStrengths.X_c = 1200e6;        % [Pa] longitudinal compressive strength
exampleStrengths.Y_t = 40e6;          % [Pa] transverse tensile strength
exampleStrengths.Y_c = 246e6;         % [Pa] transverse compressive strength
exampleStrengths.S = 68e6;            % [Pa] in-plane shear strength

exampleLayups = { ...
    [0 90 45 -45 -45 45 90 0], ...
    [0 0 45 -45 90 90 -45 45 0 0], ...
    [45 -45 0 90 90 0 -45 45]};
exampleLayupNames = { ...
    'quasi_sym_8ply', ...
    'axial_cap_10ply', ...
    'angle_balance_8ply'};

t_ply = 0.000125;                     % [m] thickness of each continuous ply
appliedMembraneLoad = [2500; 200; 75]; % [N/m] [Nx; Ny; Nxy] membrane load resultants
appliedMomentResultant = [8; 0.5; 0.2]; % [N-m/m] [Mx; My; Mxy] moment resultants

validateMaterial(continuousMaterial);
validateStrengths(exampleStrengths);
warnIfUnreasonableUnits(continuousMaterial, t_ply, exampleStrengths);

Q = buildQ( ...
    continuousMaterial.E1, ...         % [Pa] longitudinal lamina Young's modulus
    continuousMaterial.E2, ...         % [Pa] transverse lamina Young's modulus
    continuousMaterial.nu12, ...       % [-] major Poisson's ratio
    continuousMaterial.G12);           % [Pa] in-plane lamina shear modulus

laminateResults = repmat(struct(), numel(exampleLayups), 1);
summaryRows = initializeSummaryRows(numel(exampleLayups));

for caseIndex = 1:numel(exampleLayups)
    layup_deg = exampleLayups{caseIndex}; % [deg] ply angles, bottom surface to top surface
    validateLayup(layup_deg);

    [A, coupling_B, D, z, totalThickness, QbarByPly] = buildABD( ...
        layup_deg, ...                 % [deg] ply angles, bottom surface to top surface
        t_ply, ...                     % [m] scalar ply thickness or vector of ply thicknesses
        continuousMaterial);           % [SI struct] continuous lamina material properties

    engineeringConstants = laminateEngineeringConstants( ...
        A, ...                         % [N/m] extensional stiffness matrix
        coupling_B, ...                % [N] extension-bending coupling stiffness matrix
        D, ...                         % [N-m] bending stiffness matrix
        totalThickness);               % [m] total laminate thickness

    [eps0, kappa, ABD_matrix] = solveLaminateResponse( ...
        A, ...                         % [N/m] extensional stiffness matrix
        coupling_B, ...                % [N] extension-bending coupling stiffness matrix
        D, ...                         % [N-m] bending stiffness matrix
        appliedMembraneLoad, ...       % [N/m] [Nx; Ny; Nxy] membrane load resultants
        appliedMomentResultant);       % [N-m/m] [Mx; My; Mxy] moment resultants

    plyRecovery = recoverPlyStressStrain( ...
        Q, ...                         % [Pa] reduced lamina stiffness matrix in local axes
        layup_deg, ...                 % [deg] ply angles, bottom surface to top surface
        z, ...                         % [m] ply interface coordinates
        eps0, ...                      % [-] laminate mid-plane strains [epsx; epsy; gamxy]
        kappa);                        % [1/m] laminate curvatures [kx; ky; kxy]

    maxStressFI = zeros(numel(layup_deg), 1);
    tsaiHillFI = zeros(numel(layup_deg), 1);
    maxStressFIByPoint = zeros(3, numel(layup_deg));
    tsaiHillFIByPoint = zeros(3, numel(layup_deg));
    for plyIndex = 1:numel(layup_deg)
        for pointIndex = 1:3
            localStress = plyRecovery.localStressByPoint_Pa(:, pointIndex, plyIndex); % [Pa] [sigma1; sigma2; tau12]
            maxStressFIByPoint(pointIndex, plyIndex) = maxStressFailureIndex(localStress, exampleStrengths);
            tsaiHillFIByPoint(pointIndex, plyIndex) = tsaiHillFailureIndex(localStress, exampleStrengths);
        end
        maxStressFI(plyIndex) = max(maxStressFIByPoint(:, plyIndex));
        tsaiHillFI(plyIndex) = max(tsaiHillFIByPoint(:, plyIndex));
    end

    recommendedFeaInputs = buildLaminateFeaInputs( ...
        engineeringConstants, ...      % [SI struct] estimated laminate engineering constants
        continuousMaterial, ...        % [SI struct] source lamina material properties
        totalThickness);               % [m] total laminate thickness

    laminateResults(caseIndex).name = exampleLayupNames{caseIndex};
    laminateResults(caseIndex).mode = 'continuous_laminate_clt';
    laminateResults(caseIndex).layup_deg = layup_deg;
    laminateResults(caseIndex).ply_thickness_m = t_ply;
    laminateResults(caseIndex).total_thickness_m = totalThickness;
    laminateResults(caseIndex).density_kg_m3 = continuousMaterial.density;
    laminateResults(caseIndex).mass_per_area_kg_m2 = continuousMaterial.density * totalThickness;
    laminateResults(caseIndex).engineering_constants = engineeringConstants;
    laminateResults(caseIndex).A_N_per_m = A;
    laminateResults(caseIndex).B_N = coupling_B;
    laminateResults(caseIndex).D_N_m = D;
    laminateResults(caseIndex).ABD_matrix = ABD_matrix;
    laminateResults(caseIndex).z_m = z;
    laminateResults(caseIndex).Qbar_by_ply_Pa = matrixStackToCell(QbarByPly);
    laminateResults(caseIndex).midplane_strain = eps0;
    laminateResults(caseIndex).curvature_1_per_m = kappa;
    laminateResults(caseIndex).ply_recovery = plyRecovery;
    laminateResults(caseIndex).max_stress_failure_index = maxStressFI;
    laminateResults(caseIndex).tsai_hill_failure_index = tsaiHillFI;
    laminateResults(caseIndex).max_stress_failure_index_by_point = maxStressFIByPoint;
    laminateResults(caseIndex).tsai_hill_failure_index_by_point = tsaiHillFIByPoint;
    laminateResults(caseIndex).recommended_simplified_fea_inputs = recommendedFeaInputs;

    summaryRows = fillSummaryRows( ...
        summaryRows, ...               % [struct arrays] scalar/matrix summary storage
        caseIndex, ...                 % [-] case row index
        exampleLayupNames{caseIndex}, ...
        layup_deg, ...                 % [deg] ply angles
        totalThickness, ...            % [m] total laminate thickness
        continuousMaterial.density, ...% [kg/m^3] laminate density
        engineeringConstants, ...      % [SI struct] estimated laminate constants
        A, ...                         % [N/m] extensional stiffness matrix
        coupling_B, ...                % [N] extension-bending coupling stiffness matrix
        D);                            % [N-m] bending stiffness matrix

    fprintf('Estimated %-18s: Ex = %8.3f GPa, Ey = %8.3f GPa, Gxy = %7.3f GPa, h = %.6g m\n', ...
        exampleLayupNames{caseIndex}, ...
        engineeringConstants.Ex_Pa / 1e9, ...
        engineeringConstants.Ey_Pa / 1e9, ...
        engineeringConstants.Gxy_Pa / 1e9, ...
        totalThickness);
end

%% Forged/chopped-carbon coupon mode demo input
% Replace these values with measured coupon data for the exact manufacturing
% route, fiber/resin system, cure, and specimen orientation used in the thesis.
forgedCouponInput = struct();
forgedCouponInput.material_name = 'Example forged/chopped-carbon coupon';
forgedCouponInput.E_exp = 45e9;              % [Pa] measured homogenized Young's modulus
forgedCouponInput.nu_exp = 0.30;             % [-] measured homogenized Poisson's ratio
forgedCouponInput.G_exp = [];                % [Pa] optional measured shear modulus
forgedCouponInput.density_exp = 1450;        % [kg/m^3] measured coupon density
forgedCouponInput.UTS_exp = 300e6;           % [Pa] measured ultimate tensile strength
forgedCouponInput.UCS_exp = [];              % [Pa] optional measured compressive strength
forgedCouponInput.shear_strength = [];       % [Pa] optional measured shear strength

forgedCouponMaterialCard = buildForgedCouponMaterialCard(forgedCouponInput);

%% Validation and exports
validationResult = runQuiz5OffAxisValidation();
printValidationResult(validationResult);

propertySummaryTable = buildPropertySummaryTable(summaryRows);
writetable(propertySummaryTable, fullfile(resultsDirectory, 'laminate_property_summary.csv'));

jsonSummary = struct();
jsonSummary.generated_by = 'Code/composites/composite_material_model.m';
jsonSummary.generated_on = datestr(now, 30);
jsonSummary.units = buildUnitsStruct();
jsonSummary.continuous_laminate_results = laminateResults;
jsonSummary.quiz5_off_axis_validation = validationResult;
writeJsonFile(fullfile(resultsDirectory, 'laminate_property_summary.json'), jsonSummary);

exampleRecoveryTable = buildPlyRecoveryTable( ...
    laminateResults(1).name, ...                  % [-] example layup name
    laminateResults(1).layup_deg, ...             % [deg] ply angles
    laminateResults(1).ply_recovery, ...          % [SI struct] ply stress/strain recovery
    laminateResults(1).max_stress_failure_index_by_point, ...
    laminateResults(1).tsai_hill_failure_index_by_point);
writetable(exampleRecoveryTable, fullfile(resultsDirectory, 'ply_stress_recovery_example.csv'));

writeJsonFile(fullfile(resultsDirectory, 'forged_coupon_material_card.json'), forgedCouponMaterialCard);

plotLaminateEngineeringConstants(laminateResults, plotDirectory);
plotThroughThicknessStress(laminateResults(1), plotDirectory);
plotFailureIndex(laminateResults(1), plotDirectory);

fprintf('\nGenerated machine-readable outputs:\n');
fprintf('  %s\n', fullfile(resultsDirectory, 'laminate_property_summary.csv'));
fprintf('  %s\n', fullfile(resultsDirectory, 'laminate_property_summary.json'));
fprintf('  %s\n', fullfile(resultsDirectory, 'ply_stress_recovery_example.csv'));
fprintf('  %s\n', fullfile(resultsDirectory, 'forged_coupon_material_card.json'));
fprintf('\nGenerated plots:\n');
fprintf('  %s\n', fullfile(plotDirectory, 'laminate_engineering_constants.png'));
fprintf('  %s\n', fullfile(plotDirectory, 'through_thickness_local_stress.png'));
fprintf('  %s\n', fullfile(plotDirectory, 'ply_failure_indices.png'));
fprintf('\nAll printed laminate properties are estimated values for preliminary modeling.\n\n');

%% Local functions

function Q = buildQ(E1, E2, nu12, G12)
% buildQ Build reduced stiffness matrix for an orthotropic lamina.
% Inputs:
%   E1   [Pa] longitudinal lamina Young's modulus
%   E2   [Pa] transverse lamina Young's modulus
%   nu12 [-] major Poisson's ratio
%   G12  [Pa] in-plane lamina shear modulus
% Output:
%   Q    [Pa] 3-by-3 reduced stiffness matrix in local 1-2 axes

    validatePositiveScalar(E1, 'E1');
    validatePositiveScalar(E2, 'E2');
    validatePositiveScalar(G12, 'G12');
    if ~isscalar(nu12) || ~isnumeric(nu12) || ~isfinite(nu12)
        error('nu12 must be a finite numeric scalar.');
    end

    nu21 = nu12 * E2 / E1;
    denominator = 1 - nu12 * nu21;
    if denominator <= 0
        error('Invalid orthotropic lamina constants: 1 - nu12*nu21 must be positive.');
    end

    Q11 = E1 / denominator;
    Q22 = E2 / denominator;
    Q12 = nu12 * E2 / denominator;
    Q66 = G12;

    Q = [Q11, Q12, 0; ...
         Q12, Q22, 0; ...
         0,   0,   Q66];
end

function Qbar = transformQbar(Q, theta_deg)
% transformQbar Transform reduced stiffness matrix into laminate x-y axes.
% Inputs:
%   Q         [Pa] 3-by-3 reduced stiffness matrix in local 1-2 axes
%   theta_deg [deg] ply angle from global x-axis to local 1-axis
% Output:
%   Qbar      [Pa] 3-by-3 transformed reduced stiffness matrix in x-y axes

    if ~isequal(size(Q), [3, 3]) || ~isnumeric(Q) || any(~isfinite(Q(:)))
        error('Q must be a finite numeric 3-by-3 matrix.');
    end
    if ~isscalar(theta_deg) || ~isnumeric(theta_deg) || ~isfinite(theta_deg)
        error('theta_deg must be a finite numeric scalar.');
    end

    theta_rad = deg2rad(theta_deg);
    m = cos(theta_rad);
    n = sin(theta_rad);

    Q11 = Q(1, 1);
    Q12 = Q(1, 2);
    Q22 = Q(2, 2);
    Q66 = Q(3, 3);

    Qbar11 = Q11*m^4 + 2*(Q12 + 2*Q66)*m^2*n^2 + Q22*n^4;
    Qbar22 = Q11*n^4 + 2*(Q12 + 2*Q66)*m^2*n^2 + Q22*m^4;
    Qbar12 = (Q11 + Q22 - 4*Q66)*m^2*n^2 + Q12*(m^4 + n^4);
    Qbar16 = (Q11 - Q12 - 2*Q66)*m^3*n - (Q22 - Q12 - 2*Q66)*m*n^3;
    Qbar26 = (Q11 - Q12 - 2*Q66)*m*n^3 - (Q22 - Q12 - 2*Q66)*m^3*n;
    Qbar66 = (Q11 + Q22 - 2*Q12 - 2*Q66)*m^2*n^2 + Q66*(m^4 + n^4);

    Qbar = [Qbar11, Qbar12, Qbar16; ...
            Qbar12, Qbar22, Qbar26; ...
            Qbar16, Qbar26, Qbar66];
end

function [A, coupling_B, D, z, total_thickness, Qbar_by_ply] = buildABD(layup_deg, t_ply, material)
% buildABD Build Classical Lamination Theory A, B, and D stiffness matrices.
% Inputs:
%   layup_deg [deg] numeric ply angle vector ordered bottom surface to top surface
%   t_ply     [m] scalar ply thickness or vector of one thickness per ply
%   material  [SI struct] fields E1 [Pa], E2 [Pa], nu12 [-], G12 [Pa], density [kg/m^3]
% Outputs:
%   A                [N/m] extensional stiffness matrix
%   coupling_B       [N] extension-bending coupling stiffness matrix
%   D                [N-m] bending stiffness matrix
%   z                [m] ply interface coordinates from -h/2 to +h/2
%   total_thickness  [m] total laminate thickness
%   Qbar_by_ply      [Pa] 3-by-3-by-nply transformed stiffness stack

    validateLayup(layup_deg);
    validateMaterial(material);
    plyThicknesses = validatePlyThickness(t_ply, numel(layup_deg));

    Q = buildQ(material.E1, material.E2, material.nu12, material.G12);
    total_thickness = sum(plyThicknesses);
    z = [-total_thickness/2; -total_thickness/2 + cumsum(plyThicknesses(:))];

    A = zeros(3, 3);
    coupling_B = zeros(3, 3);
    D = zeros(3, 3);
    Qbar_by_ply = zeros(3, 3, numel(layup_deg));

    for plyIndex = 1:numel(layup_deg)
        Qbar = transformQbar(Q, layup_deg(plyIndex));
        zBottom = z(plyIndex);
        zTop = z(plyIndex + 1);

        A = A + Qbar * (zTop - zBottom);
        coupling_B = coupling_B + 0.5 * Qbar * (zTop^2 - zBottom^2);
        D = D + (1/3) * Qbar * (zTop^3 - zBottom^3);
        Qbar_by_ply(:, :, plyIndex) = Qbar;
    end
end

function engineeringConstants = laminateEngineeringConstants(A, coupling_B, D, total_thickness)
% laminateEngineeringConstants Estimate laminate engineering constants.
% Inputs:
%   A                [N/m] extensional stiffness matrix
%   coupling_B       [N] extension-bending coupling stiffness matrix
%   D                [N-m] bending stiffness matrix
%   total_thickness  [m] total laminate thickness
% Output:
%   engineeringConstants [SI struct] Ex [Pa], Ey [Pa], Gxy [Pa], nu_xy [-]

    validateABDInputs(A, coupling_B, D);
    validatePositiveScalar(total_thickness, 'total_thickness');

    ABD_matrix = [A, coupling_B; coupling_B, D];
    conditionNumber = cond(ABD_matrix);
    if conditionNumber > 1e10
        warning('ABD matrix is ill-conditioned: cond(ABD) = %.3e.', conditionNumber);
    end

    laminateCompliance = inv(ABD_matrix);
    extensionalCompliance = laminateCompliance(1:3, 1:3);

    engineeringConstants = struct();
    engineeringConstants.Ex_Pa = 1 / (total_thickness * extensionalCompliance(1, 1));
    engineeringConstants.Ey_Pa = 1 / (total_thickness * extensionalCompliance(2, 2));
    engineeringConstants.Gxy_Pa = 1 / (total_thickness * extensionalCompliance(3, 3));
    engineeringConstants.nu_xy = -extensionalCompliance(1, 2) / extensionalCompliance(1, 1);
    engineeringConstants.nu_yx = -extensionalCompliance(1, 2) / extensionalCompliance(2, 2);
    engineeringConstants.ABD_condition_number = conditionNumber;
    engineeringConstants.note = ['Estimated from the full ABD compliance matrix. ', ...
        'For unsymmetric laminates, extension-bending coupling is included.'];
end

function [eps0, kappa, ABD_matrix] = solveLaminateResponse(A, coupling_B, D, appliedMembraneLoad, appliedMomentResultant)
% solveLaminateResponse Solve the full coupled laminate response.
% Inputs:
%   A                       [N/m] extensional stiffness matrix
%   coupling_B              [N] extension-bending coupling stiffness matrix
%   D                       [N-m] bending stiffness matrix
%   appliedMembraneLoad     [N/m] [Nx; Ny; Nxy] membrane load resultants
%   appliedMomentResultant  [N-m/m] [Mx; My; Mxy] moment resultants
% Outputs:
%   eps0       [-] mid-plane strains [epsx; epsy; gamxy]
%   kappa      [1/m] curvatures [kx; ky; kxy]
%   ABD_matrix [mixed SI] full 6-by-6 CLT stiffness matrix

    validateABDInputs(A, coupling_B, D);
    validateVector3(appliedMembraneLoad, 'appliedMembraneLoad');
    validateVector3(appliedMomentResultant, 'appliedMomentResultant');

    ABD_matrix = [A, coupling_B; coupling_B, D];
    conditionNumber = cond(ABD_matrix);
    if conditionNumber > 1e10
        warning('ABD matrix is ill-conditioned before response solve: cond(ABD) = %.3e.', conditionNumber);
    end

    response = ABD_matrix \ [appliedMembraneLoad(:); appliedMomentResultant(:)];
    eps0 = response(1:3);
    kappa = response(4:6);
end

function plyRecovery = recoverPlyStressStrain(Q, layup_deg, z, eps0, kappa)
% recoverPlyStressStrain Recover local ply strain and stress at ply points.
% Inputs:
%   Q         [Pa] reduced lamina stiffness matrix in local 1-2 axes
%   layup_deg [deg] ply angle vector ordered bottom surface to top surface
%   z         [m] ply interface coordinates from -h/2 to +h/2
%   eps0      [-] mid-plane strains [epsx; epsy; gamxy]
%   kappa     [1/m] curvatures [kx; ky; kxy]
% Output:
%   plyRecovery [SI struct] bottom/mid/top local/global strains and stresses for each ply

    validateLayup(layup_deg);
    if numel(z) ~= numel(layup_deg) + 1 || ~isnumeric(z) || any(~isfinite(z(:)))
        error('z must contain one more finite coordinate than the number of plies.');
    end
    validateVector3(eps0, 'eps0');
    validateVector3(kappa, 'kappa');

    numberOfPlies = numel(layup_deg);
    zBottom = z(1:end-1);
    zTop = z(2:end);
    zMid = 0.5 * (zBottom + zTop);
    zByPoint = [zBottom(:).'; zMid(:).'; zTop(:).'];

    globalStrainByPoint = zeros(3, 3, numberOfPlies);
    localStrainByPoint = zeros(3, 3, numberOfPlies);
    globalStressByPoint = zeros(3, 3, numberOfPlies);
    localStressByPoint = zeros(3, 3, numberOfPlies);

    for plyIndex = 1:numberOfPlies
        theta_deg = layup_deg(plyIndex);
        Qbar = transformQbar(Q, theta_deg);
        for pointIndex = 1:3
            globalStrainByPoint(:, pointIndex, plyIndex) = eps0(:) + zByPoint(pointIndex, plyIndex) * kappa(:);
            localStrainByPoint(:, pointIndex, plyIndex) = transformGlobalStrainToLocal( ...
                globalStrainByPoint(:, pointIndex, plyIndex), theta_deg);
            localStressByPoint(:, pointIndex, plyIndex) = Q * localStrainByPoint(:, pointIndex, plyIndex);
            globalStressByPoint(:, pointIndex, plyIndex) = Qbar * globalStrainByPoint(:, pointIndex, plyIndex);
        end
    end

    plyRecovery = struct();
    plyRecovery.point_labels = {'bottom', 'mid', 'top'};
    plyRecovery.z_bottom_m = zBottom(:);
    plyRecovery.z_mid_m = zMid(:);
    plyRecovery.z_top_m = zTop(:);
    plyRecovery.zByPoint_m = zByPoint;
    plyRecovery.globalStrainByPoint = globalStrainByPoint;
    plyRecovery.localStrainByPoint = localStrainByPoint;
    plyRecovery.globalStressByPoint_Pa = globalStressByPoint;
    plyRecovery.localStressByPoint_Pa = localStressByPoint;
    plyRecovery.globalStrain = reshape(globalStrainByPoint(:, 2, :), 3, numberOfPlies);
    plyRecovery.localStrain = reshape(localStrainByPoint(:, 2, :), 3, numberOfPlies);
    plyRecovery.globalStress_Pa = reshape(globalStressByPoint(:, 2, :), 3, numberOfPlies);
    plyRecovery.localStress_Pa = reshape(localStressByPoint(:, 2, :), 3, numberOfPlies);
end

function failureIndex = maxStressFailureIndex(local_stress, strengths)
% maxStressFailureIndex Compute maximum-stress failure index for one ply point.
% Inputs:
%   local_stress [Pa] [sigma1; sigma2; tau12] local ply stress vector
%   strengths    [Pa struct] fields X_t, X_c, Y_t, Y_c, S
% Output:
%   failureIndex [-] max stress ratio; values >= 1 indicate predicted failure

    validateVector3(local_stress, 'local_stress');
    validateStrengths(strengths);

    sigma1 = local_stress(1);
    sigma2 = local_stress(2);
    tau12 = local_stress(3);

    if sigma1 >= 0
        ratio1 = sigma1 / strengths.X_t;
    else
        ratio1 = abs(sigma1) / strengths.X_c;
    end

    if sigma2 >= 0
        ratio2 = sigma2 / strengths.Y_t;
    else
        ratio2 = abs(sigma2) / strengths.Y_c;
    end

    ratio12 = abs(tau12) / strengths.S;
    failureIndex = max([ratio1, ratio2, ratio12]);
end

function failureIndex = tsaiHillFailureIndex(local_stress, strengths)
% tsaiHillFailureIndex Compute a plane-stress Tsai-Hill failure index.
% Inputs:
%   local_stress [Pa] [sigma1; sigma2; tau12] local ply stress vector
%   strengths    [Pa struct] fields X_t, X_c, Y_t, Y_c, S
% Output:
%   failureIndex [-] Tsai-Hill index; values >= 1 indicate predicted failure

    validateVector3(local_stress, 'local_stress');
    validateStrengths(strengths);

    sigma1 = local_stress(1);
    sigma2 = local_stress(2);
    tau12 = local_stress(3);

    if sigma1 >= 0
        X = strengths.X_t;
    else
        X = strengths.X_c;
    end

    if sigma2 >= 0
        Y = strengths.Y_t;
    else
        Y = strengths.Y_c;
    end

    failureIndex = (sigma1 / X)^2 - (sigma1 * sigma2) / X^2 + ...
        (sigma2 / Y)^2 + (tau12 / strengths.S)^2;
end

function localStrain = transformGlobalStrainToLocal(globalStrain, theta_deg)
% transformGlobalStrainToLocal Convert x-y engineering strain to 1-2 strain.
% Inputs:
%   globalStrain [strain] [epsx; epsy; gamxy] engineering strain vector
%   theta_deg    [deg] ply angle from global x-axis to local 1-axis
% Output:
%   localStrain  [strain] [eps1; eps2; gam12] engineering strain vector

    theta_rad = deg2rad(theta_deg);
    m = cos(theta_rad);
    n = sin(theta_rad);
    epsx = globalStrain(1);
    epsy = globalStrain(2);
    gamxy = globalStrain(3);

    eps1 = m^2 * epsx + n^2 * epsy + m*n * gamxy;
    eps2 = n^2 * epsx + m^2 * epsy - m*n * gamxy;
    gam12 = -2*m*n * epsx + 2*m*n * epsy + (m^2 - n^2) * gamxy;
    localStrain = [eps1; eps2; gam12];
end

function validationResult = runQuiz5OffAxisValidation()
% runQuiz5OffAxisValidation Validate off-axis lamina constants against Quiz 5.
% Inputs:
%   none; validation constants are embedded in SI units
% Output:
%   validationResult [struct] computed/expected engineering constants and pass flag

    E1 = 180e9;      % [Pa] Quiz 5 longitudinal lamina Young's modulus
    E2 = 10e9;       % [Pa] Quiz 5 transverse lamina Young's modulus
    nu12 = 0.25;     % [-] Quiz 5 major Poisson's ratio
    G12 = 7.17e9;    % [Pa] Quiz 5 in-plane shear modulus
    theta_deg = 10;  % [deg] Quiz 5 off-axis lamina angle

    Q = buildQ(E1, E2, nu12, G12);
    Qbar = transformQbar(Q, theta_deg);
    transformedCompliance = inv(Qbar);

    computed = struct();
    computed.Ex_Pa = 1 / transformedCompliance(1, 1);
    computed.Ey_Pa = 1 / transformedCompliance(2, 2);
    computed.Gxy_Pa = 1 / transformedCompliance(3, 3);
    computed.nu_xy = -transformedCompliance(1, 2) / transformedCompliance(1, 1);

    expected = struct();
    expected.Ex_Pa = 107.365e9;
    expected.Ey_Pa = 10.198e9;
    expected.Gxy_Pa = 7.362e9;
    expected.nu_xy = 0.2469;

    tolerances = struct();
    tolerances.Ex_Pa = 0.05e9;
    tolerances.Ey_Pa = 0.02e9;
    tolerances.Gxy_Pa = 0.02e9;
    tolerances.nu_xy = 5e-4;

    passEx = abs(computed.Ex_Pa - expected.Ex_Pa) <= tolerances.Ex_Pa;
    passEy = abs(computed.Ey_Pa - expected.Ey_Pa) <= tolerances.Ey_Pa;
    passGxy = abs(computed.Gxy_Pa - expected.Gxy_Pa) <= tolerances.Gxy_Pa;
    passNu = abs(computed.nu_xy - expected.nu_xy) <= tolerances.nu_xy;

    validationResult = struct();
    validationResult.case_name = 'Quiz 5 off-axis lamina, theta = 10 deg';
    validationResult.input_units = 'E1/E2/G12 in Pa, theta in degrees';
    validationResult.computed = computed;
    validationResult.expected = expected;
    validationResult.tolerances = tolerances;
    validationResult.pass = passEx && passEy && passGxy && passNu;
end

function printValidationResult(validationResult)
% printValidationResult Print validation status.
% Inputs:
%   validationResult [struct] output from runQuiz5OffAxisValidation
% Output:
%   none; text is printed to the MATLAB command window

    if validationResult.pass
        statusText = 'PASS';
    else
        statusText = 'FAIL';
    end

    fprintf('\nQuiz 5 off-axis lamina validation: %s\n', statusText);
    fprintf('  Ex:    computed %.3f GPa, expected %.3f GPa\n', ...
        validationResult.computed.Ex_Pa / 1e9, validationResult.expected.Ex_Pa / 1e9);
    fprintf('  Ey:    computed %.3f GPa, expected %.3f GPa\n', ...
        validationResult.computed.Ey_Pa / 1e9, validationResult.expected.Ey_Pa / 1e9);
    fprintf('  Gxy:   computed %.3f GPa, expected %.3f GPa\n', ...
        validationResult.computed.Gxy_Pa / 1e9, validationResult.expected.Gxy_Pa / 1e9);
    fprintf('  nu_xy: computed %.4f, expected %.4f\n\n', ...
        validationResult.computed.nu_xy, validationResult.expected.nu_xy);
end

function forgedCard = buildForgedCouponMaterialCard(coupon)
% buildForgedCouponMaterialCard Build homogenized forged/chopped-carbon card.
% Inputs:
%   coupon.material_name   [-] material/coupon label
%   coupon.E_exp           [Pa] measured homogenized Young's modulus
%   coupon.nu_exp          [-] measured homogenized Poisson's ratio
%   coupon.G_exp           [Pa] optional measured homogenized shear modulus
%   coupon.density_exp     [kg/m^3] measured coupon density
%   coupon.UTS_exp         [Pa] measured ultimate tensile strength
%   coupon.UCS_exp         [Pa] optional measured compressive strength
%   coupon.shear_strength  [Pa] optional measured shear strength
% Output:
%   forgedCard [SI struct] recommended homogenized material card inputs

    requiredFields = {'material_name', 'E_exp', 'nu_exp', 'density_exp', 'UTS_exp'};
    requireFields(coupon, requiredFields, 'coupon');
    validatePositiveScalar(coupon.E_exp, 'coupon.E_exp');
    validatePositiveScalar(coupon.density_exp, 'coupon.density_exp');
    validatePositiveScalar(coupon.UTS_exp, 'coupon.UTS_exp');
    if ~isscalar(coupon.nu_exp) || ~isnumeric(coupon.nu_exp) || ~isfinite(coupon.nu_exp)
        error('coupon.nu_exp must be a finite numeric scalar.');
    end

    if isfield(coupon, 'G_exp') && ~isempty(coupon.G_exp)
        validatePositiveScalar(coupon.G_exp, 'coupon.G_exp');
        shearModulus = coupon.G_exp;
        shearModulusSource = 'measured coupon input';
    else
        shearModulus = coupon.E_exp / (2 * (1 + coupon.nu_exp));
        shearModulusSource = 'estimated from E_exp/(2*(1+nu_exp)); replace with measured G_exp when available';
    end

    forgedCard = struct();
    forgedCard.mode = 'forged_chopped_carbon_coupon';
    forgedCard.material_name = coupon.material_name;
    forgedCard.is_CLT_applicable = false;
    forgedCard.modeling_comment = ['Forged/chopped carbon has discontinuous/random fiber architecture, ', ...
        'so CLT assumptions do not strictly apply. Experimentally measured coupon ', ...
        'properties should be used as the primary FEA input.'];
    forgedCard.elastic = struct();
    forgedCard.elastic.type = 'homogenized_coupon_isotropic_or_effective_orthotropic';
    forgedCard.elastic.E_Pa = coupon.E_exp;
    forgedCard.elastic.nu = coupon.nu_exp;
    forgedCard.elastic.G_Pa = shearModulus;
    forgedCard.elastic.G_source = shearModulusSource;
    forgedCard.density_kg_m3 = coupon.density_exp;
    forgedCard.strength = struct();
    forgedCard.strength.UTS_Pa = coupon.UTS_exp;

    if isfield(coupon, 'UCS_exp') && ~isempty(coupon.UCS_exp)
        validatePositiveScalar(coupon.UCS_exp, 'coupon.UCS_exp');
        forgedCard.strength.UCS_Pa = coupon.UCS_exp;
    else
        forgedCard.strength.UCS_Pa = [];
        forgedCard.strength.UCS_note = 'not supplied; measure compression coupons before compression-critical FEA allowables';
    end

    if isfield(coupon, 'shear_strength') && ~isempty(coupon.shear_strength)
        validatePositiveScalar(coupon.shear_strength, 'coupon.shear_strength');
        forgedCard.strength.shear_strength_Pa = coupon.shear_strength;
    else
        forgedCard.strength.shear_strength_Pa = [];
        forgedCard.strength.shear_note = 'not supplied; measure shear coupons before shear-critical FEA allowables';
    end

    forgedCard.recommended_simplified_fea_inputs = struct();
    forgedCard.recommended_simplified_fea_inputs.E_Pa = coupon.E_exp;
    forgedCard.recommended_simplified_fea_inputs.nu = coupon.nu_exp;
    forgedCard.recommended_simplified_fea_inputs.G_Pa = shearModulus;
    forgedCard.recommended_simplified_fea_inputs.density_kg_m3 = coupon.density_exp;
    forgedCard.recommended_simplified_fea_inputs.primary_strength_allowable_Pa = coupon.UTS_exp;
    forgedCard.recommended_simplified_fea_inputs.note = ['Use coupon-derived allowables with an appropriate safety factor ', ...
        'and manufacturing knockdowns; do not substitute continuous-ply CLT properties for forged carbon.'];
end

function feaInputs = buildLaminateFeaInputs(engineeringConstants, material, totalThickness)
% buildLaminateFeaInputs Build reduced-order FEA inputs from CLT estimates.
% Inputs:
%   engineeringConstants [SI struct] Ex [Pa], Ey [Pa], Gxy [Pa], nu_xy [-]
%   material             [SI struct] source lamina material with density [kg/m^3]
%   totalThickness       [m] total laminate thickness
% Output:
%   feaInputs [SI struct] estimated homogenized shell/beam material values

    feaInputs = struct();
    feaInputs.type = 'estimated_homogenized_continuous_laminate';
    feaInputs.Ex_Pa = engineeringConstants.Ex_Pa;
    feaInputs.Ey_Pa = engineeringConstants.Ey_Pa;
    feaInputs.Gxy_Pa = engineeringConstants.Gxy_Pa;
    feaInputs.nu_xy = engineeringConstants.nu_xy;
    feaInputs.nu_yx = engineeringConstants.nu_yx;
    feaInputs.density_kg_m3 = material.density;
    feaInputs.total_thickness_m = totalThickness;
    feaInputs.mass_per_area_kg_m2 = material.density * totalThickness;
    feaInputs.note = ['Estimated CLT homogenized inputs for preliminary shell or beam studies. ', ...
        'Use the full A/B/D matrices when coupling effects are important.'];
end

function rows = initializeSummaryRows(numberOfCases)
% initializeSummaryRows Allocate summary row storage.
% Inputs:
%   numberOfCases [-] number of laminate cases to summarize
% Output:
%   rows [struct] preallocated scalar and matrix fields

    rows.names = cell(numberOfCases, 1);
    rows.layupText = cell(numberOfCases, 1);
    rows.plyCount = zeros(numberOfCases, 1);
    rows.totalThickness_m = zeros(numberOfCases, 1);
    rows.density_kg_m3 = zeros(numberOfCases, 1);
    rows.massPerArea_kg_m2 = zeros(numberOfCases, 1);
    rows.Ex_Pa = zeros(numberOfCases, 1);
    rows.Ey_Pa = zeros(numberOfCases, 1);
    rows.Gxy_Pa = zeros(numberOfCases, 1);
    rows.nu_xy = zeros(numberOfCases, 1);
    rows.nu_yx = zeros(numberOfCases, 1);
    rows.ABD_condition_number = zeros(numberOfCases, 1);
    rows.matrixValues = zeros(numberOfCases, 27);
end

function rows = fillSummaryRows(rows, caseIndex, caseName, layup_deg, totalThickness, density, engineeringConstants, A, coupling_B, D)
% fillSummaryRows Store one laminate summary row.
% Inputs:
%   rows                 [struct] summary row storage
%   caseIndex            [-] case row index
%   caseName             [-] laminate case label
%   layup_deg            [deg] ply angle vector
%   totalThickness       [m] total laminate thickness
%   density              [kg/m^3] laminate density
%   engineeringConstants [SI struct] estimated laminate constants
%   A                    [N/m] extensional stiffness matrix
%   coupling_B           [N] extension-bending coupling stiffness matrix
%   D                    [N-m] bending stiffness matrix
% Output:
%   rows [struct] updated summary row storage

    rows.names{caseIndex} = caseName;
    rows.layupText{caseIndex} = mat2str(layup_deg);
    rows.plyCount(caseIndex) = numel(layup_deg);
    rows.totalThickness_m(caseIndex) = totalThickness;
    rows.density_kg_m3(caseIndex) = density;
    rows.massPerArea_kg_m2(caseIndex) = density * totalThickness;
    rows.Ex_Pa(caseIndex) = engineeringConstants.Ex_Pa;
    rows.Ey_Pa(caseIndex) = engineeringConstants.Ey_Pa;
    rows.Gxy_Pa(caseIndex) = engineeringConstants.Gxy_Pa;
    rows.nu_xy(caseIndex) = engineeringConstants.nu_xy;
    rows.nu_yx(caseIndex) = engineeringConstants.nu_yx;
    rows.ABD_condition_number(caseIndex) = engineeringConstants.ABD_condition_number;
    rows.matrixValues(caseIndex, :) = [reshape(A.', 1, []), reshape(coupling_B.', 1, []), reshape(D.', 1, [])];
end

function summaryTable = buildPropertySummaryTable(rows)
% buildPropertySummaryTable Convert summary rows to a machine-readable table.
% Inputs:
%   rows [struct] scalar and matrix laminate summary storage
% Output:
%   summaryTable [table] laminate summary table for CSV export

    scalarTable = table( ...
        rows.names, ...
        rows.layupText, ...
        rows.plyCount, ...
        rows.totalThickness_m, ...
        rows.density_kg_m3, ...
        rows.massPerArea_kg_m2, ...
        rows.Ex_Pa, ...
        rows.Ey_Pa, ...
        rows.Gxy_Pa, ...
        rows.nu_xy, ...
        rows.nu_yx, ...
        rows.ABD_condition_number, ...
        'VariableNames', { ...
            'layup_name', ...
            'layup_deg_bottom_to_top', ...
            'ply_count', ...
            'total_thickness_m', ...
            'density_kg_m3', ...
            'mass_per_area_kg_m2', ...
            'Ex_Pa', ...
            'Ey_Pa', ...
            'Gxy_Pa', ...
            'nu_xy', ...
            'nu_yx', ...
            'ABD_condition_number'});

    matrixColumnNames = buildMatrixColumnNames();
    matrixTable = array2table(rows.matrixValues, 'VariableNames', matrixColumnNames);
    summaryTable = [scalarTable, matrixTable];
end

function names = buildMatrixColumnNames()
% buildMatrixColumnNames Build CSV column names for A, B, and D entries.
% Inputs:
%   none
% Output:
%   names [-] 1-by-27 cell array of valid table variable names

    prefixes = {'A_N_per_m', 'B_N', 'D_N_m'};
    names = cell(1, 27);
    columnIndex = 1;
    for prefixIndex = 1:numel(prefixes)
        for rowIndex = 1:3
            for columnNumber = 1:3
                names{columnIndex} = sprintf('%s_%d%d', prefixes{prefixIndex}, rowIndex, columnNumber);
                columnIndex = columnIndex + 1;
            end
        end
    end
end

function recoveryTable = buildPlyRecoveryTable(caseName, layup_deg, plyRecovery, maxStressFI, tsaiHillFI)
% buildPlyRecoveryTable Convert ply recovery arrays to a CSV table.
% Inputs:
%   caseName    [-] laminate case label
%   layup_deg   [deg] ply angle vector
%   plyRecovery [SI struct] ply recovery output
%   maxStressFI [-] maximum-stress failure index by ply point, 3-by-nply
%   tsaiHillFI  [-] Tsai-Hill failure index by ply point, 3-by-nply
% Output:
%   recoveryTable [table] per-ply stress/strain recovery table

    numberOfPlies = numel(layup_deg);
    numberOfPoints = 3;
    numberOfRows = numberOfPlies * numberOfPoints;

    caseColumn = repmat({caseName}, numberOfRows, 1);
    plyIndex = zeros(numberOfRows, 1);
    thetaColumn = zeros(numberOfRows, 1);
    pointLabelColumn = cell(numberOfRows, 1);
    zColumn = zeros(numberOfRows, 1);
    globalStrainRows = zeros(numberOfRows, 3);
    localStrainRows = zeros(numberOfRows, 3);
    localStressRows = zeros(numberOfRows, 3);
    globalStressRows = zeros(numberOfRows, 3);
    maxStressRows = zeros(numberOfRows, 1);
    tsaiHillRows = zeros(numberOfRows, 1);

    rowIndex = 0;
    for plyNumber = 1:numberOfPlies
        for pointNumber = 1:numberOfPoints
            rowIndex = rowIndex + 1;
            plyIndex(rowIndex) = plyNumber;
            thetaColumn(rowIndex) = layup_deg(plyNumber);
            pointLabelColumn{rowIndex} = plyRecovery.point_labels{pointNumber};
            zColumn(rowIndex) = plyRecovery.zByPoint_m(pointNumber, plyNumber);
            globalStrainRows(rowIndex, :) = plyRecovery.globalStrainByPoint(:, pointNumber, plyNumber).';
            localStrainRows(rowIndex, :) = plyRecovery.localStrainByPoint(:, pointNumber, plyNumber).';
            localStressRows(rowIndex, :) = plyRecovery.localStressByPoint_Pa(:, pointNumber, plyNumber).';
            globalStressRows(rowIndex, :) = plyRecovery.globalStressByPoint_Pa(:, pointNumber, plyNumber).';
            maxStressRows(rowIndex) = maxStressFI(pointNumber, plyNumber);
            tsaiHillRows(rowIndex) = tsaiHillFI(pointNumber, plyNumber);
        end
    end

    recoveryTable = table( ...
        caseColumn, ...
        plyIndex, ...
        thetaColumn, ...
        pointLabelColumn, ...
        zColumn, ...
        globalStrainRows(:, 1), ...
        globalStrainRows(:, 2), ...
        globalStrainRows(:, 3), ...
        localStrainRows(:, 1), ...
        localStrainRows(:, 2), ...
        localStrainRows(:, 3), ...
        localStressRows(:, 1), ...
        localStressRows(:, 2), ...
        localStressRows(:, 3), ...
        globalStressRows(:, 1), ...
        globalStressRows(:, 2), ...
        globalStressRows(:, 3), ...
        maxStressRows, ...
        tsaiHillRows, ...
        'VariableNames', { ...
            'layup_name', ...
            'ply_index_bottom_to_top', ...
            'theta_deg', ...
            'ply_point', ...
            'z_m', ...
            'eps_x', ...
            'eps_y', ...
            'gamma_xy', ...
            'eps_1', ...
            'eps_2', ...
            'gamma_12', ...
            'sigma_1_Pa', ...
            'sigma_2_Pa', ...
            'tau_12_Pa', ...
            'sigma_x_Pa', ...
            'sigma_y_Pa', ...
            'tau_xy_Pa', ...
            'max_stress_failure_index', ...
            'tsai_hill_failure_index'});
end

function plotLaminateEngineeringConstants(laminateResults, plotDirectory)
% plotLaminateEngineeringConstants Create bar plot of estimated Ex, Ey, Gxy.
% Inputs:
%   laminateResults [SI struct array] continuous laminate results
%   plotDirectory   [-] output folder path
% Output:
%   none; plot files are written to disk

    numberOfCases = numel(laminateResults);
    values_GPa = zeros(numberOfCases, 3);
    labels = cell(numberOfCases, 1);
    for caseIndex = 1:numberOfCases
        labels{caseIndex} = laminateResults(caseIndex).name;
        values_GPa(caseIndex, :) = [ ...
            laminateResults(caseIndex).engineering_constants.Ex_Pa, ...
            laminateResults(caseIndex).engineering_constants.Ey_Pa, ...
            laminateResults(caseIndex).engineering_constants.Gxy_Pa] / 1e9;
    end

    fig = figure('Visible', 'off', 'Color', 'w');
    bar(values_GPa);
    grid on;
    ylabel('Estimated engineering constant [GPa]');
    title('Continuous laminate CLT estimates');
    set(gca, 'XTick', 1:numberOfCases, 'XTickLabel', labels);
    legend({'E_x', 'E_y', 'G_{xy}'}, 'Location', 'northwest');
    saveFigure(fig, fullfile(plotDirectory, 'laminate_engineering_constants'));
    close(fig);
end

function plotThroughThicknessStress(laminateResult, plotDirectory)
% plotThroughThicknessStress Plot local ply stresses through thickness.
% Inputs:
%   laminateResult [SI struct] selected laminate result
%   plotDirectory  [-] output folder path
% Output:
%   none; plot files are written to disk

    localStress_MPa = reshape(laminateResult.ply_recovery.localStressByPoint_Pa, 3, []) / 1e6;
    z_m = laminateResult.ply_recovery.zByPoint_m(:);
    [z_m, sortIndex] = sort(z_m);
    localStress_MPa = localStress_MPa(:, sortIndex);

    fig = figure('Visible', 'off', 'Color', 'w');
    plot(localStress_MPa(1, :), z_m, '-o', 'LineWidth', 1.5, 'MarkerSize', 4);
    hold on;
    plot(localStress_MPa(2, :), z_m, '-s', 'LineWidth', 1.5, 'MarkerSize', 4);
    plot(localStress_MPa(3, :), z_m, '-^', 'LineWidth', 1.5, 'MarkerSize', 4);
    hold off;
    grid on;
    xlabel('Local ply stress [MPa]');
    ylabel('z location [m]');
    title(sprintf('Through-thickness local stress: %s', laminateResult.name), 'Interpreter', 'none');
    legend({'\sigma_1', '\sigma_2', '\tau_{12}'}, 'Location', 'best');
    saveFigure(fig, fullfile(plotDirectory, 'through_thickness_local_stress'));
    close(fig);
end

function plotFailureIndex(laminateResult, plotDirectory)
% plotFailureIndex Plot maximum-stress and Tsai-Hill failure indices by ply.
% Inputs:
%   laminateResult [SI struct] selected laminate result
%   plotDirectory  [-] output folder path
% Output:
%   none; plot files are written to disk

    numberOfPlies = numel(laminateResult.layup_deg);
    values = [ ...
        laminateResult.max_stress_failure_index(:), ...
        laminateResult.tsai_hill_failure_index(:)];

    fig = figure('Visible', 'off', 'Color', 'w');
    bar(1:numberOfPlies, values);
    hold on;
    plot([0.5, numberOfPlies + 0.5], [1, 1], 'k--', 'LineWidth', 1.2);
    hold off;
    grid on;
    xlabel('Ply index, bottom to top');
    ylabel('Failure index [-]');
    title(sprintf('Estimated failure indices: %s', laminateResult.name), 'Interpreter', 'none');
    legend({'Max stress', 'Tsai-Hill', 'FI = 1'}, 'Location', 'best');
    saveFigure(fig, fullfile(plotDirectory, 'ply_failure_indices'));
    close(fig);
end

function saveFigure(fig, fileBase)
% saveFigure Save figure as PNG and PDF.
% Inputs:
%   fig      [-] MATLAB figure handle
%   fileBase [-] output path without extension
% Output:
%   none; PNG and PDF files are written to disk

    set(fig, 'PaperPositionMode', 'auto');
    print(fig, [fileBase, '.png'], '-dpng', '-r300');
    try
        print(fig, [fileBase, '.pdf'], '-dpdf', '-bestfit');
    catch
        print(fig, [fileBase, '.pdf'], '-dpdf');
    end
end

function writeJsonFile(filePath, data)
% writeJsonFile Write a struct as JSON.
% Inputs:
%   filePath [-] output JSON file path
%   data     [-] MATLAB struct/cell/numeric data to encode
% Output:
%   none; JSON file is written to disk

    try
        jsonText = jsonencode(data, 'PrettyPrint', true);
    catch
        jsonText = jsonencode(data);
    end

    fid = fopen(filePath, 'w');
    if fid < 0
        error('Could not open JSON output file: %s', filePath);
    end
    cleanupObject = onCleanup(@() fclose(fid));
    fwrite(fid, jsonText, 'char');
    clear cleanupObject;
end

function units = buildUnitsStruct()
% buildUnitsStruct Define machine-readable unit notes.
% Inputs:
%   none
% Output:
%   units [struct] unit labels for exported quantities

    units = struct();
    units.modulus_stress_strength = 'Pa';
    units.length_thickness_z = 'm';
    units.force_resultant = 'N/m';
    units.moment_resultant = 'N-m/m';
    units.density = 'kg/m^3';
    units.mass_per_area = 'kg/m^2';
    units.angle = 'deg';
    units.failure_index = 'dimensionless';
end

function qbarCells = matrixStackToCell(matrixStack)
% matrixStackToCell Convert 3-D matrix stack to cell array for JSON clarity.
% Inputs:
%   matrixStack [numeric] 3-by-3-by-n matrix stack
% Output:
%   qbarCells [cell] 1-by-n cell array of 3-by-3 matrices

    numberOfMatrices = size(matrixStack, 3);
    qbarCells = cell(1, numberOfMatrices);
    for matrixIndex = 1:numberOfMatrices
        qbarCells{matrixIndex} = matrixStack(:, :, matrixIndex);
    end
end

function ensureFolder(folderPath)
% ensureFolder Create a folder if it does not exist.
% Inputs:
%   folderPath [-] folder path to create
% Output:
%   none

    if ~exist(folderPath, 'dir')
        mkdir(folderPath);
    end
end

function validateLayup(layup_deg)
% validateLayup Validate a ply angle vector.
% Inputs:
%   layup_deg [deg] numeric ply angle vector
% Output:
%   none; errors are thrown for invalid input

    if ~isnumeric(layup_deg) || isempty(layup_deg) || ~isvector(layup_deg) || any(~isfinite(layup_deg(:)))
        error('layup_deg must be a nonempty finite numeric vector of ply angles in degrees.');
    end
end

function plyThicknesses = validatePlyThickness(t_ply, numberOfPlies)
% validatePlyThickness Validate scalar or per-ply thickness input.
% Inputs:
%   t_ply          [m] scalar ply thickness or vector of one thickness per ply
%   numberOfPlies  [-] number of plies in the laminate
% Output:
%   plyThicknesses [m] column vector of ply thickness values

    if ~isnumeric(t_ply) || isempty(t_ply) || any(~isfinite(t_ply(:))) || any(t_ply(:) <= 0)
        error('t_ply must contain positive finite thickness values in meters.');
    end
    if isscalar(t_ply)
        plyThicknesses = repmat(t_ply, numberOfPlies, 1);
    elseif numel(t_ply) == numberOfPlies
        plyThicknesses = t_ply(:);
    else
        error('t_ply must be scalar or have one value per ply.');
    end

    if any(plyThicknesses > 0.002)
        warning('At least one ply thickness is greater than 0.002 m. Check that t_ply is in meters.');
    end
    if any(plyThicknesses < 1e-6)
        warning('At least one ply thickness is less than 1e-6 m. Check that t_ply is in meters.');
    end
end

function validateMaterial(material)
% validateMaterial Validate continuous lamina material fields.
% Inputs:
%   material [SI struct] E1 [Pa], E2 [Pa], nu12 [-], G12 [Pa], density [kg/m^3]
% Output:
%   none; errors are thrown for invalid input

    requireFields(material, {'E1', 'E2', 'nu12', 'G12', 'density'}, 'material');
    validatePositiveScalar(material.E1, 'material.E1');
    validatePositiveScalar(material.E2, 'material.E2');
    validatePositiveScalar(material.G12, 'material.G12');
    validatePositiveScalar(material.density, 'material.density');
    if ~isscalar(material.nu12) || ~isnumeric(material.nu12) || ~isfinite(material.nu12)
        error('material.nu12 must be a finite numeric scalar.');
    end
    if material.E1 < 1e7 || material.E2 < 1e7 || material.G12 < 1e7
        warning('One or more material stiffnesses are below 1e7 Pa. Check that inputs are in Pa.');
    end
    if material.density < 100 || material.density > 3000
        warning('material.density is outside 100-3000 kg/m^3. Check density units.');
    end
end

function validateStrengths(strengths)
% validateStrengths Validate ply strength fields.
% Inputs:
%   strengths [Pa struct] X_t, X_c, Y_t, Y_c, S positive strength fields
% Output:
%   none; errors are thrown for invalid input

    requireFields(strengths, {'X_t', 'X_c', 'Y_t', 'Y_c', 'S'}, 'strengths');
    validatePositiveScalar(strengths.X_t, 'strengths.X_t');
    validatePositiveScalar(strengths.X_c, 'strengths.X_c');
    validatePositiveScalar(strengths.Y_t, 'strengths.Y_t');
    validatePositiveScalar(strengths.Y_c, 'strengths.Y_c');
    validatePositiveScalar(strengths.S, 'strengths.S');
    if min([strengths.X_t, strengths.X_c, strengths.Y_t, strengths.Y_c, strengths.S]) < 1e6
        warning('At least one strength is below 1e6 Pa. Check that strengths are in Pa.');
    end
end

function warnIfUnreasonableUnits(material, t_ply, strengths)
% warnIfUnreasonableUnits Emit basic SI unit sanity warnings.
% Inputs:
%   material  [SI struct] continuous lamina material fields
%   t_ply     [m] scalar ply thickness or vector of ply thicknesses
%   strengths [Pa struct] ply strengths
% Output:
%   none; warnings are emitted when values look inconsistent with SI

    validatePlyThickness(t_ply, numel(t_ply));
    if material.E1 < material.E2
        warning('material.E1 is less than material.E2. This may be valid, but check lamina axes and units.');
    end
    if strengths.Y_t > strengths.X_t
        warning('strengths.Y_t is greater than strengths.X_t. Check ply strength axes and units.');
    end
end

function validateABDInputs(A, coupling_B, D)
% validateABDInputs Validate CLT stiffness matrices.
% Inputs:
%   A          [N/m] extensional stiffness matrix
%   coupling_B [N] extension-bending coupling stiffness matrix
%   D          [N-m] bending stiffness matrix
% Output:
%   none; errors are thrown for invalid input

    validateFiniteMatrix3(A, 'A');
    validateFiniteMatrix3(coupling_B, 'coupling_B');
    validateFiniteMatrix3(D, 'D');
end

function validateFiniteMatrix3(value, name)
% validateFiniteMatrix3 Validate a finite 3-by-3 numeric matrix.
% Inputs:
%   value [numeric] matrix to validate
%   name  [-] variable name used in error messages
% Output:
%   none; errors are thrown for invalid input

    if ~isequal(size(value), [3, 3]) || ~isnumeric(value) || any(~isfinite(value(:)))
        error('%s must be a finite numeric 3-by-3 matrix.', name);
    end
end

function validateVector3(value, name)
% validateVector3 Validate a finite three-component vector.
% Inputs:
%   value [numeric] vector to validate
%   name  [-] variable name used in error messages
% Output:
%   none; errors are thrown for invalid input

    if ~isnumeric(value) || numel(value) ~= 3 || any(~isfinite(value(:)))
        error('%s must be a finite numeric 3-component vector.', name);
    end
end

function validatePositiveScalar(value, name)
% validatePositiveScalar Validate a positive finite numeric scalar.
% Inputs:
%   value [numeric] positive scalar value
%   name  [-] variable name used in error messages
% Output:
%   none; errors are thrown for invalid input

    if ~isscalar(value) || ~isnumeric(value) || ~isfinite(value) || value <= 0
        error('%s must be a positive finite numeric scalar.', name);
    end
end

function requireFields(inputStruct, fieldNames, structName)
% requireFields Require specific fields in a struct.
% Inputs:
%   inputStruct [struct] struct being checked
%   fieldNames  [-] cell array of required field names
%   structName  [-] struct label used in error messages
% Output:
%   none; errors are thrown for missing fields

    if ~isstruct(inputStruct)
        error('%s must be a struct.', structName);
    end
    for fieldIndex = 1:numel(fieldNames)
        if ~isfield(inputStruct, fieldNames{fieldIndex})
            error('%s.%s is required.', structName, fieldNames{fieldIndex});
        end
    end
end
