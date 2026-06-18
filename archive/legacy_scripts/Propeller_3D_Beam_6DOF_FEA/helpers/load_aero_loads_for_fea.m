function aero = load_aero_loads_for_fea(aero_station_csv,aero_summary_json)
%LOAD_AERO_LOADS_FOR_FEA Read aero-solver distributed loads for beam FEA.
%
% The aero solver owns BEMT/aerodynamic calculations. This helper only
% imports already-computed station loads and validates that the aero result
% is acceptable for structural loading.

if nargin < 2
    error('Usage: load_aero_loads_for_fea(aero_station_csv,aero_summary_json)');
end

aero_station_csv = char(string(aero_station_csv));
aero_summary_json = char(string(aero_summary_json));

if ~isfile(aero_station_csv)
    error('Aero station CSV not found: %s',aero_station_csv);
end
if ~isfile(aero_summary_json)
    error('Aero summary JSON not found: %s',aero_summary_json);
end

station_table = readtable(aero_station_csv,'VariableNamingRule','preserve');
summary_payload = jsondecode(fileread(aero_summary_json));
summary = extract_summary(summary_payload,station_table,aero_station_csv);

required_columns = {'r_R','r_m','qz_N_per_m'};
validate_columns(station_table,required_columns,aero_station_csv);

r_R = numeric_column(station_table,'r_R',true,[]);
r_m = numeric_column(station_table,'r_m',true,[]);
qz = numeric_column(station_table,'qz_N_per_m',true,[]);

has_qy = has_column(station_table,'qy_tangential_N_per_m');
if has_qy
    qy = numeric_column(station_table,'qy_tangential_N_per_m',true,[]);
else
    qy = zeros(size(r_m));
end

if has_column(station_table,'torque_loading_Nm_per_m')
    torque_loading = numeric_column(station_table,'torque_loading_Nm_per_m',true,[]);
else
    torque_loading = zeros(size(r_m));
end

if has_column(station_table,'dT_N')
    dT = numeric_column(station_table,'dT_N',true,[]);
else
    dT = NaN(size(r_m));
end

if has_column(station_table,'dQ_Nm')
    dQ = numeric_column(station_table,'dQ_Nm',true,[]);
else
    dQ = NaN(size(r_m));
end

if has_column(station_table,'cumulative_thrust_fraction')
    cumulative_thrust = numeric_column(station_table,'cumulative_thrust_fraction',true,[]);
else
    cumulative_thrust = NaN(size(r_m));
end

if has_column(station_table,'cumulative_torque_fraction')
    cumulative_torque = numeric_column(station_table,'cumulative_torque_fraction',true,[]);
else
    cumulative_torque = NaN(size(r_m));
end

valid_station = isfinite(r_m) & isfinite(r_R) & isfinite(qz);
if nnz(valid_station) < 2
    error('Aero station CSV must contain at least two finite r_m/r_R/qz stations.');
end

[r_m,order] = sort(r_m(valid_station));
r_R = r_R(valid_station);
r_R = r_R(order);
qz = qz(valid_station);
qz = qz(order);
qy = qy(valid_station);
qy = qy(order);
torque_loading = torque_loading(valid_station);
torque_loading = torque_loading(order);
dT = dT(valid_station);
dT = dT(order);
dQ = dQ(valid_station);
dQ = dQ(order);
cumulative_thrust = cumulative_thrust(valid_station);
cumulative_thrust = cumulative_thrust(order);
cumulative_torque = cumulative_torque(valid_station);
cumulative_torque = cumulative_torque(order);

[r_m,unique_idx] = unique(r_m,'stable');
r_R = r_R(unique_idx);
qz = qz(unique_idx);
qy = qy(unique_idx);
torque_loading = torque_loading(unique_idx);
dT = dT(unique_idx);
dQ = dQ(unique_idx);
cumulative_thrust = cumulative_thrust(unique_idx);
cumulative_torque = cumulative_torque(unique_idx);

if numel(r_m) < 2
    error('Aero station CSV must contain at least two unique radial stations.');
end

target_matched = logical_field(summary,'target_matched',false);
valid_for_thesis = logical_field(summary,'valid_for_thesis',false);
polar_is_placeholder = logical_field(summary,'polar_is_placeholder',false);

if ~target_matched
    error('Aero summary target_matched is false. Refusing to use unmatched aero loads for FEA.');
end

warnings = {};
if ~valid_for_thesis
    warnings{end+1} = 'Aero summary valid_for_thesis is false.';
    warning('%s',warnings{end});
end
if polar_is_placeholder
    warnings{end+1} = 'Aero summary polar_is_placeholder is true.';
    warning('%s',warnings{end});
end
if ~has_qy
    warnings{end+1} = 'qy_tangential_N_per_m column missing; tangential distributed load set to zero.';
    warning('%s',warnings{end});
end

aero = struct();
aero.station_csv_path = aero_station_csv;
aero.summary_json_path = aero_summary_json;
aero.station_table = station_table;
aero.summary = summary;
aero.case_name = text_field(summary,'case_name',file_stem(aero_station_csv,'_stations'));
aero.r_R = r_R(:);
aero.r_m = r_m(:);
aero.qz_N_per_m = qz(:);
aero.qy_tangential_N_per_m = qy(:);
aero.torque_loading_Nm_per_m = torque_loading(:);
aero.dT_N = dT(:);
aero.dQ_Nm = dQ(:);
aero.cumulative_thrust_fraction = cumulative_thrust(:);
aero.cumulative_torque_fraction = cumulative_torque(:);
aero.has_qy_tangential_N_per_m = has_qy;
aero.has_torque_loading_Nm_per_m = has_column(station_table,'torque_loading_Nm_per_m');
aero.target_matched = target_matched;
aero.valid_for_thesis = valid_for_thesis;
aero.polar_is_placeholder = polar_is_placeholder;
aero.thrust_N = numeric_field(summary,'thrust_N',NaN);
aero.torque_Nm = numeric_field(summary,'torque_Nm',NaN);
aero.power_W = numeric_field(summary,'power_W',NaN);
aero.rpm = numeric_field(summary,'rpm',NaN);
aero.omega_rad_s = numeric_field(summary,'omega_rad_s',NaN);
aero.target_thrust_N = numeric_field(summary,'target_thrust_N',NaN);
aero.warnings = warnings(:);

end

function validate_columns(table_in,required_columns,path)

missing = {};
for i = 1:numel(required_columns)
    if ~has_column(table_in,required_columns{i})
        missing{end+1} = required_columns{i}; %#ok<AGROW>
    end
end

if ~isempty(missing)
    error('Aero station CSV %s is missing required columns: %s',path,strjoin(missing,', '));
end

end

function tf = has_column(table_in,name)

tf = any(strcmp(table_in.Properties.VariableNames,name));

end

function values = numeric_column(table_in,name,required,default_value)

if ~has_column(table_in,name)
    if required
        error('Missing required column: %s',name);
    end
    values = default_value;
    return
end

values = table_in.(name);
if iscell(values)
    values = string(values);
end
if isstring(values) || ischar(values)
    values = str2double(values);
end
values = double(values(:));

end

function summary = extract_summary(payload,station_table,aero_station_csv)

if isstruct(payload) && isfield(payload,'summary') && isstruct(payload.summary)
    summary = payload.summary;
    top_fields = {'valid_for_thesis','polar_is_placeholder','target_matched'};
    for i = 1:numel(top_fields)
        name = top_fields{i};
        if ~isfield(summary,name) && isfield(payload,name)
            summary.(name) = payload.(name);
        end
    end
elseif is_locked_final_summary(payload)
    summary = extract_locked_final_case_summary(payload,station_table,aero_station_csv);
else
    summary = payload;
end

end

function tf = is_locked_final_summary(payload)

tf = isstruct(payload) && isfield(payload,'operating_point') && ...
    (isfield(payload,'mil') || isfield(payload,'gmt'));

end

function summary = extract_locked_final_case_summary(payload,station_table,aero_station_csv)

case_key = infer_case_key(station_table,aero_station_csv);
if ~isfield(payload,case_key)
    error('Locked aero summary JSON does not contain inferred case key "%s".',case_key);
end

case_payload = payload.(case_key);
operating_point = payload.operating_point;

summary = struct();
summary.case_name = text_field(case_payload,'case_label',upper(case_key));
if isfield(case_payload,'case_label')
    summary.case_label = case_payload.case_label;
end
summary.thrust_N = numeric_field(case_payload,'total_thrust_N',NaN);
summary.torque_Nm = numeric_field(case_payload,'total_torque_Nm',NaN);
summary.power_W = numeric_field(case_payload,'power_W',NaN);
summary.rpm = numeric_field(operating_point,'rpm',NaN);
summary.omega_rad_s = summary.rpm*2*pi/60;
summary.target_thrust_N = numeric_field(operating_point,'target_thrust_N',NaN);
summary.thrust_error_pct = numeric_field(case_payload,'thrust_error_pct',NaN);
summary.target_matched = isfinite(summary.thrust_error_pct) && abs(summary.thrust_error_pct) < 0.1;
summary.valid_for_thesis = logical_field(field_or_empty(payload,'validation'), ...
    'locked_summary_matches_expected',true);
summary.polar_is_placeholder = false;
summary.warnings = text_field(case_payload,'warnings','');

end

function case_key = infer_case_key(station_table,aero_station_csv)

case_key = '';
if has_column(station_table,'case_label')
    values = string(station_table.('case_label'));
    values = values(strlength(strtrim(values)) > 0);
    if ~isempty(values)
        case_key = lower(char(values(1)));
    end
end
if isempty(case_key) && has_column(station_table,'case_name')
    values = string(station_table.('case_name'));
    values = values(strlength(strtrim(values)) > 0);
    if ~isempty(values)
        text = lower(char(values(1)));
        if contains(text,'mil')
            case_key = 'mil';
        elseif contains(text,'gmt')
            case_key = 'gmt';
        end
    end
end
if isempty(case_key)
    [~,stem] = fileparts(aero_station_csv);
    text = lower(stem);
    if contains(text,'mil')
        case_key = 'mil';
    elseif contains(text,'gmt')
        case_key = 'gmt';
    end
end
if ~any(strcmp(case_key,{'mil','gmt'}))
    error('Could not infer MIL/GMT case from aero station CSV: %s',aero_station_csv);
end

end

function value = field_or_empty(s,name)

if isstruct(s) && isfield(s,name)
    value = s.(name);
else
    value = struct();
end

end

function value = numeric_field(s,name,default_value)

if isstruct(s) && isfield(s,name) && ~isempty(s.(name))
    value = double(s.(name));
else
    value = default_value;
end

end

function value = logical_field(s,name,default_value)

if ~(isstruct(s) && isfield(s,name)) || isempty(s.(name))
    value = default_value;
    return
end

raw = s.(name);
if islogical(raw)
    value = raw;
elseif isnumeric(raw)
    value = raw ~= 0;
else
    text = strtrim(char(string(raw)));
    value = any(strcmpi(text,{'true','1','yes','y'}));
end

end

function value = text_field(s,name,default_value)

if isstruct(s) && isfield(s,name) && ~isempty(s.(name))
    value = char(string(s.(name)));
else
    value = default_value;
end

end

function stem = file_stem(path,suffix)

[~,stem] = fileparts(path);
if nargin >= 2 && endsWith(stem,suffix)
    stem = extractBefore(stem,strlength(stem) - strlength(suffix) + 1);
    stem = char(stem);
end

end
