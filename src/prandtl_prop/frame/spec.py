"""``frame:`` section of a design file (the ``frame`` pipeline stage)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrameSpec:
    """Printed airframe sized around this design's prop and motor (prandtl_prop.frame).

    The frame keeps its own requirements file (electronics, materials assignment, joint, print limits);
    the stage replaces that file's propulsion placeholders with this design's BEMT + vehicle results.
    """

    config: str = "drone_frame/config/frame.yaml"        # frame requirements (relative to the repo root)
    materials: str = "drone_frame/config/materials.yaml"  # printed-material allowables
    build_cad: bool = True              # CadQuery parts, STEP/STL, interference + dovetail checks (~10 s)
    compare_materials: bool = False     # also optimise PAHT-CF and PETG-HF arms (~1 min more)
    export_onshape: bool = True         # FeatureScript + #pf10_* variables into the run folder
    optimizer_generations: int = 100    # differential-evolution generations per arm optimisation
    cad_mass_passes: int = 2            # analysis <-> CAD passes closing the non-arm printed mass
    mass_tolerance_fraction: float = 0.05  # warn when frame AUW and multirotor.mass_kg differ by more
    arm_tolerance_fraction: float = 0.05   # warn when motor radius and multirotor.arm_length_m differ by more
