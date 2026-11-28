"""Phase 3, batch 2: definitions - materials, frame and area sections, spring properties, load patterns, load cases,
combinations and mass source.

Every action takes dry_run, snapshot and allow_unlock, checks names first, and answers with the definition read back
from the program (the same readers get_definition_details / get_load_case_details / get_combo_details use), so what is
reported is what the program stored. A definition that already exists is changed only with replace: true.

Values come from the user (NO DESIGN): only the values given are set. A material keeps the program's own defaults
for everything not given (e.g. the nonlinear stress-strain parameters of its type); the answer lists what was kept.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import (
    ActionFailed, ALLOW_UNLOCK, DRY_RUN, SNAPSHOT, SOFTWARE, UNITS, Params, _dry, _refresh_view, action, bridge,
)
from csi_bridge import LOAD_PATTERN_TYPES, COMError, CSIError, CSIValidationError, Software, UnitPreset, api_call, as_list, \
    describe_units
from model_read import (
    COMBO_TYPES, _try, read_area_sections, read_combos, read_frame_sections, read_load_cases, read_load_patterns,
    read_mass_source, read_materials,
)
from modelling import _start_edit

NAME = Field(min_length=1, max_length=80, description="name of the definition")
REPLACE = Field(default=False, description=(
    "the name already exists: false (default) refuses, true changes the existing definition"))

MAT_TYPE_CODES = {"steel": 1, "concrete": 2, "other": 3, "aluminum": 4, "cold_formed": 5, "rebar": 6, "tendon": 7}
PATTERN_CODES = {v.lower(): k for k, v in LOAD_PATTERN_TYPES.items()}
COMBO_CODES = {"linear_add": 0, "envelope": 1, "absolute_add": 2, "srss": 3, "range_add": 4}
SAP_SHELL = {"shell_thin": 1, "shell_thick": 2, "plate_thin": 3, "plate_thick": 4, "membrane": 5}
ETABS_SHELL = {"shell_thin": 1, "shell_thick": 2, "membrane": 3}
SLAB_TYPES = {"slab": 0, "drop": 1, "stiff": 2, "mat": 5, "footing": 6}
ACCEL = ("UX", "UY", "UZ", "RX", "RY", "RZ")


class _DefP(Params):
    software: Software = SOFTWARE
    name: str = NAME
    replace: bool = REPLACE
    units: UnitPreset = UNITS
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT


def _check_new(sap: Any, collection: str, name: str, replace: bool, kind: str) -> bool:
    """True when the name exists (and replace allows changing it); refuses an existing name without replace."""
    names = bridge.name_list(sap, collection)
    there = name in names
    if not there:
        twin = next((n for n in names if n.lower() == name.lower()), None)
        if twin is not None:     # ETABS / SAFE refuse 'DEAD' next to 'Dead' (measured: LoadPatterns.Add -> 1)
            raise CSIValidationError(f"{kind} '{twin}' exists; names differing only in upper / lower case clash.",
                                     hint=f"Use '{twin}' (with replace: true to change it) or another name.")
    if there and not replace:
        raise CSIValidationError(f"{kind} '{name}' already exists.",
                                 hint="Use another name, or replace: true to change the existing one.")
    return there


def _need(sap: Any, collection: str, name: str, kind: str) -> None:
    names = bridge.try_name_list(sap, [collection])[0] or []
    if name not in names:
        raise CSIValidationError(f"{kind} '{name}' is not defined.", hint=f"Defined: {names[:30]}")


def _finish(p: Any, plan: dict[str, Any], read_back: Any, was_locked: bool, snap: dict | None,
            **extra: Any) -> dict[str, Any]:
    out = {**plan, "read_back": read_back, "model_was_unlocked": was_locked, **extra}
    if snap:
        out["snapshot"] = snap
    return out


# --------------------------------------------------------------------------- materials

class MaterialP(_DefP):
    type: Literal["concrete", "steel", "rebar", "tendon", "aluminum", "cold_formed", "other"] = Field(
        description="concrete | steel | rebar | tendon | aluminum | cold_formed | other")
    source: Literal["user", "library"] = Field(default="user", description=(
        "user (default): the values below | library: a material from the program's library (region, standard, "
        "grade) with the library's values"))
    E: float | None = Field(default=None, gt=0, description="user: modulus of elasticity (force / length^2)")
    poisson: float | None = Field(default=None, ge=0, lt=0.5, description="user: Poisson's ratio")
    unit_weight: float | None = Field(default=None, ge=0, description=(
        "user: weight per unit volume (force / length^3); mass is derived from it"))
    thermal_coeff: float | None = Field(default=None, ge=0, description=(
        "user: coefficient of thermal expansion (1 / degree); omitted = the program's default for the type"))
    fc: float | None = Field(default=None, gt=0, description="concrete: specified compressive strength f'c")
    fy: float | None = Field(default=None, gt=0, description="steel / rebar / tendon: yield stress")
    fu: float | None = Field(default=None, gt=0, description="steel / rebar / tendon: tensile strength")
    region: str | None = Field(default=None, description="library: region as the program lists it, e.g. 'United States'")
    standard: str | None = Field(default=None, description="library: standard, e.g. 'ASTM A992'")
    grade: str | None = Field(default=None, description="library: grade, e.g. 'Grade 50'")

    @model_validator(mode="after")
    def _check(self) -> "MaterialP":
        if self.source == "library":
            if not (self.region and self.standard and self.grade):
                raise ValueError("library needs region, standard and grade")
            return self
        missing = [f for f in ("E", "poisson", "unit_weight") if getattr(self, f) is None]
        if self.type == "concrete" and self.fc is None:
            missing.append("fc")
        if self.type in ("steel", "rebar", "tendon"):
            missing += [f for f in ("fy", "fu") if getattr(self, f) is None]
        if missing:
            raise ValueError(f"{self.type} from user values needs {', '.join(missing)} (values from the user)")
        if self.fy and self.fu and self.fu < self.fy:
            raise ValueError("fu must not be below fy")
        return self


@action("define_material", "Create or change a material: concrete, steel, rebar, tendon, aluminum ... from the user's "
        "values (E, Poisson, unit weight, f'c or fy / fu; everything else keeps the program's defaults for the type) "
        "or from the program's library (region, standard, grade). Answers with the material read back.",
        "Definitions", "edit", MaterialP)
def define_material(p: MaterialP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        existed = _check_new(sap, "PropMaterial", p.name, p.replace, "Material")
        plan = {"material": p.name, "type": p.type, "source": p.source, "existed": existed,
                "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_material", "define a material")
        kept: list[str] = []
        if p.source == "library":
            o = api_call("PropMaterial.AddMaterial", sap.PropMaterial.AddMaterial, p.name, MAT_TYPE_CODES[p.type],
                         p.region, p.standard, p.grade, p.name)
            stored = str(o[0]) if o and o[0] else p.name
        else:
            stored = p.name
            api_call("PropMaterial.SetMaterial", sap.PropMaterial.SetMaterial, p.name, MAT_TYPE_CODES[p.type])
            iso = _try(sap, "PropMaterial.GetMPIsotropic", p.name)
            if iso is None:            # rebar / tendon are uniaxial in the programs (measured: tendon)
                uni = api_call("PropMaterial.GetMPUniaxial", sap.PropMaterial.GetMPUniaxial, p.name)
                alpha = p.thermal_coeff if p.thermal_coeff is not None else float(uni[1])
                api_call("PropMaterial.SetMPUniaxial", sap.PropMaterial.SetMPUniaxial, p.name, p.E, alpha)
                kept.append("poisson (uniaxial material: not used)")
            else:
                alpha = p.thermal_coeff if p.thermal_coeff is not None else float(iso[2])
                api_call("PropMaterial.SetMPIsotropic", sap.PropMaterial.SetMPIsotropic, p.name, p.E, p.poisson,
                         alpha)
            if p.thermal_coeff is None:
                kept.append("thermal_coeff")
            api_call("PropMaterial.SetWeightAndMass", sap.PropMaterial.SetWeightAndMass, p.name, 1, p.unit_weight)
            kept += _set_strengths(sap, p)
        _refresh_view(sap)
        back = read_materials(sap, [stored])
        strength = _read_strength(sap, stored, p.type)
        if strength and back:
            back[0].update(strength)
        return _finish(p, plan, back[0] if back else None, was_locked, snap, name_in_model=stored,
                       kept_program_defaults=kept)


def _set_strengths(sap: Any, p: MaterialP) -> list[str]:
    """Read the program's defaults for the type, replace only the strengths given, write back."""
    pm = sap.PropMaterial
    if p.type == "concrete":
        g = api_call("PropMaterial.GetOConcrete_1", pm.GetOConcrete_1, p.name)
        api_call("PropMaterial.SetOConcrete_1", pm.SetOConcrete_1, p.name, p.fc, *g[1:10])
        return ["lightweight / stress-strain parameters"]
    if p.type in ("steel", "rebar", "tendon"):
        getter, setter = {"steel": ("GetOSteel_1", "SetOSteel_1"), "rebar": ("GetORebar_1", "SetORebar_1"),
                          "tendon": ("GetOTendon_1", "SetOTendon_1")}[p.type]
        g = api_call(f"PropMaterial.{getter}", getattr(pm, getter), p.name)
        fy0, fu0 = float(g[0]) or 1.0, float(g[1]) or 1.0
        if p.type == "tendon":     # (Fy, Fu, SSType, SSHysType, FinalSlope)
            api_call(f"PropMaterial.{setter}", getattr(pm, setter), p.name, p.fy, p.fu, *g[2:5])
            return ["stress-strain parameters"]
        # expected strengths keep the program's default ratio to fy / fu
        rest = list(g[4:10])       # api_call has removed the return code: SSType ... FinalSlope / Caltrans flag
        api_call(f"PropMaterial.{setter}", getattr(pm, setter), p.name, p.fy, p.fu, float(g[2]) / fy0 * p.fy,
                 float(g[3]) / fu0 * p.fu, *rest)
        return ["expected strengths (program's ratio to fy / fu)", "stress-strain parameters"]
    return []


def _read_strength(sap: Any, name: str, kind: str) -> dict[str, Any]:
    if kind == "concrete":
        g = _try(sap, "PropMaterial.GetOConcrete_1", name)
        return {"fc": round(float(g[0]), 6)} if g else {}
    getter = {"steel": "GetOSteel_1", "rebar": "GetORebar_1", "tendon": "GetOTendon_1"}.get(kind)
    g = _try(sap, f"PropMaterial.{getter}", name) if getter else None
    return {"fy": round(float(g[0]), 6), "fu": round(float(g[1]), 6)} if g else {}


# --------------------------------------------------------------------------- frame sections

FRAME_SHAPES = ("rectangle", "circle", "i_section", "pipe", "tube", "angle", "channel", "tee", "general", "from_file")
SHAPE_NEEDS = {"rectangle": ("depth", "width"), "circle": ("depth",),
               "i_section": ("depth", "width", "flange_thickness", "web_thickness"),
               "pipe": ("depth", "web_thickness"), "tube": ("depth", "width", "flange_thickness", "web_thickness"),
               "angle": ("depth", "width", "flange_thickness", "web_thickness"),
               "channel": ("depth", "width", "flange_thickness", "web_thickness"),
               "tee": ("depth", "width", "flange_thickness", "web_thickness"),
               "general": ("depth", "width"), "from_file": ("file_name", "section_in_file")}
GENERAL_KEYS = ("area", "as2", "as3", "torsion", "i22", "i33", "s22", "s33", "z22", "z33", "r22", "r33")


class FrameSectionP(_DefP):
    shape: Literal[FRAME_SHAPES] = Field(description=(  # type: ignore[valid-type]
        "rectangle | circle | i_section | pipe | tube | angle | channel | tee | general (section properties "
        "given) | from_file (a section from a .pro / .xml property file)"))
    material: str = Field(description="an existing material")
    depth: float | None = Field(default=None, gt=0, description="t3: overall depth (circle / pipe: outside diameter)")
    width: float | None = Field(default=None, gt=0, description="t2: width / flange width (top flange for I)")
    flange_thickness: float | None = Field(default=None, gt=0, description="tf (I / tube / angle / channel / tee)")
    web_thickness: float | None = Field(default=None, gt=0, description="tw (pipe: wall thickness)")
    bottom_width: float | None = Field(default=None, gt=0, description="I section: bottom flange width; default width")
    bottom_flange_thickness: float | None = Field(default=None, gt=0, description=(
        "I section: bottom flange thickness; default flange_thickness"))
    general_properties: dict[str, float] | None = Field(default=None, description=(
        "general: " + ", ".join(GENERAL_KEYS) + " (all twelve, from the user)"))
    file_name: str | None = Field(default=None, description="from_file: property file, e.g. 'AISC16.xml' or a full path")
    section_in_file: str | None = Field(default=None, description="from_file: the section's name in that file")
    modifiers: list[float] | None = Field(default=None, min_length=8, max_length=8, description=(
        "optional property modifiers: area, as2, as3, torsion, i22, i33, mass, weight"))

    @model_validator(mode="after")
    def _check(self) -> "FrameSectionP":
        missing = [f for f in SHAPE_NEEDS[self.shape] if getattr(self, f) is None]
        if self.shape == "general":
            got = set(self.general_properties or {})
            if got != set(GENERAL_KEYS):
                missing.append("general_properties (" + ", ".join(sorted(set(GENERAL_KEYS) - got)) + ")")
        if missing:
            raise ValueError(f"{self.shape} needs {', '.join(missing)}")
        return self


@action("define_frame_section", "Create or change a frame section: rectangle, circle, I, pipe, tube, angle, channel, "
        "tee with its dimensions, a general section from given properties, or a section from a property file; "
        "optional property modifiers. Answers with the section read back.", "Definitions", "edit", FrameSectionP)
def define_frame_section(p: FrameSectionP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        _need(sap, "PropMaterial", p.material, "Material")
        existed = _check_new(sap, "PropFrame", p.name, p.replace, "Frame section")
        plan = {"section": p.name, "shape": p.shape, "material": p.material, "existed": existed,
                "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_frame_section", "define a frame section")
        pf, n, m = sap.PropFrame, p.name, p.material
        t3, t2, tf, tw = p.depth, p.width, p.flange_thickness, p.web_thickness
        if p.shape == "rectangle":
            api_call("PropFrame.SetRectangle", pf.SetRectangle, n, m, t3, t2)
        elif p.shape == "circle":
            api_call("PropFrame.SetCircle", pf.SetCircle, n, m, t3)
        elif p.shape == "i_section":
            api_call("PropFrame.SetISection", pf.SetISection, n, m, t3, t2, tf, tw, p.bottom_width or t2,
                     p.bottom_flange_thickness or tf)
        elif p.shape == "pipe":
            api_call("PropFrame.SetPipe", pf.SetPipe, n, m, t3, tw)
        elif p.shape in ("tube", "angle", "channel", "tee"):
            meth = {"tube": "SetTube", "angle": "SetAngle", "channel": "SetChannel", "tee": "SetTee"}[p.shape]
            api_call(f"PropFrame.{meth}", getattr(pf, meth), n, m, t3, t2, tf, tw)
        elif p.shape == "general":
            g = p.general_properties or {}
            api_call("PropFrame.SetGeneral", pf.SetGeneral, n, m, t3, t2, *(g[k] for k in GENERAL_KEYS))
        else:
            api_call("PropFrame.ImportProp", pf.ImportProp, n, m, p.file_name, p.section_in_file)
        if p.modifiers:
            api_call("PropFrame.SetModifiers", pf.SetModifiers, n, list(p.modifiers))
        _refresh_view(sap)
        back = read_frame_sections(sap, p.software, [n])
        props = _try(sap, "PropFrame.GetSectProps", n)
        if props and back:
            back[0]["properties"] = {k: round(float(v), 9) for k, v in zip(
                ("area", "as2", "as3", "torsion", "i22", "i33", "s22", "s33", "z22", "z33", "r22", "r33"), props)}
        return _finish(p, plan, back[0] if back else None, was_locked, snap)


# --------------------------------------------------------------------------- area sections

class AreaSectionP(_DefP):
    kind: Literal["shell_thin", "shell_thick", "plate_thin", "plate_thick", "membrane", "slab", "wall"] = Field(
        description="[[S: shell_thin | shell_thick | plate_thin | plate_thick | membrane]][[EF: slab | wall]]")
    material: str = Field(description="an existing material")
    thickness: float = Field(gt=0, description="membrane thickness (units)")
    bending_thickness: float | None = Field(default=None, gt=0, description=(
        "bending thickness; default the thickness"))
    shell_type: Literal["shell_thin", "shell_thick", "membrane"] = Field(default="shell_thin", description=(
        "slab / wall behaviour: shell_thin (default) | shell_thick | membrane"))
    slab_type: Literal["slab", "drop", "stiff", "mat", "footing"] = Field(default="slab", description=(
        "slab: slab (default) | drop | stiff | mat | footing"))
    modifiers: list[float] | None = Field(default=None, min_length=10, max_length=10, description=(
        "optional stiffness modifiers: f11, f22, f12, m11, m22, m12, v13, v23, mass, weight"))


@action("define_area_section", "Create or change an area section: [[S: shell (thin / thick), plate (thin / thick) or "
        "membrane]][[EF: slab (slab, drop, stiff, mat, footing) or wall, as thin / thick shell or membrane]] with "
        "material and thickness; optional stiffness modifiers. Answers with the section read back.", "Definitions",
        "edit", AreaSectionP,
        options={"kind": {"shell_thin": "S", "shell_thick": "S", "plate_thin": "S", "plate_thick": "S",
                          "membrane": "S", "slab": "EF", "wall": "EF"}},
        fields={"bending_thickness": "S", "shell_type": "EF", "slab_type": "EF"})
def define_area_section(p: AreaSectionP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    with bridge.units_scope(sap, p.units):
        _need(sap, "PropMaterial", p.material, "Material")
        existed = _check_new(sap, "PropArea", p.name, p.replace, "Area section")
        plan = {"section": p.name, "kind": p.kind, "material": p.material, "thickness": p.thickness,
                "existed": existed, "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_area_section", "define an area section")
        pa = sap.PropArea
        if p.software == "SAP2000":
            api_call("PropArea.SetShell_1", pa.SetShell_1, p.name, SAP_SHELL[p.kind], False, p.material, 0.0,
                     p.thickness, p.bending_thickness or p.thickness)
        elif p.kind == "slab":
            api_call("PropArea.SetSlab", pa.SetSlab, p.name, SLAB_TYPES[p.slab_type], ETABS_SHELL[p.shell_type],
                     p.material, p.thickness)
        else:
            api_call("PropArea.SetWall", pa.SetWall, p.name, 1, ETABS_SHELL[p.shell_type], p.material, p.thickness)
        if p.modifiers:
            api_call("PropArea.SetModifiers", pa.SetModifiers, p.name, list(p.modifiers))
        _refresh_view(sap)
        back = read_area_sections(sap, p.software, [p.name])
        return _finish(p, plan, back[0] if back else None, was_locked, snap)


# --------------------------------------------------------------------------- spring properties (ETABS / SAFE)

SPRING_N = {"point": 6, "line": 4, "area": 3}
NONLIN = {"linear": 0, "compression_only": 1, "tension_only": 2}


class SpringP(_DefP):
    kind: Literal["point", "line", "area"] = Field(description=(
        "point: six stiffnesses U1 U2 U3 R1 R2 R3 | line: U1 U2 U3 R1 per unit length (e.g. a wall or beam support "
        "line) | area: U1 U2 U3 per unit area (e.g. modulus of subgrade reaction)"))
    stiffness: list[float] = Field(min_length=3, max_length=6, description=(
        "point: [U1, U2, U3, R1, R2, R3] (force / length, moment / radian) | line: [U1, U2, U3, R1] (force / "
        "length^2, moment / radian / length) | area: [U1, U2, U3] (force / length^3); from the user"))
    nonlinear: Literal["linear", "compression_only", "tension_only"] = Field(default="linear", description=(
        "line / area: the vertical (U3; line: U2 too) spring is linear (default), compression only or tension only"))

    @model_validator(mode="after")
    def _check(self) -> "SpringP":
        if len(self.stiffness) != SPRING_N[self.kind]:
            raise ValueError("point springs take 6 stiffnesses, line springs 4, area springs 3")
        if self.kind == "point" and self.nonlinear != "linear":
            raise ValueError("nonlinear applies to line and area springs")
        if any(k < 0 for k in self.stiffness):
            raise ValueError("stiffnesses must not be negative")
        return self


SAFE_LINE_TABLE = "Spring Property Definitions - Line Springs"
NONLIN_TEXT = {"linear": "None (Linear)", "compression_only": "Compression Only", "tension_only": "Tension Only"}


def _safe_line_springs(sap: Any) -> dict[str, dict[str, Any]]:
    """SAFE 23.3: GetLineSpringProp does not return the stiffnesses the model holds (measured: the table showed
    VertStiff 1.751e12 kN/m/m, the getter U2 = 0.001); the database table is the reliable source."""
    from db_edit import read_table
    _, _, rows = read_table(sap, SAFE_LINE_TABLE)
    text = {v.lower(): k for k, v in NONLIN_TEXT.items()}
    return {r["Name"]: {"stiffness": [0.0, 0.0, float(r.get("VertStiff") or 0), float(r.get("RotStiff") or 0)],
                        "nonlinear": text.get(str(r.get("NonlinOpt", "")).lower(), str(r.get("NonlinOpt")))}
            for r in rows}


def read_spring_props(sap: Any, names: list[str] | None = None, sw: str = "") -> list[dict[str, Any]]:
    out = []
    safe_lines = None
    if sw == "SAFE":
        try:
            safe_lines = _safe_line_springs(sap)
        except (CSIError, COMError, AttributeError, IndexError, TypeError, ValueError):
            safe_lines = None
    for kind, coll in (("point", "PropPointSpring"), ("line", "PropLineSpring"), ("area", "PropAreaSpring")):
        for n in bridge.try_name_list(sap, [coll])[0] or []:
            if names and n not in names:
                continue
            rec: dict[str, Any] = {"kind": kind, "name": n}
            if kind == "point":
                g = _try(sap, "PropPointSpring.GetPointSpringProp", n)
                if g:
                    rec["stiffness"] = [float(f"{float(v):.10g}") for v in as_list(g[1])]
            elif kind == "line" and safe_lines is not None and n in safe_lines:
                rec.update(safe_lines[n])
            elif kind == "line":
                g = _try(sap, "PropLineSpring.GetLineSpringProp", n)
                if g:
                    rec["stiffness"] = [float(f"{float(v):.10g}") for v in g[0:4]]
                    rec["nonlinear"] = {v: k for k, v in NONLIN.items()}.get(int(g[5]), str(g[5]))
            else:
                g = _try(sap, "PropAreaSpring.GetAreaSpringProp", n)
                if g:
                    rec["stiffness"] = [float(f"{float(v):.10g}") for v in g[0:3]]
                    rec["nonlinear"] = {v: k for k, v in NONLIN.items()}.get(int(g[3]), str(g[3]))
            out.append(rec)
    return out


@action("define_spring_property", "Create or change a point spring property (six stiffnesses), a line spring "
        "property (per unit length) or an area spring property (per unit area, e.g. subgrade modulus). Answers with the property read back.",
        "Definitions", "edit", SpringP, programs="EF")
def define_spring_property(p: SpringP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    coll = {"point": "PropPointSpring", "line": "PropLineSpring", "area": "PropAreaSpring"}[p.kind]
    with bridge.units_scope(sap, p.units):
        existed = _check_new(sap, coll, p.name, p.replace, f"{p.kind.title()} spring property")
        plan = {"spring_property": p.name, "kind": p.kind, "stiffness": p.stiffness, "nonlinear": p.nonlinear,
                "existed": existed,
                "units": describe_units(p.units)}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_spring", "define a spring property")
        if p.kind == "point":
            api_call("PropPointSpring.SetPointSpringProp", sap.PropPointSpring.SetPointSpringProp, p.name, 1,
                     list(p.stiffness), "Global")
        elif p.kind == "line" and p.software == "SAFE":
            # SAFE: the database table (the API pair does not round-trip, see _safe_line_springs)
            from db_edit import edit_table
            u1, u2, u3, r1 = p.stiffness
            if u1 or u2:
                raise CSIValidationError("SAFE line springs have a vertical (U3) and a rotational (R1) stiffness "
                                         "only; give [0, 0, U3, R1].")
            edit_table(sap, SAFE_LINE_TABLE, [{"Name": p.name, "StiffOpt": "User", "VertStiff": repr(float(u3)),
                                               "RotStiff": repr(float(r1)), "NonlinOpt": NONLIN_TEXT[p.nonlinear]}],
                       drop=lambda r: r.get("Name") == p.name)
        elif p.kind == "line":
            u1, u2, u3, r1 = p.stiffness
            nl = NONLIN[p.nonlinear]
            api_call("PropLineSpring.SetLineSpringProp", sap.PropLineSpring.SetLineSpringProp, p.name, u1, u2, u3, r1,
                     nl, nl)
        else:
            u1, u2, u3 = p.stiffness
            api_call("PropAreaSpring.SetAreaSpringProp", sap.PropAreaSpring.SetAreaSpringProp, p.name, u1, u2, u3,
                     NONLIN[p.nonlinear])
        back = read_spring_props(sap, [p.name], p.software)
        return _finish(p, plan, back[0] if back else None, was_locked, snap)


# --------------------------------------------------------------------------- load patterns

class PatternDefP(_DefP):
    type: Literal[tuple(v.lower() for v in LOAD_PATTERN_TYPES.values())] = Field(  # type: ignore[valid-type]
        description=" | ".join(v.lower() for v in LOAD_PATTERN_TYPES.values()))
    self_weight_multiplier: float = Field(default=0.0, ge=0, description=(
        "self-weight multiplier (1 for the pattern that carries the structure's own weight, else 0)"))
    add_load_case: bool = Field(default=True, description="new pattern: also create its linear static load case")


@action("define_load_pattern", "Create or change a load pattern: type (dead, superdead, live, wind, quake, other ...) "
        "and self-weight multiplier; a new pattern gets its linear static load case unless add_load_case is false. "
        "No automatic wind / seismic loads.", "Definitions", "edit", PatternDefP)
def define_load_pattern(p: PatternDefP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    existed = _check_new(sap, "LoadPatterns", p.name, p.replace, "Load pattern")
    plan = {"pattern": p.name, "type": p.type, "self_weight_multiplier": p.self_weight_multiplier, "existed": existed,
            "add_load_case": p.add_load_case and not existed}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_pattern", "define a load pattern")
    lp, code = sap.LoadPatterns, PATTERN_CODES[p.type]
    if existed:
        api_call("LoadPatterns.SetLoadType", lp.SetLoadType, p.name, code)
        api_call("LoadPatterns.SetSelfWTMultiplier", lp.SetSelfWTMultiplier, p.name, p.self_weight_multiplier)
    else:
        api_call("LoadPatterns.Add", lp.Add, p.name, code, p.self_weight_multiplier, p.add_load_case)
    back = read_load_patterns(sap, [p.name])
    return _finish(p, plan, back[0] if back else None, was_locked, snap)


# --------------------------------------------------------------------------- load cases

class CaseLoad(Params):
    kind: Literal["load_pattern", "acceleration"] = Field(default="load_pattern", description=(
        "load_pattern | acceleration (UX UY UZ RX RY RZ; response spectrum / history: the case's directions U1 U2 "
        "U3 R1 R2 R3, UX = U1 ...)"))
    name: str = Field(description="the load pattern, or the acceleration direction")
    factor: float = Field(default=1.0, description=(
        "scale factor (response spectrum / history acceleration: e.g. 9.80665 when the function is in g and the "
        "units are m)"))
    function: str | None = Field(default=None, description=(
        "response spectrum / history: the function (define_function)"))


RS_COMB = {"cqc": 1, "srss": 2, "abs": 3}
DIR_COMB = {"srss": 1, "abs": 2}
TABLE_COMB = {"cqc": "CQC", "srss": "SRSS", "abs": "Absolute"}
DIRMAP = {"UX": "U1", "UY": "U2", "UZ": "U3", "RX": "R1", "RY": "R2", "RZ": "R3"}
CASE_DIRS = ("U1", "U2", "U3", "R1", "R2", "R3")


class LoadCaseP(_DefP):
    type: Literal["linear_static", "nonlinear_static", "modal_eigen", "modal_ritz", "response_spectrum",
                  "linear_modal_history"] = Field(description=(
        "linear_static | nonlinear_static | modal_eigen | modal_ritz [[ES:| response_spectrum | "
        "linear_modal_history]]"))
    loads: list[CaseLoad] | None = Field(default=None, max_length=200, description=(
        "linear_static: load patterns with factors | modal_ritz: starting vectors, e.g. accelerations UX, UY, UZ"
        "[[ES: | response_spectrum: accelerations U1 / U2 / U3 with a function and scale | linear_modal_history: "
        "load patterns or accelerations, each with a function and scale]]"))
    max_modes: int = Field(default=12, ge=1, le=10000, description="modal: maximum number of modes")
    min_modes: int = Field(default=1, ge=1, le=10000, description="modal: minimum number of modes")
    modal_case: str | None = Field(default=None, description=(
        "response spectrum / history: the modal case to use (default 'Modal' when it exists)"))
    modal_combination: Literal["cqc", "srss", "abs"] = Field(default="cqc", description=(
        "response spectrum: modal combination cqc (default) | srss | abs"))
    directional_combination: Literal["srss", "abs"] = Field(default="srss", description=(
        "response spectrum: directional combination srss (default) | abs"))
    damping: float | None = Field(default=None, ge=0, lt=1, description=(
        "response spectrum / history: constant modal damping ratio, e.g. 0.05 (default the program's 0.05)"))
    steps: int | None = Field(default=None, ge=1, le=1000000, description="linear_modal_history: number of steps")
    step_size: float | None = Field(default=None, gt=0, description="linear_modal_history: step size (s)")
    geometric_nonlinearity: Literal["none", "p_delta", "p_delta_large_displacements"] = Field(default="none",
        description="nonlinear_static: none (default) | p_delta[[S: | p_delta_large_displacements]]")
    initial_case: str | None = Field(default=None, description=(
        "nonlinear_static: start from the end of this nonlinear case (default: zero initial conditions)"))
    load_control: Literal["full_load", "displacement"] = Field(default="full_load", description=(
        "nonlinear_static: full_load (default) | displacement (pushover: push to target_displacement at "
        "monitor_point in monitor_dof)"))
    monitor_point: str | None = Field(default=None, description="displacement control: the monitored point")
    monitor_dof: Literal["U1", "U2", "U3", "R1", "R2", "R3"] = Field(default="U1", description=(
        "displacement control: monitored degree of freedom (global)"))
    target_displacement: float | None = Field(default=None, description=(
        "displacement control: target displacement at the monitored point (present units)"))
    save_steps: bool = Field(default=False, description=(
        "nonlinear_static: save multiple states (a pushover curve) instead of the final state only"))
    min_saved_states: int = Field(default=10, ge=1, le=10000, description="save_steps: minimum number of states")
    max_saved_states: int = Field(default=100, ge=1, le=10000, description="save_steps: maximum number of states")

    @model_validator(mode="after")
    def _check(self) -> "LoadCaseP":
        if self.type in ("linear_static", "nonlinear_static", "modal_ritz", "response_spectrum",
                         "linear_modal_history") and not self.loads:
            raise ValueError(f"{self.type} needs loads")
        if self.min_modes > self.max_modes:
            raise ValueError("min_modes is above max_modes")
        if self.load_control == "displacement" and (not self.monitor_point or self.target_displacement is None):
            raise ValueError("displacement control needs monitor_point and target_displacement")
        dyn = self.type in ("response_spectrum", "linear_modal_history")
        for ld in self.loads or []:
            if ld.kind == "acceleration":
                d = DIRMAP.get(ld.name.upper(), ld.name.upper())
                if (dyn and d not in CASE_DIRS) or (not dyn and ld.name.upper() not in ACCEL):
                    raise ValueError(f"acceleration direction {ld.name!r} is not one of "
                                     f"{', '.join(CASE_DIRS if dyn else ACCEL)}")
            if dyn and not ld.function:
                raise ValueError(f"{self.type}: every load needs a function")
            if self.type == "response_spectrum" and ld.kind != "acceleration":
                raise ValueError("response_spectrum loads are accelerations (U1 / U2 / U3 ...)")
        if self.type == "linear_modal_history" and not (self.steps and self.step_size):
            raise ValueError("linear_modal_history needs steps and step_size")
        return self


def _functions(sap: Any) -> list[str]:
    o = _try(sap, "Func.GetNameList")
    return [str(x) for x in as_list(o[1])] if o else []


def _modal_case(sap: Any, p: LoadCaseP) -> str:
    cases = bridge.try_name_list(sap, ["LoadCases"])[0] or []
    mc = p.modal_case or ("Modal" if "Modal" in cases else "")
    if not mc or mc not in cases:
        raise CSIValidationError(f"Modal case '{p.modal_case or 'Modal'}' is not defined.",
                                 hint="Give modal_case (define_load_case type modal_eigen / modal_ritz makes one).")
    return mc


def _dynamic_case(sap: Any, p: LoadCaseP) -> dict[str, Any]:
    """Response spectrum / linear modal history: API where the program has it, database tables otherwise (ETABS)."""
    lc, mc, extra = sap.LoadCases, _modal_case(sap, p), {}
    dirs = [DIRMAP.get(ld.name.upper(), ld.name.upper()) if ld.kind == "acceleration" else ld.name
            for ld in p.loads or []]
    funcs = [ld.function for ld in p.loads or []]
    sfs = [ld.factor for ld in p.loads or []]
    n = len(dirs)
    if p.type == "response_spectrum":
        rs = lc.ResponseSpectrum
        api_call("LoadCases.ResponseSpectrum.SetCase", rs.SetCase, p.name)
        api_call("LoadCases.ResponseSpectrum.SetLoads", rs.SetLoads, p.name, n, dirs, funcs, sfs, ["Global"] * n,
                 [0.0] * n)
        api_call("LoadCases.ResponseSpectrum.SetModalCase", rs.SetModalCase, p.name, mc)
        if p.software == "SAP2000":
            api_call("LoadCases.ResponseSpectrum.SetModalComb_1", rs.SetModalComb_1, p.name,
                     RS_COMB[p.modal_combination], 1.0, 0.0, 1, 60.0)
            api_call("LoadCases.ResponseSpectrum.SetDirComb", rs.SetDirComb, p.name, DIR_COMB[p.directional_combination],
                     1.0)
            if p.damping is not None:
                api_call("LoadCases.ResponseSpectrum.SetDampConstant", rs.SetDampConstant, p.name, p.damping)
        else:
            from db_edit import edit_table
            extra["table_edit"] = edit_table(
                sap, "Load Case Definitions - Response Spectrum", [], update=lambda r: {
                    "ModalCombo": TABLE_COMB[p.modal_combination],
                    "DirCombo": "SRSS" if p.directional_combination == "srss" else "Absolute"}
                if r.get("Name") == p.name and r.get("ModalCombo", "") != "" else None)
    else:
        mh = lc.ModHistLinear
        kinds = ["Load" if ld.kind == "load_pattern" else "Accel" for ld in p.loads or []]
        api_call("LoadCases.ModHistLinear.SetCase", mh.SetCase, p.name)
        api_call("LoadCases.ModHistLinear.SetLoads", mh.SetLoads, p.name, n, kinds, dirs, funcs, sfs, [1.0] * n,
                 [0.0] * n, ["Global"] * n, [0.0] * n)
        if p.software == "SAP2000":
            api_call("LoadCases.ModHistLinear.SetModalCase", mh.SetModalCase, p.name, mc)
            api_call("LoadCases.ModHistLinear.SetTimeStep", mh.SetTimeStep, p.name, p.steps, p.step_size)
            if p.damping is not None:
                api_call("LoadCases.ModHistLinear.SetDampConstant", mh.SetDampConstant, p.name, p.damping)
        else:
            from db_edit import edit_table
            extra["table_edit"] = edit_table(
                sap, "Load Case Definitions - Time History - Linear Modal", [], update=lambda r: {
                    "ModalCase": mc, "NumSteps": p.steps, "StepSize": p.step_size}
                if r.get("Name") == p.name and r.get("NumSteps", "") != "" else None)
    if p.software != "SAP2000":
        extra["damping_note"] = "ETABS uses the model's modal damping for this case (the program's default is 0.05)"
    return {"modal_case": mc, **extra}


def read_dynamic_case(sap: Any, software: str, name: str, kind: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if kind == "response_spectrum":
        g = _try(sap, "LoadCases.ResponseSpectrum.GetLoads", name)
        if g:
            out["loads"] = [{"direction": str(d), "function": str(f), "factor": round(float(sf), 9)}
                            for d, f, sf in zip(as_list(g[1]), as_list(g[2]), as_list(g[3]))]
        m = _try(sap, "LoadCases.ResponseSpectrum.GetModalCase", name)
        mc = _try(sap, "LoadCases.ResponseSpectrum.GetModalComb_1", name)
        dc = _try(sap, "LoadCases.ResponseSpectrum.GetDirComb", name)
        dm = _try(sap, "LoadCases.ResponseSpectrum.GetDampConstant", name)
        inv = {v: k for k, v in RS_COMB.items()}
        out.update(modal_case=str(m[0]) if m else None,
                   modal_combination=inv.get(int(mc[0]), mc[0]) if mc else None,
                   directional_combination={1: "srss", 2: "abs"}.get(int(dc[0]), dc[0]) if dc else None,
                   damping=round(float(dm[0]), 6) if dm else None)
    else:
        g = _try(sap, "LoadCases.ModHistLinear.GetLoads", name)
        if g:
            out["loads"] = [{"type": str(t), "name": str(n), "function": str(f), "factor": round(float(sf), 9)}
                            for t, n, f, sf in zip(as_list(g[1]), as_list(g[2]), as_list(g[3]), as_list(g[4]))]
        if software == "SAP2000":
            ts = _try(sap, "LoadCases.ModHistLinear.GetTimeStep", name)
            m = _try(sap, "LoadCases.ModHistLinear.GetModalCase", name)
            dm = _try(sap, "LoadCases.ModHistLinear.GetDampConstant", name)
            out.update(steps=int(ts[0]) if ts else None, step_size=float(ts[1]) if ts else None,
                       modal_case=str(m[0]) if m else None, damping=round(float(dm[0]), 6) if dm else None)
        else:
            from db_edit import read_table
            _, _, rows = read_table(sap, "Load Case Definitions - Time History - Linear Modal")
            r = next((x for x in rows if x.get("Name") == name and x.get("NumSteps")), None)
            if r:
                out.update(steps=int(float(r["NumSteps"])), step_size=float(r["StepSize"]),
                           modal_case=r.get("ModalCase"))
    return out


@action("define_load_case", "Create or change a load case: linear or nonlinear static (load patterns with scale "
        "factors; nonlinear: geometric nonlinearity, initial case; for gap / hook / multi-linear links), modal "
        "eigen or modal Ritz (number of modes; Ritz starting vectors)[[ES:, response spectrum (functions, directions, "
        "modal case, modal / directional combination) or linear modal time history (functions, steps)]]. Values "
        "from the user. Answers with the case read back.", "Definitions", "edit", LoadCaseP,
        options={"type": {"response_spectrum": "ES", "linear_modal_history": "ES"},
                 "geometric_nonlinearity": {"p_delta_large_displacements": "S"}},
        fields={"modal_case": "ES", "modal_combination": "ES", "directional_combination": "ES", "damping": "S",
                "steps": "ES", "step_size": "ES"})
def define_load_case(p: LoadCaseP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    existed = _check_new(sap, "LoadCases", p.name, p.replace, "Load case")
    pats = bridge.try_name_list(sap, ["LoadPatterns"])[0] or []
    missing = [ld.name for ld in p.loads or [] if ld.kind == "load_pattern" and ld.name not in pats]
    if missing:
        raise CSIValidationError(f"Load patterns not defined: {missing}.", hint=f"Defined: {pats[:30]}")
    dyn = p.type in ("response_spectrum", "linear_modal_history")
    if dyn:
        funcs = _functions(sap)
        nofunc = sorted({ld.function for ld in p.loads or [] if ld.function not in funcs})
        if nofunc:
            raise CSIValidationError(f"Functions not defined: {nofunc}.",
                                     hint=f"Defined: {funcs[:30]}; define_function makes one.")
    plan = {"case": p.name, "type": p.type, "existed": existed,
            "loads": [ld.model_dump(exclude_none=True) for ld in p.loads or []] or None}
    if p.type in ("modal_eigen", "modal_ritz"):
        plan.update(max_modes=p.max_modes, min_modes=p.min_modes)
    if p.type == "response_spectrum":
        plan.update(modal_combination=p.modal_combination, directional_combination=p.directional_combination)
    if p.type == "linear_modal_history":
        plan.update(steps=p.steps, step_size=p.step_size)
    if p.type == "nonlinear_static":
        plan.update(geometric_nonlinearity=p.geometric_nonlinearity, initial_case=p.initial_case,
                    load_control=p.load_control, save_steps=p.save_steps)
        if p.load_control == "displacement":
            plan.update(monitor_point=p.monitor_point, monitor_dof=p.monitor_dof,
                        target_displacement=p.target_displacement)
            pts = bridge.try_name_list(sap, ["PointObj"])[0] or []
            if p.monitor_point not in pts:
                raise CSIValidationError(f"Monitor point '{p.monitor_point}' is not in the model.")
        if p.initial_case:
            have = bridge.try_name_list(sap, ["LoadCases"])[0] or []
            if p.initial_case not in have:
                raise CSIValidationError(f"Initial case '{p.initial_case}' is not defined.", hint=f"Cases: {have[:30]}")
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_case", "define a load case")
    lc = sap.LoadCases
    kinds = ["Load" if ld.kind == "load_pattern" else "Accel" for ld in p.loads or []]
    names = [ld.name if ld.kind == "load_pattern" else ld.name.upper() for ld in p.loads or []]
    extra: dict[str, Any] = {}
    if p.type == "linear_static":
        api_call("LoadCases.StaticLinear.SetCase", lc.StaticLinear.SetCase, p.name)
        api_call("LoadCases.StaticLinear.SetLoads", lc.StaticLinear.SetLoads, p.name, len(names), kinds, names,
                 [ld.factor for ld in p.loads or []])
    elif p.type == "nonlinear_static":
        nl = lc.StaticNonlinear
        api_call("LoadCases.StaticNonlinear.SetCase", nl.SetCase, p.name)
        api_call("LoadCases.StaticNonlinear.SetLoads", nl.SetLoads, p.name, len(names), kinds, names,
                 [ld.factor for ld in p.loads or []])
        api_call("LoadCases.StaticNonlinear.SetGeometricNonlinearity", nl.SetGeometricNonlinearity, p.name,
                 {"none": 0, "p_delta": 1, "p_delta_large_displacements": 2}[p.geometric_nonlinearity])
        if p.initial_case:
            api_call("LoadCases.StaticNonlinear.SetInitialCase", nl.SetInitialCase, p.name, p.initial_case)
        dof = ["U1", "U2", "U3", "R1", "R2", "R3"].index(p.monitor_dof) + 1
        if p.load_control == "displacement":   # (full load is the program's default: SetLoadApplication with
            with bridge.units_scope(sap, p.units):  # LoadControl 1 and no monitor point returns 1 - measured)
                # LoadControl 2 = displacement; DispType 2 = monitored; Monitor 1 = a point DOF
                api_call("LoadCases.StaticNonlinear.SetLoadApplication", nl.SetLoadApplication, p.name, 2, 2,
                         float(p.target_displacement or 0.0), 1, dof, p.monitor_point or "", "")
        if p.save_steps:
            api_call("LoadCases.StaticNonlinear.SetResultsSaved", nl.SetResultsSaved, p.name, True,
                     p.min_saved_states, p.max_saved_states, True)
        la = _try(sap, "LoadCases.StaticNonlinear.GetLoadApplication", p.name, 0, 0, 0.0, 0, 0, "", "")
        if la:
            extra["load_application_read_back"] = {"load_control": int(la[0]), "displacement": float(la[2]),
                                                   "dof": int(la[4]), "point": str(la[5])}
    elif p.type == "modal_eigen":
        api_call("LoadCases.ModalEigen.SetCase", lc.ModalEigen.SetCase, p.name)
        api_call("LoadCases.ModalEigen.SetNumberModes", lc.ModalEigen.SetNumberModes, p.name, p.max_modes,
                 p.min_modes)
    elif p.type == "modal_ritz":
        api_call("LoadCases.ModalRitz.SetCase", lc.ModalRitz.SetCase, p.name)
        api_call("LoadCases.ModalRitz.SetNumberModes", lc.ModalRitz.SetNumberModes, p.name, p.max_modes, p.min_modes)
        api_call("LoadCases.ModalRitz.SetLoads", lc.ModalRitz.SetLoads, p.name, len(names), kinds, names,
                 [0] * len(names), [99.0] * len(names))
    else:
        extra = _dynamic_case(sap, p)
    back = read_load_cases(sap, [p.name])
    rb = back[0] if back else None
    if dyn and rb is not None:
        rb.update(read_dynamic_case(sap, p.software, p.name, p.type))
    return _finish(p, plan, rb, was_locked, snap, **extra)


# --------------------------------------------------------------------------- functions

class FunctionP(_DefP):
    kind: Literal["response_spectrum", "time_history", "steady_state", "power_spectral_density"] = Field(
        description="response_spectrum | time_history | steady_state | power_spectral_density")
    points: list[list[float]] = Field(min_length=2, max_length=100000, description=(
        "[[period, value], ...] (response spectrum; value e.g. in g), [[time, value], ...] (time history) or "
        "[[frequency (Hz), value], ...] (steady state / power spectral density), increasing; from the user"))
    damping_ratio: float = Field(default=0.05, ge=0, lt=1, description=(
        "response spectrum: the damping ratio the spectrum is for (default 0.05)"))

    @model_validator(mode="after")
    def _check(self) -> "FunctionP":
        if any(len(pt) != 2 for pt in self.points):
            raise ValueError("each point is [period or time, value]")
        xs = [pt[0] for pt in self.points]
        if any(b <= a for a, b in zip(xs, xs[1:])) or xs[0] < 0:
            raise ValueError("periods / times must start at 0 or more and increase")
        return self


def read_function(sap: Any, name: str) -> dict[str, Any] | None:
    g = _try(sap, "Func.GetValues", name)
    if not g:
        return None
    n = int(g[0])
    xs, ys = [float(v) for v in as_list(g[1])], [float(v) for v in as_list(g[2])]
    if len(xs) == n + 1:            # ETABS returns one leading extra entry (measured 26 Nov)
        xs, ys = xs[1:], ys[1:]
    return {"name": name, "points": [[round(x, 9), round(y, 9)] for x, y in zip(xs, ys)]}


@action("define_function", "Create or replace a user response-spectrum function (period, value; damping ratio), "
        "time-history function (time, value), or steady-state / power-spectral-density function (frequency, value) "
        "from the user's points. Answers with the function read back from the "
        "program.", "Definitions", "edit", FunctionP, programs="ES")
def define_function(p: FunctionP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    existed = p.name in _functions(sap)
    if existed and not p.replace:
        raise CSIValidationError(f"Function '{p.name}' already exists.",
                                 hint="Use another name, or replace: true to change the existing one.")
    plan = {"function": p.name, "kind": p.kind, "points": len(p.points), "existed": existed}
    if p.kind == "response_spectrum":
        plan["damping_ratio"] = p.damping_ratio
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_function", "define a function")
    xs, ys = [pt[0] for pt in p.points], [pt[1] for pt in p.points]
    extra: dict[str, Any] = {}
    if p.software == "SAP2000":
        if p.kind == "response_spectrum":
            api_call("Func.FuncRS.SetUser", sap.Func.FuncRS.SetUser, p.name, len(xs), xs, ys, p.damping_ratio)
        elif p.kind == "time_history":
            api_call("Func.FuncTH.SetUser", sap.Func.FuncTH.SetUser, p.name, len(xs), xs, ys)
        elif p.kind == "steady_state":
            api_call("Func.FuncSS.SetUser", sap.Func.FuncSS.SetUser, p.name, len(xs), xs, ys)
        else:
            api_call("Func.FuncPSD.SetUser", sap.Func.FuncPSD.SetUser, p.name, len(xs), xs, ys)
    else:
        from db_edit import edit_table
        if p.kind == "response_spectrum":
            key = "Functions - Response Spectrum - User Defined"
            rows = [{"Name": p.name, "Period": repr(x), "Value": repr(y),
                     "DampRatio": repr(p.damping_ratio) if i == 0 else ""} for i, (x, y) in enumerate(zip(xs, ys))]
        elif p.kind == "time_history":
            key = "Functions - Time History - User Defined"
            rows = [{"Name": p.name, "Time": repr(x), "Value": repr(y)} for x, y in zip(xs, ys)]
        else:   # measured 28 Nov (ETABS 23.2): fields Name, Frequency, Value
            key = ("Functions - Steady State - User Defined" if p.kind == "steady_state"
                   else "Functions - Power Spectral Density - User Defined")
            rows = [{"Name": p.name, "Frequency": repr(x), "Value": repr(y)} for x, y in zip(xs, ys)]
        extra["table_edit"] = edit_table(sap, key, rows, drop=lambda r: r.get("Name") == p.name)
    back = read_function(sap, p.name)
    if back is None or len(back["points"]) != len(p.points):
        raise CSIValidationError(f"Function '{p.name}' read back with {len(back['points']) if back else 0} points, "
                                 f"not {len(p.points)}.", hint="Check the values; nothing else was changed.")
    return _finish(p, plan, back, was_locked, snap, **extra)


# --------------------------------------------------------------------------- combinations

class ComboItem(Params):
    name: str = Field(description="a load case or a combination")
    factor: float = Field(default=1.0, description="scale factor")
    kind: Literal["auto", "case", "combination"] = Field(default="auto", description=(
        "auto (default: found among the cases or the combinations) | case | combination"))


class ComboP(_DefP):
    type: Literal["linear_add", "envelope", "absolute_add", "srss", "range_add"] = Field(default="linear_add",
        description="linear_add (default) | envelope | absolute_add | srss | range_add")
    items: list[ComboItem] = Field(min_length=1, max_length=500, description=(
        "the cases / combinations with their factors, from the user (no default design combinations)"))


@action("define_combination", "Create or replace a load combination: type (linear add, envelope, absolute add, SRSS, "
        "range) and the cases / combinations with the user's factors. Answers with the combination read back.",
        "Definitions", "edit", ComboP)
def define_combination(p: ComboP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    existed = _check_new(sap, "RespCombo", p.name, p.replace, "Combination")
    cases = set(bridge.try_name_list(sap, ["LoadCases"])[0] or [])
    combos = set(bridge.try_name_list(sap, ["RespCombo"])[0] or [])
    resolved = []
    for it in p.items:
        if it.name == p.name:
            raise CSIValidationError("A combination cannot contain itself.")
        in_case, in_combo = it.name in cases, it.name in combos
        kind = it.kind
        if kind == "auto":
            if in_case and in_combo:
                raise CSIValidationError(f"'{it.name}' is both a case and a combination.",
                                         hint="Give kind: case or kind: combination for it.")
            kind = "case" if in_case else "combination" if in_combo else ""
        if not kind or (kind == "case" and not in_case) or (kind == "combination" and not in_combo):
            raise CSIValidationError(f"'{it.name}' is not a defined {'load case or combination' if it.kind == 'auto' else it.kind}.",
                                     hint="get_load_case_details / get_combo_details list them.")
        resolved.append((kind, it.name, it.factor))
    plan = {"combination": p.name, "type": p.type, "existed": existed,
            "items": [{"kind": k, "name": n, "factor": f} for k, n, f in resolved]}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_combo", "define a combination")
    rc = sap.RespCombo
    if existed:
        api_call("RespCombo.Delete", rc.Delete, p.name)
    api_call("RespCombo.Add", rc.Add, p.name, COMBO_CODES[p.type])
    for kind, n, f in resolved:
        api_call("RespCombo.SetCaseList", rc.SetCaseList, p.name, 0 if kind == "case" else 1, n, f)
    back = read_combos(sap, [p.name])
    if back and back[0].get("type") != COMBO_TYPES[COMBO_CODES[p.type]]:
        raise CSIValidationError(f"Combination type read back as {back[0].get('type')}, not {p.type}.")
    return _finish(p, plan, back[0] if back else None, was_locked, snap)


# --------------------------------------------------------------------------- mass source

class MassLoad(Params):
    pattern: str = Field(description="a load pattern")
    factor: float = Field(default=1.0, gt=0, description="multiplier")


class MassSourceP(Params):
    software: Software = SOFTWARE
    name: str | None = Field(default=None, description=(
        "mass source name; default the program's default mass source"))
    from_elements: bool = Field(default=True, description="mass from the elements' own mass (materials)")
    from_masses: bool = Field(default=True, description="additional masses assigned to objects")
    from_loads: bool = Field(default=False, description="mass from load patterns (loads below)")
    loads: list[MassLoad] | None = Field(default=None, max_length=100, description=(
        "from_loads: the load patterns with multipliers, from the user"))
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT

    @model_validator(mode="after")
    def _check(self) -> "MassSourceP":
        if not (self.from_elements or self.from_masses or self.from_loads):
            raise ValueError("choose at least one of from_elements, from_masses, from_loads")
        if self.from_loads and not self.loads:
            raise ValueError("from_loads needs loads")
        if self.loads and not self.from_loads:
            raise ValueError("loads given but from_loads is false")
        return self


@action("set_mass_source", "Set the mass source: element self mass, additional masses and / or load patterns with "
        "multipliers (values from the user)[[S:; a named mass source becomes the default]]. Answers with the mass "
        "source read back.", "Definitions", "edit", MassSourceP, fields={"name": "S"})
def set_mass_source(p: MassSourceP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    pats = bridge.try_name_list(sap, ["LoadPatterns"])[0] or []
    missing = [m.pattern for m in p.loads or [] if m.pattern not in pats]
    if missing:
        raise CSIValidationError(f"Load patterns not defined: {missing}.", hint=f"Defined: {pats[:30]}")
    names = [m.pattern for m in p.loads or []]
    factors = [m.factor for m in p.loads or []]
    if p.software == "SAP2000" and p.from_masses and not p.from_elements:
        # measured 28 Nov 2026 (SAP2000 27.1): SetMassSource with elements off and masses on returns 0 and reads back
        # masses off too - the program takes element self mass and additional mass as one option
        raise CSIValidationError("SAP2000 takes element self mass and additional masses together: additional masses "
                                 "alone are not kept.",
                                 hint="Give from_elements and from_masses both true; to leave the elements' own mass "
                                      "out, give their sections a mass modifier of 0 (define_frame_section modifiers).")
    target = p.name
    if p.software == "SAP2000" and not target:
        d = _try(sap, "SourceMass.GetDefault")
        target = str(d[0]) if d and d[0] else "MSSSRC1"
    plan = {"mass_source": target or "(model)", "from_elements": p.from_elements, "from_masses": p.from_masses,
            "from_loads": p.from_loads, "loads": [m.model_dump() for m in p.loads or []] or None}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    snap, was_locked = _start_edit(p, sap, "before_mass_source", "set the mass source")
    if p.software == "SAP2000":
        api_call("SourceMass.SetMassSource", sap.SourceMass.SetMassSource, target, p.from_elements, p.from_masses,
                 p.from_loads, True, len(names), names, factors)
    else:
        api_call("PropMaterial.SetMassSource_1", sap.PropMaterial.SetMassSource_1, p.from_elements, p.from_masses,
                 p.from_loads, len(names), names, factors)
    back = read_mass_source(sap, p.software)["mass_sources"]
    if target:
        back = [m for m in back if m.get("name") == target] or back
    rb = back[0] if back else None
    out = _finish(p, plan, rb, was_locked, snap)
    diff = {k: [getattr(p, k), rb.get(k)] for k in ("from_elements", "from_masses", "from_loads")
            if rb is not None and rb.get(k) != getattr(p, k)}
    if diff:
        out.update(not_kept_as_given=diff, message="The program kept a different mass source than given.")
        raise ActionFailed(out)
    return out
