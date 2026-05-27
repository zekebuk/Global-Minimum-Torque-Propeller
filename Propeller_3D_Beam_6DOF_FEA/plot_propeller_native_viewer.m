function app = plot_propeller_native_viewer(results_csv,plot_plane)
%PLOT_PROPELLER_NATIVE_VIEWER Engineering viewer for propeller beam results.
%
% This dashboard reads a station-level beam results CSV and provides:
%   - 3D isometric twisted chord ribbon plots
%   - 2D projection plots
%   - spanwise engineering line plots
%   - internal force, section property, and load diagrams
%   - station probing, max/min summaries, comparison import, and exports
%
% The ribbon is a visualization of the reduced-order beam stations. It is
% not a shell/solid finite element mesh.

this_dir = fileparts(mfilename('fullpath'));
helpers_dir = fullfile(this_dir,'helpers');
if isfolder(helpers_dir)
    addpath(helpers_dir,'-begin');
end
applyPlotStyle();

if nargin < 1 || isempty(results_csv)
    results_csv = latest_results_csv();
end
if nargin < 2 || isempty(plot_plane)
    plot_plane = '3D isometric ribbon';
end

raw_table = readtable(results_csv,'VariableNamingRule','preserve');
station = buildStationData(raw_table);
modal_data = loadModalData(results_csv,station);
comparison = table();
last_summary = table();
last_plot = struct('values',[],'x',[],'label','','units','','series_name','');

initial_view = normalizeViewName(plot_plane);

fig = uifigure('Name','Propeller Beam Viewer','Position',[80 60 1380 760]);
main_layout = uigridlayout(fig,[1 3]);
main_layout.ColumnWidth = {330,'1x',360};
main_layout.RowHeight = {'1x'};

control_panel = uipanel(main_layout,'Title','Plot Controls');
control_panel.Layout.Row = 1;
control_panel.Layout.Column = 1;

control_outer = uigridlayout(control_panel,[2 1]);
control_outer.RowHeight = {42,'1x'};
control_outer.Padding = [8 8 8 8];

plot_button = uibutton(control_outer,'Text','PLOT / UPDATE','FontWeight','bold', ...
    'ButtonPushedFcn',@(~,~) drawPlot());
plot_button.Layout.Row = 1;
plot_button.Layout.Column = 1;

scroll_panel = uipanel(control_outer);
scroll_panel.Layout.Row = 2;
scroll_panel.Layout.Column = 1;
try
    scroll_panel.Scrollable = 'on';
catch
end

controls = uigridlayout(scroll_panel,[52 2]);
controls.ColumnWidth = {132,'1x'};
controls.RowHeight = repmat({30},1,52);
controls.RowSpacing = 4;
controls.Padding = [0 0 14 0];
try
    controls.Scrollable = 'on';
catch
end

row = 1;
[default_aero_station_csv,default_aero_summary_json] = defaultAeroLoadPaths();

addSectionLabel('Plot');
view_dropdown = addDropdown('View', ...
    {'3D isometric ribbon','planform','span-flap projection', ...
     'span-edge projection','spanwise line plot','internal force diagrams', ...
     'section property plots','load distribution plots', ...
     'simplified beam model', ...
     'deformed vs undeformed overlay','natural frequency table', ...
     'mode shape 3D','mode shape span-flap','mode shape span-edge', ...
     'Campbell diagram','comparison plot'},initial_view);

type_dropdown = addDropdown('Type',resultTypeItems(),'combined stress');
geometry_dropdown = addDropdown('Geometry',{'undeformed','deformed','both'},'both');
xaxis_dropdown = addDropdown('X Axis',{'r/R','r [m]','station'},'r/R');

addSectionLabel('Display');
addLabel('Deformation Scale');
deformation_scale_field = uieditfield(controls,'numeric','Value',1);
deformation_scale_field.Layout.Row = row;
deformation_scale_field.Layout.Column = 2;
row = row + 1;

addLabel('Mode Number');
mode_number_field = uieditfield(controls,'numeric','Value',1,'Limits',[1 Inf], ...
    'RoundFractionalValues','on');
mode_number_field.Layout.Row = row;
mode_number_field.Layout.Column = 2;
row = row + 1;

addLabel('Mode Shape Scale');
mode_shape_scale_field = uieditfield(controls,'numeric','Value',0.05);
mode_shape_scale_field.Layout.Row = row;
mode_shape_scale_field.Layout.Column = 2;
row = row + 1;

addLabel('Color Bands');
color_bands_field = uieditfield(controls,'numeric','Value',13,'Limits',[2 256], ...
    'RoundFractionalValues','on');
color_bands_field.Layout.Row = row;
color_bands_field.Layout.Column = 2;
row = row + 1;

stress_breakdown_checkbox = addCheckbox('Stress breakdown',true);
node_labels_checkbox = addCheckbox('Node numbers',false);
element_labels_checkbox = addCheckbox('Element numbers',false);
mesh_lines_checkbox = addCheckbox('Mesh lines',true);
show_load_arrows_checkbox = addCheckbox('Show load arrows',false);

addLabel('Every nth label');
label_stride_field = uieditfield(controls,'numeric','Value',5,'Limits',[1 1000], ...
    'RoundFractionalValues','on');
label_stride_field.Layout.Row = row;
label_stride_field.Layout.Column = 2;
row = row + 1;

addSectionLabel('Allowables');
addLabel('Tensile allowable MPa');
tensile_allowable_field = uieditfield(controls,'numeric','Value',250,'Limits',[eps Inf]);
tensile_allowable_field.Layout.Row = row;
tensile_allowable_field.Layout.Column = 2;
row = row + 1;

addLabel('Compressive allowable MPa');
compressive_allowable_field = uieditfield(controls,'numeric','Value',250,'Limits',[eps Inf]);
compressive_allowable_field.Layout.Row = row;
compressive_allowable_field.Layout.Column = 2;
row = row + 1;

addLabel('Shear allowable MPa');
shear_allowable_field = uieditfield(controls,'numeric','Value',145,'Limits',[eps Inf]);
shear_allowable_field.Layout.Row = row;
shear_allowable_field.Layout.Column = 2;
row = row + 1;

addSectionLabel('Probe');
addLabel('Probe station or r/R');
probe_field = uieditfield(controls,'text','Value','1', ...
    'ValueChangedFcn',@(~,~) probeStation());
probe_field.Layout.Row = row;
probe_field.Layout.Column = 2;
row = row + 1;

probe_button = uibutton(controls,'Text','Probe','ButtonPushedFcn',@(~,~) probeStation());
probe_button.Layout.Row = row;
probe_button.Layout.Column = [1 2];
row = row + 1;

addSectionLabel('Export / Advanced');
import_button = uibutton(controls,'Text','Import Hand/Abaqus CSV', ...
    'ButtonPushedFcn',@(~,~) importComparison());
import_button.Layout.Row = row;
import_button.Layout.Column = [1 2];
row = row + 1;

load_source_dropdown = addDropdown('Load Source',{'aero_solver_csv','legacy_uniform'},'aero_solver_csv');

addLabel('Aero stations CSV');
aero_station_csv_field = uieditfield(controls,'text','Value',default_aero_station_csv);
aero_station_csv_field.Layout.Row = row;
aero_station_csv_field.Layout.Column = 2;
row = row + 1;

addLabel('Aero summary JSON');
aero_summary_json_field = uieditfield(controls,'text','Value',default_aero_summary_json);
aero_summary_json_field.Layout.Row = row;
aero_summary_json_field.Layout.Column = 2;
row = row + 1;

import_aero_loads_button = uibutton(controls,'Text','Import Aero Loads', ...
    'ButtonPushedFcn',@(~,~) importAeroLoads());
import_aero_loads_button.Layout.Row = row;
import_aero_loads_button.Layout.Column = [1 2];
row = row + 1;

aero_load_status_text = uitextarea(controls,'Editable','off');
aero_load_status_text.Value = {'Aero loads not imported.'};
aero_load_status_text.Layout.Row = [row row + 1];
aero_load_status_text.Layout.Column = [1 2];
row = row + 2;

export_png_button = uibutton(controls,'Text','Export Current Plot PNG', ...
    'ButtonPushedFcn',@(~,~) exportCurrentPlotPNG());
export_png_button.Layout.Row = row;
export_png_button.Layout.Column = [1 2];
row = row + 1;

export_thesis_button = uibutton(controls,'Text','Export Thesis Figure', ...
    'ButtonPushedFcn',@(~,~) exportThesisFigure());
export_thesis_button.Layout.Row = row;
export_thesis_button.Layout.Column = [1 2];
row = row + 1;

export_station_button = uibutton(controls,'Text','Export Station CSV', ...
    'ButtonPushedFcn',@(~,~) exportStationTable());
export_station_button.Layout.Row = row;
export_station_button.Layout.Column = [1 2];
row = row + 1;

export_summary_button = uibutton(controls,'Text','Export Summary CSV', ...
    'ButtonPushedFcn',@(~,~) exportSummaryTable());
export_summary_button.Layout.Row = row;
export_summary_button.Layout.Column = [1 2];
row = row + 1;

export_modal_freq_button = uibutton(controls,'Text','Export Modal Frequencies CSV', ...
    'ButtonPushedFcn',@(~,~) exportModalFrequencies());
export_modal_freq_button.Layout.Row = row;
export_modal_freq_button.Layout.Column = [1 2];
row = row + 1;

export_mode_shape_button = uibutton(controls,'Text','Export Selected Mode CSV', ...
    'ButtonPushedFcn',@(~,~) exportSelectedModeShape());
export_mode_shape_button.Layout.Row = row;
export_mode_shape_button.Layout.Column = [1 2];
row = row + 1;

export_mode_png_button = uibutton(controls,'Text','Export Mode Shape Plot PNG', ...
    'ButtonPushedFcn',@(~,~) exportCurrentPlotPNG());
export_mode_png_button.Layout.Row = row;
export_mode_png_button.Layout.Column = [1 2];
row = row + 1;

ax = uiaxes(main_layout);
ax.Layout.Row = 1;
ax.Layout.Column = 2;

summary_panel = uipanel(main_layout,'Title','Validation Summary');
summary_panel.Layout.Row = 1;
summary_panel.Layout.Column = 3;
summary_layout = uigridlayout(summary_panel,[4 1]);
summary_layout.RowHeight = {24,'1x',24,'2x'};
summary_layout.Padding = [8 8 8 8];

uilabel(summary_layout,'Text','Max / Min Plotted Result');
summary_text = uitextarea(summary_layout,'Editable','off');
summary_text.Value = {'No plot summary yet.'};

uilabel(summary_layout,'Text','Station Probe');
probe_text = uitextarea(summary_layout,'Editable','off');
probe_text.Value = {'Enter r/R in [0, 1], station index > 1, element like e3, or radial coordinate in meters.'};

setPlotUpdateCallbacks();
drawPlot();
probeStation();

app = struct();
app.figure = fig;
app.axes = ax;
app.results_csv = results_csv;
app.station = station;
app.modal = modal_data;
app.raw_table = raw_table;
app.controls = struct('view',view_dropdown,'type',type_dropdown, ...
    'geometry',geometry_dropdown,'xaxis',xaxis_dropdown, ...
    'load_source',load_source_dropdown, ...
    'aero_station_csv',aero_station_csv_field, ...
    'aero_summary_json',aero_summary_json_field, ...
    'aero_load_status',aero_load_status_text, ...
    'show_load_arrows',show_load_arrows_checkbox, ...
    'deformation_scale',deformation_scale_field, ...
    'mode_number',mode_number_field,'mode_shape_scale',mode_shape_scale_field, ...
    'color_bands',color_bands_field);

    function setPlotUpdateCallbacks()
        redraw = @(~,~) drawPlot();
        view_dropdown.ValueChangedFcn = redraw;
        type_dropdown.ValueChangedFcn = redraw;
        geometry_dropdown.ValueChangedFcn = redraw;
        xaxis_dropdown.ValueChangedFcn = redraw;
        deformation_scale_field.ValueChangedFcn = redraw;
        mode_number_field.ValueChangedFcn = redraw;
        mode_shape_scale_field.ValueChangedFcn = redraw;
        color_bands_field.ValueChangedFcn = redraw;
        stress_breakdown_checkbox.ValueChangedFcn = redraw;
        node_labels_checkbox.ValueChangedFcn = redraw;
        element_labels_checkbox.ValueChangedFcn = redraw;
        mesh_lines_checkbox.ValueChangedFcn = redraw;
        show_load_arrows_checkbox.ValueChangedFcn = redraw;
        label_stride_field.ValueChangedFcn = redraw;
        tensile_allowable_field.ValueChangedFcn = redraw;
        compressive_allowable_field.ValueChangedFcn = redraw;
        shear_allowable_field.ValueChangedFcn = redraw;
    end

    function importAeroLoads()
        if strcmpi(load_source_dropdown.Value,'legacy_uniform')
            aero_load_status_text.Value = {'Load source is legacy_uniform.'; ...
                'No aero CSV import is required for this viewer session.'};
            return
        end

        try
            aero = load_aero_loads_for_fea(aero_station_csv_field.Value,aero_summary_json_field.Value);
            aero_load_status_text.Value = {
                sprintf('target_matched: %d',logical(aero.target_matched))
                sprintf('valid_for_thesis: %d',logical(aero.valid_for_thesis))
                sprintf('thrust: %.6g N',aero.thrust_N)
                sprintf('torque: %.6g N-m',aero.torque_Nm)
                sprintf('power: %.6g W',aero.power_W)
                };
        catch ME
            aero_load_status_text.Value = [{'Aero load import failed:'}; cellstr(string(ME.message))];
        end
    end

    function source_text = loadSourceForSummary()
        source_text = resultLoadSourceText(raw_table,results_csv);
        if isempty(source_text)
            source_text = sprintf('unavailable in results CSV; viewer selection = %s', ...
                char(string(load_source_dropdown.Value)));
        end
    end

    function [station_csv,summary_json] = defaultAeroLoadPaths()
        code_dir = fileparts(this_dir);
        [project_root,code_folder] = fileparts(code_dir);
        if ~strcmpi(code_folder,'Code')
            project_root = code_dir;
        end
        case_name = 'thesis_22_static_35N';
        station_csv = fullfile(project_root,'results','aero',[case_name '_stations.csv']);
        summary_json = fullfile(project_root,'results','aero',[case_name '_summary.json']);
    end

    function item = addDropdown(label_text,items,value)
        addLabel(label_text);
        item = uidropdown(controls,'Items',items,'Value',value);
        item.Layout.Row = row;
        item.Layout.Column = 2;
        row = row + 1;
    end

    function item = addCheckbox(label_text,value)
        item = uicheckbox(controls,'Text',label_text,'Value',value);
        item.Layout.Row = row;
        item.Layout.Column = [1 2];
        row = row + 1;
    end

    function addLabel(label_text)
        label = uilabel(controls,'Text',label_text);
        label.Layout.Row = row;
        label.Layout.Column = 1;
    end

    function addSectionLabel(label_text)
        label = uilabel(controls,'Text',label_text,'FontWeight','bold');
        label.Layout.Row = row;
        label.Layout.Column = [1 2];
        row = row + 1;
    end

    function drawPlot()
        resetAxes(ax,fig);
        hold(ax,'on');
        grid(ax,'on');

        view_mode = view_dropdown.Value;
        result_type = type_dropdown.Value;
        geometry_mode = geometry_dropdown.Value;
        x_axis_mode = xaxis_dropdown.Value;
        deformation_scale = deformation_scale_field.Value;
        color_bands = validateColorBands(color_bands_field);
        allowables = allowableInputs();

        switch view_mode
            case {'3D isometric ribbon','planform','span-flap projection','span-edge projection'}
                plotRibbonResult(ax,station,view_mode,result_type,geometry_mode, ...
                    deformation_scale,color_bands,allowables,mesh_lines_checkbox.Value,x_axis_mode);
                addRibbonLabelsIfNeeded(view_mode,geometry_mode,deformation_scale,x_axis_mode);
                [values,meta] = getResultVector(station,result_type,allowables);
                meta.x = get_x_axis_data(station,x_axis_mode);
                updateSummaryPanel(values,meta);

            case 'simplified beam model'
                stride = max(1,round(label_stride_field.Value));
                [plot_values,meta] = plotSimplifiedBeamModel(ax,station,geometry_mode, ...
                    deformation_scale,x_axis_mode,mesh_lines_checkbox.Value, ...
                    node_labels_checkbox.Value,element_labels_checkbox.Value, ...
                    stride,show_load_arrows_checkbox.Value);
                [summary_lines,last_summary] = simplifiedBeamSummary(station, ...
                    geometry_mode,meta,loadSourceForSummary());
                summary_text.Value = summary_lines;
                last_plot = struct('values',plot_values,'x',meta.x,'label',meta.label, ...
                    'units',meta.units,'series_name',meta.series_name);

            case 'deformed vs undeformed overlay'
                plotRibbonResult(ax,station,'3D isometric ribbon','mesh','both', ...
                    deformation_scale,color_bands,allowables,true,x_axis_mode);
                addRibbonLabelsIfNeeded('3D isometric ribbon','both',deformation_scale,x_axis_mode);
                [values,meta] = getResultVector(station,'displacement magnitude',allowables);
                meta.x = get_x_axis_data(station,x_axis_mode);
                updateSummaryPanel(values,meta);

            case 'natural frequency table'
                plotNaturalFrequencyTable(ax,modal_data);
                updateModalSummary();

            case 'mode shape 3D'
                [plot_values,meta] = plotModalShapeInViewer(ax,station,modal_data, ...
                    mode_number_field.Value,mode_shape_scale_field.Value,'3D',x_axis_mode);
                updateSummaryPanel(plot_values,meta);

            case 'mode shape span-flap'
                [plot_values,meta] = plotModalShapeInViewer(ax,station,modal_data, ...
                    mode_number_field.Value,mode_shape_scale_field.Value,'span-flap',x_axis_mode);
                updateSummaryPanel(plot_values,meta);

            case 'mode shape span-edge'
                [plot_values,meta] = plotModalShapeInViewer(ax,station,modal_data, ...
                    mode_number_field.Value,mode_shape_scale_field.Value,'span-edge',x_axis_mode);
                updateSummaryPanel(plot_values,meta);

            case 'Campbell diagram'
                [plot_values,meta] = plotCampbellFromModalFile(ax,results_csv);
                if isfield(meta,'summary_lines') && ~isempty(meta.summary_lines)
                    summary_text.Value = meta.summary_lines;
                else
                    summary_text.Value = {sprintf('Campbell diagram: %s',meta.label)};
                end
                last_plot = struct('values',plot_values,'x',meta.x,'label',meta.label, ...
                    'units',meta.units,'series_name',meta.series_name);

            case 'spanwise line plot'
                [plot_values,meta] = plotSpanwiseLine(ax,station,result_type,x_axis_mode, ...
                    allowables,stress_breakdown_checkbox.Value);
                updateSummaryPanel(plot_values,meta);

            case 'internal force diagrams'
                [plot_values,meta] = plotInternalForceDiagram(ax,station,x_axis_mode);
                updateSummaryPanel(plot_values,meta);

            case 'section property plots'
                [plot_values,meta] = plotSectionProperties(ax,station,result_type,x_axis_mode);
                updateSummaryPanel(plot_values,meta);

            case 'load distribution plots'
                [plot_values,meta] = plotLoadDistributions(ax,station,result_type,x_axis_mode);
                updateSummaryPanel(plot_values,meta);

            case 'comparison plot'
                [plot_values,meta] = plotComparison(ax,station,comparison,result_type,x_axis_mode);
                updateSummaryPanel(plot_values,meta);
        end

        applyAxesPlotStyle(ax);
        hold(ax,'off');
    end

    function addRibbonLabelsIfNeeded(view_mode,geometry_mode,deformation_scale,x_axis_mode)
        if ~node_labels_checkbox.Value && ~element_labels_checkbox.Value
            return
        end
        stride = max(1,round(label_stride_field.Value));
        [vertices0,verticesd,faces] = ribbonVertices(station,view_mode,deformation_scale,x_axis_mode);
        vertices = visibleVertices(vertices0,verticesd,geometry_mode);
        addRibbonLabels(ax,vertices,faces,node_labels_checkbox.Value, ...
            element_labels_checkbox.Value,stride);
    end

    function allowables = allowableInputs()
        allowables = struct();
        allowables.tensile_MPa = max(tensile_allowable_field.Value,eps);
        allowables.compressive_MPa = max(compressive_allowable_field.Value,eps);
        allowables.shear_MPa = max(shear_allowable_field.Value,eps);
        allowables.max_display_sf = 10;
    end

    function probeStation()
        allowables = allowableInputs();
        try
            probe_text.Value = stationProbeSummary(station,probe_field.Value,allowables);
        catch ME
            probe_text.Value = {sprintf('Probe failed: %s',ME.message)};
        end
    end

    function updateSummaryPanel(values,meta)
        try
            [summary_lines,last_summary] = resultSummary(station,values,meta);
            summary_text.Value = summary_lines;
            last_plot = struct('values',values,'x',meta.x,'label',meta.label, ...
                'units',meta.units,'series_name',meta.series_name);
        catch ME
            summary_text.Value = {sprintf('Summary unavailable: %s',ME.message)};
            last_summary = table();
        end
    end

    function updateModalSummary()
        if ~modal_data.available
            summary_text.Value = {'No modal frequency CSV found next to this results CSV.', ...
                'Run main_propeller_3d_beam_fea or run_mil_gmt_comparison with modal analysis enabled.'};
            last_summary = table();
            return
        end
        summary_text.Value = modalSummaryLines(modal_data);
        last_summary = modal_data.frequency_table;
        last_plot = struct('values',modal_data.frequencies_hz,'x',(1:numel(modal_data.frequencies_hz))', ...
            'label','natural frequencies','units','Hz','series_name','natural frequencies');
    end

    function importComparison()
        [file,path] = uigetfile('*.csv','Select hand/Abaqus comparison CSV');
        if isequal(file,0)
            return
        end
        try
            comparison = readtable(fullfile(path,file),'VariableNamingRule','preserve');
            summary_text.Value = {sprintf('Imported comparison CSV:'),fullfile(path,file), ...
                'Use View = comparison plot.'};
        catch ME
            uialert(fig,ME.message,'Comparison Import Failed');
        end
    end

    function exportCurrentPlotPNG()
        [file,path] = uiputfile('*.png','Export current plot as PNG','propeller_beam_plot.png');
        if isequal(file,0)
            return
        end
        exportgraphics(ax,fullfile(path,file),'Resolution',300);
    end

    function exportThesisFigure()
        old_node_labels = node_labels_checkbox.Value;
        old_element_labels = element_labels_checkbox.Value;

        out_dir = fullfile(projectRootFromFeaDir(this_dir),'figures_paper','fea','beam_solver');
        if ~isfolder(out_dir)
            mkdir(out_dir);
        end

        view_name = view_dropdown.Value;
        type_name = type_dropdown.Value;
        x_name = xaxis_dropdown.Value;
        stem = safeFileStem(sprintf('%s_%s_%s',view_name,type_name,x_name));
        exported = false;

        try
            node_labels_checkbox.Value = false;
            element_labels_checkbox.Value = false;
            drawPlot();

            exportgraphics(ax,fullfile(out_dir,[stem '.svg']),'ContentType','vector');
            exportgraphics(ax,fullfile(out_dir,[stem '.png']),'Resolution',300);
            exported = true;
        catch ME
            uialert(fig,ME.message,'Thesis Export Failed');
        end

        node_labels_checkbox.Value = old_node_labels;
        element_labels_checkbox.Value = old_element_labels;
        drawPlot();
        if exported
            summary_text.Value = [summary_text.Value; { ...
                sprintf('Thesis figure exported to: %s',out_dir), ...
                sprintf('Files: %s.svg/.png',stem)}];
        end
    end

    function exportStationTable()
        [file,path] = uiputfile('*.csv','Export station results CSV','propeller_station_results.csv');
        if isequal(file,0)
            return
        end
        writetable(station.table,fullfile(path,file));
    end

    function exportSummaryTable()
        [file,path] = uiputfile('*.csv','Export summary CSV','propeller_summary.csv');
        if isequal(file,0)
            return
        end
        if isempty(last_summary)
            writetable(table("No summary available",'VariableNames',{'message'}),fullfile(path,file));
        else
            writetable(last_summary,fullfile(path,file));
        end
    end

    function exportModalFrequencies()
        if ~modal_data.available
            uialert(fig,'No modal frequencies are available for this results CSV.','Modal Export');
            return
        end
        [file,path] = uiputfile('*.csv','Export modal frequencies CSV','modal_frequencies.csv');
        if isequal(file,0)
            return
        end
        writetable(modal_data.frequency_table,fullfile(path,file));
    end

    function exportSelectedModeShape()
        if ~modal_data.available
            uialert(fig,'No mode shapes are available for this results CSV.','Modal Export');
            return
        end
        mode_id = selectedModeNumber(modal_data,mode_number_field.Value);
        [file,path] = uiputfile('*.csv','Export selected mode shape CSV', ...
            sprintf('mode_%02d_shape.csv',mode_id));
        if isequal(file,0)
            return
        end
        writetable(selectedModeShapeTable(modal_data,station,mode_id),fullfile(path,file));
    end
end

function items = resultTypeItems()

items = {'mesh', ...
    'combined stress', ...
    'axial / centrifugal stress', ...
    'flapwise bending stress', ...
    'edgewise bending stress', ...
    'torsional shear stress', ...
    'von Mises / equivalent stress', ...
    'max tensile stress', ...
    'max compressive stress', ...
    'displacement magnitude', ...
    'flapwise displacement', ...
    'edgewise displacement', ...
    'axial displacement', ...
    'twist angle', ...
    'safety factor', ...
    'axial force N', ...
    'edgewise shear Vy', ...
    'flapwise shear Vz', ...
    'torsional moment T', ...
    'flapwise bending moment My', ...
    'edgewise bending moment Mz', ...
    'chord', ...
    'twist distribution', ...
    'area', ...
    'Iy', ...
    'Iz', ...
    'J', ...
    'mass per length', ...
    'thrust load', ...
    'edgewise load', ...
    'centrifugal load', ...
    'torque distribution', ...
    'comparison percent error'};

end

function station = buildStationData(T)

n = height(T);
nan_col = NaN(n,1);

station = struct();
station.source_table = T;
station.r = getColumn(T,{'r_m','r'},nan_col);
station.mu = getColumn(T,{'r/R','r_over_R','mu'},nan_col);
if all(~isfinite(station.mu)) && any(isfinite(station.r))
    station.mu = station.r/max(station.r);
end
station.chord = getColumn(T,{'chord_m','chord'},nan_col);
station.twist_deg = getColumn(T,{'twist_deg','beta_deg'},zeros(n,1));
station.A = getColumn(T,{'A','area','A_m2'},nan_col);
station.Iy = getColumn(T,{'Iy','Iy_m4'},nan_col);
station.Iz = getColumn(T,{'Iz','Iz_m4'},nan_col);
station.J = getColumn(T,{'J','J_m4'},nan_col);
station.mass_per_length = getColumn(T,{'mass_per_length','mass_per_length_kg_m'},nan_col);

station.ux = getColumn(T,{'ux'},zeros(n,1));
station.uy = getColumn(T,{'uy'},zeros(n,1));
station.uz = getColumn(T,{'uz'},zeros(n,1));
station.rx = getColumn(T,{'rx'},zeros(n,1));
station.ry = getColumn(T,{'ry'},zeros(n,1));
station.rz = getColumn(T,{'rz'},zeros(n,1));
station.displacement_magnitude = getColumn(T,{'displacement_magnitude','u_mag'}, ...
    sqrt(station.ux.^2 + station.uy.^2 + station.uz.^2));
station.twist_angle_deg = getColumn(T,{'twist_angle_deg'},station.rx*180/pi);

station.N = getColumn(T,{'N','N_N'},nan_col);
station.Vy = getColumn(T,{'Vy_edgewise','Vy_edgewise_N','Vy'},nan_col);
station.Vz = getColumn(T,{'Vz_flapwise','Vz_flapwise_N','Vz'},nan_col);
station.T = getColumn(T,{'Mx_torsion','Mx_torsion_Nm','T','torsional_moment'},nan_col);
station.My = getColumn(T,{'My_bending','My_bending_Nm','My'},nan_col);
station.Mz = getColumn(T,{'Mz_bending','Mz_bending_Nm','Mz'},nan_col);

station.q_centrifugal = getColumn(T,{'q_centrifugal','q_x_centrifugal_N_m'},nan_col);
station.q_edgewise = getColumn(T,{'q_edgewise','q_y_edgewise_N_m'},nan_col);
station.q_thrust = getColumn(T,{'q_thrust','q_z_thrust_N_m'},nan_col);
station.m_torsion_dist = getColumn(T,{'m_torsion_dist','m_x_torsion_Nm_m'},nan_col);

station.sigma_axial_MPa = getColumn(T,{'sigma_axial_MPa'}, ...
    getColumn(T,{'sigma_axial'},nan_col)/1e6);
station.sigma_flap_MPa = getColumn(T,{'sigma_flap_MPa'}, ...
    getColumn(T,{'sigma_flap'},nan_col)/1e6);
station.sigma_edge_MPa = getColumn(T,{'sigma_edge_MPa'}, ...
    getColumn(T,{'sigma_edge'},nan_col)/1e6);
station.tau_torsion_MPa = getColumn(T,{'tau_torsion_MPa'}, ...
    getColumn(T,{'tau_torsion'},nan_col)/1e6);
station.sigma_combined_input_MPa = getColumn(T,{'sigma_combined_MPa'}, ...
    getColumn(T,{'sigma_combined'},nan_col)/1e6);

signed_combined = getColumn(T,{'sigma_combined_signed_MPa'}, ...
    getColumn(T,{'sigma_combined_signed'},nan_col)/1e6);
if all(~isfinite(signed_combined))
    signed_combined = station.sigma_axial_MPa + station.sigma_flap_MPa + station.sigma_edge_MPa;
end
station.sigma_combined_signed_MPa = signed_combined;

% Recompute the section stress envelope from beam resultants and critical
% y/z section points. CSVs written before this correction may contain a
% cancelled algebraic von Mises value, so the viewer does not trust the
% imported sigma_von_mises column when the required resultants are present.
envelope = computeSectionStressEnvelope(station,station,[]);
station.sigma_combined_MPa = envelope.sigma_abs_normal_max_Pa/1e6;
station.sigma_vm_max_MPa = envelope.sigma_vm_max_Pa/1e6;
station.sigma_vm_min_MPa = envelope.sigma_vm_min_Pa/1e6;
station.sigma_x_max_tension_MPa = envelope.sigma_x_max_tension_Pa/1e6;
station.sigma_x_max_compression_MPa = envelope.sigma_x_max_compression_Pa/1e6;
station.sigma_abs_normal_max_MPa = envelope.sigma_abs_normal_max_Pa/1e6;
station.tau_torsion_envelope_MPa = envelope.tau_torsion_max_Pa/1e6;
station.critical_section_point = envelope.critical_section_point;
station.critical_section_point_index = envelope.critical_section_point_index;
station.torsional_shear_assumed_zero = envelope.torsional_shear_assumed_zero;
station.stress_envelope_note = envelope.note;

imported_vm_MPa = getColumn(T,{'sigma_vm_max_MPa'}, ...
    getColumn(T,{'sigma_vm_max'},nan_col)/1e6);
if all(~isfinite(imported_vm_MPa))
    imported_vm_MPa = getColumn(T,{'sigma_von_mises_MPa'}, ...
    getColumn(T,{'sigma_von_mises'},nan_col)/1e6);
end
invalid_envelope = all(~isfinite(station.sigma_vm_max_MPa));
if invalid_envelope
    station.sigma_combined_MPa = station.sigma_combined_input_MPa;
    station.sigma_vm_max_MPa = imported_vm_MPa;
    station.sigma_vm_min_MPa = imported_vm_MPa;
    station.sigma_abs_normal_max_MPa = station.sigma_combined_input_MPa;
    station.tau_torsion_envelope_MPa = abs(station.tau_torsion_MPa);
    station.critical_section_point = repmat({'unavailable'},n,1);
    station.critical_section_point_index = NaN(n,1);
    station.torsional_shear_assumed_zero = false(n,1);
end
station.sigma_von_mises_MPa = station.sigma_vm_max_MPa;
station.sigma_max_tensile_MPa = station.sigma_x_max_tension_MPa;
station.sigma_max_compressive_MPa = station.sigma_x_max_compression_MPa;

if all(~isfinite(station.sigma_max_tensile_MPa))
    station.sigma_max_tensile_MPa = getColumn(T,{'sigma_max_tensile_MPa'}, ...
        getColumn(T,{'sigma_max_tensile'},nan_col)/1e6);
end
if all(~isfinite(station.sigma_max_compressive_MPa))
    station.sigma_max_compressive_MPa = getColumn(T,{'sigma_max_compressive_MPa'}, ...
        getColumn(T,{'sigma_max_compressive'},nan_col)/1e6);
end
if all(~isfinite(station.sigma_x_max_tension_MPa))
    station.sigma_x_max_tension_MPa = station.sigma_max_tensile_MPa;
end
if all(~isfinite(station.sigma_x_max_compression_MPa))
    station.sigma_x_max_compression_MPa = station.sigma_max_compressive_MPa;
end

station.n = n;
station.table = buildAugmentedStationTable(station);

end

function T = buildAugmentedStationTable(station)

T = table(station.mu,station.r,station.chord,station.twist_deg,station.A, ...
    station.Iy,station.Iz,station.J,station.mass_per_length,station.ux, ...
    station.uy,station.uz,station.rx,station.ry,station.rz, ...
    station.displacement_magnitude,station.twist_angle_deg,station.N, ...
    station.Vy,station.Vz,station.T,station.My,station.Mz, ...
    station.q_centrifugal,station.q_edgewise,station.q_thrust, ...
    station.m_torsion_dist,station.sigma_axial_MPa,station.sigma_flap_MPa, ...
    station.sigma_edge_MPa,station.tau_torsion_MPa, ...
    station.sigma_combined_MPa,station.sigma_combined_signed_MPa, ...
    station.sigma_von_mises_MPa,station.sigma_max_tensile_MPa, ...
    station.sigma_max_compressive_MPa,station.sigma_vm_max_MPa, ...
    station.sigma_vm_min_MPa,station.sigma_x_max_tension_MPa, ...
    station.sigma_x_max_compression_MPa,station.sigma_abs_normal_max_MPa, ...
    station.tau_torsion_envelope_MPa,station.critical_section_point, ...
    station.torsional_shear_assumed_zero, ...
    'VariableNames',{'r_over_R','r_m','chord_m','twist_deg','A','Iy','Iz','J', ...
    'mass_per_length','ux','uy','uz','rx','ry','rz','displacement_magnitude', ...
    'twist_angle_deg','N','Vy_edgewise','Vz_flapwise','Mx_torsion', ...
    'My_bending','Mz_bending','q_centrifugal','q_edgewise','q_thrust', ...
    'm_torsion_dist','sigma_axial_MPa','sigma_flap_MPa','sigma_edge_MPa', ...
    'tau_torsion_MPa','sigma_combined_MPa','sigma_combined_signed_MPa', ...
    'sigma_von_mises_MPa','sigma_max_tensile_MPa','sigma_max_compressive_MPa', ...
    'sigma_vm_max_MPa','sigma_vm_min_MPa','sigma_x_max_tension_MPa', ...
    'sigma_x_max_compression_MPa','sigma_abs_normal_max_MPa', ...
    'tau_torsion_envelope_MPa','critical_section_point', ...
    'torsional_shear_assumed_zero'});

end

function values = getColumn(T,candidates,default_values)

names = T.Properties.VariableNames;
norm_names = normalizeNames(names);
values = [];
for i = 1:numel(candidates)
    target = normalizeNames(candidates{i});
    idx = find(strcmp(norm_names,target),1,'first');
    if ~isempty(idx)
        values = T.(names{idx});
        values = double(values(:));
        return
    end
end
values = default_values(:);

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

function [values,meta] = getResultVector(station,result_type,allowables)
%GETRESULTVECTOR Return station result in display units.

switch result_type
    case 'mesh'
        values = NaN(station.n,1);
        meta = resultMeta(station,'mesh','',station.mu);
        meta.is_scalar = false;
        meta.summary_kind = 'mesh';
    case 'combined stress'
        values = station.sigma_combined_MPa;
        meta = resultMeta(station,'combined stress max normal envelope','MPa',station.mu);
    case 'axial / centrifugal stress'
        values = station.sigma_axial_MPa;
        meta = resultMeta(station,'axial / centrifugal stress','MPa',station.mu);
    case 'flapwise bending stress'
        values = station.sigma_flap_MPa;
        meta = resultMeta(station,'flapwise bending stress','MPa',station.mu);
    case 'edgewise bending stress'
        values = station.sigma_edge_MPa;
        meta = resultMeta(station,'edgewise bending stress','MPa',station.mu);
    case 'torsional shear stress'
        values = station.tau_torsion_MPa;
        meta = resultMeta(station,'torsional shear stress','MPa',station.mu);
    case 'von Mises / equivalent stress'
        values = station.sigma_vm_max_MPa;
        meta = resultMeta(station,'von Mises max section envelope','MPa',station.mu);
    case 'max tensile stress'
        values = station.sigma_max_tensile_MPa;
        meta = resultMeta(station,'max tensile stress','MPa',station.mu);
    case 'max compressive stress'
        values = station.sigma_max_compressive_MPa;
        meta = resultMeta(station,'max compressive stress','MPa',station.mu);
    case 'displacement magnitude'
        values = station.displacement_magnitude;
        meta = resultMeta(station,'displacement magnitude','m',station.mu);
    case 'flapwise displacement'
        values = station.uz;
        meta = resultMeta(station,'flapwise displacement','m',station.mu);
    case 'edgewise displacement'
        values = station.uy;
        meta = resultMeta(station,'edgewise displacement','m',station.mu);
    case 'axial displacement'
        values = station.ux;
        meta = resultMeta(station,'axial displacement','m',station.mu);
    case 'twist angle'
        values = station.twist_angle_deg;
        meta = resultMeta(station,'twist angle','deg',station.mu);
    case 'safety factor'
        values = safetyFactorVector(station,allowables);
        meta = resultMeta(station,'safety factor','unitless',station.mu);
    case 'axial force N'
        values = station.N;
        meta = resultMeta(station,'axial force N','N',station.mu);
    case 'edgewise shear Vy'
        values = station.Vy;
        meta = resultMeta(station,'edgewise shear Vy','N',station.mu);
    case 'flapwise shear Vz'
        values = station.Vz;
        meta = resultMeta(station,'flapwise shear Vz','N',station.mu);
    case 'torsional moment T'
        values = station.T;
        meta = resultMeta(station,'torsional moment T','N*m',station.mu);
    case 'flapwise bending moment My'
        values = station.My;
        meta = resultMeta(station,'flapwise bending moment My','N*m',station.mu);
    case 'edgewise bending moment Mz'
        values = station.Mz;
        meta = resultMeta(station,'edgewise bending moment Mz','N*m',station.mu);
    case 'chord'
        values = station.chord;
        meta = resultMeta(station,'chord','m',station.mu);
    case 'twist distribution'
        values = station.twist_deg;
        meta = resultMeta(station,'twist distribution','deg',station.mu);
    case 'area'
        values = station.A;
        meta = resultMeta(station,'area','m^2',station.mu);
    case 'Iy'
        values = station.Iy;
        meta = resultMeta(station,'Iy','m^4',station.mu);
    case 'Iz'
        values = station.Iz;
        meta = resultMeta(station,'Iz','m^4',station.mu);
    case 'J'
        values = station.J;
        meta = resultMeta(station,'J','m^4',station.mu);
    case 'mass per length'
        values = station.mass_per_length;
        meta = resultMeta(station,'mass per length','kg/m',station.mu);
    case 'thrust load'
        values = station.q_thrust;
        meta = resultMeta(station,'thrust load','N/m',station.mu);
    case 'edgewise load'
        values = station.q_edgewise;
        meta = resultMeta(station,'edgewise load','N/m',station.mu);
    case 'centrifugal load'
        values = station.q_centrifugal;
        meta = resultMeta(station,'centrifugal load','N/m',station.mu);
    case 'torque distribution'
        values = station.m_torsion_dist;
        meta = resultMeta(station,'torque distribution','N*m/m',station.mu);
    otherwise
        warning('Requested result "%s" is unavailable. Showing combined stress.',result_type);
        values = station.sigma_combined_MPa;
        meta = resultMeta(station,'combined stress max normal envelope','MPa',station.mu);
end

end

function meta = resultMeta(station,label,units,x)

meta = struct();
meta.label = label;
meta.units = units;
meta.x = x(:);
meta.r = station.r(:);
meta.mu = station.mu(:);
meta.series_name = label;
meta.is_scalar = true;
meta.summary_kind = 'scalar';

end

function sf = safetyFactorVector(station,allowables)

normal = max(abs([station.sigma_vm_max_MPa, station.sigma_combined_MPa, ...
    station.sigma_x_max_tension_MPa, station.sigma_x_max_compression_MPa, ...
    station.sigma_axial_MPa, station.sigma_flap_MPa, station.sigma_edge_MPa]),[],2,'omitnan');
shear = max(abs([station.tau_torsion_MPa, station.tau_torsion_envelope_MPa]),[],2,'omitnan');

normal_sf = allowables.tensile_MPa./max(normal,eps);
shear_sf = allowables.shear_MPa./max(shear,eps);
sf = min(normal_sf,shear_sf);
sf(~isfinite(sf)) = NaN;
sf = min(sf,allowables.max_display_sf);

end

function plotRibbonResult(ax,station,view_mode,result_type,geometry_mode, ...
    deformation_scale,color_bands,allowables,show_mesh_lines,x_axis_mode)

[vertices0,verticesd,faces] = ribbonVertices(station,view_mode,deformation_scale,x_axis_mode);
[~,x_label] = get_x_axis_data(station,x_axis_mode);
[values,meta] = getResultVector(station,result_type,allowables);
vertex_values = stationToVertexValues(values);
edge_color = [0.18 0.18 0.18];
if ~show_mesh_lines
    edge_color = 'none';
end

if strcmp(result_type,'mesh')
    vertex_values = [];
end

if strcmp(geometry_mode,'undeformed') || strcmp(geometry_mode,'both')
    if strcmp(result_type,'mesh')
        patch(ax,'Faces',faces,'Vertices',vertices0,'FaceColor','none', ...
            'EdgeColor',[0.6 0.6 0.6],'LineStyle','--','LineWidth',1.0);
    else
        patch(ax,'Faces',faces,'Vertices',vertices0,'FaceColor',[0.86 0.86 0.86], ...
            'FaceAlpha',0.25,'EdgeColor',[0.65 0.65 0.65], ...
            'LineStyle','--','LineWidth',0.8);
    end
end

if strcmp(geometry_mode,'deformed') || strcmp(geometry_mode,'both')
    if strcmp(result_type,'mesh')
        patch(ax,'Faces',faces,'Vertices',verticesd,'FaceColor','none', ...
            'EdgeColor',[0 0.25 0.85],'LineWidth',0.8);
    else
        finite_values = vertex_values(isfinite(vertex_values));
        if isempty(finite_values)
            warning('No finite values available for %s.',result_type);
            vertex_values(:) = 0;
            clim = [0 1];
        else
            clim = finiteLimits(finite_values);
        end
        vertex_values = quantizeForBands(vertex_values,color_bands,clim);
        patch(ax,'Faces',faces,'Vertices',verticesd, ...
            'FaceVertexCData',vertex_values,'FaceColor','interp', ...
            'EdgeColor',edge_color,'LineWidth',0.8);
        colormap(ax,colormapForResult(result_type,color_bands));
        cb = colorbar(ax);
        ylabel(cb,sprintf('%s (%s)',meta.label,meta.units));
        ax.CLim = clim;
    end
end

viewLabelAxes(ax,view_mode,visibleVertices(vertices0,verticesd,geometry_mode),x_label);
title(ax,sprintf('%s - %s',meta.label,view_mode),'Interpreter','none');

end

function [vertices0,verticesd,faces] = ribbonVertices(station,view_mode,deformation_scale,x_axis_mode)

switch view_mode
    case '3D isometric ribbon'
        [vertices0,verticesd,faces] = ribbonVertices3D(station,deformation_scale,x_axis_mode);
    case 'deformed vs undeformed overlay'
        [vertices0,verticesd,faces] = ribbonVertices3D(station,deformation_scale,x_axis_mode);
    case 'planform'
        ux_axis = spanDisplacementForAxis(station,x_axis_mode);
        [vertices0,verticesd,faces] = ribbonVertices2D(station,0.5*station.chord, ...
            [ux_axis station.uy],deformation_scale,x_axis_mode);
    case 'span-flap projection'
        half_width = 0.015*max(station.r - min(station.r))*ones(station.n,1);
        ux_axis = spanDisplacementForAxis(station,x_axis_mode);
        [vertices0,verticesd,faces] = ribbonVertices2D(station,half_width, ...
            [ux_axis station.uz],deformation_scale,x_axis_mode);
    case 'span-edge projection'
        half_width = 0.015*max(station.r - min(station.r))*ones(station.n,1);
        ux_axis = spanDisplacementForAxis(station,x_axis_mode);
        [vertices0,verticesd,faces] = ribbonVertices2D(station,half_width, ...
            [ux_axis station.uy],deformation_scale,x_axis_mode);
    otherwise
        [vertices0,verticesd,faces] = ribbonVertices3D(station,deformation_scale,x_axis_mode);
end

end

function [vertices0,verticesd,faces] = ribbonVertices3D(station,deformation_scale,x_axis_mode)

n = station.n;
vertices0 = zeros(2*n,3);
verticesd = zeros(2*n,3);
[span_x,~] = get_x_axis_data(station,x_axis_mode);
ux_axis = spanDisplacementForAxis(station,x_axis_mode);
for i = 1:n
    center0 = [span_x(i), 0, 0];
    beta = station.twist_deg(i)*pi/180;
    chord_axis = [0, cos(beta), sin(beta)];
    half_chord = 0.5*station.chord(i);
    offsets = [-half_chord*chord_axis; half_chord*chord_axis];
    centerd = center0 + deformation_scale*[ux_axis(i), station.uy(i), station.uz(i)];
    theta = deformation_scale*[station.rx(i), station.ry(i), station.rz(i)];
    for j = 1:2
        id = 2*i - 2 + j;
        vertices0(id,:) = center0 + offsets(j,:);
        verticesd(id,:) = centerd + offsets(j,:) + cross(theta,offsets(j,:));
    end
end
faces = ribbonFaces(n);

end

function [vertices0,verticesd,faces] = ribbonVertices2D(station,half_width,uv,deformation_scale,x_axis_mode)

n = station.n;
vertices0 = zeros(2*n,2);
verticesd = zeros(2*n,2);
[span_x,~] = get_x_axis_data(station,x_axis_mode);
for i = 1:n
    ids = [2*i - 1, 2*i];
    vertices0(ids,:) = [span_x(i), -half_width(i); span_x(i), half_width(i)];
    verticesd(ids,:) = vertices0(ids,:) + deformation_scale*[uv(i,:); uv(i,:)];
end
faces = ribbonFaces(n);

end

function ux_axis = spanDisplacementForAxis(station,x_axis_mode)
%SPANDisplacementForAxis Express axial displacement in current x-axis units.

mode = lower(strtrim(string(x_axis_mode)));
switch mode
    case {"r/r","rr","r_over_r","r over r","mu"}
        radius = max(station.r,[],'omitnan');
        ux_axis = station.ux./max(radius,eps);
    case {"station","station index","station_index"}
        dr = diff(station.r(:));
        dr = dr(isfinite(dr) & abs(dr) > eps);
        if isempty(dr)
            ux_axis = zeros(size(station.ux));
        else
            ux_axis = station.ux./median(abs(dr));
        end
    otherwise
        ux_axis = station.ux;
end

end

function faces = ribbonFaces(n)

faces = zeros(n - 1,4);
for e = 1:n - 1
    faces(e,:) = [2*e - 1, 2*(e + 1) - 1, 2*(e + 1), 2*e];
end

end

function vertex_values = stationToVertexValues(values)

vertex_values = zeros(2*numel(values),1);
vertex_values(1:2:end) = values;
vertex_values(2:2:end) = values;

end

function plot_values = quantizeForBands(values,color_bands,clim)

plot_values = values;
finite = isfinite(values);
if ~any(finite) || clim(1) == clim(2)
    return
end
edges = linspace(clim(1),clim(2),color_bands + 1);
centers = 0.5*(edges(1:end-1) + edges(2:end));
[~,~,bin] = histcounts(values(finite),edges);
bin(bin == 0 & values(finite) <= edges(1)) = 1;
bin(bin == 0 & values(finite) >= edges(end)) = color_bands;
plot_values(finite) = centers(max(1,min(color_bands,bin)));

end

function cmap = colormapForResult(~,color_bands)

cmap = parula(color_bands);

end

function viewLabelAxes(ax,view_mode,pts,x_label)

if isempty(pts)
    return
end
switch view_mode
    case '3D isometric ribbon'
        view(ax,35,25);
        axis(ax,'equal');
        axis(ax,'vis3d');
        xlim(ax,paddedRange(pts(:,1),0.08));
        ylim(ax,paddedRange(pts(:,2),0.12));
        zlim(ax,paddedRange(pts(:,3),0.12));
        xlabel(ax,x_label);
        ylabel(ax,'Chordwise/edgewise coordinate [m]');
        zlabel(ax,'Flapwise/thickness coordinate [m]');
    otherwise
        view(ax,2);
        ax.DataAspectRatioMode = 'auto';
        ax.PlotBoxAspectRatioMode = 'auto';
        xlim(ax,paddedRange(pts(:,1),0.08));
        ylim(ax,paddedRange(pts(:,2),0.18));
        xlabel(ax,x_label);
        ylabel(ax,'Projected coordinate [m]');
        zlabel(ax,'');
end

end

function pts = visibleVertices(vertices0,verticesd,geometry_mode)

switch geometry_mode
    case 'undeformed'
        pts = vertices0;
    case 'deformed'
        pts = verticesd;
    otherwise
        pts = [vertices0; verticesd];
end
pts = pts(all(isfinite(pts),2),:);

end

function addRibbonLabels(ax,vertices,faces,show_nodes,show_elements,stride)

if isempty(vertices)
    return
end
if show_nodes
    for ni = 1:stride:size(vertices,1)
        addTextAtPoint(ax,vertices(ni,:),sprintf('%d',ni),[0.1 0.1 0.1]);
    end
end
if show_elements
    for ei = 1:stride:size(faces,1)
        center = mean(vertices(faces(ei,:),:),1);
        addTextAtPoint(ax,center,sprintf('e%d',ei),[0.65 0 0]);
    end
end

end

function addTextAtPoint(ax,pt,label,color)

if numel(pt) == 3
    text(ax,pt(1),pt(2),pt(3),label,'FontSize',10,'FontName','Times New Roman','Color',color, ...
        'HorizontalAlignment','center');
else
    text(ax,pt(1),pt(2),label,'FontSize',10,'FontName','Times New Roman','Color',color, ...
        'HorizontalAlignment','center');
end

end

function [plot_values,meta] = plotSimplifiedBeamModel(ax,station,geometry_mode, ...
    deformation_scale,x_axis_mode,show_mesh_lines,show_nodes,show_elements, ...
    label_stride,show_load_arrows)

[center0,centerd,x_label,span_x] = beamCenterlinePoints(station,deformation_scale,x_axis_mode);
show_undeformed_only = strcmp(geometry_mode,'undeformed');

if show_undeformed_only
    plotBeamCenterline(ax,center0,show_mesh_lines,[0.25 0.25 0.25],'--','undeformed');
    label_points = center0;
    visible_points = center0;
else
    plotBeamCenterline(ax,center0,show_mesh_lines,[0.70 0.70 0.70],'--','undeformed');
    plotBeamCenterline(ax,centerd,show_mesh_lines,[0.00 0.25 0.85],'-','deformed');
    label_points = centerd;
    visible_points = [center0; centerd];
end

if show_nodes
    for node_id = 1:label_stride:station.n
        addTextAtPoint(ax,label_points(node_id,:),sprintf('n%d',node_id),[0.1 0.1 0.1]);
    end
end
if show_elements && show_mesh_lines
    for elem_id = 1:label_stride:max(station.n - 1,0)
        center = 0.5*(label_points(elem_id,:) + label_points(elem_id + 1,:));
        addTextAtPoint(ax,center,sprintf('e%d',elem_id),[0.65 0 0]);
    end
end

if show_load_arrows
    addSimplifiedBeamLoadArrows(ax,station,label_points);
end

grid(ax,'on');
view(ax,35,25);
ax.DataAspectRatioMode = 'auto';
ax.PlotBoxAspectRatioMode = 'auto';
xlabel(ax,x_label);
ylabel(ax,'Chordwise/edgewise coordinate [m]');
zlabel(ax,'Flapwise/thickness coordinate [m]');
title(ax,'simplified 1D beam model','Interpreter','none');
xlim(ax,paddedRange(visible_points(:,1),0.08));
ylim(ax,paddedRange(visible_points(:,2),0.18));
zlim(ax,paddedRange(visible_points(:,3),0.18));
legend(ax,'show','Location','best');

plot_values = station.displacement_magnitude(:);
meta = resultMeta(station,'simplified beam model','m',span_x);
meta.summary_kind = 'beam_model';
meta.series_name = 'simplified beam model';

end

function [center0,centerd,x_label,span_x] = beamCenterlinePoints(station,deformation_scale,x_axis_mode)

[span_x,x_label] = get_x_axis_data(station,x_axis_mode);
ux_axis = spanDisplacementForAxis(station,x_axis_mode);
center0 = [span_x(:), zeros(station.n,1), zeros(station.n,1)];
centerd = center0 + deformation_scale*[ux_axis(:), station.uy(:), station.uz(:)];

end

function plotBeamCenterline(ax,points,show_mesh_lines,color,line_style,display_name)

if show_mesh_lines
    plot3(ax,points(:,1),points(:,2),points(:,3),'o-', ...
        'Color',color,'LineStyle',line_style,'MarkerFaceColor','w', ...
        'MarkerSize',5,'LineWidth',1.4,'DisplayName',display_name);
else
    plot3(ax,points(:,1),points(:,2),points(:,3),'o', ...
        'Color',color,'MarkerFaceColor','w','MarkerSize',5, ...
        'LineStyle','none','DisplayName',display_name);
end
hold(ax,'on');

end

function addSimplifiedBeamLoadArrows(ax,station,points)

n_arrows = min(station.n,9);
idx = unique(round(linspace(1,station.n,n_arrows)));
x_len = 0.08*max(finiteSpan(points(:,1)),1);
section_len = max([0.5*finiteMaxAbs(station.chord), ...
    0.25*finiteMaxAbs(points(:,2)), 0.25*finiteMaxAbs(points(:,3)), 0.02]);

qx = scaledLoadVector(station.q_centrifugal(idx),x_len);
qy = scaledLoadVector(station.q_edgewise(idx),section_len);
qz = scaledLoadVector(station.q_thrust(idx),section_len);

if any(isfinite(qx) & abs(qx) > 0)
    quiver3(ax,points(idx,1),points(idx,2),points(idx,3), ...
        qx,zeros(size(qx)),zeros(size(qx)),0,'Color',[0.20 0.20 0.20], ...
        'LineWidth',1.0,'DisplayName','q_x centrifugal');
end
if any(isfinite(qy) & abs(qy) > 0)
    quiver3(ax,points(idx,1),points(idx,2),points(idx,3), ...
        zeros(size(qy)),qy,zeros(size(qy)),0,'Color',[0.85 0.33 0.10], ...
        'LineWidth',1.0,'DisplayName','q_y edgewise');
end
if any(isfinite(qz) & abs(qz) > 0)
    quiver3(ax,points(idx,1),points(idx,2),points(idx,3), ...
        zeros(size(qz)),zeros(size(qz)),qz,0,'Color',[0.00 0.45 0.74], ...
        'LineWidth',1.0,'DisplayName','q_z thrust');
end

end

function values = scaledLoadVector(load_values,arrow_length)

load_values = load_values(:);
denom = finiteMaxAbs(load_values);
if denom <= 0
    values = zeros(size(load_values));
else
    values = arrow_length*load_values/denom;
end

end

function value = finiteMaxAbs(values)

values = values(:);
values = values(isfinite(values));
if isempty(values)
    value = 0;
else
    value = max(abs(values));
end

end

function value = finiteSpan(values)

values = values(:);
values = values(isfinite(values));
if numel(values) < 2
    value = 0;
else
    value = max(values) - min(values);
end

end

function [summary_lines,summary_table] = simplifiedBeamSummary(station,geometry_mode,meta,load_source_text)

num_nodes = station.n;
num_elements = max(station.n - 1,0);
root_idx = 1;
tip_idx = station.n;
max_displacement = max(station.displacement_magnitude,[],'omitnan');

summary_lines = { ...
    'Beam model:', ...
    sprintf('nodes = %d',num_nodes), ...
    sprintf('elements = %d',num_elements), ...
    'DOF per node = 6', ...
    sprintf('root station = %d, r/R = %.6g, r = %.6g m', ...
        root_idx,station.mu(root_idx),station.r(root_idx)), ...
    sprintf('tip station = %d, r/R = %.6g, r = %.6g m', ...
        tip_idx,station.mu(tip_idx),station.r(tip_idx)), ...
    'root constrained = ux, uy, uz, rx, ry, rz', ...
    sprintf('load source = %s',char(string(load_source_text)))};

if strcmp(geometry_mode,'deformed') || strcmp(geometry_mode,'both')
    summary_lines{end+1} = sprintf('max displacement = %.6g m',max_displacement);
end
summary_lines = [summary_lines(:); { ...
    ''; ...
    'The structural solver uses one 6-DOF beam node per radial station. The 3D ribbon view is a visualization/stress-recovery representation, not a shell mesh.'}];

summary_table = table(num_nodes,num_elements,6,station.mu(root_idx), ...
    station.r(root_idx),station.mu(tip_idx),station.r(tip_idx), ...
    max_displacement,string(load_source_text),string(meta.label), ...
    'VariableNames',{'nodes','elements','dof_per_node','root_r_over_R', ...
    'root_r_m','tip_r_over_R','tip_r_m','max_displacement_m', ...
    'load_source','view'});

end

function [plot_values,meta] = plotSpanwiseLine(ax,station,result_type,x_axis_mode,allowables,stress_breakdown)

[x,xlabel_text] = spanAxis(station,x_axis_mode);
is_stress = contains(result_type,'stress') || contains(result_type,'von Mises');
if stress_breakdown && is_stress
    series = {station.sigma_axial_MPa, station.sigma_flap_MPa, ...
        station.sigma_edge_MPa, station.sigma_combined_MPa, ...
        station.sigma_vm_max_MPa};
    names = {'axial / centrifugal','flapwise bending','edgewise bending', ...
        'combined max normal','von Mises max'};
    plotMultipleSeries(ax,x,series,names,'Stress [MPa]');
    plot_values = station.sigma_combined_MPa;
    meta = resultMeta(station,'stress breakdown / combined max normal','MPa',x);
else
    [plot_values,meta] = getResultVector(station,result_type,allowables);
    meta.x = x;
    plot(ax,x,plot_values,'-o','LineWidth',1.5,'MarkerSize',4);
    ylabel(ax,axisLabel(meta.label,meta.units));
end
grid(ax,'on');
xlabel(ax,xlabel_text);
title(ax,'Spanwise Line Plot','Interpreter','none');

end

function [plot_values,meta] = plotInternalForceDiagram(ax,station,x_axis_mode)

[x,xlabel_text] = spanAxis(station,x_axis_mode);
yyaxis(ax,'left');
plot(ax,x,station.N,'-','LineWidth',1.4); hold(ax,'on');
plot(ax,x,station.Vy,'--','LineWidth',1.4);
plot(ax,x,station.Vz,':','LineWidth',1.6);
ylabel(ax,'Forces [N]');

yyaxis(ax,'right');
plot(ax,x,station.T,'-','LineWidth',1.4);
plot(ax,x,station.My,'--','LineWidth',1.4);
plot(ax,x,station.Mz,':','LineWidth',1.6);
ylabel(ax,'Moments [N*m]');

grid(ax,'on');
xlabel(ax,xlabel_text);
legend(ax,{'N axial','Vy edgewise shear','Vz flapwise shear', ...
    'T torsion','My flapwise bending','Mz edgewise bending'}, ...
    'Location','best');
title(ax,'Internal Beam Result Diagrams','Interpreter','none');
plot_values = station.sigma_combined_MPa;
meta = resultMeta(station,'internal beam resultants','mixed',x);

end

function [plot_values,meta] = plotSectionProperties(ax,station,result_type,x_axis_mode)

[x,xlabel_text] = spanAxis(station,x_axis_mode);
section_types = {'chord','twist distribution','area','Iy','Iz','J','mass per length'};
if any(strcmp(result_type,section_types))
    [plot_values,meta] = getResultVector(station,result_type,defaultAllowables());
    meta.x = x;
    plot(ax,x,plot_values,'-o','LineWidth',1.5,'MarkerSize',4);
    ylabel(ax,axisLabel(meta.label,meta.units));
else
    yyaxis(ax,'left');
    plot(ax,x,station.chord,'-','LineWidth',1.4); hold(ax,'on');
    plot(ax,x,station.twist_deg,'--','LineWidth',1.4);
    ylabel(ax,'Chord [m] / twist [deg]');
    yyaxis(ax,'right');
    plot(ax,x,station.A,'-','LineWidth',1.4);
    plot(ax,x,station.Iy,'--','LineWidth',1.4);
    plot(ax,x,station.Iz,':','LineWidth',1.6);
    plot(ax,x,station.J,'-.','LineWidth',1.4);
    ylabel(ax,'A [m^2], I/J [m^4]');
    legend(ax,{'chord','twist','A','Iy','Iz','J'},'Location','best');
    plot_values = station.chord;
    meta = resultMeta(station,'section properties','mixed',x);
end
grid(ax,'on');
xlabel(ax,xlabel_text);
title(ax,'Section Property Plots','Interpreter','none');

end

function [plot_values,meta] = plotLoadDistributions(ax,station,result_type,x_axis_mode)

[x,xlabel_text] = spanAxis(station,x_axis_mode);
load_types = {'thrust load','edgewise load','centrifugal load','torque distribution'};
if any(strcmp(result_type,load_types))
    [plot_values,meta] = getResultVector(station,result_type,defaultAllowables());
    meta.x = x;
    plot(ax,x,plot_values,'-o','LineWidth',1.5,'MarkerSize',4);
    ylabel(ax,axisLabel(meta.label,meta.units));
else
    yyaxis(ax,'left');
    plot(ax,x,station.q_thrust,'-','LineWidth',1.4); hold(ax,'on');
    plot(ax,x,station.q_edgewise,'--','LineWidth',1.4);
    plot(ax,x,station.q_centrifugal,':','LineWidth',1.6);
    ylabel(ax,'Distributed force [N/m]');
    yyaxis(ax,'right');
    plot(ax,x,station.m_torsion_dist,'-.','LineWidth',1.4);
    ylabel(ax,'Distributed torque [N*m/m]');
    legend(ax,{'thrust q_z','edgewise q_y','centrifugal q_x','torsion m_x'}, ...
        'Location','best');
    plot_values = station.q_thrust;
    meta = resultMeta(station,'load distributions','mixed',x);
end
grid(ax,'on');
xlabel(ax,xlabel_text);
title(ax,'Load Distribution Plots','Interpreter','none');

end

function [plot_values,meta] = plotComparison(ax,station,comparison,result_type,x_axis_mode)

[x,xlabel_text] = spanAxis(station,x_axis_mode);
if isempty(comparison)
    text(ax,0.5,0.5,'Import a hand/Abaqus CSV first.','HorizontalAlignment','center');
    plot_values = NaN(size(station.r));
    meta = resultMeta(station,'comparison unavailable','',x);
    return
end

cx = comparisonAxis(comparison,x_axis_mode);
if isempty(cx)
    warning('Comparison CSV needs r, r_m, r_over_R, or r/R.');
    plot_values = NaN(size(station.r));
    meta = resultMeta(station,'comparison unavailable','',x);
    return
end

if strcmp(result_type,'comparison percent error')
    abaqus = comparisonColumn(comparison,{'sigma_abaqus_MPa','abaqus_stress_MPa'});
    if isempty(abaqus)
        warning('Comparison percent error needs sigma_abaqus_MPa or abaqus_stress_MPa.');
        text(ax,0.5,0.5,'Comparison CSV has no Abaqus stress column.', ...
            'HorizontalAlignment','center');
        plot_values = NaN(size(station.r));
        meta = resultMeta(station,'comparison percent error','%',x);
        return
    end
    beam = comparisonColumn(comparison,{'sigma_beam_MPa','beam_stress_MPa'});
    if isempty(beam)
        beam = interp1(x,station.sigma_combined_MPa,cx,'linear','extrap');
    end
    error_percent = 100*(beam - abaqus)./abaqus;
    error_percent(abs(abaqus) < eps) = NaN;
    plot(ax,cx,error_percent,'-o','LineWidth',1.5,'DisplayName','Beam model');
    ylabel(ax,'Beam vs Abaqus error [%]');
    plot_values = error_percent;
    meta = resultMeta(station,'comparison percent error','%',cx);
else
    if contains(result_type,'displacement')
        beam = station.displacement_magnitude;
        abaqus = comparisonColumn(comparison,{'displacement_abaqus_m','abaqus_displacement_m'});
        beam_import = comparisonColumn(comparison,{'displacement_beam_m','beam_displacement_m'});
        plot(ax,x,beam,'--','Color',[0.35 0.35 0.35], ...
            'LineWidth',1.5,'DisplayName','Beam model'); hold(ax,'on');
        if ~isempty(abaqus)
            plot(ax,cx,abaqus,'^-','Color',[0 0 0], ...
                'MarkerFaceColor','w','LineWidth',1.4,'DisplayName','Abaqus');
        end
        if ~isempty(beam_import)
            plot(ax,cx,beam_import,'--','Color',[0.35 0.35 0.35], ...
                'LineWidth',1.2,'DisplayName','Beam model','HandleVisibility','off');
        end
        ylabel(ax,'Displacement [m]');
        legend(ax,'show','Location','best');
        plot_values = beam;
        meta = resultMeta(station,'beam displacement comparison','m',x);
    else
        hand = comparisonColumn(comparison,{'sigma_hand_MPa','hand_stress_MPa'});
        abaqus = comparisonColumn(comparison,{'sigma_abaqus_MPa','abaqus_stress_MPa'});
        beam_import = comparisonColumn(comparison,{'sigma_beam_MPa','beam_stress_MPa'});
        plot(ax,x,station.sigma_combined_MPa,'--','Color',[0.35 0.35 0.35], ...
            'LineWidth',1.5,'DisplayName','Beam model'); hold(ax,'on');
        if ~isempty(hand)
            plot(ax,cx,hand,'s','LineWidth',1.4,'MarkerFaceColor','w', ...
                'DisplayName','Hand calculation');
        end
        if ~isempty(abaqus)
            plot(ax,cx,abaqus,'^-','Color',[0 0 0], ...
                'MarkerFaceColor','w','LineWidth',1.4,'DisplayName','Abaqus');
        end
        if ~isempty(beam_import)
            plot(ax,cx,beam_import,'--','Color',[0.35 0.35 0.35], ...
                'LineWidth',1.2,'DisplayName','Beam model','HandleVisibility','off');
        end
        ylabel(ax,'Stress [MPa]');
        legend(ax,'show','Location','best');
        plot_values = station.sigma_combined_MPa;
        meta = resultMeta(station,'beam stress comparison','MPa',x);
    end
end
xlabel(ax,xlabel_text);
grid(ax,'on');
title(ax,'Beam vs Hand/Abaqus Comparison','Interpreter','none');

end

function plotMultipleSeries(ax,x,series,names,ylabel_text)

for i = 1:numel(series)
    plot(ax,x,series{i},'LineWidth',1.4); hold(ax,'on');
end
legend(ax,names,'Location','best');
ylabel(ax,ylabel_text);

end

function [x,label] = spanAxis(station,x_axis_mode)

[x,label] = get_x_axis_data(station,x_axis_mode);

end

function label = axisLabel(name,units)

if isempty(units)
    label = name;
elseif strcmpi(units,'unitless')
    label = sprintf('%s [-]',name);
else
    label = sprintf('%s [%s]',name,units);
end

end

function [summary_lines,summary_table] = resultSummary(station,values,meta)

if isfield(meta,'summary_kind') && strcmp(meta.summary_kind,'mesh')
    [summary_lines,summary_table] = meshSummaryForStation(station);
    return
end

values = values(:);
x = meta.x(:);
valid = isfinite(values) & isfinite(x);
if ~any(valid)
    summary_lines = {'No finite plotted values available.'};
    summary_table = table();
    return
end

vv = values(valid);
xx = x(valid);
[max_value,imax] = max(vv);
[min_value,imin] = min(vv);
[max_abs_value,iabs] = max(abs(vv));

idx_valid = find(valid);
station_max = idx_valid(imax);
station_abs = idx_valid(iabs);
unit_text = meta.units;

summary_lines = { ...
    sprintf('Max %s = %.6g %s',meta.label,max_value,unit_text), ...
    sprintf('  at r/R = %.4g, r = %.6g m, station %d', ...
    station.mu(station_max),station.r(station_max),station_max), ...
    sprintf('Min %s = %.6g %s',meta.label,min_value,unit_text), ...
    sprintf('  at x = %.6g',xx(imin)), ...
    sprintf('Max abs %s = %.6g %s',meta.label,max_abs_value,unit_text), ...
    sprintf('  at r/R = %.4g, r = %.6g m, station %d', ...
    station.mu(station_abs),station.r(station_abs),station_abs)};

if contains(lower(meta.label),'von mises') && isfield(station,'critical_section_point')
    summary_lines{end+1} = sprintf('Critical section point at max: %s', ...
        char(string(station.critical_section_point{station_max})));
elseif contains(lower(meta.label),'combined stress')
    summary_lines{end+1} = 'Combined stress is max(abs(sigma_x)) over critical section points.';
end

summary_table = table(string(meta.label),string(unit_text),max_value,min_value, ...
    max_abs_value,station.mu(station_max),station.r(station_max),station_max, ...
    'VariableNames',{'result','units','max_value','min_value','max_abs_value', ...
    'r_over_R_at_max','r_m_at_max','station_at_max'});

end

function lines = stationProbeSummary(station,probe_value,allowables)

selection = selectProbeStation(station,probe_value);
idx = selection.index;
elem_idx = selection.element_index;

sf = safetyFactorVector(station,allowables);
lines = { ...
    sprintf('Requested probe input: %s',selection.requested), ...
    sprintf('Input interpreted as: %s',selection.mode), ...
    sprintf('Selected station index: %d',idx), ...
    sprintf('Element index: %d',elem_idx), ...
    sprintf('Actual r/R: %.6g',selection.r_over_R), ...
    sprintf('Actual radial coordinate: %.6g m',selection.r_m), ...
    sprintf('chord = %.6g m',station.chord(idx)), ...
    sprintf('twist = %.6g deg',station.twist_deg(idx)), ...
    sprintf('A = %.6g m^2',station.A(idx)), ...
    sprintf('Iy = %.6g m^4',station.Iy(idx)), ...
    sprintf('Iz = %.6g m^4',station.Iz(idx)), ...
    sprintf('J = %.6g m^4',station.J(idx)), ...
    sprintf('N = %.6g N',station.N(idx)), ...
    sprintf('Vy = %.6g N',station.Vy(idx)), ...
    sprintf('Vz = %.6g N',station.Vz(idx)), ...
    sprintf('T = %.6g N*m',station.T(idx)), ...
    sprintf('My = %.6g N*m',station.My(idx)), ...
    sprintf('Mz = %.6g N*m',station.Mz(idx)), ...
    sprintf('sigma axial = %.6g MPa',station.sigma_axial_MPa(idx)), ...
    sprintf('sigma flap = %.6g MPa',station.sigma_flap_MPa(idx)), ...
    sprintf('sigma edge = %.6g MPa',station.sigma_edge_MPa(idx)), ...
    sprintf('tau torsion scalar = %.6g MPa',station.tau_torsion_MPa(idx)), ...
    sprintf('combined stress max normal envelope = %.6g MPa',station.sigma_combined_MPa(idx))};
lines = updateStationProbeWithStressEnvelope(lines,station,idx);
lines = [lines; { ...
    sprintf('ux, uy, uz = %.4g, %.4g, %.4g m',station.ux(idx),station.uy(idx),station.uz(idx)); ...
    sprintf('rx, ry, rz = %.4g, %.4g, %.4g rad',station.rx(idx),station.ry(idx),station.rz(idx)); ...
    sprintf('safety factor = %.6g',sf(idx))}];

end

function color_bands = validateColorBands(field)

color_bands = round(field.Value);
if ~isfinite(color_bands) || color_bands < 2
    color_bands = 13;
end
field.Value = color_bands;

end

function resetAxes(ax,fig)

cla(ax,'reset');
delete(findall(fig,'Type','ColorBar'));
ax.DataAspectRatioMode = 'auto';
ax.PlotBoxAspectRatioMode = 'auto';
ax.CameraViewAngleMode = 'auto';
ax.XLimMode = 'auto';
ax.YLimMode = 'auto';
ax.ZLimMode = 'auto';
ax.CLimMode = 'auto';
ax.Box = 'on';
xlabel(ax,'');
ylabel(ax,'');
zlabel(ax,'');
title(ax,'');
applyAxesPlotStyle(ax);

end

function applyAxesPlotStyle(ax)

try
    set(ax,'FontName','Times New Roman','FontSize',10, ...
        'XColor','k','YColor','k','ZColor','k');
catch
end

styleTextObject(ax.Title);
styleTextObject(ax.XLabel);
styleTextObject(ax.YLabel);
styleTextObject(ax.ZLabel);

fig = ancestor(ax,'figure');
try
    cbs = findall(fig,'Type','ColorBar');
    set(cbs,'FontName','Times New Roman','FontSize',10,'Color','k');
    for i = 1:numel(cbs)
        styleTextObject(cbs(i).Label);
    end
catch
end
try
    legends = findall(fig,'Type','Legend');
    set(legends,'FontName','Times New Roman','FontSize',10,'TextColor','k');
catch
end

end

function styleTextObject(obj)

try
    obj.FontName = 'Times New Roman';
    obj.FontSize = 11;
    obj.FontWeight = 'normal';
    obj.Color = 'k';
catch
end

end

function lim = finiteLimits(values)

values = values(isfinite(values));
if isempty(values)
    lim = [0 1];
    return
end
vmin = min(values);
vmax = max(values);
if vmin == vmax
    pad = max(abs(vmin),1)*0.05;
    lim = [vmin - pad, vmax + pad];
else
    lim = [vmin vmax];
end

end

function lim = paddedRange(values,pad_fraction)

values = values(isfinite(values));
if isempty(values)
    lim = [0 1];
    return
end
vmin = min(values);
vmax = max(values);
span = vmax - vmin;
if span <= eps(max(abs([vmin vmax 1])))
    span = max(abs(vmin),1);
end
lim = [vmin - pad_fraction*span, vmax + pad_fraction*span];

end

function [x] = comparisonAxis(T,x_axis_mode)

mode = lower(strtrim(string(x_axis_mode)));
switch mode
    case {"r [m]","r (m)","r_m","r m","radius","radius_m"}
        x = comparisonColumn(T,{'r','r_m','radius_m'});
    case {"station","station index","station_index"}
        x = comparisonColumn(T,{'station','station_index','node'});
        if isempty(x)
            x = (1:height(T))';
        end
    otherwise
        x = comparisonColumn(T,{'r_over_R','r/R','mu'});
end

end

function values = comparisonColumn(T,candidates)

values = [];
names = T.Properties.VariableNames;
norm_names = normalizeNames(names);
for i = 1:numel(candidates)
    target = normalizeNames(candidates{i});
    idx = find(strcmp(norm_names,target),1,'first');
    if ~isempty(idx)
        tmp = T.(names{idx});
        values = double(tmp(:));
        return
    end
end

end

function source_text = resultLoadSourceText(T,results_csv)

source_text = '';
names = T.Properties.VariableNames;
norm_names = normalizeNames(names);
idx = find(strcmp(norm_names,'loadsource'),1,'first');
if ~isempty(idx)
    raw = T.(names{idx});
    if iscell(raw)
        source_text = char(string(raw{1}));
    elseif isstring(raw)
        source_text = char(raw(1));
    elseif ischar(raw)
        source_text = raw;
    else
        source_text = char(string(raw(1)));
    end
    source_text = strtrim(source_text);
    if ~isempty(source_text)
        return
    end
end

path_text = lower(char(string(results_csv)));
if contains(path_text,'aero_solver_csv')
    source_text = 'aero_solver_csv';
elseif contains(path_text,'legacy_uniform')
    source_text = 'legacy_uniform';
end

end

function modal = loadModalData(results_csv,station)

paths = modalCompanionPaths(results_csv);
modal = struct();
modal.available = false;
modal.frequency_csv = paths.frequency_csv;
modal.mode_shapes_csv = paths.mode_shapes_csv;
modal.campbell_csv = paths.campbell_csv;
modal.frequency_table = table();
modal.mode_shape_table = table();
modal.frequencies_hz = [];
modal.omega_rad_s = [];
modal.modeType = {};
modal.modeShapes = [];

if ~isfile(paths.frequency_csv) || ~isfile(paths.mode_shapes_csv)
    return
end

freq_table = readtable(paths.frequency_csv,'VariableNamingRule','preserve');
shape_table = readtable(paths.mode_shapes_csv,'VariableNamingRule','preserve');
frequencies = modalTableColumn(freq_table,{'frequency_hz','frequencies_hz'});
omega = modalTableColumn(freq_table,{'omega_rad_s'});
if isempty(frequencies)
    return
end
if isempty(omega)
    omega = 2*pi*frequencies;
end

mode_numbers = modalTableColumn(freq_table,{'mode_number','mode'});
if isempty(mode_numbers)
    mode_numbers = (1:numel(frequencies))';
end
num_modes = numel(frequencies);
mode_shapes = zeros(6*station.n,num_modes);

shape_mode = modalTableColumn(shape_table,{'mode_number','mode'});
shape_station = modalTableColumn(shape_table,{'station','station_index','node'});
ux = modalTableColumn(shape_table,{'ux'});
uy = modalTableColumn(shape_table,{'uy'});
uz = modalTableColumn(shape_table,{'uz'});
rx = modalTableColumn(shape_table,{'rx'});
ry = modalTableColumn(shape_table,{'ry'});
rz = modalTableColumn(shape_table,{'rz'});

for m = 1:num_modes
    mode_id = mode_numbers(m);
    rows = find(shape_mode == mode_id);
    if isempty(rows)
        continue
    end
    for k = 1:numel(rows)
        row = rows(k);
        idx = shape_station(row);
        if ~isfinite(idx)
            continue
        end
        idx = max(1,min(station.n,round(idx)));
        dofs = (idx-1)*6 + (1:6);
        mode_shapes(dofs,m) = [ux(row); uy(row); uz(row); rx(row); ry(row); rz(row)];
    end
end

mode_type = modalTextColumn(freq_table,{'mode_type','modetype'});
if isempty(mode_type)
    mode_type = repmat({'mode'},num_modes,1);
end

modal.available = true;
modal.frequency_table = freq_table;
modal.mode_shape_table = shape_table;
modal.frequencies_hz = frequencies(:);
modal.omega_rad_s = omega(:);
modal.modeType = mode_type(:);
modal.modeShapes = mode_shapes;
modal.mode_numbers = mode_numbers(:);

end

function paths = modalCompanionPaths(results_csv)

[folder,name] = fileparts(results_csv);
prefix = regexprep(name,'_results$','');
paths = struct();
paths.frequency_csv = fullfile(folder,[prefix '_modal_frequencies.csv']);
paths.mode_shapes_csv = fullfile(folder,[prefix '_mode_shapes.csv']);
paths.campbell_csv = fullfile(folder,[prefix '_campbell.csv']);
paths.campbell_root_csv = fullfile(findResultsRoot(folder),'campbell_diagram.csv');

end

function results_root = findResultsRoot(start_dir)

results_root = start_dir;
current = start_dir;
while true
    [parent,name] = fileparts(current);
    [~,parent_name] = fileparts(parent);
    if strcmpi(name,'fea') && strcmpi(parent_name,'results')
        results_root = current;
        return
    end
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

function values = modalTableColumn(T,candidates)

values = [];
names = T.Properties.VariableNames;
norm_names = normalizeNames(names);
for i = 1:numel(candidates)
    idx = find(strcmp(norm_names,normalizeNames(candidates{i})),1,'first');
    if ~isempty(idx)
        values = double(T.(names{idx})(:));
        return
    end
end

end

function values = modalTextColumn(T,candidates)

values = {};
names = T.Properties.VariableNames;
norm_names = normalizeNames(names);
for i = 1:numel(candidates)
    idx = find(strcmp(norm_names,normalizeNames(candidates{i})),1,'first');
    if ~isempty(idx)
        values = cellstr(string(T.(names{idx})(:)));
        return
    end
end

end

function mode_id = selectedModeNumber(modal,value)

if ~modal.available
    mode_id = 1;
    return
end
mode_id = round(value);
if ~isfinite(mode_id)
    mode_id = 1;
end
mode_id = max(1,min(numel(modal.frequencies_hz),mode_id));

end

function plotNaturalFrequencyTable(ax,modal)

cla(ax,'reset');
axis(ax,'off');
if ~modal.available
    text(ax,0.5,0.5,'No modal frequency CSV found.','HorizontalAlignment','center');
    return
end

lines = modalSummaryLines(modal);
text(ax,0.02,0.96,strjoin(lines,newline),'Units','normalized', ...
    'VerticalAlignment','top','FontName','Times New Roman','FontSize',11,'Interpreter','none');
title(ax,'Natural Frequency Table','Interpreter','none');

end

function lines = modalSummaryLines(modal)

if ~modal.available
    lines = {'No modal data available.'};
    return
end

lines = {'mode    freq Hz    omega rad/s    type'};
for i = 1:numel(modal.frequencies_hz)
    lines{end+1} = sprintf('%4d  %9.4f  %12.4f    %s',i, ...
        modal.frequencies_hz(i),modal.omega_rad_s(i),modal.modeType{i}); %#ok<AGROW>
end
lines{end+1} = sprintf('Frequencies CSV: %s',modal.frequency_csv);

end

function [plot_values,meta] = plotModalShapeInViewer(ax,station,modal,mode_value,scale,view_mode,x_axis_mode)

resetAxes(ax,ancestor(ax,'figure'));
if ~modal.available
    text(ax,0.5,0.5,'No mode shape CSV found.','HorizontalAlignment','center');
    plot_values = NaN(station.n,1);
    meta = resultMeta(station,'mode shape unavailable','',station.mu);
    return
end

mode_id = selectedModeNumber(modal,mode_value);
if ~isfinite(scale) || scale == 0
    scale = 0.05;
end
U = reshape(modal.modeShapes(:,mode_id),6,[])';
[v0,vd,faces] = modalRibbonVertices(station,U,scale,view_mode,x_axis_mode);
[span_x,x_label] = get_x_axis_data(station,x_axis_mode);

patch(ax,'Faces',faces,'Vertices',v0,'FaceColor',[0.84 0.84 0.84], ...
    'FaceAlpha',0.35,'EdgeColor',[0.62 0.62 0.62],'LineStyle','--');
hold(ax,'on');
disp_mag = sqrt(sum(U(:,1:3).^2,2));
vertex_values = stationToVertexValues(disp_mag);
patch(ax,'Faces',faces,'Vertices',vd,'FaceVertexCData',vertex_values, ...
    'FaceColor','interp','EdgeColor',[0.15 0.15 0.15],'LineWidth',0.8);
grid(ax,'on');
axis(ax,'equal');
colormap(ax,parula(128));
cb = colorbar(ax);
ylabel(cb,'relative modal displacement');
title(ax,sprintf('Mode %d: %s, f = %.4g Hz',mode_id,modal.modeType{mode_id}, ...
    modal.frequencies_hz(mode_id)),'Interpreter','none');

if strcmp(view_mode,'span-flap')
    xlabel(ax,x_label);
    ylabel(ax,'Flapwise modal displacement [-]');
elseif strcmp(view_mode,'span-edge')
    xlabel(ax,x_label);
    ylabel(ax,'Edgewise modal displacement [-]');
else
    xlabel(ax,x_label);
    ylabel(ax,'Chordwise/edgewise coordinate [m]');
    zlabel(ax,'Flapwise/thickness coordinate [m]');
    view(ax,35,22);
end

plot_values = disp_mag;
meta = resultMeta(station,sprintf('mode %d displacement magnitude',mode_id), ...
    'relative',span_x);

end

function [vertices0,verticesd,faces] = modalRibbonVertices(station,U,scale,view_mode,x_axis_mode)

n = station.n;
faces = ribbonFaces(n);
[span_x,~] = get_x_axis_data(station,x_axis_mode);
ux_axis = modalSpanDisplacementForAxis(station,U(:,1),x_axis_mode);
if strcmp(view_mode,'span-flap') || strcmp(view_mode,'span-edge')
    vertices0 = zeros(2*n,2);
    verticesd = zeros(2*n,2);
    half_width = 0.015*max(station.r - min(station.r))*ones(n,1);
    for i = 1:n
        ids = [2*i - 1, 2*i];
        if strcmp(view_mode,'span-edge')
            uv = [ux_axis(i), U(i,2)];
        else
            uv = [ux_axis(i), U(i,3)];
        end
        vertices0(ids,:) = [span_x(i), -half_width(i); span_x(i), half_width(i)];
        verticesd(ids,:) = vertices0(ids,:) + scale*[uv; uv];
    end
    return
end

vertices0 = zeros(2*n,3);
verticesd = zeros(2*n,3);
for i = 1:n
    center0 = [span_x(i), 0, 0];
    beta = station.twist_deg(i)*pi/180;
    chord_axis = [0, cos(beta), sin(beta)];
    half_chord = 0.5*station.chord(i);
    offsets = [-half_chord*chord_axis; half_chord*chord_axis];
    centerd = center0 + scale*[ux_axis(i), U(i,2), U(i,3)];
    theta = scale*U(i,4:6);
    for j = 1:2
        id = 2*i - 2 + j;
        vertices0(id,:) = center0 + offsets(j,:);
        verticesd(id,:) = centerd + offsets(j,:) + cross(theta,offsets(j,:));
    end
end

end

function ux_axis = modalSpanDisplacementForAxis(station,ux,x_axis_mode)
%MODALSPANDISPLACEMENTFORAXIS Scale modal x displacement to axis units.

mode = lower(strtrim(string(x_axis_mode)));
switch mode
    case {"r/r","rr","r_over_r","r over r","mu"}
        radius = max(station.r,[],'omitnan');
        ux_axis = ux./max(radius,eps);
    case {"station","station index","station_index"}
        dr = diff(station.r(:));
        dr = dr(isfinite(dr) & abs(dr) > eps);
        if isempty(dr)
            ux_axis = zeros(size(ux));
        else
            ux_axis = ux./median(abs(dr));
        end
    otherwise
        ux_axis = ux;
end

end

function T = selectedModeShapeTable(modal,station,mode_id)

U = reshape(modal.modeShapes(:,mode_id),6,[])';
mode_number = mode_id*ones(station.n,1);
station_index = (1:station.n)';
T = table(mode_number,station_index,station.mu(:),station.r(:), ...
    U(:,1),U(:,2),U(:,3),U(:,4),U(:,5),U(:,6), ...
    'VariableNames',{'mode_number','station','r_over_R','r_m', ...
    'ux','uy','uz','rx','ry','rz'});

end

function [plot_values,meta] = plotCampbellFromModalFile(ax,results_csv)

paths = modalCompanionPaths(results_csv);
campbell_csv = '';
if isfile(paths.campbell_csv)
    campbell_csv = paths.campbell_csv;
elseif isfield(paths,'campbell_root_csv') && isfile(paths.campbell_root_csv)
    campbell_csv = paths.campbell_root_csv;
end

if isempty(campbell_csv)
    cla(ax,'reset');
    text(ax,0.5,0.5,'No Campbell CSV found. Enable run_campbell_diagram_enabled in the driver.', ...
        'HorizontalAlignment','center');
    plot_values = NaN;
    meta = struct('label','Campbell data unavailable','units','Hz','x',NaN, ...
        'series_name','Campbell data unavailable');
    return
end

T = readtable(campbell_csv,'VariableNamingRule','preserve');
rpm = modalTableColumn(T,{'RPM','rpm'});
title_text = campbellTitleFromCsv(campbell_csv);
cla(ax,'reset');
hold(ax,'on');

mode_colors = [0.000 0.447 0.741
               0.850 0.325 0.098
               0.466 0.674 0.188
               0.494 0.184 0.556
               0.301 0.745 0.933];
plot_values = [];
mode_series = cell(1,5);
for mode = 1:5
    y = campbellModeColumn(T,mode);
    mode_series{mode} = y;
    if ~isempty(y)
        plot(ax,rpm,positiveLogValues(y),'-','LineWidth',1.7,'Color',mode_colors(mode,:), ...
            'DisplayName',sprintf('Mode %d',mode));
        plot_values = [plot_values; y(:)]; %#ok<AGROW>
    end
end

excitation_series = cell(1,5);
for harmonic = 1:4
    y = modalTableColumn(T,{sprintf('f_%dx_Hz',harmonic), ...
        sprintf('f_%drev_hz',harmonic),sprintf('f%drevhz',harmonic)});
    excitation_series{harmonic} = y;
    if ~isempty(y)
        plot(ax,rpm,positiveLogValues(y),'--','Color',[0.35 0.35 0.35], ...
            'LineWidth',1.0,'DisplayName',sprintf('%dx',harmonic));
        plot_values = [plot_values; y(:)]; %#ok<AGROW>
    end
end
fb = modalTableColumn(T,{'f_bpf_Hz','blade_pass_frequency_hz','bladepassfrequencyhz'});
excitation_series{5} = fb;
if ~isempty(fb)
    plot(ax,rpm,positiveLogValues(fb),'k--','LineWidth',2.0,'DisplayName','BPF');
    plot_values = [plot_values; fb(:)];
end

markers = campbellCloseApproachesFromSeries(rpm,mode_series,excitation_series,0.10);
if ~isempty(markers)
    scatter(ax,markers.rpm,markers.frequency_Hz,38,'o', ...
        'MarkerEdgeColor',[0.70 0.00 0.00], ...
        'MarkerFaceColor','w','LineWidth',1.0, ...
        'DisplayName','<10% separation');
end
grid(ax,'on');
try
    ax.XMinorGrid = 'on';
    ax.YMinorGrid = 'on';
    ax.GridAlpha = 0.25;
    ax.MinorGridAlpha = 0.12;
    ax.TickDir = 'in';
    ax.Box = 'on';
    ax.YScale = 'log';
catch
end
xlabel(ax,'RPM');
ylabel(ax,'Frequency [Hz]');
title(ax,title_text,'Interpreter','none');
legend(ax,'Location','best','Box','off');
summary_lines = campbellSummaryLines(T,campbell_csv,title_text);
meta = struct('label',title_text,'units','Hz','x',rpm, ...
    'series_name',title_text,'summary_lines',{summary_lines});

end

function values = positiveLogValues(values)

values(~isfinite(values) | values <= 0) = NaN;

end

function title_text = campbellTitleFromCsv(campbell_csv)

[~,name] = fileparts(campbell_csv);
name = regexprep(name,'_campbell$','');
name = regexprep(name,'_campbell_diagram$','');
name = regexprep(name,'^campbell_diagram$','');
label = labelFromFileStem(name);
if isempty(label)
    title_text = 'Campbell Diagram';
else
    title_text = sprintf('%s Campbell Diagram',label);
end

end

function label = labelFromFileStem(stem)

name = lower(regexprep(char(string(stem)),'_+',' '));
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

function y = campbellModeColumn(T,mode)

y = modalTableColumn(T,{sprintf('mode_%d_Hz',mode), ...
    sprintf('mode_%d_frequency_hz',mode),sprintf('mode%dfrequencyhz',mode)});

end

function markers = campbellCloseApproachesFromSeries(rpm,mode_series,excitation_series,tol)

rpm_mark = [];
freq_mark = [];
mode_id = [];
exc_id = [];
for i = 1:numel(mode_series)
    mode_freq = mode_series{i};
    if isempty(mode_freq)
        continue
    end
    for j = 1:numel(excitation_series)
        excitation = excitation_series{j};
        if isempty(excitation)
            continue
        end
        sep = abs(mode_freq - excitation)./max(abs(mode_freq),eps);
        valid = isfinite(sep);
        if ~any(valid)
            continue
        end
        valid_idx = find(valid);
        [min_sep,local_idx] = min(sep(valid));
        if min_sep > tol
            continue
        end
        idx = valid_idx(local_idx);
        rpm_cross = campbellCrossingRpm(rpm,mode_freq,excitation);
        if isfinite(rpm_cross)
            freq_cross = interp1(rpm,mode_freq,rpm_cross,'linear');
            rpm_mark(end+1,1) = rpm_cross; %#ok<AGROW>
            freq_mark(end+1,1) = freq_cross; %#ok<AGROW>
        else
            rpm_mark(end+1,1) = rpm(idx); %#ok<AGROW>
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

function rpm_cross = campbellCrossingRpm(rpm,mode_freq,excitation)

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

function lines = campbellSummaryLines(T,campbell_csv,title_text)

mode_1 = campbellModeColumn(T,1);
bpf = modalTableColumn(T,{'f_bpf_Hz','blade_pass_frequency_hz','bladepassfrequencyhz'});
rpm = modalTableColumn(T,{'RPM','rpm'});
lines = {title_text};
if isempty(mode_1) || isempty(bpf) || isempty(rpm)
    lines{end+1} = 'Mode 1/BPF summary unavailable.';
    lines{end+1} = sprintf('CSV: %s',campbell_csv);
    return
end

sep = abs(mode_1 - bpf)./max(abs(mode_1),eps);
valid = isfinite(sep) & isfinite(rpm);
if ~any(valid)
    lines{end+1} = 'Mode 1/BPF summary unavailable.';
    lines{end+1} = sprintf('CSV: %s',campbell_csv);
    return
end

valid_idx = find(valid);
[min_sep,local_idx] = min(sep(valid));
idx = valid_idx(local_idx);
closest_rpm = rpm(idx);
rpm_cross = campbellCrossingRpm(rpm,mode_1,bpf);
if isfinite(rpm_cross)
    min_sep = 0;
    closest_rpm = rpm_cross;
end

lines{end+1} = sprintf('Mode 1/BPF minimum separation: %.3g%%',100*min_sep);
lines{end+1} = sprintf('Closest approach: %.3f RPM',closest_rpm);
if min_sep < 0.10
    lines{end+1} = 'WARNING: Mode 1 is within 10% of BPF.';
else
    lines{end+1} = 'Mode 1 remains at least 10% separated from BPF.';
end
lines{end+1} = 'Mode frequencies are constant with RPM in this implementation.';
lines{end+1} = sprintf('CSV: %s',campbell_csv);

end

function out = defaultAllowables()

out = struct('tensile_MPa',250,'compressive_MPa',250, ...
    'shear_MPa',145,'max_display_sf',10);

end

function stem = safeFileStem(text_value)

stem = lower(char(string(text_value)));
stem = regexprep(stem,'[^a-z0-9]+','_');
stem = regexprep(stem,'(^_+|_+$)','');
if isempty(stem)
    stem = 'propeller_beam_plot';
end

end

function view_name = normalizeViewName(view_name)

view_name = lower(string(view_name));
switch view_name
    case {"3d isometric","3d","isometric","iso","3d isometric ribbon"}
        view_name = '3D isometric ribbon';
    case {"planform","span-chord","span_chord"}
        view_name = 'planform';
    case {"span-flap","span_flap","flap","span-flap projection"}
        view_name = 'span-flap projection';
    case {"span-edge","span_edge","edge","span-edge projection"}
        view_name = 'span-edge projection';
    case {"simplified beam model","1d beam centerline","1d beam","beam centerline","beam model","simplified beam"}
        view_name = 'simplified beam model';
    case {"natural frequency table","frequencies","modal table","frequency table"}
        view_name = 'natural frequency table';
    case {"mode shape 3d","modal 3d","mode 3d"}
        view_name = 'mode shape 3D';
    case {"mode shape span-flap","modal span-flap","mode span-flap"}
        view_name = 'mode shape span-flap';
    case {"mode shape span-edge","modal span-edge","mode span-edge"}
        view_name = 'mode shape span-edge';
    case {"campbell","campbell diagram"}
        view_name = 'Campbell diagram';
    otherwise
        view_name = char(view_name);
end

end

function results_csv = latest_results_csv()

this_dir = fileparts(mfilename('fullpath'));
results_root = fullfile(projectRootFromFeaDir(this_dir),'results','fea');
files = dir(fullfile(results_root,'**','*_results.csv'));
if isempty(files)
    error('No *_results.csv files found under %s. Run main_propeller_3d_beam_fea first.', ...
        results_root);
end
[~,idx] = max([files.datenum]);
results_csv = fullfile(files(idx).folder,files(idx).name);

end

function project_root = projectRootFromFeaDir(this_dir)

code_dir = fileparts(this_dir);
[project_root,code_folder] = fileparts(code_dir);
if ~strcmpi(code_folder,'Code')
    project_root = code_dir;
end

end
