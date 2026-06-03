function results_dir = prepareResultsDirectory(this_dir,folder_name,clean_existing)
%PREPARERESULTSDIRECTORY Create one stable, clean results folder per run.

if nargin < 3 || isempty(clean_existing)
    clean_existing = true;
end

code_dir = fileparts(this_dir);
[project_root,code_folder] = fileparts(code_dir);
if ~strcmpi(code_folder,'Code')
    project_root = code_dir;
end

results_root = fullfile(project_root,'results','fea');
if ~exist(results_root,'dir')
    mkdir(results_root);
end

parts = regexp(char(string(folder_name)),'[\\/]+','split');
parts = parts(~cellfun('isempty',parts));
for i = 1:numel(parts)
    parts{i} = regexprep(parts{i},'[^a-zA-Z0-9_\-]','_');
end
results_dir = fullfile(results_root,parts{:});

if exist(results_dir,'dir')
    if clean_existing
        clearDirectoryContents(results_dir);
    end
else
    mkdir(results_dir);
end

end

function clearDirectoryContents(target_dir)

items = dir(target_dir);
items = items(~ismember({items.name},{'.','..'}));

for i = 1:numel(items)
    path = fullfile(items(i).folder,items(i).name);
    if items(i).isdir
        rmdir(path,'s');
    else
        delete(path);
    end
end

end
