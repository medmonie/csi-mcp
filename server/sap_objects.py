"""Phase 5, SAP2000 objects (0.19.0): cables (with their cable property), solids (with their solid property) and
tendon objects in 3D (the tendon property comes from define_tendon_property).

All through typed API calls (measured signatures, SAP2000 27.1): PropCable.SetProp, CableObj.AddByPoint /
SetCableData / GetCableData, PropSolid.SetProp, SolidObj.AddByPoint, TendonObj.AddByPoint / SetTendonData /
SetLoadForceStress. Every object is read back; the cable shape is read back from the program's 'Cable Shape Data'
table as well, so the answer states the shape the program computed.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import Params, _dry, _refresh_view, action, bridge
from csi_bridge import CSIValidationError, api_call, as_list, describe_units
from model_read import _try
from modelling import _start_edit
from objects import _EditP

MAX = 2000


def _point(sap: Any, v: str | list[float]) -> str:
    """A point name (checked) or [x, y, z] (an existing point at the place is reused, as in the program)."""
    if isinstance(v, str):
        return v
    o = api_call("PointObj.AddCartesian", sap.PointObj.AddCartesian, float(v[0]), float(v[1]), float(v[2]), "", "",
                 "Global", False, 0)
    return str(o[0])


def _check_points(sap: Any, ends: list[str | list[float]]) -> None:
    named = sorted({e for e in ends if isinstance(e, str)})
    if named:
        have = set(bridge.try_name_list(sap, ["PointObj"])[0] or [])
        missing = [n for n in named if n not in have]
        if missing:
            raise CSIValidationError(f"Points not in the model: {missing[:20]}.")
    for e in ends:
        if isinstance(e, list) and len(e) != 3:
            raise CSIValidationError("A coordinate point is [x, y, z].")


def _new_names(sap: Any, coll: str, names: list[str], replace: bool = False) -> None:
    have = set(bridge.try_name_list(sap, [coll])[0] or [])
    clash = sorted({n for n in names if n} & have)
    if clash and not replace:
        raise CSIValidationError(f"Names already used: {clash[:20]}.", hint="Give other names, or leave them empty.")


def _need(sap: Any, coll: str, name: str, what: str, hint: str) -> None:
    have = bridge.try_name_list(sap, [coll])[0] or []
    if name not in have:
        raise CSIValidationError(f"{what} '{name}' is not defined.", hint=f"{hint} Defined: {have[:30]}")


# --------------------------------------------------------------------------- cable property

class CablePropP(_EditP):
    name: str = Field(min_length=1, description="cable property name")
    material: str = Field(description="an existing material (e.g. a tendon or steel material)")
    area: float = Field(gt=0, description="cross-section area")
    replace: bool = Field(default=False, description="change an existing property")


@action("define_cable_property", "Create or change a cable section: material and area. Answers with the property read "
        "back.", "Definitions", "edit", CablePropP, programs="S")
def define_cable_property(p: CablePropP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    _new_names(sap, "PropCable", [p.name], p.replace)
    _need(sap, "PropMaterial", p.material, "Material", "Define it with define_material.")
    plan = {"name": p.name, "material": p.material, "area": p.area, "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_cable_property", "define a cable property")
        api_call("PropCable.SetProp", sap.PropCable.SetProp, p.name, p.material, p.area, -1, "", "")
        o = api_call("PropCable.GetProp", sap.PropCable.GetProp, p.name, "", 0.0, 0, "", "")
    back = {"material": str(o[0]), "area": float(o[1])}
    if back["material"] != p.material or abs(back["area"] - p.area) > 1e-9 * p.area:
        raise CSIValidationError(f"Read back differs: {back}")
    return {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- cables

# CableType codes and the slot of GetCableData's Parameter() that holds the defining value (SAP2000 API: Parameter has
# 9 slots - tension I, tension J, horizontal tension, maximum sag, low-point sag, deformed length, deformed relative
# length, undeformed length, undeformed relative length). SetCableData takes the one Value (a number - measured).
CABLE_TYPES = {"min_tension_i": (1, 0), "min_tension_j": (2, 1), "tension_i": (3, 0), "tension_j": (4, 1),
               "horizontal_tension": (5, 2), "max_sag": (6, 3), "low_point_sag": (7, 4),
               "undeformed_length": (8, 7), "relative_undeformed_length": (9, 8)}
PARAMS = ("tension_i", "tension_j", "horizontal_tension", "max_sag", "low_point_sag", "deformed_length",
          "deformed_relative_length", "undeformed_length", "relative_undeformed_length")


class NewCable(Params):
    start: str | list[float] = Field(description="I end: a point name or [x, y, z]")
    end: str | list[float] = Field(description="J end: a point name or [x, y, z]")
    name: str = Field(default="", description="name; default: the program numbers it")


class CablesP(_EditP):
    cables: list[NewCable] = Field(min_length=1, max_length=MAX, description="cables to add")
    cable_property: str = Field(description="an existing cable property (define_cable_property)")
    shape: Literal[tuple(CABLE_TYPES)] = Field(description=(  # type: ignore[valid-type]
        "how the designer defines the cable's shape under its own weight plus added / projected load: "
        "min_tension_i | min_tension_j | tension_i | tension_j | horizontal_tension (forces) | max_sag | "
        "low_point_sag (vertical sag, length) | undeformed_length (length) | relative_undeformed_length (ratio to the "
        "chord)"))
    value: float = Field(gt=0, description="the value of that parameter (present units)")
    segments: int = Field(default=1, ge=1, le=10000, description="number of analysis segments per cable")
    added_weight: float = Field(default=0.0, ge=0, description="added weight per length (shape calculation)")
    projected_load: float = Field(default=0.0, ge=0, description="projected uniform gravity load per horizontal "
                                  "length (shape calculation)")
    model_as_frames: bool = Field(default=False, description="model the cable with frame elements (default: cable "
                                  "elements)")


@action("create_cables", "Add cables between points with a cable property and the designer's shape parameter "
        "(tension, sag or length) and segments. Answers with each cable's data and the shape the program computed "
        "(tensions, sags, lengths) read back. Cables are nonlinear: use a nonlinear static case with "
        "p_delta_large_displacements for their results.", "Objects", "edit", CablesP, programs="S")
def create_cables(p: CablesP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    _need(sap, "PropCable", p.cable_property, "Cable property", "Define it with define_cable_property.")
    _check_points(sap, [e for c in p.cables for e in (c.start, c.end)])
    _new_names(sap, "CableObj", [c.name for c in p.cables])
    code, slot = CABLE_TYPES[p.shape]
    plan = {"cables": len(p.cables), "cable_property": p.cable_property, "shape": p.shape, "value": p.value,
            "segments": p.segments, "added_weight": p.added_weight, "projected_load": p.projected_load,
            "model_as_frames": p.model_as_frames, "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    out = []
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_cables", "add cables")
        for c in p.cables:
            i, j = _point(sap, c.start), _point(sap, c.end)
            o = api_call("CableObj.AddByPoint", sap.CableObj.AddByPoint, i, j, "", p.cable_property, c.name)
            name = str(o[0])
            api_call("CableObj.SetCableData", sap.CableObj.SetCableData, name, code, p.segments, p.added_weight,
                     p.projected_load, p.value, False, p.model_as_frames)
            g = _try(sap, "CableObj.GetCableData", name, 0, 0, 0.0, 0.0, False, False, [])
            rec: dict[str, Any] = {"name": name, "points": [i, j]}
            if g:
                par = [float(v) for v in as_list(g[6])]
                rec.update(cable_type_code=int(g[0]), segments=int(g[1]),
                           program_shape={k: round(par[i], 6) for i, k in enumerate(PARAMS) if i < len(par)})
            out.append(rec)
        _refresh_view(sap)
    key = "relative_undeformed_length" if p.shape == "relative_undeformed_length" else \
        {"min_tension_i": "tension_i", "min_tension_j": "tension_j"}.get(p.shape, p.shape)
    bad = [{r["name"]: r["program_shape"].get(key)} for r in out if "program_shape" in r
           and abs(float(r["program_shape"].get(key, 0.0)) - p.value) > 1e-4 * max(1.0, abs(p.value))
           and not p.shape.startswith("min_")]
    res = {**plan, "created": out, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if bad:
        res["warning"] = f"The program's {key} differs from the value given: {bad[:10]}"
    return res


# --------------------------------------------------------------------------- solids

class SolidPropP(_EditP):
    name: str = Field(min_length=1, description="solid property name")
    material: str = Field(description="an existing material")
    replace: bool = Field(default=False, description="change an existing property")


@action("define_solid_property", "Create or change a solid property with a material (other options the program's "
        "defaults). Answers with the property read back.", "Definitions", "edit", SolidPropP, programs="S")
def define_solid_property(p: SolidPropP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    _new_names(sap, "PropSolid", [p.name], p.replace)
    _need(sap, "PropMaterial", p.material, "Material", "Define it with define_material.")
    plan = {"name": p.name, "material": p.material}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_solid_property", "define a solid property")
    api_call("PropSolid.SetProp", sap.PropSolid.SetProp, p.name, p.material, 0.0, 0.0, 0.0, True, -1, "", "")
    o = api_call("PropSolid.GetProp", sap.PropSolid.GetProp, p.name, "", 0.0, 0.0, 0.0, False, 0, "", "")
    back = {"material": str(o[0]), "material_angles": [float(o[1]), float(o[2]), float(o[3])]}
    if back["material"] != p.material:
        raise CSIValidationError(f"Read back differs: {back}")
    return {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


class NewSolid(Params):
    points: list[str] | list[list[float]] = Field(min_length=8, max_length=8, description=(
        "8 corners, point names or [x, y, z], in the program's order: bottom face 1 2 3 4 then top face 5 6 7 8, "
        "where 1-2 runs along one edge, 1-3 along the other (3 is opposite 2, NOT around the face: 1 (0,0,0), "
        "2 (1,0,0), 3 (0,1,0), 4 (1,1,0)), and 5-8 lie above 1-4"))
    name: str = Field(default="", description="name; default: the program numbers it")


def _volume(sap: Any, pts: list[str]) -> float:
    """Signed volume of the 8-node solid in the program's corner order (1 2 3 4 bottom, 1-2 and 1-3 along the edges;
    5-8 above): six tetrahedra of the hexahedron 1 2 4 3 / 5 6 8 7."""
    c = []
    for n in pts:
        o = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, n, 0.0, 0.0, 0.0)
        c.append([float(o[0]), float(o[1]), float(o[2])])
    h = [c[0], c[1], c[3], c[2], c[4], c[5], c[7], c[6]]           # ordered around the faces

    def tet(a, b, d, e):
        u = [b[k] - a[k] for k in range(3)]
        v = [d[k] - a[k] for k in range(3)]
        w = [e[k] - a[k] for k in range(3)]
        return (u[0] * (v[1] * w[2] - v[2] * w[1]) - u[1] * (v[0] * w[2] - v[2] * w[0])
                + u[2] * (v[0] * w[1] - v[1] * w[0])) / 6.0
    return sum(tet(h[a], h[b], h[d], h[e]) for a, b, d, e in
               ((0, 1, 2, 6), (0, 1, 6, 5), (0, 5, 6, 4), (0, 2, 3, 6), (0, 3, 7, 6), (0, 4, 6, 7)))


class SolidsP(_EditP):
    solids: list[NewSolid] = Field(min_length=1, max_length=MAX, description="solids to add")
    solid_property: str = Field(description="an existing solid property (define_solid_property)")


@action("create_solids", "Add 8-node solids by corner points (names or coordinates) with a solid property. Answers "
        "with each solid's points and volume read back (a zero or negative volume means the corner order is "
        "wrong).", "Objects", "edit", SolidsP, programs="S")
def create_solids(p: SolidsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    _need(sap, "PropSolid", p.solid_property, "Solid property", "Define it with define_solid_property.")
    _check_points(sap, [pt for s in p.solids for pt in s.points])
    _new_names(sap, "SolidObj", [s.name for s in p.solids])
    plan = {"solids": len(p.solids), "solid_property": p.solid_property, "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    out = []
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_solids", "add solids")
        for s in p.solids:
            pts = [_point(sap, v) for v in s.points]
            o = api_call("SolidObj.AddByPoint", sap.SolidObj.AddByPoint, pts, "", p.solid_property, s.name)
            name = str(o[-1] if isinstance(o, (list, tuple)) and len(o) > 1 and not isinstance(o[-1], list)
                       else o[0])
            back = _try(sap, "SolidObj.GetPoints", name, [])
            out.append({"name": name, "points": [str(v) for v in as_list(back[0])] if back else pts})
            out[-1]["volume"] = round(_volume(sap, out[-1]["points"]), 9)
        _refresh_view(sap)
    bad = [r["name"] for r in out if r["volume"] <= 0]
    res = {**plan, "created": out, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if bad:
        res["warning"] = f"Solids with zero or negative volume (corner order): {bad[:20]}"
    return res


# --------------------------------------------------------------------------- tendon objects (SAP2000)

JACK = {"start": 1, "end": 2, "both": 3}


class TendonPath(Params):
    points: list[list[float]] = Field(min_length=2, max_length=200, description=(
        "the tendon's path, [x, y, z] in global coordinates, from the I end to the J end; straight segments "
        "between the points (give more points for a curved profile)"))
    name: str = Field(default="", description="name; default: the program numbers it")


class TendonObjP(_EditP):
    tendons: list[TendonPath] = Field(min_length=1, max_length=500, description="tendons to add")
    tendon_property: str = Field(description="an existing tendon property (define_tendon_property)")
    load_pattern: str = Field(description="the load pattern carrying the prestress")
    force_or_stress: Literal["force", "stress"] = Field(description="jacking value given as force or as stress")
    jacking: float = Field(gt=0, description="jacking force or stress (present units)")
    jack_from: Literal["start", "end", "both"] = Field(description="start (I end) | end (J end) | both")
    curvature_coefficient: float = Field(ge=0, description="friction: curvature coefficient (per radian)")
    wobble_coefficient: float = Field(ge=0, description="friction: wobble coefficient (per length)")
    anchorage_set_slip: float = Field(ge=0, description="anchorage set slip (length)")
    loss_elastic_shortening: float = Field(ge=0, description="elastic shortening loss (stress)")
    loss_creep: float = Field(ge=0, description="creep loss (stress)")
    loss_shrinkage: float = Field(ge=0, description="shrinkage loss (stress)")
    loss_relaxation: float = Field(ge=0, description="steel relaxation loss (stress)")
    discretization: float | None = Field(default=None, gt=0, description="maximum discretization length (default: "
                                         "the program's)")

    @model_validator(mode="after")
    def _check(self) -> "TendonObjP":
        for t in self.tendons:
            if any(len(v) != 3 for v in t.points):
                raise ValueError("tendon points are [x, y, z]")
        return self


@action("create_tendon_objects", "Add tendons along 3D paths (straight segments between the points) with a tendon "
        "property, and their prestress: load pattern, jacking force or stress, jacking end, friction (curvature, "
        "wobble), anchorage set and losses - all given by the designer. As loads the tendon acts on the objects it "
        "passes through. Answers with each tendon's path and prestress read back.", "Objects", "edit", TendonObjP,
        programs="S")
def create_tendon_objects(p: TendonObjP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    _need(sap, "PropTendon", p.tendon_property, "Tendon property", "Define it with define_tendon_property.")
    _need(sap, "LoadPatterns", p.load_pattern, "Load pattern", "Define it with define_load_pattern.")
    _new_names(sap, "TendonObj", [t.name for t in p.tendons])
    plan = {k: getattr(p, k) for k in ("tendon_property", "load_pattern", "force_or_stress", "jacking", "jack_from",
                                       "curvature_coefficient", "wobble_coefficient", "anchorage_set_slip",
                                       "loss_elastic_shortening", "loss_creep", "loss_shrinkage", "loss_relaxation",
                                       "discretization")}
    plan.update(tendons=len(p.tendons), units=describe_units(p.units))
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    out = []
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_tendons", "add tendons")
        for t in p.tendons:
            a, b = t.points[0], t.points[-1]
            o = api_call("TendonObj.AddByCoord", sap.TendonObj.AddByCoord, *a, *b, "", p.tendon_property, t.name,
                         "Global")
            name = str(o[0])
            n = len(t.points)
            types = [1] + [2] * (n - 1)                      # 1 = start of tendon, 2 = linear (SAP2000 API)
            xs, ys, zs = ([float(v[k]) for v in t.points] for k in range(3))
            api_call("TendonObj.SetTendonData", sap.TendonObj.SetTendonData, name, n, types, xs, ys, zs, "Global")
            if p.discretization:
                api_call("TendonObj.SetDiscretization", sap.TendonObj.SetDiscretization, name, p.discretization, 0)
            api_call("TendonObj.SetLoadForceStress", sap.TendonObj.SetLoadForceStress, name, p.load_pattern,
                     JACK[p.jack_from], 0 if p.force_or_stress == "force" else 1, p.jacking,
                     p.curvature_coefficient, p.wobble_coefficient, p.anchorage_set_slip, p.loss_elastic_shortening,
                     p.loss_creep, p.loss_shrinkage, p.loss_relaxation, True, 0)
            rec: dict[str, Any] = {"name": name}
            g = _try(sap, "TendonObj.GetTendonData", name, 0, [], [], [], [], "Global")
            if g:
                rec["path"] = [[round(float(x), 6), round(float(y), 6), round(float(z), 6)]
                               for x, y, z in zip(as_list(g[2]), as_list(g[3]), as_list(g[4]))]
                rec["segment_types"] = [int(v) for v in as_list(g[1])]
            ld = _try(sap, "TendonObj.GetLoadForceStress", name, 0, [], [], [], [], [], [], [], [], [], [], [], [], 0)
            if ld:
                rec["prestress"] = {"load_pattern": as_list(ld[2])[:1], "jack_from": as_list(ld[3])[:1],
                                    "type": as_list(ld[4])[:1], "value": as_list(ld[5])[:1]}
            out.append(rec)
        _refresh_view(sap)
    return {**plan, "created": out, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
