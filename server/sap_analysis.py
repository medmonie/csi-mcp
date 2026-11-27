"""Phase 5, SAP2000 analysis features (0.19.0): staged construction cases, frame hinges, moving loads (lanes,
vehicles, moving-load cases).

Staged construction: typed calls LoadCases.StaticNonlinearStaged (SetCase, SetStageDefinitions_2, SetStageData_2,
SetGeometricNonlinearity, SetInitialCase), read back with GetStageDefinitions_2 / GetStageData_2.
Hinges, lanes and vehicles have no typed setters in SAP2000 27.1 (measured: FrameObj has only GetHingeAssigns*; no
Lane / Vehicle objects): they go through the program's tables ('Frame Hinge Assigns NN - ...', 'Lane Definition
Data', 'Vehicles 1 - Standard Vehicles', 'Vehicles 4 - Vehicle Classes'), whose field values come from the designer;
moving-load cases through the typed LoadCases.Moving calls.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from actions import Params, _dry, _refresh_view, action, bridge
from autoload import table_fields
from csi_bridge import CSIValidationError, api_call, as_list, describe_units
from db_edit import edit_table, read_table, write_table
from model_read import _try
from modelling import _start_edit
from objects import _EditP

# --------------------------------------------------------------------------- staged construction

OPERATIONS = {"add": 1, "remove": 2, "load_if_new": 3, "load": 4, "change_section": 5, "change_modifiers": 6,
              "change_releases": 7, "change_section_and_age": 11}
LOAD_OPS = ("load_if_new", "load")


class StageOp(Params):
    operation: Literal[tuple(OPERATIONS)] = Field(description=(  # type: ignore[valid-type]
        "add (structure) | remove (structure) | load_if_new (load only objects added in this stage) | load (all "
        "objects of the group) | change_section | change_modifiers | change_releases | change_section_and_age"))
    group: str = Field(description="the group the operation acts on (set_group)")
    age: float = Field(default=0.0, ge=0, description="add: age of the added objects at the stage start (days)")
    load_type: Literal["load_pattern", "acceleration"] = Field(default="load_pattern", description=(
        "load operations: load_pattern | acceleration"))
    load_name: str = Field(default="", description=(
        "load operations: the load pattern (or UX / UY / UZ); change_section: the new section; change_modifiers / "
        "change_releases: the named set"))
    factor: float = Field(default=1.0, description="load operations: scale factor")


class Stage(Params):
    duration: float = Field(default=0.0, ge=0, description="stage duration (days)")
    operations: list[StageOp] = Field(min_length=1, max_length=500, description="the stage's operations, in order")
    output: bool = Field(default=True, description="save results at the end of this stage")
    output_name: str = Field(default="", description="name for this stage's output")
    comment: str = Field(default="", description="comment")


class StagedP(_EditP):
    name: str = Field(min_length=1, description="the staged construction load case")
    stages: list[Stage] = Field(min_length=1, max_length=500, description="the stages, in order")
    geometric_nonlinearity: Literal["none", "p_delta", "p_delta_large_displacements"] = Field(description=(
        "none | p_delta | p_delta_large_displacements"))
    initial_case: str | None = Field(default=None, description="start from the end of this nonlinear case")
    replace: bool = Field(default=False, description="change an existing case")


@action("define_staged_construction", "Create or change a nonlinear staged-construction load case: stages with "
        "duration and operations (add / remove structure by group with age, load patterns on new or all objects of a "
        "group, section / modifier / release changes), geometric nonlinearity, initial case. Answers with the stages "
        "read back.", "Definitions", "edit", StagedP, programs="ES")
def define_staged_construction(p: StagedP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    cases = bridge.try_name_list(sap, ["LoadCases"])[0] or []
    if p.name in cases and not p.replace:
        raise CSIValidationError(f"Load case '{p.name}' exists.", hint="replace: true changes it.")
    groups = set(bridge.try_name_list(sap, ["GroupDef"])[0] or [])
    pats = set(bridge.try_name_list(sap, ["LoadPatterns"])[0] or [])
    for i, st in enumerate(p.stages, 1):
        for op in st.operations:
            if op.group not in groups:
                raise CSIValidationError(f"Stage {i}: group '{op.group}' is not defined.", hint="set_group defines it.")
            if op.operation in LOAD_OPS and op.load_type == "load_pattern" and op.load_name not in pats:
                raise CSIValidationError(f"Stage {i}: load pattern '{op.load_name}' is not defined.")
    if p.initial_case and p.initial_case not in cases:
        raise CSIValidationError(f"Initial case '{p.initial_case}' is not defined.")
    plan = {"case": p.name, "stages": len(p.stages), "geometric_nonlinearity": p.geometric_nonlinearity,
            "initial_case": p.initial_case}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_staged_case", "define a staged construction case")
    st = sap.LoadCases.StaticNonlinearStaged
    api_call("StaticNonlinearStaged.SetCase", st.SetCase, p.name)
    n = len(p.stages)
    api_call("StaticNonlinearStaged.SetStageDefinitions_2", st.SetStageDefinitions_2, p.name, n,
             [s.duration for s in p.stages], [s.output for s in p.stages], [s.output_name for s in p.stages],
             [s.comment for s in p.stages])
    for i, s in enumerate(p.stages, 1):
        ops = s.operations
        api_call("StaticNonlinearStaged.SetStageData_2", st.SetStageData_2, p.name, i, len(ops),
                 [OPERATIONS[o.operation] for o in ops], ["Group"] * len(ops), [o.group for o in ops],
                 [o.age for o in ops],
                 [("Load" if o.load_type == "load_pattern" else "Accel") if o.operation in LOAD_OPS else ""
                  for o in ops],
                 [o.load_name for o in ops], [o.factor if o.operation in LOAD_OPS else 0.0 for o in ops])
    api_call("StaticNonlinearStaged.SetGeometricNonlinearity", st.SetGeometricNonlinearity, p.name,
             {"none": 0, "p_delta": 1, "p_delta_large_displacements": 2}[p.geometric_nonlinearity])
    if p.initial_case:
        api_call("StaticNonlinearStaged.SetInitialCase", st.SetInitialCase, p.name, p.initial_case)
    back: list[dict[str, Any]] = []
    d = _try(sap, "LoadCases.StaticNonlinearStaged.GetStageDefinitions_2", p.name, 0, [], [], [], [])
    durations = as_list(d[1]) if d else []
    inv = {v: k for k, v in OPERATIONS.items()}
    for i in range(1, n + 1):
        # Stage is passed by reference too (measured signature), so the outputs are Stage, NumberOperations, ...
        g = _try(sap, "LoadCases.StaticNonlinearStaged.GetStageData_2", p.name, i, 0, [], [], [], [], [], [], [])
        ops = []
        if g:
            for k in range(int(g[1])):
                ops.append({"operation": inv.get(int(as_list(g[2])[k]), as_list(g[2])[k]),
                            "group": str(as_list(g[4])[k]), "age": float(as_list(g[5])[k]),
                            "load_type": str(as_list(g[6])[k]), "load_name": str(as_list(g[7])[k]),
                            "factor": float(as_list(g[8])[k])})
        back.append({"stage": i, "duration": float(durations[i - 1]) if len(durations) >= i else None,
                     "operations": ops})
    want = [len(s.operations) for s in p.stages]
    if [len(b["operations"]) for b in back] != want:
        raise CSIValidationError(f"Read back differs: {back}")
    return {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- frame hinges

def hinge_tables(sap: Any) -> list[str]:
    o = sap.DatabaseTables.GetAllTables()
    return [str(k) for k in as_list(o[1]) if str(k).startswith("Frame Hinge Assigns")]


class HingeTablesP(Params):
    software: Literal["SAP2000"] = "SAP2000"
    table: str | None = Field(default=None, description="a hinge table key: answers with its fields (default: the list)")


@action("describe_hinge_tables", "The program's frame hinge assignment tables (auto hinges by code and element "
        "type: ASCE 41-13 / 41-17, FEMA 356, Caltrans; user-defined hinge properties), or one table's fields with "
        "units and descriptions - the values assign_frame_hinges needs from the designer.", "Definitions", "read",
        HingeTablesP, programs="S")
def describe_hinge_tables(p: HingeTablesP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    keys = hinge_tables(sap)
    if not p.table:
        return {"tables": keys}
    if p.table not in keys:
        raise CSIValidationError(f"'{p.table}' is not a hinge table.", hint=f"Tables: {keys}")
    return {"table": p.table, "fields": table_fields(sap, p.table)}


class HingeP(_EditP):
    frame_names: list[str] = Field(min_length=1, max_length=2000, description="frames")
    table: str = Field(description="the hinge table (describe_hinge_tables), e.g. 'Frame Hinge Assigns 19 - Auto "
                       "ASCE 41-17 - Steel Beam'")
    relative_distances: list[float] = Field(min_length=1, max_length=20, description=(
        "hinge locations along each frame, relative 0..1"))
    values: dict[str, str | float | int | bool] = Field(default_factory=dict, description=(
        "the table's other fields with the designer's values (e.g. DOF: M3, BeyondE: Extrapolated, and the code's "
        "parameters); fields not given take the program's values and are listed"))
    replace: bool = Field(default=False, description="replace the frames' existing hinges in this table")


@action("assign_frame_hinges", "Assign frame hinges (auto hinges by ASCE 41 / FEMA 356 / Caltrans, or user hinge "
        "properties) at relative distances, through the program's hinge table with the designer's values. Answers "
        "with the hinges read back from the frames.", "Objects", "edit", HingeP, programs="S")
def assign_frame_hinges(p: HingeP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    keys = hinge_tables(sap)
    if p.table not in keys:
        raise CSIValidationError(f"'{p.table}' is not a hinge table.", hint=f"Tables: {keys}")
    frames = set(bridge.try_name_list(sap, ["FrameObj"])[0] or [])
    missing = [f for f in p.frame_names if f not in frames]
    if missing:
        raise CSIValidationError(f"Frames not in the model: {missing[:20]}.")
    if any(not 0 <= d <= 1 for d in p.relative_distances):
        raise CSIValidationError("relative_distances are between 0 and 1.")
    # a hinge table for steel on a concrete section (or the reverse) makes SAP2000 show a modal 'Error in
    # regenerating hinges' message that blocks the API until someone clicks OK (measured) - refuse it here
    kind = "steel" if "Steel" in p.table else "concrete" if "Concrete" in p.table else None
    if kind:
        wrong = []
        for f in p.frame_names:
            sec = _try(sap, "FrameObj.GetSection", f)
            mat = _try(sap, "PropFrame.GetMaterial", str(sec[0])) if sec else None
            mt = _try(sap, "PropMaterial.GetMaterial", str(mat[0])) if mat else None
            if mt and int(mt[0]) != (1 if kind == "steel" else 2):
                wrong.append(f)
        if wrong:
            raise CSIValidationError(f"'{p.table}' is for {kind} sections; these frames are not: {wrong[:20]}.",
                                     hint="Pick the table for the frames' material (describe_hinge_tables).")
    fields = [f["field"] for f in table_fields(sap, p.table)]
    unknown = sorted(set(p.values) - set(fields))
    if unknown:
        raise CSIValidationError(f"Table '{p.table}' has no fields {unknown}.", hint=f"Fields: {fields}")
    vals = {k: ("Yes" if v is True else "No" if v is False else v) for k, v in p.values.items()}
    # how the row is laid out, not design values (measured accepted in SAP2000 27.1): a point hinge at a relative
    # distance, no fiber response saved, no hinge length overwrite - unless the designer gives them
    plumbing = {"HgeDistrTyp": "Point", "DistType": "Relative", "SaveFibResp": "No", "LenOWType": "None"}
    vals = {**{k: v for k, v in plumbing.items() if k in fields}, **vals}
    rows = [{"Frame": f, "RelDist": d, **vals} for f in p.frame_names for d in p.relative_distances]
    plan = {"table": p.table, "frames": len(p.frame_names), "relative_distances": p.relative_distances,
            "values": vals, "program_values_not_given": [f for f in fields if f not in vals and f not in
                                                         ("Frame", "RelDist", "AbsDist", "ActualDist", "ActualRDist")]}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_hinges", "assign frame hinges")
    names = set(p.frame_names)
    res = edit_table(sap, p.table, rows, drop=(lambda r: r.get("Frame") in names) if p.replace else None)
    _refresh_view(sap)
    back = {}
    for f in p.frame_names[:50]:
        g = _try(sap, "FrameObj.GetHingeAssigns_1", f, 0, [], [], [], [], [], [], [], [])
        if g:
            # the program places a point hinge at the middle of its hinge length: a hinge at relative distance 0
            # sits half a hinge length in (measured: 0.15 m for a 0.3 m deep section, hinge length 1 x depth)
            back[f] = [{"hinge": str(as_list(g[2])[k]), "relative_distance": round(float(as_list(g[7])[k]), 6),
                        "actual_distance": round(float(as_list(g[8])[k]), 6)} for k in range(int(g[0]))]
    short = [f for f in p.frame_names[:50] if len(back.get(f, [])) < len(p.relative_distances)]
    out = {**plan, "table_edit": res, "read_back": back, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if short:
        out["warning"] = f"Frames without all their hinges read back: {short[:20]}"
    return out


# --------------------------------------------------------------------------- moving loads

LANE_KEY, STD_VEH_KEY, CLASS_KEY = "Lane Definition Data", "Vehicles 1 - Standard Vehicles", "Vehicles 4 - Vehicle Classes"


class LaneP(_EditP):
    name: str = Field(min_length=1, description="lane name")
    frames: list[str] = Field(min_length=1, max_length=500, description="the frames the lane follows, in order")
    width: float = Field(ge=0, description="lane width (present units)")
    offset: float = Field(default=0.0, description="offset of the lane centre line from the frames")
    values: dict[str, str | float | int] = Field(default_factory=dict, description=(
        "other fields of 'Lane Definition Data' with the designer's values (discretization, edge types ...)"))


@action("define_lane", "Define a moving-load lane along frames with width and offset (and other lane table fields "
        "from the designer). Answers with the lane rows read back.", "Definitions", "edit", LaneP, programs="S")
def define_lane(p: LaneP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    frames = set(bridge.try_name_list(sap, ["FrameObj"])[0] or [])
    missing = [f for f in p.frames if f not in frames]
    if missing:
        raise CSIValidationError(f"Frames not in the model: {missing[:20]}.")
    rows = [{"Lane": p.name, "LaneFrom": "Frame", "Frame": f, "Width": p.width, "Offset": p.offset, **p.values}
            for f in p.frames]
    plan = {"lane": p.name, "frames": p.frames, "width": p.width, "offset": p.offset, "values": p.values,
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_lane", "define a lane")
        res = edit_table(sap, LANE_KEY, rows, drop=lambda r: r.get("Lane") == p.name)
        back = [r for r in read_table(sap, LANE_KEY)[2] if r.get("Lane") == p.name]
    if len(back) != len(p.frames):
        raise CSIValidationError(f"Lane read back has {len(back)} rows for {len(p.frames)} frames.", hint=str(back))
    return {**plan, "table_edit": res, "read_back": back, "model_was_unlocked": was_locked,
            **({"snapshot": snap} if snap else {})}


class StdVehicle(Params):
    name: str = Field(min_length=1, description="vehicle name")
    standard_type: str = Field(description="the program's standard vehicle type as the program names it, e.g. "
                               "HL-93K, HL-93M, Hn-44, HSn-44 (the program's Standard Vehicles list)")
    scale_factor: float = Field(gt=0, description="scale factor (for Hn-44 / HSn-44 the program's n: 20 = H20)")
    dynamic_allowance: float = Field(ge=0, description="dynamic allowance (%), where the type uses it")
    vehicle_class: str | None = Field(default=None, description="also put the vehicle in this vehicle class "
                                      "(factor 1); the program also makes a class of the vehicle's own name")
    values: dict[str, str | float | int] = Field(default_factory=dict, description=(
        "other fields of 'Vehicles 1 - Standard Vehicles' with the designer's values"))


class VehicleP(_EditP):
    vehicles: list[StdVehicle] = Field(min_length=1, max_length=50, description="all the model's standard vehicles")
    replace_existing: bool = Field(default=False, description=(
        "the model already has vehicles: true replaces them with these"))


@action("define_vehicles", "Define the model's standard vehicles (the program's built-in types: HL-93, H / HS ...) "
        "with scale factor and dynamic allowance, optionally in vehicle classes - all in one call, because the "
        "program's vehicle table is written whole and cannot be read back through the API (measured). Answers with "
        "the vehicle classes read back.", "Definitions", "edit", VehicleP, programs="S")
def define_vehicles(p: VehicleP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    _, _, classes = read_table(sap, CLASS_KEY)
    existing = sorted({r.get("VehName") for r in classes if r.get("VehName")})
    if existing and not p.replace_existing:
        raise CSIValidationError(f"The model already has vehicles {existing[:20]}; the vehicle table is written "
                                 "whole.", hint="Give all vehicles with replace_existing: true.")
    names = [v.name for v in p.vehicles]
    if len(set(names)) != len(names):
        raise CSIValidationError("Vehicle names repeat.")
    plan = {"vehicles": [v.model_dump() for v in p.vehicles], "replaced": existing}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_vehicles", "define vehicles")
    version, fields, _ = read_table(sap, STD_VEH_KEY)
    rows = [{"VehName": v.name, "Type": v.standard_type, "ScaleFactor": v.scale_factor,
             "DynAllow": v.dynamic_allowance, **v.values} for v in p.vehicles]
    unknown = sorted({k for r in rows for k in r} - set(fields))
    if unknown:
        raise CSIValidationError(f"'{STD_VEH_KEY}' has no fields {unknown}.", hint=f"Fields: {fields}")
    use = [f for f in fields if any(f in r for r in rows)]
    res = write_table(sap, STD_VEH_KEY, version, use,
                      [{f: "" if r.get(f) is None else str(r.get(f)) for f in use} for r in rows])
    extra = [{"VehClass": v.vehicle_class, "VehName": v.name, "ScaleFactor": 1} for v in p.vehicles
             if v.vehicle_class]
    if extra:
        pairs = {(r["VehClass"], r["VehName"]) for r in extra}
        res["class_edit"] = edit_table(sap, CLASS_KEY, extra,
                                       drop=lambda r: (r.get("VehClass"), r.get("VehName")) in pairs)
    back = read_table(sap, CLASS_KEY)[2]
    missing = [n for n in names if n not in {r.get("VehName") for r in back}]
    if missing:
        raise CSIValidationError(f"Vehicles not in the program's vehicle classes after the edit: {missing}.",
                                 hint=str(res))
    return {**plan, "table_edit": res, "vehicle_classes_read_back": back, "model_was_unlocked": was_locked,
            **({"snapshot": snap} if snap else {})}


class MovingAssign(Params):
    vehicle_class: str = Field(description="a vehicle class")
    scale_factor: float = Field(description="scale factor")
    min_lanes_loaded: int = Field(ge=0, description="minimum number of lanes loaded")
    max_lanes_loaded: int = Field(ge=0, description="maximum number of lanes loaded (0 = all)")
    lanes: list[str] = Field(min_length=1, description="lanes this vehicle class may load")


class MovingCaseP(_EditP):
    name: str = Field(min_length=1, description="moving-load case name")
    assignments: list[MovingAssign] = Field(min_length=1, max_length=50, description="vehicle class assignments")
    multilane_factors: list[float] = Field(min_length=1, max_length=50, description=(
        "reduction factor for 1, 2, 3 ... lanes loaded (designer's code)"))
    replace: bool = Field(default=False, description="change an existing case")


@action("define_moving_load_case", "Create or change a moving-load case: vehicle classes with scale factors, lanes "
        "loaded (min / max) and multi-lane reduction factors, all from the designer. Answers with the case read back "
        "from the program's moving-load case tables.", "Definitions", "edit", MovingCaseP, programs="S")
def define_moving_load_case(p: MovingCaseP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    cases = bridge.try_name_list(sap, ["LoadCases"])[0] or []
    if p.name in cases and not p.replace:
        raise CSIValidationError(f"Load case '{p.name}' exists.", hint="replace: true changes it.")
    plan = {"case": p.name, "assignments": [a.model_dump() for a in p.assignments],
            "multilane_factors": p.multilane_factors}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_moving_case", "define a moving-load case")
    mv = sap.LoadCases.Moving
    api_call("LoadCases.Moving.SetCase", mv.SetCase, p.name)
    a = p.assignments
    api_call("LoadCases.Moving.SetLoads", mv.SetLoads, p.name, len(a), [x.vehicle_class for x in a],
             [x.scale_factor for x in a], [x.min_lanes_loaded for x in a], [x.max_lanes_loaded for x in a])
    for i, x in enumerate(a, 1):
        api_call("LoadCases.Moving.SetLanesLoaded", mv.SetLanesLoaded, p.name, i, len(x.lanes), x.lanes)
    api_call("LoadCases.Moving.SetMultiLaneSF", mv.SetMultiLaneSF, p.name, len(p.multilane_factors),
             p.multilane_factors)
    back = {}
    for key in ("Case - Moving Load 1 - Lane Assignments", "Case - Moving Load 2 - Lanes Loaded",
                "Case - Moving Load 3 - MultiLane Factors"):
        try:
            back[key] = [r for r in read_table(sap, key)[2] if r.get("Case") == p.name]
        except Exception as exc:  # noqa: BLE001
            back[key] = f"not read: {exc}"
    return {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
