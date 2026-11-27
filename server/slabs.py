"""Phase 5, SAFE / ETABS floors (0.18.0): design strips, post-tensioning tendons (with their tendon property), ETABS
towers. (Soil / spring properties and assignments exist since 0.8: define_spring_property, assign_*_properties.)

Towers and tendon properties have typed API calls (Tower, PropTendon). Strips and tendons have no API setters in SAFE 23 / ETABS 23 (TendonObj has only getters; no strip object -
measured): they go through the programs' own input tables, whose row layout was read from CSI's 'ACI 318-19 PT-SL
Ex001' (a copy): 'Strip Object Connectivity' one row per strip between two point objects; 'Tendon Object Connectivity'
a first row with the span profile (SpanProfile, A / B / C lengths, ZL / ZC / ZR elevations relative to the slab top)
and a row per further point; 'Tendon Assignments - Properties', 'Tendon Load Assignments - Jacking Stress' / 'Loss
Options'. ETABS' tables add a Story column.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import Params, _dry, action, bridge
from csi_bridge import CSIValidationError, api_call, describe_units
from model_read import _try
from modelling import _start_edit
from objects import _EditP

# --------------------------------------------------------------------------- tendon property

class TendonPropP(_EditP):
    name: str = Field(min_length=1, description="tendon property name")
    material: str = Field(description="a tendon material (define_material type tendon)")
    strand_area: float = Field(gt=0, description="area of one strand")
    replace: bool = Field(default=False, description="change an existing property")


@action("define_tendon_property", "Create or change a tendon (strand) property: tendon material and the area of one "
        "strand. Answers with the property read back.", "Definitions", "edit", TendonPropP, programs="EF")
def define_tendon_property(p: TendonPropP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    have = bridge.try_name_list(sap, ["PropTendon"])[0] or []
    if p.name in have and not p.replace:
        raise CSIValidationError(f"Tendon property '{p.name}' exists.", hint="replace: true changes it.")
    mats = bridge.try_name_list(sap, ["PropMaterial"])[0] or []
    if p.material not in mats:
        raise CSIValidationError(f"Material '{p.material}' is not defined.", hint=f"Materials: {mats[:30]}")
    plan = {"name": p.name, "material": p.material, "strand_area": p.strand_area, "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_tendon_property", "define a tendon property")
        api_call("PropTendon.SetProp", sap.PropTendon.SetProp, p.name, p.material, 1, p.strand_area, -1, "", "")
        o = api_call("PropTendon.GetProp", sap.PropTendon.GetProp, p.name, "", 0, 0.0, 0, "", "")
    back = {"material": str(o[0]), "strand_area": float(o[2])}
    if back["material"] != p.material or abs(back["strand_area"] - p.strand_area) > 1e-9 * max(1, p.strand_area):
        raise CSIValidationError(f"Read back differs: {back}")
    return {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- strips

STRIP_KEY = "Strip Object Connectivity"
STRIP_DESIGN_KEY = "Concrete Slab Design Overwrites - Strip Based"


class Strip(Params):
    name: str = Field(min_length=1, description="strip name")
    start: list[float] = Field(min_length=2, max_length=2, description="(x, y) of the strip's start on its centre line")
    end: list[float] = Field(min_length=2, max_length=2, description="(x, y) of the end")
    width_left: float = Field(gt=0, description="width to the left of the centre line (looking start -> end)")
    width_right: float = Field(gt=0, description="width to the right")
    layer: Literal["A", "B", "Other"] = Field(description="design layer: A | B (the two directions) | Other")
    design_type: Literal["column_strip", "middle_strip", "no_design"] | None = Field(default=None, description=(
        "column_strip | middle_strip | no_design (else the program's default)"))


class StripsP(_EditP):
    strips: list[Strip] = Field(min_length=1, max_length=500, description="the strips")
    story: str | None = Field(default=None, description="[[E: story of the slab (required)]]")
    z: float | None = Field(default=None, description="[[F: elevation of the strip points (default: the slabs')]]")
    replace: bool = Field(default=False, description="replace strips of the same names")


def _slab_z(sap: Any) -> float | None:
    for a in (bridge.try_name_list(sap, ["AreaObj"])[0] or [])[:5]:
        o = _try(sap, "AreaObj.GetPoints", a)
        if o and o[1]:
            c = _try(sap, "PointObj.GetCoordCartesian", str(o[1][0]), 0.0, 0.0, 0.0, "Global")
            if c:
                return float(c[2])
    return None


def _story_z(sap: Any, story: str) -> float:
    o = _try(sap, "Story.GetElevation", story)
    if o is None:
        names = bridge.try_name_list(sap, ["Story"])[0] or []
        raise CSIValidationError(f"Story '{story}' is not in the model.", hint=f"Stories: {names}")
    return float(o[0])


def _add_point(sap: Any, x: float, y: float, z: float) -> str:
    o = api_call("PointObj.AddCartesian", sap.PointObj.AddCartesian, x, y, z, "", "", "Global", False, 0)
    return str(o[0])


@action("create_strips", "Create design strips (strip-based slab design): each strip from a start to an end point "
        "with left / right widths, layer A / B, and optionally column / middle strip design type. Answers with the "
        "strips read back.", "Modelling", "edit", StripsP, programs="EF", fields={"story": "E", "z": "F"})
def create_strips(p: StripsP) -> dict[str, Any]:
    import db_edit
    sap = bridge.sap_model(p.software)
    if p.software == "ETABS" and not p.story:
        raise CSIValidationError("ETABS strips need the story.")
    names = [s.name for s in p.strips]
    if len(set(names)) != len(names):
        raise CSIValidationError("Strip names repeat.")
    plan = {"strips": [s.model_dump(exclude_none=True) for s in p.strips], "story": p.story,
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        z = _story_z(sap, p.story) if p.software == "ETABS" else (p.z if p.z is not None else _slab_z(sap))
        if z is None:
            raise CSIValidationError("No slab in the model to take the strip elevation from.", hint="Give z.")
        version, fields, rows = db_edit.read_table(sap, STRIP_KEY)
        clash = [r["Name"] for r in rows if r.get("Name") in names]
        if clash and not p.replace:
            raise CSIValidationError(f"Strips exist: {clash}", hint="replace: true replaces them.")
        snap, was_locked = _start_edit(p, sap, "before_strips", "create strips")
        rows = [r for r in rows if r.get("Name") not in names]
        for s in p.strips:
            a, b = _add_point(sap, *s.start, z), _add_point(sap, *s.end, z)
            w = {"Name": s.name, "Story": p.story or "", "NumSegs": "1", "StartPoint": a, "EndPoint": b,
                 "WStartLeft": f"{s.width_left:.10g}", "WStartRight": f"{s.width_right:.10g}",
                 "WEndLeft": f"{s.width_left:.10g}", "WEndRight": f"{s.width_right:.10g}", "AutoWiden": "No",
                 "Layer": s.layer}
            rows.append({f: w.get(f, "") for f in fields})
        db_edit.write_table(sap, STRIP_KEY, version, fields, rows)
        typed = [s for s in p.strips if s.design_type]
        if typed:
            v2, f2, r2 = db_edit.read_table(sap, STRIP_DESIGN_KEY)
            dt = {"column_strip": "Column Strip", "middle_strip": "Middle Strip", "no_design": "No Design"}
            for r in r2:
                s = next((x for x in typed if x.name == r.get("StripName")), None)
                if s:
                    r["DesignType"] = dt[s.design_type]
                    if s.design_type == "no_design" and "DoDesign" in r:
                        r["DesignType"], r["DoDesign"] = "Column Strip", "No"
            db_edit.write_table(sap, STRIP_DESIGN_KEY, v2, f2, r2)
        _, _, back = db_edit.read_table(sap, STRIP_KEY)
        dback = {r.get("StripName"): r.get("DesignType") for r in db_edit.read_table(sap, STRIP_DESIGN_KEY)[2]}
    got = {r["Name"]: r for r in back if r.get("Name") in names}
    missing = [n for n in names if n not in got]
    if missing:
        raise CSIValidationError(f"Strips not in the table after the write: {missing}")
    out = [{"name": n, "layer": got[n].get("Layer"), "width_left": float(got[n].get("WStartLeft") or 0),
            "width_right": float(got[n].get("WStartRight") or 0), "design_type": dback.get(n)} for n in names]
    return {**plan, "z": z, "read_back": out, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- PT tendons

TENDON_KEY = "Tendon Object Connectivity"
TPROP_KEY = "Tendon Assignments - Properties"
TJACK_KEY = "Tendon Load Assignments - Jacking Stress"
TLOSS_KEY = "Tendon Load Assignments - Loss Options"


class TendonSpan(Params):
    profile: Literal["parabola", "reverse_parabola"] = Field(description=(
        "parabola (one parabola from left to right through the low point) | reverse_parabola (with reverse "
        "curvature over inflection lengths a / b at the ends, as the program's 'Reverse Parabola')"))
    z_left: float = Field(description="tendon centroid at the left end, measured from the slab top (negative = down)")
    z_low: float = Field(description="at the low point (negative = down)")
    z_right: float = Field(description="at the right end (negative = down)")
    low_point: float = Field(gt=0, lt=1, description="low point position as a ratio of the span (0.5 = midspan)")
    a: float = Field(default=0.0, ge=0, lt=0.5, description="reverse parabola: left inflection length / span")
    b: float = Field(default=0.0, ge=0, lt=0.5, description="reverse parabola: right inflection length / span")


class Tendon(Params):
    name: str = Field(min_length=1, description="tendon name")
    start: list[float] = Field(min_length=2, max_length=2, description="(x, y) of the start (plan)")
    end: list[float] = Field(min_length=2, max_length=2, description="(x, y) of the end (one straight plan segment)")
    span: TendonSpan = Field(description="vertical profile over the tendon (one span)")


class TendonsP(_EditP):
    tendons: list[Tendon] = Field(min_length=1, max_length=500, description="the tendons")
    tendon_property: str = Field(description="tendon section property (its material and strand area)")
    strands: int = Field(ge=1, le=200, description="strands per tendon")
    bonded: bool = Field(description="true: bonded | false: unbonded")
    jacking_stress: float = Field(gt=0, description="jacking stress (force / area units)")
    jack_from: Literal["start", "end", "both"] = Field(description="jacking end: start | end | both")
    transfer_pattern: str = Field(description="load pattern for the prestress at transfer")
    final_pattern: str = Field(description="load pattern for the final prestress")
    losses: Literal["fixed", "percent"] = Field(description="loss input: fixed (stress values) | percent")
    loss_at_stressing: float = Field(ge=0, description="stressing loss (stress, or % of the jacking stress)")
    loss_long_term: float = Field(ge=0, description="long-term loss (stress, or %)")
    story: str | None = Field(default=None, description="[[E: story of the slab (required)]]")
    z: float | None = Field(default=None, description="[[F: elevation of the tendon points (default: the slabs')]]")
    replace: bool = Field(default=False, description="replace tendons of the same names")

    @model_validator(mode="after")
    def _chk(self) -> "TendonsP":
        if self.losses == "percent" and (self.loss_at_stressing >= 100 or self.loss_long_term >= 100):
            raise ValueError("percent losses must be below 100")
        return self


@action("create_tendons", "Create post-tensioning tendons: plan from a start to an end point, vertical profile "
        "(parabola or reverse parabola: ends and low point from the slab top, low point position), tendon property, "
        "strands, bonded / unbonded, jacking stress and end, transfer / final load patterns, losses (fixed or "
        "percent). Every value from the designer. Answers with the tendons read back (profile, discretized "
        "points).", "Modelling", "edit", TendonsP, programs="EF", fields={"story": "E", "z": "F"})
def create_tendons(p: TendonsP) -> dict[str, Any]:
    import db_edit
    sap = bridge.sap_model(p.software)
    if p.software == "ETABS" and not p.story:
        raise CSIValidationError("ETABS tendons need the story.")
    props = bridge.try_name_list(sap, ["PropTendon"])[0] or []
    if p.tendon_property not in props:
        raise CSIValidationError(f"Tendon property '{p.tendon_property}' is not defined.", hint=f"Defined: {props}")
    pats = bridge.try_name_list(sap, ["LoadPatterns"])[0] or []
    for pat in (p.transfer_pattern, p.final_pattern):
        if pat not in pats:
            raise CSIValidationError(f"Load pattern '{pat}' is not defined.", hint=f"Patterns: {pats[:30]}")
    names = [t.name for t in p.tendons]
    if len(set(names)) != len(names):
        raise CSIValidationError("Tendon names repeat.")
    plan = {k: v for k, v in p.model_dump(exclude_none=True).items()
            if k not in ("software", "dry_run", "snapshot", "allow_unlock")}
    plan["units"] = describe_units(p.units)
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        z = _story_z(sap, p.story) if p.software == "ETABS" else (p.z if p.z is not None else _slab_z(sap))
        if z is None:
            raise CSIValidationError("No slab in the model to take the tendon elevation from.", hint="Give z.")
        version, fields, rows = db_edit.read_table(sap, TENDON_KEY)
        have = {r.get("Name") for r in rows}
        clash = [n for n in names if n in have]
        if clash and not p.replace:
            raise CSIValidationError(f"Tendons exist: {clash}", hint="replace: true replaces them.")
        snap, was_locked = _start_edit(p, sap, "before_tendons", "create tendons")
        rows = [r for r in rows if r.get("Name") not in names]
        for t in p.tendons:
            a, b = _add_point(sap, *t.start, z), _add_point(sap, *t.end, z)
            L = ((t.end[0] - t.start[0]) ** 2 + (t.end[1] - t.start[1]) ** 2) ** 0.5
            sp = t.span
            first = {"Name": t.name, "Story": p.story or "", "NumPoints": "2", "PointName": a, "HorizSegNum": "1",
                     "HCurveType": "Straight", "NumCP": "2", "CurvePtNum": "1", "GlobalX": f"{t.start[0]:.10g}",
                     "GlobalY": f"{t.start[1]:.10g}", "NumSpans": "1", "DatumOffset": "0", "SpanNum": "1",
                     "SpanProfile": "Reverse Parabola" if sp.profile == "reverse_parabola" else "Parabola",
                     "RD": "1", "A": f"{sp.a * L:.10g}", "B": f"{sp.b * L:.10g}", "C": f"{sp.low_point * L:.10g}",
                     "ZL": f"{sp.z_left:.10g}", "ZC": f"{sp.z_low:.10g}", "ZR": f"{sp.z_right:.10g}",
                     "Layer": "Other"}
            second = {"Name": t.name, "Story": p.story or "", "PointName": b, "HorizSegNum": "1", "CurvePtNum": "2",
                      "GlobalX": f"{t.end[0]:.10g}", "GlobalY": f"{t.end[1]:.10g}"}
            rows.append({f: first.get(f, "") for f in fields})
            rows.append({f: second.get(f, "") for f in fields})
        db_edit.write_table(sap, TENDON_KEY, version, fields, rows)
        st = {"Story": p.story or ""}
        jack = {"start": "I End", "end": "J End", "both": "Both Ends"}[p.jack_from]
        for key, make in (
            (TPROP_KEY, lambda n: {"Name": n, **st, "TendonProp": p.tendon_property, "NumStrands": str(p.strands),
                                   "BondingOpt": "Bonded" if p.bonded else "Unbonded"}),
            (TJACK_KEY, lambda n: {"Name": n, **st, "TranLoadPat": p.transfer_pattern, "FinLoadPat": p.final_pattern,
                                   "JackStress": f"{p.jacking_stress:.10g}", "JackLoc": jack}),
            (TLOSS_KEY, lambda n: {"Name": n, **st, "LossMethod": "Fixed" if p.losses == "fixed" else "Percent",
                                   **({"StrLossFix": f"{p.loss_at_stressing:.10g}",
                                       "LTLossFix": f"{p.loss_long_term:.10g}"} if p.losses == "fixed" else
                                      {"StrLoss": f"{p.loss_at_stressing:.10g}", "LTLoss": f"{p.loss_long_term:.10g}"})})):
            v, f, rr = db_edit.read_table(sap, key)
            rr = [r for r in rr if r.get("Name") not in names]
            rr += [{x: make(n).get(x, "") for x in f} for n in names]
            db_edit.write_table(sap, key, v, f, rr)
        # read back
        tb = [r for r in db_edit.read_table(sap, TENDON_KEY)[2] if r.get("Name") in names]
        pb = {r["Name"]: r for r in db_edit.read_table(sap, TPROP_KEY)[2] if r.get("Name") in names}
        jb = {r["Name"]: r for r in db_edit.read_table(sap, TJACK_KEY)[2] if r.get("Name") in names}
        lb = {r["Name"]: r for r in db_edit.read_table(sap, TLOSS_KEY)[2] if r.get("Name") in names}
        geo = {}
        for n in names:
            g = _try(sap, "TendonObj.GetTendonGeometry", n, 0, [], [], [], "Global")
            if g:
                geo[n] = {"points": int(g[0]), "z_min": min(g[3]) - z if g[3] else None,
                          "z_max": max(g[3]) - z if g[3] else None}
    out, problems = [], []
    for t in p.tendons:
        head = next((r for r in tb if r.get("Name") == t.name and r.get("SpanProfile")), None)
        if not head or t.name not in pb or t.name not in jb:
            problems.append(t.name)
            continue
        out.append({"name": t.name, "profile": head.get("SpanProfile"), "ZL": head.get("ZL"), "ZC": head.get("ZC"),
                    "ZR": head.get("ZR"), "property": pb[t.name].get("TendonProp"),
                    "strands": pb[t.name].get("NumStrands"), "bonding": pb[t.name].get("BondingOpt"),
                    "jacking_stress": jb[t.name].get("JackStress"), "jack_from": jb[t.name].get("JackLoc"),
                    "losses": {k: v for k, v in lb.get(t.name, {}).items() if v not in ("", None) and k != "Name"},
                    "geometry": geo.get(t.name)})
    if problems:
        raise CSIValidationError(f"Tendons not read back: {problems}")
    return {**plan, "z": z, "read_back": out, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- towers (ETABS)

class TowersP(_EditP):
    operation: Literal["list", "allow", "add", "copy", "rename", "delete", "set_active"] = Field(description=(
        "list | allow (multiple towers on) | add (a new tower with its stories) | copy | rename | delete | "
        "set_active"))
    tower: str | None = Field(default=None, description="the tower (add / copy / rename / delete / set_active)")
    new_name: str | None = Field(default=None, description="copy / rename: the new name")
    stories: int | None = Field(default=None, ge=1, le=300, description="add: number of stories")
    typical_story_height: float | None = Field(default=None, gt=0, description="add: typical story height")
    bottom_story_height: float | None = Field(default=None, gt=0, description="add: bottom story height")
    associate_with: str | None = Field(default=None, description="delete: move its objects to this tower")


@action("manage_towers", "ETABS towers: list them, allow multiple towers, add a tower with its stories, copy, rename, "
        "delete (its objects to another tower) or set the active tower. Answers with the towers read back.",
        "Modelling", "edit", TowersP, programs="E")
def manage_towers(p: TowersP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)

    def towers() -> dict[str, Any]:
        o = _try(sap, "Tower.GetNameList")
        act = _try(sap, "Tower.GetActiveTower")
        return {"towers": [str(x) for x in (o[1] if o else [])], "active": str(act[0]) if act else None}
    before = towers()
    if p.operation == "list":
        return before
    need = {"add": ("tower", "stories", "typical_story_height", "bottom_story_height"), "copy": ("tower", "new_name"),
            "rename": ("tower", "new_name"), "delete": ("tower",), "set_active": ("tower",), "allow": ()}[p.operation]
    missing = [f for f in need if getattr(p, f) is None]
    if missing:
        raise CSIValidationError(f"{p.operation} needs {missing}.")
    if p.operation in ("copy", "rename", "delete", "set_active") and p.tower not in before["towers"]:
        raise CSIValidationError(f"Tower '{p.tower}' is not in the model.", hint=f"Towers: {before['towers']}")
    plan = {"operation": p.operation, **{f: getattr(p, f) for f in need}, "before": before}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_towers", "change towers")
        t = sap.Tower
        if p.operation == "allow":
            api_call("Tower.AllowMultipleTowers", t.AllowMultipleTowers, True, "", False)
        elif p.operation == "add":
            api_call("Tower.AddNewTower", t.AddNewTower, p.tower, p.stories, p.typical_story_height,
                     p.bottom_story_height)
        elif p.operation == "copy":
            api_call("Tower.AddCopyOfTower", t.AddCopyOfTower, p.tower, p.new_name)
        elif p.operation == "rename":
            api_call("Tower.RenameTower", t.RenameTower, p.tower, p.new_name)
        elif p.operation == "delete":
            api_call("Tower.DeleteTower", t.DeleteTower, p.tower, bool(p.associate_with), p.associate_with or "")
        else:
            api_call("Tower.SetActiveTower", t.SetActiveTower, p.tower)
        after = towers()
    return {**plan, "read_back": after, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
