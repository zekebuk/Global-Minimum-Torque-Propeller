function paths = exportModalResultsCSV(modalResults,mesh,dof,results_dir,output_name)
%EXPORTMODALRESULTSCSV Write modal frequency and mode-shape CSV files.

if ~exist(results_dir,'dir')
    mkdir(results_dir);
end

numModes = numel(modalResults.frequencies_hz);
mode_number = (1:numModes)';
frequency_hz = modalResults.frequencies_hz(:);
omega_rad_s = modalResults.omega_rad_s(:);
mode_type = string(modalResults.modeType(:));
generalized_mass = modalResults.generalizedMass(:);
generalized_stiffness = modalResults.generalizedStiffness(:);
max_displacement_node = modalResults.max_displacement_node(:);
max_displacement_r_over_R = modalResults.max_displacement_r_over_R(:);
max_displacement_dof = string(modalResults.max_displacement_dof(:));

frequency_table = table(mode_number,frequency_hz,omega_rad_s,mode_type, ...
    generalized_mass,generalized_stiffness,max_displacement_node, ...
    max_displacement_r_over_R,max_displacement_dof);

if isfield(modalResults,'excitation')
    warning_text = repmat("",numModes,1);
    for i = 1:numModes
        warning_text(i) = warningsForMode(modalResults.excitation.warnings,i);
    end
    frequency_table.excitation_warning = warning_text;
end

frequency_csv = fullfile(results_dir,[output_name '_modal_frequencies.csv']);
writetable(frequency_table,frequency_csv);

mode_column = [];
station_column = [];
mu_column = [];
r_column = [];
ux = [];
uy = [];
uz = [];
rx = [];
ry = [];
rz = [];

for mode = 1:numModes
    U = reshape(modalResults.modeShapes(:,mode),dof.ndof_node,[])';
    n = size(U,1);
    mode_column = [mode_column; mode*ones(n,1)]; %#ok<AGROW>
    station_column = [station_column; (1:n)']; %#ok<AGROW>
    mu_column = [mu_column; mesh.mu(:)]; %#ok<AGROW>
    r_column = [r_column; mesh.radius_m(:)]; %#ok<AGROW>
    ux = [ux; U(:,1)]; %#ok<AGROW>
    uy = [uy; U(:,2)]; %#ok<AGROW>
    uz = [uz; U(:,3)]; %#ok<AGROW>
    rx = [rx; U(:,4)]; %#ok<AGROW>
    ry = [ry; U(:,5)]; %#ok<AGROW>
    rz = [rz; U(:,6)]; %#ok<AGROW>
end

mode_shape_table = table(mode_column,station_column,mu_column,r_column, ...
    ux,uy,uz,rx,ry,rz, ...
    'VariableNames',{'mode_number','station','r_over_R','r_m', ...
    'ux','uy','uz','rx','ry','rz'});
mode_shapes_csv = fullfile(results_dir,[output_name '_mode_shapes.csv']);
writetable(mode_shape_table,mode_shapes_csv);

paths = struct();
paths.frequency_csv = frequency_csv;
paths.mode_shapes_csv = mode_shapes_csv;
paths.frequency_table = frequency_table;
paths.mode_shape_table = mode_shape_table;

end

function text = warningsForMode(warnings,mode_number)

matched = {};
needle = sprintf('Mode %d ',mode_number);
for i = 1:numel(warnings)
    if contains(string(warnings{i}),needle)
        matched{end+1} = warnings{i}; %#ok<AGROW>
    end
end
text = strjoin(string(matched),'; ');

end
