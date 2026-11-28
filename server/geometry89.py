"""Phase 8.9 (0.24.0): geometry edits in all three programs - divide frames (ETABS / SAFE added), join frames, trim /
extend a frame to another, divide and merge areas, expand / shrink areas (SAP2000), and set point coordinates
(move to coordinates or align on an ordinate).

Measured (tests/diag_86.py, 28 Nov 2026):
- SAP2000 27.1 has the typed tools: EditFrame.Join / Trim / Extend (Trim and Extend move the chosen end to the line
  of the other frame), EditArea.Merge (selected areas; answers the merged area), EditArea.ExpandShrink (selected
  areas; OffsetType 0 offsets every edge, + expands, - shrinks), EditArea.Divide, EditPoint.ChangeCoordinates_1
  (EditPoint.Align 1 / 2 / 3 = X / Y / Z, and it merges points that end up together).
- ETABS 23.2 and SAFE 23.3 have only EditGeneral.Move (on the selection; a point moved alone takes its frames with
  it) and EditFrame.ChangeConnectivity. Their edits here are built from the typed object calls: new points
  (PointObj.AddCartesian), ChangeConnectivity, FrameObj / AreaObj AddByPoint / AddByCoord / Delete and the getters /
  setters of each assignment. What each answer copied, and what it did not, is listed in the answer.
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, model_validator

from actions import ActionFailed, Params, _dry, _refresh_view, action, bridge
from csi_bridge import CSIApiError, CSIValidationError, api_call, as_list, describe_units
from geometry import _restore, _select_only, _selection
from model_read import _names, _try
from modelling import _start_edit, _targets
from objects import MAX, _EditP
from programs import sap2000 as sap_api

TOL = 1e-6


def _xyz(sap: Any, p: str) -> tuple[float, float, float]:
    c = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, p, 0.0, 0.0, 0.0)
    return float(c[0]), float(c[1]), float(c[2])


def _ends(sap: Any, f: str) -> tuple[str, str]:
    g = api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, f, "", "")
    return str(g[0]), str(g[1])


def _sub(a, b):
    return tuple(a[i] - b[i] for i in range(3))


def _dot(a, b):
    return sum(a[i] * b[i] for i in range(3))


def _norm(a):
    return math.sqrt(_dot(a, a))


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def _r(v: float) -> float:
    return float(f"{float(v):.10g}")


def _new_point(sap: Any, xyz: tuple[float, float, float]) -> str:
    o = api_call("PointObj.AddCartesian", sap.PointObj.AddCartesian, *xyz, "", "", "Global")
    return str(o[0])


def _label(sap: Any, sw: str, coll: str, n: str) -> dict[str, str]:
    if sw == "SAP2000":
        return {}
    g = _try(sap, f"{coll}.GetLabelFromName", n, "", "")
    return {"label": str(g[0]), "story": str(g[1]).strip()} if g else {}


# --------------------------------------------------------------------------- frame assignments (ETABS / SAFE copies)

def frame_state(sap: Any, sw: str, f: str) -> dict[str, Any]:
    """Everything the ETABS / SAFE emulation copies to the pieces of a frame."""
    s: dict[str, Any] = {"section": str(api_call("FrameObj.GetSection", sap.FrameObj.GetSection, f)[0])}
    rel = _try(sap, "FrameObj.GetReleases", f, [False] * 6, [False] * 6, [0.0] * 6, [0.0] * 6)
    s["releases"] = [list(as_list(rel[i])) for i in range(4)] if rel else None
    ax = _try(sap, "FrameObj.GetLocalAxes", f)
    s["angle"] = float(ax[0]) if ax else 0.0
    mod = _try(sap, "FrameObj.GetModifiers", f, [0.0] * 8) or _try(sap, "FrameObj.GetModifiers", f)
    s["modifiers"] = [float(v) for v in as_list(mod[0])] if mod else None
    ins = _try(sap, "FrameObj.GetInsertionPoint_1", f)
    s["insertion"] = ins
    off = _try(sap, "FrameObj.GetEndLengthOffset", f)
    s["offsets"] = off
    mo = _try(sap, "FrameObj.GetMaterialOverwrite", f)
    s["material_overwrite"] = str(mo[0]) if mo and str(mo[0]) not in ("", "None") else None
    g = _try(sap, "FrameObj.GetGroupAssign", f)
    s["groups"] = [x for x in (str(v) for v in as_list(g[1])) if x.upper() != "ALL"] if g else []
    if sw != "SAP2000":
        sp = _try(sap, "FrameObj.GetSpringAssignment", f)
        s["spring"] = str(sp[0]) if sp and str(sp[0]) not in ("", "None") else None
    if sw == "ETABS":
        for what in ("Pier", "Spandrel"):
            v = _try(sap, f"FrameObj.Get{what}", f)
            s[what.lower()] = str(v[0]) if v and str(v[0]) not in ("", "None") else None
    d = _try(sap, "FrameObj.GetLoadDistributed", f)
    s["distributed"] = ([dict(zip(("pattern", "type", "csys", "dir", "d1", "d2", "v1", "v2"),
                                  (str(a), int(b), str(c), int(e), float(h), float(i), float(j), float(k))))
                         for a, b, c, e, h, i, j, k in zip(*(as_list(d[n]) for n in (2, 3, 4, 5, 8, 9, 10, 11)))]
                        if d and d[0] else [])
    pl = _try(sap, "FrameObj.GetLoadPoint", f)
    s["point"] = ([dict(zip(("pattern", "type", "csys", "dir", "dist", "value"),
                            (str(a), int(b), str(c), int(e), float(h), float(i))))
                   for a, b, c, e, h, i in zip(*(as_list(pl[n]) for n in (2, 3, 4, 5, 7, 8)))] if pl and pl[0] else [])
    tl = _try(sap, "FrameObj.GetLoadTemperature", f)
    s["temperature"] = ([(str(a), int(b), float(c), str(e)) for a, b, c, e in
                         zip(as_list(tl[2]), as_list(tl[3]), as_list(tl[4]), as_list(tl[5]))] if tl and tl[0] else [])
    return s


def _apply_frame_state(sap: Any, sw: str, new: str, s: dict[str, Any], first: bool, last: bool) -> None:
    fo = sap.FrameObj
    api_call("FrameObj.SetSection", fo.SetSection, new, s["section"])
    if s["releases"]:
        ii, jj, vi, vj = s["releases"]
        ii2 = ii if first else [False] * 6
        jj2 = jj if last else [False] * 6
        if any(ii2) or any(jj2):
            api_call("FrameObj.SetReleases", fo.SetReleases, new, list(ii2), list(jj2),
                     list(vi) if first else [0.0] * 6, list(vj) if last else [0.0] * 6)
    if s["angle"]:
        api_call("FrameObj.SetLocalAxes", fo.SetLocalAxes, new, s["angle"])
    if s["modifiers"] and any(abs(v - 1) > 1e-12 for v in s["modifiers"]):
        api_call("FrameObj.SetModifiers", fo.SetModifiers, new, list(s["modifiers"]))
    ins = s["insertion"]
    if ins and (int(ins[0]) != 10 or ins[1] or ins[2] or any(as_list(ins[4])) or any(as_list(ins[5]))):
        api_call("FrameObj.SetInsertionPoint_1", fo.SetInsertionPoint_1, new, int(ins[0]), bool(ins[1]), bool(ins[2]),
                 bool(ins[3]), list(as_list(ins[4])), list(as_list(ins[5])), str(ins[6] or "Local"))
    off = s["offsets"]
    if off:
        api_call("FrameObj.SetEndLengthOffset", fo.SetEndLengthOffset, new, bool(off[0]),
                 float(off[1]) if first else 0.0, float(off[2]) if last else 0.0, float(off[3]))
    if s["material_overwrite"]:
        api_call("FrameObj.SetMaterialOverwrite", fo.SetMaterialOverwrite, new, s["material_overwrite"])
    for grp in s["groups"]:
        api_call("FrameObj.SetGroupAssign", fo.SetGroupAssign, new, grp, False)
    if s.get("spring"):
        api_call("FrameObj.SetSpringAssignment", fo.SetSpringAssignment, new, s["spring"])
    for what in ("pier", "spandrel"):
        if s.get(what):
            api_call(f"FrameObj.Set{what.title()}", getattr(fo, f"Set{what.title()}"), new, s[what])
    for pat, typ, val, jp in s["temperature"]:
        api_call("FrameObj.SetLoadTemperature", fo.SetLoadTemperature, new, pat, typ, val, jp if jp != "None" else "",
                 False)


def _loads_on_piece(sap: Any, new: str, s: dict[str, Any], a: float, b: float) -> int:
    """Put the original frame's distributed and point loads (absolute distances from its I end) on the piece that
    spans [a, b] of the original; values interpolated linearly at the piece ends. Returns loads set."""
    fo, n = sap.FrameObj, 0
    for ld in s["distributed"]:
        lo, hi = max(a, ld["d1"]), min(b, ld["d2"])
        if hi - lo <= TOL * max(1.0, b):
            continue
        span = ld["d2"] - ld["d1"]
        val = (lambda x: ld["v1"] + (ld["v2"] - ld["v1"]) * (x - ld["d1"]) / span) if span > 0 else (lambda x: ld["v1"])
        api_call("FrameObj.SetLoadDistributed", fo.SetLoadDistributed, new, ld["pattern"], ld["type"], ld["dir"],
                 lo - a, hi - a, val(lo), val(hi), ld["csys"], False, False)
        n += 1
    for ld in s["point"]:
        if not ld.get("_placed") and a - TOL <= ld["dist"] <= b + TOL:
            api_call("FrameObj.SetLoadPoint", fo.SetLoadPoint, new, ld["pattern"], ld["type"], ld["dir"],
                     min(max(ld["dist"] - a, 0.0), b - a), ld["value"], ld["csys"], False, False)
            ld["_placed"] = True
            n += 1
    return n


# --------------------------------------------------------------------------- divide frames (all programs)

def divide_frame_emulated(sap: Any, sw: str, f: str, stations: list[float]) -> list[str]:
    """ETABS / SAFE: split frame f at the given distances from its I end (0 < d < L, increasing). The original keeps
    the first piece; new frames carry the rest with the original's assignments and loads."""
    pi, pj = _ends(sap, f)
    a, b = _xyz(sap, pi), _xyz(sap, pj)
    length = _norm(_sub(b, a))
    s = frame_state(sap, sw, f)
    pts = [pi] + [_new_point(sap, _lerp(a, b, d / length)) for d in stations] + [pj]
    cuts = [0.0] + list(stations) + [length]
    # loads first off the original (they are re-applied per piece by absolute distance)
    for pat in {ld["pattern"] for ld in s["distributed"]}:
        api_call("FrameObj.DeleteLoadDistributed", sap.FrameObj.DeleteLoadDistributed, f, pat)
    for pat in {ld["pattern"] for ld in s["point"]}:
        api_call("FrameObj.DeleteLoadPoint", sap.FrameObj.DeleteLoadPoint, f, pat)
    api_call("EditFrame.ChangeConnectivity", sap.EditFrame.ChangeConnectivity, f, pts[0], pts[1])
    pieces = [f]
    rel = s["releases"]
    if rel and any(rel[1]):     # the J release now belongs to the last piece
        api_call("FrameObj.SetReleases", sap.FrameObj.SetReleases, f, list(rel[0]), [False] * 6, list(rel[2]),
                 [0.0] * 6)
    if s["offsets"] and s["offsets"][2]:
        off = s["offsets"]
        api_call("FrameObj.SetEndLengthOffset", sap.FrameObj.SetEndLengthOffset, f, bool(off[0]), float(off[1]), 0.0,
                 float(off[3]))
    _loads_on_piece(sap, f, s, cuts[0], cuts[1])
    for k in range(1, len(pts) - 1):
        o = api_call("FrameObj.AddByPoint", sap.FrameObj.AddByPoint, pts[k], pts[k + 1], "", s["section"], "")
        new = str(o[0])
        _apply_frame_state(sap, sw, new, s, first=False, last=(k == len(pts) - 2))
        _loads_on_piece(sap, new, s, cuts[k], cuts[k + 1])
        pieces.append(new)
    return pieces


class DivideFramesP(_EditP):
    frames: list[str] | None = Field(default=None, max_length=MAX, description="frame names")
    group: str | None = Field(default=None, description="or a group: its frames")
    mode: Literal["ratio", "distance", "intersections"] = Field(description=(
        "ratio: into `number` pieces, last / first length = `ratio` | distance: in two at `distance` from the I or "
        "J end | [[S:intersections: at points and frames that cross it]]"))
    number: int = Field(default=2, ge=2, le=1000, description="ratio: pieces")
    ratio: float = Field(default=1.0, gt=0, description="ratio: last piece / first piece (1 = equal)")
    distance: float | None = Field(default=None, gt=0, description="distance: from the end (length units)")
    from_end: Literal["i", "j"] = Field(default="i", description="distance: measured from the I or J end")

    @model_validator(mode="after")
    def _mode(self) -> "DivideFramesP":
        if self.mode == "distance" and self.distance is None:
            raise ValueError("distance mode needs distance")
        return self


def _stations(length: float, p: DivideFramesP) -> list[float]:
    if p.mode == "distance":
        d = p.distance if p.from_end == "i" else length - p.distance
        return [d]
    n = p.number
    q = p.ratio ** (1.0 / (n - 1)) if n > 1 else 1.0     # geometric: piece k = l1 q^k, l_last / l_first = ratio
    w = [q ** k for k in range(n)]
    tot, run, out = sum(w), 0.0, []
    for k in range(n - 1):
        run += w[k]
        out.append(length * run / tot)
    return out


@action("divide_frames", "Divide frame objects (names or a group) into pieces: equal or graded (ratio), at a "
        "distance from an end[[S:, or at intersections]]. [[S:Loads and assignments go to the pieces.]][[EF:ETABS / "
        "SAFE have no divide tool: the frame keeps its first piece and new frames are added for the rest with its "
        "section, releases (I end on the first piece, J end on the last), local axis, modifiers, insertion point, end "
        "offsets, material overwrite, groups, line spring]][[E:, pier / spandrel]][[EF: and its distributed, point and "
        "temperature loads (split by distance).]] Answers with the pieces and their end points.", "Geometry", "edit",
        DivideFramesP, options={"mode": {"intersections": "S"}})
def divide_frames(p: DivideFramesP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        frames = _targets(sap, "FrameObj", p.frames, p.group, "frames")
        lengths = {}
        for f in frames:
            i, j = _ends(sap, f)
            lengths[f] = _norm(_sub(_xyz(sap, j), _xyz(sap, i)))
        if p.mode == "distance":
            short = [f for f, length in lengths.items() if p.distance >= length]
            if short:
                raise CSIValidationError(f"distance {p.distance} is not inside frames {short[:10]} (lengths "
                                         f"{[round(lengths[f], 4) for f in short[:10]]}).")
        plan = {"mode": p.mode, "frames": len(frames), "units": describe_units(p.units)}
        if p.mode == "ratio":
            plan.update(number=p.number, ratio=p.ratio)
        elif p.mode == "distance":
            plan.update(distance=p.distance, from_end=p.from_end)
        if p.dry_run:
            if p.mode != "intersections":
                plan["cut_stations"] = {f: [_r(x) for x in _stations(lengths[f], p)] for f in frames[:20]}
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_divide_frames", "divide frames")
        pieces: dict[str, list[str]] = {}
        for f in frames:
            if p.software != "SAP2000":
                pieces[f] = divide_frame_emulated(sap, p.software, f, _stations(lengths[f], p))
                continue
            if p.mode == "ratio":
                o = api_call(f"EditFrame.DivideByRatio('{f}')", sap.EditFrame.DivideByRatio, f, p.number, p.ratio, [])
            elif p.mode == "distance":
                o = api_call(f"EditFrame.DivideAtDistance('{f}')", sap.EditFrame.DivideAtDistance, f, p.distance,
                             p.from_end == "i", [])
            else:
                o = api_call(f"EditFrame.DivideAtIntersections('{f}')", sap.EditFrame.DivideAtIntersections, f, 0, [])
            pieces[f] = [str(x) for x in as_list(o[-1])] if o else []
        detail = {}
        for f, names in list(pieces.items())[:100]:
            rows = []
            for n in names:
                i, j = _ends(sap, n)
                rows.append({"frame": n, "i": [_r(v) for v in _xyz(sap, i)], "j": [_r(v) for v in _xyz(sap, j)],
                             **_label(sap, p.software, "FrameObj", n)})
            detail[f] = rows
    _refresh_view(sap)
    out = {**plan, "pieces": detail, "pieces_total": sum(len(v) for v in pieces.values()),
           "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if p.software != "SAP2000":
        out["not_copied"] = ["lateral bracing", "output stations", "design overwrites", "hinges"]
    return out


# --------------------------------------------------------------------------- join frames

class JoinP(_EditP):
    frame: str = Field(description="the frame that stays (it is extended over the second)")
    other: str = Field(description="the frame joined to it (deleted); collinear, sharing one end point")


@action("join_frames", "Join two collinear frames that share an end point into one: the first frame is extended "
        "over the second, which is deleted. [[S:SAP2000's own join.]][[EF:The first frame is reconnected to the far end "
        "of the second; both must have the same section, and frame loads on either are refused (move them after "
        "joining).]] Answers with the joined frame and its end points.", "Geometry", "edit", JoinP)
def join_frames(p: JoinP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        _targets(sap, "FrameObj", [p.frame, p.other], None, "frames")
        a1, a2 = _ends(sap, p.frame)
        b1, b2 = _ends(sap, p.other)
        shared = {a1, a2} & {b1, b2}
        if len(shared) != 1:
            raise CSIValidationError(f"'{p.frame}' and '{p.other}' do not share exactly one end point "
                                     f"({a1}-{a2} and {b1}-{b2}).")
        mid = shared.pop()
        far_a = a1 if a2 == mid else a2
        far_b = b1 if b2 == mid else b2
        A, M, B = _xyz(sap, far_a), _xyz(sap, mid), _xyz(sap, far_b)
        u, v = _sub(M, A), _sub(B, M)
        off = _norm(_cross(u, v)) / max(_norm(u) * _norm(v), 1e-30)
        if off > 1e-6 or _dot(u, v) <= 0:
            raise CSIValidationError(f"'{p.frame}' and '{p.other}' are not collinear and continuing (sine of the "
                                     f"angle {off:.3g}).")
        secs = [str(api_call("FrameObj.GetSection", sap.FrameObj.GetSection, f)[0]) for f in (p.frame, p.other)]
        plan = {"frame": p.frame, "other": p.other, "shared_point": mid, "new_ends": [far_a, far_b],
                "sections": secs, "length": _r(_norm(_sub(B, A))), "units": describe_units(p.units)}
        if p.software != "SAP2000":
            if secs[0] != secs[1]:
                raise CSIValidationError(f"Sections differ ({secs[0]} / {secs[1]}).", hint="Give both the same section.")
            loaded = [f for f in (p.frame, p.other)
                      if (frame_state(sap, p.software, f)["distributed"] or frame_state(sap, p.software, f)["point"])]
            if loaded:
                raise CSIValidationError(f"Frames {loaded} carry frame loads, which a join would lose or stretch.",
                                         hint="Delete their loads, join, then assign the loads to the joined frame.")
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_join_frames", "join frames")
        if p.software == "SAP2000":
            api_call("EditFrame.Join", sap.EditFrame.Join, p.frame, p.other)
        else:
            # reconnect first and refresh: ETABS deletes a deleted frame's end points that nothing else uses, judged
            # on the connectivity as of the last refresh (measured 28 Nov: without the refresh the far point went too)
            i_end = a1 == far_a
            new = [far_a, far_b] if i_end else [far_b, far_a]
            api_call("EditFrame.ChangeConnectivity", sap.EditFrame.ChangeConnectivity, p.frame, *new)
            _refresh_view(sap)
            api_call("FrameObj.Delete", sap.FrameObj.Delete, p.other, 0)
        e1, e2 = _ends(sap, p.frame)
        frames_now = set(_names(sap, "FrameObj"))
    _refresh_view(sap)
    with bridge.units_scope(sap, p.units):
        ends = {e1: [_r(v) for v in _xyz(sap, e1)], e2: [_r(v) for v in _xyz(sap, e2)]}
    if p.other in frames_now or {e1, e2} != {far_a, far_b} and not (
            _close_xyz(ends[e1], A) and _close_xyz(ends[e2], B) or _close_xyz(ends[e1], B) and _close_xyz(ends[e2], A)):
        raise ActionFailed({**plan, "message": "The program did not join the frames as expected.",
                            "read_back": {"ends": ends, "other_still_there": p.other in frames_now}})
    return {**plan, "joined": {"frame": p.frame, "ends": ends, **_label(sap, p.software, "FrameObj", p.frame)},
            "deleted": p.other, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


def _close_xyz(a, b, tol: float = 1e-6) -> bool:
    return all(abs(a[i] - b[i]) <= tol * max(1.0, abs(b[i])) for i in range(3))


# --------------------------------------------------------------------------- trim / extend a frame

def _line_meet(p1, p2, q1, q2):
    """Point on line p1-p2 closest to line q1-q2, its parameter t along p (0 at p1, 1 at p2), parameter s along q,
    and the gap between the two lines there."""
    d1, d2, r = _sub(p2, p1), _sub(q2, q1), _sub(p1, q1)
    a, e, f = _dot(d1, d1), _dot(d2, d2), _dot(d2, r)
    b, c = _dot(d1, d2), _dot(d1, r)
    den = a * e - b * b
    if den <= 1e-14 * a * e:
        return None
    t = (b * f - c * e) / den
    s = (a * f - b * c) / den
    pa, qb = _lerp(p1, p2, t), _lerp(q1, q2, s)
    return pa, t, s, _norm(_sub(pa, qb))


class TrimP(_EditP):
    frame: str = Field(description="the frame to trim or extend")
    end: Literal["i", "j"] = Field(description="which end moves: i | j")
    to_frame: str = Field(description="the frame whose line the end moves to (it is not changed)")
    mode: Literal["trim", "extend"] = Field(description=(
        "trim: the meeting point lies inside the frame (the end is cut back) | extend: it lies beyond that end"))


@action("trim_or_extend_frame", "Trim or extend one end of a frame to the line of another frame (the meeting point is "
        "worked out and checked: inside the frame for trim, beyond the end for extend; the lines must meet). "
        "[[S:SAP2000's own trim / extend.]][[EF:The end is reconnected to a point at the meeting place (a new point, "
        "or the existing one there).]] Answers with the frame's new end points.", "Geometry", "edit", TrimP)
def trim_or_extend_frame(p: TrimP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        _targets(sap, "FrameObj", [p.frame, p.to_frame], None, "frames")
        i, j = _ends(sap, p.frame)
        qi, qj = _ends(sap, p.to_frame)
        I, J, Q1, Q2 = _xyz(sap, i), _xyz(sap, j), _xyz(sap, qi), _xyz(sap, qj)
        meet = _line_meet(I, J, Q1, Q2)
        length = _norm(_sub(J, I))
        if meet is None:
            raise CSIValidationError(f"'{p.frame}' is parallel to '{p.to_frame}': the lines do not meet.")
        pt, t, s, gap = meet
        if gap > 1e-6 * max(1.0, length):
            raise CSIValidationError(f"The lines of '{p.frame}' and '{p.to_frame}' do not meet (closest {gap:.6g} "
                                     "apart).")
        inside = 1e-9 < t < 1 - 1e-9
        beyond = t > 1 + 1e-9 if p.end == "j" else t < -1e-9
        if p.mode == "trim" and not inside:
            raise CSIValidationError(f"The meeting point is not inside '{p.frame}' (t = {t:.4g}): nothing to trim.",
                                     hint="Use mode extend if it lies beyond the end.")
        if p.mode == "extend" and not beyond:
            raise CSIValidationError(f"The meeting point is not beyond the {p.end.upper()} end of '{p.frame}' "
                                     f"(t = {t:.4g}).", hint="Use mode trim if it lies inside the frame.")
        on_other = -1e-9 <= s <= 1 + 1e-9
        plan = {"frame": p.frame, "end": p.end, "to_frame": p.to_frame, "mode": p.mode,
                "meeting_point": [_r(v) for v in pt], "within_to_frame": on_other,
                "new_length": _r(_norm(_sub(pt, I if p.end == "j" else J))), "old_length": _r(length),
                "units": describe_units(p.units)}
        if not on_other:
            plan["note"] = f"the meeting point lies on the line of '{p.to_frame}' but outside the frame itself"
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_trim_extend", f"{p.mode} a frame")
        if p.software == "SAP2000" and on_other:
            fn = sap.EditFrame.Trim if p.mode == "trim" else sap.EditFrame.Extend
            api_call(f"EditFrame.{p.mode.title()}", fn, p.frame, p.end == "i", p.end == "j", p.to_frame, "")
        else:
            existing = next((q for q in (qi, qj) if _close_xyz(_xyz(sap, q), pt)), None)
            np_ = existing or _new_point(sap, pt)
            new = [np_, j] if p.end == "i" else [i, np_]
            api_call("EditFrame.ChangeConnectivity", sap.EditFrame.ChangeConnectivity, p.frame, *new)
        e1, e2 = _ends(sap, p.frame)
        ends = [[_r(v) for v in _xyz(sap, e1)], [_r(v) for v in _xyz(sap, e2)]]
    _refresh_view(sap)
    moved = ends[0] if p.end == "i" else ends[1]
    out = {**plan, "ends": {"i": ends[0], "j": ends[1]}, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if not _close_xyz(moved, pt):
        out["message"] = "The end did not move to the meeting point."
        raise ActionFailed(out)
    return out


# --------------------------------------------------------------------------- area assignments (ETABS / SAFE copies)

def area_state(sap: Any, sw: str, a: str) -> dict[str, Any]:
    s: dict[str, Any] = {"property": str(api_call("AreaObj.GetProperty", sap.AreaObj.GetProperty, a)[0])}
    ax = _try(sap, "AreaObj.GetLocalAxes", a)
    s["angle"] = float(ax[0]) if ax else 0.0
    mod = _try(sap, "AreaObj.GetModifiers", a, [0.0] * 10) or _try(sap, "AreaObj.GetModifiers", a)
    s["modifiers"] = [float(v) for v in as_list(mod[0])] if mod else None
    op = _try(sap, "AreaObj.GetOpening", a)
    s["opening"] = bool(op[0]) if op else False
    mo = _try(sap, "AreaObj.GetMaterialOverwrite", a)
    s["material_overwrite"] = str(mo[0]) if mo and str(mo[0]) not in ("", "None") else None
    g = _try(sap, "AreaObj.GetGroupAssign", a)
    s["groups"] = [x for x in (str(v) for v in as_list(g[1])) if x.upper() != "ALL"] if g else []
    if sw != "SAP2000":
        for what, getter in (("diaphragm", "GetDiaphragm"), ("spring", "GetSpringAssignment")):
            v = _try(sap, f"AreaObj.{getter}", a)
            s[what] = str(v[0]) if v and str(v[0]) not in ("", "None") else None
    if sw == "ETABS":
        for what in ("Pier", "Spandrel"):
            v = _try(sap, f"AreaObj.Get{what}", a)
            s[what.lower()] = str(v[0]) if v and str(v[0]) not in ("", "None") else None
    u = _try(sap, "AreaObj.GetLoadUniform", a)
    s["uniform"] = [(str(x), str(c), int(d), float(v)) for x, c, d, v in
                    zip(as_list(u[2]), as_list(u[3]), as_list(u[4]), as_list(u[5]))] if u and u[0] else []
    u = _try(sap, "AreaObj.GetLoadUniformToFrame", a)
    s["to_frame"] = [(str(x), str(c), int(d), float(v), int(t)) for x, c, d, v, t in
                     zip(as_list(u[2]), as_list(u[3]), as_list(u[4]), as_list(u[5]), as_list(u[6]))] if u and u[0] else []
    u = _try(sap, "AreaObj.GetLoadTemperature", a)
    s["temperature"] = [(str(x), int(t), float(v), str(jp)) for x, t, v, jp in
                        zip(as_list(u[2]), as_list(u[3]), as_list(u[4]), as_list(u[5]))] if u and u[0] else []
    u = _try(sap, "AreaObj.GetLoadWindPressure", a)
    s["wind"] = [(str(x), int(t), float(cp)) for x, t, cp in
                 zip(as_list(u[2]), as_list(u[3]), as_list(u[4]))] if u and u[0] else []
    return s


def _apply_area_state(sap: Any, sw: str, new: str, s: dict[str, Any]) -> None:
    ao = sap.AreaObj
    if s["angle"]:
        api_call("AreaObj.SetLocalAxes", ao.SetLocalAxes, new, s["angle"])
    if s["modifiers"] and any(abs(v - 1) > 1e-12 for v in s["modifiers"]):
        api_call("AreaObj.SetModifiers", ao.SetModifiers, new, list(s["modifiers"]))
    if s["opening"]:
        api_call("AreaObj.SetOpening", ao.SetOpening, new, True)
    if s["material_overwrite"]:
        api_call("AreaObj.SetMaterialOverwrite", ao.SetMaterialOverwrite, new, s["material_overwrite"])
    for grp in s["groups"]:
        api_call("AreaObj.SetGroupAssign", ao.SetGroupAssign, new, grp, False)
    if s.get("diaphragm"):
        api_call("AreaObj.SetDiaphragm", ao.SetDiaphragm, new, s["diaphragm"])
    if s.get("spring"):
        api_call("AreaObj.SetSpringAssignment", ao.SetSpringAssignment, new, s["spring"])
    for what in ("pier", "spandrel"):
        if s.get(what):
            api_call(f"AreaObj.Set{what.title()}", getattr(ao, f"Set{what.title()}"), new, s[what])
    for pat, cs, d, v in s["uniform"]:
        api_call("AreaObj.SetLoadUniform", ao.SetLoadUniform, new, pat, v, d, False, cs)
    for pat, cs, d, v, t in s["to_frame"]:
        api_call("AreaObj.SetLoadUniformToFrame", ao.SetLoadUniformToFrame, new, pat, v, d, t, False, cs)
    for pat, t, v, jp in s["temperature"]:
        api_call("AreaObj.SetLoadTemperature", ao.SetLoadTemperature, new, pat, t, v, jp if jp != "None" else "", False)
    for pat, t, cp in s["wind"]:
        api_call("AreaObj.SetLoadWindPressure", ao.SetLoadWindPressure, new, pat, t, cp)


def _corners(sap: Any, a: str) -> list[str]:
    g = api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, a)
    return [str(x) for x in as_list(g[1])]


def _add_area(sap: Any, xyz: list[tuple[float, float, float]], prop: str) -> str:
    xs, ys, zs = ([c[k] for c in xyz] for k in range(3))
    o = api_call("AreaObj.AddByCoord", sap.AreaObj.AddByCoord, len(xyz), xs, ys, zs, "", prop, "", "Global")
    return next((str(x) for x in reversed(o) if isinstance(x, str) and x), "")


AREA_NOT_COPIED = ["auto edge constraints", "design overwrites", "point restraints on new edge joints"]


# --------------------------------------------------------------------------- divide areas

class DivideAreasP(_EditP):
    areas: list[str] | None = Field(default=None, max_length=MAX, description="area names")
    group: str | None = Field(default=None, description="or a group: its areas")
    n1: int = Field(ge=1, le=200, description="pieces along the first edge (corner 1 to corner 2)")
    n2: int = Field(ge=1, le=200, description="pieces along the second edge (corner 2 to corner 3)")


@action("divide_areas", "Divide areas into n1 x n2 smaller area objects (the pieces replace the area). "
        "[[S:SAP2000's own divide (restraints of an edge carried to its new joints).]][[EF:ETABS / SAFE have no divide "
        "tool: four-sided areas are split along their edges (bilinear) into new areas with the original's section, "
        "local axis, modifiers, opening, material overwrite, groups, diaphragm, spring property]][[E:, pier / spandrel]]"
        "[[EF: and its uniform, uniform-to-frame, temperature and wind-pressure loads; the original is deleted.]] Answers with "
        "the new areas.", "Geometry", "edit", DivideAreasP)
def divide_areas(p: DivideAreasP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        areas = _targets(sap, "AreaObj", p.areas, p.group, "areas")
        if p.software != "SAP2000":
            bad = [a for a in areas if len(_corners(sap, a)) != 4]
            if bad:
                raise CSIValidationError(f"Areas {bad[:10]} do not have four corners.",
                                         hint="ETABS / SAFE: only four-sided areas can be divided here.")
        plan = {"areas": len(areas), "first_areas": areas[:20], "n1": p.n1, "n2": p.n2,
                "pieces_each": p.n1 * p.n2, "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_divide_areas", "divide areas")
        made: dict[str, list[str]] = {}
        failed = []
        for a in areas:
            try:
                if p.software == "SAP2000":
                    made[a] = sap_api.divide(sap, a, "divisions", p.n1, p.n2, 0.0, 0.0, True)["areas"]
                    continue
                c = [_xyz(sap, q) for q in _corners(sap, a)]
                s = area_state(sap, p.software, a)
                grid = [[_lerp(_lerp(c[0], c[1], u / p.n1), _lerp(c[3], c[2], u / p.n1), v / p.n2)
                         for v in range(p.n2 + 1)] for u in range(p.n1 + 1)]
                news = []
                for u in range(p.n1):
                    for v in range(p.n2):
                        quad = [grid[u][v], grid[u + 1][v], grid[u + 1][v + 1], grid[u][v + 1]]
                        new = _add_area(sap, quad, s["property"])
                        _apply_area_state(sap, p.software, new, s)
                        news.append(new)
                api_call("AreaObj.Delete", sap.AreaObj.Delete, a, 0)
                made[a] = news
            except (CSIApiError, CSIValidationError) as exc:
                failed.append({"area": a, "error": str(exc)[:300]})
    _refresh_view(sap)
    out = {**plan, "new_areas": {k: v[:200] for k, v in list(made.items())[:50]},
           "areas_created": sum(len(v) for v in made.values()), "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if p.software != "SAP2000":
        out["not_copied"] = AREA_NOT_COPIED
    if failed:
        out["failed"] = failed
        out["message"] = f"{len(failed)} of {len(areas)} areas were not divided."
        raise ActionFailed(out)
    return out


# --------------------------------------------------------------------------- merge areas

def _merge_outline(a: list[str], b: list[str]) -> list[str] | None:
    """Outline of two polygons sharing exactly one whole edge (two consecutive corners)."""
    common = set(a) & set(b)
    if len(common) != 2:
        return None
    p, q = sorted(common, key=a.index)
    na = len(a)
    ia, ib = a.index(p), a.index(q)
    if (ib - ia) % na != 1:
        if (ia - ib) % na == 1:
            p, q = q, p
        else:
            return None
    # a walks p -> q; b must walk q -> p (opposite sense) or p -> q (same sense: reverse b)
    nb = len(b)
    jq, jp = b.index(q), b.index(p)
    if (jp - jq) % nb != 1:
        if (jq - jp) % nb == 1:
            b = list(reversed(b))
        else:
            return None
    ia = a.index(q)
    rot_a = [a[(ia + k) % na] for k in range(na)]          # q ... p
    jb = b.index(p)
    rot_b = [b[(jb + k) % nb] for k in range(nb)]          # p ... q
    return rot_a + rot_b[1:-1]


def _drop_collinear(pts: list[str], xyz: dict[str, tuple]) -> list[str]:
    out = list(pts)
    changed = True
    while changed and len(out) > 3:
        changed = False
        for k in range(len(out)):
            a, b, c = xyz[out[k - 1]], xyz[out[k]], xyz[out[(k + 1) % len(out)]]
            u, v = _sub(b, a), _sub(c, b)
            if _norm(_cross(u, v)) <= 1e-9 * max(_norm(u) * _norm(v), 1e-30) and _dot(u, v) > 0:
                del out[k]
                changed = True
                break
    return out


class MergeAreasP(_EditP):
    areas: list[str] = Field(min_length=2, max_length=50, description=(
        "areas to merge: [[S:any adjacent areas]][[EF:two areas sharing one whole edge, with the same section]]"))


@action("merge_areas", "Merge adjacent areas into one. [[S:SAP2000's own merge.]][[EF:ETABS / SAFE: two areas that share "
        "one whole edge and have the same section become one new area (corners in a straight line dropped) with the "
        "first area's assignments and loads; both originals are deleted. Areas whose loads differ are refused.]] "
        "Answers with the merged area and its corners.", "Geometry", "edit", MergeAreasP)
def merge_areas(p: MergeAreasP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        areas = _targets(sap, "AreaObj", p.areas, None, "areas")
        plan: dict[str, Any] = {"areas": areas, "units": describe_units(p.units)}
        if p.software != "SAP2000":
            if len(areas) != 2:
                raise CSIValidationError("ETABS / SAFE: give exactly two areas.")
            ca, cb = _corners(sap, areas[0]), _corners(sap, areas[1])
            outline = _merge_outline(ca, cb)
            if not outline:
                raise CSIValidationError(f"'{areas[0]}' and '{areas[1]}' do not share exactly one whole edge.",
                                         hint=f"Corners: {ca} / {cb}")
            xyz = {q: _xyz(sap, q) for q in set(ca) | set(cb)}
            outline = _drop_collinear(outline, xyz)
            sa, sb = area_state(sap, p.software, areas[0]), area_state(sap, p.software, areas[1])
            if sa["property"] != sb["property"]:
                raise CSIValidationError(f"Sections differ ({sa['property']} / {sb['property']}).")
            for k in ("uniform", "to_frame", "temperature", "wind"):
                if sorted(sa[k]) != sorted(sb[k]):
                    raise CSIValidationError(f"The areas carry different {k.replace('_', ' ')} loads.",
                                             hint="Make their loads the same first, or merge and re-assign.")
            plan["outline"] = [{"point": q, "xyz": [_r(v) for v in xyz[q]]} for q in outline]
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_merge_areas", "merge areas")
        if p.software == "SAP2000":
            saved = _selection(sap)
            try:
                _select_only(sap, {"areas": areas})
                o = api_call("EditArea.Merge", sap.EditArea.Merge, 0, [])
            finally:
                _restore(sap, saved)
            result = [str(x) for x in as_list(o[1])] if o else []
        else:
            new = _add_area(sap, [xyz[q] for q in outline], sa["property"])
            _apply_area_state(sap, p.software, new, sa)
            for a in areas:
                api_call("AreaObj.Delete", sap.AreaObj.Delete, a, 0)
            result = [new]
        left = set(_names(sap, "AreaObj"))
        merged = [{"area": a, "corners": [[_r(v) for v in _xyz(sap, q)] for q in _corners(sap, a)],
                   **_label(sap, p.software, "AreaObj", a)} for a in result if a in left]
    _refresh_view(sap)
    out = {**plan, "merged": merged, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if p.software != "SAP2000":
        out["not_copied"] = AREA_NOT_COPIED
    if len(merged) != 1:
        out["message"] = f"Expected one merged area, the program reports {len(merged)}."
        raise ActionFailed(out)
    return out


# --------------------------------------------------------------------------- expand / shrink (SAP2000)

class ExpandP(_EditP):
    areas: list[str] = Field(min_length=1, max_length=MAX, description="area names")
    offset: float = Field(description="edge offset, length units: + expands every edge outward, - shrinks")

    @model_validator(mode="after")
    def _nz(self) -> "ExpandP":
        if self.offset == 0:
            raise ValueError("offset must not be 0")
        return self


@action("expand_shrink_areas", "Offset every edge of areas outward (+) or inward (-) by a distance (SAP2000's expand / "
        "shrink). Answers with the new corner coordinates.", "Geometry", "edit", ExpandP, programs="S")
def expand_shrink_areas(p: ExpandP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        areas = _targets(sap, "AreaObj", p.areas, None, "areas")
        before = {a: [[_r(v) for v in _xyz(sap, q)] for q in _corners(sap, a)] for a in areas[:50]}
        plan = {"areas": len(areas), "offset": p.offset, "units": describe_units(p.units)}
        if p.dry_run:
            return _dry({**plan, "corners_now": before}, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_expand_shrink", "expand / shrink areas")
        saved = _selection(sap)
        try:
            _select_only(sap, {"areas": areas})
            api_call("EditArea.ExpandShrink", sap.EditArea.ExpandShrink, 0, p.offset)
        finally:
            _restore(sap, saved)
        after = {a: [[_r(v) for v in _xyz(sap, q)] for q in _corners(sap, a)] for a in areas[:50]}
    _refresh_view(sap)
    out = {**plan, "corners": {a: {"before": before[a], "after": after[a]} for a in after},
           "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    unchanged = [a for a in after if after[a] == before[a]]
    if unchanged:
        out["message"] = f"{len(unchanged)} areas did not change."
        raise ActionFailed(out)
    return out


# --------------------------------------------------------------------------- point coordinates

class PointMove(Params):
    point: str = Field(description="point name")
    xyz: list[float] = Field(min_length=3, max_length=3, description="[x, y, z] new coordinates, length units")


class PointCoordsP(_EditP):
    mode: Literal["move_to", "align"] = Field(description=(
        "move_to: each point to its given coordinates | align: the points' x, y or z set to one ordinate"))
    moves: list[PointMove] = Field(default=[], max_length=MAX, description="move_to: [{point, xyz}, ...]")
    points: list[str] = Field(default=[], max_length=MAX, description="align: the points")
    axis: Literal["x", "y", "z"] | None = Field(default=None, description="align: the coordinate to set")
    ordinate: float | None = Field(default=None, description="align: the value, length units")

    @model_validator(mode="after")
    def _check(self) -> "PointCoordsP":
        if self.mode == "move_to" and not self.moves:
            raise ValueError("move_to needs moves")
        if self.mode == "align" and (not self.points or self.axis is None or self.ordinate is None):
            raise ValueError("align needs points, axis and ordinate")
        return self


@action("set_point_coordinates", "Move points to given coordinates, or align points on one x, y or z ordinate; "
        "frames and areas connected to a point follow it. [[S:SAP2000 EditPoint.ChangeCoordinates.]][[EF:ETABS / SAFE: "
        "each point is moved alone with the move tool.]] Answers with every point's coordinates before and after.",
        "Geometry", "edit", PointCoordsP)
def set_point_coordinates(p: PointCoordsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        names = [m.point for m in p.moves] if p.mode == "move_to" else list(p.points)
        _targets(sap, "PointObj", names, None, "points")
        before = {n: _xyz(sap, n) for n in names}
        k = {"x": 0, "y": 1, "z": 2}.get(p.axis or "x", 0)
        target = {m.point: tuple(m.xyz) for m in p.moves} if p.mode == "move_to" else {
            n: tuple(p.ordinate if i == k else before[n][i] for i in range(3)) for n in names}
        moving = {n: t for n, t in target.items() if not _close_xyz(before[n], t, 1e-12)}
        plan = {"mode": p.mode, "points": len(names), "moving": len(moving), "units": describe_units(p.units),
                "targets": {n: [_r(v) for v in t] for n, t in list(target.items())[:100]}}
        if p.mode == "align":
            plan.update(axis=p.axis, ordinate=p.ordinate)
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_point_coordinates", "move points")
        if p.software == "SAP2000":
            for n, t in moving.items():
                api_call(f"EditPoint.ChangeCoordinates_1('{n}')", sap.EditPoint.ChangeCoordinates_1, n, *t, False)
        else:
            saved = _selection(sap)
            try:
                for n, t in moving.items():
                    _select_only(sap, {"points": [n]})
                    api_call(f"EditGeneral.Move('{n}')", sap.EditGeneral.Move, *_sub(t, before[n]))
            finally:
                _restore(sap, saved)
        left = set(_names(sap, "PointObj"))
        after = {n: _xyz(sap, n) for n in names if n in left}
    _refresh_view(sap)
    rows = [{"point": n, "before": [_r(v) for v in before[n]],
             "after": [_r(v) for v in after[n]] if n in after else None} for n in names[:500]]
    out = {**plan, "points_read_back": rows, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    wrong = [n for n in moving if n not in after or not _close_xyz(after[n], target[n])]
    if wrong:
        out["message"] = f"{len(wrong)} points are not at their target (merged or refused): {wrong[:20]}"
        raise ActionFailed(out)
    return out


