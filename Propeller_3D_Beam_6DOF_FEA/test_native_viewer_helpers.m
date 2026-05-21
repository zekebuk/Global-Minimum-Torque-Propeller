function tests = test_native_viewer_helpers
%TEST_NATIVE_VIEWER_HELPERS Validate shared plotting UI helpers.

tests = functiontests(localfunctions);

end

function setupOnce(~)

this_dir = fileparts(mfilename('fullpath'));
addpath(fullfile(this_dir,'helpers'),'-begin');

end

function testXAxisData(testCase)

station = sampleStation(5);

[x,label] = get_x_axis_data(station,'r/R');
verifyEqual(testCase,x,station.mu);
verifyEqual(testCase,label,'r/R [-]');

[x,label] = get_x_axis_data(station,'r [m]');
verifyEqual(testCase,x,station.r);
verifyEqual(testCase,label,'r [m]');

[x,label] = get_x_axis_data(station,'station');
verifyEqual(testCase,x,(1:station.n)');
verifyEqual(testCase,label,'Station index [-]');

end

function testProbeOneSelectsTip(testCase)

station = sampleStation(6);

selection = selectProbeStation(station,'1.0');
verifyEqual(testCase,selection.mode,'r/R');
verifyEqual(testCase,selection.index,station.n);
verifyEqual(testCase,selection.r_over_R,1.0,'AbsTol',1e-12);

selection = selectProbeStation(station,'1');
verifyEqual(testCase,selection.mode,'r/R');
verifyEqual(testCase,selection.index,station.n);
verifyEqual(testCase,selection.r_over_R,1.0,'AbsTol',1e-12);

end

function testProbeIntegerGreaterThanOneSelectsStation(testCase)

station = sampleStation(6);

selection = selectProbeStation(station,'5');
verifyEqual(testCase,selection.mode,'station index');
verifyEqual(testCase,selection.index,5);

end

function testMeshSummaryIsMetadata(testCase)

station = sampleStation(5);

[lines,summary_table] = meshSummaryForStation(station);
joined = strjoin(lines,newline);

verifyTrue(testCase,contains(joined,'Mesh metadata'));
verifyTrue(testCase,contains(joined,'Stations: 5'));
verifyFalse(testCase,contains(joined,'Max mesh'));
verifyFalse(testCase,contains(joined,'Min mesh'));
verifyEqual(testCase,summary_table.num_elements,4);

end

function station = sampleStation(n)

station = struct();
station.n = n;
station.mu = linspace(0.2,1.0,n)';
station.r = 0.2794*station.mu;
station.chord = linspace(0.054,0.012,n)';

end
