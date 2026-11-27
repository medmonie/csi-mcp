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

    def __init__(self, pts2: Iterable[tuple[float, float]], w: float, h: float, margin: float = 22,
                 y0: float = 0.0) -> None:
        pts2 = list(pts2) or [(0.0, 0.0)]
        xs, ys = [p[0] for p in pts2], [p[1] for p in pts2]
        self.x0, self.y0 = min(xs), min(ys)
        dx, dy = max(xs) - self.x0 or 1.0, max(ys) - self.y0 or 1.0
        self.s = min((w - 2 * margin) / dx, (h - 2 * margin) / dy)
        self.ox = margin + ((w - 2 * margin) - dx * self.s) / 2
        self.oy = y0 + margin + ((h - 2 * margin) - dy * self.s) / 2

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


FILL_POS, FILL_NEG = "#c5d8f6", "#f6c9c9"      # the programs: positive blue, negative red


def _dense(series: list[tuple[float, float]], slopes: list[float] | None, k: int = 8) -> list[tuple[float, float]]:
    """Points along a member: straight between stations, or - for a moment with the matching shear - a cubic
    Hermite curve with dM/dx from the shear (exact for the parabola of a uniform load). A segment whose chord does
    not agree with its end slopes (a point load at a station) stays straight."""
    out = [series[0]]
    for i in range(len(series) - 1):
        (s0, m0), (s1, m1) = series[i], series[i + 1]
        L = s1 - s0
        if slopes is None or L <= 0:
            out.append(series[i + 1])
            continue
        d0, d1 = slopes[i] * L, slopes[i + 1] * L
        chord = m1 - m0
        if abs(chord - (d0 + d1) / 2) > 0.02 * max(abs(m0), abs(m1), abs(d0), abs(d1), 1e-9):
            out.append(series[i + 1])
            continue
        for j in range(1, k + 1):
            t = j / k
            h00, h10, h01, h11 = 2*t**3 - 3*t**2 + 1, t**3 - 2*t**2 + t, -2*t**3 + 3*t**2, t**3 - t**2
            out.append((s0 + t * L, h00 * m0 + h10 * d0 + h01 * m1 + h11 * d1))
    return out


def _slope_sign(series: list[tuple[float, float]], shear: list[float]) -> float | None:
    """dM/dx = sign x V (CSI's sign convention differs between M3/V2 and M2/V3): the sign that fits the chords."""
    num = sum((m1 - m0) * (v0 + v1) / 2 for (_, m0), (_, m1), v0, v1 in zip(series, series[1:], shear, shear[1:]))
    if abs(num) < 1e-12:
        return None
    return 1.0 if num > 0 else -1.0


def _sign_runs(pts: list[tuple[float, float]]) -> list[list[tuple[float, float]]]:
    """Split a diagram at its zero crossings (also at exact zeros): runs of one sign, each closed on the member."""
    sgn = lambda v: (v > 0) - (v < 0)  # noqa: E731
    runs: list[list[tuple[float, float]]] = []
    cur, cur_s = [(pts[0][0], 0.0)], 0
    prev = pts[0]
    for s1, v1 in pts:
        s_new = sgn(v1)
        if cur_s and s_new and s_new != cur_s:
            s0, v0 = prev
            sz = s0 + (s1 - s0) * v0 / (v0 - v1) if v0 != v1 and v0 else s0
            cur.append((sz, 0.0))
            runs.append(cur)
            cur = [(sz, 0.0)]
        if s_new:
            cur_s = s_new
        cur.append((s1, v1))
        prev = (s1, v1)
    cur.append((pts[-1][0], 0.0))
    runs.append(cur)
    return [r for r in runs if max(abs(v) for _, v in r) > 0]


ETABS_LINE, ETABS_TEXT = "#c8a200", "#6b5600"   # ETABS / SAFE draw frame diagrams in yellow lines, unfilled


def _interp(run: list[tuple[float, float]], s: float) -> float:
    for (s0, v0), (s1, v1) in zip(run, run[1:]):
        if s0 - 1e-12 <= s <= s1 + 1e-12:
            return v0 if s1 - s0 < 1e-12 else v0 + (v1 - v0) * (s - s0) / (s1 - s0)
    return run[-1][1] if s > run[-1][0] else run[0][1]


def frame_diagram(geo: dict[str, Any], forces: dict[str, list[tuple[float, dict[str, float]]]], axis: str, c: float,
                  w: float, h: float, comp: str = "bending", axes: dict[str, tuple[Pt, Pt, Pt]] | None = None,
                  style: str = "sap"):
    """Force diagram of the frames in the plane axis = c, drawn as the programs draw it: perpendicular to each
    member, split at the zero crossings, positive parts blue and negative parts red, the moment curved between the
    stations from the shear. comp 'bending' = the moment that bends each member in this plane (M3 when local 3 is
    normal to the plane, M2 when local 2 is), drawn on the member's local -2 (M3) or -3 (M2) side: the tension side
    (a positive, sagging M3 of a beam with local 2 up plots below it). Scaled so the largest value is 10 % of the
    plane size; values at the member ends and at the peak of each part. style 'sap' = SAP2000's look (filled,
    positive blue / negative red); 'etabs' = ETABS / SAFE's default look (one colour, not filled, hatched with
    lines across the diagram)."""
    from reportlab.graphics.shapes import Line, Polygon, PolyLine
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
    pad = 0.10 * plan_w
    box = uv + [(u + du, z + dz) for u, z in uv for du, dz in ((pad, pad), (-pad, -pad))]
    v = _View(box, w, h, margin=26)
    d = _drawing(w, h)
    labels, used = 0, set()
    tiny = 1e-6 * max(vmax, 1.0)
    for f in frames:
        a, b = (_p1(f)[hor], _p1(f)[2]), (_p2(f)[hor], _p2(f)[2])
        pk = chosen[f["name"]]
        st = sorted(forces.get(f["name"], []), key=lambda q: q[0])
        if pk and st and kscale:
            name, (nx, ny) = pk
            used.add(name)
            L = math.dist(a, b) or 1.0
            series = [(s_, vals_.get(name, 0.0)) for s_, vals_ in st]
            slopes = None
            if name in ("M3", "M2") and len(series) > 1:
                shear = [vals_.get("V2" if name == "M3" else "V3", 0.0) for _, vals_ in st]
                sgn = _slope_sign(series, shear)
                slopes = [sgn * x for x in shear] if sgn else None
            pts = _dense(series, slopes)
            at = lambda s_, val: (a[0] + (b[0] - a[0]) * min(max(s_ / L, 0.0), 1.0) + nx * val * kscale,  # noqa: E731
                                  a[1] + (b[1] - a[1]) * min(max(s_ / L, 0.0), 1.0) + ny * val * kscale)
            etabs = style == "etabs"
            lab = lambda val: ETABS_TEXT if etabs else (POS if val > 0 else NEG)  # noqa: E731
            for run in _sign_runs(pts):
                pos = max(run, key=lambda q: abs(q[1]))[1] > 0
                if etabs:
                    n_h = max(int(12 * (run[-1][0] - run[0][0]) / L), 2)
                    for i in range(n_h + 1):
                        s_ = run[0][0] + (run[-1][0] - run[0][0]) * i / n_h
                        val = _interp(run, s_)
                        if abs(val) > tiny:
                            d.add(Line(*v(*at(s_, 0.0)), *v(*at(s_, val)), strokeColor=_c(ETABS_LINE),
                                       strokeWidth=0.35))
                else:
                    d.add(Polygon([c_ for s_, val in run for c_ in v(*at(s_, val))],
                                  fillColor=_c(FILL_POS if pos else FILL_NEG), strokeColor=None))
                d.add(PolyLine([c_ for s_, val in (run if etabs else run[1:-1]) for c_ in v(*at(s_, val))],
                               strokeColor=_c(ETABS_LINE if etabs else POS if pos else NEG), strokeWidth=0.6))
                if labels < LABEL_CAP:
                    peak = max(run, key=lambda q: abs(q[1]))
                    x, y = v(*at(*peak))
                    _text(d, x, y + 1.5, _fmt(peak[1]), 4.6, lab(peak[1]))
                    labels += 1
            for s_, val in (series[0], series[-1]):
                if labels < LABEL_CAP and abs(val) > tiny and abs(val) < 0.98 * max(abs(q[1]) for q in pts):
                    x, y = v(*at(s_, val))
                    _text(d, x, y + 1.5, _fmt(val), 4.6, lab(val))
                    labels += 1
        (x1, y1), (x2, y2) = v(*a), v(*b)
        d.add(Line(x1, y1, x2, y2, strokeColor=_c(DARK), strokeWidth=0.6))
    what = "/".join(sorted(used)) or comp
    if style == "etabs":
        _legend(d, w, [(ETABS_LINE, f"{what} (on the tension side for moments)")])
    else:
        _legend(d, w, [(POS, f"{what} positive"), (NEG, f"{what} negative")])
    _scale_note(d, w, f"largest |{what}| {_fmt(vmax)}")
    return d, vmax, what


# The programs' contour colours, most positive band first (read off SAP2000 27's colour bar: dark blue, blue, light
# blue, cyan, greens, yellow, orange, reds, purple for the most negative band)
CSI_BANDS = ["#0000c8", "#0000ff", "#3c78ff", "#00ffff", "#00ff00", "#00c800", "#96f000", "#ffff00", "#ffc800",
             "#ff9600", "#ff6400", "#ff0000", "#e10000", "#be0028", "#a000a0"]


def contour_levels(lo: float, hi: float, target: int = 14) -> list[float]:
    """Band boundaries as the programs label them: multiples of a round step (about (hi - lo) / 14), inside
    [lo, hi], largest first. SAP2000 showed 14 / 10.5 / ... / -31.5 for M11 from -33.7 to 16.44."""
    if hi - lo < 1e-12:
        return [hi]
    raw = (hi - lo) / target
    mag = 10 ** math.floor(math.log10(raw))
    step = min((m * mag for m in (1, 1.2, 1.5, 2, 2.5, 3, 3.5, 4, 5, 6, 7, 8, 10)), key=lambda x: abs(x - raw))
    top, bot = math.floor(hi / step + 1e-9), math.ceil(lo / step - 1e-9)
    return [round(i * step, 10) for i in range(top, bot - 1, -1)]


# ETABS / SAFE colour bar, most negative first (read off ETABS 23 and SAFE 23): purple, reds, oranges, yellow just
# below zero, green just above zero, light greens, cyan, blues; the scale is symmetric about zero
ETABS_NEG = ["#9600b4", "#c80032", "#ff0000", "#ff4600", "#ff8200", "#ffbe00", "#ffff00"]
ETABS_POS = ["#00dc00", "#46f000", "#96ff32", "#00ffaa", "#00ffff", "#3c8cff", "#0000ff"]


def symmetric_levels(lo: float, hi: float, target: int = 14) -> list[float]:
    """ETABS / SAFE band boundaries: symmetric about zero, +-A where A is the largest magnitude rounded up to a
    round step (the smallest of 1, 2, 2.5, 4, 5 x 10^n giving at most 8 bands a side) (ETABS showed -32 ... 32 by 4 for M11 from -31.6 to 16.6; SAFE -350 ... 350 by
    50). Smallest first."""
    a = max(abs(lo), abs(hi))
    if a < 1e-12:
        return [0.0]
    half = target // 2 + 1                                 # at most 8 bands each side
    mag = 10 ** math.floor(math.log10(a / half))
    step = next(m * mag for m in (1, 2, 2.5, 4, 5, 10, 20) if math.ceil(a / (m * mag) - 1e-9) <= half)
    n = math.ceil(a / step - 1e-9)
    return [round(i * step, 10) for i in range(-n, n + 1)]


def _etabs_colors(nbands: int) -> list[str]:
    half = nbands // 2

    def pick(pal: list[str], m: int) -> list[str]:
        return [pal[round(i * (len(pal) - 1) / max(m - 1, 1))] for i in range(m)] if m else []
    return pick(ETABS_NEG, half) + pick(ETABS_POS, nbands - half)


def _band_colors(n: int) -> list[str]:
    if n <= len(CSI_BANDS):
        idx = [round(i * (len(CSI_BANDS) - 1) / max(n - 1, 1)) for i in range(n)]
        return [CSI_BANDS[i] for i in idx]
    return [CSI_BANDS[min(int(i * len(CSI_BANDS) / n), len(CSI_BANDS) - 1)] for i in range(n)]


def _band_of(val: float, levels: list[float]) -> int:
    """0 = above the highest boundary ... len(levels) = below the lowest."""
    for i, b in enumerate(levels):
        if val > b:
            return i
    return len(levels)


def joint_average(elements: list[dict[str, Any]]) -> dict[str, float]:
    """Average of the element-corner values at each joint (the programs' default 'averaging at all joints')."""
    acc: dict[str, list[float]] = defaultdict(list)
    for e in elements:
        for n, val in zip(e["nodes"], e["vals"]):
            acc[n].append(val)
    return {n: sum(v) / len(v) for n, v in acc.items()}


def _sub_cells(poly: list[tuple[float, float]], vals: list[float], k: int):
    """Split a triangle / quad into k x k cells with bilinear (or linear) values at the cell centres."""
    if len(poly) == 4:
        def at(u, v):
            w_ = ((1 - u) * (1 - v), u * (1 - v), u * v, (1 - u) * v)
            return (sum(wi * p[0] for wi, p in zip(w_, poly)), sum(wi * p[1] for wi, p in zip(w_, poly)),
                    sum(wi * x for wi, x in zip(w_, vals)))
        for i in range(k):
            for j in range(k):
                c = [at((i + di) / k, (j + dj) / k) for di, dj in ((0, 0), (1, 0), (1, 1), (0, 1))]
                yield [(p[0], p[1]) for p in c], at((i + 0.5) / k, (j + 0.5) / k)[2]
    elif len(poly) == 3:
        def at(a, b):
            w_ = (1 - a - b, a, b)
            return (sum(wi * p[0] for wi, p in zip(w_, poly)), sum(wi * p[1] for wi, p in zip(w_, poly)),
                    sum(wi * x for wi, x in zip(w_, vals)))
        for i in range(k):
            for j in range(k - i):
                tri = [at(i / k, j / k), at((i + 1) / k, j / k), at(i / k, (j + 1) / k)]
                yield [(p[0], p[1]) for p in tri], sum(p[2] for p in tri) / 3
                if i + j < k - 1:
                    tri = [at((i + 1) / k, j / k), at((i + 1) / k, (j + 1) / k), at(i / k, (j + 1) / k)]
                    yield [(p[0], p[1]) for p in tri], sum(p[2] for p in tri) / 3
    else:
        yield poly, sum(vals) / len(vals)


def _triangles(poly: list[tuple[float, float]], vals: list[float], k: int):
    """Triangles (x, y, value) covering an element: a quad as a k x k grid of bilinear values, each cell in two
    triangles; a triangle as is; other polygons fanned from their centroid."""
    if len(poly) == 4:
        def at(u, v):
            w_ = ((1 - u) * (1 - v), u * (1 - v), u * v, (1 - u) * v)
            return (sum(wi * p[0] for wi, p in zip(w_, poly)), sum(wi * p[1] for wi, p in zip(w_, poly)),
                    sum(wi * x for wi, x in zip(w_, vals)))
        g = [[at(i / k, j / k) for j in range(k + 1)] for i in range(k + 1)]
        for i in range(k):
            for j in range(k):
                a, b, c, d = g[i][j], g[i + 1][j], g[i + 1][j + 1], g[i][j + 1]
                yield (a, b, c)
                yield (a, c, d)
    elif len(poly) == 3:
        yield tuple((p[0], p[1], x) for p, x in zip(poly, vals))
    elif len(poly) > 4:
        cx, cy = sum(p[0] for p in poly) / len(poly), sum(p[1] for p in poly) / len(poly)
        cv = sum(vals) / len(vals)
        for i in range(len(poly)):
            j = (i + 1) % len(poly)
            yield ((poly[i][0], poly[i][1], vals[i]), (poly[j][0], poly[j][1], vals[j]), (cx, cy, cv))


def _clip(pts: list[tuple[float, float, float]], level: float, keep_above: bool):
    """Clip a polygon with linearly varying value to value >= level (or <= level)."""
    out = []
    n = len(pts)
    for i in range(n):
        p, q = pts[i], pts[(i + 1) % n]
        pin = p[2] >= level if keep_above else p[2] <= level
        qin = q[2] >= level if keep_above else q[2] <= level
        if pin:
            out.append(p)
        if pin != qin and abs(q[2] - p[2]) > 1e-300:
            t = (level - p[2]) / (q[2] - p[2])
            out.append((p[0] + t * (q[0] - p[0]), p[1] + t * (q[1] - p[1]), level))
    return out


def _iso_bands(tri, bounds: list[float]):
    """Split a linear triangle into iso-bands: yields (band index, polygon) for bounds ascending (band i between
    bounds[i] and bounds[i + 1]; below the first / above the last go to the end bands)."""
    vs = [p[2] for p in tri]
    lo_b = max(0, next((i for i in range(len(bounds) - 1) if min(vs) < bounds[i + 1]), len(bounds) - 2))
    for i in range(lo_b, len(bounds) - 1):
        lo = bounds[i] if i > 0 else -math.inf
        hi = bounds[i + 1] if i < len(bounds) - 2 else math.inf
        if max(vs) < lo:
            break
        poly = list(tri)
        if lo != -math.inf:
            poly = _clip(poly, lo, True)
        if len(poly) >= 3 and hi != math.inf:
            poly = _clip(poly, hi, False)
        if len(poly) >= 3:
            yield i, poly


def shell_contour(elements: list[dict[str, Any]], comp: str, w: float, h: float, averaged: bool | None = None,
                  style: str = "sap"):
    """Contour of a shell component over one plane: elements = [{'poly': [(u, v)...], 'nodes': [...],
    'vals': [corner values]}], interpolated over each element and coloured in bands as the program shows it.
    style 'sap' = SAP2000 (values averaged at the joints, bands between round values from max to min, blue = most
    positive ... purple = most negative, vertical bar at the right); 'etabs' = ETABS / SAFE (no averaging, a scale
    symmetric about zero: purple ... yellow below zero, green ... blue above, horizontal bar at the bottom).
    averaged None = the program's default."""
    from reportlab.graphics.shapes import Polygon, Rect
    if not elements:
        return _drawing(w, h), (0.0, 0.0)
    etabs = style == "etabs"
    if averaged is None:
        averaged = not etabs
    avg = joint_average(elements) if averaged else None
    for e in elements:
        e["use"] = [avg[n] for n in e["nodes"]] if avg else e["vals"]
    vals = [x for e in elements for x in e["use"]]
    lo, hi = min(vals), max(vals)
    if etabs:
        bounds = symmetric_levels(lo, hi)                  # ascending
        cols = [_c(x) for x in _etabs_colors(max(len(bounds) - 1, 1))]

        v = _View([p for e in elements for p in e["poly"]], w - 4, h - 34, margin=16, y0=30)
    else:
        levels = contour_levels(lo, hi)                   # descending
        cols = [_c(x) for x in _band_colors(len(levels) + 1)]
        v = _View([p for e in elements for p in e["poly"]], w - 46, h - 4, margin=18)
    d = _drawing(w, h)
    # exact iso-bands: each element in linear triangles, each triangle clipped at the band boundaries, so the band
    # edges run straight and continuous across the elements as in the programs' displays
    if etabs:
        edges = bounds
    else:
        edges = list(reversed(levels))
        edges = [min(lo, edges[0]) - 1.0] + edges + [max(hi, edges[-1]) + 1.0]
    k = 3 if len(elements) < 400 else 2 if len(elements) < 3000 else 1
    nb = len(cols)
    for e in elements:
        for tri in _triangles(e["poly"], e["use"], k):
            for i, poly in _iso_bands(tri, edges):
                col = cols[i] if etabs else cols[nb - 1 - i]
                d.add(Polygon([c for p in poly for c in v(p[0], p[1])], fillColor=col, strokeColor=col,
                              strokeWidth=0.4))
        if len(elements) <= 800:            # mesh lines; left out on very fine meshes, where they would hide the colours
            d.add(Polygon([c for p in e["poly"] for c in v(*p)], fillColor=None, strokeColor=_c("#3a3a3a"),
                          strokeWidth=0.15))
    n = len(cols)
    if etabs:
        bw = min(w - 60, 26 * n)
        bx, by = (w - bw) / 2, 10
        for i, col in enumerate(cols):
            d.add(Rect(bx + i * bw / n, by, bw / n, 8, fillColor=col, strokeColor=None))
        for i, lev in enumerate(bounds):
            _text(d, bx + i * bw / n, by - 6, _fmt(lev), 4.6, DARK)
        _text(d, bx - 14, by + 2, comp, 5.5, DARK)
    else:
        bx, bh = w - 40, min(h - 30, 13 * n)
        by = (h - bh) / 2
        for i, col in enumerate(cols):
            d.add(Rect(bx, by + bh - (i + 1) * bh / n, 8, bh / n, fillColor=col, strokeColor=None))
        for i, lev in enumerate(levels):
            _text(d, bx + 10, by + bh - (i + 1) * bh / n - 2, _fmt(lev), 4.6, DARK, "start")
        _text(d, bx + 4, by + bh + 5, comp, 5.5, DARK)
    return d, (lo, hi)


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
