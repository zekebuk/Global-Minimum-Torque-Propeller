function lines = updateStationProbeWithStressEnvelope(lines,station,idx)
%UPDATESTATIONPROBEWITHSTRESSENVELOPE Append corrected stress envelope fields.

if isrow(lines)
    lines = lines(:);
end

critical = '';
if isfield(station,'critical_section_point') && numel(station.critical_section_point) >= idx
    critical = char(string(station.critical_section_point{idx}));
end
if isempty(critical)
    critical = 'unavailable';
end

shear_note = '';
if isfield(station,'torsional_shear_assumed_zero') && ...
        numel(station.torsional_shear_assumed_zero) >= idx && ...
        station.torsional_shear_assumed_zero(idx)
    shear_note = ' (assumed zero: T or J unavailable)';
end

lines = [lines; { ...
    sprintf('sigma_x max tensile = %.6g MPa',safeField(station,'sigma_x_max_tension_MPa',idx)); ...
    sprintf('sigma_x max compressive = %.6g MPa',safeField(station,'sigma_x_max_compression_MPa',idx)); ...
    sprintf('max abs normal stress = %.6g MPa',safeField(station,'sigma_abs_normal_max_MPa',idx)); ...
    sprintf('von Mises max = %.6g MPa',safeField(station,'sigma_vm_max_MPa',idx)); ...
    sprintf('von Mises min over section = %.6g MPa',safeField(station,'sigma_vm_min_MPa',idx)); ...
    sprintf('critical section point = %s',critical); ...
    sprintf('torsional shear envelope = %.6g MPa%s', ...
        safeField(station,'tau_torsion_envelope_MPa',idx),shear_note)}];

end

function value = safeField(data,name,idx)

value = NaN;
if isfield(data,name)
    values = data.(name);
    if numel(values) >= idx
        value = values(idx);
    end
end

end
