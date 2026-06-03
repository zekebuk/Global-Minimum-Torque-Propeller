function selection = selectProbeStation(station,probe_value)
%SELECTPROBESTATION Interpret probe input as r/R, station index, or element.
%
% Inputs in [0, 1] are treated as requested r/R. Integer values greater
% than 1 are treated as station indices. Values outside those cases are
% interpreted as physical radial coordinates in meters.

txt = strtrim(string(probe_value));
selection = struct();
selection.requested = char(txt);
selection.is_element = false;
selection.mode = '';
selection.index = NaN;
selection.element_index = NaN;
selection.r_over_R = NaN;
selection.r_m = NaN;

if startsWith(lower(txt),"e")
    elem_idx = str2double(extractAfter(txt,1));
    if ~isfinite(elem_idx)
        error('Could not parse element number.');
    end
    elem_idx = max(1,min(station.n - 1,round(elem_idx)));
    idx = elem_idx;
    selection.is_element = true;
    selection.mode = 'element';
else
    value = str2double(txt);
    if ~isfinite(value)
        error('Enter station index, element like e3, r/R, or radial coordinate in meters.');
    end

    if value >= 0 && value <= 1
        [~,idx] = min(abs(station.mu - value));
        selection.mode = 'r/R';
    elseif abs(value - round(value)) < 1e-9 && value > 1 && value <= station.n
        idx = round(value);
        selection.mode = 'station index';
    else
        [~,idx] = min(abs(station.r - value));
        selection.mode = 'r [m]';
    end

    elem_idx = min(max(idx,1),station.n - 1);
end

selection.index = idx;
selection.element_index = elem_idx;
selection.r_over_R = station.mu(idx);
selection.r_m = station.r(idx);

end
