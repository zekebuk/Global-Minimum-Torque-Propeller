function [x,label] = get_x_axis_data(results,x_axis_mode)
%GET_X_AXIS_DATA Shared span-axis selector for viewer and exports.

mode = lower(strtrim(string(x_axis_mode)));
switch mode
    case {"r/r","rr","r_over_r","r over r","mu"}
        x = firstAvailableVector(results,{'r_R','r_over_R','mu'});
        label = 'r/R [-]';
    case {"r [m]","r (m)","r_m","r m","radius","radius_m"}
        x = firstAvailableVector(results,{'r_m','r'});
        label = 'r [m]';
    case {"station","station index","station_index"}
        n = stationCount(results);
        x = (1:n)';
        label = 'Station index [-]';
    otherwise
        error('Unknown x-axis mode: %s',x_axis_mode);
end

x = x(:);

end

function x = firstAvailableVector(results,fields)

x = [];
for i = 1:numel(fields)
    if isfield(results,fields{i})
        value = results.(fields{i});
        if ~isempty(value)
            x = double(value(:));
            return
        end
    end
end
error('Could not find requested x-axis data in results structure.');

end

function n = stationCount(results)

if isfield(results,'n') && isfinite(results.n) && results.n > 0
    n = round(results.n);
    return
end

candidate_fields = {'r_R','r_over_R','mu','r_m','r'};
for i = 1:numel(candidate_fields)
    if isfield(results,candidate_fields{i}) && ~isempty(results.(candidate_fields{i}))
        n = numel(results.(candidate_fields{i}));
        return
    end
end
error('Could not infer station count from results structure.');

end
