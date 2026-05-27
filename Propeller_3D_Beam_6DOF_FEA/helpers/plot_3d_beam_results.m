function plot_files = plot_3d_beam_results(mesh,props,loads,solution,forces,stress,params,results_dir,output_name)
%PLOT_3D_BEAM_RESULTS Generate standard result plots for the beam model.

if ~exist(results_dir,'dir')
    mkdir(results_dir);
end
applyPlotStyle();

plot_files = {};
mu = mesh.mu(:);
r = mesh.radius_m(:);
U = solution.displacements;
case_label = '';
if isfield(params,'case_label') && ~isempty(params.case_label)
    case_label = char(string(params.case_label));
end

plot_files{end+1} = simple_plot(mu,mesh.chord_m,'r/R','chord (m)', ...
    case_title(case_label,'Chord Distribution'),results_dir,[output_name '_01_chord_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,mesh.twist_deg,'r/R','twist (deg)', ...
    case_title(case_label,'Twist Distribution'),results_dir,[output_name '_02_twist_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,loads.q_z_thrust_N_m,'r/R','q_z thrust (N/m)', ...
    case_title(case_label,'Thrust Load Distribution'),results_dir,[output_name '_03_thrust_load_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,forces.N_N,'r/R','N axial force (N)', ...
    case_title(case_label,'Centrifugal Axial Force'),results_dir,[output_name '_04_centrifugal_axial_force_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,forces.My_bending_Nm,'r/R','M_y flapwise bending (N-m)', ...
    case_title(case_label,'Flapwise Bending Moment'),results_dir,[output_name '_05_flapwise_bending_moment_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,forces.Mz_bending_Nm,'r/R','M_z edgewise bending (N-m)', ...
    case_title(case_label,'Edgewise Bending Moment'),results_dir,[output_name '_06_edgewise_bending_moment_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,forces.Mx_torsion_Nm,'r/R','M_x torsion (N-m)', ...
    case_title(case_label,'Torsional Moment'),results_dir,[output_name '_07_torsional_moment_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,stress.sigma_flap_Pa/1e6,'r/R','flapwise stress (MPa)', ...
    case_title(case_label,'Approximate Flapwise Beam Stress'),results_dir,[output_name '_08_flapwise_stress_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,stress.sigma_axial_Pa/1e6,'r/R','axial stress (MPa)', ...
    case_title(case_label,'Approximate Axial Beam Stress'),results_dir,[output_name '_09_axial_stress_vs_rR.png']);
plot_files{end+1} = simple_plot(mu,stress.sigma_combined_Pa/1e6,'r/R','max normal stress envelope (MPa)', ...
    case_title(case_label,'Approximate Max Normal Beam Stress Envelope'),results_dir,[output_name '_10_combined_stress_vs_rR.png']);

scale = params.deflection_scale_factor;
coords = mesh.nodes_global;
def = coords + scale*U(:,1:3);

fig = figure('Visible','off','Color','w');
plot3(coords(:,1),coords(:,2),coords(:,3),'k--','LineWidth',1.2); hold on;
plot3(def(:,1),def(:,2),def(:,3),'b-o','LineWidth',1.4,'MarkerSize',4);
grid on; axis equal;
xlabel('x span/radius (m)'); ylabel('y edgewise (m)'); zlabel('z flapwise (m)');
title(case_title(case_label,sprintf('Deflected Blade Shape, scale = %.3g',scale)));
legend('undeformed','deflected','Location','best');
plot_files{end+1} = save_figure(fig,results_dir,[output_name '_11_deflected_blade_shape_3d.png']);

fig = figure('Visible','off','Color','w');
plot(r,zeros(size(r)),'k--','LineWidth',1.2); hold on;
plot(r,scale*U(:,3),'b-o','LineWidth',1.4,'MarkerSize',4);
grid on; xlabel('radius r (m)'); ylabel('scaled flapwise deflection z (m)');
title(case_title(case_label,sprintf('Exaggerated Flapwise Deflection, scale = %.3g',scale)));
plot_files{end+1} = save_figure(fig,results_dir,[output_name '_12_exaggerated_flapwise_deflection.png']);

fig = figure('Visible','off','Color','w');
plot_twisted_ribbon(mesh,U,stress.sigma_combined_Pa/1e6,scale);
title(case_title(case_label,sprintf('Twisted Chord Ribbon, max normal stress envelope color, scale = %.3g',scale)));
plot_files{end+1} = save_figure(fig,results_dir,[output_name '_13_twisted_chord_ribbon_3d.png']);

end

function ttl = case_title(case_label,plot_title)

case_label = strtrim(char(string(case_label)));
if isempty(case_label)
    ttl = plot_title;
else
    ttl = sprintf('%s: %s',case_label,plot_title);
end

end

function path = simple_plot(x,y,xlab,ylab,ttl,results_dir,file_name)

fig = figure('Visible','off','Color','w');
plot(x,y,'-o','LineWidth',1.4,'MarkerSize',4);
grid on; xlabel(xlab); ylabel(ylab); title(ttl);
path = save_figure(fig,results_dir,file_name);

end

function path = save_figure(fig,results_dir,file_name)

if ~exist(results_dir,'dir')
    mkdir(results_dir);
end
[~,stem] = fileparts(file_name);
path = fullfile(results_dir,[stem '.png']);
svg_path = fullfile(results_dir,[stem '.svg']);
if exist('exportgraphics','file')
    exportgraphics(fig,svg_path,'ContentType','vector');
    exportgraphics(fig,path,'Resolution',300);
else
    try
        saveas(fig,svg_path);
    catch
    end
    print(fig,path,'-dpng','-r300');
end
close(fig);

end

function plot_twisted_ribbon(mesh,U,color_values,scale)

n = mesh.n_nodes;
center_0 = mesh.nodes_global;
center_d = center_0 + scale*U(:,1:3);

lead_0 = zeros(n,3);
trail_0 = zeros(n,3);
lead_d = zeros(n,3);
trail_d = zeros(n,3);

for i = 1:n
    beta = mesh.twist_deg(i)*pi/180;
    ey = [0 cos(beta) sin(beta)];
    half_chord = 0.5*mesh.chord_m(i);

    lead_0(i,:) = center_0(i,:) + half_chord*ey;
    trail_0(i,:) = center_0(i,:) - half_chord*ey;
    lead_d(i,:) = center_d(i,:) + half_chord*ey;
    trail_d(i,:) = center_d(i,:) - half_chord*ey;
end

X0 = [lead_0(:,1)'; trail_0(:,1)'];
Y0 = [lead_0(:,2)'; trail_0(:,2)'];
Z0 = [lead_0(:,3)'; trail_0(:,3)'];

Xd = [lead_d(:,1)'; trail_d(:,1)'];
Yd = [lead_d(:,2)'; trail_d(:,2)'];
Zd = [lead_d(:,3)'; trail_d(:,3)'];
C = [color_values(:)'; color_values(:)'];

surf(X0,Y0,Z0,0*C,'FaceColor',[0.86 0.86 0.86],'EdgeColor',[0.55 0.55 0.55], ...
    'FaceAlpha',0.45); hold on;
surf(Xd,Yd,Zd,C,'FaceColor','interp','EdgeColor',[0.15 0.15 0.15], ...
    'FaceAlpha',0.95);
plot3(center_0(:,1),center_0(:,2),center_0(:,3),'k--','LineWidth',1.0);
plot3(center_d(:,1),center_d(:,2),center_d(:,3),'k-','LineWidth',1.2);
grid on; axis equal; view(35,22);
xlabel('x span/radius (m)');
ylabel('y chordwise/edgewise (m)');
zlabel('z flapwise/thickness (m)');
cb = colorbar;
ylabel(cb,'max normal stress envelope (MPa)','FontSize',11,'Color','k');
legend('undeformed chord ribbon','deflected chord ribbon','undeformed beam axis', ...
    'deflected beam axis','Location','best','FontSize',10,'TextColor','k');

end
