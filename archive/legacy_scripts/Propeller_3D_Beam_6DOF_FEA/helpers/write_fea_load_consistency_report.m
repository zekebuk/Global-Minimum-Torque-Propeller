function paths = write_fea_load_consistency_report(loads,params,results_dir,case_name)
%WRITE_FEA_LOAD_CONSISTENCY_REPORT Save FEA/aero load consistency reports.

if nargin < 4 || isempty(case_name)
    case_name = report_case_name(loads,params);
end

results_root = find_fea_results_root(results_dir);
if ~exist(results_root,'dir')
    mkdir(results_root);
end

case_name = sanitize_stem(case_name);
report = build_report(loads,params);
txt_path = fullfile(results_root,[case_name '_load_consistency_report.txt']);
json_path = fullfile(results_root,[case_name '_load_consistency_report.json']);

write_text_report(txt_path,report);
write_json_report(json_path,report);

paths = struct();
paths.text = txt_path;
paths.json = json_path;

end

function report = build_report(loads,params)

if isfield(loads,'load_consistency')
    consistency = loads.load_consistency;
else
    consistency = struct();
end

report = struct();
report.aero_station_file = field_or_default(consistency,'aero_station_file','');
report.aero_summary_file = field_or_default(consistency,'aero_summary_file','');
report.load_source = field_or_default(loads,'load_source',field_or_default(params,'load_source','legacy_uniform'));
report.target_thrust_N = field_or_default(consistency,'target_thrust_N',NaN);
report.aero_summary_thrust_N = field_or_default(consistency,'aero_summary_thrust_N',NaN);
report.FEA_integrated_thrust_N = field_or_default(consistency,'FEA_integrated_thrust_N',NaN);
report.aero_summary_torque_Nm = field_or_default(consistency,'aero_summary_torque_Nm',NaN);
report.FEA_integrated_torque_from_qy_Nm = field_or_default(consistency,'FEA_integrated_torque_from_qy_Nm',NaN);
report.FEA_integrated_torque_from_torque_loading_Nm = field_or_default(consistency,'FEA_integrated_torque_from_torque_loading_Nm',NaN);
report.aero_summary_power_W = field_or_default(consistency,'aero_summary_power_W',NaN);
report.power_from_qy_W = field_or_default(consistency,'power_from_qy_W',NaN);
report.max_qz_N_per_m = field_or_default(consistency,'max_qz_N_per_m',max_abs(loads,'q_z_thrust_N_m'));
report.max_qy_N_per_m = field_or_default(consistency,'max_qy_N_per_m',max_abs(loads,'q_y_edgewise_N_m'));
report.max_qx_N_per_m = field_or_default(consistency,'max_qx_N_per_m',max_abs(loads,'q_x_centrifugal_N_m'));
report.max_torque_loading_Nm_per_m = field_or_default(consistency,'max_torque_loading_Nm_per_m',max_abs(loads,'torque_loading_Nm_m'));
report.omega_rad_s = field_or_default(consistency,'omega_rad_s',field_or_default(loads,'omega_rad_s',NaN));
report.rpm = field_or_default(consistency,'rpm',field_or_default(loads,'rpm',NaN));
report.target_matched = field_or_default(consistency,'target_matched',true);
report.valid_for_thesis = field_or_default(consistency,'valid_for_thesis',true);
report.polar_is_placeholder = field_or_default(consistency,'polar_is_placeholder',false);
report.thrust_error_pct = field_or_default(consistency,'thrust_error_pct',NaN);
report.torque_from_qy_error_pct = field_or_default(consistency,'torque_from_qy_error_pct',NaN);
report.torque_from_torque_loading_error_pct = field_or_default(consistency,'torque_from_torque_loading_error_pct',NaN);
report.torsion_note = field_or_default(consistency,'torsion_note',field_or_default(loads,'torsion_note',''));
report.distributed_torsional_moment_note = field_or_default(consistency,'distributed_torsional_moment_note','Distributed blade torsional moment not modeled.');
report.warnings = field_or_default(consistency,'warnings',{});

end

function write_text_report(path,report)

fid = fopen(path,'w');
if fid < 0
    error('Could not open load consistency text report: %s',path);
end
cleanup = onCleanup(@() fclose(fid));

fprintf(fid,'FEA Load Consistency Report\n');
fprintf(fid,'===========================\n\n');
fprintf(fid,'Load source: %s\n',char(string(report.load_source)));
fprintf(fid,'Aero station file: %s\n',char(string(report.aero_station_file)));
fprintf(fid,'Aero summary file: %s\n\n',char(string(report.aero_summary_file)));

fprintf(fid,'Target thrust: %.12g N\n',report.target_thrust_N);
fprintf(fid,'Aero summary thrust: %.12g N\n',report.aero_summary_thrust_N);
fprintf(fid,'FEA-integrated thrust: %.12g N\n',report.FEA_integrated_thrust_N);
fprintf(fid,'Thrust difference: %.12g %%\n\n',report.thrust_error_pct);

fprintf(fid,'Aero summary torque: %.12g N-m\n',report.aero_summary_torque_Nm);
fprintf(fid,'FEA-integrated torque from qy: %.12g N-m\n',report.FEA_integrated_torque_from_qy_Nm);
fprintf(fid,'FEA-integrated torque from dQ/dr: %.12g N-m\n',report.FEA_integrated_torque_from_torque_loading_Nm);
fprintf(fid,'Torque difference from qy: %.12g %%\n',report.torque_from_qy_error_pct);
fprintf(fid,'Torque difference from dQ/dr: %.12g %%\n\n',report.torque_from_torque_loading_error_pct);

fprintf(fid,'Aero summary power: %.12g W\n',report.aero_summary_power_W);
fprintf(fid,'Power from FEA qy torque and omega: %.12g W\n',report.power_from_qy_W);
fprintf(fid,'omega: %.12g rad/s\n',report.omega_rad_s);
fprintf(fid,'rpm: %.12g\n\n',report.rpm);

fprintf(fid,'max qz: %.12g N/m\n',report.max_qz_N_per_m);
fprintf(fid,'max qy: %.12g N/m\n',report.max_qy_N_per_m);
fprintf(fid,'max qx: %.12g N/m\n',report.max_qx_N_per_m);
fprintf(fid,'max dQ/dr: %.12g N-m/m\n\n',report.max_torque_loading_Nm_per_m);

fprintf(fid,'target_matched: %d\n',logical(report.target_matched));
fprintf(fid,'valid_for_thesis: %d\n',logical(report.valid_for_thesis));
fprintf(fid,'polar_is_placeholder: %d\n\n',logical(report.polar_is_placeholder));

fprintf(fid,'%s\n',char(string(report.torsion_note)));
fprintf(fid,'%s\n\n',char(string(report.distributed_torsional_moment_note)));

fprintf(fid,'Warnings:\n');
warnings = cellstr(string(report.warnings));
if isempty(warnings)
    fprintf(fid,'  none\n');
else
    for i = 1:numel(warnings)
        fprintf(fid,'  - %s\n',warnings{i});
    end
end

end

function write_json_report(path,report)

try
    text = jsonencode(report,'PrettyPrint',true);
catch
    text = jsonencode(report);
end

fid = fopen(path,'w');
if fid < 0
    error('Could not open load consistency JSON report: %s',path);
end
cleanup = onCleanup(@() fclose(fid));
fprintf(fid,'%s',text);

end

function value = field_or_default(s,name,default_value)

if isstruct(s) && isfield(s,name) && ~isempty(s.(name))
    value = s.(name);
else
    value = default_value;
end

end

function value = max_abs(s,name)

if isstruct(s) && isfield(s,name) && ~isempty(s.(name))
    value = max(abs(s.(name)(:)));
else
    value = NaN;
end

end

function name = report_case_name(loads,params)

name = '';
if isfield(loads,'aero_loads') && isstruct(loads.aero_loads) && isfield(loads.aero_loads,'case_name')
    name = loads.aero_loads.case_name;
end
if isempty(name) && isfield(params,'output_name')
    name = params.output_name;
end
if isempty(name)
    name = 'fea_case';
end

end

function stem = sanitize_stem(stem)

stem = regexprep(char(string(stem)),'[^a-zA-Z0-9_\-]','_');
stem = regexprep(stem,'(^_+|_+$)','');
if isempty(stem)
    stem = 'fea_case';
end

end

function results_root = find_fea_results_root(results_dir)

candidate = char(string(results_dir));
results_root = candidate;
while ~isempty(candidate)
    [parent,name] = fileparts(candidate);
    if strcmpi(name,'fea')
        results_root = candidate;
        return
    end
    if strcmp(parent,candidate)
        break
    end
    candidate = parent;
end

end
