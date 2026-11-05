"""PF10 printed-frame development pipeline (prandtl_prop.frame).

Runs standalone (``python drone_frame/run_pipeline.py``) or as the ``frame`` stage of the prandtl
pipeline (``prandtl run designs/gmt_fpv10_tri.yaml --stages frame``), where the rotor, motor and prop
inputs come from the aero, cad and multirotor stages instead of config placeholders.

Stages (see run_pipeline.py):
    config -> rotor/loads -> layout optimisation -> arm section optimisation (per material)
    -> mass-budget closure -> CAD (CadQuery reference geometry) -> Onshape FeatureScript/variables
    -> X1C print plan -> report
"""

__version__ = "0.1.0"
