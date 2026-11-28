"""Phase 8.7 (0.24.0): support displacements, area wind pressure coefficients, and SAP2000 frame / area strain,
deformation and gravity loads.

Typed in each program's API (docs/api/methods.csv, measured with tests/diag_86.py on ETABS 23.2, SAP2000 27.1 and
SAFE 23.3, 28 Nov 2026):
- PointObj.SetLoadDispl / GetLoadDispl / DeleteLoadDispl (E S F): ground displacement of a joint, acting only in the
  joint's restrained (or spring) degrees of freedom.
- AreaObj.SetLoadWindPressure / GetLoadWindPressure (E S F): the Cp of a windward face (1, pressure varies over the
  height) or another face (2, constant over the height); it acts only when the wind pattern's automatic load takes
  its exposure from shell objects. SAP2000's SetLoadWindPressure_1 adds a DistributionType whose codes are not
  documented, so it is not used.
- SAP2000 only: FrameObj.SetLoadStrain / SetLoadDeformation / SetLoadGravity and AreaObj.SetLoadStrain /
  SetLoadGravity (with their getters and deleters).
Values (displacements, Cp, strains, multipliers) come from the designer (D1).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import ActionFailed, _dry, _refresh_view, action, bridge
from csi_bridge import CSIApiError, CSIValidationError, api_call, as_list, describe_units
from model_read import _try
from modelling import _start_edit, _targets
from objects import MAX, _EditP

DOF = ("U1", "U2", "U3", "R1", "R2", "R3")
SAMPLE = 20


def _f(v: Any) -> float:
    return float(f"{float(v):.10g}")


def _patterns(sap: Any) -> list[str]:
    return bridge.try_name_list(sap, ["LoadPatterns"])[0] or []


def _check_pattern(sap: Any, name: str) -> None:
    pats = _patterns(sap)
    if name not in pats:
        raise CSIValidationError(f"Load pattern '{name}' is not defined.", hint=f"Defined: {pats[:30]}")


def _rows(g: Any, n_fields: int) -> list[tuple]:
    """Getter outputs (count, then parallel arrays) as rows."""
    if not g:
        return []
    n = int(g[0])
    cols = [as_list(g[i]) for i in range(1, 1 + n_fields)]
    return [tuple(c[i] for c in cols) for i in range(n) if all(len(c) > i for c in cols)]


# --------------------------------------------------------------------------- support displacement (E S F)

def read_displacements(sap: Any, point: str) -> list[dict[str, Any]]:
    g = _try(sap, "PointObj.GetLoadDispl", point)
    out = []
    for r in _rows(g, 10):
        out.append({"pattern": str(r[1]), "csys": str(r[3]), "values": [_f(v) for v in r[4:10]]})
    return out


def _restraint(sap: Any, point: str) -> list[bool]:
    g = _try(sap, "PointObj.GetRestraint", point, [False] * 6)
    return [bool(v) for v in as_list(g[0])] if g else [False] * 6


def _has_spring(sap: Any, point: str) -> list[bool]:
    g = _try(sap, "PointObj.GetSpring", point, [0.0] * 6)
    return [abs(float(v)) > 0 for v in as_list(g[0])] if g else [False] * 6


class DisplP(_EditP):
    points: list[str] | None = Field(default=None, max_length=MAX, description="joint names")
    group: str | None = Field(default=None, description="or a group: its joints")
    load_pattern: str = Field(description="an existing load pattern")
    values: list[float] = Field(min_length=6, max_length=6, description=(
        "[U1, U2, U3, R1, R2, R3]: ground displacements (length units) and rotations (radians), from the designer; "
        "act only in restrained or spring degrees of freedom"))
    csys: Literal["Global", "Local"] = Field(default="Global", description="coordinate system of the values")
    replace: bool = Field(default=True, description="replace the joints' earlier displacements in this pattern")


@action("assign_support_displacement", "Ground (support) displacements and rotations at joints, in a load pattern - "
        "settlement or imposed support movement [U1, U2, U3, R1, R2, R3] from the designer. They act only in the "
        "joints' restrained or spring degrees of freedom: the answer lists joints where a non-zero value meets a free "
        "direction (no effect). Answers with the displacements read back.", "Modelling", "edit", DisplP)
def assign_support_displacement(p: DisplP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    pts = _targets(sap, "PointObj", p.points, p.group, "points")
    _check_pattern(sap, p.load_pattern)
    if not any(p.values):
        raise CSIValidationError("All six values are zero.", hint="Give at least one non-zero displacement.")
    no_effect = []
    for n in pts:
        held = [r or s for r, s in zip(_restraint(sap, n), _has_spring(sap, n))]
        free = [DOF[i] for i in range(6) if p.values[i] and not held[i]]
        if free:
            no_effect.append({"point": n, "free_directions": free})
    plan = {"points": len(pts), "first_points": pts[:20], "load_pattern": p.load_pattern, "values": p.values,
            "csys": p.csys, "replace": p.replace, "units": {**describe_units(p.units), "rotations": "radians"}}
    if no_effect:
        plan["no_effect"] = no_effect[:50]
        plan["note"] = ("a displacement in a free direction does nothing; restrain the direction or give the joint a "
                        "spring there")
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    failed = []
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_support_displacement", "assign support displacements")
        for n in pts:
            try:
                api_call(f"PointObj.SetLoadDispl('{n}')", sap.PointObj.SetLoadDispl, n, p.load_pattern,
                         [float(v) for v in p.values], p.replace, p.csys)
            except CSIApiError as exc:
                failed.append({"point": n, "error": str(exc)[:200]})
        back = {n: [r for r in read_displacements(sap, n) if r["pattern"] == p.load_pattern]
                for n in pts[:SAMPLE]}
    _refresh_view(sap)
    out = {**plan, "done": len(pts) - len(failed), "read_back": back, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    wrong = [n for n, rows in back.items() if n not in {f["point"] for f in failed}
             and not any(all(abs(a - b) <= 1e-9 * max(1.0, abs(b)) for a, b in zip(r["values"], p.values))
                         for r in rows)]
    if wrong:
        out["not_kept_as_given"] = wrong
    if failed:
        out["failed"] = failed
        out["message"] = f"{len(failed)} of {len(pts)} joints were refused."
        raise ActionFailed(out)
    return out


# --------------------------------------------------------------------------- area wind pressure (E S F)

WIND_FACE = {"windward": 1, "leeward_or_side": 2}


def read_wind_pressure(sap: Any, area: str, software: str) -> list[dict[str, Any]]:
    g = _try(sap, "AreaObj.GetLoadWindPressure", area)
    return [{"pattern": str(r[1]), "face": _inv(WIND_FACE, r[2]), "cp": _f(r[3])} for r in _rows(g, 4)]


def _inv(d: dict[str, int], v: Any) -> Any:
    try:
        return {b: a for a, b in d.items()}.get(int(v), v)
    except (TypeError, ValueError):
        return v


class WindPressureP(_EditP):
    areas: list[str] | None = Field(default=None, max_length=MAX, description="area names (walls / shells)")
    group: str | None = Field(default=None, description="or a group: its areas")
    load_pattern: str = Field(description="an existing wind load pattern")
    face: Literal["windward", "leeward_or_side"] = Field(description=(
        "windward (pressure varies over the height) | leeward_or_side (other faces: constant over the height)"))
    cp: float = Field(ge=-10, le=10, description="pressure coefficient Cp from the designer (+ = toward the face)")


@action("assign_area_wind_pressure", "Wind pressure coefficients on areas (walls / shells) for a wind load pattern: "
        "windward or leeward / side face and Cp, from the designer. They take effect when the pattern's automatic wind "
        "load takes its exposure from shell objects (set_auto_wind / set_auto_load with exposure from shells). Answers "
        "with the coefficients read back.", "Modelling", "edit", WindPressureP)
def assign_area_wind_pressure(p: WindPressureP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    areas = _targets(sap, "AreaObj", p.areas, p.group, "areas")
    _check_pattern(sap, p.load_pattern)
    ptype = _try(sap, "LoadPatterns.GetLoadType", p.load_pattern)
    if ptype and int(ptype[0]) != 6:
        raise CSIValidationError(f"Load pattern '{p.load_pattern}' is not a wind pattern (type {int(ptype[0])}).",
                                 hint="define_load_pattern type wind first.")
    plan = {"areas": len(areas), "first_areas": areas[:20], "load_pattern": p.load_pattern, "face": p.face,
            "cp": p.cp}
    code = _try(sap, "LoadPatterns.AutoWind.GetAutoCode", p.load_pattern)
    plan["pattern_auto_wind_code"] = str(code[0]) if code else None
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    failed = []
    snap, was_locked = _start_edit(p, sap, "before_wind_pressure", "assign wind pressure coefficients")
    for a in areas:
        try:
            api_call(f"AreaObj.SetLoadWindPressure('{a}')", sap.AreaObj.SetLoadWindPressure, a, p.load_pattern,
                     WIND_FACE[p.face], p.cp)
        except CSIApiError as exc:
            failed.append({"area": a, "error": str(exc)[:200]})
    back = {a: [r for r in read_wind_pressure(sap, a, p.software) if r["pattern"] == p.load_pattern]
            for a in areas[:SAMPLE]}
    _refresh_view(sap)
    out = {**plan, "done": len(areas) - len(failed), "read_back": back, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    wrong = [a for a, rows in back.items() if a not in {f["area"] for f in failed}
             and not any(r["face"] == p.face and abs(r["cp"] - p.cp) < 1e-9 for r in rows)]
    if wrong:
        out["not_kept_as_given"] = wrong
    if failed:
        out["failed"] = failed
        out["message"] = f"{len(failed)} of {len(areas)} areas were refused."
        raise ActionFailed(out)
    return out


# --------------------------------------------------------------------------- SAP2000 strain / deformation / gravity

FRAME_STRAIN = {"axial": 1, "shear_2": 2, "shear_3": 3, "torsion": 4, "curvature_2": 5, "curvature_3": 6}
# components 1 to 3 are membrane strains 11, 22, 12 in every listing of the API; the order of 4 to 8 (curvatures,
# transverse shear) could not be confirmed (28 Nov), so they are not offered
AREA_STRAIN = {"strain_11": 1, "strain_22": 2, "strain_12": 3}
FRAME_DEF = ("U1", "U2", "U3", "R1", "R2", "R3")


def read_imposed(sap: Any, coll: str, name: str) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    comp = FRAME_STRAIN if coll == "FrameObj" else AREA_STRAIN
    g = _try(sap, f"{coll}.GetLoadStrain", name)
    out["strain"] = [{"pattern": str(r[1]), "component": _inv(comp, r[2]), "value": _f(r[3])} for r in _rows(g, 5)]
    g = _try(sap, f"{coll}.GetLoadGravity", name)
    out["gravity"] = [{"pattern": str(r[1]), "csys": str(r[2]), "multipliers": [_f(r[3]), _f(r[4]), _f(r[5])]}
                      for r in _rows(g, 6)]
    if coll == "FrameObj":
        g = _try(sap, "FrameObj.GetLoadDeformation", name)
        rows = _rows(g, 14)
        out["deformation"] = [{"pattern": str(r[1]),
                               "values": {FRAME_DEF[i]: _f(r[8 + i]) for i in range(6) if bool(r[2 + i])}}
                              for r in rows]
    return out


class ImposedP(_EditP):
    target_type: Literal["frame", "area"] = Field(description="frame | area")
    object_names: list[str] | None = Field(default=None, max_length=MAX, description="frames or areas")
    group: str | None = Field(default=None, description="or a group: its frames / areas")
    load_pattern: str = Field(description="an existing load pattern")
    load_type: Literal["strain", "deformation", "gravity"] = Field(description=(
        "strain: a strain / curvature component | deformation (frames): imposed deformations of the whole frame "
        "(U1 elongation, U2 / U3 shear, R1 twist, R2 / R3 rotation) | gravity: self-weight multipliers [x, y, z] "
        "on these objects only"))
    component: Literal["axial", "shear_2", "shear_3", "torsion", "curvature_2", "curvature_3", "strain_11",
                       "strain_22", "strain_12"] | None = Field(default=None, description=(
        "strain: frames axial | shear_2 | shear_3 | torsion | curvature_2 | curvature_3; areas (membrane) strain_11 | "
        "strain_22 | strain_12"))
    value: float | None = Field(default=None, description="strain: the strain (or curvature, 1 / length units)")
    deformation: dict[Literal["U1", "U2", "U3", "R1", "R2", "R3"], float] | None = Field(default=None, description=(
        "deformation: {U1: elongation, R3: rotation ...} - lengths in length units, rotations in radians"))
    multipliers: list[float] | None = Field(default=None, min_length=3, max_length=3, description=(
        "gravity: [x, y, z] multipliers of the objects' self weight (e.g. [0, 0, -1] = weight downward)"))
    csys: str = Field(default="Global", description="gravity: coordinate system of the multipliers")
    replace: bool = Field(default=True, description="replace the objects' earlier loads of this type in the pattern")

    @model_validator(mode="after")
    def _check(self) -> "ImposedP":
        if self.load_type == "strain":
            comps = FRAME_STRAIN if self.target_type == "frame" else AREA_STRAIN
            if self.component not in comps or self.value is None:
                raise ValueError(f"strain on {self.target_type}s needs component (one of {', '.join(comps)}) and value")
        elif self.load_type == "deformation":
            if self.target_type != "frame":
                raise ValueError("deformation loads are for frames")
            if not self.deformation:
                raise ValueError("deformation needs deformation {U1: ..., ...}")
        elif not self.multipliers:
            raise ValueError("gravity needs multipliers [x, y, z]")
        return self


@action("assign_strain_or_gravity_load", "SAP2000 imposed loads on frames or areas in a load pattern: a strain or "
        "curvature component, frame deformations (elongation, shear, twist, rotation), or gravity (self-weight "
        "multipliers on these objects only) - values from the designer. Answers with the loads read back.", "Modelling",
        "edit", ImposedP, programs="S")
def assign_strain_or_gravity_load(p: ImposedP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    coll = "FrameObj" if p.target_type == "frame" else "AreaObj"
    objs = _targets(sap, coll, p.object_names, p.group, p.target_type + "s")
    _check_pattern(sap, p.load_pattern)
    plan: dict[str, Any] = {"target_type": p.target_type, "objects": len(objs), "first_objects": objs[:20],
                            "load_pattern": p.load_pattern, "load_type": p.load_type, "replace": p.replace,
                            "units": describe_units(p.units)}
    if p.load_type == "strain":
        plan.update(component=p.component, value=p.value)
    elif p.load_type == "deformation":
        plan["deformation"] = p.deformation
    else:
        plan.update(multipliers=p.multipliers, csys=p.csys)
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    obj = getattr(sap, coll)
    failed = []
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_imposed_load", f"assign {p.load_type} loads")
        for n in objs:
            try:
                if p.load_type == "strain":
                    comps = FRAME_STRAIN if coll == "FrameObj" else AREA_STRAIN
                    api_call(f"{coll}.SetLoadStrain('{n}')", obj.SetLoadStrain, n, p.load_pattern,
                             comps[p.component], float(p.value), p.replace, "")
                elif p.load_type == "deformation":
                    dof = [k in p.deformation for k in FRAME_DEF]
                    vals = [float(p.deformation.get(k, 0.0)) for k in FRAME_DEF]
                    if p.replace:
                        _try(sap, "FrameObj.DeleteLoadDeformation", n, p.load_pattern)
                    api_call(f"FrameObj.SetLoadDeformation('{n}')", obj.SetLoadDeformation, n, p.load_pattern, dof,
                             vals)
                else:
                    api_call(f"{coll}.SetLoadGravity('{n}')", obj.SetLoadGravity, n, p.load_pattern,
                             *[float(v) for v in p.multipliers], p.replace, p.csys)
            except CSIApiError as exc:
                failed.append({"object": n, "error": str(exc)[:200]})
        back = {}
        for n in objs[:SAMPLE]:
            got = read_imposed(sap, coll, n).get(p.load_type, [])
            back[n] = [r for r in got if r["pattern"] == p.load_pattern]
    _refresh_view(sap)
    out = {**plan, "done": len(objs) - len(failed), "read_back": back, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if failed:
        out["failed"] = failed
        out["message"] = f"{len(failed)} of {len(objs)} objects were refused."
        raise ActionFailed(out)
    return out
