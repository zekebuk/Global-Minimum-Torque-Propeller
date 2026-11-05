"""Quick shaded previews of the CAD (matplotlib, no GPU / display needed)."""

from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from mpl_toolkits.mplot3d.art3d import Poly3DCollection

LIGHT = np.array([0.4, -0.5, 0.75])
LIGHT = LIGHT / np.linalg.norm(LIGHT)


def _tris(wp, tol=0.4):
    out = []
    for s in wp.solids().vals():
        v, t = s.tessellate(tol, 0.3)
        V = np.array([(p.x, p.y, p.z) for p in v])
        if len(t):
            out.append(V[np.array(t)])
    return np.concatenate(out) if out else np.zeros((0, 3, 3))


def _shade(tris, rgb, alpha=1.0):
    n = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    n /= np.linalg.norm(n, axis=1, keepdims=True) + 1e-12
    k = 0.35 + 0.65 * np.abs(n @ LIGHT)
    c = np.clip(np.outer(k, rgb), 0, 1)
    return np.hstack([c, np.full((len(c), 1), alpha)])


def render(items, path, elev=28, azim=-60, title=None, size=(9, 7), lims=None):
    """items: list of (workplane, rgb, alpha)."""
    fig = plt.figure(figsize=size, dpi=140)
    ax = fig.add_subplot(111, projection="3d")
    allp = []
    for wp, rgb, alpha in items:
        T = _tris(wp)
        if not len(T):
            continue
        ax.add_collection3d(Poly3DCollection(T, facecolors=_shade(T, np.array(rgb), alpha), edgecolor="none"))
        allp.append(T.reshape(-1, 3))
    P = np.concatenate(allp)
    lo, hi = P.min(0), P.max(0)
    if lims is not None:
        lo, hi = np.array(lims[0]), np.array(lims[1])
    c = (lo + hi) / 2
    r = (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r)
    ax.set_ylim(c[1] - r, c[1] + r)
    ax.set_zlim(c[2] - r, c[2] + r)
    ax.set_box_aspect((1, 1, 1))
    ax.view_init(elev=elev, azim=azim)
    ax.set_axis_off()
    if title:
        ax.set_title(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


COL = {"PPA-CF": (0.18, 0.18, 0.2), "PAHT-CF": (0.42, 0.43, 0.47), "PETG-HF": (0.95, 0.47, 0.12), "env": (0.25, 0.55, 0.9)}


def render_all(shapes: dict, mat_of: dict, out_dir):
    parts, arms, env = shapes["parts"], shapes["arms"], shapes["env"]
    frame = [(parts[n], COL[mat_of[n]], 1.0) for n in
             ("bottom_body", "top_plate", "jetson_tray", "canopy", "camera_mount", "battery_pad", "gps_mast")]
    frame += [(a, COL[mat_of["arm"]], 1.0) for a in arms.values()]
    elec = [(env[k], COL["env"], 0.35) for k in ("motors", "battery", "camera")]
    render(frame + elec, f"{out_dir}/assembly_iso.png", title="PF10 assembly (props hidden)")
    render(frame + elec + [(env["props"], (0.6, 0.6, 0.6), 0.15)], f"{out_dir}/assembly_top.png", elev=90, azim=-90,
           title="Plan view with 10-inch prop discs")
    no_canopy = [it for it in frame if it[0] is not parts["canopy"]] + [(env["jetson"], COL["env"], 0.4), (env["fc_stack"], COL["env"], 0.4)]
    render(no_canopy, f"{out_dir}/body_open.png", elev=35, azim=-40, title="Canopy removed: Jetson tray, stack, sockets",
           lims=((-90, -90, -50), (90, 90, 90)))
    render([(parts["arm"], COL[mat_of["arm"]], 1.0)], f"{out_dir}/arm_iso.png", elev=25, azim=-120, title="Modular arm (PPA-CF)")
    render([(parts["arm"], COL[mat_of["arm"]], 1.0)], f"{out_dir}/arm_bottom.png", elev=-35, azim=-120,
           title="Arm underside: counterbores, tapered keel")
    render([(parts["bottom_body"], COL[mat_of["bottom_body"]], 1.0)], f"{out_dir}/bottom_body.png", elev=50, azim=-60,
           title="Bottom body (PAHT-CF): arm sockets, stack and insert bosses")
