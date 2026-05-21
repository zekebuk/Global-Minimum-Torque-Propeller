function campbell = plotCampbellDiagram(K,M,Kg_ref,bc,mesh,dof,params,results_dir,output_name)
%PLOTCAMPBELLDIAGRAM Export and plot a Campbell diagram data set.
%
% The first implementation uses modal frequencies that are constant with
% RPM. Centrifugal stiffening can be added later by replacing the replicated
% modal-frequency block with an RPM-dependent modal solve.

if nargin < 9 || isempty(output_name)
    output_name = 'modal';
end
if nargin < 8 || isempty(results_dir)
    results_dir = pwd;
end
if nargin < 7 || isempty(params)
    params = struct();
end
applyPlotStyle();

max_rpm = paramOrDefault(params,'campbell_max_RPM',paramOrDefault(params,'RPM',0));
num_points = paramOrDefault(params,'campbell_num_points', ...
    paramOrDefault(params,'campbell_num_steps',101));
num_points = max(2,round(num_points));
num_blades = paramOrDefault(params,'number_of_blades',2);
tol = paramOrDefault(params,'campbell_warning_tolerance',0.10);
num_modes_out = 5;

rpm_values = linspace(0,max_rpm,num_points)';
f_rot = rpm_values/60;
f_1x = f_rot;
f_2x = 2*f_rot;
f_3x = 3*f_rot;
f_4x = 4*f_rot;
f_bpf = num_blades*f_rot;

base_modal_freq = modalFrequenciesForCampbell(K,M,Kg_ref,bc,mesh,dof,params,num_modes_out);
mode_freq = NaN(numel(rpm_values),num_modes_out);
copy_count = min(num_modes_out,numel(base_modal_freq));
for mode = 1:copy_count
    mode_freq(:,mode) = base_modal_freq(mode);
end

T = table(rpm_values,f_1x,f_2x,f_3x,f_4x,f_bpf, ...
    mode_freq(:,1),mode_freq(:,2),mode_freq(:,3),mode_freq(:,4),mode_freq(:,5), ...
    'VariableNames',{'rpm','f_1x_Hz','f_2x_Hz','f_3x_Hz','f_4x_Hz','f_bpf_Hz', ...
    'mode_1_Hz','mode_2_Hz','mode_3_Hz','mode_4_Hz','mode_5_Hz'});

if ~exist(results_dir,'dir')
    mkdir(results_dir);
end
results_root = findResultsRoot(results_dir);
if ~exist(results_root,'dir')
    mkdir(results_root);
end

campbell = struct();
campbell.RPM = rpm_values;
campbell.frequencies_hz = mode_freq;
campbell.table = T;
campbell.csv_path = fullfile(results_root,'campbell_diagram.csv');
campbell.case_csv_path = fullfile(results_dir,[output_name '_campbell.csv']);
writetable(T,campbell.csv_path);
writetable(T,campbell.case_csv_path);

title_text = campbellTitleForCase(params,output_name);
fig = createCampbellFigure(T,tol,title_text);
campbell.plot_path = fullfile(results_dir,[output_name '_campbell_diagram.png']);
saveFigureFormats(fig,campbell.plot_path);

figure_category = figureCategoryForCase(output_name);
figures_dir = fullfile(fileparts(results_root),'figures_paper',figure_category);
if ~exist(figures_dir,'dir')
    mkdir(figures_dir);
end
campbell.paper_plot_path = fullfile(figures_dir,[output_name '_campbell_diagram.png']);
saveFigureFormats(fig,campbell.paper_plot_path);
close(fig);

summary = summarizeModeOneBpf(T);
campbell.minimum_mode1_bpf_separation_percent = summary.min_sep_percent;
campbell.mode1_bpf_closest_rpm = summary.closest_rpm;
campbell.warning = summary.min_sep_fraction < tol;

end

function category = figureCategoryForCase(output_name)

name = lower(char(string(output_name)));
if startsWith(name,'reference_')
    category = 'reference';
elseif startsWith(name,'optimized_')
    category = 'optimized';
else
    category = 'comparison';
end

end

function value = paramOrDefault(params,name,default_value)

if isfield(params,name) && ~isempty(params.(name))
    value = params.(name);
else
    value = default_value;
end

end

function freq = modalFrequenciesForCampbell(K,M,Kg_ref,bc,mesh,dof,params,num_modes_out)

if isfield(params,'campbell_modal_frequencies_hz') && ~isempty(params.campbell_modal_frequencies_hz)
    freq = params.campbell_modal_frequencies_hz(:);
    return
end

num_modes = max(num_modes_out,paramOrDefault(params,'number_of_modes',num_modes_out));
K_modal = K;
if isfield(params,'include_centrifugal_stiffening') && params.include_centrifugal_stiffening
    K_modal = K + Kg_ref;
end
modal_options = struct('normalization',paramOrDefault(params,'modal_normalization','max_displacement'));
modal = solveModalEigenproblem(K_modal,M,bc,num_modes,mesh,dof,modal_options);
freq = modal.frequencies_hz(:);

end

function title_text = campbellTitleForCase(params,output_name)

label = '';
if isfield(params,'case_label') && ~isempty(params.case_label)
    label = strtrim(char(string(params.case_label)));
end
if isempty(label)
    label = labelFromOutputName(output_name);
end

if isempty(label)
    title_text = 'Campbell Diagram';
elseif contains(lower(label),'campbell diagram')
    title_text = label;
else
    title_text = sprintf('%s Campbell Diagram',label);
end

end

function label = labelFromOutputName(output_name)

name = lower(regexprep(char(string(output_name)),'_+',' '));
name = strtrim(name);
if isempty(name)
    label = '';
    return
end

parts = split(string(name));
for i = 1:numel(parts)
    token = char(parts(i));
    switch token
        case {'mil','gmt'}
            token = upper(token);
        otherwise
            token = [upper(token(1)) token(2:end)]; %#ok<AGROW>
    end
    parts(i) = string(token);
end
label = char(strjoin(parts,' '));

end

function fig = createCampbellFigure(T,tol,title_text)

rpm = T.rpm;
mode_names = {'mode_1_Hz','mode_2_Hz','mode_3_Hz','mode_4_Hz','mode_5_Hz'};
exc_names = {'f_1x_Hz','f_2x_Hz','f_3x_Hz','f_4x_Hz'};
mode_colors = [0.000 0.447 0.741
               0.850 0.325 0.098
               0.466 0.674 0.188
               0.494 0.184 0.556
               0.301 0.745 0.933];
exc_color = [0.35 0.35 0.35];

fig = figure('Visible','off','Color','w','Units','inches','Position',[1 1 6.6 3.8]);
ax = axes(fig);
hold(ax,'on');
for mode = 1:numel(mode_names)
    plot(ax,rpm,positiveLogValues(T.(mode_names{mode})),'LineWidth',1.7, ...
        'Color',mode_colors(mode,:),'LineStyle','-', ...
        'DisplayName',sprintf('Mode %d',mode));
end
for h = 1:numel(exc_names)
    plot(ax,rpm,positiveLogValues(T.(exc_names{h})),'LineWidth',1.0, ...
        'Color',exc_color,'LineStyle','--', ...
        'DisplayName',sprintf('%dx',h));
end
plot(ax,rpm,positiveLogValues(T.f_bpf_Hz),'k--','LineWidth',2.0,'DisplayName','BPF');

markers = campbellCloseApproaches(T,tol);
if ~isempty(markers)
    scatter(ax,markers.rpm,markers.frequency_Hz,36,'o', ...
        'MarkerEdgeColor',[0.70 0.00 0.00], ...
        'MarkerFaceColor','w','LineWidth',1.0, ...
        'DisplayName','<10% separation');
end

grid(ax,'on');
ax.GridAlpha = 0.25;
ax.MinorGridAlpha = 0.12;
ax.XMinorTick = 'on';
ax.YMinorTick = 'on';
ax.TickDir = 'in';
ax.Box = 'on';
ax.YScale = 'log';
xlabel(ax,'RPM','FontSize',11,'Color','k');
ylabel(ax,'Frequency [Hz]','FontSize',11,'Color','k');
title(ax,title_text,'Interpreter','none','FontSize',11,'FontWeight','normal','Color','k');
legend(ax,'Location','eastoutside','Box','off','FontSize',10,'TextColor','k');
set(ax,'FontName','Times New Roman','FontSize',10,'LineWidth',0.8,'XColor','k','YColor','k');

all_values = table2array(T(:,2:end));
all_values = all_values(isfinite(all_values) & all_values > 0);
if ~isempty(all_values)
    ymin = max(0.5,0.8*min(all_values));
    ymax = max(all_values);
    if ymax > 0
        ylim(ax,[ymin 1.25*ymax]);
    end
end
xlim(ax,[min(rpm) max(rpm)]);

end

function values = positiveLogValues(values)

values(~isfinite(values) | values <= 0) = NaN;

end

function markers = campbellCloseApproaches(T,tol)

mode_names = {'mode_1_Hz','mode_2_Hz','mode_3_Hz','mode_4_Hz','mode_5_Hz'};
exc_names = {'f_1x_Hz','f_2x_Hz','f_3x_Hz','f_4x_Hz','f_bpf_Hz'};
rpm_mark = [];
freq_mark = [];
mode_id = [];
exc_id = [];

for i = 1:numel(mode_names)
    mode_freq = T.(mode_names{i});
    for j = 1:numel(exc_names)
        excitation = T.(exc_names{j});
        sep_values = abs(mode_freq - excitation)./max(abs(mode_freq),eps);
        valid = isfinite(sep_values);
        if ~any(valid)
            continue
        end
        valid_idx = find(valid);
        [sep,local_idx] = min(sep_values(valid));
        idx = valid_idx(local_idx);
        if ~isfinite(sep) || sep > tol
            continue
        end
        rpm_cross = crossingRpm(T.rpm,mode_freq,excitation);
        if isfinite(rpm_cross)
            freq_cross = interp1(T.rpm,mode_freq,rpm_cross,'linear');
            rpm_mark(end+1,1) = rpm_cross; %#ok<AGROW>
            freq_mark(end+1,1) = freq_cross; %#ok<AGROW>
        else
            rpm_mark(end+1,1) = T.rpm(idx); %#ok<AGROW>
            freq_mark(end+1,1) = excitation(idx); %#ok<AGROW>
        end
        mode_id(end+1,1) = i; %#ok<AGROW>
        exc_id(end+1,1) = j; %#ok<AGROW>
    end
end

if isempty(rpm_mark)
    markers = table();
else
    markers = table(rpm_mark,freq_mark,mode_id,exc_id, ...
        'VariableNames',{'rpm','frequency_Hz','mode','excitation'});
end

end

function rpm_cross = crossingRpm(rpm,mode_freq,excitation)

rpm_cross = NaN;
diff_values = excitation - mode_freq;
valid = isfinite(rpm) & isfinite(diff_values);
rpm = rpm(valid);
diff_values = diff_values(valid);
mode_freq = mode_freq(valid);
if numel(rpm) < 2
    return
end

zero_idx = find(abs(diff_values) <= 1e-10*max(abs(mode_freq),1),1,'first');
if ~isempty(zero_idx)
    rpm_cross = rpm(zero_idx);
    return
end

for k = 1:(numel(rpm) - 1)
    d1 = diff_values(k);
    d2 = diff_values(k+1);
    if d1*d2 < 0
        rpm_cross = rpm(k) + (0 - d1)*(rpm(k+1) - rpm(k))/(d2 - d1);
        return
    end
end

end

function summary = summarizeModeOneBpf(T)

mode_1 = T.mode_1_Hz;
bpf = T.f_bpf_Hz;
sep = abs(mode_1 - bpf)./max(abs(mode_1),eps);
valid = isfinite(sep);
if any(valid)
    valid_idx = find(valid);
    [min_sep,local_idx] = min(sep(valid));
    idx = valid_idx(local_idx);
    closest_rpm = T.rpm(idx);
else
    min_sep = NaN;
    closest_rpm = NaN;
end
rpm_cross = crossingRpm(T.rpm,mode_1,bpf);
if isfinite(rpm_cross)
    min_sep = 0;
    closest_rpm = rpm_cross;
end

summary = struct();
summary.min_sep_fraction = min_sep;
summary.min_sep_percent = 100*min_sep;
summary.closest_rpm = closest_rpm;

end

function results_root = findResultsRoot(results_dir)

results_root = results_dir;
current = results_dir;
while true
    [parent,name] = fileparts(current);
    if strcmpi(name,'results')
        results_root = current;
        return
    end
    if isempty(parent) || strcmp(parent,current)
        return
    end
    current = parent;
end

end

function saveFigureFormats(fig,png_path)

[folder,name] = fileparts(png_path);
if ~exist(folder,'dir')
    mkdir(folder);
end
png_file = fullfile(folder,[name '.png']);
pdf_file = fullfile(folder,[name '.pdf']);
svg_file = fullfile(folder,[name '.svg']);

if exist('exportgraphics','file')
    exportgraphics(fig,png_file,'Resolution',300);
    exportgraphics(fig,pdf_file,'ContentType','vector');
else
    saveas(fig,png_file);
    saveas(fig,pdf_file);
end
try
    saveas(fig,svg_file);
catch
end

end
