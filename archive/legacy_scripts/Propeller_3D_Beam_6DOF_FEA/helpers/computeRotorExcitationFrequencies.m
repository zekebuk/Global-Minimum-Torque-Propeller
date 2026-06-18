function excitation = computeRotorExcitationFrequencies(RPM,num_blades,frequencies_hz,tolerance)
%COMPUTEROTOREXCITATIONFREQUENCIES Rotor 1/rev, 2/rev, and blade-pass checks.

if nargin < 2 || isempty(num_blades)
    num_blades = 2;
end
if nargin < 3
    frequencies_hz = [];
end
if nargin < 4 || isempty(tolerance)
    tolerance = 0.10;
end

f_rotor = RPM/60;
labels = {'1/rev','2/rev','blade passage'};
values = [f_rotor, 2*f_rotor, num_blades*f_rotor];

warnings = {};
for i = 1:numel(frequencies_hz)
    f = frequencies_hz(i);
    for j = 1:numel(values)
        target = values(j);
        if target > 0 && abs(f - target)/target <= tolerance
            warnings{end+1} = sprintf('Warning: Mode %d is within %.0f percent of %s excitation at %.0f RPM.', ...
                i,100*tolerance,labels{j},RPM); %#ok<AGROW>
        end
    end
end

excitation = struct();
excitation.RPM = RPM;
excitation.num_blades = num_blades;
excitation.f_rotor_hz = f_rotor;
excitation.f_1rev_hz = f_rotor;
excitation.f_2rev_hz = 2*f_rotor;
excitation.blade_pass_frequency_hz = num_blades*f_rotor;
excitation.labels = labels;
excitation.values_hz = values;
excitation.tolerance = tolerance;
excitation.warnings = warnings;

end
