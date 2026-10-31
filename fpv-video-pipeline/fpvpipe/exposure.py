"""Exposure math: what ND was on, and what ND to use next time.

First principles
----------------
Exposure at the sensor, in EV at ISO 100, is fixed by aperture N, shutter time t and gain (ISO S):

    EV_cam = log2(N^2 / t) - log2(S / 100)

Auto-exposure picks (t, S) so the image lands at its brightness target, so EV_cam is the light the sensor actually saw.
An ND of k stops removes k stops before the sensor:

    EV_cam = EV_scene - k          ->   k_used = EV_scene - EV_cam

EV_scene is unknown in general, but in daylight it is dominated by the sun. For clear sky we model global horizontal
illuminance from sun elevation h, then convert to incident EV (meter constant C = 250):

    E  ~ 128000 * sin(h) * exp(-0.2 / sin(h))   lux      (h=60 deg -> ~88 klx -> EV 15.1, "sunny 16")
    EV_scene = log2(E / 2.5)

Clouds lower EV_scene by 1-3 stops, so k_used is an upper bound unless the sky was clear. Reflective scenes (snow,
water, sky-filled frames) push auto-exposure the other way. Expect +/- 1 stop.

The recommendation does not need EV_scene. To move from the recorded (t, S) to the target shutter t* at base ISO S0
with the same image brightness:

    extra_stops = log2(t* / t) + log2(S / S0)     (positive -> add that much more ND than was on)
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

ND_STOPS = {0: "none", 1: "ND2", 2: "ND4", 3: "ND8", 4: "ND16", 5: "ND32", 6: "ND64", 7: "ND128", 8: "ND256"}


def ev100(f_number: float, shutter_s: float, iso: float) -> float:
    return math.log2(f_number ** 2 / shutter_s) - math.log2(iso / 100.0)


def sun_elevation_deg(lat: float, lon: float, when_utc: datetime) -> float:
    """NOAA low-precision solar position (about 0.5 deg), refraction ignored."""
    if when_utc.tzinfo is None:
        when_utc = when_utc.replace(tzinfo=timezone.utc)
    when_utc = when_utc.astimezone(timezone.utc)
    doy = when_utc.timetuple().tm_yday
    hour = when_utc.hour + when_utc.minute / 60 + when_utc.second / 3600
    g = 2 * math.pi / 365 * (doy - 1 + (hour - 12) / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = hour * 60 + eqtime + 4 * lon
    ha = math.radians(tst / 4 - 180)
    phi = math.radians(lat)
    cos_zen = math.sin(phi) * math.sin(decl) + math.cos(phi) * math.cos(decl) * math.cos(ha)
    return 90 - math.degrees(math.acos(max(-1.0, min(1.0, cos_zen))))


def clear_sky_ev(sun_elev_deg: float) -> float | None:
    if sun_elev_deg <= 2:
        return None  # twilight: model invalid
    s = math.sin(math.radians(sun_elev_deg))
    lux = 128000 * s * math.exp(-0.2 / s)
    return math.log2(lux / 2.5)


def nd_name(stops: float) -> str:
    k = max(0, min(8, round(stops)))
    return ND_STOPS[k]


def recommend(shutter_s: float, iso: float, fps: float, nd_on_stops: float,
              base_iso: float = 100, shutter_angle: float = 180) -> dict:
    target = shutter_angle / 360.0 / fps
    extra = math.log2(target / shutter_s) + math.log2(iso / base_iso)
    total = nd_on_stops + extra
    return {
        "target_shutter": f"1/{round(1 / target)}",
        "extra_stops": round(extra, 2),
        "recommended_total_stops": round(total, 2),
        "recommended_nd": nd_name(total),
    }
