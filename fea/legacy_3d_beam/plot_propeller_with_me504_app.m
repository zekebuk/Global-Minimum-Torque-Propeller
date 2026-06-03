function [x,elem,ul,sv,filnam] = plot_propeller_with_me504_app(results_csv,plot_plane,launch_ui)
%PLOT_PROPELLER_WITH_ME504_APP Convert beam results for ME504 UI_2D_PLOT_CP.
%
% The ME504 app is a 2D plotting app. This adapter projects the 3D beam
% blade into a 2D visualization ribbon made from 4-node quad elements:
%
%   x    = [node, x, y]
%   elem = [element, type, section, node1, node2, node3, node4]
%   ul   = [2 dof, 4 nodes, element]
%   sv   = [3 variables, 4 nodes, element]
%
% Usage:
%   plot_propeller_with_me504_app
%   plot_propeller_with_me504_app(results_csv)
%   plot_propeller_with_me504_app(results_csv,'planform')
%   plot_propeller_with_me504_app(results_csv,'span-flap')
%
% plot_plane:
%   'planform'  uses span x chord and displacements [ux, uy]
%   'span-flap' uses span x thin ribbon and displacements [ux, uz]

this_dir = fileparts(mfilename('fullpath'));
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end

if nargin < 1 || isempty(results_csv)
    results_csv = latest_results_csv();
end
if nargin < 2 || isempty(plot_plane)
    plot_plane = 'planform';
end
if nargin < 3 || isempty(launch_ui)
    launch_ui = usejava('desktop') && desktop('-inuse');
end

if ~isfile(results_csv)
    error('Results CSV not found: %s',results_csv);
end

T = readtable(results_csv,'VariableNamingRule','preserve');
if height(T) < 2
    error('Results CSV must contain at least two radial stations.');
end

r = col(T,'r_m');
chord = col(T,'chord_m');
ux = col(T,'ux');
uy = col(T,'uy');
uz = col(T,'uz');

combined_MPa = col(T,'sigma_combined')/1e6;
flap_MPa = col(T,'sigma_flap')/1e6;
torsion_MPa = col(T,'tau_torsion')/1e6;

n_station = numel(r);
n_node = 2*n_station;
n_elem = n_station - 1;

plot_plane = lower(string(plot_plane));
switch plot_plane
    case "planform"
        half_width = 0.5*chord;
        v = uy;
        plane_label = 'span-chord planform';
    case {"span-flap","flap","span_flap"}
        half_width = 0.015*max(r - min(r))*ones(size(r));
        v = uz;
        plane_label = 'span-flap projection';
    otherwise
        error('Unknown plot_plane: %s',plot_plane);
end

x = zeros(n_node,3);
for i = 1:n_station
    bottom = 2*i - 1;
    top = 2*i;
    x(bottom,:) = [bottom, r(i), -half_width(i)];
    x(top,:) = [top, r(i), half_width(i)];
end

% Element type 6 and section 1 match the ME504 quad examples.
elem = zeros(n_elem,7);
ul = zeros(2,4,n_elem);
sv = zeros(3,4,n_elem);

for e = 1:n_elem
    n1 = 2*e - 1;
    n2 = 2*(e + 1) - 1;
    n3 = 2*(e + 1);
    n4 = 2*e;
    nodes = [n1 n2 n3 n4];
    station_ids = [e e+1 e+1 e];

    elem(e,:) = [e, 6, 1, nodes];
    ul(:,:,e) = [ux(station_ids)'; v(station_ids)'];
    sv(:,:,e) = [combined_MPa(station_ids)'; ...
                 flap_MPa(station_ids)'; ...
                 torsion_MPa(station_ids)'];
end

[~,base_name] = fileparts(results_csv);
filnam = sprintf('%s - %s',base_name,plane_label);

fprintf('ME504 plot input built from:\n  %s\n',results_csv);
fprintf('x size    = %s\n',mat2str(size(x)));
fprintf('elem size = %s\n',mat2str(size(elem)));
fprintf('ul size   = %s\n',mat2str(size(ul)));
fprintf('sv size   = %s\n',mat2str(size(sv)));
fprintf('sv variables are approximate beam stresses in MPa: combined, flap, torsion.\n');

if launch_ui
    addpath(this_dir,'-begin');
    add_me504_plot_path();
    if exist('UI_2D_PLOT_CP','file') == 2 && me504_helpers_usable()
        ndof = 2;
        UI_2D_PLOT_CP(x,elem,ul,sv,ndof,filnam);
    else
        warning(['ME504 plot app or p-code helpers are not usable in this MATLAB session. ', ...
            'Opening the native propeller viewer instead.']);
        plot_propeller_native_viewer(results_csv,plot_plane);
    end
end

end

function values = col(T,name)

names = T.Properties.VariableNames;
idx = find(strcmp(names,name),1,'first');
if isempty(idx)
    error('Required column "%s" not found in results CSV.',name);
end
values = T.(names{idx});
values = values(:);

end

function results_csv = latest_results_csv()

this_dir = fileparts(mfilename('fullpath'));
code_dir = fileparts(this_dir);
[project_root,code_folder] = fileparts(code_dir);
if ~strcmpi(code_folder,'Code')
    project_root = code_dir;
end
results_root = fullfile(project_root,'results','fea');
files = dir(fullfile(results_root,'**','*_results.csv'));
if isempty(files)
    error('No *_results.csv files found under %s. Run main_propeller_3d_beam_fea first.', ...
        results_root);
end

[~,idx] = max([files.datenum]);
results_csv = fullfile(files(idx).folder,files(idx).name);

end

function add_me504_plot_path()

this_dir = fileparts(mfilename('fullpath'));
cal_poly_dir = fileparts(fileparts(fileparts(this_dir)));
plot_dir = fullfile(cal_poly_dir,'ME 504','UI_2D_PLOT_CP');

if isfolder(plot_dir) && ~any(strcmp(strsplit(path,pathsep),plot_dir))
    addpath(plot_dir);
end

end

function ok = me504_helpers_usable()

ok = false;
if exist('feplot2d','file') == 0
    return
end

figs_before = findall(0,'Type','figure');

try
    x0 = [1 0 0; 2 1 0; 3 1 0.1; 4 0 0.1];
    elem0 = [1 6 1 1 2 3 4];
    ul0 = zeros(2,4,1);
    sv0 = zeros(3,4,1);
    evalc('feplot2d(x0,elem0,ul0,sv0,2,''ME504 helper self-test'')');
    ok = true;
catch ME
    if contains(ME.message,'P-code file header is damaged')
        warning('ME504 feplot2d.p is damaged or incompatible with this MATLAB install.');
    else
        warning('ME504 helper self-test failed: %s',ME.message);
    end
end

figs_after = findall(0,'Type','figure');
new_figs = setdiff(figs_after,figs_before);
for i = 1:numel(new_figs)
    try
        close(new_figs(i));
    catch
    end
end

end
