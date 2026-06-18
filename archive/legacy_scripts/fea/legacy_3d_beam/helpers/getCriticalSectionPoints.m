function section_points = getCriticalSectionPoints(geometry,stationIndex)
%GETCRITICALSECTIONPOINTS Critical local y/z points for beam stress recovery.
%
% Local y is chordwise/edgewise and local z is thickness/flapwise. The
% rectangular-equivalent section uses the same section-property convention as
% section_properties_from_geometry.m. Points include the top/bottom surfaces,
% leading/trailing sides, and all four corners; the leading/trailing points
% are also the ribbon surface points used by the native viewer.

if nargin < 2 || isempty(stationIndex)
    stationIndex = 1;
end

chord = readStationValue(geometry,{'chord_m','chord','c'},stationIndex,NaN);
A = readStationValue(geometry,{'A','A_m2','area'},stationIndex,NaN);
Iy = readStationValue(geometry,{'Iy','Iyy','Iy_m4','Iyy_m4'},stationIndex,NaN);
thickness = readStationValue(geometry,{'thickness_m','thickness','t'},stationIndex,NaN);
z_max = readStationValue(geometry,{'z_max_m','zmax','z_max'},stationIndex,NaN);
y_max = readStationValue(geometry,{'y_max_m','ymax','y_max'},stationIndex,NaN);

if ~isfinite(y_max) || y_max <= 0
    y_max = 0.5*abs(chord);
end

if ~isfinite(z_max) || z_max <= 0
    if isfinite(thickness) && thickness > 0
        z_max = 0.5*thickness;
    elseif isfinite(A) && A > 0 && isfinite(chord) && chord > 0
        area_factor = 0.65;
        z_max = 0.5*A/(area_factor*chord);
    elseif isfinite(Iy) && Iy > 0 && isfinite(chord) && chord > 0
        inertia_factor = 0.75;
        z_max = 0.5*(12*Iy/(inertia_factor*chord))^(1/3);
    elseif isfinite(chord) && chord > 0
        z_max = 0.5*0.10*chord;
    else
        z_max = NaN;
    end
end

names = {'top surface'; 'bottom surface'; 'leading edge side'; ...
    'trailing edge side'; 'top leading corner'; 'top trailing corner'; ...
    'bottom leading corner'; 'bottom trailing corner'};
y = [0; 0; y_max; -y_max; y_max; -y_max; y_max; -y_max];
z = [z_max; -z_max; 0; 0; z_max; z_max; -z_max; -z_max];

section_points = struct();
section_points.name = names;
section_points.y_m = y;
section_points.z_m = z;
section_points.y_max_m = y_max;
section_points.z_max_m = z_max;

end

function value = readStationValue(data,names,idx,defaultValue)

value = defaultValue;
if isempty(data)
    return
end

if istable(data)
    raw_names = data.Properties.VariableNames;
    norm_names = normalizeNames(raw_names);
    for i = 1:numel(names)
        match = find(strcmp(norm_names,normalizeNames(names{i})),1,'first');
        if ~isempty(match)
            column = data.(raw_names{match});
            value = valueAt(column,idx,defaultValue);
            return
        end
    end
elseif isstruct(data)
    raw_names = fieldnames(data);
    norm_names = normalizeNames(raw_names);
    for i = 1:numel(names)
        match = find(strcmp(norm_names,normalizeNames(names{i})),1,'first');
        if ~isempty(match)
            column = data.(raw_names{match});
            value = valueAt(column,idx,defaultValue);
            return
        end
    end
end

end

function value = valueAt(column,idx,defaultValue)

if isempty(column)
    value = defaultValue;
    return
end
if iscell(column)
    value = defaultValue;
    return
end
column = double(column(:));
if isempty(column)
    value = defaultValue;
elseif numel(column) == 1
    value = column(1);
else
    idx = max(1,min(numel(column),round(idx)));
    value = column(idx);
end

end

function out = normalizeNames(in)

if ischar(in) || isstring(in)
    in = cellstr(string(in));
end
out = cell(size(in));
for i = 1:numel(in)
    out{i} = lower(regexprep(char(in{i}),'[^a-zA-Z0-9]',''));
end

end
