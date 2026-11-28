"""Phase 8.5 (0.23.0): layered shells, ETABS / SAFE deck sections, material stress-strain curves and time-dependent
concrete properties.

Measured on ETABS 23.2, SAP2000 27.1 and SAFE 23.3 (tests/diag_85.py, diag_85b.py, 28 Nov 2026):
- Layered shell = shell type 6 in all three programs (SAP2000 SetShell_1 type 6; ETABS / SAFE SetSlab / SetWall type 6;
  ETABS / SAFE refuse slab shell types 4 and 5), then PropArea.SetShellLayer_1, read back exactly by GetShellLayer_1.
  SetShellLayer_1 on a property that is not type 6 returns 1. The type-6 property reads material '' / None and
  thickness 0: the layers carry them.
- Deck (ETABS, SAFE): PropArea.SetDeck_1 sets every field and GetDeck_1 reads them back; the deck is a membrane
  (GetDeck shell type 3). GetDeckSolidSlab returns the stud Fu a million times too large (400000 -> 4e11 in kN, m):
  the read-back uses GetDeck_1.
- Stress-strain: SSType 0 = user (SetSSCurve / GetSSCurve), 1 = simple parametric, 2 = Mander (concrete) / Park
  (rebar); the program draws parametric curves itself (GetSSCurve), except Mander, which GetSSCurve does not return
  (ret 1, with or without a section name). SAP2000 refuses hysteresis 'Concrete' (4) for a concrete material (ETABS /
  SAFE take it). Simple concrete: SAP2000 is the parabola fc [2 e/e0 - (e/e0)^2] from the origin; ETABS / SAFE start
  linear (E e) to 0.2 fc, then the same parabola.
- Time-dependent concrete: typed only in SAP2000 (PropMaterial.TimeDep: CEB-FIP 90 and scale factors). ETABS 23.2 has
  the TimeDep interface without methods, and its 'Material Properties - Time Dependence - CEB-FIP 90' table imports
  with no error and keeps nothing; SAFE 23.3 cannot open these tables for editing (-96). Refused there with that
  reason.
All values (layers, materials, deck geometry, curve points, creep / shrinkage parameters) come from the designer (D1).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import Params, _dry, _refresh_view, action, bridge
from csi_bridge import CSIValidationError, api_call, as_list, describe_units
from model_read import _try
from modelling import _start_edit
from objects import _EditP

LAYER_TYPE = {"shell": 1, "membrane": 2, "plate": 3}
COMPONENT = {"inactive": 0, "linear": 1, "nonlinear": 2}
SLAB_TYPES = {"slab": 0, "drop": 1, "stiff": 2, "mat": 5, "footing": 6}
LAYERED = 6
DECK_TYPES = {"filled": 1, "unfilled": 2, "solid_slab": 3}
HYSTERESIS = {"elastic": 0, "kinematic": 1, "takeda": 2, "pivot": 3, "concrete": 4, "brb_hardening": 5,
              "degrading": 6, "isotropic": 7}
MAT_KIND = {1: "steel", 2: "concrete", 6: "rebar", 7: "tendon"}
SS_MODELS = {"concrete": {"simple": 1, "mander": 2}, "steel": {"simple": 1}, "rebar": {"simple": 1, "park": 2},
             "tendon": {"strand_250": 1, "strand_270": 2}}
TOL = 1e-9


def _f(v: Any) -> float:
    return float(f"{float(v):.10g}")


def _inv(d: dict[str, int], v: Any) -> Any:
    try:
        return {b: a for a, b in d.items()}.get(int(v), v)
    except (TypeError, ValueError):
        return v


def _close(a: float, b: float) -> bool:
    return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(a)), abs(float(b)))


def _names(sap: Any, coll: str) -> list[str]:
    return bridge.try_name_list(sap, [coll])[0] or []


# --------------------------------------------------------------------------- layered shells

def read_shell_layers(sap: Any, name: str) -> list[dict[str, Any]] | None:
    g = _try(sap, "PropArea.GetShellLayer_1", name)
    if not g or int(g[0]) < 1:
        return None
    n = int(g[0])
    names, dist, thick, typ, nint, mats, ang, s11, s22, s12 = (as_list(g[i]) for i in range(1, 11))
    return [{"name": str(names[i]), "distance": _f(dist[i]), "thickness": _f(thick[i]),
             "behaviour": _inv(LAYER_TYPE, typ[i]), "material": str(mats[i]), "angle": _f(ang[i]),
             "integration_points": int(nint[i]), "s11": _inv(COMPONENT, s11[i]), "s22": _inv(COMPONENT, s22[i]),
             "s12": _inv(COMPONENT, s12[i])} for i in range(n)]


class Layer(Params):
    name: str = Field(min_length=1, max_length=40, description="layer name (unique in the section)")
    distance: float = Field(description=(
        "distance from the section's reference surface to the middle of the layer, along local axis 3 (+ = toward "
        "the positive 3 face), length units"))
    thickness: float = Field(gt=0, description="layer thickness, length units")
    behaviour: Literal["shell", "membrane", "plate"] = Field(description=(
        "shell (membrane and plate bending) | membrane (in-plane only) | plate (bending only)"))
    material: str = Field(description="an existing material")
    angle: float = Field(default=0.0, ge=-360, le=360, description="material angle in the layer, degrees")
    integration_points: int = Field(ge=1, le=5, description=(
        "integration points through the layer thickness (1 to 5) - from the designer"))
    s11: Literal["inactive", "linear", "nonlinear"] = Field(description="S11 component behaviour")
    s22: Literal["inactive", "linear", "nonlinear"] = Field(description="S22 component behaviour")
    s12: Literal["inactive", "linear", "nonlinear"] = Field(description="S12 component behaviour")


class LayeredShellP(_EditP):
    name: str = Field(min_length=1, max_length=80, description="the layered area section")
    use: Literal["slab", "wall"] = Field(default="slab", description=(
        "[[EF: slab (default) | wall: the kind of area property that carries the layers]][[S: not used in SAP2000 "
        "(a layered shell property)]]"))
    slab_type: Literal["slab", "drop", "stiff", "mat", "footing"] = Field(default="slab", description=(
        "slab: slab (default) | drop | stiff | mat | footing"))
    layers: list[Layer] = Field(min_length=1, max_length=100, description="the layers, from the designer")
    replace: bool = Field(default=False, description="change an existing section")

    @model_validator(mode="after")
    def _check(self) -> "LayeredShellP":
        names = [x.name for x in self.layers]
        if len(set(names)) != len(names):
            raise ValueError("layer names must be unique")
        return self


@action("define_layered_shell", "Create or change a layered shell section: layers with distance from the reference "
        "surface, thickness, behaviour (shell / membrane / plate), material and angle, integration points and S11 / "
        "S22 / S12 behaviour (inactive / linear / nonlinear) - values from the designer. Answers with the layers read "
        "back.", "Definitions", "edit", LayeredShellP, fields={"use": "EF", "slab_type": "EF"})
def define_layered_shell(p: LayeredShellP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    secs = _names(sap, "PropArea")
    if p.name in secs and not p.replace:
        raise CSIValidationError(f"Area section '{p.name}' exists.", hint="replace: true changes it.")
    mats = set(_names(sap, "PropMaterial"))
    missing = sorted({x.material for x in p.layers} - mats)
    if missing:
        raise CSIValidationError(f"Materials not defined: {missing}.", hint=f"Defined: {sorted(mats)[:40]}")
    top = max(x.distance + x.thickness / 2 for x in p.layers)
    bottom = min(x.distance - x.thickness / 2 for x in p.layers)
    plan = {"section": p.name, "layers": [x.model_dump() for x in p.layers], "existed": p.name in secs,
            "extent": {"top": _f(top), "bottom": _f(bottom), "depth": _f(top - bottom),
                       "sum_of_layer_thicknesses": _f(sum(x.thickness for x in p.layers))},
            "units": describe_units(p.units)}
    if p.software != "SAP2000":
        plan["use"] = p.use
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    L = p.layers
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_layered_shell", "define a layered shell")
        pa, mat0, t = sap.PropArea, L[0].material, sum(x.thickness for x in L)
        if p.software == "SAP2000":
            api_call("PropArea.SetShell_1", pa.SetShell_1, p.name, LAYERED, False, mat0, 0.0, t, t)
        elif p.use == "slab":
            api_call("PropArea.SetSlab", pa.SetSlab, p.name, SLAB_TYPES[p.slab_type], LAYERED, mat0, t)
        else:
            api_call("PropArea.SetWall", pa.SetWall, p.name, 1, LAYERED, mat0, t)
        api_call("PropArea.SetShellLayer_1", pa.SetShellLayer_1, p.name, len(L), [x.name for x in L],
                 [x.distance for x in L], [x.thickness for x in L], [LAYER_TYPE[x.behaviour] for x in L],
                 [x.integration_points for x in L], [x.material for x in L], [x.angle for x in L],
                 [COMPONENT[x.s11] for x in L], [COMPONENT[x.s22] for x in L], [COMPONENT[x.s12] for x in L])
        _refresh_view(sap)
        back = read_shell_layers(sap, p.name)
    if not back or len(back) != len(L):
        raise CSIValidationError(f"The program did not keep the layers of '{p.name}'.", hint=f"Read back: {back}")
    diff = []
    for x, b in zip(L, back):
        for k, v in x.model_dump().items():
            same = _close(b[k], v) if isinstance(v, float) else str(b[k]) == str(v)
            if not same:
                diff.append({"layer": x.name, k: [v, b[k]]})
    out = {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out


# --------------------------------------------------------------------------- deck sections (ETABS, SAFE)

DECK_NEEDS = {
    "filled": ("slab_material", "deck_material", "slab_depth", "rib_depth", "rib_width_top", "rib_width_bottom",
               "rib_spacing", "deck_shear_thickness", "deck_unit_weight", "stud_diameter", "stud_height", "stud_fu"),
    "unfilled": ("deck_material", "rib_depth", "rib_width_top", "rib_width_bottom", "rib_spacing",
                 "deck_shear_thickness", "deck_unit_weight"),
    "solid_slab": ("slab_material", "slab_depth", "stud_diameter", "stud_height", "stud_fu")}
DECK_FIELDS = ("slab_material", "deck_material", "slab_depth", "rib_depth", "rib_width_top", "rib_width_bottom",
               "rib_spacing", "deck_shear_thickness", "deck_unit_weight", "stud_diameter", "stud_height", "stud_fu")


def read_deck(sap: Any, name: str) -> dict[str, Any] | None:
    g = _try(sap, "PropArea.GetDeck_1", name)
    if not g:
        return None
    rec: dict[str, Any] = {"deck_type": _inv(DECK_TYPES, g[0]), "slab_material": str(g[1] or ""),
                           "deck_material": str(g[2] or "")}
    rec.update({k: _f(v) for k, v in zip(DECK_FIELDS[2:], g[3:13])})
    return rec


class DeckP(_EditP):
    name: str = Field(min_length=1, max_length=80, description="the deck section")
    deck_type: Literal["filled", "unfilled", "solid_slab"] = Field(description=(
        "filled (concrete on metal deck) | unfilled (metal deck alone) | solid_slab (solid slab with studs)"))
    slab_material: str | None = Field(default=None, description="filled / solid_slab: concrete material")
    deck_material: str | None = Field(default=None, description="filled / unfilled: steel deck material")
    slab_depth: float | None = Field(default=None, gt=0, description=(
        "filled: concrete depth above the ribs (tc) | solid_slab: slab depth; length units"))
    rib_depth: float | None = Field(default=None, gt=0, description="filled / unfilled: rib depth (hr)")
    rib_width_top: float | None = Field(default=None, gt=0, description="filled / unfilled: rib width at the top (wrt)")
    rib_width_bottom: float | None = Field(default=None, gt=0, description=(
        "filled / unfilled: rib width at the bottom (wrb)"))
    rib_spacing: float | None = Field(default=None, gt=0, description="filled / unfilled: rib spacing (sr)")
    deck_shear_thickness: float | None = Field(default=None, gt=0, description=(
        "filled / unfilled: deck plate thickness for shear"))
    deck_unit_weight: float | None = Field(default=None, ge=0, description=(
        "filled / unfilled: deck weight per unit area (force / length^2)"))
    stud_diameter: float | None = Field(default=None, gt=0, description="filled / solid_slab: shear stud diameter")
    stud_height: float | None = Field(default=None, gt=0, description="filled / solid_slab: shear stud height (hs)")
    stud_fu: float | None = Field(default=None, gt=0, description=(
        "filled / solid_slab: stud tensile strength Fu (force / length^2)"))
    replace: bool = Field(default=False, description="change an existing section")

    @model_validator(mode="after")
    def _check(self) -> "DeckP":
        need = DECK_NEEDS[self.deck_type]
        missing = [f for f in need if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.deck_type} deck needs {', '.join(missing)} (values from the designer)")
        extra = [f for f in DECK_FIELDS if f not in need and getattr(self, f) is not None]
        if extra:
            raise ValueError(f"{self.deck_type} deck does not use {', '.join(extra)}")
        return self


@action("define_deck_section", "Create or change a deck section: filled (concrete on metal deck), unfilled or solid "
        "slab with studs - slab / deck materials, slab depth, rib depth / widths / spacing, deck shear thickness and "
        "unit weight, stud diameter / height / Fu, from the designer. The program models a deck as a membrane. "
        "Answers with the deck read back.", "Definitions", "edit", DeckP, programs="EF")
def define_deck_section(p: DeckP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    secs = _names(sap, "PropArea")
    if p.name in secs and not p.replace:
        raise CSIValidationError(f"Area section '{p.name}' exists.", hint="replace: true changes it.")
    mats = set(_names(sap, "PropMaterial"))
    missing = sorted(m for m in (p.slab_material, p.deck_material) if m and m not in mats)
    if missing:
        raise CSIValidationError(f"Materials not defined: {missing}.", hint=f"Defined: {sorted(mats)[:40]}")
    given = {f: getattr(p, f) for f in DECK_NEEDS[p.deck_type]}
    plan = {"section": p.name, "deck_type": p.deck_type, **given, "existed": p.name in secs,
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    slab_mat = p.slab_material or p.deck_material or ""
    deck_mat = p.deck_material or p.slab_material or ""
    nums = [getattr(p, f) or 0.0 for f in DECK_FIELDS[2:]]
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_deck", "define a deck section")
        api_call("PropArea.SetDeck_1", sap.PropArea.SetDeck_1, p.name, DECK_TYPES[p.deck_type], slab_mat, deck_mat,
                 *nums)
        _refresh_view(sap)
        back = read_deck(sap, p.name)
    if not back:
        raise CSIValidationError(f"The program did not keep the deck '{p.name}'.")
    diff = {k: [v, back.get(k)] for k, v in given.items()
            if not (_close(back.get(k, 0), v) if isinstance(v, float) else str(back.get(k)) == str(v))}
    if back.get("deck_type") != p.deck_type:
        diff["deck_type"] = [p.deck_type, back.get("deck_type")]
    out = {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out


# --------------------------------------------------------------------------- stress-strain curves

def _mat_kind(sap: Any, name: str) -> str:
    g = _try(sap, "PropMaterial.GetMaterial", name)
    kind = MAT_KIND.get(int(g[0])) if g else None
    if not kind:
        raise CSIValidationError(f"Material '{name}' is not concrete, steel, rebar or tendon.",
                                 hint="Stress-strain settings here cover those four material types.")
    return kind


GETSET = {"concrete": ("GetOConcrete_1", "SetOConcrete_1"), "steel": ("GetOSteel_1", "SetOSteel_1"),
          "rebar": ("GetORebar_1", "SetORebar_1"), "tendon": ("GetOTendon_1", "SetOTendon_1")}
# position of SSType / SSHysType and of the curve parameters in each getter's outputs (return code removed)
SS_AT = {"concrete": (3, 4), "steel": (4, 5), "rebar": (4, 5), "tendon": (2, 3)}
PARAM_AT = {"concrete": {"strain_at_fc": 5, "ultimate_strain": 6, "final_slope": 7, "friction_angle": 8,
                         "dilatation_angle": 9},
            "steel": {"strain_at_hardening": 6, "strain_at_max_stress": 7, "strain_at_rupture": 8, "final_slope": 9},
            "rebar": {"strain_at_hardening": 6, "ultimate_strain": 7, "final_slope": 8},
            "tendon": {"final_slope": 4}}
NPARAM = {"concrete": 10, "steel": 10, "rebar": 10, "tendon": 5}


def read_stress_strain(sap: Any, name: str, kind: str | None = None) -> dict[str, Any]:
    kind = kind or MAT_KIND.get(int((_try(sap, "PropMaterial.GetMaterial", name) or [0])[0]))
    if not kind:
        return {}
    getter = GETSET[kind][0]
    g = _try(sap, f"PropMaterial.{getter}", name)
    rec: dict[str, Any] = {}
    if g:
        ss, hy = SS_AT[kind]
        code = int(g[ss])
        rec["curve"] = "user" if code == 0 else _inv(SS_MODELS[kind], code)
        rec["hysteresis"] = _inv(HYSTERESIS, g[hy])
        if code != 0:
            rec["parameters"] = {k: (bool(g[i]) if isinstance(g[i], bool) else _f(g[i]))
                                 for k, i in PARAM_AT[kind].items()}
    c = _try(sap, "PropMaterial.GetSSCurve", name)
    if c and int(c[0]) > 0:
        rec["points"] = [{"strain": _f(e), "stress": _f(s), "point_id": int(i)}
                         for i, e, s in zip(as_list(c[1]), as_list(c[2]), as_list(c[3]))]
    else:
        rec["points"] = None
        rec["points_note"] = ("The program does not return this curve through its API (measured for Mander "
                              "concrete); it draws it in its material dialog.")
    return rec


class SSPoint(Params):
    strain: float = Field(description="strain (negative = compression)")
    stress: float = Field(description="stress (force / length^2; negative = compression)")
    point_id: int | None = Field(default=None, ge=-5, le=5, description=(
        "optional point label for hinges / acceptance (0 none; 1 = A at the origin; 2..5 = B..E, negative in "
        "compression); default 1 at the origin and 0 elsewhere"))


class StressStrainP(_EditP):
    material: str = Field(description="an existing concrete, steel, rebar or tendon material")
    curve: Literal["user", "simple", "mander", "park", "strand_250", "strand_270"] = Field(description=(
        "user: the points below | simple: parametric (concrete, steel, rebar) | mander: concrete | park: rebar | "
        "strand_250 / strand_270: tendon"))
    points: list[SSPoint] | None = Field(default=None, min_length=3, max_length=200, description=(
        "user: points in increasing strain, including the origin (0, 0); from the designer"))
    hysteresis: Literal["elastic", "kinematic", "takeda", "pivot", "concrete", "brb_hardening", "degrading",
                        "isotropic"] | None = Field(default=None, description=(
        "hysteresis type; omitted = keep the material's. [[S: SAP2000 refuses 'concrete' for a concrete material "
        "(measured)]]"))
    strain_at_fc: float | None = Field(default=None, gt=0, description="concrete simple / mander: strain at f'c")
    ultimate_strain: float | None = Field(default=None, gt=0, description=(
        "concrete: ultimate strain | rebar: ultimate strain"))
    strain_at_hardening: float | None = Field(default=None, gt=0, description="steel / rebar: strain at hardening")
    strain_at_max_stress: float | None = Field(default=None, gt=0, description="steel: strain at maximum stress")
    strain_at_rupture: float | None = Field(default=None, gt=0, description="steel: strain at rupture")
    final_slope: float | None = Field(default=None, le=0, description=(
        "slope after the last point as a multiple of E (negative, e.g. -0.1)"))
    friction_angle: float | None = Field(default=None, ge=0, lt=90, description="concrete: friction angle, degrees")
    dilatation_angle: float | None = Field(default=None, ge=0, lt=90, description="concrete: dilatation angle, degrees")

    @model_validator(mode="after")
    def _check(self) -> "StressStrainP":
        if self.curve == "user":
            pts = self.points or []
            if not pts:
                raise ValueError("a user curve needs points")
            e = [x.strain for x in pts]
            if any(b <= a for a, b in zip(e, e[1:])):
                raise ValueError("points must be in strictly increasing strain")
            if not any(abs(x.strain) < 1e-15 and abs(x.stress) < 1e-9 for x in pts):
                raise ValueError("the curve must pass through the origin (strain 0, stress 0)")
            if any(getattr(self, f) is not None for f in ("strain_at_fc", "ultimate_strain", "strain_at_hardening",
                                                         "strain_at_max_stress", "strain_at_rupture", "final_slope",
                                                         "friction_angle", "dilatation_angle")):
                raise ValueError("parametric values do not apply to a user curve")
        elif self.points:
            raise ValueError("points apply to curve 'user' only")
        return self


@action("set_material_stress_strain", "Set a material's stress-strain curve: user points (strain, stress) or the "
        "program's parametric curve (concrete simple / Mander, steel simple, rebar simple / Park, tendon 250 / 270 "
        "strand) with the designer's strains and final slope, and the hysteresis type. Answers with the settings and "
        "the curve the program draws, read back.", "Definitions", "edit", StressStrainP)
def set_material_stress_strain(p: StressStrainP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    if p.material not in _names(sap, "PropMaterial"):
        raise CSIValidationError(f"Material '{p.material}' is not defined.")
    kind = _mat_kind(sap, p.material)
    if p.curve != "user" and p.curve not in SS_MODELS[kind]:
        raise CSIValidationError(f"Curve '{p.curve}' does not exist for a {kind} material.",
                                 hint=f"For {kind}: user, " + ", ".join(SS_MODELS[kind]))
    given = {k: getattr(p, k) for k in PARAM_AT[kind] if getattr(p, k) is not None}
    wrong = [k for k in ("strain_at_fc", "ultimate_strain", "strain_at_hardening", "strain_at_max_stress",
                         "strain_at_rupture", "final_slope", "friction_angle", "dilatation_angle")
             if getattr(p, k) is not None and k not in PARAM_AT[kind]]
    if wrong:
        raise CSIValidationError(f"{', '.join(wrong)} do not apply to a {kind} material.",
                                 hint=f"{kind}: {', '.join(PARAM_AT[kind])}")
    pts = None
    if p.points:
        pts = [{"strain": x.strain, "stress": x.stress,
                "point_id": x.point_id if x.point_id is not None else (1 if abs(x.strain) < 1e-15 else 0)}
               for x in p.points]
    plan = {"material": p.material, "kind": kind, "curve": p.curve, **({"hysteresis": p.hysteresis}
                                                                        if p.hysteresis else {}),
            **({"parameters": given} if given else {}), **({"points": pts} if pts else {}),
            "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    getter, setter = GETSET[kind]
    pm = sap.PropMaterial
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_stress_strain", "set a stress-strain curve")
        g = list(api_call(f"PropMaterial.{getter}", getattr(pm, getter), p.material))[:NPARAM[kind]]
        ss, hy = SS_AT[kind]
        g[ss] = 0 if p.curve == "user" else SS_MODELS[kind][p.curve]
        if p.hysteresis:
            g[hy] = HYSTERESIS[p.hysteresis]
        for k, v in given.items():
            g[PARAM_AT[kind][k]] = v
        api_call(f"PropMaterial.{setter}", getattr(pm, setter), p.material, *g,
                 hint="The program refused this combination (e.g. SAP2000 takes no 'concrete' hysteresis for a "
                      "concrete material).")
        if pts:
            api_call("PropMaterial.SetSSCurve", pm.SetSSCurve, p.material, len(pts), [x["point_id"] for x in pts],
                     [x["strain"] for x in pts], [x["stress"] for x in pts])
        _refresh_view(sap)
        back = read_stress_strain(sap, p.material, kind)
    diff: dict[str, Any] = {}
    if back.get("curve") != p.curve:
        diff["curve"] = [p.curve, back.get("curve")]
    if p.hysteresis and back.get("hysteresis") != p.hysteresis:
        diff["hysteresis"] = [p.hysteresis, back.get("hysteresis")]
    for k, v in given.items():
        b = (back.get("parameters") or {}).get(k)
        if b is None or not _close(b, v):
            diff[k] = [v, b]
    if pts:
        got = back.get("points") or []
        if len(got) != len(pts) or any(not (_close(a["strain"], b["strain"]) and _close(a["stress"], b["stress"]))
                                       for a, b in zip(pts, got)):
            diff["points"] = {"given": pts, "read_back": got}
    out = {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out


# --------------------------------------------------------------------------- time-dependent concrete (SAP2000)

TD_REFUSED = {
    "ETABS": ("ETABS 23.2 has no typed time-dependent material calls (its TimeDep interface has no methods), and its "
              "'Material Properties - Time Dependence - CEB-FIP 90' table imports with no error but keeps nothing "
              "(measured 28 Nov 2026)."),
    "SAFE": ("SAFE 23.3 has no typed time-dependent material calls and cannot open its time-dependence tables for "
             "editing (return code -96, measured 28 Nov 2026)."),
}


def read_time_dependence(sap: Any, name: str) -> dict[str, Any] | None:
    td = getattr(sap.PropMaterial, "TimeDep", None)
    if td is None or not hasattr(td, "GetConcreteCEBFIP90"):
        return None
    try:
        g = api_call("TimeDep.GetConcreteCEBFIP90", td.GetConcreteCEBFIP90, name)
    except Exception:  # noqa: BLE001 - not concrete / not set
        return None
    rec = {"model": "CEB-FIP 90", "consider_age": bool(g[0]), "consider_creep": bool(g[1]),
           "consider_shrinkage": bool(g[2]), "cement_coefficient": _f(g[3]), "relative_humidity": _f(g[4]),
           "notional_size": _f(g[5]), "shrinkage_coefficient": _f(g[6]), "shrinkage_start_age": _f(g[7]),
           "creep_integration": "dirichlet_series" if int(g[8]) == 1 else "full_integration",
           "series_terms": int(g[9])}
    try:
        s = api_call("TimeDep.GetConcreteScaleFactors", td.GetConcreteScaleFactors, name)
    except Exception:  # noqa: BLE001
        s = None
    if s:
        rec["scale_factors"] = {"age": _f(s[0]), "creep": _f(s[1]), "shrinkage": _f(s[2])}
    return rec


class TimeDepP(_EditP):
    material: str = Field(description="an existing concrete material")
    model: Literal["CEB-FIP 90"] = Field(default="CEB-FIP 90", description="time-dependence model: CEB-FIP 90")
    consider_age: bool = Field(description="time-dependent stiffness (age)")
    consider_creep: bool = Field(description="creep")
    consider_shrinkage: bool = Field(description="shrinkage")
    cement_coefficient: float = Field(gt=0, lt=1, description="cement type coefficient s (e.g. 0.25 for normal cement)")
    relative_humidity: float = Field(gt=0, le=100, description="relative humidity RH, percent")
    notional_size: float = Field(gt=0, description="notional size h = 2 Ac / u, length units")
    shrinkage_coefficient: float = Field(gt=0, description="shrinkage coefficient beta_sc")
    shrinkage_start_age: float = Field(ge=0, description="age at the start of drying (shrinkage), days")
    creep_integration: Literal["full_integration", "dirichlet_series"] = Field(description=(
        "creep analysis: full_integration | dirichlet_series"))
    series_terms: int = Field(default=5, ge=1, le=20, description="dirichlet_series: number of series terms")
    scale_factors: list[float] | None = Field(default=None, min_length=3, max_length=3, description=(
        "optional scale factors [age (stiffness), creep, shrinkage]"))


@action("set_material_time_dependence", "Make a concrete material time dependent (CEB-FIP 90): age, creep and "
        "shrinkage switches, cement coefficient s, relative humidity, notional size, beta_sc, shrinkage start age, "
        "creep integration and optional scale factors - all from the designer. For staged-construction creep and "
        "shrinkage. Answers with the properties read back.", "Definitions", "edit", TimeDepP, programs="S")
def set_material_time_dependence(p: TimeDepP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    if p.software in TD_REFUSED:       # the action is SAP2000-only; this guards the all-programs development server
        raise CSIValidationError(f"Time-dependent material properties cannot be set in {p.software} through its API.",
                                 hint=TD_REFUSED[p.software])
    if p.material not in _names(sap, "PropMaterial"):
        raise CSIValidationError(f"Material '{p.material}' is not defined.")
    if _mat_kind(sap, p.material) != "concrete":
        raise CSIValidationError(f"Material '{p.material}' is not concrete.")
    given = p.model_dump(include={"consider_age", "consider_creep", "consider_shrinkage", "cement_coefficient",
                                  "relative_humidity", "notional_size", "shrinkage_coefficient",
                                  "shrinkage_start_age", "creep_integration"})
    if p.creep_integration == "dirichlet_series":
        given["series_terms"] = p.series_terms
    plan = {"material": p.material, "model": p.model, **given,
            **({"scale_factors": dict(zip(("age", "creep", "shrinkage"), p.scale_factors))} if p.scale_factors
               else {}), "units": describe_units(p.units)}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    td = sap.PropMaterial.TimeDep
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_time_dependence", "set time-dependent properties")
        api_call("TimeDep.SetConcreteCEBFIP90", td.SetConcreteCEBFIP90, p.material, p.consider_age, p.consider_creep,
                 p.consider_shrinkage, p.cement_coefficient, p.relative_humidity, p.notional_size,
                 p.shrinkage_coefficient, p.shrinkage_start_age, 1 if p.creep_integration == "dirichlet_series" else 0,
                 p.series_terms)
        if p.scale_factors:
            api_call("TimeDep.SetConcreteScaleFactors", td.SetConcreteScaleFactors, p.material, *p.scale_factors)
        back = read_time_dependence(sap, p.material)
    if not back:
        raise CSIValidationError(f"The program did not keep the time-dependent properties of '{p.material}'.")
    diff = {k: [v, back.get(k)] for k, v in given.items()
            if not (_close(back.get(k), v) if isinstance(v, float) else back.get(k) == v)}
    if p.scale_factors:
        sf = back.get("scale_factors") or {}
        for k, v in zip(("age", "creep", "shrinkage"), p.scale_factors):
            if k not in sf or not _close(sf[k], v):
                diff[f"scale_factor_{k}"] = [v, sf.get(k)]
    out = {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out
