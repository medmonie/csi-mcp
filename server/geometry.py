"""Phase 4: geometry operations - move, replicate, merge points, extrude, change connectivity (divide_frames: geometry89).

The CSI edit tools work on the selection, so these actions save the user's selection, select the targets, run the
tool and restore the selection. Every answer reads the geometry back (point coordinates / new object names).
SAP2000 has the full EditGeneral / EditFrame / EditPoint set. ETABS and SAFE have only Move and frame connectivity;
replicate there is a linear copy made with the create calls (what is copied is listed in the answer).
"""
from __future__ import annotations

import logging
import sys
import contextlib
from typing import Any, Literal

from pydantic import Field, model_validator

from actions import _dry, _refresh_view, action, bridge
from csi_bridge import CSIValidationError, api_call, as_list, describe_units
from model_read import _names, _try
from modelling import _start_edit, _targets
from objects import MAX, _EditP

TYPE_CODE = {1: "points", 2: "frames", 5: "areas"}


# --------------------------------------------------------------------------- helpers

class _TargetsP(_EditP):
    points: list[str] = Field(default=[], max_length=MAX, description="point names")
    frames: list[str] = Field(default=[], max_length=MAX, description="frame names")
    areas: list[str] = Field(default=[], max_length=MAX, description="area names")
    group: str | None = Field(default=None, description="or a group: all its points, frames and areas")

    @model_validator(mode="after")
    def _one_way(self) -> "_TargetsP":
        if self.group and (self.points or self.frames or self.areas):
            raise ValueError("give names or a group, not both")
        if not (self.group or self.points or self.frames or self.areas):
            raise ValueError("give points, frames and / or areas, or a group")
        return self


def _collect(sap: Any, p: _TargetsP) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for kind, coll in (("points", "PointObj"), ("frames", "FrameObj"), ("areas", "AreaObj")):
        names = getattr(p, kind)
        if p.group:
            try:
                out[kind] = _targets(sap, coll, None, p.group, kind)
            except CSIValidationError as exc:
                if "has no" not in str(exc):
                    raise
                out[kind] = []
        elif names:
            out[kind] = _targets(sap, coll, names, None, kind)
        else:
            out[kind] = []
    return out


def _selection(sap: Any) -> list[tuple[int, str]]:
    g = api_call("SelectObj.GetSelected", sap.SelectObj.GetSelected)
    if len(g) != 3 or len(as_list(g[1])) != g[0] or len(as_list(g[2])) != g[0]:
        raise CSIValidationError("Cannot snapshot the current object selection.")
    selected = [(int(t), str(n)) for t, n in zip(as_list(g[1]), as_list(g[2]))]
    if any(t not in TYPE_CODE for t, _ in selected):
        raise CSIValidationError("The current selection contains an object this action cannot restore.")
    return selected


def _select_only(sap: Any, objs: dict[str, list[str]]) -> None:
    api_call("SelectObj.ClearSelection", sap.SelectObj.ClearSelection)
    for kind, coll in (("points", "PointObj"), ("frames", "FrameObj"), ("areas", "AreaObj")):
        obj = getattr(sap, coll)
        for n in objs.get(kind, []):
            api_call(f"{coll}.SetSelected", obj.SetSelected, n, True)


def _restore(sap: Any, saved: list[tuple[int, str]]) -> None:
    """Put the user's selection back and verify it. During a failed edit a restore problem is logged, not raised,
    so the edit's own error is the one reported."""
    if sys.exc_info()[0] is not None:
        try:
            _restore_checked(sap, saved)
        except Exception as exc:  # noqa: BLE001
            logging.getLogger("csi.geometry").warning("selection not restored after a failed edit: %s", exc)
        return
    _restore_checked(sap, saved)


def _restore_checked(sap: Any, saved: list[tuple[int, str]]) -> None:
    api_call("Restore object selection", sap.SelectObj.ClearSelection)
    expected = []
    for t, collection in ((1, "PointObj"), (2, "FrameObj"), (5, "AreaObj")):
        names = [n for typ, n in saved if typ == t]
        if names:
            existing = set(_names(sap, collection))
            for n in names:
                if n in existing:  # a merge can intentionally remove a selected point
                    api_call("Restore object selection", getattr(sap, collection).SetSelected, n, True)
                    expected.append((t, n))
    if set(_selection(sap)) != set(expected):
        raise CSIValidationError("CSI did not confirm the restored selection. Inspect the edit before retrying.")


def _coords(sap: Any, names: list[str] | None = None) -> dict[str, tuple[float, float, float]]:
    out = {}
    for n in names if names is not None else _names(sap, "PointObj"):
        c = _try(sap, "PointObj.GetCoordCartesian", n, 0.0, 0.0, 0.0)
        if c:
            out[n] = tuple(float(v) for v in c[:3])
    return out


def _new_names(before: dict[str, set[str]], sap: Any) -> dict[str, list[str]]:
    return {k: sorted(set(_names(sap, c)) - before[k], key=_natural)
            for k, c in (("points", "PointObj"), ("frames", "FrameObj"), ("areas", "AreaObj"))}


def _natural(s: str) -> tuple:
    return (0, int(s), "") if s.isdigit() else (1, 0, s)


def _all_names(sap: Any) -> dict[str, set[str]]:
    return {k: set(_names(sap, c)) for k, c in (("points", "PointObj"), ("frames", "FrameObj"), ("areas", "AreaObj"))}


def _points_of(sap: Any, objs: dict[str, list[str]]) -> list[str]:
    pts = set(objs.get("points", []))
    for f in objs.get("frames", []):
        g = _try(sap, "FrameObj.GetPoints", f)
        if g:
            pts.update((str(g[0]), str(g[1])))
    for a in objs.get("areas", []):
        g = _try(sap, "AreaObj.GetPoints", a)
        if g:
            pts.update(str(x) for x in as_list(g[1]))
    return sorted(pts, key=_natural)


# --------------------------------------------------------------------------- move

class MoveP(_TargetsP):
    offset: list[float] = Field(min_length=3, max_length=3, description="[dx, dy, dz] in the length unit of `units`")


@action("move_objects", "Move points, frames and areas (names or a group) by [dx, dy, dz]. Moving a frame or area "
        "moves its points, so objects that share those points stretch with them. Answers with the coordinates of "
        "every point that moved, before and after.", "Geometry", "edit", MoveP)
def move_objects(p: MoveP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        objs = _collect(sap, p)
        pts = _points_of(sap, objs)
        before = _coords(sap, pts)
        plan = {"offset": p.offset, "objects": {k: len(v) for k, v in objs.items()}, "points_moving": len(pts),
                "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        saved = _selection(sap)
        snap, was_locked = _start_edit(p, sap, "before_move", "move objects")
        try:
            _select_only(sap, {**objs, "points": pts})   # the points too: ETABS / SAFE move points, not lines
            api_call("EditGeneral.Move", sap.EditGeneral.Move, *p.offset)
        finally:
            _restore(sap, saved)
        after = _coords(sap, pts)
    moved = [{"point": n, "before": before.get(n), "after": after.get(n)} for n in pts if before.get(n) != after.get(n)]
    if pts and any(abs(v) > 0 for v in p.offset) and not moved:
        raise CSIValidationError("The program reported success but no point moved.",
                                 hint="Check the model is not locked and the objects are not restricted; "
                                      "get_objects shows the coordinates.")
    _refresh_view(sap)
    return {**plan, "points_moved": len(moved), "moved": moved[:200], "truncated": len(moved) > 200,
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- replicate

class ReplicateP(_TargetsP):
    mode: Literal["linear", "radial", "mirror"] = Field(default="linear", description=(
        "linear: copies at offset, 2 x offset ...[[S: | radial: copies rotated about an axis | mirror: one mirrored "
        "copy]]"))
    offset: list[float] | None = Field(default=None, min_length=3, max_length=3, description="linear: [dx, dy, dz]")
    number: int = Field(default=1, ge=1, le=500, description="linear / radial: number of copies")
    axis: Literal["x", "y", "z"] | None = Field(default=None, description="radial: axis parallel to X, Y or Z")
    axis_point: list[float] | None = Field(default=None, min_length=3, max_length=3, description=(
        "radial: a point on the axis"))
    angle: float | None = Field(default=None, description="radial: angle between copies (degrees)")
    mirror_plane: Literal["parallel_z", "parallel_x", "parallel_y"] | None = Field(default=None, description=(
        "mirror: the plane is parallel to this axis and passes through point_1 and point_2"))
    point_1: list[float] | None = Field(default=None, min_length=3, max_length=3, description="mirror: [x, y, z]")
    point_2: list[float] | None = Field(default=None, min_length=3, max_length=3, description="mirror: [x, y, z]")

    @model_validator(mode="after")
    def _mode(self) -> "ReplicateP":
        need = {"linear": ("offset",), "radial": ("axis", "axis_point", "angle"),
                "mirror": ("mirror_plane", "point_1", "point_2")}[self.mode]
        miss = [f for f in need if getattr(self, f) is None]
        if miss:
            raise ValueError(f"{self.mode} needs {', '.join(miss)}")
        return self


def _copy_restraint(sap: Any, src: str, dst: str) -> None:
    r = _try(sap, "PointObj.GetRestraint", src) or _try(sap, "PointObj.GetRestraint", src, [False] * 6)
    if not r:
        raise CSIValidationError(f"Could not read the restraint of point {src} to copy it.")
    want = [bool(v) for v in as_list(r[0])]
    if not any(want):
        return
    api_call("PointObj.SetRestraint", sap.PointObj.SetRestraint, dst, want)
    back = _try(sap, "PointObj.GetRestraint", dst) or _try(sap, "PointObj.GetRestraint", dst, [False] * 6)
    if not back or [bool(v) for v in as_list(back[0])] != want:
        raise CSIValidationError(f"The restraint copied to point {dst} reads back as "
                                 f"{back and list(as_list(back[0]))}, not {want}.")


def _copy_linear(sap: Any, sw: str, objs: dict[str, list[str]], d: list[float], n: int) -> tuple[dict, list[str]]:
    """ETABS / SAFE: no replicate API - copy points, frames, areas with the create calls."""
    made: dict[str, list[str]] = {"points": [], "frames": [], "areas": []}
    pts = _points_of(sap, objs)
    xyz = _coords(sap, pts)
    for k in range(1, n + 1):
        off = [k * v for v in d]
        pmap: dict[str, str] = {}

        def point(name: str) -> str:
            if name not in pmap:
                x, y, z = (xyz[name][i] + off[i] for i in range(3))
                o = api_call("PointObj.AddCartesian", sap.PointObj.AddCartesian, x, y, z, "", "", "Global")
                pmap[name] = str(o[0])
                _copy_restraint(sap, name, pmap[name])
            return pmap[name]

        for q in objs.get("points", []):
            made["points"].append(point(q))
        for f in objs.get("frames", []):
            g = api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, f)
            sec = api_call("FrameObj.GetSection", sap.FrameObj.GetSection, f)[0]
            o = api_call("FrameObj.AddByPoint", sap.FrameObj.AddByPoint, point(str(g[0])), point(str(g[1])), "",
                         str(sec), "")
            new = str(o[0])
            made["frames"].append(new)
            rel = _try(sap, "FrameObj.GetReleases", f, [False] * 6, [False] * 6, [0.0] * 6, [0.0] * 6)
            if rel and (any(as_list(rel[0])) or any(as_list(rel[1]))):
                api_call("FrameObj.SetReleases", sap.FrameObj.SetReleases, new, list(as_list(rel[0])),
                         list(as_list(rel[1])), list(as_list(rel[2])), list(as_list(rel[3])))
            ax = _try(sap, "FrameObj.GetLocalAxes", f)
            if ax and float(ax[0]):
                api_call("FrameObj.SetLocalAxes", sap.FrameObj.SetLocalAxes, new, float(ax[0]))
        for a in objs.get("areas", []):
            g = api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, a)
            corners = [str(x) for x in as_list(g[1])]
            prop = api_call("AreaObj.GetProperty", sap.AreaObj.GetProperty, a)[0]
            c = [xyz[q] if q in xyz else _coords(sap, [q])[q] for q in corners]
            xs, ys, zs = ([c[i][j] + off[j] for i in range(len(c))] for j in range(3))
            o = api_call("AreaObj.AddByCoord", sap.AreaObj.AddByCoord, len(c), xs, ys, zs, "", str(prop), "",
                         "Global")
            new = next((str(x) for x in reversed(o) if isinstance(x, str) and x), "")
            made["areas"].append(new)
            # AddByCoord makes the corner points itself: give each the restraint of the original corner
            g2 = api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, new)
            for orig, q in zip(corners, [str(x) for x in as_list(g2[1])]):
                if orig not in pmap:
                    pmap[orig] = q
                    _copy_restraint(sap, orig, q)
                    made["points"].append(q)
    not_copied = ["loads", "springs", "groups", "frame end offsets / insertion point / modifiers",
                  "area assignments other than the section"]
    return made, not_copied


@action("replicate_objects", "Copy points, frames and areas (names or a group): linear (n copies at an offset)"
        "[[S:, radial (n copies rotated about an axis) or mirror]]. [[S:SAP2000 copies every assignment and load.]]"
        "[[EF:ETABS / SAFE have no replicate API: the copies are made with the create calls and carry the section, "
        "restraints, frame releases and local axis angle; loads, springs and other assignments are listed as not "
        "copied.]] Answers with the new object names.", "Geometry", "edit", ReplicateP,
        options={"mode": {"radial": "S", "mirror": "S"}})
def replicate_objects(p: ReplicateP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        objs = _collect(sap, p)
        plan = {"mode": p.mode, "objects": {k: len(v) for k, v in objs.items()},
                "copies": 1 if p.mode == "mirror" else p.number, "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        saved = _selection(sap) if p.software == "SAP2000" else []
        before = _all_names(sap)
        snap, was_locked = _start_edit(p, sap, "before_replicate", "replicate objects")
        extra: dict[str, Any] = {}
        if p.software == "SAP2000":
            try:
                _select_only(sap, objs)
                if p.mode == "linear":
                    api_call("EditGeneral.ReplicateLinear", sap.EditGeneral.ReplicateLinear, *p.offset, p.number,
                             0, [], [], False)
                elif p.mode == "radial":
                    code = {"x": 1, "y": 2, "z": 3}[p.axis]
                    x, y, z = p.axis_point
                    api_call("EditGeneral.ReplicateRadial", sap.EditGeneral.ReplicateRadial, code, x, y, z, x, y, z,
                             p.number, p.angle, 0, [], [], False)
                else:
                    code = {"parallel_z": 1, "parallel_x": 2, "parallel_y": 3}[p.mirror_plane]
                    api_call("EditGeneral.ReplicateMirror", sap.EditGeneral.ReplicateMirror, code, *p.point_1,
                             *p.point_2, 0.0, 0.0, 0.0, 0, [], [], False)
            finally:
                _restore(sap, saved)
        else:
            _, extra["not_copied"] = _copy_linear(sap, p.software, objs, p.offset, p.number)
        new = _new_names(before, sap)
        new_xyz = _coords(sap, new["points"][:200])
    _refresh_view(sap)
    return {**plan, "new": {k: v[:500] for k, v in new.items()}, "new_counts": {k: len(v) for k, v in new.items()},
            "new_point_coordinates": new_xyz, **extra, "model_was_unlocked": was_locked,
            **({"snapshot": snap} if snap else {})}


# divide_frames moved to geometry89.py (0.24.0: ETABS / SAFE added)


# --------------------------------------------------------------------------- merge points (SAP2000)

class MergeP(_EditP):
    points: list[str] | None = Field(default=None, max_length=MAX, description="points to consider (default all)")
    tolerance: float = Field(gt=0, description="points closer than this are merged (length units)")


def _close_pairs(xyz: dict[str, tuple[float, float, float]], tol: float, preview: bool = False) -> list[tuple[str, str]]:
    """Pairs closer than tol (sorted sweep on x)."""
    items = sorted(xyz.items(), key=lambda kv: kv[1][0])
    out = []
    comparisons = 0
    for i, (a, ca) in enumerate(items):
        for j in range(i + 1, len(items)):
            b, cb = items[j]
            if cb[0] - ca[0] >= tol:
                break
            comparisons += 1
            if comparisons > 2_000_000:
                raise CSIValidationError("Merge search exceeds the comparison limit. Select a smaller set of points.")
            if sum((ca[k] - cb[k]) ** 2 for k in range(3)) ** 0.5 < tol:
                out.append((a, b))
                if preview and len(out) == 200:
                    return out
                if len(out) > 5000:
                    raise CSIValidationError("Too many merge pairs. Select a smaller set of points.")
    return out


@action("merge_points", "Merge points that lie closer together than a tolerance (all points or a list). Answers "
        "with the points removed by the merge.", "Geometry", "edit", MergeP, programs="S")
def merge_points(p: MergeP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        pts = _targets(sap, "PointObj", p.points, None, "points") if p.points else _names(sap, "PointObj")
        plan = {"points_considered": len(pts), "tolerance": p.tolerance, "units": describe_units(p.units)}
        if p.dry_run:
            close = [list(x) for x in _close_pairs(_coords(sap, pts), p.tolerance, preview=True)]
            return _dry({**plan, "close_pairs": close}, model_locked=bridge.is_locked(sap))
        pairs = _close_pairs(_coords(sap, pts), p.tolerance)
        if not pairs:
            return {**plan, "close_pairs": 0, "merged": [], "points_removed": [], "points_removed_count": 0,
                    "model_was_unlocked": False}
        before = set(_names(sap, "PointObj"))
        saved = _selection(sap)
        snap, was_locked = _start_edit(p, sap, "before_merge_points", "merge points")
        merged = []
        # SAP2000 27.1 (measured): Merge with all points selected merges nothing; with the close pair selected it
        # merges them but returns 1. So pair by pair, judged by which points are gone.
        try:
            for a, b in pairs:
                now = set(_names(sap, "PointObj"))
                if a not in now or b not in now:
                    continue
                _select_only(sap, {"points": [a, b]})
                with contextlib.suppress(Exception):
                    sap.EditPoint.Merge(p.tolerance, 0, [])
                after = set(_names(sap, "PointObj"))
                gone = [q for q in (a, b) if q not in after]
                if gone:
                    merged.append({"removed": gone[0], "kept": b if gone[0] == a else a})
        finally:
            _restore(sap, saved)
        removed = sorted(before - set(_names(sap, "PointObj")), key=_natural)
        if pairs and not removed:
            raise CSIValidationError(f"{len(pairs)} close pair(s) found but the program merged none.",
                                     hint=f"Pairs: {pairs[:10]}")
    _refresh_view(sap)
    return {**plan, "close_pairs": len(pairs), "merged": merged[:500], "points_removed": removed[:500],
            "points_removed_count": len(removed),
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- extrude (SAP2000)

class ExtrudeP(_EditP):
    kind: Literal["points_to_frames", "frames_to_areas"] = Field(description=(
        "points_to_frames: a line of frames from each point | frames_to_areas: a strip of areas from each frame"))
    names: list[str] = Field(min_length=1, max_length=MAX, description="the points or frames to extrude")
    section: str = Field(description="frame section (points_to_frames) or area section (frames_to_areas)")
    offset: list[float] = Field(min_length=3, max_length=3, description="[dx, dy, dz] of each new piece")
    number: int = Field(default=1, ge=1, le=1000, description="pieces per point / frame")
    remove_frames: bool = Field(default=False, description="frames_to_areas: delete the frames afterwards")


@action("extrude_objects", "Extrude points into lines of frames, or frames into strips of areas, by an offset "
        "repeated `number` times. Answers with the new objects.", "Geometry", "edit", ExtrudeP, programs="S")
def extrude_objects(p: ExtrudeP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    to_frames = p.kind == "points_to_frames"
    coll = "PointObj" if to_frames else "FrameObj"
    with bridge.units_scope(sap, p.units):
        names = _targets(sap, coll, p.names, None, "points" if to_frames else "frames")
        props = bridge.try_name_list(sap, ["PropFrame" if to_frames else "PropArea"])[0] or []
        if p.section not in props:
            raise CSIValidationError(f"{'Frame' if to_frames else 'Area'} section '{p.section}' is not defined.",
                                     hint=f"Defined: {props[:30]}")
        plan = {"kind": p.kind, "objects": len(names), "pieces_each": p.number, "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_extrude", "extrude objects")
        made: dict[str, list[str]] = {}
        for n in names:
            if to_frames:
                o = api_call(f"EditGeneral.ExtrudePointToFrameLinear('{n}')",
                             sap.EditGeneral.ExtrudePointToFrameLinear, n, p.section, *p.offset, p.number, [])
            else:
                o = api_call(f"EditGeneral.ExtrudeFrameToAreaLinear('{n}')", sap.EditGeneral.ExtrudeFrameToAreaLinear,
                             n, p.section, *p.offset, p.number, [], p.remove_frames)
            made[n] = [str(x) for x in as_list(o[-1])] if o else []
    _refresh_view(sap)
    return {**plan, "new": {k: v[:200] for k, v in list(made.items())[:200]},
            "new_total": sum(len(v) for v in made.values()), "model_was_unlocked": was_locked,
            **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- connectivity

class ConnectP(_EditP):
    frame: str | None = Field(default=None, description="the frame to reconnect")
    area: str | None = Field(default=None, description="[[S:the area to reconnect]]")
    points: list[str] = Field(min_length=2, max_length=64, description=(
        "frame: [new I point, new J point] | area: its new corner points in order"))

    @model_validator(mode="after")
    def _one(self) -> "ConnectP":
        if (self.frame is None) == (self.area is None):
            raise ValueError("give frame or area")
        if self.frame is not None and len(self.points) != 2:
            raise ValueError("a frame takes two points")
        if self.area is not None and len(self.points) < 3:
            raise ValueError("an area takes at least three points")
        return self


@action("change_connectivity", "Reconnect a frame to two other existing points[[S:, or an area to other corner "
        "points]]. Answers with the new end / corner coordinates.", "Geometry", "edit", ConnectP,
        fields={"area": "S"})
def change_connectivity(p: ConnectP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        if p.frame is not None:
            _targets(sap, "FrameObj", [p.frame], None, "frames")
        else:
            _targets(sap, "AreaObj", [p.area], None, "areas")
        missing = sorted(set(p.points) - set(_names(sap, "PointObj")))
        if missing:
            raise CSIValidationError(f"Points not in the model: {missing}.")
        if len(set(p.points)) != len(p.points):
            raise CSIValidationError("Each point only once.")
        plan = {"frame": p.frame, "area": p.area, "points": p.points}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_connectivity", "change connectivity")
        if p.frame is not None:
            api_call("EditFrame.ChangeConnectivity", sap.EditFrame.ChangeConnectivity, p.frame, *p.points)
            g = api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, p.frame)
            now = [str(g[0]), str(g[1])]
        else:
            api_call("EditArea.ChangeConnectivity", sap.EditArea.ChangeConnectivity, p.area, len(p.points),
                     list(p.points))
            g = api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, p.area)
            now = [str(x) for x in as_list(g[1])]
        if now != list(p.points):
            raise CSIValidationError(f"The program reports points {now} after the change, not {p.points}.")
        xyz = _coords(sap, now)
    _refresh_view(sap)
    return {**plan, "connected_to": [{"point": n, "xyz": xyz.get(n)} for n in now], "model_was_unlocked": was_locked,
            **({"snapshot": snap} if snap else {})}

