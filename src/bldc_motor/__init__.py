"""Development toolkit for BLDC motors on FPV multirotors.

Modules
-------
motor           DC-equivalent motor model with temperature dependence
propeller       CT/CP propeller load model and static coefficient fitting
powertrain      battery -> ESC -> motor -> prop operating-point solver
thermal         two-node electro-thermal steady state and transient simulation
teststand       thrust-stand CSV import, unit normalization, steady-state extraction
identification  parameter identification (Kv, R, I0, thermal) from bench data
matching        motor/prop/battery sweep at hover and full throttle
synthetic       synthetic thrust-stand logs for pipeline testing
library         JSON component library (bundled entries are illustrative only)
"""

from bldc_motor.motor import MotorParams, MotorPoint, ThermalParams
from bldc_motor.powertrain import ESC, Battery, Powertrain, PowertrainPoint
from bldc_motor.propeller import PropellerModel

__all__ = [
    "ESC",
    "Battery",
    "MotorParams",
    "MotorPoint",
    "Powertrain",
    "PowertrainPoint",
    "PropellerModel",
    "ThermalParams",
]
