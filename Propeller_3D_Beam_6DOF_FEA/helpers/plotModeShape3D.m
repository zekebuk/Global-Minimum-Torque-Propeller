function fig = plotModeShape3D(mesh,modalResults,modeNumber,scale,viewMode,results_dir,output_name)
%PLOTMODESHAPE3D Plot a modal deformation using the blade ribbon geometry.

if nargin < 3 || isempty(modeNumber)
    modeNumber = 1;
end
if nargin < 4 || isempty(scale)
    scale = 0.05;
end
if nargin < 5 || isempty(viewMode)
    viewMode = '3D';
end
if nargin < 6
    results_dir = '';
end
if nargin < 7
    output_name = '';
end
applyPlotStyle();

modeNumber = max(1,min(numel(modalResults.frequencies_hz),round(modeNumber)));
U = reshape(modalResults.modeShapes(:,modeNumber),6,[])';

fig = figure('Visible','off','Color','w');
ax = axes(fig);
plotModeShapeOnAxes(ax,mesh,U,scale,viewMode);
ttl = sprintf('Mode %d: %s, f = %.4g Hz',modeNumber, ...
    modalResults.modeType{modeNumber},modalResults.frequencies_hz(modeNumber));
if isfield(modalResults,'case_label') && ~isempty(modalResults.case_label)
    ttl = sprintf('%s: %s',strtrim(char(string(modalResults.case_label))),ttl);
end
title(ax,ttl,'Interpreter','none');

if ~isempty(results_dir) && ~isempty(output_name)
    if ~exist(results_dir,'dir')
        mkdir(results_dir);
    end
    stem = sprintf('%s_mode_%02d_shape_%s',output_name,modeNumber,lower(regexprep(viewMode,'[^a-zA-Z0-9]','_')));
    saveModeFigure(fig,results_dir,stem);
end

end

function plotModeShapeOnAxes(ax,mesh,U,scale,viewMode)

[v0,vd,faces] = modalRibbonVertices(mesh,U,scale,viewMode);
patch(ax,'Faces',faces,'Vertices',v0,'FaceColor',[0.82 0.82 0.82], ...
    'FaceAlpha',0.35,'EdgeColor',[0.62 0.62 0.62],'LineStyle','--');
hold(ax,'on');
color_values = sqrt(sum(U(:,1:3).^2,2));
vertex_values = zeros(2*numel(color_values),1);
vertex_values(1:2:end) = color_values;
vertex_values(2:2:end) = color_values;
patch(ax,'Faces',faces,'Vertices',vd,'FaceVertexCData',vertex_values, ...
    'FaceColor','interp','EdgeColor',[0.15 0.15 0.15],'LineWidth',0.8);
grid(ax,'on');
axis(ax,'equal');
if strcmpi(viewMode,'span-flap')
    xlabel(ax,'radius r (m)');
    ylabel(ax,'flapwise modal displacement');
elseif strcmpi(viewMode,'span-edge')
    xlabel(ax,'radius r (m)');
    ylabel(ax,'edgewise modal displacement');
else
    xlabel(ax,'x span/radius (m)');
    ylabel(ax,'y chordwise/edgewise (m)');
    zlabel(ax,'z flapwise/thickness (m)');
    view(ax,35,22);
end
cb = colorbar(ax);
ylabel(cb,'relative modal displacement','FontSize',11,'Color','k');

end

function saveModeFigure(fig,results_dir,stem)

if exist('exportgraphics','file')
    exportgraphics(fig,fullfile(results_dir,[stem '.svg']),'ContentType','vector');
    exportgraphics(fig,fullfile(results_dir,[stem '.png']),'Resolution',300);
else
    try
        saveas(fig,fullfile(results_dir,[stem '.svg']));
    catch
    end
    print(fig,fullfile(results_dir,[stem '.png']),'-dpng','-r300');
end

end

function [v0,vd,faces] = modalRibbonVertices(mesh,U,scale,viewMode)

n = mesh.n_nodes;
faces = zeros(n - 1,4);
for e = 1:n - 1
    faces(e,:) = [2*e - 1, 2*(e + 1) - 1, 2*(e + 1), 2*e];
end

if strcmpi(viewMode,'span-flap') || strcmpi(viewMode,'span-edge')
    v0 = zeros(2*n,2);
    vd = zeros(2*n,2);
    half_width = 0.015*max(mesh.radius_m - min(mesh.radius_m));
    for i = 1:n
        ids = [2*i - 1, 2*i];
        if strcmpi(viewMode,'span-edge')
            modal_uv = [U(i,1), U(i,2)];
        else
            modal_uv = [U(i,1), U(i,3)];
        end
        v0(ids,:) = [mesh.radius_m(i), -half_width; mesh.radius_m(i), half_width];
        vd(ids,:) = v0(ids,:) + scale*[modal_uv; modal_uv];
    end
    return
end

v0 = zeros(2*n,3);
vd = zeros(2*n,3);
for i = 1:n
    center0 = [mesh.radius_m(i), 0, 0];
    beta = mesh.twist_deg(i)*pi/180;
    chord_axis = [0, cos(beta), sin(beta)];
    half_chord = 0.5*mesh.chord_m(i);
    offsets = [-half_chord*chord_axis; half_chord*chord_axis];
    centerd = center0 + scale*U(i,1:3);
    theta = scale*U(i,4:6);
    for j = 1:2
        id = 2*i - 2 + j;
        v0(id,:) = center0 + offsets(j,:);
        vd(id,:) = centerd + offsets(j,:) + cross(theta,offsets(j,:));
    end
end

end
