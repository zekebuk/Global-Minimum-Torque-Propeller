"""PF10 printed-frame development pipeline.

Stages (see run_pipeline.py):
    config -> rotor/loads -> layout optimisation -> arm section optimisation (per material)
    -> mass-budget closure -> CAD (CadQuery reference geometry) -> Onshape FeatureScript/variables
    -> X1C print plan -> report
"""

__version__ = "0.1.0"
