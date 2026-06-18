function geom = read_prop_geometry(csv_path,radius_m,geometry_variant)
%READ_PROP_GEOMETRY Read propeller blade geometry and convert to SI.
%
% The reader accepts common columns such as:
%   r/R, mu, radius_m, radius_in, r, c/R, chord_over_R, chord_m,
%   chord_in, chord, twist_deg, beta_deg, airfoil.
% For Reference_Propeller_Geometry.xlsx, only the 22-inch blocks in columns
% L:Q are read; the 18-inch block is ignored. geometry_variant can be
% 'gmt_22' or 'mil_22'.
%
% If only normalized radius/chord are present, radius_m is used as the
% blade tip radius. For a 22 inch propeller, radius_m = 0.2794 m.

if nargin < 2 || isempty(radius_m)
    radius_m = 0.2794;
end
if nargin < 3 || isempty(geometry_variant)
    geometry_variant = 'gmt_22';
end

if ~isfile(csv_path)
    error('Geometry file not found: %s',csv_path);
end

T = read_geometry_table(csv_path,geometry_variant);
if isempty(T)
    error('Geometry file is empty: %s',csv_path);
end

mu = get_numeric_column(T,{'mu','r/R','r_over_R','r_R','radius_over_R','radial_station'});

r_m = get_numeric_column(T,{'radius_m','r_m','r [m]','radius [m]','r_meters'});
if isempty(r_m)
    r_in = get_numeric_column(T,{'radius_in','r_in','r [in]','radius [in]','radius_inches'});
    if ~isempty(r_in)
        r_m = r_in*0.0254;
    end
end

generic_r = get_numeric_column(T,{'r','radius'});
if isempty(r_m) && ~isempty(generic_r)
    if radius_m > 0 && max(generic_r) <= 1.5*radius_m
        r_m = generic_r;
    elseif max(generic_r) <= 1.2 && radius_m > 0
        if isempty(mu)
            mu = generic_r;
        end
    elseif max(generic_r) > 1.5 && radius_m > 0 && max(generic_r)*0.0254 <= 1.5*radius_m
        r_m = generic_r*0.0254;
    else
        r_m = generic_r;
    end
end

if isempty(r_m)
    if isempty(mu)
        error('Geometry CSV needs radius_m/radius_in/r or normalized r/R.');
    end
    r_m = mu*radius_m;
end

if isempty(mu)
    mu = r_m/radius_m;
end

chord_m = get_numeric_column(T,{'chord_m','c_m','chord [m]','chord_meters'});
if isempty(chord_m)
    chord_in = get_numeric_column(T,{'chord_in','c_in','chord [in]','chord_inches'});
    if ~isempty(chord_in)
        chord_m = chord_in*0.0254;
    end
end

generic_chord = get_numeric_column(T,{'chord','c'});
if isempty(chord_m) && ~isempty(generic_chord)
    if max(generic_chord) > 0.8 && max(generic_chord)*0.0254 < 0.8
        chord_m = generic_chord*0.0254;
    else
        chord_m = generic_chord;
    end
end

if isempty(chord_m)
    chord_over_R = get_numeric_column(T,{'chord_over_R','c/R','c_R','c_over_R','chord/R'});
    if isempty(chord_over_R)
        error('Geometry CSV needs chord_m/chord_in/chord or normalized c/R.');
    end
    chord_m = chord_over_R*radius_m;
end

twist_deg = get_numeric_column(T,{'twist_deg','beta_deg','twist','beta','pitch_deg','blade_twist_deg'});
if isempty(twist_deg)
    twist_deg = zeros(size(r_m));
end

thickness_ratio = get_numeric_column(T,{'thickness_ratio','t_c','t/c','thickness_over_chord','tc'});
if isempty(thickness_ratio)
    thickness_ratio = NaN(size(r_m));
end

airfoil = get_text_column(T,{'airfoil','section','section_name','airfoil_name'});
if isempty(airfoil)
    airfoil = repmat({''},height(T),1);
end

dthrust_N = get_numeric_column(T,{'dthrust_n','dT_N','delta_thrust_N','thrust_N','dthrust'});
dr_m = get_numeric_column(T,{'dr_m','delta_r_m','dr'});
dtorque_Nm = get_numeric_column(T,{'dtorque_nm','dQ_Nm','delta_torque_Nm','torque_Nm','dtorque'});

valid = isfinite(r_m) & isfinite(mu) & isfinite(chord_m) & chord_m > 0;
if nnz(valid) < 2
    error('Geometry CSV must contain at least two valid radial stations.');
end

idx_valid = find(valid);
[r_m_sorted,order] = sort(r_m(valid));
idx_sorted = idx_valid(order);

[r_m_unique,unique_idx] = unique(r_m_sorted,'stable');
idx_sorted = idx_sorted(unique_idx);

geom = struct();
geom.csv_path = csv_path;
geom.radius_tip_m = radius_m;
geom.radius_m = r_m_unique(:);
geom.mu = mu(idx_sorted);
geom.chord_m = chord_m(idx_sorted);
geom.twist_deg = twist_deg(idx_sorted);
geom.thickness_ratio = thickness_ratio(idx_sorted);
geom.airfoil = airfoil(idx_sorted);
geom.raw_table = T;

if ~isempty(dthrust_N)
    geom.dthrust_N = dthrust_N(idx_sorted);
end
if ~isempty(dr_m)
    geom.dr_m = dr_m(idx_sorted);
end
if ~isempty(dtorque_Nm)
    geom.dtorque_Nm = dtorque_Nm(idx_sorted);
end

end

function T = read_geometry_table(geometry_path,geometry_variant)

[~,base_name,ext] = fileparts(geometry_path);
is_excel = any(strcmpi(ext,{'.xlsx','.xlsm','.xls'}));
if is_excel && strcmpi(base_name,'Reference_Propeller_Geometry')
    if strcmpi(geometry_variant,'mil_22')
        workbook_range = 'L55:Q92';
    else
        workbook_range = 'L5:Q43';
    end
    T = readtable(geometry_path,'Sheet',1,'Range',workbook_range, ...
        'VariableNamingRule','preserve');
    return
end

opts = detectImportOptions(geometry_path);
try
    opts.VariableNamingRule = 'preserve';
catch
end
try
    opts = setvaropts(opts,opts.VariableNames,'WhitespaceRule','preserve');
catch
end
T = readtable(geometry_path,opts);

end

function values = get_numeric_column(T,candidates)

values = [];
idx = find_column(T,candidates);
if idx == 0
    return
end

raw = T.(T.Properties.VariableNames{idx});
if isnumeric(raw) || islogical(raw)
    values = double(raw);
elseif iscell(raw) || isstring(raw) || ischar(raw) || iscategorical(raw)
    values = str2double(string(raw));
else
    values = [];
end

if isempty(values) || all(isnan(values))
    values = [];
else
    values = values(:);
end

end

function values = get_text_column(T,candidates)

values = {};
idx = find_column(T,candidates);
if idx == 0
    return
end

raw = T.(T.Properties.VariableNames{idx});
values = cellstr(string(raw));
values = values(:);

end

function idx = find_column(T,candidates)

names = T.Properties.VariableNames;
norm_names = normalize_name(names);
norm_candidates = normalize_name(candidates);

idx = 0;
for k = 1:numel(norm_candidates)
    hit = find(strcmp(norm_names,norm_candidates{k}),1,'first');
    if ~isempty(hit)
        idx = hit;
        return
    end
end

for k = 1:numel(norm_candidates)
    if numel(norm_candidates{k}) < 3
        continue
    end
    hit = find(contains(norm_names,norm_candidates{k}),1,'first');
    if ~isempty(hit)
        idx = hit;
        return
    end
end

end

function out = normalize_name(in)

if ischar(in)
    in = {in};
end
out = cell(size(in));
for k = 1:numel(in)
    out{k} = lower(regexprep(char(in{k}),'[^a-zA-Z0-9]',''));
end

end
