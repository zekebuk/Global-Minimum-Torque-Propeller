# GUI Launchers

This folder contains interactive convenience entry points. They are useful for reviewing solver setup and FEA visualizations, but they are not part of the locked thesis quick-start workflow.

## Streamlit Aero UI

```powershell
python scripts/gui/start_streamlit_aero_ui.py
```

This launches the archived Streamlit aero-solver interface through `scripts/gui/streamlit_aero_ui.py`, with the legacy solver modules added to the Python path.

## MATLAB FEA UI

```powershell
python scripts/gui/start_matlab_fea_ui.py
```

By default this opens the native MATLAB propeller beam viewer. To request the ME504 plotting app adapter instead:

```powershell
python scripts/gui/start_matlab_fea_ui.py --viewer me504
```

If no result CSV is provided, the MATLAB viewer searches for the newest `results/fea/**/*_results.csv`. Generate FEA results first with the MATLAB FEA workflow, or pass a result file explicitly:

```powershell
python scripts/gui/start_matlab_fea_ui.py --results-csv results/fea/beam_solver/final_locked_mil_gmt/final_locked_gmt/final_locked_gmt_results.csv
```

Use `--print-command` on either launcher to inspect the command without starting the UI.
