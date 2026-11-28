"""Phase 3 (first batch): area mesh, joint patterns and area loads.

set_area_mesh        SAP2000: auto-mesh assignment of areas (analysis mesh, the objects stay) or divide them into
                     smaller area objects.
define_joint_pattern SAP2000: a joint pattern with values from a linear rule A*x + B*y + C*z + D, or from a fluid
                     level and unit weight (hydrostatic: unit_weight * (level - z) below the level, 0 above).
assign_area_load     uniform (all programs), uniform to frames (all programs), surface pressure scaled by a joint
                     pattern (SAP2000: the way to put hydrostatic or earth pressure on walls).

Every action takes dry_run, snapshot and allow_unlock, checks the names first (ETABS / SAFE answer 0 for names
that do not exist) and is logged in the change log. Program-specific calls live in programs/.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

import programs
from actions import (
    ALLOW_UNLOCK, DRY_RUN, SNAPSHOT, SOFTWARE, UNITS, ActionFailed, Params, _FRAME_DIRS, _dry, _lock, _refresh_view,
    _take_snapshot, action, bridge,
)
from csi_bridge import CSIApiError, CSIValidationError, Software, UnitPreset, api_call, describe_units
from programs import sap2000 as sap_api

NAMES = Field(default=None, max_length=5000, description="area object names; give names or group")
GROUP = Field(default=None, description="a group of objects instead of names (e.g. 'ALL' / 'All')")


def _targets(sap: Any, collection: str, names: list[str] | None, group: str | None, kind: str) -> list[str]:
    """Object names from names or a group (GroupDef.GetAssignments), checked against the model."""
    existing = bridge.name_list(sap, collection)
    if names and group:
        raise CSIValidationError("Give names or group, not both.")
    if group:
        groups = bridge.name_list(sap, "GroupDef")
        real = next((g for g in groups if g.lower() == group.lower()), None)
        if real is None:
            raise CSIValidationError(f"Group '{group}' is not defined.", hint=f"Groups: {groups[:30]}")
        o = api_call("GroupDef.GetAssignments", sap.GroupDef.GetAssignments, real)
        type_code = {"PointObj": 1, "FrameObj": 2, "CableObj": 3, "TendonObj": 4, "AreaObj": 5, "SolidObj": 6,
                     "LinkObj": 7}[collection]
        picked = [str(n) for t, n in zip(o[1] or (), o[2] or ()) if int(t) == type_code]
        if not picked:
            raise CSIValidationError(f"Group '{real}' has no {kind}.")
        return picked
    if not names:
        raise CSIValidationError(f"Give the {kind} names (or a group).")
    if len(set(names)) != len(names):
        raise CSIValidationError("Each name only once, please.")
    missing = [n for n in names if n not in set(existing)]
    if missing:
        raise CSIValidationError(f"Not {kind} in the model: {missing[:20]}.",
                                 hint="Use the unique names get_objects lists.")
    return list(names)


def _start_edit(p: Any, sap: Any, label: str, what: str) -> tuple[dict | None, bool]:
    snap = _take_snapshot(p.software, sap, label) if p.snapshot else None
    return snap, _lock(sap, p.allow_unlock, what)


# --------------------------------------------------------------------------- mesh

class MeshP(Params):
    software: Software = SOFTWARE
    area_names: list[str] | None = NAMES
    group: str | None = GROUP
    method: Literal["auto_mesh", "divide"] = Field(default="auto_mesh", description=(
        "auto_mesh (default): analysis mesh assigned to the area, the object stays one area | divide: the area is "
        "replaced by smaller area objects (cannot be undone except by snapshot / undo in the program)"))
    mode: Literal["divisions", "max_size", "general", "none"] = Field(default="divisions", description=(
        "divisions: n1 x n2 pieces | max_size: pieces no longer than max_size_1 / max_size_2 | general: general "
        "divide tool with max_size (any shape) | none: remove the auto mesh (auto_mesh only)"))
    n1: int = Field(default=2, ge=1, le=500, description="divisions along the area's first edge (divisions mode)")
    n2: int = Field(default=2, ge=1, le=500, description="divisions along the second edge (divisions mode)")
    max_size_1: float = Field(default=0.0, ge=0, description="max element size along edge 1 (max_size mode), units")
    max_size_2: float = Field(default=0.0, ge=0, description="max element size along edge 2 (max_size mode), units")
    max_size: float = Field(default=0.0, ge=0, description="max element size (general mode), units")
    mesh_at_points: bool = Field(default=False, description="also mesh at points that lie on the area edges")
    mesh_at_lines: bool = Field(default=False, description="also mesh at frames / lines that meet the area edges")
    restraints_on_edges: bool = Field(default=True, description=(
        "joints added along an edge whose two corner joints share a restraint get the same restraint (keeps a wall "
        "fixed along its whole base); default true"))
    units: UnitPreset = UNITS
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


WALL_MESH = "Area Assignments - Wall Auto Mesh Options"
WALL_SIZE = "Analysis Options - Automatic Rectangular Mesh Options for Walls"
FLOOR_SIZE = "Analysis Options - Automatic Mesh Settings for Floors"


def _etabs_mesh(p: MeshP) -> dict[str, Any]:
    """ETABS: no API call; the program's own assignment / option tables. Walls: auto rectangular mesh (per wall) at
    the model's wall mesh size; 'none' puts the wall back to Default (one element per wall panel, measured). Floors:
    the model's floor mesh size (floors on Default use it)."""
    import db_edit
    if p.method != "auto_mesh" or p.mode not in ("max_size", "none"):
        raise CSIValidationError("ETABS: set_area_mesh takes method auto_mesh with mode max_size or none.",
                                 hint="ETABS meshes walls rectangularly and floors with one size for the model; "
                                      "give max_size_1 (and max_size_2) for the element size.")
    if p.mode == "max_size" and not (p.max_size_1 > 0 and p.max_size_2 > 0):
        raise CSIValidationError("max_size mode needs max_size_1 and max_size_2 > 0.")
    size = min(p.max_size_1, p.max_size_2) if p.mode == "max_size" else 0.0
    sap = bridge.sap_model(p.software)
    areas = _targets(sap, "AreaObj", p.area_names, p.group, "areas")
    _, wf, wrows = db_edit.read_table(sap, WALL_MESH)
    wall_names = {r.get("UniqueName") for r in wrows}
    walls = [a for a in areas if a in wall_names]
    floors = [a for a in areas if a not in wall_names]
    if p.mode == "none" and floors:
        raise CSIValidationError(f"ETABS: mode none applies to walls only; {len(floors)} of the areas are floors.",
                                 hint="Floors always get the model's floor mesh; give mode max_size to change its size.")
    plan = {"method": p.method, "mode": p.mode, "walls": walls[:50], "floors": floors[:50], "max_size": size,
            "units": describe_units(p.units),
            "note": "ETABS has one wall mesh size and one floor mesh size for the whole model: the size given here "
                    "applies to every meshed wall / floor; walls named here are set to auto rectangular mesh."}
    if p.dry_run:
        locked = bridge.is_locked(sap)
        return _dry(plan, model_locked=locked, needs_allow_unlock=bool(locked) and not p.allow_unlock)
    snap, was_locked = _start_edit(p, sap, "before_mesh", "change the mesh")
    out: dict[str, Any] = {**plan, "model_was_unlocked": was_locked}
    with bridge.units_scope(sap, p.units):
        if walls:
            ver, wf, wrows = db_edit.read_table(sap, WALL_MESH)
            for r in wrows:
                if r.get("UniqueName") in walls:
                    r["MeshOption"] = "Auto Rectangular" if p.mode == "max_size" else "Default"
            db_edit.write_table(sap, WALL_MESH, ver, wf, wrows)
            if size:
                ver, sf, srows = db_edit.read_table(sap, WALL_SIZE)
                srows[0]["MaxMeshSize"] = f"{size:g}"
                db_edit.write_table(sap, WALL_SIZE, ver, sf, srows)
        if floors and size:
            ver, ff, frows = db_edit.read_table(sap, FLOOR_SIZE)
            frows[0]["MaxMeshSize"] = f"{size:g}"
            db_edit.write_table(sap, FLOOR_SIZE, ver, ff, frows)
        # read back
        _, _, wrows = db_edit.read_table(sap, WALL_MESH)
        out["walls_read_back"] = {r["UniqueName"]: r.get("MeshOption") for r in wrows if r.get("UniqueName") in walls}
        out["wall_mesh_size_read_back"] = db_edit.read_table(sap, WALL_SIZE)[2][0].get("MaxMeshSize")
        out["floor_mesh_size_read_back"] = db_edit.read_table(sap, FLOOR_SIZE)[2][0].get("MaxMeshSize")
    wrong = [w for w, o in out["walls_read_back"].items()
             if o != ("Auto Rectangular" if p.mode == "max_size" else "Default")]
    _refresh_view(sap)
    if snap:
        out["snapshot"] = snap
    out["done"] = len(walls) + len(floors) - len(wrong)
    if wrong:
        out["message"] = f"{len(wrong)} walls did not take the mesh option."
        raise ActionFailed(out)
    return out


@action("set_area_mesh", "Set the analysis mesh of areas (auto mesh by divisions, max size or general divide; or "
        "none) or divide areas into smaller area objects (restraints carried to the new edge joints)[[E:; in ETABS "
        "walls get auto rectangular mesh and floors the model's floor mesh size (mode max_size or none)]].", "Modelling",
        "edit", MeshP, programs="SE")
def set_area_mesh(p: MeshP) -> dict[str, Any]:
    if p.software == "ETABS":
        return _etabs_mesh(p)
    programs.require(p.software, "area_auto_mesh" if p.method == "auto_mesh" else "area_divide")
    if p.mode == "max_size" and not (p.max_size_1 > 0 and p.max_size_2 > 0):
        raise CSIValidationError("max_size mode needs max_size_1 and max_size_2 > 0.")
    if p.mode == "general" and not p.max_size > 0:
        raise CSIValidationError("general mode needs max_size > 0.")
    if p.method == "divide" and p.mode in ("none", "general"):
        raise CSIValidationError("divide takes mode divisions or max_size.")
    sap = bridge.sap_model(p.software)
    areas = _targets(sap, "AreaObj", p.area_names, p.group, "areas")
    plan = {"method": p.method, "mode": p.mode, "areas": len(areas), "first_areas": areas[:20],
            "n1": p.n1, "n2": p.n2, "max_size_1": p.max_size_1, "max_size_2": p.max_size_2, "max_size": p.max_size,
            "restraints_on_edges": p.restraints_on_edges, "units": describe_units(p.units)}
    if p.dry_run:
        locked = bridge.is_locked(sap)
        return _dry(plan, model_locked=locked, needs_allow_unlock=bool(locked) and not p.allow_unlock)
    snap, was_locked = _start_edit(p, sap, "before_mesh", "change the mesh")
    done, failed, new_areas, edge_restrained = [], [], {}, []
    with bridge.units_scope(sap, p.units):
        for a in areas:
            try:
                if p.method == "auto_mesh":
                    sap_api.set_auto_mesh(sap, a, p.mode, p.n1, p.n2, p.max_size_1, p.max_size_2, p.max_size,
                                          p.mesh_at_points, p.mesh_at_lines, p.restraints_on_edges)
                else:
                    res = sap_api.divide(sap, a, p.mode, p.n1, p.n2, p.max_size_1, p.max_size_2,
                                         p.restraints_on_edges)
                    new_areas[a] = res["areas"]
                    edge_restrained += res["edge_joints_restrained"]
                done.append(a)
            except (CSIApiError, TypeError, ValueError) as exc:
                failed.append({"area": a, "error": str(exc)[:300]})
    _refresh_view(sap)
    out = {**plan, "done": len(done), "failed": failed, "model_was_unlocked": was_locked}
    if new_areas:
        out["new_areas"] = {k: v for k, v in list(new_areas.items())[:50]}
        out["areas_created"] = sum(len(v) for v in new_areas.values())
        if p.restraints_on_edges:
            out["edge_joints_restrained"] = len(edge_restrained)
    if snap:
        out["snapshot"] = snap
    if failed:
        out["message"] = f"{len(failed)} of {len(areas)} areas were not meshed."
        raise ActionFailed(out)
    return out


# --------------------------------------------------------------------------- joint patterns

class PatternP(Params):
    software: Software = SOFTWARE
    name: str = Field(min_length=1, max_length=60, description="joint pattern name (created if new)")
    method: Literal["hydrostatic", "linear"] = Field(description=(
        "hydrostatic: value = unit_weight x (fluid_level - z) below the level, 0 above | linear: value = "
        "A*x + B*y + C*z + D"))
    fluid_level: float | None = Field(default=None, description="hydrostatic: Z of the fluid surface, units")
    unit_weight: float | None = Field(default=None, gt=0, description=(
        "hydrostatic: unit weight of the fluid in force / length^3 of the units given (e.g. 10 kN/m3 for water); "
        "from the user"))
    a: float = Field(default=0.0, description="linear: coefficient of x")
    b: float = Field(default=0.0, description="linear: coefficient of y")
    c: float = Field(default=0.0, description="linear: coefficient of z")
    d: float = Field(default=0.0, description="linear: constant")
    restriction: Literal["all", "positive_only", "negative_only"] = Field(default="all", description=(
        "linear: keep all values | positive_only (negative set to 0) | negative_only"))
    point_names: list[str] | None = Field(default=None, max_length=20000, description="joints; default the group")
    group: str | None = Field(default="ALL", description="joints of this group (default ALL) when no point_names")
    replace: bool = Field(default=True, description="replace the joints' earlier values of this pattern")
    units: UnitPreset = UNITS
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


@action("define_joint_pattern", "Create / update a joint pattern - hydrostatic (unit weight x depth below a "
        "fluid level) or linear (A*x + B*y + C*z + D) - for surface or pore pressure on areas (assign_area_load "
        "type surface_pressure with joint_pattern). Reports the values at the highest and lowest joints.",
        "Modelling", "edit", PatternP, programs="S")
def define_joint_pattern(p: PatternP) -> dict[str, Any]:
    programs.require(p.software, "joint_patterns")
    if p.method == "hydrostatic":
        if p.fluid_level is None or p.unit_weight is None:
            raise CSIValidationError("hydrostatic needs fluid_level and unit_weight (from the user).")
        a, b, c, d, restriction = 0.0, 0.0, -float(p.unit_weight), float(p.unit_weight) * float(p.fluid_level), \
            "positive_only"
    else:
        a, b, c, d, restriction = p.a, p.b, p.c, p.d, p.restriction
    sap = bridge.sap_model(p.software)
    if p.point_names:
        points = _targets(sap, "PointObj", p.point_names, None, "points")
        targets, item = points, 0
    else:
        groups = bridge.name_list(sap, "GroupDef")
        real = next((g for g in groups if g.lower() == (p.group or "ALL").lower()), None)
        if real is None:
            raise CSIValidationError(f"Group '{p.group}' is not defined.", hint=f"Groups: {groups[:30]}")
        targets, item = [real], 1
    rule = {"A": a, "B": b, "C": c, "D": d, "restriction": restriction,
            "value": "A*x + B*y + C*z + D" + (" (negative values set to 0)" if restriction == "positive_only" else "")}
    existed = p.name in sap_api.pattern_names(sap)
    plan = {"pattern": p.name, "exists": existed, "method": p.method, "rule": rule,
            "applied_to": targets if item == 0 else f"group {targets[0]}", "units": describe_units(p.units)}
    if p.dry_run:
        locked = bridge.is_locked(sap)
        return _dry(plan, model_locked=locked, needs_allow_unlock=bool(locked) and not p.allow_unlock)
    snap, was_locked = _start_edit(p, sap, "before_pattern", "define a joint pattern")
    with bridge.units_scope(sap, p.units):
        if not existed:
            sap_api.define_pattern(sap, p.name)
        for t in targets:
            sap_api.set_pattern_xyz(sap, t, item, p.name, a, b, c, d, restriction, p.replace)
        # read back a few values: highest and lowest joints of the targets
        from model_read import read_points
        pts = [q for q in read_points(sap, p.software) if item == 1 or q["name"] in set(targets)]
        pts.sort(key=lambda q: q["z"])
        sample = pts[:3] + pts[-3:] if len(pts) > 6 else pts
        values = [{"point": q["name"], "x": q["x"], "y": q["y"], "z": q["z"],
                   "value": sap_api.pattern_value(sap, q["name"], p.name)} for q in sample]
    out = {**plan, "created": not existed, "sample_values": values, "model_was_unlocked": was_locked}
    if snap:
        out["snapshot"] = snap
    return out


# --------------------------------------------------------------------------- area loads

class AreaLoadP(Params):
    software: Software = SOFTWARE
    area_names: list[str] | None = NAMES
    group: str | None = GROUP
    load_pattern: str = Field(description="an existing load pattern")
    load_type: Literal["uniform", "uniform_to_frame", "surface_pressure"] = Field(description=(
        "uniform: force per area on the area | uniform_to_frame: uniform load carried to the frames around the "
        "area, one- or two-way[[S: | surface_pressure: pressure on a shell face, optionally scaled by a joint "
        "pattern]]"))
    value: float = Field(description=(
        "force per area (units); Gravity: positive acts downward[[S: | surface_pressure: pressure, or the multiplier "
        "of the joint pattern values (1 when the pattern already holds the pressure)]]"))
    direction: str = Field(default="Gravity", description=(
        "Gravity | X | Y | Z | Local1 | Local2 | Local3 | ProjectedX | ProjectedY | "
        "ProjectedZ | ProjectedGravity"))
    distribution: Literal["two_way", "one_way"] = Field(default="two_way", description="uniform_to_frame only")
    face: Literal["top", "bottom"] = Field(default="bottom", description=(
        "surface_pressure: shell face the pressure acts on - bottom (local -3 side) or top (local +3 side); "
        "a positive pressure pushes on that face toward the shell"))
    joint_pattern: str | None = Field(default=None, description="surface_pressure: joint pattern scaling the pressure")
    replace: bool = Field(default=True, description="replace this load pattern's earlier loads of the same type")
    units: UnitPreset = UNITS
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


@action("assign_area_load", "Area loads: uniform (force per area) or uniform to frames (one- / two-way)[[S:; "
        "surface pressure on a shell face scaled by a joint pattern (hydrostatic / earth pressure on walls)]]. Values "
        "and patterns come from the user.", "Modelling", "edit", AreaLoadP,
        options={"load_type": {"surface_pressure": "S"}}, fields={"face": "S", "joint_pattern": "S"})
def assign_area_load(p: AreaLoadP) -> dict[str, Any]:
    if p.load_type == "surface_pressure":
        programs.require(p.software, "surface_pressure")
    elif p.joint_pattern:
        raise CSIValidationError("joint_pattern is only used with load_type surface_pressure.")
    sap = bridge.sap_model(p.software)
    patterns = bridge.name_list(sap, "LoadPatterns")
    if p.load_pattern not in patterns:
        raise CSIValidationError(f"Load pattern '{p.load_pattern}' is not defined.", hint=f"Defined: {patterns}")
    if p.joint_pattern and p.joint_pattern not in sap_api.pattern_names(sap):
        raise CSIValidationError(f"Joint pattern '{p.joint_pattern}' is not defined.",
                                 hint="Create it with define_joint_pattern.")
    areas = _targets(sap, "AreaObj", p.area_names, p.group, "areas")
    dkey = p.direction.replace(" ", "").replace("_", "").replace("-", "").lower()
    if p.load_type != "surface_pressure" and dkey not in _FRAME_DIRS:
        raise CSIValidationError(f"Direction '{p.direction}' is not supported.", hint=f"Use one of {list(_FRAME_DIRS)}.")
    plan: dict[str, Any] = {"load_type": p.load_type, "load_pattern": p.load_pattern, "value": p.value,
                            "areas": len(areas), "first_areas": areas[:20], "replace_existing": p.replace,
                            "units": describe_units(p.units)}
    if p.load_type == "surface_pressure":
        plan.update(face=p.face, joint_pattern=p.joint_pattern or None,
                    pressure="value x joint pattern value at each joint" if p.joint_pattern else "uniform")
    else:
        plan.update(direction=p.direction, note="Gravity: positive acts downward" if dkey == "gravity" else "")
        if p.load_type == "uniform_to_frame":
            plan["distribution"] = p.distribution
    if p.dry_run:
        locked = bridge.is_locked(sap)
        return _dry(plan, model_locked=locked, needs_allow_unlock=bool(locked) and not p.allow_unlock)
    snap, was_locked = _start_edit(p, sap, "before_area_loads", "assign area loads")
    done, failed = [], []
    with bridge.units_scope(sap, p.units):
        for a in areas:
            try:
                if p.load_type == "uniform":
                    code = _FRAME_DIRS[dkey]
                    api_call(f"AreaObj.SetLoadUniform('{a}')", sap.AreaObj.SetLoadUniform, a, p.load_pattern,
                             float(p.value), code, p.replace, "Local" if code <= 3 else "Global", 0)
                elif p.load_type == "uniform_to_frame":
                    code = _FRAME_DIRS[dkey]
                    api_call(f"AreaObj.SetLoadUniformToFrame('{a}')", sap.AreaObj.SetLoadUniformToFrame, a,
                             p.load_pattern, float(p.value), code, 1 if p.distribution == "one_way" else 2, p.replace,
                             "Local" if code <= 3 else "Global", 0)
                else:
                    sap_api.set_surface_pressure(sap, a, p.load_pattern, sap_api.FACE_CODES[p.face], p.value,
                                                 p.joint_pattern or "", p.replace)
                done.append(a)
            except CSIApiError as exc:
                failed.append({"area": a, "error": str(exc)[:300]})
    _refresh_view(sap)
    out = {**plan, "assigned": len(done), "failed": failed, "model_was_unlocked": was_locked}
    if snap:
        out["snapshot"] = snap
    if failed:
        out["message"] = f"{len(failed)} of {len(areas)} areas were not loaded."
        raise ActionFailed(out)
    return out
