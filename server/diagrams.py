"""Report track R2: model diagrams for export_report, drawn with reportlab.graphics (no extra dependency).

Pure drawing code: it takes the geometry gathered by model_read (points, frames, areas, grids, stories, loads) and
returns reportlab Drawings. Views: 3-D overview (isometric), plan per story / level, elevation per grid line
(or per frame plane in SAP2000), and one load view per load pattern with the assigned values.
"""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Iterable

TOL = 1e-3
LABEL_CAP = 80          # section / load labels per drawing; more would be unreadable
GREY, DARK, BLUE = "#9a9a9a", "#222222", "#1f5fa8"
SLAB, LOADED_AREA, LOAD, SUPPORT = "#dfe8f3", "#f6d7a7", "#d9480f", "#2b8a3e"

Pt = tuple[float, float, float]


def _c(hexcode: str):
    from reportlab.lib import colors
    return colors.HexColor(hexcode)


def _fmt(v: float) -> str:
    return f"{v:.4g}"


class _View:
    """Fit 2-D points into a box (w x h, with margins), y up; equal scale on both axes."""

    def __init__(self, pts2: Iterable[tuple[float, float]], w: float, h: float, margin: float = 22) -> None:
        pts2 = list(pts2) or [(0.0, 0.0)]
        xs, ys = [p[0] for p in pts2], [p[1] for p in pts2]
        self.x0, self.y0 = min(xs), min(ys)
        dx, dy = max(xs) - self.x0 or 1.0, max(ys) - self.y0 or 1.0
        self.s = min((w - 2 * margin) / dx, (h - 2 * margin) / dy)
        self.ox = margin + ((w - 2 * margin) - dx * self.s) / 2
        self.oy = margin + ((h - 2 * margin) - dy * self.s) / 2

    def __call__(self, x: float, y: float) -> tuple[float, float]:
        return self.ox + (x - self.x0) * self.s, self.oy + (y - self.y0) * self.s


def _iso(p: Pt) -> tuple[float, float]:
    """Isometric-like projection: x to the right-down, y to the right-up, z up."""
    x, y, z = p
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    return (x - y) * c, (x + y) * s + z


def _plan(p: Pt) -> tuple[float, float]:
    return p[0], p[1]


def _drawing(w: float, h: float):
    from reportlab.graphics.shapes import Drawing, Rect
    d = Drawing(w, h)
    d.add(Rect(0, 0, w, h, strokeColor=_c("#cccccc"), strokeWidth=0.4, fillColor=None))
    return d


def _text(d, x: float, y: float, s: str, size: float = 5.5, color: str = DARK, anchor: str = "middle") -> None:
    from reportlab.graphics.shapes import String
    d.add(String(x, y, s, fontName="Helvetica", fontSize=size, fillColor=_c(color), textAnchor=anchor))


def _support(d, x: float, y: float, kind: str) -> None:
    """Triangle for a support; filled for fixed, open for pinned / partial."""
    from reportlab.graphics.shapes import Polygon
    r = 3.2
    d.add(Polygon([x, y, x - r, y - r * 1.5, x + r, y - r * 1.5], strokeColor=_c(SUPPORT), strokeWidth=0.6,
                  fillColor=_c(SUPPORT) if kind == "fixed" else None))


def _legend(d, w: float, items: list[tuple[str, str]]) -> None:
    from reportlab.graphics.shapes import Rect
    x = 6
    for color, label in items:
        d.add(Rect(x, 5, 7, 4, fillColor=_c(color), strokeColor=None))
        _text(d, x + 9, 5, label, 5.5, anchor="start")
        x += 14 + len(label) * 2.9


def _frame_label(f: dict[str, Any]) -> str:
    return f.get("section", "")


# --------------------------------------------------------------------------- views

def overview(geo: dict[str, Any], w: float, h: float):
    from reportlab.graphics.shapes import Line, Polygon
    pts = [_iso(p) for p in _all_xyz(geo)]
    v = _View(pts, w, h)
    d = _drawing(w, h)
    for a in sorted(geo["areas"], key=lambda a: a["z_max"]):
        flat = [c for p in a["coords"] for c in v(*_iso(tuple(p)))]
        d.add(Polygon(flat, fillColor=_c(SLAB), strokeColor=_c(GREY), strokeWidth=0.3))
    for f in geo["frames"]:
        (x1, y1), (x2, y2) = v(*_iso(_p1(f))), v(*_iso(_p2(f)))
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(BLUE), strokeWidth=0.7))
    for p in geo["points"]:
        if p["restraint"]:
            _support(d, *v(*_iso((p["x"], p["y"], p["z"]))), p["restraint"])
    _legend(d, w, [(BLUE, "frames"), (SLAB, "areas"), (SUPPORT, "supports")])
    return d


def plan(geo: dict[str, Any], z: float, w: float, h: float, labels: bool = True):
    """Plan at level z: areas and horizontal frames at z, columns arriving at z (squares), supports at z, grids."""
    from reportlab.graphics.shapes import Line, Polygon, Rect
    areas = [a for a in geo["areas"] if abs(a["z_min"] - z) < TOL and abs(a["z_max"] - z) < TOL]
    beams = [f for f in geo["frames"] if abs(f["z1"] - z) < TOL and abs(f["z2"] - z) < TOL]
    cols = [f for f in geo["frames"] if f["orientation"] != "horizontal" and abs(max(f["z1"], f["z2"]) - z) < TOL]
    sup = [p for p in geo["points"] if p["restraint"] and abs(p["z"] - z) < TOL]
    xy = [(p[0], p[1]) for a in areas for p in a["coords"]] + [(f["x1"], f["y1"]) for f in beams + cols] + \
         [(f["x2"], f["y2"]) for f in beams + cols] + [(p["x"], p["y"]) for p in sup]
    grids = geo.get("grids") or {}
    xy += [(g, y) for g in grids.get("x", {}).values() for y in _span(xy, 1)] + \
          [(x, g) for g in grids.get("y", {}).values() for x in _span(xy, 0)]
    v = _View(xy, w, h, margin=26)
    d = _drawing(w, h)
    _grids(d, v, grids, xy)
    for a in areas:
        flat = [c for p in a["coords"] for c in v(p[0], p[1])]
        d.add(Polygon(flat, fillColor=_c(SLAB), strokeColor=_c(GREY), strokeWidth=0.3))
    n_lab = 0
    for f in beams:
        (x1, y1), (x2, y2) = v(f["x1"], f["y1"]), v(f["x2"], f["y2"])
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(BLUE), strokeWidth=0.8))
        if labels and n_lab < LABEL_CAP and len(beams) <= LABEL_CAP:
            _text(d, (x1 + x2) / 2, (y1 + y2) / 2 + 1.5, _frame_label(f), 4.5, BLUE)
            n_lab += 1
    seen = set()
    for f in cols:
        top = (f["x1"], f["y1"]) if f["z1"] >= f["z2"] else (f["x2"], f["y2"])
        key = (round(top[0], 3), round(top[1], 3))
        if key in seen:
            continue
        seen.add(key)
        x, y = v(*top)
        d.add(Rect(x - 2.2, y - 2.2, 4.4, 4.4, fillColor=_c(DARK), strokeColor=None))
    for p in sup:
        _support(d, *v(p["x"], p["y"]), p["restraint"])
    _legend(d, w, [(BLUE, "beams / horizontal frames"), (DARK, "columns / vertical members"), (SLAB, "areas"),
                   (SUPPORT, "supports")])
    return d


def elevation(geo: dict[str, Any], axis: str, c: float, w: float, h: float, labels: bool = True):
    """Elevation on the plane axis = c ('x' or 'y'): frames and areas lying in the plane, supports, story lines."""
    from reportlab.graphics.shapes import Line, Polygon
    k = 0 if axis == "x" else 1
    hor = 1 - k            # horizontal drawing axis: y for an x-plane, x for a y-plane
    frames = [f for f in geo["frames"] if abs(_p1(f)[k] - c) < TOL and abs(_p2(f)[k] - c) < TOL]
    areas = [a for a in geo["areas"] if all(abs(p[k] - c) < TOL for p in a["coords"])]
    sup = [p for p in geo["points"] if p["restraint"] and abs((p["x"], p["y"])[k] - c) < TOL]
    uv = [(_p1(f)[hor], _p1(f)[2]) for f in frames] + [(_p2(f)[hor], _p2(f)[2]) for f in frames] + \
         [(p[hor], p[2]) for a in areas for p in a["coords"]] + [((p["x"], p["y"])[hor], p["z"]) for p in sup]
    v = _View(uv, w, h, margin=26)
    d = _drawing(w, h)
    lo, hi = _span(uv, 0)
    levels = list(geo.get("stories") or [])
    if geo.get("base_elevation") is not None:
        levels.append({"name": "Base", "elevation": geo["base_elevation"]})
    for s in levels:
        if uv and min(u[1] for u in uv) - TOL <= s["elevation"] <= max(u[1] for u in uv) + TOL:
            (x1, y), (x2, _) = v(lo, s["elevation"]), v(hi, s["elevation"])
            d.add(Line(x1 - 8, y, x2 + 8, y, strokeColor=_c("#c8c8c8"), strokeWidth=0.3, strokeDashArray=[2, 2]))
            _text(d, x2 + 10, y + 1, s["name"], 5, GREY, "start")
    for a in areas:
        flat = [c2 for p in a["coords"] for c2 in v(p[hor], p[2])]
        d.add(Polygon(flat, fillColor=_c(SLAB), strokeColor=_c(GREY), strokeWidth=0.3))
    for f in frames:
        (x1, y1), (x2, y2) = v(_p1(f)[hor], _p1(f)[2]), v(_p2(f)[hor], _p2(f)[2])
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(BLUE), strokeWidth=0.8))
        if labels and len(frames) <= LABEL_CAP:
            _text(d, (x1 + x2) / 2 + 2, (y1 + y2) / 2 + 1.5, _frame_label(f), 4.5, BLUE, "start")
    for p in sup:
        _support(d, *v((p["x"], p["y"])[hor], p["z"]), p["restraint"])
    _legend(d, w, [(BLUE, "frames"), (SLAB, "areas"), (SUPPORT, "supports")])
    return d


def load_view(geo: dict[str, Any], pattern: str, w: float, h: float):
    """One load pattern: loaded areas shaded with their value, loaded frames in orange with w, point loads with
    their force. Plan view when the model is one level (SAFE, a single floor), else isometric."""
    from reportlab.graphics.shapes import Circle, Line, Polygon
    loads = geo["loads"]
    one_level = len({round(z, 3) for z in (p["z"] for p in geo["points"])}) <= 1
    proj = _plan if one_level else _iso
    v = _View([proj(p) for p in _all_xyz(geo)], w, h)
    d = _drawing(w, h)
    area_val = {a: x for a, pat, x in loads["areas"] if pat == pattern}
    frame_val = defaultdict(list)
    for fr, pat, txt in loads["frames"]:
        if pat == pattern:
            frame_val[fr].append(txt)
    point_val = {pt: txt for pt, pat, txt in loads["points"] if pat == pattern}
    labels: list[tuple[float, float, str, str]] = []      # drawn last, so no polygon hides them
    # areas lowest first, so upper floors are drawn over lower ones
    for a in sorted(geo["areas"], key=lambda a: a["z_max"]):
        flat = [c for p in a["coords"] for c in v(*proj(tuple(p)))]
        loaded = a["name"] in area_val
        d.add(Polygon(flat, fillColor=_c(LOADED_AREA if loaded else "#f4f4f4"), strokeColor=_c(GREY), strokeWidth=0.3))
        if loaded:
            labels.append((sum(flat[0::2]) / (len(flat) / 2), sum(flat[1::2]) / (len(flat) / 2), area_val[a["name"]],
                           "middle"))
    for f in geo["frames"]:
        (x1, y1), (x2, y2) = v(*proj(_p1(f))), v(*proj(_p2(f)))
        loaded = f["name"] in frame_val
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(LOAD if loaded else "#b5b5b5"), strokeWidth=1.4 if loaded else 0.4))
        if loaded:
            labels.append(((x1 + x2) / 2, (y1 + y2) / 2 + 2, "; ".join(frame_val[f["name"]])[:40], "middle"))
    for p in geo["points"]:
        x, y = v(*proj((p["x"], p["y"], p["z"])))
        if p["name"] in point_val:
            d.add(Circle(x, y, 1.8, fillColor=_c(LOAD), strokeColor=None))
            d.add(Line(x, y + 2, x, y + 9, strokeColor=_c(LOAD), strokeWidth=0.6))
            labels.append((x + 2, y + 9, point_val[p["name"]], "start"))
        elif p["restraint"]:
            _support(d, x, y, p["restraint"])
    for x, y, txt, anchor in labels[:LABEL_CAP]:
        _text(d, x, y, txt, 5.2, LOAD, anchor)
    _legend(d, w, [(LOADED_AREA, "loaded areas"), (LOAD, "loaded frames / point loads"), (SUPPORT, "supports")])
    return d, (len(area_val), len(frame_val), len(point_val))


# --------------------------------------------------------------------------- selection of views

def plan_levels(geo: dict[str, Any], limit: int = 12) -> list[tuple[str, float, int]]:
    """(name, z, objects) for the plans to draw: stories when known (ETABS / SAFE), else the Z levels that carry
    horizontal frames or areas (SAP2000); the busiest `limit` levels, listed top-down."""
    count: Counter = Counter()
    for f in geo["frames"]:
        if abs(f["z1"] - f["z2"]) < TOL:
            count[round(f["z1"], 3)] += 1
        else:
            count[round(max(f["z1"], f["z2"]), 3)] += 1
    for a in geo["areas"]:
        if abs(a["z_max"] - a["z_min"]) < TOL:
            count[round(a["z_min"], 3)] += 1
    for p in geo["points"]:
        if p["restraint"]:
            count[round(p["z"], 3)] += 1
    names = {round(s["elevation"], 3): s["name"] for s in geo.get("stories") or []}
    if geo.get("base_elevation") is not None:
        names.setdefault(round(geo["base_elevation"], 3), "Base")
    levels = [(names.get(z, f"Z = {_fmt(z)}"), z, n) for z, n in count.items() if n]
    levels = sorted(sorted(levels, key=lambda t: -t[2])[:limit], key=lambda t: -t[1])
    return levels


def elevation_planes(geo: dict[str, Any], limit: int = 12) -> list[tuple[str, str, float, int]]:
    """(title, axis, ordinate, frames) for elevations: grid lines when known, else the X / Y planes that hold at
    least two non-horizontal frames; the busiest `limit`. Single-level models get none."""
    if len({round(p["z"], 3) for p in geo["points"]}) <= 1:
        return []
    grids = geo.get("grids") or {}
    cands: list[tuple[str, str, float]] = []
    if grids.get("x") or grids.get("y"):
        cands = [(f"Grid {gid} (X = {_fmt(c)})", "x", c) for gid, c in grids.get("x", {}).items()] + \
                [(f"Grid {gid} (Y = {_fmt(c)})", "y", c) for gid, c in grids.get("y", {}).items()]
    else:
        for k, axis in ((0, "x"), (1, "y")):
            vals = Counter()
            for f in geo["frames"]:
                if abs(_p1(f)[k] - _p2(f)[k]) < TOL:
                    vals[round(_p1(f)[k], 3)] += 1
            cands += [(f"{axis.upper()} = {_fmt(c)}", axis, c) for c, n in vals.items() if n >= 2]
    out = []
    for title, axis, c in cands:
        k = 0 if axis == "x" else 1
        inplane = [f for f in geo["frames"] if abs(_p1(f)[k] - c) < TOL and abs(_p2(f)[k] - c) < TOL]
        n = sum(1 for f in inplane if f["orientation"] != "horizontal")
        across = {round(q[1 - k], 3) for f in inplane for q in (_p1(f), _p2(f))}
        if n and len(across) > 1:          # a lone column line is not worth a page
            out.append((title, axis, c, n))
    return sorted(out, key=lambda t: -t[3])[:limit]


# --------------------------------------------------------------------------- helpers

def _p1(f: dict[str, Any]) -> Pt:
    return f["x1"], f["y1"], f["z1"]


def _p2(f: dict[str, Any]) -> Pt:
    return f["x2"], f["y2"], f["z2"]


def _all_xyz(geo: dict[str, Any]) -> list[Pt]:
    pts = [(p["x"], p["y"], p["z"]) for p in geo["points"]]
    return pts or [(0.0, 0.0, 0.0)]


def _span(pts: list[tuple[float, float]], k: int) -> tuple[float, float]:
    vals = [p[k] for p in pts]
    return (min(vals), max(vals)) if vals else (0.0, 1.0)


def _grids(d, v: _View, grids: dict[str, dict[str, float]], xy: list[tuple[float, float]]) -> None:
    from reportlab.graphics.shapes import Circle, Line
    if not grids:
        return
    (xlo, xhi), (ylo, yhi) = _span(xy, 0), _span(xy, 1)
    for gid, x in grids.get("x", {}).items():
        (a, b), (_, c) = v(x, ylo), v(x, yhi)
        d.add(Line(a, b - 8, a, c + 8, strokeColor=_c("#c8c8c8"), strokeWidth=0.3, strokeDashArray=[3, 2]))
        d.add(Circle(a, c + 13, 5, strokeColor=_c(GREY), strokeWidth=0.4, fillColor=None))
        _text(d, a, c + 11, gid, 5.5, DARK)
    for gid, y in grids.get("y", {}).items():
        (a, b), (c, _) = v(xlo, y), v(xhi, y)
        d.add(Line(a - 8, b, c + 8, b, strokeColor=_c("#c8c8c8"), strokeWidth=0.3, strokeDashArray=[3, 2]))
        d.add(Circle(a - 13, b, 5, strokeColor=_c(GREY), strokeWidth=0.4, fillColor=None))
        _text(d, a - 13, b - 2, gid, 5.5, DARK)


# =========================================================================== R3: result diagrams

DEF, UNDEF, NEG, POS = "#1f5fa8", "#c4c4c4", "#c92a2a", "#1971c2"


def _scale_note(d, w: float, text: str) -> None:
    _text(d, w - 6, 5, text, 5.5, GREY, "end")


def deformed(geo: dict[str, Any], disp: dict[str, tuple[float, float, float]], w: float, h: float,
             one_level: bool = False):
    """Undeformed (grey) and deformed (blue) shape. Displacements scaled so the largest is 8 % of the model size."""
    from reportlab.graphics.shapes import Line, Polygon
    pts = {p["name"]: (p["x"], p["y"], p["z"]) for p in geo["points"]}
    proj = _iso                     # slabs too: the 3-D view shows the deflected surface
    size = max((max(c) - min(c) for c in zip(*pts.values())), default=1.0) if pts else 1.0
    umax = max((math.sqrt(sum(u * u for u in v)) for v in disp.values()), default=0.0)
    k = 0.08 * size / umax if umax > 0 else 0.0

    def moved(name: str) -> Pt:
        x, y, z = pts[name]
        ux, uy, uz = disp.get(name, (0.0, 0.0, 0.0))
        return x + k * ux, y + k * uy, z + k * uz

    v = _View([proj(p) for p in pts.values()] + [proj(moved(n)) for n in pts], w, h)
    d = _drawing(w, h)
    for a in geo["areas"]:
        names = a["points"].split()
        if all(n in pts for n in names):
            d.add(Polygon([c for n in names for c in v(*proj(pts[n]))], fillColor=None, strokeColor=_c(UNDEF),
                          strokeWidth=0.3))
            d.add(Polygon([c for n in names for c in v(*proj(moved(n)))], fillColor=_c("#e7eef7"),
                          strokeColor=_c(DEF), strokeWidth=0.4))
    for f in geo["frames"]:
        a, b = f["point1"], f["point2"]
        if a in pts and b in pts:
            (x1, y1), (x2, y2) = v(*proj(pts[a])), v(*proj(pts[b]))
            d.add(Line(x1, y1, x2, y2, strokeColor=_c(UNDEF), strokeWidth=0.4))
            (x1, y1), (x2, y2) = v(*proj(moved(a))), v(*proj(moved(b)))
            d.add(Line(x1, y1, x2, y2, strokeColor=_c(DEF), strokeWidth=0.9))
    if disp:
        worst = max(disp, key=lambda n: math.sqrt(sum(u * u for u in disp[n])))
        if worst in pts:
            x, y = v(*proj(moved(worst)))
            u = disp[worst]
            _text(d, x + 3, y + 3, f"joint {worst}: |u| {_fmt(math.sqrt(sum(c * c for c in u)))}", 5.5, NEG, "start")
    for p in geo["points"]:
        if p["restraint"]:
            _support(d, *v(*proj((p["x"], p["y"], p["z"]))), p["restraint"])
    _legend(d, w, [(UNDEF, "undeformed"), (DEF, "deformed"), (SUPPORT, "supports")])
    _scale_note(d, w, f"displacement scale x{_fmt(k)}" if k else "no displacement")
    return d, umax


def reactions(geo: dict[str, Any], react: dict[str, tuple[float, ...]], w: float, h: float):
    """Support reactions: vertical reaction F3 written at each support (plan view)."""
    from reportlab.graphics.shapes import Line, Polygon
    sup = [p for p in geo["points"] if p["name"] in react]
    xy = [(p["x"], p["y"]) for p in geo["points"]]
    v = _View(xy, w, h, margin=30)
    d = _drawing(w, h)
    _grids(d, v, geo.get("grids") or {}, xy)
    for a in geo["areas"]:
        d.add(Polygon([c for p in a["coords"] for c in v(p[0], p[1])], fillColor=_c("#f2f2f2"),
                      strokeColor=_c(UNDEF), strokeWidth=0.3))
    for f in geo["frames"]:
        (x1, y1), (x2, y2) = v(f["x1"], f["y1"]), v(f["x2"], f["y2"])
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(UNDEF), strokeWidth=0.4))
    for p in sup[:LABEL_CAP * 3]:
        x, y = v(p["x"], p["y"])
        f3 = react[p["name"]][2]
        d.add(Polygon([x, y, x - 2.5, y - 4, x + 2.5, y - 4], fillColor=_c(SUPPORT), strokeColor=None))
        if len(sup) <= LABEL_CAP * 3:
            _text(d, x, y + 3, _fmt(f3), 5, NEG if f3 < 0 else DARK)
    _legend(d, w, [(SUPPORT, "supports, F3 (vertical reaction) written above")])
    return d


def default_axes(p1: Pt, p2: Pt) -> tuple[Pt, Pt, Pt]:
    """CSI default frame local axes (angle 0): 1 along the member; 2 vertical-up in the plane of 1 and Z (vertical
    members: 2 = +X); 3 = 1 x 2."""
    d = [b - a for a, b in zip(p1, p2)]
    L = math.sqrt(sum(c * c for c in d)) or 1.0
    e1 = tuple(c / L for c in d)
    if math.hypot(e1[0], e1[1]) < 1e-6:
        e2 = (1.0, 0.0, 0.0)
    else:
        up = (0.0, 0.0, 1.0)
        dot = sum(a * b for a, b in zip(up, e1))
        v = tuple(u - dot * e for u, e in zip(up, e1))
        n = math.sqrt(sum(c * c for c in v)) or 1.0
        e2 = tuple(c / n for c in v)
    e3 = (e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0])
    return e1, e2, e3


def frame_diagram(geo: dict[str, Any], forces: dict[str, list[tuple[float, dict[str, float]]]], axis: str, c: float,
                  w: float, h: float, comp: str = "bending", axes: dict[str, tuple[Pt, Pt, Pt]] | None = None):
    """Force diagram of the frames in the plane axis = c, drawn perpendicular to each member.
    comp 'bending' = the moment that bends each member in this plane (M3 when local 3 is normal to the plane, M2
    when local 2 is), drawn toward the member's local -2 (M3) or -3 (M2) side: for a beam with local 2 up, a
    positive (sagging) M3 plots below the beam. Other components are drawn toward local -2 (P, V2, T, M3) or -3
    (V3, M2) when that axis lies in the plane. Scaled so the largest value is 10 % of the plane size."""
    from reportlab.graphics.shapes import Line, Polygon
    k = 0 if axis == "x" else 1
    hor = 1 - k
    normal = (1.0, 0.0, 0.0) if axis == "x" else (0.0, 1.0, 0.0)
    frames = [f for f in geo["frames"] if abs(_p1(f)[k] - c) < TOL and abs(_p2(f)[k] - c) < TOL]
    uv = [(_p1(f)[hor], _p1(f)[2]) for f in frames] + [(_p2(f)[hor], _p2(f)[2]) for f in frames]
    plan_w = max(_span(uv, 0)[1] - _span(uv, 0)[0], _span(uv, 1)[1] - _span(uv, 1)[0], 1e-9)

    def pick(f: dict[str, Any]) -> tuple[str, tuple[float, float]] | None:
        e1, e2, e3 = (axes or {}).get(f["name"]) or default_axes(_p1(f), _p2(f))
        dot = lambda a: abs(sum(x * y for x, y in zip(a, normal)))  # noqa: E731
        if comp == "bending":
            if dot(e3) > 0.7:
                name, side = "M3", e2
            elif dot(e2) > 0.7:
                name, side = "M2", e3
            else:
                return None
        else:
            name, side = comp, (e3 if comp in ("V3", "M2") else e2)
            if dot(side) > 0.7:           # that axis points out of the plane
                side = e3 if side is e2 else e2
        return name, (-side[hor], -side[2])

    chosen = {f["name"]: pick(f) for f in frames}
    vals = [st[1].get(chosen[f["name"]][0], 0.0) for f in frames if chosen[f["name"]]
            for st in forces.get(f["name"], [])]
    vmax = max((abs(x) for x in vals), default=0.0)
    kscale = 0.10 * plan_w / vmax if vmax > 0 else 0.0
    # make room for the diagrams: extend the view by the largest offset
    pad = 0.10 * plan_w
    box = uv + [(u + du, z + dz) for u, z in uv for du, dz in ((pad, pad), (-pad, -pad))]
    v = _View(box, w, h, margin=26)
    d = _drawing(w, h)
    labels, used = 0, set()
    for f in frames:
        a, b = (_p1(f)[hor], _p1(f)[2]), (_p2(f)[hor], _p2(f)[2])
        (x1, y1), (x2, y2) = v(*a), v(*b)
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(DARK), strokeWidth=0.6))
        pk = chosen[f["name"]]
        st = sorted(forces.get(f["name"], []), key=lambda q: q[0])
        if not pk or not st or not kscale:
            continue
        name, (nx, ny) = pk
        used.add(name)
        L = math.dist(a, b) or 1.0
        series = [(s_, vals_.get(name, 0.0)) for s_, vals_ in st]
        at = lambda s_, val: (a[0] + (b[0] - a[0]) * min(max(s_ / L, 0.0), 1.0) + nx * val * kscale,  # noqa: E731
                              a[1] + (b[1] - a[1]) * min(max(s_ / L, 0.0), 1.0) + ny * val * kscale)
        poly = [*v(*a)] + [c_ for s_, val in series for c_ in v(*at(s_, val))] + [*v(*b)]
        peak = max(series, key=lambda q: abs(q[1]))
        d.add(Polygon(poly, fillColor=_c("#fde2e2" if peak[1] < 0 else "#dbe8fb"),
                      strokeColor=_c(NEG if peak[1] < 0 else POS), strokeWidth=0.5))
        if labels < LABEL_CAP:
            for s_, val in {series[0], series[-1], peak}:
                if abs(val) < 1e-6 * max(vmax, 1.0):
                    continue
                x, y = v(*at(s_, val))
                _text(d, x, y + 1.5, _fmt(val), 4.6, NEG if val < 0 else POS)
                labels += 1
    what = "/".join(sorted(used)) or comp
    _legend(d, w, [(POS, f"{what} positive"), (NEG, f"{what} negative")])
    _scale_note(d, w, f"largest |{what}| {_fmt(vmax)}")
    return d, vmax, what


def shell_contour(elements: list[tuple[list[tuple[float, float]], float]], comp: str, w: float, h: float):
    """Element-by-element colour map (blue = most negative, red = most positive) with a colour bar."""
    from reportlab.graphics.shapes import Polygon, Rect
    if not elements:
        return _drawing(w, h), (0.0, 0.0)
    v = _View([p for poly, _ in elements for p in poly], w, h - 18, margin=24)
    d = _drawing(w, h)
    vals = [val for _, val in elements]
    lo, hi = min(vals), max(vals)
    for poly, val in elements:
        d.add(Polygon([c for p in poly for c in v(*p)], fillColor=_ramp(val, lo, hi), strokeColor=_c("#ffffff"),
                      strokeWidth=0.15))
    bx, by, bw = 10, h - 14, min(200, w - 20)
    for i in range(40):
        d.add(Rect(bx + i * bw / 40, by, bw / 40 + 0.3, 6, fillColor=_ramp(lo + (hi - lo) * i / 39, lo, hi),
                   strokeColor=None))
    _text(d, bx, by - 6, _fmt(lo), 5.5, DARK, "start")
    _text(d, bx + bw, by - 6, _fmt(hi), 5.5, DARK, "end")
    _text(d, bx + bw + 6, by, comp + " (average of element nodes)", 5.5, DARK, "start")
    return d, (lo, hi)


def _ramp(val: float, lo: float, hi: float):
    from reportlab.lib import colors
    t = 0.5 if hi - lo < 1e-12 else (val - lo) / (hi - lo)
    stops = [(0.0, (0.13, 0.30, 0.75)), (0.25, (0.30, 0.65, 0.95)), (0.5, (0.55, 0.85, 0.45)),
             (0.75, (0.98, 0.80, 0.25)), (1.0, (0.85, 0.18, 0.15))]
    for (t0, c0), (t1, c1) in zip(stops, stops[1:]):
        if t <= t1:
            f = (t - t0) / (t1 - t0)
            return colors.Color(*(a + (b - a) * f for a, b in zip(c0, c1)))
    return colors.Color(*stops[-1][1])


def story_chart(rows: list[dict[str, Any]], value: str, title: str, w: float, h: float, limit: float | None = None):
    """Profile by story: value per story for each direction / case (rows: Story, Elevation, Series, value)."""
    from reportlab.graphics.shapes import Line, PolyLine
    d = _drawing(w, h)
    if not rows:
        return d
    stories = sorted({(r["Elevation"], r["Story"]) for r in rows})
    elev = {s: e for e, s in stories}
    series: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for r in rows:
        series[r["Series"]].append((r[value], elev[r["Story"]]))
    xmax = max([abs(x) for pts in series.values() for x, _ in pts] + ([limit] if limit else [])) or 1.0
    x0, y0, x1, y1 = 70, 26, w - 20, h - 22
    e_lo, e_hi = stories[0][0], stories[-1][0]
    ys = lambda e: y0 + (e - e_lo) / ((e_hi - e_lo) or 1.0) * (y1 - y0)  # noqa: E731
    xs = lambda x: x0 + x / xmax * (x1 - x0)  # noqa: E731
    d.add(Line(x0, y0, x0, y1, strokeColor=_c(DARK), strokeWidth=0.5))
    d.add(Line(x0, y0, x1, y0, strokeColor=_c(DARK), strokeWidth=0.5))
    for e, s in stories:
        d.add(Line(x0, ys(e), x1, ys(e), strokeColor=_c("#eeeeee"), strokeWidth=0.3))
        _text(d, x0 - 4, ys(e) - 2, s, 5.5, DARK, "end")
    for i in range(5):
        _text(d, xs(xmax * i / 4), y0 - 9, _fmt(xmax * i / 4), 5, GREY)
    palette = [BLUE, NEG, "#2b8a3e", "#e67700", "#7048e8", "#0c8599"]
    for i, (name, pts) in enumerate(sorted(series.items())):
        pts = sorted(pts, key=lambda q: q[1])
        d.add(PolyLine([c for x, e in pts for c in (xs(abs(x)), ys(e))], strokeColor=_c(palette[i % 6]),
                       strokeWidth=1.0))
        _text(d, x1 - 2, y1 + 6 - 8 * i, name, 5.5, palette[i % 6], "end")
    if limit:
        d.add(Line(xs(limit), y0, xs(limit), y1, strokeColor=_c(NEG), strokeWidth=0.6, strokeDashArray=[3, 2]))
        _text(d, xs(limit), y1 + 3, f"limit {_fmt(limit)}", 5.5, NEG)
    _text(d, (x0 + x1) / 2, 6, title, 6, DARK)
    return d


# --------------------------------------------------------------------------- design (Phase 6, report stage R5)

RATIO_BANDS = ((0.5, "#1f5fa8", "< 0.50"), (0.7, "#2b8a3e", "0.50 - 0.70"), (0.9, "#e6b800", "0.70 - 0.90"),
               (1.0, "#e67700", "0.90 - 1.00"), (float("inf"), "#d9480f", "> 1.00"))


def ratio_color(r: float) -> str:
    return next(c for lim, c, _ in RATIO_BANDS if r < lim or lim == float("inf"))


def _ratio_legend(d, w: float) -> None:
    _legend(d, w, [(c, lab) for _, c, lab in RATIO_BANDS] + [(GREY, "not designed")])


def ratio_overview(geo: dict[str, Any], ratios: dict[str, float], w: float, h: float):
    """3-D view, every frame coloured by its governing D/C ratio (grey: no ratio)."""
    from reportlab.graphics.shapes import Line, Polygon
    pts = [_iso(p) for p in _all_xyz(geo)]
    v = _View(pts, w, h)
    d = _drawing(w, h)
    for a in sorted(geo["areas"], key=lambda a: a["z_max"]):
        flat = [c for p in a["coords"] for c in v(*_iso(tuple(p)))]
        d.add(Polygon(flat, fillColor=_c("#f2f2f2"), strokeColor=_c("#dddddd"), strokeWidth=0.2))
    for f in sorted(geo["frames"], key=lambda f: ratios.get(f["name"], -1)):
        r = ratios.get(f["name"])
        (x1, y1), (x2, y2) = v(*_iso(_p1(f))), v(*_iso(_p2(f)))
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(GREY if r is None else ratio_color(r)),
                   strokeWidth=0.5 if r is None else (1.8 if r > 1.0 else 1.2)))
    _ratio_legend(d, w)
    return d


def ratio_elevation(geo: dict[str, Any], ratios: dict[str, float], axis: str, c: float, w: float, h: float):
    """Elevation on the plane axis = c with frames coloured by D/C and labelled with the ratio."""
    from reportlab.graphics.shapes import Line
    k = 0 if axis == "x" else 1
    hor = 1 - k
    frames = [f for f in geo["frames"] if abs(_p1(f)[k] - c) < TOL and abs(_p2(f)[k] - c) < TOL]
    uv = [(_p1(f)[hor], _p1(f)[2]) for f in frames] + [(_p2(f)[hor], _p2(f)[2]) for f in frames]
    v = _View(uv, w, h, margin=26)
    d = _drawing(w, h)
    labels = len(frames) <= LABEL_CAP * 2
    for f in frames:
        r = ratios.get(f["name"])
        (x1, y1), (x2, y2) = v(_p1(f)[hor], _p1(f)[2]), v(_p2(f)[hor], _p2(f)[2])
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(GREY if r is None else ratio_color(r)),
                   strokeWidth=0.6 if r is None else 1.6))
        if labels and r is not None:
            _text(d, (x1 + x2) / 2 + 2, (y1 + y2) / 2 + 2, f"{r:.2f}", 4.8, DARK, "start")
    _ratio_legend(d, w)
    return d


def ratio_bars(ratios: dict[str, float], w: float, h: float, top: int = 25, title: str = "governing D/C ratio"):
    """Horizontal bars of the highest ratios, with the 1.0 line."""
    from reportlab.graphics.shapes import Line, Rect
    d = _drawing(w, h)
    items = sorted(ratios.items(), key=lambda kv: -kv[1])[:top]
    if not items:
        return d
    x0, x1, y0, y1 = 60, w - 30, 24, h - 18
    rmax = max(1.1, max(r for _, r in items) * 1.05)
    xs = lambda r: x0 + r / rmax * (x1 - x0)  # noqa: E731
    bh = (y1 - y0) / len(items)
    for i, (name, r) in enumerate(items):
        y = y1 - (i + 1) * bh
        d.add(Rect(x0, y + bh * 0.15, xs(r) - x0, bh * 0.7, fillColor=_c(ratio_color(r)), strokeColor=None))
        _text(d, x0 - 3, y + bh * 0.3, str(name), 5, DARK, "end")
        _text(d, xs(r) + 2, y + bh * 0.3, f"{r:.3f}", 5, DARK, "start")
    d.add(Line(xs(1.0), y0, xs(1.0), y1, strokeColor=_c("#d9480f"), strokeWidth=0.7, strokeDashArray=[3, 2]))
    _text(d, xs(1.0), y0 - 9, "1.0", 5.5, "#d9480f")
    _text(d, (x0 + x1) / 2, 6, title, 6, DARK)
    return d


def ratio_histogram(ratios: dict[str, float], w: float, h: float):
    """Members per D/C band."""
    from reportlab.graphics.shapes import Rect
    d = _drawing(w, h)
    counts = Counter(next(lab for lim, _, lab in RATIO_BANDS if r < lim or lim == float("inf"))
                     for r in ratios.values())
    bands = [(lab, c) for _, c, lab in RATIO_BANDS]
    nmax = max(counts.values(), default=1)
    x0, x1, y0, y1 = 30, w - 20, 28, h - 18
    bw = (x1 - x0) / len(bands)
    for i, (lab, col) in enumerate(bands):
        n = counts.get(lab, 0)
        hh = (y1 - y0) * n / nmax if nmax else 0
        d.add(Rect(x0 + i * bw + bw * 0.15, y0, bw * 0.7, hh, fillColor=_c(col), strokeColor=None))
        _text(d, x0 + i * bw + bw / 2, y0 + hh + 3, str(n), 6, DARK)
        _text(d, x0 + i * bw + bw / 2, y0 - 10, lab, 5.5, DARK)
    _text(d, (x0 + x1) / 2, 6, "members per D/C band", 6, DARK)
    return d
