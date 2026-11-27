"""Phase 1: full model read (read only). Signatures from docs/api (gen_api_matrix.py).

ETABS and SAFE have bulk readers (PointObj.GetAllPoints, FrameObj.GetAllFrames, AreaObj.GetAllAreas,
PropFrame.GetAllFrameProperties_2); SAP2000 does not, so it is read object by object. Every list longer than
max_rows also goes in full to a CSV in the output folder (export_path), like query_database_table.

Actions: get_grids, get_stories, get_groups, get_objects, get_object_assignments, get_definition_details,
get_load_case_details, get_combo_details, get_mass_source, get_selected, export_model_snapshot.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import json
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

import safety
from actions import Params, SOFTWARE, UNITS, _read_selected, action, bridge
from csi_bridge import LOAD_CASE_TYPES, LOAD_PATTERN_TYPES, COMError, CSIError, CSIValidationError, Software, \
    UnitPreset, api_call, as_list, describe_units
from table_tools import TableError, records, rows_to_dataframe

MAT_TYPES = {1: "Steel", 2: "Concrete", 3: "NoDesign", 4: "Aluminum", 5: "ColdFormed", 6: "Rebar", 7: "Tendon",
             8: "Masonry"}
FRAME_PROP_TYPES = {1: "I", 2: "Channel", 3: "Tee", 4: "Angle", 5: "DoubleAngle", 6: "Box", 7: "Pipe",
                    8: "Rectangular", 9: "Circle", 10: "General", 11: "DoubleChannel", 12: "Auto", 13: "SD",
                    14: "Variable", 15: "Joist", 16: "Bridge"}
COMBO_TYPES = {0: "LinearAdd", 1: "Envelope", 2: "AbsAdd", 3: "SRSS", 4: "RangeAdd"}
OBJECT_TYPES = {1: "point", 2: "frame", 3: "cable", 4: "tendon", 5: "area", 6: "solid", 7: "link"}
DOF = ("U1", "U2", "U3", "R1", "R2", "R3")
LOAD_DIRS = {1: "Local1", 2: "Local2", 3: "Local3", 4: "X", 5: "Y", 6: "Z", 7: "ProjectedX", 8: "ProjectedY",
             9: "ProjectedZ", 10: "Gravity", 11: "ProjectedGravity"}
MAX_ROWS = Field(default=200, ge=1, le=5000, description="rows returned per list, default 200; longer lists also go "
                                                         "in full to a CSV (export_path)")


# --------------------------------------------------------------------------- helpers

def _try(sap: Any, method: str, *args: Any) -> list[Any] | None:
    """api_call of SapModel.<method> ('PointObj.GetSpring') answering None instead of raising: optional data that a
    program or a build may not have (missing method, non-zero return code, COM error)."""
    try:
        func = sap
        for part in method.split("."):
            func = getattr(func, part)
        return api_call(method, func, *args)
    except (CSIError, COMError, AttributeError, TypeError, IndexError, OSError):
        return None


def _names(sap: Any, collection: str) -> list[str]:
    return bridge.name_list(sap, collection)


def _r(v: Any, nd: int = 6) -> Any:
    """Rounded to nd decimals; small magnitudes (unit weights, coefficients) keep 7 significant digits."""
    if not isinstance(v, (int, float)):
        return v
    f = float(v)
    if f and abs(f) < 10.0 ** (3 - nd):
        return float(f"{f:.7g}")
    return round(f, nd)


def _out(df, items_key: str, max_rows: int, stem: str) -> dict[str, Any]:
    """Records (first max_rows) + export of the whole list when cut."""
    path = None
    if len(df) > max_rows:
        path = str(safety.export_frame(df, stem, "csv"))
    return {"count": int(len(df)), items_key: records(df, max_rows), "truncated": len(df) > max_rows,
            "export_path": path}


def _frame(rows: list[dict[str, Any]]):
    import pandas as pd
    return pd.DataFrame(rows)


def _labels(sap: Any, collection: str) -> dict[str, tuple[str, str]]:
    """ETABS / SAFE: name -> (label, story) in one call."""
    outs = _try(sap, f"{collection}.GetLabelNameList")
    if not outs:
        return {}
    return {str(n): (str(lab), str(st)) for n, lab, st in zip(as_list(outs[1]), as_list(outs[2]), as_list(outs[3]))}


def _restraint_text(values: list[bool]) -> str:
    if all(values):
        return "fixed"
    if list(values[:3]) == [True, True, True] and not any(values[3:]):
        return "pinned"
    return " ".join(d for d, v in zip(DOF, values) if v)


def _polygon_area(xyz: list[tuple[float, float, float]]) -> float:
    """Area of a planar polygon in 3-D (Newell's method)."""
    nx = ny = nz = 0.0
    for i, (x1, y1, z1) in enumerate(xyz):
        x2, y2, z2 = xyz[(i + 1) % len(xyz)]
        nx += (y1 - y2) * (z1 + z2)
        ny += (z1 - z2) * (x1 + x2)
        nz += (x1 - x2) * (y1 + y2)
    return 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)


def _orientation(p1: tuple[float, float, float], p2: tuple[float, float, float]) -> str:
    dx, dy, dz = (p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2])
    horiz = math.hypot(dx, dy)
    if horiz < 1e-6:
        return "vertical"
    if abs(dz) < 1e-6:
        return "horizontal"
    return "inclined"


# --------------------------------------------------------------------------- geometry readers (also used by report)

def read_points(sap: Any, software: str) -> list[dict[str, Any]]:
    labels = _labels(sap, "PointObj") if software != "SAP2000" else {}
    outs = _try(sap, "PointObj.GetAllPoints") if software != "SAP2000" else None
    if outs:
        data = zip(as_list(outs[1]), as_list(outs[2]), as_list(outs[3]), as_list(outs[4]))
    else:
        data = []
        for n in _names(sap, "PointObj"):
            c = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, n, 0.0, 0.0, 0.0)
            data.append((n, c[0], c[1], c[2]))
    rows = []
    for n, x, y, z in data:
        r = api_call("PointObj.GetRestraint", sap.PointObj.GetRestraint, str(n))
        vals = [bool(v) for v in as_list(r[0])] if r else [False] * 6
        row = {"name": str(n), "x": _r(x), "y": _r(y), "z": _r(z), "restraint": _restraint_text(vals) if any(vals) else ""}
        if str(n) in labels:
            row["label"], row["story"] = labels[str(n)]
        rows.append(row)
    return rows


def read_frames(sap: Any, software: str, points: dict[str, tuple[float, float, float]] | None = None) -> list[dict]:
    labels = _labels(sap, "FrameObj") if software != "SAP2000" else {}
    rows = []
    outs = _try(sap, "FrameObj.GetAllFrames") if software != "SAP2000" else None
    if outs:
        cols = [as_list(outs[i]) for i in range(1, 13)]
        for n, prop, story, pt1, pt2, x1, y1, z1, x2, y2, z2, ang in zip(*cols):
            rows.append({"name": str(n), "section": str(prop), "point1": str(pt1), "point2": str(pt2),
                         "p1": (x1, y1, z1), "p2": (x2, y2, z2), "angle": ang, "story": str(story)})
    else:
        if points is None:
            points = {p["name"]: (p["x"], p["y"], p["z"]) for p in read_points(sap, software)}
        for n in _names(sap, "FrameObj"):
            pt1, pt2 = api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, n, "", "")[:2]
            sec = api_call("FrameObj.GetSection", sap.FrameObj.GetSection, n)[0]
            rows.append({"name": n, "section": str(sec), "point1": str(pt1), "point2": str(pt2),
                         "p1": points[str(pt1)], "p2": points[str(pt2)], "angle": None, "story": None})
    out = []
    for r in rows:
        (x1, y1, z1), (x2, y2, z2) = r["p1"], r["p2"]
        rec = {"name": r["name"], "section": r["section"], "point1": r["point1"], "point2": r["point2"],
               "x1": _r(x1), "y1": _r(y1), "z1": _r(z1), "x2": _r(x2), "y2": _r(y2), "z2": _r(z2),
               "length": _r(math.dist(r["p1"], r["p2"])), "orientation": _orientation(r["p1"], r["p2"])}
        if r["name"] in labels:
            rec["label"], rec["story"] = labels[r["name"]]
        elif r.get("story"):
            rec["story"] = r["story"]
        out.append(rec)
    return out


def read_areas(sap: Any, software: str, points: dict[str, tuple[float, float, float]] | None = None) -> list[dict]:
    labels = _labels(sap, "AreaObj") if software != "SAP2000" else {}
    polys: list[tuple[str, list[str], list[tuple[float, float, float]]]] = []
    outs = _try(sap, "AreaObj.GetAllAreas") if software != "SAP2000" else None
    if outs:
        names, delim = as_list(outs[1]), [int(d) for d in as_list(outs[4])]
        pnames, xs, ys, zs = (as_list(outs[i]) for i in (5, 6, 7, 8))
        # PointDelimiter: index of each area's last boundary point (0-based); tolerate a count-style (exclusive) end
        exclusive = bool(delim) and delim[-1] == len(pnames)
        start = 0
        for n, end in zip(names, delim):
            stop = end if exclusive else end + 1
            idx = range(start, stop)
            polys.append((str(n), [str(pnames[i]) for i in idx], [(xs[i], ys[i], zs[i]) for i in idx]))
            start = stop
    else:
        if points is None:
            points = {p["name"]: (p["x"], p["y"], p["z"]) for p in read_points(sap, software)}
        for n in _names(sap, "AreaObj"):
            pts = [str(p) for p in as_list(api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, n)[1])]
            polys.append((n, pts, [points[p] for p in pts]))
    out = []
    for n, pts, xyz in polys:
        prop = _try(sap, "AreaObj.GetProperty", n)
        rec = {"name": n, "property": str(prop[0]) if prop else "", "points": " ".join(pts),
               "n_points": len(pts), "area": _r(_polygon_area(xyz)),
               "z_min": _r(min(p[2] for p in xyz)), "z_max": _r(max(p[2] for p in xyz)),
               "coords": [[_r(c) for c in p] for p in xyz]}
        if n in labels:
            rec["label"], rec["story"] = labels[n]
        out.append(rec)
    return out


# --------------------------------------------------------------------------- parameter models

class GridsP(Params):
    software: Software = SOFTWARE
    units: UnitPreset = UNITS


class GroupsP(Params):
    software: Software = SOFTWARE
    include_members: bool = Field(default=False, description="also list each group's objects (default counts only)")
    max_rows: int = MAX_ROWS


class ObjectsP(Params):
    software: Software = SOFTWARE
    object_type: Literal["points", "frames", "areas", "all"] = Field(default="all", description="points | frames | "
                                                                                                "areas | all")
    story: str | None = Field(default=None, description="only objects on this story")
    max_rows: int = MAX_ROWS
    units: UnitPreset = UNITS


class AssignP(Params):
    software: Software = SOFTWARE
    object_type: Literal["points", "frames", "areas"] = Field(description="points | frames | areas")
    names: list[str] | None = Field(default=None, max_length=5000, description="objects to read; default all")
    include_loads: bool = Field(default=True, description="also the loads assigned to the objects")
    non_default_only: bool = Field(default=True, description="leave out values at their defaults (no restraint, "
                                                             "modifiers 1, no releases ...); default true")
    max_objects: int = Field(default=5000, ge=1, le=50000, description="refuse when more objects would be read "
                                                                      "(object by object); filter with names")
    max_rows: int = MAX_ROWS
    units: UnitPreset = UNITS


class DefinitionsDetailP(Params):
    software: Software = SOFTWARE
    kind: Literal["materials", "frame_sections", "area_sections", "springs", "load_patterns", "joint_patterns"] = \
        Field(description="materials | frame_sections | area_sections | [[EF: springs (point / area spring "
                          "properties) | ]]load_patterns[[S: | joint_patterns (joints with a value, min and max)]]")
    names: list[str] | None = Field(default=None, max_length=5000, description="default all")
    max_rows: int = MAX_ROWS
    units: UnitPreset = UNITS


class NamesP(Params):
    software: Software = SOFTWARE
    names: list[str] | None = Field(default=None, max_length=5000, description="default all")
    max_rows: int = MAX_ROWS


class SoftwareOnlyP(Params):
    software: Software = SOFTWARE


class SnapshotJsonP(Params):
    software: Software = SOFTWARE
    output_path: str | None = Field(default=None, description="full path of the .json; default: Documents\\CsiMcp\\"
                                                              "exports\\<model>_model_<date-time>.json")
    include_assignments: bool = Field(default=True, description="also every object's assignments and loads")
    units: UnitPreset = UNITS


# --------------------------------------------------------------------------- actions

@action("get_grids", "Grid systems and grid lines (ID, ordinate, visibility)[[EF:, from the grid API]][[S:, from "
        "the 'Grid Lines' table]].", "Model reading", "read", GridsP)
def get_grids(p: GridsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        if p.software != "SAP2000":
            systems = []
            for g in _names(sap, "GridSys"):
                o = api_call("GridSys.GetGridSys_2", sap.GridSys.GetGridSys_2, g)
                xo, yo, rz, kind = o[0], o[1], o[2], o[3]
                xl = [{"id": str(i), "ordinate": _r(v), "visible": bool(vis)}
                      for i, v, vis in zip(as_list(o[6]), as_list(o[8]), as_list(o[10]))]
                yl = [{"id": str(i), "ordinate": _r(v), "visible": bool(vis)}
                      for i, v, vis in zip(as_list(o[7]), as_list(o[9]), as_list(o[11]))]
                systems.append({"name": g, "type": str(kind), "origin": [_r(xo), _r(yo)], "rotation": _r(rz),
                                "x_lines": xl, "y_lines": yl})
            return {"software": p.software, "units": describe_units(p.units), "grid_systems": systems}
        keys = [t["key"] for t in bridge.available_tables(sap)]
        key = next((k for k in keys if k.lower() == "grid lines"), None) or \
            next((k for k in keys if "grid lines" in k.lower()), None)
        if key is None:
            raise TableError("SAP2000 has no 'Grid Lines' table available.")
        cases = bridge.name_list(sap, "LoadCases")
        combos = bridge.try_name_list(sap, ["RespCombo"])[0] or []
        fields, rows, _, _ = _read_selected(p.software, sap, key, cases, combos)
        df = rows_to_dataframe(fields, rows)
        return {"software": p.software, "units": describe_units(p.units), "source": f"table '{key}'",
                "grid_lines": records(df, 1000)}


@action("get_stories", "Stories: base elevation, name, elevation, height, master story, similar to, splice.",
        "Model reading", "read", GridsP, programs="EF")
def get_stories(p: GridsP) -> dict[str, Any]:
    if p.software == "SAP2000":
        raise CSIValidationError("SAP2000 has no stories.", hint="Use get_grids for its Z grid lines.")
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        o = api_call("Story.GetStories_2", sap.Story.GetStories_2)
    stories = [{"name": str(n), "elevation": _r(e), "height": _r(h), "master": bool(m),
                "similar_to": str(s) if s else "", "splice_above": bool(sa), "splice_height": _r(sh)}
               for n, e, h, m, s, sa, sh in zip(*(as_list(o[i]) for i in range(2, 9)))]
    return {"software": p.software, "units": describe_units(p.units), "base_elevation": _r(o[0]),
            "stories_top_down": list(reversed(stories))}


@action("get_groups", "Groups with the number of points, frames, areas ... in each (members on request).",
        "Model reading", "read", GroupsP)
def get_groups(p: GroupsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    out, member_rows = [], []
    for g in _names(sap, "GroupDef"):
        o = _try(sap, "GroupDef.GetAssignments", g) or [0, (), ()]
        types, names = as_list(o[1]), as_list(o[2])
        counts: dict[str, int] = {}
        for t in types:
            k = OBJECT_TYPES.get(int(t), str(t))
            counts[k] = counts.get(k, 0) + 1
        out.append({"group": g, "objects": len(names), **counts})
        if p.include_members:
            member_rows += [{"group": g, "type": OBJECT_TYPES.get(int(t), str(t)), "name": str(n)}
                            for t, n in zip(types, names)]
    res: dict[str, Any] = {"software": p.software, "groups": out}
    if p.include_members:
        res["members"] = _out(_frame(member_rows), "items", p.max_rows, f"{p.software}_group_members")
    return res


@action("get_objects", "Points (coordinates, support), frames (section, end points and coordinates, length, "
        "vertical / horizontal / inclined) and areas (property, corner points, area, levels)[[EF:, with label and "
        "story]]. Long lists also go to CSV.", "Model reading", "read", ObjectsP, long_running=True,
        fields={"story": "EF"})
def get_objects(p: ObjectsP) -> dict[str, Any]:
    if p.story and p.software == "SAP2000":
        raise CSIValidationError("SAP2000 has no stories; leave story out.")
    sap = bridge.sap_model(p.software)
    res: dict[str, Any] = {"software": p.software, "units": describe_units(p.units)}
    with bridge.units_scope(sap, p.units):
        pts = read_points(sap, p.software)
        coord = {q["name"]: (q["x"], q["y"], q["z"]) for q in pts}
        want = ("points", "frames", "areas") if p.object_type == "all" else (p.object_type,)
        data = {"points": pts}
        if "frames" in want:
            data["frames"] = read_frames(sap, p.software, coord)
        if "areas" in want:
            data["areas"] = [{k: v for k, v in a.items() if k != "coords"} for a in read_areas(sap, p.software, coord)]
    for kind in want:
        rows = data[kind]
        if p.story:
            rows = [r for r in rows if r.get("story") == p.story]
        res[kind] = _out(_frame(rows), "items", p.max_rows, f"{p.software}_{kind}")
    if "points" in want:
        res["supports"] = sum(1 for q in data["points"] if q["restraint"] and (not p.story or q.get("story") == p.story))
    return res


def _point_assign(sap: Any, sw: str, n: str, loads: bool, nd: bool) -> dict[str, Any]:
    a: dict[str, Any] = {"name": n}
    r = _try(sap, "PointObj.GetRestraint", n)
    vals = [bool(v) for v in as_list(r[0])] if r else []
    if vals and (any(vals) or not nd):
        a["restraint"] = _restraint_text(vals)
    k = _try(sap, "PointObj.GetSpring", n)
    if k and (any(as_list(k[0])) or not nd):
        a["spring_K"] = [_r(v) for v in as_list(k[0])]
    if sw != "SAP2000":
        sp = _try(sap, "PointObj.GetSpringAssignment", n)
        if sp and (sp[0] or not nd):
            a["spring_property"] = str(sp[0] or "")
        dia = _try(sap, "PointObj.GetDiaphragm", n)
        if dia and (dia[1] or not nd):
            a["diaphragm"] = str(dia[1] or "")
    m = _try(sap, "PointObj.GetMass", n)
    if m and (any(as_list(m[0])) or not nd):
        a["mass"] = [_r(v) for v in as_list(m[0])]
    g = _try(sap, "PointObj.GetGroupAssign", n)
    groups = [str(x) for x in as_list(g[1])] if g else []
    groups = [x for x in groups if x.upper() != "ALL"]
    if groups or not nd:
        a["groups"] = groups
    if sw == "SAP2000":
        from programs import sap2000 as sap_api
        vals = {pat: sap_api.pattern_value(sap, n, pat) for pat in _joint_patterns(sap)}
        vals = {k: _r(v) for k, v in vals.items() if v is not None and (abs(v) > 0 or not nd)}
        if vals:
            a["joint_pattern_values"] = vals
    if loads:
        f = _try(sap, "PointObj.GetLoadForce", n)
        if f and f[0]:
            a["loads"] = [{"pattern": str(pat), "csys": str(cs), "F1_F2_F3_M1_M2_M3": [_r(v) for v in vals6]}
                          for pat, cs, *vals6 in zip(as_list(f[2]), as_list(f[4]), *(as_list(f[i]) for i in range(5, 11)))]
    return a


_PATTERN_CACHE: dict[int, list[str]] = {}
TEMP_KIND = {1: "uniform", 2: "gradient_2", 3: "gradient_3"}


def _joint_patterns(sap: Any) -> list[str]:
    """SAP2000 joint pattern names, read once per model object per call chain (cleared by read_assignments)."""
    key = id(sap)
    if key not in _PATTERN_CACHE:
        from programs import sap2000 as sap_api
        _PATTERN_CACHE[key] = [x for x in sap_api.pattern_names(sap) if x.lower() != "default"]
    return _PATTERN_CACHE[key]


def _frame_assign(sap: Any, sw: str, n: str, loads: bool, nd: bool) -> dict[str, Any]:
    a: dict[str, Any] = {"name": n}
    sec = _try(sap, "FrameObj.GetSection", n)
    a["section"] = str(sec[0]) if sec else ""
    rel = _try(sap, "FrameObj.GetReleases", n)
    if rel:
        ii, jj = [bool(v) for v in as_list(rel[0])], [bool(v) for v in as_list(rel[1])]
        if any(ii) or any(jj) or not nd:
            a["releases_i"] = " ".join(d for d, v in zip(("P", "V2", "V3", "T", "M2", "M3"), ii) if v)
            a["releases_j"] = " ".join(d for d, v in zip(("P", "V2", "V3", "T", "M2", "M3"), jj) if v)
    off = _try(sap, "FrameObj.GetEndLengthOffset", n)
    # new frames: ETABS automatic offsets on, SAP2000 / SAFE off
    if off and (bool(off[0]) != (sw == "ETABS") or off[1] or off[2] or not nd):
        a["end_offsets"] = {"auto": bool(off[0]), "length_i": _r(off[1]), "length_j": _r(off[2]),
                            "rigid_zone_factor": _r(off[3])}
    ins = _try(sap, "FrameObj.GetInsertionPoint_1", n)
    if ins and (int(ins[0]) != 10 or any(as_list(ins[4])) or any(as_list(ins[5])) or not nd):
        a["insertion_point"] = {"cardinal_point": int(ins[0]), "offset_i": [_r(v) for v in as_list(ins[4])],
                                "offset_j": [_r(v) for v in as_list(ins[5])]}
    # SAP2000 27.1 through comtypes returns code 1 unless the ref array is passed in (measured 26 Nov)
    mod = _try(sap, "FrameObj.GetModifiers", n, [0.0] * 8) or _try(sap, "FrameObj.GetModifiers", n)
    if mod and (any(abs(v - 1) > 1e-12 for v in as_list(mod[0])) or not nd):
        a["modifiers"] = dict(zip(("A", "AS2", "AS3", "J", "I22", "I33", "Mass", "Weight"),
                                  (_r(v) for v in as_list(mod[0]))))
    st = _try(sap, "FrameObj.GetOutputStations", n)
    if st:
        a["output_stations"] = ({"max_segment": _r(st[1])} if int(st[0]) == 1 else {"min_sections": int(st[2])}) | {
            "at_element_ends": not bool(st[3]), "at_point_loads": not bool(st[4])}
    ax = _try(sap, "FrameObj.GetLocalAxes", n)
    if ax and (abs(ax[0]) > 1e-12 or ax[1] or not nd):
        a["local_axis_angle"] = _r(ax[0])
    mo = _try(sap, "FrameObj.GetMaterialOverwrite", n)
    if mo and (mo[0] not in ("", "None", None) or not nd):
        a["material_overwrite"] = str(mo[0] or "")
    if sw != "SAP2000":
        sup = _try(sap, "FrameObj.GetSupports", n)
        if sup and any(str(x) not in ("", "None") for x in (sup[0], sup[2])):
            a["supports"] = {"start": str(sup[0]), "start_type": int(sup[1]), "end": str(sup[2]),
                             "end_type": int(sup[3])}
        sp = _try(sap, "FrameObj.GetSpringAssignment", n)
        if sp and str(sp[0]) not in ("", "None"):
            a["line_spring_property"] = str(sp[0])
    if sw == "ETABS":
        for what in ("Pier", "Spandrel"):
            v = _try(sap, f"FrameObj.Get{what}", n)
            if v and (v[0] not in ("", "None", None) or not nd):
                a[what.lower()] = str(v[0] or "")
    g = _try(sap, "FrameObj.GetGroupAssign", n)
    groups = [x for x in (str(v) for v in as_list(g[1])) if x.upper() != "ALL"] if g else []
    if groups or not nd:
        a["groups"] = groups
    if loads:
        d = _try(sap, "FrameObj.GetLoadDistributed", n)
        if d and d[0]:
            a["distributed_loads"] = [
                {"pattern": str(pat), "type": "force" if int(t) == 1 else "moment", "csys": str(cs),
                 "direction": LOAD_DIRS.get(int(di), str(di)), "rel_dist": [_r(r1), _r(r2)], "values": [_r(v1), _r(v2)]}
                for pat, t, cs, di, r1, r2, v1, v2 in zip(*(as_list(d[i]) for i in (2, 3, 4, 5, 6, 7, 10, 11)))]
        tl = _try(sap, "FrameObj.GetLoadTemperature", n)
        if tl and tl[0]:
            a["temperature_loads"] = [{"pattern": str(pat), "type": TEMP_KIND.get(int(t), str(t)), "value": _r(v)}
                                      for pat, t, v in zip(as_list(tl[2]), as_list(tl[3]), as_list(tl[4]))]
        pl = _try(sap, "FrameObj.GetLoadPoint", n)
        if pl and pl[0]:
            a["point_loads"] = [
                {"pattern": str(pat), "type": "force" if int(t) == 1 else "moment", "csys": str(cs),
                 "direction": LOAD_DIRS.get(int(di), str(di)), "rel_dist": _r(rd), "value": _r(v)}
                for pat, t, cs, di, rd, v in zip(*(as_list(pl[i]) for i in (2, 3, 4, 5, 6, 8)))]
    return a


def _area_assign(sap: Any, sw: str, n: str, loads: bool, nd: bool) -> dict[str, Any]:
    a: dict[str, Any] = {"name": n}
    pr = _try(sap, "AreaObj.GetProperty", n)
    a["property"] = str(pr[0]) if pr else ""
    mod = _try(sap, "AreaObj.GetModifiers", n, [0.0] * 10) or _try(sap, "AreaObj.GetModifiers", n)
    if mod and (any(abs(v - 1) > 1e-12 for v in as_list(mod[0])) or not nd):
        a["modifiers"] = dict(zip(("f11", "f22", "f12", "m11", "m22", "m12", "v13", "v23", "Mass", "Weight"),
                                  (_r(v) for v in as_list(mod[0]))))
    ax = _try(sap, "AreaObj.GetLocalAxes", n)
    if ax and (abs(ax[0]) > 1e-12 or not nd):
        a["local_axis_angle"] = _r(ax[0])
    op = _try(sap, "AreaObj.GetOpening", n)
    if op and (op[0] or not nd):
        a["opening"] = bool(op[0])
    if sw != "SAP2000":
        for meth, key in (("GetDiaphragm", "diaphragm"), ("GetSpringAssignment", "spring_property")):
            v = _try(sap, f"AreaObj.{meth}", n)
            if v and (v[0] not in ("", "None", None) or not nd):
                a[key] = str(v[0] or "")
        if sw == "ETABS":
            for what in ("Pier", "Spandrel"):
                v = _try(sap, f"AreaObj.Get{what}", n)
                if v and (v[0] not in ("", "None", None) or not nd):
                    a[what.lower()] = str(v[0] or "")
    else:
        sp = _try(sap, "AreaObj.GetSpring", n)
        if sp and (sp[0] or not nd):
            a["springs"] = int(sp[0])
    g = _try(sap, "AreaObj.GetGroupAssign", n)
    groups = [x for x in (str(v) for v in as_list(g[1])) if x.upper() != "ALL"] if g else []
    if groups or not nd:
        a["groups"] = groups
    if sw == "SAP2000":
        from programs import sap2000 as sap_api
        mesh = sap_api.get_auto_mesh(sap, n)
        if mesh and (mesh["mode"] != "none" or not nd):
            a["auto_mesh"] = mesh
    if loads:
        uf = _try(sap, "AreaObj.GetLoadUniformToFrame", n)
        if uf and uf[0]:
            a["uniform_to_frame_loads"] = [
                {"pattern": str(pat), "csys": str(cs), "direction": LOAD_DIRS.get(int(di), str(di)), "value": _r(v),
                 "distribution": "one_way" if int(dt) == 1 else "two_way"}
                for pat, cs, di, v, dt in zip(*(as_list(uf[i]) for i in (2, 3, 4, 5, 6)))]
        if sw == "SAP2000":
            sp = sap_api.get_surface_pressure(sap, n)
            if sp:
                a["surface_pressure_loads"] = [{**x, "value": _r(x["value"])} for x in sp]
        tl = _try(sap, "AreaObj.GetLoadTemperature", n)
        if tl and tl[0]:
            a["temperature_loads"] = [{"pattern": str(pat), "type": TEMP_KIND.get(int(t), str(t)), "value": _r(v)}
                                      for pat, t, v in zip(as_list(tl[2]), as_list(tl[3]), as_list(tl[4]))]
        u = _try(sap, "AreaObj.GetLoadUniform", n)
        if u and u[0]:
            a["uniform_loads"] = [{"pattern": str(pat), "csys": str(cs), "direction": LOAD_DIRS.get(int(di), str(di)),
                                   "value": _r(v)}
                                  for pat, cs, di, v in zip(*(as_list(u[i]) for i in (2, 3, 4, 5)))]
    return a


ASSIGNERS = {"points": ("PointObj", _point_assign), "frames": ("FrameObj", _frame_assign),
             "areas": ("AreaObj", _area_assign)}


def read_assignments(sap: Any, sw: str, kind: str, names: list[str] | None, loads: bool, nd: bool,
                     max_objects: int) -> list[dict[str, Any]]:
    coll, fn = ASSIGNERS[kind]
    _PATTERN_CACHE.clear()
    all_names = _names(sap, coll)
    if names:
        missing = [n for n in names if n not in set(all_names)]
        if missing:
            raise CSIValidationError(f"Not {kind} in the model: {missing[:20]}.")
        all_names = names
    if len(all_names) > max_objects:
        raise CSIValidationError(f"{len(all_names)} {kind} to read one by one (max_objects {max_objects}).",
                                 hint="Give names (e.g. from get_objects with a story), or raise max_objects.")
    return [fn(sap, sw, n, loads, nd) for n in all_names]


@action("get_object_assignments", "Assignments of points (restraint, springs[[EF:, spring property, diaphragm]], "
        "mass, groups, loads[[S:, joint pattern values]]), frames (section, releases, end offsets, insertion point, "
        "modifiers, local axis, material overwrite[[E:, pier / spandrel]], groups, distributed and point loads) or "
        "areas (property, modifiers, local axis, opening[[EF:, diaphragm, spring property]][[S:, springs, auto "
        "mesh]][[E:, pier / spandrel]], groups, uniform and uniform-to-frame loads[[S:, surface pressure]]). Defaults "
        "left out unless non_default_only is false.", "Model reading", "read", AssignP, long_running=True)
def get_object_assignments(p: AssignP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        rows = read_assignments(sap, p.software, p.object_type, p.names, p.include_loads, p.non_default_only,
                                p.max_objects)
    path = None
    if len(rows) > p.max_rows:
        folder = safety.output_root() / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{p.software}_{p.object_type}_assignments_{safety.stamp()}.json"
        path.write_text(json.dumps(rows, indent=1, default=str), encoding="utf-8")
    with_any = [r for r in rows if len(r) > (2 if p.object_type != "points" else 1)]
    return {"software": p.software, "object_type": p.object_type, "units": describe_units(p.units),
            "objects_read": len(rows), "objects_with_assignments": len(with_any),
            "items": rows[:p.max_rows], "truncated": len(rows) > p.max_rows, "export_path": str(path) if path else None}


# --------------------------------------------------------------------------- definitions

def read_materials(sap: Any, names: list[str] | None) -> list[dict[str, Any]]:
    out = []
    for n in names or _names(sap, "PropMaterial"):
        rec: dict[str, Any] = {"name": n}
        m = _try(sap, "PropMaterial.GetMaterial", n)
        if m:
            rec["type"] = MAT_TYPES.get(int(m[0]), str(m[0]))
        iso = _try(sap, "PropMaterial.GetMPIsotropic", n)
        if iso:
            rec.update(E=_r(iso[0]), poisson=_r(iso[1]), thermal_coeff=_r(iso[2], 10), G=_r(iso[3]))
        wm = _try(sap, "PropMaterial.GetWeightAndMass", n)
        if wm:
            rec.update(weight_per_volume=_r(wm[0]), mass_per_volume=_r(wm[1]))
        kind = rec.get("type", "")
        if kind == "Concrete":
            g = _try(sap, "PropMaterial.GetOConcrete_1", n)
            if g:
                rec["fc"] = _r(g[0])
        elif kind in ("Steel", "Rebar", "Tendon", "ColdFormed", "Aluminum"):
            meth = {"Steel": "GetOSteel_1", "Rebar": "GetORebar_1", "Tendon": "GetOTendon_1",
                    "ColdFormed": "GetOColdFormed_1", "Aluminum": "GetOAluminum_1"}[kind]
            g = _try(sap, f"PropMaterial.{meth}", n)
            if g:
                rec.update(fy=_r(g[0]), fu=_r(g[1]))
        out.append(rec)
    return out


def read_frame_sections(sap: Any, sw: str, names: list[str] | None) -> list[dict[str, Any]]:
    wanted = set(names) if names else None
    out: list[dict[str, Any]] = []
    bulk = _try(sap, "PropFrame.GetAllFrameProperties_2") if sw != "SAP2000" else None
    if bulk:
        cols = [as_list(bulk[i]) for i in range(1, 10)]
        for n, t, t3, t2, tf, tw, t2b, tfb, area in zip(*cols):
            if wanted and str(n) not in wanted:
                continue
            out.append({"name": str(n), "type": FRAME_PROP_TYPES.get(int(t), str(t)), "t3_depth": _r(t3),
                        "t2_width": _r(t2), "tf": _r(tf), "tw": _r(tw), "t2b": _r(t2b), "tfb": _r(tfb),
                        "area": _r(area)})
    else:
        for n in names or _names(sap, "PropFrame"):
            t = _try(sap, "PropFrame.GetTypeOAPI", n)
            code = int(t[0]) if t else 0
            rec: dict[str, Any] = {"name": n, "type": FRAME_PROP_TYPES.get(code, str(code))}
            getter = {8: ("GetRectangle", ("t3_depth", "t2_width")), 9: ("GetCircle", ("t3_depth",)),
                      1: ("GetISection", ("t3_depth", "t2_width", "tf", "tw", "t2b", "tfb")),
                      6: ("GetTube", ("t3_depth", "t2_width", "tf", "tw")), 7: ("GetPipe", ("t3_depth", "tw"))}.get(code)
            if getter:
                meth, keys = getter
                # newest variants first (I: + fillet radius, tube: + corner radius), older ones as fallback
                newer = {"GetISection": ("GetISection_1", keys + ("fillet_radius",)),
                         "GetTube": ("GetTube_1", keys + ("corner_radius",))}.get(meth)
                g = _try(sap, f"PropFrame.{newer[0]}", n) if newer else None
                if g:
                    keys = newer[1]
                else:
                    g = _try(sap, f"PropFrame.{meth}", n)
                if g:
                    rec.update({k: _r(v) for k, v in zip(keys, g[2:2 + len(keys)])})
            out.append(rec)
    for rec in out:
        m = _try(sap, "PropFrame.GetMaterial", rec["name"])
        rec["material"] = str(m[0]) if m else ""
        sm = _try(sap, "PropFrame.GetModifiers", rec["name"], [0.0] * 8) or \
            _try(sap, "PropFrame.GetModifiers", rec["name"])
        if sm and any(abs(float(v) - 1) > 1e-12 for v in as_list(sm[0])):
            rec["modifiers"] = dict(zip(("A", "AS2", "AS3", "J", "I22", "I33", "Mass", "Weight"),
                                        (_r(v) for v in as_list(sm[0]))))
        if rec.get("type") == "General":           # its properties are the section (dimensions are nominal)
            g = _try(sap, "PropFrame.GetSectProps", rec["name"])
            if g:
                rec["properties"] = {k: float(f"{float(v):.10g}") for k, v in zip(
                    ("area", "as2", "as3", "torsion", "i22", "i33", "s22", "s33", "z22", "z33", "r22", "r33"), g)}
        f = _try(sap, "PropFrame.GetNameInPropFile", rec["name"])
        if f and f[0]:
            rec["from_file"] = f"{f[0]} ({Path(str(f[1])).name})"
    return out


def read_area_sections(sap: Any, sw: str, names: list[str] | None) -> list[dict[str, Any]]:
    out = []
    for n in names or _names(sap, "PropArea"):
        rec: dict[str, Any] = {"name": n}
        for meth, kind in (("GetShell_1", "shell"), ("GetSlab", "slab"), ("GetWall", "wall"), ("GetDeck", "deck")):
            if not hasattr(sap.PropArea, meth):
                continue
            g = _try(sap, f"PropArea.{meth}", n)
            if not g:
                continue
            if kind == "shell":
                rec.update(kind="shell", shell_type=int(g[0]), material=str(g[2]), thickness=_r(g[4]),
                           bending_thickness=_r(g[5]))
            else:
                rec.update(kind=kind, subtype=int(g[0]), shell_type=int(g[1]), material=str(g[2]),
                           thickness=_r(g[3]))
            break
        mod = _try(sap, "PropArea.GetModifiers", n)
        if mod and any(abs(v - 1) > 1e-12 for v in as_list(mod[0])):
            rec["modifiers"] = [_r(v) for v in as_list(mod[0])]
        out.append(rec)
    return out


def read_joint_patterns(sap: Any, sw: str, names: list[str] | None) -> list[dict[str, Any]]:
    from programs import sap2000 as sap_api
    pats = names or [x for x in sap_api.pattern_names(sap) if x.lower() != "default"]
    pts = read_points(sap, sw)
    out = []
    for pat in pats:
        vals = [(q, sap_api.pattern_value(sap, q["name"], pat)) for q in pts]
        nz = [(q, v) for q, v in vals if v]
        rec: dict[str, Any] = {"name": pat, "joints_with_value": len(nz)}
        if nz:
            lo, hi = min(nz, key=lambda t: t[1]), max(nz, key=lambda t: t[1])
            rec.update(min=_r(lo[1]), min_at=lo[0]["name"], min_z=lo[0]["z"], max=_r(hi[1]), max_at=hi[0]["name"],
                       max_z=hi[0]["z"])
        out.append(rec)
    return out


def read_load_patterns(sap: Any, names: list[str] | None) -> list[dict[str, Any]]:
    out = []
    for n in names or _names(sap, "LoadPatterns"):
        t = _try(sap, "LoadPatterns.GetLoadType", n)
        sw = _try(sap, "LoadPatterns.GetSelfWTMultiplier", n)
        out.append({"name": n, "type": LOAD_PATTERN_TYPES.get(int(t[0]), str(t[0])) if t else "",
                    "self_weight_multiplier": _r(sw[0]) if sw else None})
    return out


@action("get_definition_details", "Full definitions of one kind: materials (type, E, Poisson, G, weight, mass), "
        "frame sections (type, dimensions, area, material, section file), area sections (shell / slab / wall / deck, "
        "material, thickness, modifiers), [[EF: springs (point / area stiffnesses), ]]load patterns (type, self-weight "
        "multiplier)[[S: or joint patterns (joints with a value)]].",
        "Model reading", "read", DefinitionsDetailP, options={"kind": {"springs": "EF", "joint_patterns": "S"}})
def get_definition_details(p: DefinitionsDetailP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        if p.kind == "materials":
            rows = read_materials(sap, p.names)
        elif p.kind == "frame_sections":
            rows = read_frame_sections(sap, p.software, p.names)
        elif p.kind == "area_sections":
            rows = read_area_sections(sap, p.software, p.names)
        elif p.kind == "load_patterns":
            rows = read_load_patterns(sap, p.names)
        elif p.kind == "joint_patterns":
            import programs
            programs.require(p.software, "joint_patterns")
            rows = read_joint_patterns(sap, p.software, p.names)
        else:
            if p.software == "SAP2000":
                raise CSIValidationError("SAP2000 springs are assigned per object; see get_object_assignments.")
            from definitions import read_spring_props
            rows = read_spring_props(sap, p.names, p.software)
    return {"software": p.software, "kind": p.kind, "units": describe_units(p.units),
            **_out(_frame(rows), "items", p.max_rows, f"{p.software}_{p.kind}")}


CASE_LOADERS = {1: "StaticLinear", 2: "StaticNonlinear", 3: "ModalEigen", 4: "ResponseSpectrum"}
NL_GEOM = {0: "none", 1: "p_delta", 2: "p_delta_large_displacements"}


def read_load_cases(sap: Any, names: list[str] | None) -> list[dict[str, Any]]:
    out = []
    for n in names or _names(sap, "LoadCases"):
        t = _try(sap, "LoadCases.GetTypeOAPI_1", n) or _try(sap, "LoadCases.GetTypeOAPI", n)
        code = int(t[0]) if t else 0
        rec: dict[str, Any] = {"name": n, "type": LOAD_CASE_TYPES.get(code, str(code)),
                               "subtype": int(t[1]) if t else None}
        if t and len(t) >= 5:     # GetTypeOAPI_1: design type (a load pattern type) and auto-created flag
            rec["design_type"] = LOAD_PATTERN_TYPES.get(int(t[2]), str(t[2]))
            rec["auto_created"] = int(t[4]) == 1
        sub = CASE_LOADERS.get(code)
        if sub == "ModalEigen" and t and int(t[1]) != 1:
            sub = None   # Ritz modal case
        if sub == "StaticNonlinear":
            g = _try(sap, "LoadCases.StaticNonlinear.GetGeometricNonlinearity", n)
            ic = _try(sap, "LoadCases.StaticNonlinear.GetInitialCase", n)
            rec["geometric_nonlinearity"] = NL_GEOM.get(int(g[0]), g[0]) if g else None
            rec["initial_case"] = str(ic[0]) if ic and str(ic[0]) else None
        if sub:
            o = _try(sap, f"LoadCases.{sub}.GetLoads", n)
            if o:
                if sub in ("StaticLinear", "StaticNonlinear"):
                    rec["loads"] = [{"type": str(a), "name": str(b), "factor": _r(c)}
                                    for a, b, c in zip(as_list(o[1]), as_list(o[2]), as_list(o[3]))]
                elif sub == "ModalEigen":
                    rec["loads"] = [{"type": str(a), "name": str(b), "target_participation": _r(c)}
                                    for a, b, c in zip(as_list(o[1]), as_list(o[2]), as_list(o[3]))]
                else:
                    rec["loads"] = [{"direction": str(a), "function": str(b), "factor": _r(c), "csys": str(d),
                                     "angle": _r(e)}
                                    for a, b, c, d, e in zip(*(as_list(o[i]) for i in range(1, 6)))]
        out.append(rec)
    return out


@action("get_load_case_details", "Load cases with type and, for linear static, modal eigen and response-spectrum "
        "cases, their loads with factors (functions and directions for spectra).", "Model reading", "read", NamesP)
def get_load_case_details(p: NamesP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    rows = read_load_cases(sap, p.names)
    return {"software": p.software, "load_cases": rows[:p.max_rows], "count": len(rows),
            "truncated": len(rows) > p.max_rows}


def read_combos(sap: Any, names: list[str] | None) -> list[dict[str, Any]]:
    out = []
    for n in names or (bridge.try_name_list(sap, ["RespCombo"])[0] or []):
        t = _try(sap, "RespCombo.GetTypeOAPI", n)
        items = []
        o = _try(sap, "RespCombo.GetCaseList_1", n)      # newest: with mode numbers for modal cases
        if o:
            for k, c, mode, f in zip(as_list(o[1]), as_list(o[2]), as_list(o[3]), as_list(o[4])):
                it = {"kind": "combination" if int(k) == 1 else "case", "name": str(c), "factor": _r(f)}
                if int(mode or 0):
                    it["mode"] = int(mode)
                items.append(it)
        else:
            o = _try(sap, "RespCombo.GetCaseList", n)
            items = [{"kind": "combination" if int(k) == 1 else "case", "name": str(c), "factor": _r(f)}
                     for k, c, f in zip(as_list(o[1]), as_list(o[2]), as_list(o[3]))] if o else []
        out.append({"name": n, "type": COMBO_TYPES.get(int(t[0]), str(t[0])) if t else "", "items": items})
    return out


@action("get_combo_details", "Load combinations with type (linear add, envelope, abs add, SRSS, range) and the "
        "cases / combinations with their factors.", "Model reading", "read", NamesP)
def get_combo_details(p: NamesP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    rows = read_combos(sap, p.names)
    return {"software": p.software, "combinations": rows[:p.max_rows], "count": len(rows),
            "truncated": len(rows) > p.max_rows}


def read_mass_source(sap: Any, sw: str) -> dict[str, Any]:
    if sw == "SAP2000":
        out = []
        for n in bridge.try_name_list(sap, ["SourceMass"])[0] or []:
            o = _try(sap, "SourceMass.GetMassSource", n)
            if o:
                out.append({"name": n, "from_elements": bool(o[0]), "from_masses": bool(o[1]), "from_loads": bool(o[2]),
                            "default": bool(o[3]),
                            "load_patterns": [{"pattern": str(a), "factor": _r(b)}
                                              for a, b in zip(as_list(o[5]), as_list(o[6]))]})
        return {"mass_sources": out}
    o = api_call("PropMaterial.GetMassSource_1", sap.PropMaterial.GetMassSource_1)
    return {"mass_sources": [{"name": "(model)", "from_elements": bool(o[0]), "from_masses": bool(o[1]),
                              "from_loads": bool(o[2]),
                              "load_patterns": [{"pattern": str(a), "factor": _r(b)}
                                                for a, b in zip(as_list(o[4]), as_list(o[5]))]}]}


@action("get_mass_source", "Mass source: from element self mass, added masses and / or load patterns with factors.",
        "Model reading", "read", SoftwareOnlyP)
def get_mass_source(p: SoftwareOnlyP) -> dict[str, Any]:
    return {"software": p.software, **read_mass_source(bridge.sap_model(p.software), p.software)}


@action("get_selected", "Objects the user has selected in the program (type and name).", "Model reading", "read",
        SoftwareOnlyP)
def get_selected(p: SoftwareOnlyP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    o = api_call("SelectObj.GetSelected", sap.SelectObj.GetSelected)
    items = [{"type": OBJECT_TYPES.get(int(t), str(t)), "name": str(n)} for t, n in zip(as_list(o[1]), as_list(o[2]))]
    counts: dict[str, int] = {}
    for i in items:
        counts[i["type"]] = counts.get(i["type"], 0) + 1
    return {"software": p.software, "selected": len(items), "by_type": counts, "items": items[:500],
            "truncated": len(items) > 500}


# --------------------------------------------------------------------------- snapshot

def model_snapshot(sap: Any, sw: str, include_assignments: bool) -> dict[str, Any]:
    pts = read_points(sap, sw)
    coord = {q["name"]: (q["x"], q["y"], q["z"]) for q in pts}
    snap: dict[str, Any] = {
        "points": pts, "frames": read_frames(sap, sw, coord), "areas": read_areas(sap, sw, coord),
        "materials": read_materials(sap, None), "frame_sections": read_frame_sections(sap, sw, None),
        "area_sections": read_area_sections(sap, sw, None), "load_patterns": read_load_patterns(sap, None),
        "load_cases": read_load_cases(sap, None), "combinations": read_combos(sap, None),
        "groups": [{"group": g} for g in _names(sap, "GroupDef")],
    }
    with contextlib.suppress(CSIError, COMError, AttributeError, IndexError, TypeError):
        snap["mass_source"] = read_mass_source(sap, sw)["mass_sources"]
    if sw != "SAP2000":
        with contextlib.suppress(CSIError, COMError, AttributeError, IndexError):
            o = api_call("Story.GetStories_2", sap.Story.GetStories_2)
            snap["stories"] = [{"name": str(n), "elevation": _r(e)} for n, e in zip(as_list(o[2]), as_list(o[3]))]
    from actions import read_analysis_options
    snap["analysis_options"] = read_analysis_options(sap, sw)
    if sw != "SAP2000":
        from definitions import read_spring_props
        with contextlib.suppress(CSIError, COMError, AttributeError, IndexError, TypeError):
            snap["spring_properties"] = read_spring_props(sap, None, sw)
    if include_assignments:
        snap["assignments"] = {k: read_assignments(sap, sw, k, None, True, True, 10 ** 9)
                               for k in ("points", "frames", "areas")}
    return snap


@action("export_model_snapshot", "The whole model as one JSON file: points, frames, areas, definitions, load "
        "patterns, cases, combinations, groups, mass source, stories and (by default) every object's assignments "
        "and loads. Returns the counts and the file path.", "Output", "read", SnapshotJsonP, long_running=True)
def export_model_snapshot(p: SnapshotJsonP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    model = bridge.model_filename(sap, p.software)
    if p.output_path:
        path = Path(p.output_path).expanduser().resolve()
        if path.suffix.lower() != ".json":
            raise CSIValidationError("output_path must end in .json")
        if not path.parent.is_dir():
            raise CSIValidationError(f"Folder does not exist: {path.parent}")
    else:
        folder = safety.output_root() / "exports"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{safety.safe_stem(Path(model).stem if model else p.software)}_model_{safety.stamp()}.json"
    with bridge.units_scope(sap, p.units):
        snap = model_snapshot(sap, p.software, p.include_assignments)
    doc = {"software": p.software, "model_file": model or None, "units": describe_units(p.units),
           "created": dt.datetime.now().isoformat(timespec="seconds"), **snap}
    path.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    counts = {k: len(v) for k, v in snap.items() if isinstance(v, list)}
    if "assignments" in snap:
        counts["assignments"] = {k: len(v) for k, v in snap["assignments"].items()}
    return {"software": p.software, "file": str(path), "bytes": path.stat().st_size, "counts": counts,
            "units": describe_units(p.units)}


# --------------------------------------------------------------------------- report geometry (R2)

def _group_call(sap: Any, method: str) -> list[Any] | None:
    """Loads of every object in one call: Get...("All", ItemType=1 = group). None when the build refuses it."""
    try:
        func = sap
        for part in method.split("."):
            func = getattr(func, part)
        return api_call(method, func, "All", ItemType=1)
    except (CSIError, COMError, AttributeError, TypeError, ValueError, IndexError, OSError):
        return None


def read_loads(sap: Any, max_objects: int = 20000) -> dict[str, Any]:
    """Assigned loads as (object, pattern, label text): area uniform, frame distributed / point, joint forces.
    Uses the group 'All' in one call per kind; falls back to object by object (up to max_objects per kind)."""
    out: dict[str, Any] = {"areas": [], "frames": [], "points": [], "method": "group", "skipped": []}

    def per_object(coll: str, method: str) -> list[list[Any]]:
        names = _names(sap, coll)
        if len(names) > max_objects:
            out["skipped"].append(f"{coll}: {len(names)} objects (more than {max_objects}); loads not drawn")
            return []
        out["method"] = "per object"
        return [o for o in (_try(sap, f"{coll}.{method}", n) for n in names) if o and o[0]]

    def rows(coll: str, method: str) -> list[list[Any]]:
        g = _group_call(sap, f"{coll}.{method}")
        return [g] if g is not None else per_object(coll, method)

    for o in rows("AreaObj", "GetLoadUniform"):
        for n, pat, di, val in zip(*(as_list(o[i]) for i in (1, 2, 4, 5))):
            out["areas"].append((str(n), str(pat), f"{_fmt_num(val)} {LOAD_DIRS.get(int(di), di)}"))
    for o in rows("FrameObj", "GetLoadDistributed"):
        for n, pat, di, v1, v2 in zip(*(as_list(o[i]) for i in (1, 2, 5, 10, 11))):
            val = _fmt_num(v1) if abs(v1 - v2) < 1e-12 else f"{_fmt_num(v1)}..{_fmt_num(v2)}"
            out["frames"].append((str(n), str(pat), f"w {val} {LOAD_DIRS.get(int(di), di)}"))
    for o in rows("FrameObj", "GetLoadPoint"):
        for n, pat, di, v in zip(*(as_list(o[i]) for i in (1, 2, 5, 8))):
            out["frames"].append((str(n), str(pat), f"P {_fmt_num(v)} {LOAD_DIRS.get(int(di), di)}"))
    for o in rows("PointObj", "GetLoadForce"):
        for n, pat, *f in zip(*(as_list(o[i]) for i in (1, 2, 5, 6, 7, 8, 9, 10))):
            comps = [f"{k} {_fmt_num(x)}" for k, x in zip(("F1", "F2", "F3", "M1", "M2", "M3"), f) if abs(x) > 1e-12]
            out["points"].append((str(n), str(pat), ", ".join(comps) or "0"))
    return out


def _fmt_num(v: Any) -> str:
    return f"{float(v):.4g}"


def report_geometry(sap: Any, sw: str) -> dict[str, Any]:
    """Everything the report diagrams need, in the present units."""
    pts = read_points(sap, sw)
    coord = {q["name"]: (q["x"], q["y"], q["z"]) for q in pts}
    geo: dict[str, Any] = {"points": pts, "frames": read_frames(sap, sw, coord), "areas": read_areas(sap, sw, coord),
                           "grids": {}, "stories": [], "base_elevation": None}
    if sw != "SAP2000":
        for g in (_try(sap, "GridSys.GetNameList") or [0, ()])[1] or ():
            o = _try(sap, "GridSys.GetGridSys_2", str(g))
            if o and abs(float(o[2])) < 1e-9 and abs(float(o[0])) < 1e-9 and abs(float(o[1])) < 1e-9:
                geo["grids"].setdefault("x", {}).update({str(i): float(v) for i, v in zip(as_list(o[6]), as_list(o[8]))})
                geo["grids"].setdefault("y", {}).update({str(i): float(v) for i, v in zip(as_list(o[7]), as_list(o[9]))})
        o = _try(sap, "Story.GetStories_2")
        if o:
            geo["base_elevation"] = float(o[0])
            geo["stories"] = [{"name": str(n), "elevation": float(e)} for n, e in zip(as_list(o[2]), as_list(o[3]))]
    geo["loads"] = read_loads(sap)
    geo["patterns"] = _names(sap, "LoadPatterns")
    return geo
