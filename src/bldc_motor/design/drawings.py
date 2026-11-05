"""Dimensioned manufacturing drawings (A3 SVG sheets, combined to PDF) for every part and the assembly.

Conventions: ISO 128 line types, first-angle projection, millimetres, general tolerance
ISO 2768-mK unless toleranced. Shafts are shown unsectioned in assembly sections. All geometry
comes from ``mechanical`` (the same profiles drive the STEP models), so the drawings cannot
disagree with the CAD.

Status: rev P1, PROTOTYPE. The drawings are generated from a parametric model and have not
been checked by a human; verify critical dimensions against the STEP files before ordering.
"""

from __future__ import annotations

import base64
import html
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from bldc_motor.design import export
from bldc_motor.design import mechanical as mech
from bldc_motor.design.mechanical import FITS, GENERAL_TOLERANCE, MechanicalDesign
from bldc_motor.design.model import DesignAnalysis

A3 = (420.0, 297.0)
STATUS = "REV P1 - PROTOTYPE - GENERATED FROM PARAMETRIC MODEL - VERIFY AGAINST STEP BEFORE ORDERING"

STYLE = """
.vis{stroke:#000;stroke-width:0.5;fill:none;stroke-linejoin:round}
.thin{stroke:#000;stroke-width:0.25;fill:none}
.hid{stroke:#000;stroke-width:0.25;fill:none;stroke-dasharray:2,1}
.ctr{stroke:#000;stroke-width:0.18;fill:none;stroke-dasharray:6,1.5,1,1.5}
.dim{stroke:#000;stroke-width:0.18;fill:none}
.arr{fill:#000;stroke:none}
.frame{stroke:#000;stroke-width:0.7;fill:none}
text{font-family:Arial,Helvetica,'DejaVu Sans',sans-serif;fill:#000}
"""
HATCHES = {
    "metal": ("45", 1.2, "#000"),
    "metal2": ("-45", 1.2, "#000"),
    "steel": ("45", 0.8, "#000"),
    "magnet": ("-45", 0.6, "#000"),
    "copper": ("45", 0.5, "#a0522d"),
    "lam": ("0", 0.45, "#000"),
}


def _fmt(v: float, digits: int = 2) -> str:
    return f"{v:.{digits}f}"


def _tol(nominal: float, fit_key: str, digits: int = 2) -> str:
    """'9.000 H6 (+0.009/0)'; ISO fit codes are shown when the FITS entry has one."""

    name, lo, hi = FITS[fit_key]

    def signed(x):
        return "0" if abs(x) < 1e-12 else f"{x:+.3f}"

    code = f" {name.split('/')[-1]}" if name[0].isalpha() else ""
    return f"{nominal:.{digits}f}{code} ({signed(hi)}/{signed(lo)})"


@dataclass
class Sheet:
    number: str
    title: str
    material: str = ""
    finish: str = ""
    scale: str = ""
    mass_g: float | None = None
    rev: str = "P1"
    notes: list[str] = field(default_factory=list)
    project: str = "FPV OUTRUNNER"
    body: list[str] = field(default_factory=list)

    # ---- primitives in sheet mm ---------------------------------------------------------
    def add(self, svg: str) -> None:
        self.body.append(svg)

    def line(self, x1, y1, x2, y2, cls="vis") -> None:
        self.add(f'<line x1="{x1:.3f}" y1="{y1:.3f}" x2="{x2:.3f}" y2="{y2:.3f}" class="{cls}"/>')

    def poly(self, pts, cls="vis", closed=True, fill=None) -> None:
        tag = "polygon" if closed else "polyline"
        style = f' style="fill:{fill}"' if fill else ""
        self.add(f'<{tag} points="{" ".join(f"{x:.3f},{y:.3f}" for x, y in pts)}" class="{cls}"{style}/>')

    def circle(self, cx, cy, r, cls="vis") -> None:
        self.add(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r:.3f}" class="{cls}"/>')

    def text(self, x, y, s, size=2.5, anchor="start", weight="normal", rotate=0.0) -> None:
        rot = f' transform="rotate({rotate:.2f} {x:.3f} {y:.3f})"' if rotate else ""
        self.add(
            f'<text x="{x:.3f}" y="{y:.3f}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}"{rot}>{html.escape(str(s))}</text>'
        )

    def arrow(self, tip, direction, length=2.2, width=0.75) -> None:
        dx, dy = direction
        n = math.hypot(dx, dy) or 1.0
        ux, uy = dx / n, dy / n
        bx, by = tip[0] - ux * length, tip[1] - uy * length
        px, py = -uy * width / 2, ux * width / 2
        self.add(
            f'<polygon points="{tip[0]:.3f},{tip[1]:.3f} {bx + px:.3f},{by + py:.3f} {bx - px:.3f},{by - py:.3f}" class="arr"/>'
        )

    def table(self, x, y, widths, rows, row_h=5.0, size=2.4, header=True) -> float:
        total = sum(widths)
        for r, row in enumerate(rows):
            yy = y + r * row_h
            self.line(x, yy, x + total, yy, "thin")
            cx = x
            for w, cell in zip(widths, row, strict=False):
                self.text(cx + 1.0, yy + row_h - 1.4, cell, size, weight="bold" if header and r == 0 else "normal")
                cx += w
        bottom = y + len(rows) * row_h
        self.line(x, bottom, x + total, bottom, "thin")
        cx = x
        for w in [0, *widths]:
            cx += w
            self.line(cx, y, cx, bottom, "thin")
        return bottom

    def image_svg(self, svg_text: str, x, y, w, h) -> None:
        data = base64.b64encode(svg_text.encode()).decode()
        self.add(
            f'<image x="{x}" y="{y}" width="{w}" height="{h}" preserveAspectRatio="xMidYMid meet" href="data:image/svg+xml;base64,{data}"/>'
        )

    # ---- sheet frame ---------------------------------------------------------------------
    def render(self, sheet_index: int, sheet_count: int) -> str:
        W, H = A3
        defs = ["<defs>"]
        for key, (angle, gap, color) in HATCHES.items():
            defs.append(
                f'<pattern id="h-{key}" patternUnits="userSpaceOnUse" width="{gap}" height="{gap}" patternTransform="rotate({angle})">'
                f'<line x1="0" y1="0" x2="0" y2="{gap}" stroke="{color}" stroke-width="0.15"/></pattern>'
            )
        defs.append("</defs>")
        frame = [
            f'<rect x="0" y="0" width="{W}" height="{H}" fill="white"/>',
            f'<rect x="10" y="10" width="{W - 20}" height="{H - 20}" class="frame"/>',
        ]
        # zone markers
        for i in range(1, 9):
            x = 10 + (W - 20) * (i - 0.5) / 8
            frame.append(f'<text x="{x:.1f}" y="8" font-size="2.5" text-anchor="middle">{i}</text>')
        tb = self._title_block(W - 190, H - 62, 180, 52, sheet_index, sheet_count)
        notes = self._notes_block()
        status = f'<text x="12" y="{H - 4}" font-size="2.6" font-weight="bold">{html.escape(STATUS)}</text>'
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}mm" height="{H}mm" viewBox="0 0 {W} {H}">'
            f"<style>{STYLE}</style>{''.join(defs)}{''.join(frame)}{''.join(self.body)}{tb}{notes}{status}</svg>"
        )

    def _title_block(self, x, y, w, h, idx, count) -> str:
        rows = [
            ("PROJECT", self.project, "DWG NO", self.number),
            ("TITLE", self.title, "REV", self.rev),
            ("MATERIAL", self.material, "SCALE", self.scale),
            ("FINISH", self.finish, "MASS", f"{self.mass_g:.2f} g" if self.mass_g is not None else "-"),
            ("TOLERANCE", f"{GENERAL_TOLERANCE} unless stated; dims mm", "SHEET", f"{idx} / {count}"),
            ("DRAWN", f"bldc_motor.design.drawings  {date.today().isoformat()}", "CHECKED", "NOT CHECKED"),
        ]
        out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" class="frame" fill="white"/>']
        rh = h / len(rows)
        for i, (k1, v1, k2, v2) in enumerate(rows):
            yy = y + i * rh
            if i:
                out.append(f'<line x1="{x}" y1="{yy}" x2="{x + w}" y2="{yy}" class="thin"/>')
            size = 3.6 if k1 == "TITLE" else 2.6
            out.append(f'<text x="{x + 1.5}" y="{yy + 3}" font-size="1.8">{k1}</text>')
            out.append(
                f'<text x="{x + 1.5}" y="{yy + rh - 1.6}" font-size="{size}" font-weight="bold">{html.escape(v1)}</text>'
            )
            out.append(f'<text x="{x + 128}" y="{yy + 3}" font-size="1.8">{k2}</text>')
            out.append(
                f'<text x="{x + 128}" y="{yy + rh - 1.6}" font-size="2.8" font-weight="bold">{html.escape(v2)}</text>'
            )
        out.append(f'<line x1="{x + 126}" y1="{y}" x2="{x + 126}" y2="{y + h}" class="thin"/>')
        # first-angle projection symbol
        sx, sy = x + w - 22, y + h - 6.5
        out.append(
            f'<circle cx="{sx + 14}" cy="{sy}" r="2.2" class="thin"/><circle cx="{sx + 14}" cy="{sy}" r="1.0" class="thin"/>'
        )
        out.append(
            f'<polygon points="{sx},{sy - 1.0} {sx + 7},{sy - 2.2} {sx + 7},{sy + 2.2} {sx},{sy + 1.0}" class="thin"/>'
        )
        return "".join(out)

    def _notes_block(self) -> str:
        if not self.notes:
            return ""
        x, y = 14.0, A3[1] - 14.0 - 4.2 * len(self.notes)
        out = [f'<text x="{x}" y="{y - 2}" font-size="3" font-weight="bold">NOTES</text>']
        for i, note in enumerate(self.notes, start=1):
            out.append(f'<text x="{x}" y="{y + 4.2 * i - 1}" font-size="2.5">{i}. {html.escape(note)}</text>')
        return "".join(out)


class View:
    """Maps model mm (x right, y up) onto a sheet at a scale."""

    def __init__(
        self, sheet: Sheet, ox: float, oy: float, scale: float, label: str | None = None, label_y: float | None = None
    ) -> None:
        self.s, self.ox, self.oy, self.k = sheet, ox, oy, scale
        if label:
            sheet.text(ox, label_y if label_y is not None else oy + 4, label, 3.0, "middle", "bold")

    def p(self, x, y):
        return (self.ox + self.k * x, self.oy - self.k * y)

    def poly(self, pts, cls="vis", closed=True, hatch=None) -> None:
        self.s.poly([self.p(x, y) for x, y in pts], cls, closed, f"url(#h-{hatch})" if hatch else None)

    def line(self, x1, y1, x2, y2, cls="vis") -> None:
        a, b = self.p(x1, y1), self.p(x2, y2)
        self.s.line(*a, *b, cls)

    def circle(self, cx, cy, r, cls="vis") -> None:
        c = self.p(cx, cy)
        self.s.circle(*c, r * self.k, cls)

    def centerline_v(self, x, y1, y2) -> None:
        self.line(x, y1, x, y2, "ctr")

    def cross(self, cx, cy, r) -> None:
        self.line(cx - r, cy, cx + r, cy, "ctr")
        self.line(cx, cy - r, cx, cy + r, "ctr")

    # ---- dimensions -----------------------------------------------------------------------
    def dim(self, p1, p2, offset, text, side=1, size=2.5) -> None:
        """Aligned linear dimension between model points; ``offset`` in sheet mm (normal side)."""

        a, b = self.p(*p1), self.p(*p2)
        dx, dy = b[0] - a[0], b[1] - a[1]
        L = math.hypot(dx, dy) or 1.0
        ux, uy = dx / L, dy / L
        nx, ny = -uy * side, ux * side
        d1 = (a[0] + nx * offset, a[1] + ny * offset)
        d2 = (b[0] + nx * offset, b[1] + ny * offset)
        sgn = 1 if offset >= 0 else -1
        self.s.line(a[0] + nx * 1.0 * sgn, a[1] + ny * 1.0 * sgn, d1[0] + nx * 1.5 * sgn, d1[1] + ny * 1.5 * sgn, "dim")
        self.s.line(b[0] + nx * 1.0 * sgn, b[1] + ny * 1.0 * sgn, d2[0] + nx * 1.5 * sgn, d2[1] + ny * 1.5 * sgn, "dim")
        if L >= 7.0:
            self.s.line(*d1, *d2, "dim")
            self.s.arrow(d1, (-ux, -uy))
            self.s.arrow(d2, (ux, uy))
        else:  # arrows outside for short dimensions
            self.s.line(d1[0] - ux * 5, d1[1] - uy * 5, d2[0] + ux * 5, d2[1] + uy * 5, "dim")
            self.s.arrow(d1, (ux, uy))
            self.s.arrow(d2, (-ux, -uy))
        angle = math.degrees(math.atan2(dy, dx))
        if angle > 90 or angle <= -90:
            angle += 180
        mx, my = (d1[0] + d2[0]) / 2, (d1[1] + d2[1]) / 2
        # outward = away from the measured feature; text "up" direction after rotation
        ox_, oy_ = nx * sgn, ny * sgn
        upx, upy = math.sin(math.radians(angle)), -math.cos(math.radians(angle))
        gap = 1.0 if ox_ * upx + oy_ * upy > 0 else 1.0 + size * 0.8
        self.s.text(mx + ox_ * gap, my + oy_ * gap, text, size, "middle", rotate=angle)

    def dim_h(self, x1, x2, y, offset, text, size=2.5) -> None:
        """Horizontal dimension; positive ``offset`` (sheet mm) places it above, negative below."""

        self.dim((x1, y), (x2, y), offset, text, side=-1, size=size)

    def dim_v(self, y1, y2, x, offset, text, size=2.5) -> None:
        """Vertical dimension; positive ``offset`` places it to the right, negative to the left."""

        self.dim((x, y1), (x, y2), offset, text, side=1, size=size)

    def dia(self, cx, cy, r, angle_deg, text, shelf=12.0, size=2.5) -> None:
        u = (math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg)))
        a = self.p(cx + r * u[0], cy + r * u[1])
        b = self.p(cx - r * u[0], cy - r * u[1])
        sx, sy = a[0] - b[0], a[1] - b[1]
        n = math.hypot(sx, sy)
        ux, uy = sx / n, sy / n
        end = (a[0] + ux * 6, a[1] + uy * 6)
        self.s.line(*b, *end, "dim")
        self.s.arrow(a, (ux, uy))
        self.s.arrow(b, (-ux, -uy))
        direction = 1 if ux >= 0 else -1
        self.s.line(*end, end[0] + direction * shelf, end[1], "dim")
        self.s.text(end[0] + direction * 1.0, end[1] - 0.8, text, size, "start" if direction > 0 else "end")

    def radius(self, cx, cy, r, angle_deg, text, size=2.5, from_center=True) -> None:
        u = (math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg)))
        a = self.p(cx + r * u[0], cy + r * u[1])
        c = self.p(cx, cy) if from_center else self.p(cx + (r - 8.0 / self.k) * u[0], cy + (r - 8.0 / self.k) * u[1])
        n = math.hypot(a[0] - c[0], a[1] - c[1]) or 1.0
        ux, uy = (a[0] - c[0]) / n, (a[1] - c[1]) / n
        end = (a[0] + ux * 6, a[1] + uy * 6)
        self.s.line(*c, *end, "dim")
        self.s.arrow(a, (ux, uy))
        direction = 1 if ux >= 0 else -1
        self.s.line(*end, end[0] + direction * 10, end[1], "dim")
        self.s.text(end[0] + direction * 1.0, end[1] - 0.8, text, size, "start" if direction > 0 else "end")

    def angle(self, cx, cy, r, a1, a2, text, size=2.5) -> None:
        pts = [
            self.p(
                cx + r * math.cos(math.radians(a1 + (a2 - a1) * t / 24)),
                cy + r * math.sin(math.radians(a1 + (a2 - a1) * t / 24)),
            )
            for t in range(25)
        ]
        self.s.poly(pts, "dim", closed=False)
        self.s.arrow(pts[0], (pts[0][0] - pts[1][0], pts[0][1] - pts[1][1]))
        self.s.arrow(pts[-1], (pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]))
        mid = math.radians((a1 + a2) / 2)
        t = self.p(cx + (r + 2.0 / self.k + 1.5) * math.cos(mid), cy + (r + 2.0 / self.k + 1.5) * math.sin(mid))
        self.s.text(t[0], t[1], text, size, "middle")

    def leader(self, x, y, tx, ty, text, size=2.5) -> None:
        a = self.p(x, y)
        self.s.line(*a, tx, ty, "dim")
        self.s.arrow(a, (a[0] - tx, a[1] - ty))
        direction = 1 if tx >= a[0] else -1
        self.s.line(tx, ty, tx + direction * 4, ty, "dim")
        self.s.text(tx + direction * 5, ty + 0.9, text, size, "start" if direction > 0 else "end")

    def balloon(self, x, y, tx, ty, n) -> None:
        a = self.p(x, y)
        self.s.add(f'<circle cx="{a[0]:.3f}" cy="{a[1]:.3f}" r="0.5" class="arr"/>')
        self.s.line(*a, tx, ty, "dim")
        self.s.circle(tx, ty, 3.2, "thin")
        self.s.text(tx, ty + 1.2, str(n), 3.2, "middle", "bold")

    # ---- revolved bodies --------------------------------------------------------------------
    def revolved_section(self, profile, hatch="metal", mirror=True) -> None:
        """Full section of a body of revolution: both halves hatched, edge lines across hollows."""

        sides = (1, -1) if mirror else (1,)
        for sgn in sides:
            self.poly([(sgn * r, z) for r, z in profile], "vis", True, hatch)
        for z, w in _section_edges(profile):
            self.line(-w, z, w, z, "vis")

    def revolved_outside(self, profile) -> None:
        """Outside view of a solid of revolution (e.g. a shaft): silhouette + edge circles."""

        outline = [(r, z) for r, z in profile] + [(-r, z) for r, z in reversed(profile)]
        self.poly(outline, "vis", True)
        for r, z in profile:
            if r > 1e-6:
                self.line(-r, z, r, z, "vis")


def _inside(profile, r, z) -> bool:
    inside = False
    n = len(profile)
    for i in range(n):
        x1, y1 = profile[i]
        x2, y2 = profile[(i + 1) % n]
        if (y1 > z) != (y2 > z):
            xi = x1 + (z - y1) * (x2 - x1) / (y2 - y1)
            if r < xi:
                inside = not inside
    return inside


def _hollow_radius(profile, z) -> float | None:
    rmax = max(r for r, _ in profile)
    steps = 400
    for i in range(steps + 1):
        r = rmax * i / steps
        if _inside(profile, r + 1e-6, z):
            return r
    return None


def _section_edges(profile, eps=1e-4):
    edges = []
    n = len(profile)
    for i in range(n):
        (r1, z1), (r2, z2) = profile[i], profile[(i + 1) % n]
        if abs(z1 - z2) > 1e-9 or min(r1, r2) <= 1e-6:
            continue
        below, above = _hollow_radius(profile, z1 - eps), _hollow_radius(profile, z1 + eps)
        if below is None or above is None:
            w = below if above is None else above
        elif abs(below - above) > 1e-3:
            w = min(below, above)
        else:
            continue
        if w and w > 1e-3:
            edges.append((z1, w))
    return edges


# ======================================================================================
# Individual sheets
# ======================================================================================


def _tooth_points(analysis: DesignAnalysis, arc_segments: int = 10):
    """Contiguous outline of tooth T1 with the adjoining slot-bottom arcs (polyline order)."""

    pts = export.stator_outline_mm(analysis, arc_segments=arc_segments)
    block = 8 + 2 * (arc_segments - 1)
    tooth = pts[: 8 + arc_segments - 1]
    after = pts[8 + arc_segments - 1 : block] + pts[block : block + 4]  # up to the next tooth's shoe tip
    before = pts[-(arc_segments - 1) - 4 :]  # from the previous tooth's shoe tip
    return before + tooth + after


def sheet_lamination(m: MechanicalDesign, a: DesignAnalysis, mass: float) -> Sheet:
    d = m.dims
    sh = Sheet(
        "DWG-001",
        "STATOR LAMINATION",
        f"{d['lamination_t']:.2f} mm NO Si-steel (10JNEX900 / NO10 or equiv.)",
        "C-5 inorganic insulation both sides, as rolled",
        "4:1, detail 10:1",
        mass,
    )
    v = View(sh, 105, 115, 4.0)
    pts = export.stator_outline_mm(a, arc_segments=10)
    v.poly(pts, "vis")
    v.circle(0, 0, d["stator_id"] / 2)
    v.cross(0, 0, d["stator_od"] / 2 + 3)
    v.circle(0, 0, d["slot_bottom_d"] / 2, "ctr")
    v.dia(0, 0, d["stator_od"] / 2, 125, f"Ø{d['stator_od']:.2f} ±0.02")
    v.dia(0, 0, d["stator_id"] / 2, -35, f"Ø{_tol(d['stator_id'], 'stator_id')}", shelf=24)
    v.dia(0, 0, d["slot_bottom_d"] / 2, 205, f"Ø{d['slot_bottom_d']:.2f} SLOT BOTTOM", shelf=26)
    v.dia(0, 0, d["shoe_inner_d"] / 2, 65, f"Ø{d['shoe_inner_d']:.2f} UNDER SHOE (REF)", shelf=30)
    sh.text(105, 182, f"{d['slots']} TEETH EQUALLY SPACED {360 / d['slots']:.1f}°", 3.0, "middle", "bold")
    # detail of one tooth
    pitch = 360.0 / d["slots"]
    tooth = _tooth_points(a)
    cx = -(d["slot_bottom_d"] + d["stator_od"]) / 4
    det = View(sh, 300, 125, 10.0, "DETAIL A - TOOTH T1 (10:1)", label_y=200)
    shifted = [(x + cx, y) for x, y in tooth]
    det.poly(shifted, "vis", closed=False)
    r_s = d["stator_od"] / 2
    w = d["tooth_width"]
    r1, r2 = d["slot_bottom_d"] / 2, d["shoe_inner_d"] / 2
    xm = (r1 + r2) / 2 + cx
    det.dim_v(-w / 2, w / 2, xm, -14, f"{w:.2f} ±0.02")
    shoe_half = (a.geometry.slot_pitch - a.geometry.slot_opening) * 1e3 / 2 / r_s
    nxt = math.radians(pitch) - shoe_half
    p_a = (r_s * math.cos(shoe_half) + cx, r_s * math.sin(shoe_half))
    p_b = (r_s * math.cos(nxt) + cx, r_s * math.sin(nxt))
    det.dim(p_a, p_b, 9, f"{d['slot_opening']:.2f} ±0.03 SLOT OPENING", side=-1)
    det.dim_h(r_s - d["tip_height"] + cx, r_s + cx, -w / 2 - 1.5, -10, f"{d['tip_height']:.2f}")
    det.dim_h(r1 + cx, r2 + cx, -w / 2 - 0.5, -18, f"{r2 - r1:.2f} SLOT DEPTH (REF)")
    sh.notes = [
        f"Material: {d['lamination_t']:.2f} mm non-oriented electrical steel, 10JNEX900, NO10 or equivalent (P1.0/400 <= 13 W/kg).",
        "Profile per lamination.dxf (1:1 mm). Profile tolerance 0.02 mm. Burr <= 0.01 mm. Flat within 0.02 mm.",
        "Cut by wire-EDM, photo-etch or fibre laser; stress-relief anneal laser-cut parts (750-800 C, N2) unless vendor shows equal loss.",
        f"Quantity per motor: {d['lamination_count']} pcs ({d['stack']:.2f} mm stack). Keep grain/rolling direction marked, rotate alternate laminations 90 deg if anisotropic.",
        "No sharp edges at slot bottom and shoe tips: R0.1 min to protect the magnet wire.",
    ]
    return sh


def sheet_stator_core(m: MechanicalDesign, a: DesignAnalysis, mass: float) -> Sheet:
    d = m.dims
    sh = Sheet(
        "DWG-002",
        "STATOR CORE, BONDED + COATED",
        f"{d['lamination_count']} x DWG-001",
        "Epoxy powder coat (3M Scotchcast 265 or equiv.), class F min",
        "4:1",
        mass,
    )
    v = View(sh, 100, 125, 4.0, "TOP VIEW", label_y=185)
    v.poly(export.stator_outline_mm(a, arc_segments=8), "vis")
    v.circle(0, 0, d["stator_id"] / 2)
    v.cross(0, 0, d["stator_od"] / 2 + 3)
    v.dia(0, 0, d["stator_id"] / 2, 45, f"Ø{_tol(d['stator_id'], 'stator_id')} BORE FREE OF COATING", shelf=40)
    s = View(sh, 270, 150, 4.0, "SECTION A-A", label_y=185)
    zb, zt = 0.0, d["stack"]
    r_s, r_i, r1 = d["stator_od"] / 2, d["stator_id"] / 2, d["slot_bottom_d"] / 2
    for sgn in (1, -1):
        s.poly([(sgn * r_i, zb), (sgn * r_s, zb), (sgn * r_s, zt), (sgn * r_i, zt)], "vis", True, "lam")
        c = d["coat"]
        s.poly(
            [(sgn * r1, zb - c), (sgn * (r_s - 0.0), zb - c), (sgn * (r_s - 0.0), zb), (sgn * r1, zb)],
            "thin",
            True,
            None,
        )
    s.centerline_v(0, -3, zt + 3)
    s.dim_v(zb, zt, r_s, 12, f"{d['stack']:.2f} ±0.05 ({d['lamination_count']} LAM.)")
    s.dim_h(-r_s, r_s, zt, 10, f"Ø{d['stator_od']:.2f} ±0.03 (OVER COAT Ø{d['stator_od'] + 2 * d['coat']:.2f} MAX)")
    s.dim_h(-r_i, r_i, zb, -10, f"Ø{d['stator_id']:.2f}")
    sh.notes = [
        "Stack laminations on a mandrel; bond by self-bonding varnish (Backlack), glue-dot or 3 axial laser weld seams on the ID.",
        f"Stack height {d['stack']:.2f} ±0.05 mm under 0.5 MPa; teeth aligned within 0.03 mm; perpendicularity of faces to bore 0.03.",
        f"Coat all surfaces except bore: {d['coat'] - 0.05:.2f}-{d['coat'] + 0.05:.2f} mm, edges fully covered, no voids; dielectric >= 1.5 kV.",
        "Bore Ø free of coating (mask or ream after cure) for bonded fit onto base tube (DWG-007).",
        "Inspect: 100 % visual (edge coverage), 5 % section or spark test.",
    ]
    return sh


def sheet_wound_stator(m: MechanicalDesign, a: DesignAnalysis, mass: float) -> Sheet:
    d = m.dims
    des = a.design
    i = a.internals
    sh = Sheet(
        "DWG-003",
        "WOUND STATOR",
        f"DWG-002 + AWG{des['awg']} magnet wire",
        "Impregnated (varnish), class 180 min",
        "4:1",
        mass,
    )
    v = View(sh, 100, 125, 4.0, "WINDING LAYOUT (VIEW FROM BELL SIDE)", label_y=190)
    v.poly(export.stator_outline_mm(a, arc_segments=8), "vis")
    v.circle(0, 0, d["stator_id"] / 2)
    v.cross(0, 0, d["stator_od"] / 2 + 3)
    r_label = (d["slot_bottom_d"] + d["shoe_inner_d"]) / 4
    for k, (phase, direction) in enumerate(a.layout.coils):
        ang = 2 * math.pi * k / d["slots"]
        p = v.p(r_label * math.cos(ang), r_label * math.sin(ang))
        sh.text(p[0], p[1] + 1.2, phase if direction > 0 else phase.lower(), 3.6, "middle", "bold")
        q = v.p((d["stator_od"] / 2 + 2.2) * math.cos(ang), (d["stator_od"] / 2 + 2.2) * math.sin(ang))
        sh.text(q[0], q[1] + 1, f"T{k + 1}{'' if direction > 0 else '*'}", 2.4, "middle")
    rows = [["PHASE", "COIL SEQUENCE (start -> finish)", "TURNS"]]
    for ph in "ABC":
        seq = " -> ".join(f"T{t}{'(CW)' if dirn > 0 else '(CCW)'}" for t, dirn in a.layout.coils_of(ph))
        rows.append([ph, seq, f"{des['turns']} / coil"])
    sh.text(215, 40, "WINDING TABLE", 3.0, weight="bold")
    sh.table(215, 43, [14, 140, 26], rows, 6.0, 2.4)
    sh.text(215, 80, "ELECTRICAL", 3.0, weight="bold")
    r_ll = i["r_phase_ohm"] * 2 * 1e3 if des["connection"] == "wye" else i["r_phase_ohm"] * 2 / 3 * 1e3
    hipot = 2 * 25.2 + 1000
    erows = [
        ["ITEM", "REQUIREMENT"],
        [
            "Connection",
            f"{des['connection'].upper()} ({'star point inside winding, sleeved' if des['connection'] == 'wye' else 'delta'})",
        ],
        [
            "Wire",
            f"AWG{des['awg']} ({i['wire_bare_mm']:.3f} mm bare / {i['wire_insulated_mm']:.3f} mm max OD) heavy build, class 200",
        ],
        ["Turns", f"{des['turns']} per tooth, {des['strands']} strand(s) in hand, layer-wound, no crossovers in slot"],
        ["Slot fill (bare Cu)", f"{i['fill_bare']:.2f} (design); max 0.45"],
        ["R line-line @25 C", f"{r_ll:.1f} mOhm ±6 % each pair; pair imbalance <= 2 %"],
        ["Hipot winding-core", f"{hipot / 1000:.2f} kV AC 1 s (2U+1000 V), leakage <= 1 mA"],
        ["Insulation res.", ">= 100 MOhm at 500 V DC"],
        ["Leads", f"3 x {d['lead_awg']} AWG silicone, {d['lead_length']:.0f} mm, tinned 3 mm, heat-shrink at joint"],
    ]
    sh.table(215, 83, [40, 140], erows, 5.5, 2.3)
    sh.notes = [
        "Wind T1 first; '*' = reverse winding direction (CCW viewed from bell side). Inter-coil links routed on the base side.",
        "Lead joints: strip enamel (solder pot 450 C or abrasive), solder, sleeve with PTFE/silicone tube; anchor with Kapton + varnish.",
        "Impregnate: dip in Elmotherm 2340/70 (or equiv. class 180+) and cure per datasheet; no varnish in the bore.",
        "Test 100 %: line-line resistance (4-wire, temperature corrected), hipot, IR, visual (no nicks, no wire above shoe).",
    ]
    return sh


def sheet_magnet(m: MechanicalDesign, mass: float) -> Sheet:
    d = m.dims
    sh = Sheet(
        "DWG-004",
        "ARC MAGNET (-01 N-IN / -02 S-IN)",
        f"Sintered NdFeB {d['magnet_grade']} (alt. N48H/N45SH)",
        "NiCuNi 10-20 µm",
        "10:1",
        mass,
    )
    ri, ro = d["magnet_ri"], d["magnet_ro"]
    half = math.radians(d["magnet_arc_deg"]) / 2
    cy = -(ri + ro) / 2  # arc centre below; view origin sits at mid-thickness
    v = View(sh, 150, 130, 10.0, "END VIEW", label_y=200)

    def arc(r, n=40):
        return [
            (r * math.sin(-half + 2 * half * t / n), r * math.cos(-half + 2 * half * t / n) + cy) for t in range(n + 1)
        ]

    v.poly(arc(ro) + list(reversed(arc(ri))), "vis")
    for sgn in (1, -1):
        v.line(
            sgn * (ri - 1.0) * math.sin(half),
            (ri - 1.0) * math.cos(half) + cy,
            sgn * (ro + 1.5) * math.sin(half),
            (ro + 1.5) * math.cos(half) + cy,
            "ctr",
        )
    v.line(0, ri - 1.0 + cy, 0, ro + 1.5 + cy, "ctr")
    v.angle(0, cy, ro + 1.2, 90 - math.degrees(half), 90 + math.degrees(half), f"{d['magnet_arc_deg']:.2f}° ±0.15°")
    v.radius(0, cy, ri, 90 - math.degrees(half) * 0.4, f"R{ri:.2f} ±0.03 (INNER)", from_center=False)
    v.radius(0, cy, ro, 90 + math.degrees(half) * 0.6, f"R{ro:.2f} ±0.03 (OUTER)", from_center=False)
    v.dim(
        (ri * math.sin(half), ri * math.cos(half) + cy),
        (ro * math.sin(half), ro * math.cos(half) + cy),
        10,
        f"{d['magnet_thickness']:.2f} ±0.02",
        side=-1,
    )
    for k in range(3):
        xa = (-0.5 + 0.5 * k) * ri * math.sin(half)
        v.line(xa, ri + cy + 0.2, xa, ro + cy - 0.2, "thin")
        sh.arrow(v.p(xa, ro + cy - 0.2), (0, -1), 1.6, 0.6)
    s = View(sh, 320, 165, 10.0, "SIDE VIEW", label_y=200)
    t = d["magnet_thickness"]
    s.poly([(-t / 2, 0), (t / 2, 0), (t / 2, d["magnet_length"]), (-t / 2, d["magnet_length"])], "vis")
    s.dim_v(0, d["magnet_length"], t / 2, 14, f"{d['magnet_length']:.2f} ±0.05")
    sh.text(150, 206, "ARROWS = MAGNETIZATION DIRECTION FOR -01 (N ON CONCAVE FACE)", 2.5, "middle")
    sh.notes = [
        f"Grade {d['magnet_grade']}: Br >= 1.42 T, Hcj >= 876 kA/m (N52). Max operating temp per grade; design burst magnet temp see design.json.",
        "Magnetize radially. -01: N pole on concave (inner) face, marked with a dot on the outer face. -02: S pole on concave face, unmarked.",
        f"Supply as matched set: {d['poles'] // 2} x -01 + {d['poles'] // 2} x -02 per motor; flux of each piece within ±2 % of set mean.",
        "Edges chamfered 0.1 x 45 deg max; no chips > 0.2 mm; coating free of blisters; salt spray 24 h.",
        "Pack in alternating spacers; magnets are brittle and pinch hazards - handle with non-magnetic tools.",
    ]
    return sh


def sheet_ring(m: MechanicalDesign, mass: float) -> Sheet:
    d = m.dims
    sh = Sheet(
        "DWG-005",
        "BACK-IRON RING",
        "Low-carbon steel 1010/1018 (or ST37), annealed",
        "Zinc-nickel 5-8 µm or black oxide + oil",
        "5:1",
        mass,
    )
    z0 = d["z"]["ring_bottom"]
    prof = [(r, zz - z0) for r, zz in mech.ring_profile(m)]
    v = View(sh, 160, 160, 5.0, "SECTION A-A", label_y=200)
    v.revolved_section(prof, "steel")
    v.centerline_v(0, -3, d["ring_length"] + 3)
    v.dim_h(-d["ring_od"] / 2, d["ring_od"] / 2, d["ring_length"], 10, f"Ø{_tol(d['ring_od'], 'ring_od')}")
    v.dim_h(-d["ring_id"] / 2, d["ring_id"] / 2, 0, -10, f"Ø{d['ring_id']:.2f} ±0.02")
    v.dim_v(0, d["ring_length"], d["ring_od"] / 2, 14, f"{d['ring_length']:.2f} ±0.05")
    sh.notes = [
        "ID concentric to OD within 0.02 mm; faces perpendicular to axis within 0.03 mm.",
        "Break edges 0.1-0.2 mm x 45 deg; ID edge on magnet-insertion side 0.3 x 45 deg lead-in.",
        "Saturation: design flux density in ring per design.json (b_bell_t); do not substitute stainless (non-magnetic) steel.",
    ]
    return sh


def sheet_bell(m: MechanicalDesign, mass: float) -> Sheet:
    d, z = m.dims, m.dims["z"]
    sh = Sheet(
        "DWG-006", "BELL", "Aluminium 7075-T6", "Hard anodize type II, black, 10-15 µm (mask ring bore)", "4:1", mass
    )
    z0 = z["skirt_bottom"]
    prof = [(r, zz - z0) for r, zz in mech.bell_profile(m)]
    v = View(sh, 130, 190, 4.0, "SECTION A-A", label_y=212)
    v.revolved_section(prof, "metal")
    top = z["hub_top"] - z0
    v.centerline_v(0, -3, top + 3)
    ro = d["bell_od"] / 2
    v.dim_h(-ro, ro, top, 14, f"Ø{d['bell_od']:.2f} ±0.05")
    v.dim_h(-d["ring_od"] / 2, d["ring_od"] / 2, 0, -9, f"Ø{d['ring_od']:.3f} H7 (+0.021/0) RING BORE")
    v.dim_h(-d["hub_boss_d"] / 2, d["hub_boss_d"] / 2, top, 6, f"Ø{d['hub_boss_d']:.1f}")
    v.dim_v(0, z["ring_top"] - z0, ro, 10, f"{z['ring_top'] - z0:.2f} +0.05/0")
    v.dim_v(z["plate_bottom"] - z0, z["plate_top"] - z0, ro, 22, f"{d['bell_plate']:.2f}")
    v.dim_v(0, top, ro, 34, f"{top:.2f} ±0.05")
    v.leader(d["hub_bore"] / 2, top - 1.0, 160, 110, f"Ø{_tol(d['hub_bore'], 'hub_bore')} THRU")
    v.leader(
        -d["bell_inner_relief_d"] / 2,
        (z["plate_bottom"] + z["ring_top"]) / 2 - z0,
        90,
        120,
        f"Ø{d['bell_inner_relief_d']:.2f} RELIEF",
    )
    v.leader(-d["hub_boss_d"] / 2, z["grub"] - z0, 105, 100, "M3x0.5 GRUB, 1 PL RADIAL")
    t = View(sh, 310, 140, 3.0, "TOP VIEW", label_y=200)
    t.circle(0, 0, ro)
    t.circle(0, 0, d["hub_boss_d"] / 2)
    t.circle(0, 0, d["hub_bore"] / 2)
    t.circle(0, 0, d["vent_pcd"] / 2, "ctr")
    for k in range(d["vent_holes"]):
        ang = 2 * math.pi * k / d["vent_holes"]
        t.circle(d["vent_pcd"] / 2 * math.cos(ang), d["vent_pcd"] / 2 * math.sin(ang), d["vent_hole_d"] / 2)
    t.cross(0, 0, ro + 3)
    t.leader(
        d["vent_pcd"] / 2 * math.cos(math.radians(60)) + d["vent_hole_d"] / 2 * 0.7,
        d["vent_pcd"] / 2 * math.sin(math.radians(60)),
        352,
        92,
        f"{d['vent_holes']}x Ø{d['vent_hole_d']:.1f} THRU EQ SP PCD {d['vent_pcd']:.1f}",
    )
    sh.notes = [
        "Ring bore and hub bore coaxial within 0.02 mm (A); plate top face perpendicular to hub bore within 0.02 mm.",
        "Ring (DWG-005) is pressed in (H7/s6) with Loctite 638 or 648; anodize masked in ring bore.",
        "Grub hole: tap M3x0.5 radially through hub boss on mid-height; deburr inside the bore.",
        "Dynamic balance as assembled rotor, see DWG-000. Remove material only on skirt OD bottom chamfer zone.",
    ]
    return sh


def sheet_base(m: MechanicalDesign, mass: float) -> Sheet:
    d, z = m.dims, m.dims["z"]
    sh = Sheet(
        "DWG-007",
        "BASE (STATOR MOUNT)",
        "Aluminium 7075-T6",
        "Hard anodize type II, black (mask bearing bores and tube OD)",
        "4:1",
        mass,
    )
    prof = mech.base_profile(m)
    v = View(sh, 120, 185, 4.0, "SECTION A-A", label_y=215)
    v.revolved_section(prof, "metal")
    v.centerline_v(0, -3, z["tube_top"] + 3)
    rf = d["flange_d"] / 2
    v.dim_h(-rf, rf, 0, -16, f"Ø{d['flange_d']:.2f} ±0.05")
    v.dim_h(-d["tube_od"] / 2, d["tube_od"] / 2, z["tube_top"], 10, f"Ø{d['tube_od']:.3f} (0/-0.030) STATOR SEAT")
    v.dim_h(
        -d["bearing_D"] / 2,
        d["bearing_D"] / 2,
        z["tube_top"],
        18,
        f"Ø{_tol(d['bearing_D'], 'bearing_bore', 3)} BOTH ENDS",
    )
    v.dim_v(0, d["flange_thickness"], rf, 10, f"{d['flange_thickness']:.2f}")
    v.dim_v(0, z["tube_top"], rf, 22, f"{z['tube_top']:.2f} ±0.03")
    v.dim_v(z["upper_bearing_bottom"], z["tube_top"], -d["bearing_D"] / 2, -8, f"{d['bearing_B']:.2f} +0.05/0")
    v.dim_v(0, d["bearing_B"], -d["bearing_D"] / 2, -8, f"{d['bearing_B']:.2f} +0.05/0")
    v.leader(
        d["bearing_relief_d"] / 2,
        (z["lower_bearing_top"] + z["upper_bearing_bottom"]) / 2,
        175,
        150,
        f"Ø{d['bearing_relief_d']:.2f} RELIEF",
    )
    b = View(sh, 305, 140, 3.0, "BOTTOM VIEW", label_y=200)
    b.circle(0, 0, rf)
    b.circle(0, 0, d["bearing_D"] / 2)
    b.circle(0, 0, d["tube_od"] / 2, "hid")
    a2 = d["mount_pattern"] / 2
    for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
        b.circle(sx * a2, sy * a2, 1.25)
        b.circle(sx * a2, sy * a2, 1.5, "thin")
    half = math.radians(d["base_window_half_angle_deg"])
    for k in range(d["base_windows"]):
        c = 2 * math.pi * k / d["base_windows"]
        ri, ro = d["base_window_r_in"], d["base_window_r_out"]
        arc_o = [
            (ro * math.cos(c - half + 2 * half * t / 16), ro * math.sin(c - half + 2 * half * t / 16))
            for t in range(17)
        ]
        arc_i = [
            (ri * math.cos(c + half - 2 * half * t / 16), ri * math.sin(c + half - 2 * half * t / 16))
            for t in range(17)
        ]
        b.poly(arc_o + arc_i, "vis")
    b.cross(0, 0, rf + 3)
    b.dim_h(-a2, a2, -a2, -10, f"{d['mount_pattern']:.2f} ±0.05")
    b.dim_v(-a2, a2, a2, 12, f"{d['mount_pattern']:.2f} ±0.05")
    b.leader(a2 + 1.0, a2 + 1.0, 345, 85, f"4x M3x0.5-6H DEPTH {d['mount_thread_depth']:.1f} (BLIND)")
    b.leader(
        -ri * 1.05,
        0.5,
        250,
        85,
        f"{d['base_windows']}x WINDOW R{d['base_window_r_in']:.2f}-R{d['base_window_r_out']:.2f}, ±{d['base_window_half_angle_deg']:.0f}° (W1 = LEAD EXIT)",
    )
    sh.notes = [
        "Bearing bores coaxial within 0.01 mm and to stator seat within 0.02 mm; machine both bores in one setup or line-bore.",
        f"Bearings {d['bearing']} retained with Loctite 641 (H6 housing). Stator bonded to seat with Loctite 638 (0.02-0.09 mm clearance).",
        "Mounting threads blind; >= 0.8 mm floor protects end turns from over-length screws. Max screw engagement 2.6 mm.",
        "Window edges break 0.2 mm; lead exit window W1 edges R0.3 min plus silicone grommet sleeve.",
    ]
    return sh


def sheet_shaft(m: MechanicalDesign, mass: float) -> Sheet:
    d, z = m.dims, m.dims["z"]
    sh = Sheet(
        "DWG-008",
        "SHAFT",
        "Bearing steel 52100 / SUJ2",
        "Through hardened 58-62 HRC; journals ground Ra 0.4",
        "5:1",
        mass,
    )
    z0 = z["shaft_bottom"]
    prof = [(r, zz - z0) for r, zz in mech.shaft_profile(m)]
    # draw horizontally: rotate (r, z) -> (z, r)
    v = View(sh, 60, 140, 5.0, None)
    outline = [(zz, r) for r, zz in prof] + [(zz, -r) for r, zz in reversed(prof)]
    v.poly(outline, "vis")
    for r, zz in prof:
        if r > 1e-6:
            v.line(zz, -r, zz, r, "vis")
    v.line(-2, 0, d["shaft_length"] + 2, 0, "ctr")
    L = d["shaft_length"]
    zs, zh = z["shoulder"] - z0, z["hub_top"] - z0
    zg = z["clip_groove"] - z0
    v.dim_h(0, L, d["shaft_upper_d"] / 2, 18, f"{L:.2f} ±0.05")
    v.dim_h(0, zs, d["shaft_d"] / 2, 10, f"{zs:.2f} ±0.02 (SHOULDER)")
    v.dim_h(zh, L, d["shaft_upper_d"] / 2, 10, f"{L - zh:.2f} THREAD M5x0.8-6g")
    v.leader(
        zs * 0.45,
        -d["shaft_d"] / 2,
        60 + 5.0 * zs * 0.45 + 15,
        172,
        f"Ø{_tol(d['shaft_d'], 'shaft_journal', 3)} JOURNAL",
    )
    v.leader(
        (zs + zh) / 2,
        -d["shaft_upper_d"] / 2,
        60 + 5.0 * (zs + zh) / 2 + 20,
        186,
        f"Ø{_tol(d['shaft_upper_d'], 'shaft_upper', 3)} HUB SEAT",
    )
    v.leader(
        zg,
        -d["clip_groove_d"] / 2,
        60 + 5.0 * zg + 10,
        200,
        f"GROOVE Ø{d['clip_groove_d']:.2f} h11 x {d['clip_groove_w']:.2f} +0.05 (DIN 6799 SIZE {d['e_clip']})",
    )
    v.leader(
        z["grub"] - z0,
        d["shaft_upper_d"] / 2 - 0.5,
        60 + 5.0 * (z["grub"] - z0),
        95,
        "FLAT 0.5 DEEP FOR M3 GRUB (LENGTH = HUB)",
    )
    sh.notes = [
        f"Journal Ø{d['shaft_d']:.0f} h5 fits {d['bearing']} inner rings (push fit); runout of all diameters to journal axis 0.005 mm.",
        "Shoulder face perpendicular to journal within 0.005 mm (it locates the upper inner race); undercut/relief R0.1 max.",
        "Chamfer 0.3 x 45 deg both ends; thread M5x0.8-6g rolled or ground after hardening, no decarburisation.",
        "Groove sharp-cornered (R0.05 max) for DIN 6799 e-clip; no burrs.",
    ]
    return sh


PARTS_LIST = [
    ("1", "DWG-007", "Base", 1, "7075-T6, anodized", "CNC: Xometry / PCBWay / JLCCNC"),
    (
        "2",
        "DWG-003",
        "Wound stator (incl. DWG-001/-002)",
        1,
        "Si-steel + AWG23 Cu",
        "Lams: Motorneo / Polaris / Precision Micro; wind in-house",
    ),
    ("3", "684ZZ", "Bearing 4x9x4 (NMB L-940ZZ / EZO)", 2, "ABEC-5+, shielded", "EZO / NMB distributors"),
    ("4", "DWG-008", "Shaft", 1, "52100, 58-62 HRC", "Custom turn+grind (Misumi custom / CNC shop)"),
    ("5", "DIN 6799-3.2", "E-clip", 1, "Spring steel", "Fastener Superstore / McMaster"),
    ("6", "DWG-006", "Bell", 1, "7075-T6, anodized", "CNC: Xometry / PCBWay / JLCCNC"),
    ("7", "DWG-005", "Back-iron ring", 1, "1010 steel", "CNC/tube: same CNC vendor"),
    ("8", "DWG-004-01/-02", "Arc magnet N-in / S-in", 14, "NdFeB, NiCuNi", "K&J (proto) / Bestway / MPCO (volume)"),
    ("9", "ISO 4029 M3x3", "Grub screw, cup point", 1, "Alloy steel 45H", "Accu / McMaster"),
    ("10", "-", "Shim washer 4x7x0.1", 1, "Spring steel", "Misumi / McMaster"),
    ("11", "-", "Lead wire 20 AWG silicone, 150 mm", 3, "200 C, 600 V", "BNTECHGO / hobby suppliers"),
]


def sheet_assembly(m: MechanicalDesign, a: DesignAnalysis, mass_total: float, iso_svg: str | None) -> Sheet:
    d, z = m.dims, m.dims["z"]
    sh = Sheet("DWG-000", f"MOTOR ASSEMBLY {m.name}", "see parts list", "-", "4:1", mass_total)
    v = View(sh, 110, 195, 4.0, "SECTION A-A (THROUGH TOOTH T1 AND MAGNET 1)", label_y=218)
    v.revolved_section(mech.base_profile(m), "metal")
    v.revolved_section(mech.bell_profile(m), "metal2")
    v.revolved_section(mech.ring_profile(m), "steel")
    for zb in (0.0, z["upper_bearing_bottom"]):
        prof = mech.bearing_profile(m, zb)
        for sgn in (1, -1):
            pts = [(sgn * r, zz) for r, zz in prof]
            v.poly(pts, "vis")
            v.line(pts[0][0], pts[0][1], pts[2][0], pts[2][1], "thin")
            v.line(pts[1][0], pts[1][1], pts[3][0], pts[3][1], "thin")
    rs, ri = d["stator_od"] / 2, d["stator_id"] / 2
    r1, r2 = d["slot_bottom_d"] / 2, d["shoe_inner_d"] / 2
    et = d["end_turn_overhang"]
    for sgn in (1, -1):
        v.poly(
            [
                (sgn * ri, z["stator_bottom"]),
                (sgn * rs, z["stator_bottom"]),
                (sgn * rs, z["stator_top"]),
                (sgn * ri, z["stator_top"]),
            ],
            "vis",
            True,
            "lam",
        )
        for zz0, zz1 in ((z["stator_top"], z["stator_top"] + et), (z["stator_bottom"] - et, z["stator_bottom"])):
            v.poly([(sgn * r1, zz0), (sgn * r2, zz0), (sgn * r2, zz1), (sgn * r1, zz1)], "vis", True, "copper")
        v.poly(
            [
                (sgn * d["magnet_ri"], z["mag_bottom"]),
                (sgn * d["magnet_ro"], z["mag_bottom"]),
                (sgn * d["magnet_ro"], z["mag_top"]),
                (sgn * d["magnet_ri"], z["mag_top"]),
            ],
            "vis",
            True,
            "magnet",
        )
    v.revolved_outside(mech.shaft_profile(m))
    v.centerline_v(0, z["shaft_bottom"] - 2, z["shaft_top"] + 2)
    balloons = [
        (1, (d["flange_d"] / 2 - 2, 1.5), (185, 205)),
        (2, (-(r1 + r2) / 2, z["stator_top"] + et / 2), (35, 100)),
        (3, (-d["bearing_D"] / 2 + 1, 2.0), (35, 205)),
        (4, (0.3, z["shaft_top"] - 4), (140, 72)),
        (5, (-d["clip_groove_d"] / 2 - 0.8, z["clip_groove"]), (75, 212)),
        (6, (d["bell_od"] / 2 - 0.3, z["plate_top"] - 1), (185, 105)),
        (7, (d["ring_od"] / 2 - 0.5, z["ring_bottom"] + 2), (185, 180)),
        (8, (-(d["magnet_ri"] + d["magnet_ro"]) / 2, (z["mag_bottom"] + z["mag_top"]) / 2), (35, 170)),
        (9, (d["hub_boss_d"] / 2, z["grub"]), (170, 82)),
    ]
    for n, (x, y), (tx, ty) in balloons:
        v.balloon(x, y, tx, ty, n)
    v.dim_v(0, z["hub_top"], -d["bell_od"] / 2, -12, f"{z['hub_top']:.2f} (BODY)")
    v.dim_v(0, z["shaft_top"], -d["bell_od"] / 2, -24, f"{z['shaft_top']:.2f} (OVERALL)")
    v.dim_h(-d["bell_od"] / 2, d["bell_od"] / 2, z["shaft_bottom"], -10, f"Ø{d['bell_od']:.2f}")
    if iso_svg:
        sh.image_svg(iso_svg, 232, 20, 120, 95)
        sh.text(292, 116, "ISOMETRIC, QUARTER CUT-AWAY", 2.6, "middle", "bold")
    rows = [["ITEM", "PART NO", "DESCRIPTION", "QTY", "MATERIAL / SPEC", "SOURCE (CANDIDATES)"]]
    rows += [[i, p, desc, str(q), mat, src] for i, p, desc, q, mat, src in PARTS_LIST]
    sh.table(196, 124, [10, 26, 50, 9, 33, 86], rows, 4.6, 2.0)
    sh.notes = [
        "Press bearings (3) into base (1) with Loctite 641 on OD only; seat on shoulders with flat anvil, <= 300 N, no load through balls.",
        "Bond stator (2) on tube with Loctite 638; align lead exit to window W1; cure 24 h RT or 15 min 120 C.",
        "Bond magnets (8) into ring (7) with Loctite AA 326 + SF 7649 using alternating-pole indexing fixture; check pole order with compass.",
        "Press ring into bell (6) (H7/s6, Loctite 638). Press shaft (4) into hub, fit grub (9) with Loctite 243 on flat.",
        "Balance rotor (6+7+8+4): residual unbalance <= 11 mg·mm (ISO 21940-11 G2.5 at 36,000 rpm, rotor 16.6 g).",
        "Insert rotor, add shim (10), fit e-clip (5). Axial play 0.02-0.08 mm; rotor turns freely, cogging uniform.",
        "End-of-line test: R line-line, hipot, no-load Kv at 12 V (spec in spec_sheet.md), current, vibration, noise.",
    ]
    return sh


def build_all(m: MechanicalDesign, a: DesignAnalysis, masses: dict, iso_svg: str | None = None) -> list[Sheet]:
    stator_mass = masses["P-002_stator_stack"] * 0.95
    total = masses.get("total_g")
    return [
        sheet_assembly(m, a, total, iso_svg),
        sheet_lamination(m, a, masses["P-001_lamination"]),
        sheet_stator_core(m, a, stator_mass),
        sheet_wound_stator(m, a, stator_mass + a.internals["mass_copper_g"]),
        sheet_magnet(m, masses["P-003_magnet"]),
        sheet_ring(m, masses["P-004_back_iron_ring"]),
        sheet_bell(m, masses["P-005_bell"]),
        sheet_base(m, masses["P-006_base"]),
        sheet_shaft(m, masses["P-007_shaft"]),
    ]


def write_sheets(sheets: list[Sheet], out_dir: str | Path) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    for idx, sheet in enumerate(sheets, start=1):
        path = (
            out
            / f"{sheet.number}_{sheet.title.split(' (')[0].replace(' ', '_').replace(',', '').replace('+', 'and').lower()}.svg"
        )
        path.write_text(sheet.render(idx, len(sheets)), encoding="utf-8")
        paths.append(path)
    return paths


def write_pdf(svg_paths: list[Path], pdf_path: str | Path) -> Path | None:
    """Combine sheets into one A3 PDF with headless Chromium (Playwright). Returns None if unavailable."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return None
    pages = "".join(f'<div class="page">{p.read_text(encoding="utf-8")}</div>' for p in svg_paths)
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><style>
@page {{ size: 420mm 297mm; margin: 0 }} body {{ margin: 0 }}
.page {{ width: 420mm; height: 297mm; page-break-after: always; overflow: hidden }}
.page svg {{ width: 420mm; height: 297mm; display: block }}
</style></head><body>{pages}</body></html>"""
    target = Path(pdf_path)
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.set_content(doc)
        page.pdf(path=str(target), width="420mm", height="297mm", print_background=True)
        browser.close()
    return target
