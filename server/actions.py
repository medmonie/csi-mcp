"""Every action Claude can ask ETABS / SAP2000 / SAFE to do, defined once.

Each action has a Pydantic parameter model (unknown parameters are refused) and an implementation that runs on the
COM thread. catalog.py renders the wording Claude sees from these definitions, so the two cannot drift apart.

An implementation returns a dict on success. It raises:
  CSIError / TableError      -> the call failed; message + hint go back to Claude as a tool error
  ActionFailed(payload)      -> the call ran but did not fully succeed (some loads failed, analysis cases failed,
                                model locked and no permission to unlock); the payload goes back as a tool error
"""
from __future__ import annotations

import contextlib
import math
import os
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from csi_bridge import (
    COM_AVAILABLE,
    LOAD_CASE_TYPES,
    LOAD_PATTERN_TYPES,
    SPECS,
    CSIApiError,
    CSIBridge,
    CSIConnectionError,
    CSIError,
    CSIValidationError,
    Software,
    UnitPreset,
    COMError,
    api_call,
    as_list,
    describe_units,
    user_path,
)
import programs
import safety
from table_tools import (
    TableError,
    apply_filters,
    filter_by_cases,
    numeric_summary,
    records,
    resolve_exact_key,
    resolve_result_table,
    rows_to_dataframe,
    search_tables,
    sort_frame,
    suggest_keys,
    summarize_base_reactions,
    summarize_joint_displacements,
    summarize_modal,
    summarize_story_drifts,
)

bridge = CSIBridge()


class ActionFailed(Exception):
    """The action ran but the outcome needs attention; `payload` is returned to Claude as a tool error."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(payload.get("message", "action did not fully succeed"))
        self.payload = payload


# --------------------------------------------------------------------------- parameter models

class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


SOFTWARE = Field(description="ETABS | SAP2000 | SAFE")
UNITS = Field(default="kN_m_C", description="unit preset for this call (force_length_temperature): kN_m_C (default), "
                                            "Ton_m_C, N_mm_C, kN_mm_C, kip_ft_F, kip_in_F ... (16 CSI presets)")
DRY_RUN = Field(default=False, description="check everything and report what would happen; change nothing")
SNAPSHOT = Field(default=False, description=("first copy the model file as last saved to _csi_mcp_snapshots next "
                                             "to the model"))
ALLOW_UNLOCK = Field(default=False, description="true ONLY after the user agreed that unlocking the model DELETES its "
                                                "analysis results")


class NoParams(Params):
    pass


class HelpP(Params):
    topic: str | None = Field(default=None, max_length=80, description=(
        "an action name for its full parameter list; omitted = the list of actions; 'all' = everything (very large)"))


class SoftwareP(Params):
    software: Software = SOFTWARE


class ConnectP(Params):
    software: Software = SOFTWARE
    launch: bool = Field(default=False, description="start the program when it is not running")
    visible: bool = Field(default=True, description="show the window of a program started here")
    model_path: str | None = Field(default=None, description="full path of an .edb / .sdb / .fdb to open after connecting")
    program_path: str | None = Field(default=None, description="full path of the program .exe, to start one specific version")
    dry_run: bool = DRY_RUN


class OpenSaveP(Params):
    software: Software = SOFTWARE
    action: Literal["open", "save"] = Field(description="open | save")
    file_path: str | None = Field(default=None, description="needed for open; for save: omit = save in place, give = Save As")
    dry_run: bool = DRY_RUN


class SetUnitsP(Params):
    software: Software = SOFTWARE
    unit_preset: UnitPreset = Field(description="kN_m_C, Ton_m_C, N_mm_C, kip_ft_F ... (the program's display units)")
    dry_run: bool = DRY_RUN


class DefinitionsP(Params):
    software: Software = SOFTWARE
    max_items: int = Field(default=200, ge=1, le=5000, description="per list, default 200")


class FrameP(Params):
    software: Software = SOFTWARE
    start_xyz: list[float] = Field(min_length=3, max_length=3, description="[x, y, z] in the length unit of `units`")
    end_xyz: list[float] = Field(min_length=3, max_length=3, description="[x, y, z]")
    section_name: str = Field(description="an existing frame section (list_definitions)")
    units: UnitPreset = UNITS
    user_name: str = Field(default="", description="name for the new frame; default: the program numbers it")
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


class LoadP(Params):
    software: Software = SOFTWARE
    target_type: Literal["point", "frame"] = Field(description="point (joint force / moment) | frame (distributed load)")
    object_names: list[str] = Field(min_length=1, max_length=2000, description="point or frame object names")
    load_pattern: str = Field(description="an existing load pattern")
    values: list[float] = Field(min_length=1, max_length=6, description=(
        "point: [P] with direction, or [F1,F2,F3,M1,M2,M3] in Global; frame: [w] uniform, [w1,w2] start->end, "
        "or [w1,w2,rel_d1,rel_d2]"))
    direction: str = Field(default="Gravity", description=(
        "point: Gravity | X | Y | Z | MX | MY | MZ; frame: Gravity | X | Y | Z | Local1 | Local2 | Local3 | "
        "ProjectedX | ProjectedY | ProjectedZ | ProjectedGravity. Gravity: a positive value acts downward"))
    replace: bool = Field(default=True, description="replace this pattern's existing loads on the objects (false = add)")
    frame_load_type: Literal["force", "moment"] = Field(default="force", description="frame only: force | moment")
    frame_load_shape: Literal["distributed", "point"] = Field(default="distributed", description=(
        "frame only: distributed (per length, values as above) | point (a concentrated force / moment on the frame: "
        "values [P, relative distance 0..1])"))
    units: UnitPreset = UNITS
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


class AnalysisP(Params):
    software: Software = SOFTWARE
    save_before_run: bool = Field(default=True, description="save the model first (default true)")
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


DOF6 = ("UX", "UY", "UZ", "RX", "RY", "RZ")
DOF_PRESETS = {"space": DOF6, "plane_xz": ("UX", "UZ", "RY"), "plane_yz": ("UY", "UZ", "RX"),
               "plane_xy": ("UX", "UY", "RZ"), "grid_xy": ("UZ", "RX", "RY"), "truss_xz": ("UX", "UZ"),
               "truss_3d": ("UX", "UY", "UZ")}


class FloorMesh(Params):
    type: Literal["general", "rectangular"] = Field(description=(
        "general (any shape) | rectangular (quadrilaterals where it can)"))
    max_size: float = Field(gt=0, description="largest element side (length units)")
    localized: bool = Field(default=True, description="rectangular: localize the mesh at points / lines inside")
    merge_joints: bool = Field(default=True, description="rectangular: merge joints that fall close together")


FLOOR_MESH_TABLE = "Analysis Options - Automatic Mesh Settings for Floors"


class AnalysisOptionsP(Params):
    software: Software = SOFTWARE
    active_dof: str | list[Literal[DOF6]] | None = Field(default=None, description=(  # type: ignore[valid-type]
        "degrees of freedom the analysis uses: a preset (space, plane_xz, plane_yz, plane_xy, grid_xy, truss_xz, "
        "truss_3d) or a list of UX UY UZ RX RY RZ"))
    run_cases: dict[str, bool] | None = Field(default=None, description=(
        "set which cases run: {case: true | false}; give {\"*\": false} first to clear all"))
    floor_mesh: FloorMesh | None = Field(default=None, description=(
        "automatic mesh of floors / slabs: type general | rectangular, max_size (length), localized, merge_joints"))
    units: UnitPreset = UNITS
    dry_run: bool = DRY_RUN
    allow_unlock: bool = ALLOW_UNLOCK

    @model_validator(mode="after")
    def _check(self) -> "AnalysisOptionsP":
        if self.active_dof is None and not self.run_cases and self.floor_mesh is None:
            raise ValueError("give active_dof, run_cases and / or floor_mesh")
        if isinstance(self.active_dof, str) and self.active_dof not in DOF_PRESETS:
            raise ValueError(f"active_dof preset must be one of {', '.join(DOF_PRESETS)}")
        return self


class TablesP(Params):
    software: Software = SOFTWARE
    search_term: str = Field(default="", description="words that must all appear in the key or name, e.g. 'drift', 'pier force'")
    max_results: int = Field(default=200, ge=1, le=2000, description="default 200")


class QueryP(Params):
    software: Software = SOFTWARE
    table_key: str = Field(description="exact key from list_available_tables, e.g. 'Story Drifts', 'Joint Reactions'")
    load_cases_or_combos: list[str] | None = Field(default=None, description="cases / combinations to include; default all")
    filters: dict[str, str] | None = Field(default=None, description=(
        "per column: 'Story3' exact | 'Story3|Story4' any of | '~DCON' contains | '>=0.002' '<5' '!=0' numeric"))
    max_rows: int = Field(default=50, ge=1, le=1000, description="rows returned, default 50; the statistics cover every filtered row")
    sort_by: str | None = Field(default=None, description="column to sort by before the rows are cut")
    descending: bool = Field(default=True, description="default true")
    sort_by_abs: bool = Field(default=True, description="sort a numeric column by absolute value, default true")
    units: UnitPreset = UNITS
    export: Literal["auto", "csv", "xlsx", "json", "none"] = Field(default="auto", description=(
        "write the filtered rows to a file in the output folder: auto (default) = CSV when the rows are cut to "
        "max_rows, csv / xlsx / json = always, none = never (several tables in one workbook: "
        "export_tables_to_excel)"))


class SummaryP(Params):
    software: Software = SOFTWARE
    result_type: Literal["base_reactions", "story_drifts", "modal_periods", "joint_displacements"] = Field(
        description="base_reactions | [[E: story_drifts | ]]modal_periods | joint_displacements")
    combos: list[str] | None = Field(default=None, description=(
        "cases / combinations; default all (displacements and drifts: all combinations when there are any)"))
    drift_limit: float | None = Field(default=None, gt=0, description=(
        "story_drifts: a drift ratio given by the engineer, e.g. 0.02; rows and stories above it are listed"))
    units: UnitPreset = UNITS


class SnapshotP(Params):
    software: Software = SOFTWARE
    label: str = Field(default="", max_length=40, description="short text added to the snapshot file name")


class DisconnectP(Params):
    software: Software = SOFTWARE
    close_application: bool = Field(default=False, description="also close the program (ask the user first)")
    save_model: bool = Field(default=False, description="with close_application: save the model first")
    dry_run: bool = DRY_RUN


# --------------------------------------------------------------------------- registry

@dataclass(frozen=True)
class Action:
    name: str
    summary: str
    group: str
    kind: Literal["read", "edit"]
    params: type[Params]
    run: Callable[[Any], dict[str, Any]]
    long_running: bool = False
    programs: str = "ESF"      # E = ETABS, S = SAP2000, F = SAFE: programs whose API has this action
    # option values that only some programs have: {field: {value: "EF"}}; hidden from other editions, refused there
    options: dict[str, dict[str, str]] = field(default_factory=dict)
    # parameters that only some programs use: {field: "EF"}; hidden from other editions, refused there when set
    fields: dict[str, str] = field(default_factory=dict)


ACTIONS: dict[str, Action] = {}


def action(name: str, summary: str, group: str, kind: Literal["read", "edit"], params: type[Params],
           long_running: bool = False, programs: str = "ESF", options: dict[str, dict[str, str]] | None = None,
           fields: dict[str, str] | None = None) -> Callable:
    """Summaries and field descriptions may hold [[EF: text]]: shown only in those programs' editions."""
    if not programs or set(programs) - set("ESF"):
        raise ValueError(f"{name}: invalid program mask {programs!r}")
    for f in list(options or {}) + list(fields or {}):
        if f not in params.model_fields:
            raise ValueError(f"{name}: unknown field {f}")
    def register(fn: Callable[[Any], dict[str, Any]]) -> Callable:
        if name in ACTIONS:
            raise ValueError(f"{name} registered twice")
        ACTIONS[name] = Action(name, summary, group, kind, params, fn, long_running, programs, dict(options or {}),
                               dict(fields or {}))
        return fn
    return register


def _dry(would: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {"dry_run": True, "changed": False, "would": would, **extra}


def _take_snapshot(software: str, sap: Any, label: str) -> dict[str, Any]:
    path = bridge.model_filename(sap, software)
    if not path:
        raise CSIValidationError("The model has never been saved, so there is no file to snapshot.",
                                 hint="Save it first (open_or_save_model with action='save' and a file_path), "
                                      "or call again with snapshot: false.")
    try:
        return safety.snapshot_file(path, label)
    except OSError as exc:
        raise CSIValidationError(f"Snapshot failed: {exc}") from exc


def _refresh_view(sap: Any) -> None:
    with contextlib.suppress(Exception):
        sap.View.RefreshView(0, False)


def _lock(sap: Any, allow_unlock: bool, what: str) -> bool:
    """Raise ActionFailed when locked without permission; unlock when allowed. Returns 'was locked'."""
    outcome = bridge.lock_guard(sap, allow_unlock, what)
    if outcome and outcome.get("status") == "confirmation_required":
        raise ActionFailed(outcome)
    return bool(outcome)


# --------------------------------------------------------------------------- session

@action("list_programs", "Whether the program is running and reachable, with version and open model.",
        "Session", "read", NoParams)
def list_programs(_: NoParams) -> dict[str, Any]:
    out = {}
    for name in programs.enabled():
        try:
            out[name] = {"reachable": True, **_status_core(name, counts=False)}
        except CSIError as exc:
            out[name] = {"reachable": False, "reason": str(exc).split(". Tried:")[0]}
    return {"programs": out, "com_available": COM_AVAILABLE}


@action("connect", "Attach to the running program, or start it (launch=true), optionally opening a model. Reads attach "
        "by themselves; use this to start a program or open a file.", "Session", "edit", ConnectP, long_running=True)
def connect(p: ConnectP) -> dict[str, Any]:
    if p.dry_run:
        try:
            running = _status_core(p.software, counts=False)
        except CSIConnectionError:
            return _dry({"action": "start the program" if p.launch else "fail: not running and launch is false",
                         "open_model": p.model_path}, running=False)
        return _dry({"action": "use the running program", "open_model": p.model_path}, running=running)
    bridge.connect(p.software, launch_if_not_running=p.launch, visible=p.visible, model_path=p.model_path,
                   program_path=p.program_path)
    return get_model_status(SoftwareP(software=p.software))


@action("disconnect", "Release the connection; optionally close the program (ask the user first).", "Session", "edit",
        DisconnectP)
def disconnect(p: DisconnectP) -> dict[str, Any]:
    if p.dry_run:
        return _dry({"release_connection": True, "close_application": p.close_application,
                     "save_model": p.close_application and p.save_model})
    return bridge.disconnect(p.software, p.close_application, p.save_model)


# --------------------------------------------------------------------------- model

def _status_core(software: str, counts: bool = True) -> dict[str, Any]:
    conn = bridge.connection(software)
    sap = conn.sap_model
    spec = SPECS[conn.software]
    version = version_number = None
    with contextlib.suppress(CSIApiError):
        outs = api_call("GetVersion", sap.GetVersion)
        version = str(outs[0]) if outs else None
        version_number = (float(outs[1]) or None) if len(outs) > 1 else None
    model_file = bridge.model_filename(sap, conn.software)
    out: dict[str, Any] = {
        "software": conn.software,
        "version": version,
        "version_number": version_number,
        "attached_via": conn.attached_via,
        "started_by_server": conn.launched_by_bridge,
        "model_file": model_file or None,
        "units": describe_units(bridge.present_units(sap)),
        "model_locked": bridge.is_locked(sap),
    }
    if counts:
        c = {
            "points": bridge.count(sap, "PointObj"),
            "frames": bridge.count(sap, "FrameObj"),
            "areas": bridge.count(sap, "AreaObj"),
            "load_patterns": bridge.count(sap, "LoadPatterns"),
            "load_cases": bridge.count(sap, "LoadCases"),
            "load_combinations": bridge.count(sap, "RespCombo"),
        }
        if spec.has_stories:
            names, _ = bridge.try_name_list(sap, ["Story"])
            c["stories"] = len(names) if names is not None else None
        out["object_counts"] = c
    return out


@action("get_model_status", "Version, model file, present units, lock state (locked = has results) and object counts "
        "(points, frames, areas, load patterns, cases, combinations[[E:, stories]]).", "Model", "read", SoftwareP)
def get_model_status(p: SoftwareP) -> dict[str, Any]:
    return _status_core(p.software)


@action("list_definitions", "Materials, frame sections, area / slab sections, load patterns (with type), load cases "
        "(with type) and load combinations.", "Model", "read", DefinitionsP)
def list_definitions(p: DefinitionsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    n = p.max_items

    def pack(names: list[str] | None, error: str | None, typer: Callable[[str], str] | None = None) -> dict:
        if names is None:
            return {"available": False, "error": error}
        shown = names[:n]
        return {"count": len(names), "items": [{"name": x, "type": typer(x)} for x in shown] if typer else shown,
                "truncated": len(names) > n}

    def pattern_type(name: str) -> str:
        try:
            code = int(api_call("LoadPatterns.GetLoadType", sap.LoadPatterns.GetLoadType, name)[0])
            return LOAD_PATTERN_TYPES.get(code, f"type_{code}")
        except (CSIApiError, IndexError, TypeError, ValueError, AttributeError):
            return "unknown"

    def case_type(name: str) -> str:
        try:
            code = int(api_call("LoadCases.GetTypeOAPI", sap.LoadCases.GetTypeOAPI, name)[0])
            return LOAD_CASE_TYPES.get(code, f"type_{code}")
        except (CSIApiError, IndexError, TypeError, ValueError, AttributeError):
            return "unknown"

    return {
        "software": SPECS[p.software].name,
        "materials": pack(*bridge.try_name_list(sap, ["PropMaterial"])),
        "frame_sections": pack(*bridge.try_name_list(sap, ["PropFrame"])),
        "area_sections": pack(*bridge.try_name_list(sap, ["PropArea", "PropAreaSlab"])),
        "load_patterns": pack(*bridge.try_name_list(sap, ["LoadPatterns"]), typer=pattern_type),
        "load_cases": pack(*bridge.try_name_list(sap, ["LoadCases"]), typer=case_type),
        "load_combinations": pack(*bridge.try_name_list(sap, ["RespCombo"])),
    }


@action("open_or_save_model", "Open a model file ([[E: .edb]][[S: .sdb]][[F: .fdb]]), save it in place, or Save As.",
        "Model", "edit", OpenSaveP, long_running=True)
def open_or_save_model(p: OpenSaveP) -> dict[str, Any]:
    if p.action == "open" and not p.file_path:
        raise CSIValidationError("file_path is needed to open a model.")
    if p.dry_run:
        ext = SPECS[p.software].model_extensions
        if p.action == "open":
            path = user_path(p.file_path or "")
            problems = ([] if os.path.isfile(path) else ["file not found"]) + \
                       ([] if os.path.splitext(path)[1].lower() in ext else [f"{p.software} opens {', '.join(ext)}"])
            return _dry({"open": path, "current_model_closed": True}, problems=problems)
        target = p.file_path or bridge.model_filename(bridge.sap_model(p.software), p.software)
        return _dry({"save_to": target or None, "overwrites_existing_file": bool(target and os.path.isfile(target))},
                    problems=[] if target else ["the model has never been saved: give file_path"])
    if p.action == "open":
        path = bridge.open_model(p.software, p.file_path)
        return {"opened": path, "status": _status_core(p.software)}
    return {"saved": bridge.save_model(p.software, p.file_path)}


@action("set_units", "Change the program's present (display) units and confirm. The other actions set their own "
        "units per call and put the user's units back afterwards.", "Model", "edit", SetUnitsP)
def set_units(p: SetUnitsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    previous = bridge.present_units(sap)
    if p.dry_run:
        return _dry({"set_units": p.unit_preset}, previous_units=previous)
    return {"previous_units": previous, "units": describe_units(bridge.set_units(sap, p.unit_preset))}


# --------------------------------------------------------------------------- modelling

@action("create_frame_element", "A frame (beam / column / brace) between two points with an existing section "
        "(FrameObj.AddByCoord); returns its name[[EF: with its label and story]].", "Modelling", "edit", FrameP)
def create_frame_element(p: FrameP) -> dict[str, Any]:
    if math.dist(p.start_xyz, p.end_xyz) <= 1e-9:
        raise CSIValidationError("start_xyz and end_xyz are the same point; a frame needs a length.")
    sap = bridge.sap_model(p.software)
    frame_secs = bridge.name_list(sap, "PropFrame")
    if p.section_name not in frame_secs:
        raise CSIValidationError(
            f"Frame section '{p.section_name}' is not defined.",
            hint=f"Closest sections: {suggest_keys(p.section_name, frame_secs) or frame_secs[:20]}. See list_definitions.")
    if p.dry_run:
        locked = bridge.is_locked(sap)
        return _dry({"create_frame": {"start_xyz": p.start_xyz, "end_xyz": p.end_xyz, "section": p.section_name,
                                      "length": round(math.dist(p.start_xyz, p.end_xyz), 6),
                                      "units": describe_units(p.units)},
                     "snapshot_first": p.snapshot},
                    model_locked=locked, needs_allow_unlock=bool(locked) and not p.allow_unlock)
    snap = _take_snapshot(p.software, sap, "before_frame") if p.snapshot else None
    was_locked = _lock(sap, p.allow_unlock, "add a frame element")
    with bridge.units_scope(sap, p.units):
        outs = api_call("FrameObj.AddByCoord", sap.FrameObj.AddByCoord, *p.start_xyz, *p.end_xyz, "",
                        p.section_name, p.user_name, "Global")
        name = str(outs[0]) if outs and outs[0] else ""
        if not name:
            raise CSIApiError("FrameObj.AddByCoord", detail="no object name returned")
        assigned = None
        with contextlib.suppress(CSIApiError, IndexError):
            assigned = str(api_call("FrameObj.GetSection", sap.FrameObj.GetSection, name)[0])
        if assigned != p.section_name:
            try:
                api_call("FrameObj.SetSection", sap.FrameObj.SetSection, name, p.section_name)
            except CSIApiError as exc:
                raise ActionFailed({"frame_name": name, "message": "Frame created, section assignment failed.",
                                    "detail": str(exc), "hint": "Inspect the existing frame before retrying."}) from exc
            assigned = p.section_name
        result: dict[str, Any] = {"frame_name": name, "section": assigned, "start_xyz": p.start_xyz,
                                  "end_xyz": p.end_xyz, "length": round(math.dist(p.start_xyz, p.end_xyz), 6),
                                  "units": describe_units(p.units), "model_was_unlocked": was_locked}
        if snap:
            result["snapshot"] = snap
        if p.software != "SAP2000":        # ETABS and SAFE: FrameObj.GetLabelFromName (not in SAP2000)
            with contextlib.suppress(CSIApiError, IndexError, AttributeError):
                lab = api_call("FrameObj.GetLabelFromName", sap.FrameObj.GetLabelFromName, name)
                result.update(label=str(lab[0]), story=str(lab[1]).strip())
    _refresh_view(sap)
    return result


_POINT_DIRS = {"gravity": (2, -1.0), "x": (0, 1.0), "y": (1, 1.0), "z": (2, 1.0),
               "mx": (3, 1.0), "my": (4, 1.0), "mz": (5, 1.0)}
_FRAME_DIRS = {"local1": 1, "local2": 2, "local3": 3, "x": 4, "y": 5, "z": 6, "projectedx": 7,
               "projectedy": 8, "projectedz": 9, "gravity": 10, "projectedgravity": 11}


@action("assign_point_or_frame_load", "Point forces / moments on joints, or distributed or concentrated loads on "
        "frames, in an existing load pattern; every object's return code is checked and reported.", "Modelling",
        "edit", LoadP)
def assign_point_or_frame_load(p: LoadP) -> dict[str, Any]:
    if len(set(p.object_names)) != len(p.object_names):
        raise CSIValidationError("object_names must contain each target only once.")
    sap = bridge.sap_model(p.software)
    patterns = bridge.name_list(sap, "LoadPatterns")
    if p.load_pattern not in patterns:
        raise CSIValidationError(f"Load pattern '{p.load_pattern}' is not defined.", hint=f"Defined patterns: {patterns}")
    dkey = p.direction.replace(" ", "").replace("_", "").replace("-", "").lower()
    if p.target_type == "point":
        if len(p.values) == 6:
            vector = [float(v) for v in p.values]
            applied = "full vector [F1,F2,F3,M1,M2,M3] in Global (direction not used)"
        elif len(p.values) == 1:
            if dkey not in _POINT_DIRS:
                raise CSIValidationError(f"Point direction '{p.direction}' is not supported.",
                                         hint="Use Gravity, X, Y, Z, MX, MY or MZ, or give 6 values.")
            idx, sign = _POINT_DIRS[dkey]
            vector = [0.0] * 6
            vector[idx] = sign * float(p.values[0])
            applied = p.direction + (" (positive acts downward, -Z)" if dkey == "gravity" else "")
        else:
            raise CSIValidationError("Point loads take 1 value (with direction) or 6 values [F1,F2,F3,M1,M2,M3].")
        w1 = w2 = d1 = d2 = None
    else:
        if dkey not in _FRAME_DIRS:
            raise CSIValidationError(f"Frame direction '{p.direction}' is not supported.",
                                     hint=f"Use one of: {', '.join(_FRAME_DIRS)}.")
        vals = [float(v) for v in p.values]
        if p.frame_load_shape == "point":
            if len(vals) != 2 or not 0.0 <= vals[1] <= 1.0:
                raise CSIValidationError("A point load on a frame takes [P, relative distance 0..1].")
            if dkey.startswith("projected"):
                raise CSIValidationError("Projected directions apply to distributed loads only.")
            w1, d1 = vals
            w2, d2 = w1, d1
        elif len(vals) == 1:
            w1 = w2 = vals[0]; d1, d2 = 0.0, 1.0
        elif len(vals) == 2:
            (w1, w2), (d1, d2) = vals, (0.0, 1.0)
        elif len(vals) == 4:
            w1, w2, d1, d2 = vals
            if not 0.0 <= d1 < d2 <= 1.0:
                raise CSIValidationError("Relative distances must satisfy 0 <= rel_d1 < rel_d2 <= 1.")
        else:
            raise CSIValidationError("Frame loads take [w], [w1, w2] or [w1, w2, rel_d1, rel_d2].")
    # ETABS and SAFE return 0 (success) for a name that does not exist, so check the names against the model.
    collection = "PointObj" if p.target_type == "point" else "FrameObj"
    existing = set(bridge.name_list(sap, collection))
    failed: list[dict[str, str]] = [{"object": n, "error": f"no {p.target_type} object with this name in the model"}
                                    for n in p.object_names if n not in existing]
    targets = [n for n in p.object_names if n in existing]
    if not targets:
        raise CSIValidationError(f"None of the {p.target_type} names exist in the model: {p.object_names[:20]}.",
                                 hint="Point and frame names are the unique names the program shows (or that "
                                      "create_frame_element returned).")
    if p.dry_run:
        locked = bridge.is_locked(sap)
        plan: dict[str, Any] = {"load_pattern": p.load_pattern, "objects": targets, "replace_existing": p.replace,
                                "units": describe_units(p.units), "snapshot_first": p.snapshot}
        if p.target_type == "point":
            plan.update(vector_F1_F2_F3_M1_M2_M3=vector, applied_as=applied)
        else:
            plan.update(direction=p.direction, w_start=w1, w_end=w2, rel_dist_start=d1, rel_dist_end=d2,
                        load_type=f"{p.frame_load_type} per length")
        return _dry(plan, not_found=failed, model_locked=locked, needs_allow_unlock=bool(locked) and not p.allow_unlock)
    snap = _take_snapshot(p.software, sap, "before_loads") if p.snapshot else None
    was_locked = _lock(sap, p.allow_unlock, "assign loads")

    succeeded: list[str] = []
    with bridge.units_scope(sap, p.units):
        if p.target_type == "point":
            for name in targets:
                try:
                    api_call(f"PointObj.SetLoadForce('{name}')", sap.PointObj.SetLoadForce,
                             name, p.load_pattern, list(vector), p.replace, "Global", 0)
                    succeeded.append(name)
                except CSIApiError as exc:
                    failed.append({"object": name, "error": str(exc)})
            detail: dict[str, Any] = {"vector_F1_F2_F3_M1_M2_M3": vector, "applied_as": applied}
        else:
            code = _FRAME_DIRS[dkey]
            csys = "Local" if code <= 3 else "Global"
            my_type = 1 if p.frame_load_type == "force" else 2
            for name in targets:
                try:
                    if p.frame_load_shape == "point":
                        api_call(f"FrameObj.SetLoadPoint('{name}')", sap.FrameObj.SetLoadPoint,
                                 name, p.load_pattern, my_type, code, d1, w1, csys, True, p.replace, 0)
                    else:
                        api_call(f"FrameObj.SetLoadDistributed('{name}')", sap.FrameObj.SetLoadDistributed,
                                 name, p.load_pattern, my_type, code, d1, d2, w1, w2, csys, True, p.replace, 0)
                    succeeded.append(name)
                except CSIApiError as exc:
                    failed.append({"object": name, "error": str(exc)})
            if p.frame_load_shape == "point":
                detail = {"load_type": f"point {p.frame_load_type}", "direction_code": code,
                          "coordinate_system": csys, "value": w1, "rel_dist": d1}
            else:
                detail = {"load_type": f"{p.frame_load_type} per length", "direction_code": code,
                          "coordinate_system": csys, "w_start": w1, "w_end": w2, "rel_dist_start": d1,
                          "rel_dist_end": d2}
    _refresh_view(sap)
    result = {"target_type": p.target_type, "load_pattern": p.load_pattern, "replace_existing": p.replace,
              "units": describe_units(p.units), "assigned": succeeded, "failed": failed,
              "model_was_unlocked": was_locked, **detail}
    if snap:
        result["snapshot"] = snap
    if failed:
        result["message"] = f"{len(failed)} of {len(p.object_names)} objects were not loaded (names wrong or of another type)."
        raise ActionFailed(result)
    return result


# --------------------------------------------------------------------------- analysis

def read_analysis_options(sap: Any, software: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if software in ("ETABS", "SAFE"):
        try:
            from db_edit import read_table
            _, _, rows = read_table(sap, FLOOR_MESH_TABLE)
            if rows:
                r = rows[0]
                fm: dict[str, Any] = {"type": str(r.get("MeshOpt", "")).lower(),
                                      "max_size": float(r.get("MaxMeshSize") or 0)}
                if fm["type"] == "rectangular":
                    fm.update(localized=r.get("Localized") == "Yes", merge_joints=r.get("MergeJoints") == "Yes")
                out["floor_mesh"] = fm
        except (CSIError, COMError, AttributeError, IndexError, TypeError, ValueError):
            pass
    try:
        d = api_call("Analyze.GetActiveDOF", sap.Analyze.GetActiveDOF, [False] * 6)
        out["active_dof"] = [n for n, v in zip(DOF6, as_list(d[0])) if v]
    except (CSIError, COMError, AttributeError, IndexError, TypeError):
        pass
    try:
        f = api_call("Analyze.GetRunCaseFlag", sap.Analyze.GetRunCaseFlag)
        out["run_cases"] = {str(n): bool(v) for n, v in zip(as_list(f[1]), as_list(f[2]))}
    except (CSIError, COMError, AttributeError, IndexError, TypeError):
        pass
    return out


@action("set_analysis_options", "Analysis options: the active degrees of freedom (e.g. plane_xz for a 2D frame "
        "in the XZ plane), which load cases run[[EF:, and the automatic floor mesh (type, maximum size)]]. Answers "
        "with the options read back.", "Analysis", "edit", AnalysisOptionsP, fields={"floor_mesh": "EF"})
def set_analysis_options(p: AnalysisOptionsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    if p.floor_mesh is not None and p.software == "SAP2000":
        raise CSIValidationError("floor_mesh is an ETABS / SAFE option; SAP2000 meshes each area (set_area_mesh).")
    with bridge.units_scope(sap, p.units):
        before = read_analysis_options(sap, p.software)
    dof = None
    if p.active_dof is not None:
        names = DOF_PRESETS[p.active_dof] if isinstance(p.active_dof, str) else tuple(p.active_dof)
        dof = [d in names for d in DOF6]
    cases = dict(p.run_cases or {})
    unknown = [c for c in cases if c != "*" and c not in before.get("run_cases", {})]
    if unknown:
        raise CSIValidationError(f"Unknown load cases: {unknown}", hint=f"Cases: {list(before.get('run_cases', {}))}")
    plan = {"active_dof": [d for d, v in zip(DOF6, dof) if v] if dof else None, "run_cases": cases or None,
            "floor_mesh": p.floor_mesh.model_dump() if p.floor_mesh else None, "units": describe_units(p.units),
            "before": before}
    if p.dry_run:
        locked = bridge.is_locked(sap)
        return _dry(plan, model_locked=locked, needs_allow_unlock=bool(locked and dof) and not p.allow_unlock)
    was_locked = _lock(sap, p.allow_unlock, "change analysis options") if (dof or p.floor_mesh) else False
    if dof:
        api_call("Analyze.SetActiveDOF", sap.Analyze.SetActiveDOF, dof)
    if cases.pop("*", None) is not None:
        api_call("Analyze.SetRunCaseFlag(all)", sap.Analyze.SetRunCaseFlag, "", bool(p.run_cases["*"]), True)
    for name, run in cases.items():
        api_call(f"Analyze.SetRunCaseFlag('{name}')", sap.Analyze.SetRunCaseFlag, name, bool(run), False)
    extra: dict[str, Any] = {}
    with bridge.units_scope(sap, p.units):
        if p.floor_mesh is not None:
            from db_edit import edit_table
            fm = p.floor_mesh
            rect = fm.type == "rectangular"
            row = {"MeshOpt": "Rectangular" if rect else "General", "MaxMeshSize": repr(float(fm.max_size)),
                   "Localized": ("Yes" if fm.localized else "No") if rect else "",
                   "MergeJoints": ("Yes" if fm.merge_joints else "No") if rect else ""}
            extra["table_edit"] = edit_table(sap, FLOOR_MESH_TABLE, [], update=lambda r: row)
        after = read_analysis_options(sap, p.software)
    return {**plan, "after": after, "model_was_unlocked": was_locked, **extra}


@action("run_analysis", "Save, run the analysis of every case set to run, and report runtime and the status of "
        "each case. Blocks until the solver finishes.", "Analysis", "edit", AnalysisP, long_running=True)
def run_analysis(p: AnalysisP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    flags = api_call("Analyze.GetRunCaseFlag", sap.Analyze.GetRunCaseFlag)
    if len(flags) != 3 or flags[0] != len(flags[1] or ()) or flags[0] != len(flags[2] or ()):
        raise CSIApiError("Analyze.GetRunCaseFlag", detail="inconsistent case flags")
    to_run = [str(n) for n, f in zip(flags[1] or (), flags[2] or ()) if f]
    if p.dry_run:
        path = bridge.model_filename(sap, p.software)
        problems = ([] if path else ["the model has never been saved"]) + ([] if to_run else ["no case set to run"])
        return _dry({"save_first": p.save_before_run, "snapshot_first": p.snapshot, "run_cases": to_run,
                     "replaces_existing_results": bool(bridge.is_locked(sap))}, model_file=path or None,
                    problems=problems)
    if not to_run:
        raise CSIValidationError("No analysis cases are selected to run.")
    snap = _take_snapshot(p.software, sap, "before_analysis") if p.snapshot else None
    if p.save_before_run:
        path = bridge.save_model(p.software)
    else:
        path = bridge.model_filename(sap, p.software)
        if not path:
            raise CSIValidationError("The model has never been saved; the program needs a file to analyse.",
                                     hint="Save it first: open_or_save_model with action='save' and a file_path.")
    t0 = time.perf_counter()
    run_error = None
    try:
        api_call("Analyze.RunAnalysis", sap.Analyze.RunAnalysis)
    except CSIApiError as exc:
        run_error = str(exc)
    runtime = time.perf_counter() - t0
    status: dict[str, str] = {}
    status_error = None
    try:
        status = bridge.case_status(sap)
    except CSIApiError as exc:
        status_error = str(exc)
    problems = {n: status.get(n, "Unknown") for n in to_run if status.get(n) != "Finished"}
    result = {"model_file": path, "saved_before_run": p.save_before_run, "runtime_seconds": round(runtime, 2),
              "cases_set_to_run": to_run, "case_status_counts": dict(Counter(status.values())),
              "problem_cases": problems, "model_locked_after_run": bridge.is_locked(sap)}
    if snap:
        result["snapshot"] = snap
    if run_error or problems:
        result.update(run_error=run_error, status_error=status_error, message="Completion could not be verified for every selected case.",
                      hint="Read the analysis log (.LOG next to the model) for instabilities or missing definitions.")
        raise ActionFailed(result)
    return result


def _selection(sap: Any, names: list[str] | None) -> tuple[list[str], list[str]]:
    """Split names into (load cases, load combinations); default = all of both."""
    all_cases = bridge.name_list(sap, "LoadCases")
    all_combos = bridge.try_name_list(sap, ["RespCombo"])[0] or []   # some builds have no combinations API
    if not names:
        return all_cases, all_combos
    case_map = {c.lower(): c for c in all_cases}
    combo_map = {c.lower(): c for c in all_combos}
    cases, combos, unknown = [], [], []
    for n in names:
        k = n.strip().lower()
        if k in case_map:
            cases.append(case_map[k])
        elif k in combo_map:
            combos.append(combo_map[k])
        else:
            unknown.append(n)
    if unknown:
        raise CSIValidationError(f"Unknown load cases / combinations: {unknown}.",
                                 hint=f"Cases: {all_cases[:40]}; combinations: {all_combos[:40]}.")
    return cases, combos


def _read_selected(software: str, sap: Any, key: str, cases: list[str], combos: list[str]):
    """Read a table with the wanted cases / combinations selected for display, leaving the user's selection as it was.

    The selection is changed only when it differs, and put back afterwards. Rows are also filtered in Python.
    """
    cases = [c for c in cases if not c.startswith("~")]   # internal cases (SAFE ~LLRF) are not selectable
    prev_cases, prev_combos = bridge.display_selection(sap)
    known = prev_cases is not None and prev_combos is not None
    if known and set(prev_cases) == set(cases) and set(prev_combos) == set(combos):
        fields, rows, version = bridge.read_table(sap, software, key)
        return fields, rows, version, []
    if not known:
        raise CSIApiError("display selection", detail="cannot snapshot both selections before changing them")
    try:
        warnings = bridge.set_display_selection(sap, cases, combos)
        fields, rows, version = bridge.read_table(sap, software, key)
    except BaseException:
        # Put the selection back but never let a restore problem hide the real error.
        with contextlib.suppress(Exception):
            bridge.restore_display_selection(sap, prev_cases, prev_combos)
        raise
    warnings += bridge.restore_display_selection(sap, prev_cases, prev_combos)
    return fields, rows, version, warnings


@action("list_available_tables", "Database table keys available now (keys differ between programs and versions; "
        "result tables appear after the analysis).", "Results", "read", TablesP)
def list_available_tables(p: TablesP) -> dict[str, Any]:
    tables = bridge.available_tables(bridge.sap_model(p.software))
    hits = search_tables(tables, p.search_term)
    return {"software": SPECS[p.software].name, "search_term": p.search_term, "total_available": len(tables),
            "matches": len(hits), "tables": hits[:p.max_results], "truncated": len(hits) > p.max_results,
            "import_type_legend": {0: "not importable (results)", 1: "importable, not interactively",
                                   2: "importable when unlocked", 3: "importable locked or unlocked"}}


@action("query_database_table", "Any database table (GetTableForDisplayArray) with case / combination selection, "
        "column filters, sorting and a row limit. Always returns the total and filtered row counts and the min / max "
        "(with location) of every numeric column.", "Results", "read", QueryP)
def query_database_table(p: QueryP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    keys = [t["key"] for t in bridge.available_tables(sap)]
    key = resolve_exact_key(p.table_key, keys)
    if key is None:
        raise TableError(f"Table '{p.table_key}' is not available in {p.software} now.",
                         hint=f"Closest keys: {suggest_keys(p.table_key, keys)}. Result tables appear after the "
                         "analysis; list_available_tables searches the keys.")
    cases, combos = _selection(sap, p.load_cases_or_combos)
    with bridge.units_scope(sap, p.units):
        fields, rows, version, warnings = _read_selected(p.software, sap, key, cases, combos)
    df = rows_to_dataframe(fields, rows)
    total = len(df)
    df = filter_by_cases(df, p.load_cases_or_combos)
    df, applied = apply_filters(df, p.filters)
    df = sort_frame(df, p.sort_by, p.descending, p.sort_by_abs)
    out = records(df, p.max_rows)
    export_path = None
    if p.export in ("csv", "xlsx", "json") or (p.export == "auto" and len(df) > p.max_rows):
        try:
            export_path = str(safety.export_frame(df, f"{p.software}_{key}", p.export if p.export != "auto" else "csv"))
        except OSError:
            if p.export != "auto":
                raise TableError("Cannot write the requested table export. Check the CSI_MCP_OUTPUT directory.")
            warnings.append("Automatic CSV export failed. Check the CSI_MCP_OUTPUT directory.")
    return {"export_path": export_path, "table_key": key, "table_version": version, "units": describe_units(p.units), "columns": list(df.columns),
            "total_rows": total, "rows_after_filters": int(len(df)), "returned_rows": len(out),
            "truncated": len(df) > p.max_rows, "filters_applied": applied,
            "cases_or_combos": p.load_cases_or_combos or "all", "numeric_summary": numeric_summary(df),
            "records": out, "warnings": warnings}


@action("get_analysis_summary", "Headline results: governing cases, peak values and where they occur. "
        "base_reactions, [[E: story_drifts, ]]modal_periods (periods, dominant modes, cumulative mass), "
        "joint_displacements.", "Results", "read", SummaryP, options={"result_type": {"story_drifts": "E"}},
        fields={"drift_limit": "E"})
def get_analysis_summary(p: SummaryP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    status = bridge.case_status(sap)
    if not any(s == "Finished" for s in status.values()):
        raise CSIValidationError("No load case has analysis results.", hint="Run the analysis first (run_analysis).")
    keys = [t["key"] for t in bridge.available_tables(sap)]
    key = resolve_result_table(p.result_type, p.software, keys)
    cases, combos = _selection(sap, p.combos)
    note = None
    if not p.combos:
        if p.result_type in ("joint_displacements", "story_drifts") and combos:
            cases, note = [], "all load combinations (load cases left out to keep the table small)"
        else:
            note = "all load cases and combinations"
    with bridge.units_scope(sap, p.units):
        fields, rows, _, warnings = _read_selected(p.software, sap, key, cases, combos)
    df = filter_by_cases(rows_to_dataframe(fields, rows), cases + combos)
    base = {"software": SPECS[p.software].name, "result_type": p.result_type, "table_key": key,
            "units": describe_units(p.units), "rows_analysed": int(len(df)), "selection": note or p.combos,
            "cases_without_results": [n for n, s in status.items() if s != "Finished"][:50], "warnings": warnings}
    if df.empty:
        return {**base, "summary": None, "message": "The table is empty for the selected cases / combinations."}
    summarize = {"base_reactions": summarize_base_reactions,
                 "story_drifts": lambda d: summarize_story_drifts(d, p.drift_limit),
                 "modal_periods": summarize_modal,
                 "joint_displacements": summarize_joint_displacements}[p.result_type]
    return {**base, "summary": summarize(df)}


# --------------------------------------------------------------------------- output

@action("snapshot_model", "Copy the model file as last saved to _csi_mcp_snapshots next to the model (time-stamped). "
        "The program and the open model are not touched; unsaved changes are not in the copy.", "Output", "read",
        SnapshotP)
def snapshot_model(p: SnapshotP) -> dict[str, Any]:
    return _take_snapshot(p.software, bridge.sap_model(p.software), p.label)


import model_read  # noqa: E402,F401 - registers the Phase 1 model-reading actions
import modelling  # noqa: E402,F401 - registers the Phase 3 modelling actions
import definitions  # noqa: E402,F401 - registers the Phase 3 definition actions
import objects  # noqa: E402,F401 - registers the Phase 3 object actions
import tables_edit  # noqa: E402,F401 - registers the Phase 4 table editing action
import geometry  # noqa: E402,F401 - registers the Phase 4 geometry actions
import model_spec  # noqa: E402,F401 - registers new_model and build_model_from_spec
import design  # noqa: E402,F401 - registers the Phase 6 design actions
import program_specific  # noqa: E402,F401 - registers the Phase 5 actions
import autoload  # noqa: E402,F401 - registers the automatic wind / seismic loads by any code
import slabs  # noqa: E402,F401 - registers strips, PT tendons and towers
import sap_objects  # noqa: E402,F401 - registers SAP2000 cables, solids, tendon objects
import sap_analysis  # noqa: E402,F401 - registers staged construction, hinges, moving loads
import results  # noqa: E402,F401 - registers the Phase 2 results actions
import report  # noqa: E402,F401 - registers export_report (report.py builds on the actions above)
import rebar  # noqa: E402,F401 - registers set_frame_rebar
import sections  # noqa: E402,F401 - registers non-prismatic and Section Designer sections
import excel_export  # noqa: E402,F401 - registers export_tables_to_excel
import area_material  # noqa: E402,F401 - registers layered shells, decks, stress-strain curves, time dependence
import loads87  # noqa: E402,F401 - registers support displacements, area wind pressure, SAP2000 strain / gravity loads
import geometry89  # noqa: E402,F401 - registers divide / join / trim / extend frames, divide / merge areas, points
import grids810  # noqa: E402,F401 - registers grid systems / coordinate systems and SAP2000 constraints
import cases86  # noqa: E402,F401 - registers buckling, direct / FNA time history, steady state, PSD, hyperstatic cases
import assign811  # noqa: E402,F401 - registers lateral bracing, T/C limits, design procedure, panel zones, point axes, edge constraints
