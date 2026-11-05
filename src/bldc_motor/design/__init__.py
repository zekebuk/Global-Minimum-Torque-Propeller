"""RL-driven BLDC motor design pipeline: geometry -> analytical model -> mission objective.

Modules
-------
materials      magnet grades, laminations, magnet wire, illustrative cost coefficients
winding        star-of-slots concentrated windings: layout, winding factor, symmetry
geometry       17-parameter outrunner design space, unit-cube encoding, DFM snapping
model          analytical magnetic circuit / losses / mass / thermal -> MotorParams
manufacturing  DFM rules, unit-cost model, production tolerances
mission        mission specs and the design objective evaluated in the aircraft
env            Gymnasium environment for sequential design refinement (requires gymnasium)
ppo            NumPy PPO trainer (+ optional Stable-Baselines3 hook)
baselines      differential evolution, random search, DFM-grid local refinement
robustness     tolerance Monte Carlo yield
export         lamination DXF, cross-section SVG, winding table, BOM, design JSON
calibration    prototype measurements -> model correction factors
pipeline       end-to-end orchestration
"""
