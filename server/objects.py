"""Phase 3, batch 3: objects - create points, frames and areas; assign their properties; temperature loads; groups,
selection and delete.

Edits take dry_run, snapshot, allow_unlock and units; targets are given by names or by a group; names are checked
first (ETABS / SAFE setters answer 0 for unknown names). Answers carry the assignments read back with the same
readers as get_object_assignments.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import (
    ALLOW_UNLOCK, DRY_RUN, SNAPSHOT, SOFTWARE, UNITS, Params, _dry, _refresh_view, action, bridge,
)
from csi_bridge import CSIValidationError, Software, UnitPreset, api_call, as_list, describe_units
from model_read import _area_assign, _frame_assign, _names, _point_assign, _try
from modelling import _start_edit, _targets

DOF = ("U1", "U2", "U3", "R1", "R2", "R3")
FRAME_DOF = ("P", "V2", "V3", "T", "M2", "M3")
RESTRAINTS = {"fixed": [True] * 6, "pinned": [True, True, True, False, False, False],
              "roller_z": [False, False, True, False, False, False], "free": [False] * 6}
XYZ = Field(min_length=3, max_length=3, description="[x, y, z] in the length unit of `units`")
GROUP = Field(default=None, description="a group: all its objects of this kind")
MAX = 5000


def _restraint(value: Any) -> list[bool]:
    if isinstance(value, str):
        return list(RESTRAINTS[value])
    return [bool(v) for v in value]


class _EditP(Params):
    software: Software = SOFTWARE
    units: UnitPreset = UNITS
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


RestraintIn = Literal["fixed", "pinned", "roller_z", "free"] | list[bool]
RESTRAINT_DOC = ("fixed | pinned | roller_z | free, or six booleans [U1, U2, U3, R1, R2, R3] (true = restrained, "
                 "global)")


def _check_six(v: Any) -> Any:
    if isinstance(v, list) and len(v) != 6:
        raise ValueError("give six values [U1, U2, U3, R1, R2, R3]")
    return v


# --------------------------------------------------------------------------- create points

class NewPoint(Params):
    xyz: list[float] = XYZ
    name: str = Field(default="", description="name for the new point; default: the program numbers it")


class PointsP(_EditP):
    points: list[NewPoint] = Field(min_length=1, max_length=MAX, description="points to add")
    restraint: RestraintIn | None = Field(default=None, description="optional, for all of them: " + RESTRAINT_DOC)

    @model_validator(mode="after")
    def _check(self) -> "PointsP":
        _check_six(self.restraint)
        return self


@action("create_points", "Add points by coordinates (a point already at the place is reused, as in the program), "
        "optionally restrained. Answers with the point names.", "Objects", "edit", PointsP)
def create_points(p: PointsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    plan = {"points": len(p.points), "restraint": p.restraint, "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_points", "add points")
        names = []
        before = set(_names(sap, "PointObj"))
        for pt in p.points:
            o = api_call("PointObj.AddCartesian", sap.PointObj.AddCartesian, *pt.xyz, "", pt.name, "Global")
            names.append(str(o[0]))
            if p.restraint is not None:
                api_call("PointObj.SetRestraint", sap.PointObj.SetRestraint, names[-1], _restraint(p.restraint))
        _refresh_view(sap)
        free = _etabs_base_free(sap, p.software, names)
        now = set(_names(sap, "PointObj"))
        gone_new = sorted(set(names) - now)
        merged_old = sorted(before - now)
        if gone_new or merged_old:
            # SAP2000 (measured): a point drawn within the program's merge tolerance of another replaces it - Q2
            # 0.5 mm from Q1 removed Q1; NEAR 0.5 mm from point 4 removed point 4 (frames now end at NEAR)
            raise CSIValidationError(
                f"The program merged points on creation: {gone_new + merged_old} no longer exist (a new point "
                "within the program's merge tolerance of another replaces it).",
                hint="Points closer than the merge tolerance cannot be kept apart; space them further, or use the "
                     "surviving names: " + str(sorted(now & set(names))[:20]))
    out = {**plan, "point_names": names, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if free:
        out.update(free_base_level_points=free[:50], note=BASE_NOTE)
    return out


# --------------------------------------------------------------------------- create frames

class NewFrame(Params):
    start: str | list[float] = Field(description="start: a point name, or [x, y, z]")
    end: str | list[float] = Field(description="end: a point name, or [x, y, z]")
    name: str = Field(default="", description="name for the new frame; default: the program numbers it")

    @model_validator(mode="after")
    def _check(self) -> "NewFrame":
        for v in (self.start, self.end):
            if isinstance(v, list) and len(v) != 3:
                raise ValueError("a coordinate end is [x, y, z]")
        return self


class FramesP(_EditP):
    frames: list[NewFrame] = Field(min_length=1, max_length=MAX, description="frames to add")
    section: str = Field(description="an existing frame section[[EF: | none (a null line, e.g. a support line)]]")


def _point_names(sap: Any) -> set[str]:
    return set(_names(sap, "PointObj"))


BASE_NOTE = ("ETABS holds joints at the Base level fixed in the analysis when they have no restraint (measured: a "
             "free Base-level joint gives zero displacement and moves no load into its members). Put members that "
             "must deflect above the Base level, or support these joints as intended.")


def _etabs_base_free(sap: Any, software: str, points: list[str]) -> list[str]:
    """ETABS: the new points that sit at the Base level without a restraint (see BASE_NOTE)."""
    if software != "ETABS" or not points:
        return []
    st = _try(sap, "Story.GetStories_2")
    if not st:
        return []
    base = float(st[0])
    free = []
    for pt in dict.fromkeys(points):
        c = _try(sap, "PointObj.GetCoordCartesian", pt, 0.0, 0.0, 0.0)
        r = _try(sap, "PointObj.GetRestraint", pt)
        if c and abs(float(c[2]) - base) < 1e-6 and r and not any(as_list(r[0])):
            free.append(pt)
    return free


@action("create_frames", "Add frames between points (point names or coordinates) with one section[[EF:; answers "
        "with labels and stories]]. Answers with the frame names and their end points.", "Objects", "edit", FramesP)
def create_frames(p: FramesP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    secs = bridge.try_name_list(sap, ["PropFrame"])[0] or []
    section = "None" if p.section.lower() == "none" else p.section
    if section == "None":                  # ETABS / SAFE null line (e.g. a support line carrying a line spring)
        if p.software == "SAP2000":
            raise CSIValidationError("SAP2000 frames need a section.", hint="Define one with define_frame_section.")
    elif p.section not in secs:
        raise CSIValidationError(f"Frame section '{p.section}' is not defined.", hint=f"Defined: {secs[:30]}")
    named = {e for f in p.frames for e in (f.start, f.end) if isinstance(e, str)}
    if named:
        missing = sorted(named - _point_names(sap))
        if missing:
            raise CSIValidationError(f"Points not in the model: {missing[:20]}.")
    plan = {"frames": len(p.frames), "section": section, "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    out = []
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_frames", "add frames")
        for f in p.frames:
            if isinstance(f.start, str) and isinstance(f.end, str):
                o = api_call("FrameObj.AddByPoint", sap.FrameObj.AddByPoint, f.start, f.end, "", section, f.name)
            else:
                ends = []
                for e in (f.start, f.end):
                    if isinstance(e, str):
                        c = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, e, 0.0, 0.0, 0.0)
                        ends += [float(c[0]), float(c[1]), float(c[2])]
                    else:
                        ends += list(e)
                o = api_call("FrameObj.AddByCoord", sap.FrameObj.AddByCoord, *ends, "", section, f.name, "Global")
            name = str(o[0])
            pts = api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, name, "", "")
            rec = {"name": name, "points": [str(pts[0]), str(pts[1])]}
            if p.software != "SAP2000":
                lab = _try(sap, "FrameObj.GetLabelFromName", name)
                if lab:
                    rec.update(label=str(lab[0]), story=str(lab[1]).strip())
            out.append(rec)
        _refresh_view(sap)
        free = _etabs_base_free(sap, p.software, [pt for r in out for pt in r["points"]])
    res = {**plan, "created": out, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if free:
        res.update(free_base_level_points=free[:50], note=BASE_NOTE)
    return res


# --------------------------------------------------------------------------- create areas

class NewArea(Params):
    points: list[str] | list[list[float]] = Field(min_length=3, max_length=100, description=(
        "corners in order: point names, or [x, y, z] coordinates"))
    name: str = Field(default="", description="name for the new area; default: the program numbers it")

    @model_validator(mode="after")
    def _check(self) -> "NewArea":
        if self.points and isinstance(self.points[0], list) and any(len(c) != 3 for c in self.points):
            raise ValueError("coordinate corners are [x, y, z]")
        return self


class AreasP(_EditP):
    areas: list[NewArea] = Field(min_length=1, max_length=MAX, description="areas to add")
    section: str = Field(default="", description="an existing area section (not needed for openings)")
    opening: bool = Field(default=False, description="true: the areas are openings (no section, no stiffness)")

    @model_validator(mode="after")
    def _check(self) -> "AreasP":
        if not self.opening and not self.section:
            raise ValueError("give section (or opening: true)")
        return self


@action("create_areas", "Add areas (slabs, walls, shells, openings) by corner points or coordinates with one area "
        "section. [[F:SAFE draws new areas at the base level of a grid-only model (API limit of SAFE 23.3). ]]"
        "Answers with the area names and their corner points.", "Objects", "edit", AreasP)
def create_areas(p: AreasP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    if p.section:
        secs = bridge.try_name_list(sap, ["PropArea"])[0] or []
        if p.section not in secs:
            raise CSIValidationError(f"Area section '{p.section}' is not defined.", hint=f"Defined: {secs[:30]}")
    named = {c for a in p.areas for c in a.points if isinstance(c, str)}
    if named:
        missing = sorted(named - _point_names(sap))
        if missing:
            raise CSIValidationError(f"Points not in the model: {missing[:20]}.")
    plan = {"areas": len(p.areas), "section": p.section or "(opening)", "opening": p.opening,
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    out = []
    prop = p.section or "Default"
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_areas", "add areas")
        for a in p.areas:
            n = len(a.points)
            if isinstance(a.points[0], str):
                o = api_call("AreaObj.AddByPoint", sap.AreaObj.AddByPoint, n, list(a.points), "", prop, a.name)
            else:
                xs, ys, zs = ([float(c[i]) for c in a.points] for i in range(3))
                o = api_call("AreaObj.AddByCoord", sap.AreaObj.AddByCoord, n, xs, ys, zs, "", prop, a.name,
                             "Global")
            name = next((str(x) for x in reversed(o) if isinstance(x, str) and x), "")
            if not name:
                raise CSIValidationError("The program did not return the new area's name.")
            if p.opening:
                api_call("AreaObj.SetOpening", sap.AreaObj.SetOpening, name, True)
            pts = api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, name)
            rec = {"name": name, "points": [str(x) for x in as_list(pts[1])]}
            if p.software != "SAP2000":
                lab = _try(sap, "AreaObj.GetLabelFromName", name)
                if lab:
                    rec.update(label=str(lab[0]), story=str(lab[1]).strip())
            out.append(rec)
        _refresh_view(sap)
        free = _etabs_base_free(sap, p.software, [pt for r in out for pt in r["points"]])
    res = {**plan, "created": out, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if free:
        res.update(free_base_level_points=free[:50], note=BASE_NOTE)
    return res


# --------------------------------------------------------------------------- assign point properties

class PointAssignP(_EditP):
    point_names: list[str] | None = Field(default=None, max_length=MAX, description="points")
    group: str | None = GROUP
    restraint: RestraintIn | None = Field(default=None, description=RESTRAINT_DOC)
    spring_stiffness: list[float] | None = Field(default=None, min_length=6, max_length=6, description=(
        "point spring [U1, U2, U3, R1, R2, R3] in global axes (force / length, moment / radian), from the user; "
        "replaces existing springs; all zero removes them"))
    spring_property: str | None = Field(default=None, description=(
        "a point spring property (define_spring_property); 'none' removes the assignment"))
    mass: list[float] | None = Field(default=None, min_length=6, max_length=6, description=(
        "additional mass [U1, U2, U3, R1, R2, R3] (mass units of `units`); replaces; all zero removes"))
    diaphragm: str | None = Field(default=None, description=(
        "a diaphragm name (a rigid one is created when missing) | from_shell | disconnect"))

    @model_validator(mode="after")
    def _check(self) -> "PointAssignP":
        _check_six(self.restraint)
        if not any(v is not None for v in (self.restraint, self.spring_stiffness, self.spring_property, self.mass,
                                           self.diaphragm)):
            raise ValueError("give at least one of restraint, spring_stiffness, spring_property, mass, diaphragm")
        if self.spring_stiffness and any(v < 0 for v in self.spring_stiffness):
            raise ValueError("spring stiffnesses must not be negative")
        return self


def _diaphragm(sap: Any, name: str) -> bool:
    """Make sure a diaphragm exists (rigid when created). True when it was created."""
    names = [str(x) for x in as_list((_try(sap, "Diaphragm.GetNameList") or [0, []])[1])]
    if name in names:
        return False
    api_call("Diaphragm.SetDiaphragm", sap.Diaphragm.SetDiaphragm, name, False)
    return True


@action("assign_point_properties", "Assign to points (names or a group): restraint, spring stiffnesses, "
        "[[EF:spring property, diaphragm, ]]additional mass. Answers with the assignments read back.", "Objects", "edit",
        PointAssignP, fields={"spring_property": "EF", "diaphragm": "EF"})
def assign_point_properties(p: PointAssignP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    pts = _targets(sap, "PointObj", p.point_names, p.group, "points")
    if p.spring_property and p.spring_property.lower() != "none":
        props = bridge.try_name_list(sap, ["PropPointSpring"])[0] or []
        if p.spring_property not in props:
            raise CSIValidationError(f"Point spring property '{p.spring_property}' is not defined.",
                                     hint=f"Defined: {props[:30]}; define_spring_property makes one.")
    plan = {"points": len(pts), "restraint": p.restraint, "spring_stiffness": p.spring_stiffness,
            "spring_property": p.spring_property, "mass": p.mass, "diaphragm": p.diaphragm,
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    po = sap.PointObj
    created = False
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_point_assign", "assign point properties")
        if p.diaphragm and p.diaphragm not in ("from_shell", "disconnect"):
            created = _diaphragm(sap, p.diaphragm)
        for n in pts:
            if p.restraint is not None:
                r = _restraint(p.restraint)
                if any(r):
                    api_call("PointObj.SetRestraint", po.SetRestraint, n, r)
                else:
                    api_call("PointObj.DeleteRestraint", po.DeleteRestraint, n)
            if p.spring_stiffness is not None:
                if any(p.spring_stiffness):
                    api_call("PointObj.SetSpring", po.SetSpring, n, list(p.spring_stiffness), 0, False, True)
                else:
                    api_call("PointObj.DeleteSpring", po.DeleteSpring, n)
            if p.spring_property is not None:
                api_call("PointObj.SetSpringAssignment", po.SetSpringAssignment, n,
                         "None" if p.spring_property.lower() == "none" else p.spring_property)
            if p.mass is not None:
                if any(p.mass):
                    api_call("PointObj.SetMass", po.SetMass, n, list(p.mass), 0, False, True)
                else:
                    api_call("PointObj.DeleteMass", po.DeleteMass, n)
            if p.diaphragm is not None:
                if p.diaphragm == "disconnect":
                    api_call("PointObj.SetDiaphragm", po.SetDiaphragm, n, 1, "")
                elif p.diaphragm == "from_shell":
                    api_call("PointObj.SetDiaphragm", po.SetDiaphragm, n, 2, "")
                else:
                    api_call("PointObj.SetDiaphragm", po.SetDiaphragm, n, 3, p.diaphragm)
        _refresh_view(sap)
        back = [_point_assign(sap, p.software, n, False, True) for n in pts[:20]]
    return {**plan, "diaphragm_created": created, "read_back": back, "read_back_shown": len(back),
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- assign frame properties

class Releases(Params):
    i: list[Literal[FRAME_DOF]] = Field(default=[], description="released at the start: P V2 V3 T M2 M3")  # type: ignore[valid-type]
    j: list[Literal[FRAME_DOF]] = Field(default=[], description="released at the end: P V2 V3 T M2 M3")  # type: ignore[valid-type]


class EndOffsets(Params):
    auto: bool = Field(default=False, description="true: the program's automatic offsets from connectivity")
    length_i: float = Field(default=0.0, ge=0, description="rigid end length at the start")
    length_j: float = Field(default=0.0, ge=0, description="rigid end length at the end")
    rigid_zone_factor: float = Field(default=0.0, ge=0, le=1, description="rigid-zone factor 0..1")


class Insertion(Params):
    cardinal_point: int = Field(default=10, ge=1, le=11, description=(
        "1 bottom left ... 5 middle centre ... 8 top centre ... 10 centroid (default), 11 shear centre"))
    offset_i: list[float] = Field(default=[0.0, 0.0, 0.0], min_length=3, max_length=3, description=(
        "joint offset at the start [1, 2, 3] in csys"))
    offset_j: list[float] = Field(default=[0.0, 0.0, 0.0], min_length=3, max_length=3, description=(
        "joint offset at the end [1, 2, 3] in csys"))
    csys: Literal["Local", "Global"] = Field(default="Local", description="Local (default) | Global")


class OutputStations(Params):
    max_segment: float | None = Field(default=None, gt=0, description="stations at most this far apart (length)")
    min_sections: int | None = Field(default=None, ge=1, le=1000, description="at least this many segments")
    at_element_ends: bool = Field(default=True, description="also output at the ends of the analysis elements")
    at_point_loads: bool = Field(default=True, description="also output at concentrated loads")

    @model_validator(mode="after")
    def _one(self) -> "OutputStations":
        if (self.max_segment is None) == (self.min_sections is None):
            raise ValueError("give max_segment or min_sections")
        return self


class FrameAssignP(_EditP):
    frame_names: list[str] | None = Field(default=None, max_length=MAX, description="frames")
    group: str | None = GROUP
    section: str | None = Field(default=None, description="an existing frame section")
    releases: Releases | None = Field(default=None, description=(
        "end releases (fully released; the rest fixed); {} removes all"))
    end_offsets: EndOffsets | None = Field(default=None, description="rigid end offsets")
    insertion_point: Insertion | None = Field(default=None, description="cardinal point and joint offsets")
    modifiers: list[float] | None = Field(default=None, min_length=8, max_length=8, description=(
        "property modifiers: area, as2, as3, torsion, i22, i33, mass, weight"))
    local_axis_angle: float | None = Field(default=None, description="rotation of local axes 2-3 about 1 (degrees)")
    output_stations: OutputStations | None = Field(default=None, description=(
        "where results are reported along the frame: max_segment or min_sections"))
    spring_property: str | None = Field(default=None, description=(
        "a line spring property (define_spring_property kind line) along the frame, e.g. a support line; 'none' "
        "removes it"))

    @model_validator(mode="after")
    def _check(self) -> "FrameAssignP":
        if not any(v is not None for v in (self.section, self.releases, self.end_offsets, self.insertion_point,
                                           self.modifiers, self.local_axis_angle, self.output_stations,
                                           self.spring_property)):
            raise ValueError("give at least one of section, releases, end_offsets, insertion_point, modifiers, "
                             "local_axis_angle, output_stations, spring_property")
        return self


@action("assign_frame_properties", "Assign to frames (names or a group): section, end releases, rigid end offsets, "
        "insertion point, property modifiers, local axis angle, output stations[[EF:, line spring property]]. "
        "Answers with the assignments read back.", "Objects", "edit", FrameAssignP, fields={"spring_property": "EF"})
def assign_frame_properties(p: FrameAssignP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    frames = _targets(sap, "FrameObj", p.frame_names, p.group, "frames")
    if p.section:
        secs = bridge.try_name_list(sap, ["PropFrame"])[0] or []
        if p.section not in secs:
            raise CSIValidationError(f"Frame section '{p.section}' is not defined.", hint=f"Defined: {secs[:30]}")
    if p.spring_property and p.spring_property.lower() != "none":
        props = bridge.try_name_list(sap, ["PropLineSpring"])[0] or []
        if p.spring_property not in props:
            raise CSIValidationError(f"Line spring property '{p.spring_property}' is not defined.",
                                     hint=f"Defined: {props[:30]}; define_spring_property (kind line) makes one.")
    plan = {"frames": len(frames), "section": p.section, "spring_property": p.spring_property,
            "releases": p.releases.model_dump() if p.releases else None,
            "end_offsets": p.end_offsets.model_dump() if p.end_offsets else None,
            "insertion_point": p.insertion_point.model_dump() if p.insertion_point else None,
            "modifiers": p.modifiers, "local_axis_angle": p.local_axis_angle,
            "output_stations": p.output_stations.model_dump() if p.output_stations else None,
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    fo = sap.FrameObj
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_frame_assign", "assign frame properties")
        for n in frames:
            if p.section:
                api_call("FrameObj.SetSection", fo.SetSection, n, p.section)
            if p.releases is not None:
                ii = [d in p.releases.i for d in FRAME_DOF]
                jj = [d in p.releases.j for d in FRAME_DOF]
                api_call("FrameObj.SetReleases", fo.SetReleases, n, ii, jj, [0.0] * 6, [0.0] * 6)
            if p.end_offsets is not None:
                e = p.end_offsets
                api_call("FrameObj.SetEndLengthOffset", fo.SetEndLengthOffset, n, e.auto, e.length_i, e.length_j,
                         e.rigid_zone_factor)
            if p.insertion_point is not None:
                ins = p.insertion_point
                api_call("FrameObj.SetInsertionPoint", fo.SetInsertionPoint, n, ins.cardinal_point, False, True,
                         list(ins.offset_i), list(ins.offset_j), ins.csys)
            if p.modifiers is not None:
                api_call("FrameObj.SetModifiers", fo.SetModifiers, n, list(p.modifiers))
            if p.local_axis_angle is not None:
                api_call("FrameObj.SetLocalAxes", fo.SetLocalAxes, n, p.local_axis_angle)
            if p.spring_property is not None:
                api_call("FrameObj.SetSpringAssignment", fo.SetSpringAssignment, n,
                         "None" if p.spring_property.lower() == "none" else p.spring_property)
            if p.output_stations is not None:
                o = p.output_stations
                kind, seg, mins = (1, o.max_segment, 2) if o.max_segment is not None else (2, 24.0, o.min_sections)
                api_call("FrameObj.SetOutputStations", fo.SetOutputStations, n, kind, seg, mins,
                         not o.at_element_ends, not o.at_point_loads, 0)
        _refresh_view(sap)
        back = [_frame_assign(sap, p.software, n, False, True) for n in frames[:20]]
    return {**plan, "read_back": back, "read_back_shown": len(back), "model_was_unlocked": was_locked,
            **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- assign area properties

class AreaAssignP(_EditP):
    area_names: list[str] | None = Field(default=None, max_length=MAX, description="areas")
    group: str | None = GROUP
    section: str | None = Field(default=None, description="an existing area section")
    modifiers: list[float] | None = Field(default=None, min_length=10, max_length=10, description=(
        "stiffness modifiers: f11, f22, f12, m11, m22, m12, v13, v23, mass, weight"))
    local_axis_angle: float | None = Field(default=None, description="rotation of local axes 1-2 about 3 (degrees)")
    opening: bool | None = Field(default=None, description="true: make the areas openings; false: back to areas")
    diaphragm: str | None = Field(default=None, description=(
        "a diaphragm name (a rigid one is created when missing) | none"))
    spring_property: str | None = Field(default=None, description=(
        "an area spring property (define_spring_property); 'none' removes the assignment"))
    subgrade_modulus: float | None = Field(default=None, ge=0, description=(
        "area spring normal to the shell on its bottom face, compression and tension (force / length^3), from the "
        "user; replaces existing area springs; 0 removes them"))

    @model_validator(mode="after")
    def _check(self) -> "AreaAssignP":
        if not any(v is not None for v in (self.section, self.modifiers, self.local_axis_angle, self.opening,
                                           self.diaphragm, self.spring_property, self.subgrade_modulus)):
            raise ValueError("give at least one property to assign")
        return self


@action("assign_area_properties", "Assign to areas (names or a group): section, stiffness modifiers, local axis angle, "
        "opening[[EF:, diaphragm, area spring property]][[S:, area springs from a subgrade modulus]]. Answers with "
        "the assignments read back.", "Objects", "edit", AreaAssignP,
        fields={"diaphragm": "EF", "spring_property": "EF", "subgrade_modulus": "S"})
def assign_area_properties(p: AreaAssignP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    areas = _targets(sap, "AreaObj", p.area_names, p.group, "areas")
    if p.section:
        secs = bridge.try_name_list(sap, ["PropArea"])[0] or []
        if p.section not in secs:
            raise CSIValidationError(f"Area section '{p.section}' is not defined.", hint=f"Defined: {secs[:30]}")
    if p.spring_property and p.spring_property.lower() != "none":
        props = bridge.try_name_list(sap, ["PropAreaSpring"])[0] or []
        if p.spring_property not in props:
            raise CSIValidationError(f"Area spring property '{p.spring_property}' is not defined.",
                                     hint=f"Defined: {props[:30]}; define_spring_property makes one.")
    plan = {"areas": len(areas), **{k: v for k, v in p.model_dump(exclude={"software", "area_names", "group", "units",
                                                                            "allow_unlock", "dry_run", "snapshot"}).items()
                                     if v is not None}, "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    ao = sap.AreaObj
    created = False
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_area_assign", "assign area properties")
        if p.diaphragm and p.diaphragm.lower() != "none":
            created = _diaphragm(sap, p.diaphragm)
        for n in areas:
            if p.section:
                api_call("AreaObj.SetProperty", ao.SetProperty, n, p.section)
            if p.modifiers is not None:
                api_call("AreaObj.SetModifiers", ao.SetModifiers, n, list(p.modifiers))
            if p.local_axis_angle is not None:
                api_call("AreaObj.SetLocalAxes", ao.SetLocalAxes, n, p.local_axis_angle)
            if p.opening is not None:
                api_call("AreaObj.SetOpening", ao.SetOpening, n, p.opening)
            if p.diaphragm is not None:
                api_call("AreaObj.SetDiaphragm", ao.SetDiaphragm, n,
                         "None" if p.diaphragm.lower() == "none" else p.diaphragm)
            if p.spring_property is not None:
                api_call("AreaObj.SetSpringAssignment", ao.SetSpringAssignment, n,
                         "None" if p.spring_property.lower() == "none" else p.spring_property)
            if p.subgrade_modulus is not None:
                if p.subgrade_modulus > 0:
                    # simple spring (1), tension and compression (1), bottom face (-1), normal to the face (local 3)
                    api_call("AreaObj.SetSpring", ao.SetSpring, n, 1, p.subgrade_modulus, 1, "", -1, 1, 3, False,
                             [0.0, 0.0, 0.0], 0.0, True, "Local")
                else:
                    api_call("AreaObj.DeleteSpring", ao.DeleteSpring, n)
        _refresh_view(sap)
        back = [_area_assign(sap, p.software, n, False, True) for n in areas[:20]]
    return {**plan, "diaphragm_created": created, "read_back": back, "read_back_shown": len(back),
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- temperature loads

TEMP_TYPES = {"frame": {"uniform": 1, "gradient_2": 2, "gradient_3": 3}, "area": {"uniform": 1, "gradient_3": 3}}


class TemperatureP(_EditP):
    target_type: Literal["frame", "area"] = Field(description="[[ES:frame | ]]area")
    object_names: list[str] | None = Field(default=None, max_length=MAX, description="frames or areas")
    group: str | None = GROUP
    load_pattern: str = Field(description="an existing load pattern")
    kind: Literal["uniform", "gradient_2", "gradient_3"] = Field(default="uniform", description=(
        "uniform: temperature change | gradient_2 / gradient_3: temperature gradient across local 2 / 3 (frames); "
        "areas: uniform | gradient_3 (through the thickness)"))
    value: float = Field(description="temperature change (degrees of `units`) or gradient (degrees / length)")
    replace: bool = Field(default=True, description="replace this pattern's temperature loads on the objects")

    @model_validator(mode="after")
    def _check(self) -> "TemperatureP":
        if self.kind not in TEMP_TYPES[self.target_type]:
            raise ValueError(f"{self.target_type} temperature kind is one of {', '.join(TEMP_TYPES[self.target_type])}")
        return self


@action("assign_temperature_load", "Temperature loads on [[ES:frames or ]]areas (names or a group): uniform change "
        "or gradient, in a load pattern; values from the user.[[F: SAFE ignores temperature loads on frames (measured) "
        "and a uniform change gives no result in its bending-only analysis; confirm the effect of an area gradient on "
        "a result.]] Answers with the loads read back.", "Objects", "edit", TemperatureP,
        options={"target_type": {"frame": "ES"}})
def assign_temperature_load(p: TemperatureP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    coll = "FrameObj" if p.target_type == "frame" else "AreaObj"
    objs = _targets(sap, coll, p.object_names, p.group, p.target_type + "s")
    pats = bridge.try_name_list(sap, ["LoadPatterns"])[0] or []
    if p.load_pattern not in pats:
        raise CSIValidationError(f"Load pattern '{p.load_pattern}' is not defined.", hint=f"Defined: {pats[:30]}")
    plan = {"target_type": p.target_type, "objects": len(objs), "load_pattern": p.load_pattern, "kind": p.kind,
            "value": p.value, "replace": p.replace, "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    obj = getattr(sap, coll)
    code = TEMP_TYPES[p.target_type][p.kind]
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_temperature", "assign temperature loads")
        for n in objs:
            api_call(f"{coll}.SetLoadTemperature", obj.SetLoadTemperature, n, p.load_pattern, code, p.value, "",
                     p.replace)
        back = []
        for n in objs[:20]:
            g = _try(sap, f"{coll}.GetLoadTemperature", n)
            if g:
                back.append({"name": n, "loads": [
                    {"pattern": str(pat), "type": int(t), "value": round(float(v), 9)}
                    for pat, t, v in zip(as_list(g[2]), as_list(g[3]), as_list(g[4]))]})
    out = {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if p.software == "SAFE":
        out["note"] = ("SAFE analyses out-of-plane bending only (UZ, RX, RY): a uniform change produces no result; "
                       "confirm the effect of a gradient on a result")
    return out


# --------------------------------------------------------------------------- groups

OBJ_TYPES = {1: "point", 2: "frame", 3: "cable", 4: "tendon", 5: "area", 6: "solid", 7: "link"}


class GroupP(Params):
    software: Software = SOFTWARE
    operation: Literal["define", "add", "remove", "delete"] = Field(description=(
        "define (new, empty) | add objects | remove objects | delete the group (objects stay)"))
    name: str = Field(min_length=1, max_length=80, description="group name")
    points: list[str] = Field(default=[], max_length=MAX, description="add / remove: point names")
    frames: list[str] = Field(default=[], max_length=MAX, description="add / remove: frame names")
    areas: list[str] = Field(default=[], max_length=MAX, description="add / remove: area names")
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


def _group_contents(sap: Any, name: str) -> dict[str, int]:
    g = _try(sap, "GroupDef.GetAssignments", name)
    counts: dict[str, int] = {}
    for t in as_list(g[1]) if g else []:
        k = OBJ_TYPES.get(int(t), str(t))
        counts[k] = counts.get(k, 0) + 1
    return counts


@action("set_group", "Groups: define a group, add or remove points / frames / areas, or delete a group (its objects "
        "stay). Answers with what the group holds.", "Objects", "edit", GroupP)
def set_group(p: GroupP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    groups = bridge.try_name_list(sap, ["GroupDef"])[0] or []
    exists = p.name in groups
    if p.operation == "define" and exists:
        raise CSIValidationError(f"Group '{p.name}' already exists.")
    if p.operation in ("remove", "delete") and not exists:
        raise CSIValidationError(f"Group '{p.name}' is not defined.", hint=f"Defined: {groups[:30]}")
    if p.operation in ("add", "remove") and not (p.points or p.frames or p.areas):
        raise CSIValidationError("Give points, frames and / or areas.")
    lists = (("PointObj", p.points), ("FrameObj", p.frames), ("AreaObj", p.areas))
    for coll, names in lists:
        if names:
            missing = sorted(set(names) - set(_names(sap, coll)))
            if missing:
                raise CSIValidationError(f"Not in the model ({coll[:-3].lower()}s): {missing[:20]}.")
    plan = {"group": p.name, "operation": p.operation, "existed": exists,
            "objects": {"points": len(p.points), "frames": len(p.frames), "areas": len(p.areas)}}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_group", "change a group")
    if p.operation == "delete":
        api_call("GroupDef.Delete", sap.GroupDef.Delete, p.name)
        return {**plan, "deleted": True, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if not exists:
        api_call("GroupDef.SetGroup", sap.GroupDef.SetGroup, p.name)
    if p.operation in ("add", "remove"):
        for coll, names in lists:
            obj = getattr(sap, coll)
            for n in names:
                api_call(f"{coll}.SetGroupAssign", obj.SetGroupAssign, n, p.name, p.operation == "remove")
    return {**plan, "group_holds": _group_contents(sap, p.name), "model_was_unlocked": was_locked,
            **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- selection

class SelectP(Params):
    software: Software = SOFTWARE
    mode: Literal["clear", "names", "group", "all"] = Field(description=(
        "clear | names (points / frames / areas below) | group | all"))
    points: list[str] = Field(default=[], max_length=MAX, description="names mode: point names")
    frames: list[str] = Field(default=[], max_length=MAX, description="names mode: frame names")
    areas: list[str] = Field(default=[], max_length=MAX, description="names mode: area names")
    group: str | None = Field(default=None, description="group mode: the group")
    deselect: bool = Field(default=False, description="true: remove these from the selection instead")
    keep_existing: bool = Field(default=False, description="names / group / all: add to the current selection")


@action("select_objects", "Select objects in the program (by names, a group, or all), or clear the selection - e.g. "
        "to show the user which objects an answer is about. The model is not changed. Answers with what is "
        "selected.", "Objects", "edit", SelectP)
def select_objects(p: SelectP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    if p.mode == "group":
        groups = bridge.try_name_list(sap, ["GroupDef"])[0] or []
        if p.group not in groups:
            raise CSIValidationError(f"Group '{p.group}' is not defined.", hint=f"Defined: {groups[:30]}")
    lists = (("PointObj", p.points), ("FrameObj", p.frames), ("AreaObj", p.areas))
    if p.mode == "names":
        if not (p.points or p.frames or p.areas):
            raise CSIValidationError("Give points, frames and / or areas.")
        for coll, names in lists:
            if names:
                missing = sorted(set(names) - set(_names(sap, coll)))
                if missing:
                    raise CSIValidationError(f"Not in the model ({coll[:-3].lower()}s): {missing[:20]}.")
    if p.mode == "clear" or (not p.keep_existing and not p.deselect):
        api_call("SelectObj.ClearSelection", sap.SelectObj.ClearSelection)
    if p.mode == "all":
        api_call("SelectObj.All", sap.SelectObj.All, p.deselect)
    elif p.mode == "group":
        api_call("SelectObj.Group", sap.SelectObj.Group, p.group, p.deselect)
    elif p.mode == "names":
        for coll, names in lists:
            obj = getattr(sap, coll)
            for n in names:
                api_call(f"{coll}.SetSelected", obj.SetSelected, n, not p.deselect)
    _refresh_view(sap)
    g = _try(sap, "SelectObj.GetSelected")
    counts: dict[str, int] = {}
    for t in as_list(g[1]) if g else []:
        k = OBJ_TYPES.get(int(t), str(t))
        counts[k] = counts.get(k, 0) + 1
    return {"mode": p.mode, "selected": counts, "selected_total": sum(counts.values())}


# --------------------------------------------------------------------------- delete

class DeleteP(_EditP):
    kind: Literal["points", "frames", "areas"] = Field(description="points | frames | areas")
    names: list[str] | None = Field(default=None, max_length=MAX, description="objects to delete")
    group: str | None = GROUP


@action("delete_objects", "Delete frames or areas (names or a group); points are deleted only when nothing is "
        "connected to them (the others are kept and listed). Loads and assignments on deleted objects go with them. "
        "Confirm with the user first.", "Objects", "edit", DeleteP)
def delete_objects(p: DeleteP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    coll = {"points": "PointObj", "frames": "FrameObj", "areas": "AreaObj"}[p.kind]
    objs = _targets(sap, coll, p.names, p.group, p.kind)
    plan = {"kind": p.kind, "objects": len(objs), "names": objs[:50]}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_delete", "delete objects")
    points_before = set(_names(sap, "PointObj")) if p.kind != "points" else set()
    obj = getattr(sap, coll)
    for n in objs:
        if p.kind == "points":
            api_call("PointObj.DeleteSpecialPoint", obj.DeleteSpecialPoint, n)
        else:
            api_call(f"{coll}.Delete", obj.Delete, n)
    _refresh_view(sap)
    left = set(_names(sap, coll))
    kept = [n for n in objs if n in left]
    deleted = [n for n in objs if n not in left]
    out = {**plan, "deleted": len(deleted), "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if points_before:
        gone = sorted(points_before - set(_names(sap, "PointObj")))
        if gone:
            out["points_removed_with_them"] = gone[:100]
            out["points_note"] = "the program removes end points that nothing else uses"
    if kept:
        out["kept"] = kept[:100]
        out["kept_reason"] = "points still connected to frames / areas (or carrying assignments) stay in the model"
    return out
