function plot_files = plot_fea_imported_loads(mesh,loads,params,this_dir,case_name)
%PLOT_FEA_IMPORTED_LOADS Plot distributed loads used by the FEA model.

if nargin < 5 || isempty(case_name)
    case_name = load_case_name(loads,params);
end

applyPlotStyle();

out_dir = fullfile(project_root_from_fea_dir(this_dir),'figures_paper','fea','beam_solver',sanitize_stem(case_name));
if ~exist(out_dir,'dir')
    mkdir(out_dir);
end

stem = sanitize_stem(case_name);
r_R = mesh.mu(:);
r_m = mesh.radius_m(:);

qz = column_or_nan(loads,'q_z_thrust_N_m',numel(r_R));
qy = column_or_nan(loads,'q_y_edgewise_N_m',numel(r_R));
qx = column_or_nan(loads,'q_x_centrifugal_N_m',numel(r_R));
torque_loading = column_or_nan(loads,'torque_loading_Nm_m',numel(r_R));

plot_files = {};
plot_files{end+1} = simple_load_plot(r_R,qz,'r/R [-]','q_z thrust [N/m]', ...
    'Imported thrust loading',out_dir,[stem '_qz_thrust_loading']);
plot_files{end+1} = simple_load_plot(r_R,qy,'r/R [-]','q_y tangential [N/m]', ...
    'Imported tangential loading',out_dir,[stem '_qy_tangential_loading']);
plot_files{end+1} = simple_load_plot(r_R,qx,'r/R [-]','q_x centrifugal [N/m]', ...
    'FEA centrifugal loading',out_dir,[stem '_qx_centrifugal_loading']);
plot_files{end+1} = simple_load_plot(r_R,torque_loading,'r/R [-]','dQ/dr [N-m/m]', ...
    'Imported torque loading',out_dir,[stem '_torque_loading']);

if has_cumulative(loads)
    cumulative_thrust = loads.aero_loads.cumulative_thrust_fraction(:);
    cumulative_torque = loads.aero_loads.cumulative_torque_fraction(:);
    x_cum = loads.aero_loads.r_R(:);
else
    cumulative_thrust = normalized_cumulative(r_m,qz);
    cumulative_torque = normalized_cumulative(r_m,torque_loading);
    x_cum = r_R;
end

plot_files{end+1} = cumulative_plot(x_cum,cumulative_thrust,cumulative_torque, ...
    out_dir,[stem '_cumulative_thrust_torque']);

end

function path = simple_load_plot(x,y,xlab,ylab,ttl,out_dir,stem)

fig = figure('Visible','off','Color','w','Units','inches','Position',[1 1 5.0 3.2]);
plot(x,y,'k-','LineWidth',1.6);
grid on;
xlabel(xlab);
ylabel(ylab);
title(ttl);
xlim([min(x) max(x)]);
path = save_figure_set(fig,out_dir,stem);

end

function path = cumulative_plot(x,cumulative_thrust,cumulative_torque,out_dir,stem)

fig = figure('Visible','off','Color','w','Units','inches','Position',[1 1 5.0 3.2]);
plot(x,cumulative_thrust,'k-','LineWidth',1.6); hold on;
plot(x,cumulative_torque,'k--','LineWidth',1.6);
grid on;
xlabel('r/R [-]');
ylabel('cumulative fraction [-]');
title('Cumulative thrust and torque');
legend('Thrust','Torque','Location','best');
xlim([min(x) max(x)]);
path = save_figure_set(fig,out_dir,stem);

end

function path = save_figure_set(fig,out_dir,stem)

path = fullfile(out_dir,[stem '.png']);
if exist('exportgraphics','file')
    exportgraphics(fig,fullfile(out_dir,[stem '.svg']),'ContentType','vector');
    exportgraphics(fig,path,'Resolution',300);
else
    try
        saveas(fig,fullfile(out_dir,[stem '.svg']));
    catch
    end
    print(fig,path,'-dpng','-r300');
end
close(fig);

end

function values = column_or_nan(s,name,n)

if isstruct(s) && isfield(s,name) && ~isempty(s.(name))
    values = s.(name)(:);
else
    values = NaN(n,1);
end

end

function tf = has_cumulative(loads)

tf = isfield(loads,'aero_loads') && isstruct(loads.aero_loads) && ...
    isfield(loads.aero_loads,'cumulative_thrust_fraction') && ...
    isfield(loads.aero_loads,'cumulative_torque_fraction') && ...
    any(isfinite(loads.aero_loads.cumulative_thrust_fraction)) && ...
    any(isfinite(loads.aero_loads.cumulative_torque_fraction));

end

function cumulative = normalized_cumulative(r,loading)

area = cumtrapz(r,loading);
total = area(end);
if isfinite(total) && abs(total) > eps
    cumulative = area/total;
else
    cumulative = NaN(size(r));
end

end

function root = project_root_from_fea_dir(this_dir)

code_dir = fileparts(this_dir);
[root,code_folder] = fileparts(code_dir);
if ~strcmpi(code_folder,'Code')
    root = code_dir;
end

end

function name = load_case_name(loads,params)

name = '';
if isfield(loads,'aero_loads') && isstruct(loads.aero_loads) && isfield(loads.aero_loads,'case_name')
    name = loads.aero_loads.case_name;
end
if isempty(name) && isfield(params,'output_name')
    name = params.output_name;
end
if isempty(name)
    name = 'fea_loads';
end

end

function stem = sanitize_stem(stem)

stem = regexprep(char(string(stem)),'[^a-zA-Z0-9_\-]','_');
stem = regexprep(stem,'(^_+|_+$)','');
if isempty(stem)
    stem = 'fea_loads';
end

end
