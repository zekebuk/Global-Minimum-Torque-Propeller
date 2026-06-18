from __future__ import annotations

import math


def rescale_to_thrust(rpm: float, thrust: float, torque: float, power: float, target_thrust: float = 35.0) -> dict[str, float]:
    scale = target_thrust / thrust
    return {
        "rpm_35N": rpm * math.sqrt(scale),
        "Q_35N_Nm": torque * scale,
        "P_35N_W": power * scale**1.5,
    }
