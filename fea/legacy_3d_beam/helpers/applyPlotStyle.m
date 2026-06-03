function applyPlotStyle()
%APPLYPLOTSTYLE Set shared thesis plot typography defaults.

safeSetDefault('defaultAxesFontName','Times New Roman');
safeSetDefault('defaultTextFontName','Times New Roman');
safeSetDefault('defaultLegendFontName','Times New Roman');
safeSetDefault('defaultColorbarFontName','Times New Roman');
safeSetDefault('defaultAxesFontSize',11);
safeSetDefault('defaultTextFontSize',11);
safeSetDefault('defaultLegendFontSize',10);
safeSetDefault('defaultColorbarFontSize',11);
safeSetDefault('defaultAxesTitleFontSizeMultiplier',1);
safeSetDefault('defaultAxesLabelFontSizeMultiplier',1);
safeSetDefault('defaultAxesTitleFontWeight','normal');
safeSetDefault('defaultTextFontWeight','normal');
safeSetDefault('defaultLineLineWidth',1.5);
safeSetDefault('defaultLineMarkerSize',4);
safeSetDefault('defaultAxesLineWidth',0.8);
safeSetDefault('defaultAxesGridLineStyle','-');
safeSetDefault('defaultAxesGridAlpha',0.35);
safeSetDefault('defaultAxesMinorGridAlpha',0.18);
safeSetDefault('defaultAxesTickDir','in');
safeSetDefault('defaultAxesBox','on');
safeSetDefault('defaultFigureColor','w');
safeSetDefault('defaultAxesXColor','k');
safeSetDefault('defaultAxesYColor','k');
safeSetDefault('defaultAxesZColor','k');
safeSetDefault('defaultTextColor','k');

end

function safeSetDefault(name,value)

try
    set(groot,name,value);
catch
end

end
