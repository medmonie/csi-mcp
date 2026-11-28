"""Phase 8.11 (0.25.0): frame lateral bracing, tension / compression limits, design procedure, panel zones, point
local axes (SAP2000) and area edge constraints. Selection by property / coordinate range / plane and deletion of links,
cables, tendons and solids live in objects.py (select_objects / delete_objects).

Measured on ETABS 23.2, SAP2000 27.1 and SAFE 23.3 (tests/diag_811.py, diag_811b.py, 28 Nov 2026):
- FrameObj.SetTCLimits / GetTCLimits work in all three; the compression limit is stored negative whatever sign is
  given (5 -> -5, 0 -> -0).
- FrameObj.SetDesignProcedure codes differ: SAP2000 takes 1 (default from the material; reads back 1 steel / 2
  concrete) and 2 (no design; reads back -9), every other code returns 1. ETABS / SAFE take 0 (program determined),
  1-4, 7 and 13; a steel beam reads 1 after 0-2, 3 (composite beam) after 3-4, 7 (no design) after 7 / 13; a concrete
  beam reads 2 after 0-4. SAFE reads a steel beam's default as 3. Offered: from_material / no_design (all),
  composite_beam (ETABS / SAFE).
- FrameObj.SetLateralBracing / GetLateralBracing: type 1 point / 2 uniform, location 1 top / 2 bottom / 3 all;
  GetLateralBracing gives relative and absolute distances. ETABS / SAFE take and give absolute distances in the
  model's database units (N_mm in the test models) whatever the present units (2.0 given in kN_m -> 2 mm); the
  action therefore always sends relative distances and reports absolute ones as relative x length. ETABS / SAFE
  DeleteLateralBracing returns 0 and deletes nothing. ETABS deletes through its tables 'Steel Frame Lateral Bracing
  - Point' / '- Uniform' (UniqueName); SAFE has no such tables -> replace / delete are SAP2000 / ETABS only.
  (Importing the 'Composite Beam ... Bracing' tables empty blanked the steel bracing locations: not touched.)
- PointObj.SetPanelZone: property 1 = elastic from column (+ doubler plate thickness), 2 = spring stiffnesses
  K1 / K2, 3 = link property (all three). Connectivity and local-axis codes are zero-based, not as documented:
  SAP2000 connectivity 0 = beams to other objects, 1 = braces to other objects (2 and 3 return 1); local axis 0 =
  from the column, 1 = user angle (2 and 3 return 1). ETABS / SAFE ignore the connectivity (always read 1, the
  property table shows none) and keep the user angle with code 1. DeletePanelZone works in all three.
- PointObj.SetLocalAxes / GetLocalAxes: SAP2000 only (ETABS / SAFE have GetLocalAxes but no setter).
- AreaObj.SetEdgeConstraint / GetEdgeConstraint in all three; new ETABS / SAFE areas start constrained (auto edge
  constraint), SAP2000 areas unconstrained.
Values (bracing points, limits, stiffnesses, doubler thickness, angles) come from the designer (D1).
"""
from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import Field, model_validator

import db_edit
from actions import Params, _dry, _refresh_view, action, bridge
from csi_bridge import CSIValidationError, api_call, as_list, describe_units
from model_read import _names, _try
from modelling import _start_edit, _targets
from objects import GROUP, MAX, _EditP

TOL = 1e-6
BRACE_TYPE = {"point": 1, "uniform": 2}
BRACE_LOC = {"top": 1, "bottom": 2, "all": 3}
ETABS_BRACING_TABLES = ("Steel Frame Lateral Bracing - Point", "Steel Frame Lateral Bracing - Uniform")
PROC_SET = {"SAP2000": {"from_material": 1, "no_design": 2},
            "ETABS": {"from_material": 0, "composite_beam": 3, "no_design": 7},
            "SAFE": {"from_material": 0, "composite_beam": 3, "no_design": 7}}
PROC_READ = {"SAP2000": {1: "steel_frame", 2: "concrete_frame", 3: "aluminum_frame", 4: "cold_formed_frame",
                         -9: "no_design"},
             "ETABS": {1: "steel_frame", 2: "concrete_frame", 3: "composite_beam", 4: "steel_joist", 7: "no_design",
                       13: "composite_column"},
             "SAFE": {1: "steel_frame", 2: "concrete_frame", 3: "composite_beam", 4: "steel_joist", 7: "no_design",
                      13: "composite_column"}}
PZ_PROP = {"column": 1, "springs": 2, "link": 3}
PZ_CONN = {"beams_to_other": 0, "braces_to_other": 1}


def _f(v: Any) -> float:
    return float(f"{float(v):.10g}")


def _inv(d: dict[str, int], v: Any) -> Any:
    try:
        return {b: a for a, b in d.items()}.get(int(v), v)
    except (TypeError, ValueError):
        return v


def _close(a: float, b: float, tol: float = TOL) -> bool:
    return abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)), abs(float(b)))


def _frame_length(sap: Any, name: str) -> float:
    i, j = (str(x) for x in api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, name)[:2])
    a = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, i)[:3]
    b = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, j)[:3]
    return math.dist([float(x) for x in a], [float(x) for x in b])


# --------------------------------------------------------------------------- lateral bracing

def read_bracing(sap: Any, name: str, length: float) -> list[dict[str, Any]]:
    g = _try(sap, "FrameObj.GetLateralBracing", name)
    if not g or int(g[0]) == 0:
        return []
    typ, loc, rd1, rd2 = (as_list(g[i]) for i in (2, 3, 4, 5))
    out = []
    for k in range(int(g[0])):
        rec = {"kind": _inv(BRACE_TYPE, typ[k]), "location": _inv(BRACE_LOC, loc[k]), "start": _f(rd1[k]),
               "start_abs": _f(float(rd1[k]) * length)}
        if rec["kind"] == "uniform":
            rec.update(end=_f(rd2[k]), end_abs=_f(float(rd2[k]) * length))
        out.append(rec)
    return out


def _delete_bracing(sap: Any, sw: str, frames: list[str]) -> None:
    if sw == "SAP2000":
        for n in frames:
            api_call("FrameObj.DeleteLateralBracing", sap.FrameObj.DeleteLateralBracing, n, 3)
        return
    # ETABS: DeleteLateralBracing keeps everything (measured); drop the frames' rows from the steel bracing tables
    gone = set(frames)
    for key in ETABS_BRACING_TABLES:
        try:
            rows = db_edit.read_table(sap, key)[2]
        except Exception:  # noqa: BLE001 - empty / absent table: nothing to drop
            continue
        if any(r.get("UniqueName") in gone for r in rows):
            db_edit.edit_table(sap, key, [], drop=lambda r: r.get("UniqueName") in gone)


class Brace(Params):
    kind: Literal["point", "uniform"] = Field(description="point (a brace at one place) | uniform (continuous)")
    location: Literal["top", "bottom", "all"] = Field(description="braced flange: top | bottom | all")
    start: float = Field(ge=0, description="point: where | uniform: from; relative 0-1 or a length (relative false)")
    end: float | None = Field(default=None, ge=0, description="uniform: to (same convention as start)")

    @model_validator(mode="after")
    def _check(self) -> "Brace":
        if self.kind == "uniform" and self.end is None:
            raise ValueError("a uniform bracing needs end")
        if self.kind == "point" and self.end is not None:
            raise ValueError("a point bracing takes start only")
        if self.end is not None and self.end < self.start:
            raise ValueError("end must not be before start")
        return self


class BracingP(_EditP):
    frame_names: list[str] | None = Field(default=None, max_length=MAX, description="frames")
    group: str | None = GROUP
    operation: Literal["add", "replace", "delete"] = Field(default="add", description=(
        "add (to the frame's bracing)[[ES: | replace (delete the frame's bracing first) | delete (all bracing of the "
        "frames)]]"))
    bracing: list[Brace] = Field(default=[], max_length=50, description="add / replace: the bracing, from the designer")
    relative: bool = Field(default=True, description=(
        "true: start / end are relative distances (0-1) from the I end; false: lengths in `units`"))

    @model_validator(mode="after")
    def _check(self) -> "BracingP":
        if self.operation == "delete" and self.bracing:
            raise ValueError("delete takes no bracing")
        if self.operation != "delete" and not self.bracing:
            raise ValueError("give the bracing")
        if self.relative and any(b.start > 1 or (b.end or 0) > 1 for b in self.bracing):
            raise ValueError("relative distances lie between 0 and 1")
        return self


@action("assign_lateral_bracing", "Assign lateral bracing to frames for design: point or uniform bracing of the top, "
        "bottom or both flanges, at relative or absolute distances from the I end - from the designer. Answers with "
        "each frame's bracing read back (relative and absolute).", "Assignments", "edit", BracingP,
        options={"operation": {"replace": "ES", "delete": "ES"}})
def assign_lateral_bracing(p: BracingP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    frames = _targets(sap, "FrameObj", p.frame_names, p.group, "frames")
    with bridge.units_scope(sap, p.units):
        lengths = {n: _frame_length(sap, n) for n in frames}
        rel: dict[str, list[tuple[Brace, float, float | None]]] = {}
        for n in frames:
            L = lengths[n]
            items = []
            for b in p.bracing:
                s = b.start if p.relative else b.start / L
                e = None if b.end is None else (b.end if p.relative else b.end / L)
                if s > 1 + TOL or (e or 0) > 1 + TOL:
                    raise CSIValidationError(f"Frame {n} is {L:.6g} long; a distance in the bracing is beyond it.")
                items.append((b, min(s, 1.0), None if e is None else min(e, 1.0)))
            rel[n] = items
        plan = {"operation": p.operation, "frames": len(frames), "names": frames[:50],
                "bracing": [b.model_dump(exclude_none=True) for b in p.bracing], "relative": p.relative,
                "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_lateral_bracing", "assign lateral bracing")
        fo = sap.FrameObj
        before = {n: read_bracing(sap, n, lengths[n]) for n in frames}
        if p.operation in ("replace", "delete"):
            _delete_bracing(sap, p.software, frames)
        for n in frames:
            for b, s, e in rel[n]:
                api_call("FrameObj.SetLateralBracing", fo.SetLateralBracing, n, BRACE_TYPE[b.kind],
                         BRACE_LOC[b.location], s, s if e is None else e, True)
        _refresh_view(sap)
        back = {n: read_bracing(sap, n, lengths[n]) for n in frames}
    missing: dict[str, Any] = {}
    for n in frames:
        want = [] if p.operation == "delete" else [(b.kind, b.location, s, e) for b, s, e in rel[n]]
        got = back[n]
        for k, loc, s, e in want:
            if not any(g["kind"] == k and g["location"] == loc and _close(g["start"], s, 1e-5)
                       and (e is None or _close(g.get("end", -1), e, 1e-5)) for g in got):
                missing.setdefault(n, []).append({"kind": k, "location": loc, "start": s, "end": e})
        if p.operation == "delete" and got:
            missing[n] = {"still_braced": got}
        if p.operation == "replace" and len(got) != len(want):
            missing.setdefault(n, []).append({"expected_items": len(want), "read_back_items": len(got)})
    out = {**plan, "read_back": {n: back[n] for n in frames[:50]}, "before": {n: before[n] for n in frames[:20]},
           "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if missing:
        out["not_kept_as_given"] = missing
    return out


# --------------------------------------------------------------------------- tension / compression limits

def read_tc(sap: Any, name: str) -> dict[str, Any]:
    g = api_call("FrameObj.GetTCLimits", sap.FrameObj.GetTCLimits, name)
    return {"compression_limit": abs(_f(g[1])) if g[0] else None, "tension_limit": _f(g[3]) if g[2] else None}


class TCP(_EditP):
    frame_names: list[str] | None = Field(default=None, max_length=MAX, description="frames")
    group: str | None = GROUP
    compression_limit: float | None = Field(default=None, ge=0, description=(
        "largest compression force the frames take, as a positive number (0 = no compression, e.g. a tension-only "
        "brace); omitted = no compression limit. Force units of `units`. Acts in nonlinear analyses"))
    tension_limit: float | None = Field(default=None, ge=0, description=(
        "largest tension force (0 = no tension, e.g. a compression-only strut); omitted = no tension limit"))


@action("assign_frame_tc_limits", "Assign tension / compression force limits to frames (e.g. tension-only braces) "
        "- the limits act in nonlinear analysis cases; omit both to remove them. Values from the designer. Answers "
        "with the limits read back.", "Assignments", "edit", TCP)
def assign_frame_tc_limits(p: TCP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    frames = _targets(sap, "FrameObj", p.frame_names, p.group, "frames")
    plan = {"frames": len(frames), "names": frames[:50], "compression_limit": p.compression_limit,
            "tension_limit": p.tension_limit, "units": describe_units(p.units)}
    if p.compression_limit is None and p.tension_limit is None:
        plan["note"] = "no limits: removes any limits of these frames"
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_tc_limits", "assign T/C limits")
        for n in frames:
            api_call("FrameObj.SetTCLimits", sap.FrameObj.SetTCLimits, n, p.compression_limit is not None,
                     -(p.compression_limit or 0.0), p.tension_limit is not None, p.tension_limit or 0.0)
        back = {n: read_tc(sap, n) for n in frames}
    diff = {n: b for n, b in back.items()
            if (b["compression_limit"] is None) != (p.compression_limit is None)
            or (b["tension_limit"] is None) != (p.tension_limit is None)
            or (p.compression_limit is not None and not _close(b["compression_limit"], p.compression_limit))
            or (p.tension_limit is not None and not _close(b["tension_limit"], p.tension_limit))}
    out = {**plan, "read_back": {n: back[n] for n in frames[:50]}, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out


# --------------------------------------------------------------------------- design procedure

class ProcP(_EditP):
    frame_names: list[str] | None = Field(default=None, max_length=MAX, description="frames")
    group: str | None = GROUP
    procedure: Literal["from_material", "no_design", "composite_beam"] = Field(description=(
        "from_material (the program's default for the section material) | no_design (left out of design)"
        "[[EF: | composite_beam (steel beams designed as composite)]]"))


@action("assign_design_procedure", "Set the design procedure of frames: default from the material, no design, "
        "[[EF: or composite beam]] - as the designer decides. Answers with the procedure each frame reads back "
        "(steel / concrete / ... / no design).", "Assignments", "edit", ProcP,
        options={"procedure": {"composite_beam": "EF"}})
def assign_design_procedure(p: ProcP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    frames = _targets(sap, "FrameObj", p.frame_names, p.group, "frames")
    code = PROC_SET[p.software][p.procedure]
    plan = {"frames": len(frames), "names": frames[:50], "procedure": p.procedure}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_design_procedure", "set the design procedure")
    names = PROC_READ[p.software]
    before = {n: names.get(int(api_call("FrameObj.GetDesignProcedure", sap.FrameObj.GetDesignProcedure, n)[0]))
              for n in frames}
    for n in frames:
        api_call("FrameObj.SetDesignProcedure", sap.FrameObj.SetDesignProcedure, n, code)
    back = {}
    for n in frames:
        c = int(api_call("FrameObj.GetDesignProcedure", sap.FrameObj.GetDesignProcedure, n)[0])
        back[n] = names.get(c, f"code {c}")
    if p.procedure == "from_material":
        diff = {n: b for n, b in back.items() if b == "no_design"}
    else:
        diff = {n: b for n, b in back.items() if b != p.procedure}
    out = {**plan, "read_back": {n: back[n] for n in frames[:100]}, "before": {n: before[n] for n in frames[:50]},
           "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
        out["note"] = ("the program kept another procedure for these frames (e.g. composite beam applies to steel "
                       "beams only)")
    return out


# --------------------------------------------------------------------------- panel zones

def read_panel_zone(sap: Any, name: str, sw: str) -> dict[str, Any] | None:
    g = _try(sap, "PointObj.GetPanelZone", name)
    if not g or int(g[0]) == 0:
        return None
    prop = _inv(PZ_PROP, g[0])
    rec: dict[str, Any] = {"property": prop}
    if prop == "column":
        rec["doubler_thickness"] = _f(g[1])
    elif prop == "springs":
        rec.update(k_major=_f(g[2]), k_minor=_f(g[3]))
    elif prop == "link":
        rec["link_property"] = str(g[4] or "")
    rec["local_axis"] = "user" if int(g[6]) == 1 else "from_column"
    if int(g[6]) == 1:
        rec["local_axis_angle"] = _f(g[7])
    if sw == "SAP2000":       # ETABS / SAFE ignore the connectivity (measured)
        rec["connectivity"] = _inv(PZ_CONN, g[5])
    return rec


class PanelZoneP(_EditP):
    point_names: list[str] | None = Field(default=None, max_length=MAX, description="beam-column joints")
    group: str | None = GROUP
    operation: Literal["set", "delete"] = Field(default="set", description="set | delete the panel zones")
    property: Literal["column", "springs", "link"] | None = Field(default=None, description=(
        "set: column (elastic from the column, plus an optional doubler plate) | springs (rotational stiffnesses "
        "k_major / k_minor) [[ES: | link (a link property)]]"))
    doubler_thickness: float = Field(default=0.0, ge=0, description="column: doubler plate thickness (length)")
    k_major: float | None = Field(default=None, gt=0, description="springs: stiffness about the major axis "
                                                                    "(moment / radian)")
    k_minor: float | None = Field(default=None, gt=0, description="springs: stiffness about the minor axis")
    link_property: str | None = Field(default=None, description="link: an existing link property")
    connectivity: Literal["beams_to_other", "braces_to_other"] = Field(default="beams_to_other", description=(
        "beams_to_other (the beams connect to the panel zone) | braces_to_other"))
    local_axis_angle: float | None = Field(default=None, ge=-360, le=360, description=(
        "omitted = local axes from the column; a value = user angle, degrees"))

    @model_validator(mode="after")
    def _check(self) -> "PanelZoneP":
        if self.operation == "delete":
            if self.property:
                raise ValueError("delete takes no property")
            return self
        if not self.property:
            raise ValueError("give property (column | springs | link)")
        if self.property == "springs" and (self.k_major is None or self.k_minor is None):
            raise ValueError("springs need k_major and k_minor (from the designer)")
        if self.property == "link" and not self.link_property:
            raise ValueError("link needs link_property")
        if self.property != "column" and self.doubler_thickness:
            raise ValueError("doubler_thickness applies to property column")
        return self


@action("assign_panel_zone", "Assign (or delete) panel zones at beam-column joints: elastic from the column with an "
        "optional doubler plate, rotational springs[[ES: or a link property]], [[S: connectivity and ]]local axes - "
        "values from the designer. Answers with the panel zones read back.", "Assignments", "edit", PanelZoneP,
        options={"property": {"link": "ES"}}, fields={"connectivity": "S"})
def assign_panel_zone(p: PanelZoneP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    pts = _targets(sap, "PointObj", p.point_names, p.group, "points")
    if p.property == "link":
        links = bridge.try_name_list(sap, ["PropLink"])[0] or []
        if p.link_property not in links:
            raise CSIValidationError(f"Link property '{p.link_property}' is not defined.", hint=f"Defined: {links[:30]}")
    want: dict[str, Any] | None = None
    if p.operation == "set":
        want = {"property": p.property, "local_axis": "from_column" if p.local_axis_angle is None else "user"}
        if p.software == "SAP2000":
            want["connectivity"] = p.connectivity
        if p.property == "column":
            want["doubler_thickness"] = p.doubler_thickness
        elif p.property == "springs":
            want.update(k_major=p.k_major, k_minor=p.k_minor)
        else:
            want["link_property"] = p.link_property
        if p.local_axis_angle is not None:
            want["local_axis_angle"] = p.local_axis_angle
    plan = {"operation": p.operation, "points": len(pts), "names": pts[:50], **({"panel_zone": want} if want else {}),
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    po = sap.PointObj
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_panel_zone", "assign panel zones")
        for n in pts:
            if p.operation == "delete":
                if read_panel_zone(sap, n, p.software):
                    api_call("PointObj.DeletePanelZone", po.DeletePanelZone, n)
                continue
            api_call("PointObj.SetPanelZone", po.SetPanelZone, n, PZ_PROP[p.property], p.doubler_thickness,
                     p.k_major or 0.0, p.k_minor or 0.0, p.link_property or "", PZ_CONN[p.connectivity],
                     0 if p.local_axis_angle is None else 1, p.local_axis_angle or 0.0)
        _refresh_view(sap)
        back = {n: read_panel_zone(sap, n, p.software) for n in pts}
    diff: dict[str, Any] = {}
    for n, b in back.items():
        if p.operation == "delete":
            if b:
                diff[n] = b
            continue
        if not b:
            diff[n] = None
            continue
        bad = {k: [v, b.get(k)] for k, v in (want or {}).items()
               if not (_close(b.get(k, float("nan")), v) if isinstance(v, float) else b.get(k) == v)}
        if bad:
            diff[n] = bad
    out = {**plan, "read_back": {n: back[n] for n in pts[:50]}, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out


# --------------------------------------------------------------------------- point local axes (SAP2000)

class PointAxesP(_EditP):
    point_names: list[str] | None = Field(default=None, max_length=MAX, description="points")
    group: str | None = GROUP
    angles: list[float] = Field(min_length=3, max_length=3, description=(
        "[a, b, c] degrees: rotations of the point's local axes about global Z, then about the new -Y (local 2), "
        "then about the new X (local 1) - as SAP2000's Assign > Joint > Local Axes. [0, 0, 0] = global"))


@action("assign_point_local_axes", "Rotate the local axes of points (supports and springs act in them; reactions are "
        "reported in them) by three angles a, b, c. Answers with the angles read back.", "Assignments", "edit",
        PointAxesP, programs="S")
def assign_point_local_axes(p: PointAxesP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    pts = _targets(sap, "PointObj", p.point_names, p.group, "points")
    plan = {"points": len(pts), "names": pts[:50], "angles": p.angles}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_point_axes", "set point local axes")
    for n in pts:
        api_call("PointObj.SetLocalAxes", sap.PointObj.SetLocalAxes, n, *p.angles)
    _refresh_view(sap)
    back = {}
    for n in pts:
        g = api_call("PointObj.GetLocalAxes", sap.PointObj.GetLocalAxes, n)
        back[n] = {"angles": [_f(g[0]), _f(g[1]), _f(g[2])], "advanced": bool(g[3])}
    diff = {n: b for n, b in back.items() if not all(_close(a, c) for a, c in zip(b["angles"], p.angles))}
    out = {**plan, "read_back": {n: back[n] for n in pts[:50]}, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out


# --------------------------------------------------------------------------- area edge constraints

class EdgeP(_EditP):
    area_names: list[str] | None = Field(default=None, max_length=MAX, description="areas")
    group: str | None = GROUP
    constrained: bool = Field(description=(
        "true: generate edge constraints (the area's mesh is tied to points on its edges that do not match its "
        "nodes, e.g. a slab meshed differently from its neighbour) | false: remove them"))


@action("assign_area_edge_constraint", "Turn the edge constraint of areas on or off (ties an area's mesh to "
        "unmatched points on its edges). Answers with the setting read back.", "Assignments", "edit", EdgeP)
def assign_area_edge_constraint(p: EdgeP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    areas = _targets(sap, "AreaObj", p.area_names, p.group, "areas")
    plan = {"areas": len(areas), "names": areas[:50], "constrained": p.constrained}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_edge_constraint", "set area edge constraints")
    for n in areas:
        api_call("AreaObj.SetEdgeConstraint", sap.AreaObj.SetEdgeConstraint, n, p.constrained)
    _refresh_view(sap)
    back = {n: bool(api_call("AreaObj.GetEdgeConstraint", sap.AreaObj.GetEdgeConstraint, n)[0]) for n in areas}
    diff = {n: b for n, b in back.items() if b != p.constrained}
    out = {**plan, "read_back": {n: back[n] for n in areas[:100]}, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out


__all__ = ["read_bracing", "read_panel_zone", "read_tc", "_names"]
