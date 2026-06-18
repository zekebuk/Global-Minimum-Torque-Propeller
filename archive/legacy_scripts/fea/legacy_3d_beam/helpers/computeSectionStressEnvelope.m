function envelope = computeSectionStressEnvelope(results,geometry,stationIndex)
%COMPUTESECTIONSTRESSENVELOPE Max section-point stress envelope by station.
%
% This helper recomputes the equivalent stress from beam resultants and
% critical y/z section points. It intentionally avoids algebraic cancellation
% from signed scalar component sums.

if nargin < 2 || isempty(geometry)
    geometry = results;
end
if nargin < 3 || isempty(stationIndex)
    stationIndex = 1:stationCount(results,geometry);
end

stationIndex = stationIndex(:);
n = numel(stationIndex);

envelope = struct();
envelope.station_index = stationIndex;
envelope.sigma_vm_max_Pa = NaN(n,1);
envelope.sigma_vm_min_Pa = NaN(n,1);
envelope.sigma_x_max_tension_Pa = NaN(n,1);
envelope.sigma_x_max_compression_Pa = NaN(n,1);
envelope.sigma_abs_normal_max_Pa = NaN(n,1);
envelope.tau_torsion_max_Pa = NaN(n,1);
envelope.critical_section_point = repmat({''},n,1);
envelope.critical_section_point_index = NaN(n,1);
envelope.torsional_shear_assumed_zero = false(n,1);
envelope.note = 'Combined stress is max(abs(sigma_x)) over section points; von Mises is max(sqrt(sigma_x^2 + 3*tau^2)).';

for k = 1:n
    idx = stationIndex(k);
    points = getCriticalSectionPoints(geometry,idx);
    point_stress = computeVonMisesFromBeamStress( ...
        readStationValue(results,{'N','N_N','axial_force_N'},idx,NaN), ...
        readStationValue(results,{'My','My_bending','My_bending_Nm'},idx,NaN), ...
        readStationValue(results,{'Mz','Mz_bending','Mz_bending_Nm'},idx,NaN), ...
        readStationValue(results,{'T','Mx_torsion','Mx_torsion_Nm','torsional_moment'},idx,0), ...
        readStationValue(results,{'A','A_m2','area'},idx,NaN), ...
        readStationValue(results,{'Iy','Iyy','Iy_m4','Iyy_m4'},idx,NaN), ...
        readStationValue(results,{'Iz','Izz','Iz_m4','Izz_m4'},idx,NaN), ...
        readStationValue(results,{'J','J_m4'},idx,NaN), ...
        points);

    envelope.sigma_vm_max_Pa(k) = point_stress.sigma_vm_max_Pa;
    envelope.sigma_vm_min_Pa(k) = point_stress.sigma_vm_min_Pa;
    envelope.sigma_x_max_tension_Pa(k) = point_stress.sigma_x_max_tension_Pa;
    envelope.sigma_x_max_compression_Pa(k) = point_stress.sigma_x_max_compression_Pa;
    envelope.sigma_abs_normal_max_Pa(k) = point_stress.sigma_abs_normal_max_Pa;
    envelope.tau_torsion_max_Pa(k) = point_stress.tau_torsion_max_Pa;
    envelope.critical_section_point{k} = point_stress.critical_section_point;
    envelope.critical_section_point_index(k) = point_stress.critical_section_point_index;
    envelope.torsional_shear_assumed_zero(k) = point_stress.torsional_shear_assumed_zero;
end

end

function n = stationCount(results,geometry)

n = max(countFromData(results),countFromData(geometry));
if n < 1
    n = 1;
end

end

function n = countFromData(data)

n = 0;
if isempty(data)
    return
end
if istable(data)
    n = height(data);
elseif isstruct(data)
    names = fieldnames(data);
    for i = 1:numel(names)
        value = data.(names{i});
        if isnumeric(value) || islogical(value) || isstring(value) || iscell(value)
            n = max(n,numel(value));
        end
    end
end

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
            value = valueAt(data.(raw_names{match}),idx,defaultValue);
            return
        end
    end
elseif isstruct(data)
    raw_names = fieldnames(data);
    norm_names = normalizeNames(raw_names);
    for i = 1:numel(names)
        match = find(strcmp(norm_names,normalizeNames(names{i})),1,'first');
        if ~isempty(match)
            value = valueAt(data.(raw_names{match}),idx,defaultValue);
            return
        end
    end
end

end

function value = valueAt(column,idx,defaultValue)

if isempty(column) || iscell(column)
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
