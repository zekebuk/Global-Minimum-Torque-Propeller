function app = start_matlab_fea_ui(results_csv,viewer,plot_plane)
%START_MATLAB_FEA_UI Launch the archived propeller beam FEA viewer.
%
% Usage from MATLAB:
%   start_matlab_fea_ui
%   start_matlab_fea_ui([], 'native', '3D isometric ribbon')
%   start_matlab_fea_ui([], 'me504', 'span-flap')

if nargin < 1
    results_csv = [];
end
if nargin < 2 || isempty(viewer)
    viewer = 'native';
end
if nargin < 3 || isempty(plot_plane)
    plot_plane = '3D isometric ribbon';
end

this_dir = fileparts(mfilename('fullpath'));
code_root = fileparts(fileparts(this_dir));
fea_dir = fullfile(code_root,'archive','legacy_scripts','Propeller_3D_Beam_6DOF_FEA');
fallback_fea_dir = fullfile(code_root,'archive','legacy_scripts','fea','legacy_3d_beam');
if ~isfolder(fea_dir) && isfolder(fallback_fea_dir)
    fea_dir = fallback_fea_dir;
end
if ~isfolder(fea_dir)
    error('Archived MATLAB FEA directory not found under %s.',code_root);
end

helpers_dir = fullfile(fea_dir,'helpers');
addpath(fea_dir,'-begin');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end

if isstring(results_csv)
    results_csv = char(results_csv);
end
if isempty(results_csv)
    results_csv = [];
end

old_dir = pwd;
cleanup = onCleanup(@() cd(old_dir));
cd(fea_dir);

switch lower(char(string(viewer)))
    case {'me504','me504_app','ui_2d_plot_cp'}
        plot_propeller_with_me504_app(results_csv,plot_plane,true);
        app = [];
    otherwise
        app = plot_propeller_native_viewer(results_csv,plot_plane);
end

end
