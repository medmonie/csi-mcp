"""Phase 8.4 (0.22.0): Section Designer and non-prismatic frame sections.

Non-prismatic (variable) sections: PropFrame.SetNonPrismatic / GetNonPrismatic, typed in ETABS 23.2, SAP2000 27.1 and
SAFE 23.3. Each segment goes from a start section to an end section over a length (variable = a share of what the
absolute segments leave, absolute = a length), with the variation of EI33 and EI22 along it (linear, parabolic, cubic:
the 1st, 2nd or 3rd root of EI varies linearly; area and shear areas vary linearly).

Section Designer: only SAP2000 27.1 has the shape setters (PropFrameSDShape.Set*). In ETABS 23.2 SetSDSection returns
1 and there are no shape setters (measured), but the program's tables 'Frame Section Property Definitions - Section
Designer' and 'Section Designer Shapes - ...' are importable: the section row and its shapes are imported together in
ONE ApplyEditedTables (a shape imported apart from its section row is refused on 'Section Name' - measured). SAFE 23.3
has neither setters nor these tables: its Section Designer sections are read back only. Shapes, sizes, materials and
bars are the designer's (D1).
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import Params, _dry, action, bridge
from csi_bridge import CSIValidationError, api_call, as_list, describe_units
from db_edit import read_table, write_tables
from model_read import _try
from modelling import _start_edit
from objects import _EditP

EI_VAR = {"linear": 1, "parabolic": 2, "cubic": 3}
LEN_TYPE = {"variable": 1, "absolute": 2}
DESIGN_TYPE = {"no_design": 0, "general_steel": 1, "concrete_column_check": 2, "concrete_column_design": 3}
# GetSDSection MyType (CSI API help)
SD_TYPES = {1: "i_section", 2: "channel", 3: "tee", 4: "angle", 5: "double_angle", 6: "tube", 7: "pipe", 8: "plate",
            101: "solid_rect", 102: "solid_circle", 103: "solid_segment", 104: "solid_sector", 201: "polygon",
            301: "rebar_single", 302: "rebar_line", 303: "rebar_rectangle", 304: "rebar_circle", 401: "reference_line",
            402: "reference_circle"}
PROPS = ("area", "as2", "as3", "torsion", "i22", "i33", "s22", "s33", "z22", "z33", "r22", "r33")


def _inv(d: dict[str, int], v: Any) -> Any:
    r = {b: a for a, b in d.items()}
    try:
        return r.get(int(v), v)
    except (TypeError, ValueError):
        return v


def _f(v: Any) -> float:
    return float(f"{float(v):.10g}")


def section_props(sap: Any, name: str) -> dict[str, float] | None:
    g = _try(sap, "PropFrame.GetSectProps", name)
    return {k: _f(v) for k, v in zip(PROPS, g)} if g else None


# --------------------------------------------------------------------------- non-prismatic

def read_nonprismatic(sap: Any, name: str) -> list[dict[str, Any]] | None:
    g = _try(sap, "PropFrame.GetNonPrismatic", name, 0, [], [], [], [], [], [], 0, "", "") or \
        _try(sap, "PropFrame.GetNonPrismatic", name)
    if not g or int(g[0]) < 1:
        return None
    n = int(g[0])
    starts, ends, lens, types, e33, e22 = (as_list(g[i]) for i in range(1, 7))
    return [{"start_section": str(starts[i]), "end_section": str(ends[i]), "length": _f(lens[i]),
             "length_type": _inv(LEN_TYPE, types[i]), "ei33": _inv(EI_VAR, e33[i]), "ei22": _inv(EI_VAR, e22[i])}
            for i in range(n)]


class Segment(Params):
    start_section: str = Field(description="section at the segment start (an existing prismatic section)")
    end_section: str = Field(description="section at the segment end")
    length: float = Field(gt=0, description=(
        "absolute: the length (length units); variable: a relative weight - the variable segments share what the "
        "absolute segments leave of the frame length in proportion to these values"))
    length_type: Literal["variable", "absolute"] = Field(description="variable | absolute")
    ei33: Literal["linear", "parabolic", "cubic"] = Field(description=(
        "variation of EI33 along the segment: linear (EI), parabolic (EI^1/2) or cubic (EI^1/3) varies linearly - "
        "cubic is exact for a rectangle of linearly varying depth"))
    ei22: Literal["linear", "parabolic", "cubic"] = Field(description="variation of EI22 along the segment")


class NonPrismaticP(_EditP):
    name: str = Field(min_length=1, description="the non-prismatic section")
    segments: list[Segment] = Field(min_length=1, max_length=200, description="segments from the I end to the J end")
    replace: bool = Field(default=False, description="change an existing section")


@action("define_nonprismatic_section", "Create or change a non-prismatic (variable) frame section: segments from the "
        "I end to the J end, each from a start to an end section over an absolute or variable length, with the "
        "variation of EI33 / EI22 (linear, parabolic, cubic) - values from the designer. Assign it with "
        "assign_frame_properties. Answers with the segments read back.", "Definitions", "edit", NonPrismaticP)
def define_nonprismatic_section(p: NonPrismaticP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    secs = bridge.try_name_list(sap, ["PropFrame"])[0] or []
    if p.name in secs and not p.replace:
        raise CSIValidationError(f"Frame section '{p.name}' exists.", hint="replace: true changes it.")
    used = {s.start_section for s in p.segments} | {s.end_section for s in p.segments}
    missing = sorted(used - set(secs))
    if missing:
        raise CSIValidationError(f"Sections not defined: {missing}.", hint=f"Defined: {secs[:40]}")
    if p.name in used:
        raise CSIValidationError("A non-prismatic section cannot use itself as a segment section.")
    for s in sorted(used):
        t = _try(sap, "PropFrame.GetTypeOAPI", s)
        if t and int(t[0]) == 14:
            raise CSIValidationError(f"Segment section '{s}' is itself non-prismatic.")
    if not any(s.length_type == "variable" for s in p.segments):
        note = "All segments are absolute: their total must equal the length of the frames they are assigned to."
    else:
        note = None
    plan = {"section": p.name, "segments": [s.model_dump() for s in p.segments], "units": describe_units(p.units),
            "existed": p.name in secs, **({"note": note} if note else {})}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_nonprismatic", "define a non-prismatic section")
        seg = p.segments
        api_call("PropFrame.SetNonPrismatic", sap.PropFrame.SetNonPrismatic, p.name, len(seg),
                 [s.start_section for s in seg], [s.end_section for s in seg], [s.length for s in seg],
                 [LEN_TYPE[s.length_type] for s in seg], [EI_VAR[s.ei33] for s in seg],
                 [EI_VAR[s.ei22] for s in seg], -1, "", "")
        back = read_nonprismatic(sap, p.name)
    if not back or len(back) != len(seg):
        raise CSIValidationError(f"The program did not keep the segments of '{p.name}'.", hint=f"Read back: {back}")
    diff = []
    for i, (s, b) in enumerate(zip(seg, back), 1):
        for k, v in s.model_dump().items():
            if k == "length":
                if abs(float(b[k]) - v) > 1e-6 * max(1.0, v):
                    diff.append({"segment": i, k: (v, b[k])})
            elif str(b[k]) != str(v):
                diff.append({"segment": i, k: (v, b[k])})
    out = {**plan, "read_back": back, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if diff:
        out["not_kept_as_given"] = diff
    return out


# --------------------------------------------------------------------------- Section Designer

def _shape_detail(sap: Any, name: str, shape: str, kind: str) -> dict[str, Any]:
    """One shape's data through its getter (getters exist in E, S and F for the main shapes)."""
    def g(meth: str) -> list[Any] | None:
        return _try(sap, f"PropFrame.SDShape.{meth}", name, shape)
    if kind == "solid_rect":
        o = g("GetSolidRect")
        if o:
            return {"material": str(o[0]), "center": [_f(o[3]), _f(o[4])], "height": _f(o[5]), "width": _f(o[6]),
                    "rotation": _f(o[7]), "reinforced": bool(o[8]), "rebar_material": str(o[9])}
    if kind == "solid_circle":
        o = g("GetSolidCircle")
        if o:
            return {"material": str(o[0]), "center": [_f(o[3]), _f(o[4])], "diameter": _f(o[5]),
                    "reinforced": bool(o[6]), "bars": int(o[7]), "rotation": _f(o[8]), "cover": _f(o[9]),
                    "bar_size": str(o[10]), "rebar_material": str(o[11])}
    if kind == "polygon":
        o = g("GetPolygon")
        if o:
            xs, ys = as_list(o[3]), as_list(o[4])
            return {"material": str(o[0]), "points": [[_f(x), _f(y)] for x, y in zip(xs, ys)],
                    "reinforced": bool(o[7]), "rebar_material": str(o[8])}
    if kind in ("i_section",):
        o = g("GetISection")
        if o:
            return {"material": str(o[0]), "center": [_f(o[3]), _f(o[4])], "height": _f(o[5]), "width": _f(o[6]),
                    "flange_thickness": _f(o[7]), "web_thickness": _f(o[8]), "bottom_width": _f(o[9]),
                    "bottom_flange_thickness": _f(o[10]), "rotation": _f(o[11])}
    if kind in ("tee", "angle", "channel"):
        o = g({"tee": "GetTee", "angle": "GetAngle", "channel": "GetChannel"}[kind])
        if o:
            return {"material": str(o[0]), "center": [_f(o[3]), _f(o[4])], "height": _f(o[5]), "width": _f(o[6]),
                    "flange_thickness": _f(o[7]), "web_thickness": _f(o[8]), "rotation": _f(o[9])}
    if kind == "tube":
        o = g("GetTube")
        if o:
            return {"material": str(o[0]), "center": [_f(o[3]), _f(o[4])], "height": _f(o[5]), "width": _f(o[6]),
                    "flange_thickness": _f(o[7]), "web_thickness": _f(o[8]), "rotation": _f(o[9])}
    if kind == "pipe":
        o = g("GetPipe")
        if o:
            return {"material": str(o[0]), "center": [_f(o[3]), _f(o[4])], "diameter": _f(o[5]),
                    "thickness": _f(o[6])}
    if kind == "plate":
        o = g("GetPlate")
        if o:
            return {"material": str(o[0]), "center": [_f(o[2]), _f(o[3])], "thickness": _f(o[4]), "width": _f(o[5]),
                    "rotation": _f(o[6])}
    if kind == "rebar_single":
        o = g("GetReinfSingle")
        if o:
            return {"center": [_f(o[0]), _f(o[1])], "bar_size": str(o[2]), "rebar_material": str(o[3])}
    if kind == "rebar_line":
        o = g("GetReinfLine")
        if o:
            return {"start": [_f(o[0]), _f(o[1])], "end": [_f(o[2]), _f(o[3])], "spacing": _f(o[4]),
                    "bar_size": str(o[5]), "end_bars": bool(o[6]), "rebar_material": str(o[7])}
    if kind == "rebar_circle":
        o = g("GetReinfCircle")
        if o:
            return {"center": [_f(o[0]), _f(o[1])], "diameter": _f(o[2]), "bars": int(o[3]), "rotation": _f(o[4]),
                    "bar_size": str(o[5]), "rebar_material": str(o[6])}
    return {}


def read_sd(sap: Any, name: str) -> dict[str, Any] | None:
    o = _try(sap, "PropFrame.GetSDSection", name, "", 0, [], [], 0, 0, "", "") or \
        _try(sap, "PropFrame.GetSDSection", name)
    if not o:
        return None
    shapes, types = as_list(o[2]), as_list(o[3])
    out: dict[str, Any] = {"material": str(o[0]), "design_type": _inv(DESIGN_TYPE, o[4]), "shapes": []}
    for s, t in zip(shapes, types):
        kind = SD_TYPES.get(int(t), f"type {t}")
        rec = {"name": str(s), "shape": kind, **_shape_detail(sap, name, str(s), kind)}
        if kind in ("solid_rect", "polygon") and rec.get("reinforced"):
            e = _try(sap, "PropFrame.SDShape.GetReinfEdge", name, str(s))
            if e and int(e[0]):
                rec["edge_bars"] = [{"edge": int(a), "bar_size": str(b), "spacing": _f(c), "cover": _f(d)} for a, b, c, d
                                    in zip(*(as_list(e[i]) for i in range(1, 5)))]
            c = _try(sap, "PropFrame.SDShape.GetReinfCorner", name, str(s))
            if c and int(c[0]):
                rec["corner_bars"] = [{"corner": int(a), "bar_size": str(b)} for a, b in
                                      zip(as_list(c[1]), as_list(c[2]))]
        out["shapes"].append(rec)
    return out


ShapeKind = Literal["solid_rect", "solid_circle", "polygon", "i_section", "tee", "angle", "channel", "tube", "pipe",
                    "plate", "rebar_single", "rebar_line", "rebar_circle"]
NEEDS = {"solid_rect": ("center", "height", "width"), "solid_circle": ("center", "diameter"), "polygon": ("points",),
         "i_section": ("center", "height", "width", "flange_thickness", "web_thickness"),
         "tee": ("center", "height", "width", "flange_thickness", "web_thickness"),
         "angle": ("center", "height", "width", "flange_thickness", "web_thickness"),
         "channel": ("center", "height", "width", "flange_thickness", "web_thickness"),
         "tube": ("center", "height", "width", "flange_thickness", "web_thickness"),
         "pipe": ("center", "diameter", "thickness"), "plate": ("center", "thickness", "width"),
         "rebar_single": ("center", "bar_size"), "rebar_line": ("start", "end", "spacing", "bar_size"),
         "rebar_circle": ("center", "diameter", "bars", "bar_size")}


class EdgeBars(Params):
    bar_size: str = Field(description="bar size as the program names it")
    spacing: float = Field(gt=0, description="maximum bar spacing along every edge")
    cover: float = Field(gt=0, description="clear cover to the bars")


class SDShape(Params):
    shape: ShapeKind = Field(description=(
        "solid_rect | solid_circle | polygon | i_section | tee | angle | channel | tube | pipe | plate | rebar_single "
        "| rebar_line | rebar_circle"))
    material: str | None = Field(default=None, description="shape material (default: the section's material)")
    center: list[float] | None = Field(default=None, min_length=2, max_length=2, description=(
        "[x, y] of the shape centre: x along the section's local 2 axis, y along local 3"))
    height: float | None = Field(default=None, gt=0, description="depth (along y)")
    width: float | None = Field(default=None, gt=0, description="width (along x); flange width of steel shapes")
    flange_thickness: float | None = Field(default=None, gt=0, description="steel shapes / tube")
    web_thickness: float | None = Field(default=None, gt=0, description="steel shapes / tube")
    bottom_width: float | None = Field(default=None, gt=0, description="i_section bottom flange (default top)")
    bottom_flange_thickness: float | None = Field(default=None, gt=0, description="i_section (default top)")
    diameter: float | None = Field(default=None, gt=0, description="solid_circle / pipe / rebar_circle")
    thickness: float | None = Field(default=None, gt=0, description="pipe wall / plate")
    rotation: float = Field(default=0.0, description="rotation in degrees, counter-clockwise")
    points: list[list[float]] | None = Field(default=None, min_length=3, max_length=500, description=(
        "polygon: [[x, y], ...] corners in order"))
    reinforced: bool = Field(default=False, description="solid_rect / solid_circle / polygon: the shape carries bars")
    edge_bars: EdgeBars | None = Field(default=None, description="solid_rect / polygon: bars along every edge")
    corner_bars: str | None = Field(default=None, description="solid_rect / polygon: bar size at every corner")
    bars: int | None = Field(default=None, ge=1, le=500, description="solid_circle / rebar_circle: number of bars")
    cover: float | None = Field(default=None, gt=0, description="solid_circle: cover to the bars")
    bar_size: str | None = Field(default=None, description="bar size as the program names it")
    rebar_material: str | None = Field(default=None, description="rebar material for bars")
    start: list[float] | None = Field(default=None, min_length=2, max_length=2, description="rebar_line: [x, y]")
    end: list[float] | None = Field(default=None, min_length=2, max_length=2, description="rebar_line: [x, y]")
    spacing: float | None = Field(default=None, gt=0, description="rebar_line: bar spacing")
    end_bars: bool = Field(default=True, description="rebar_line: bars at both ends")

    @model_validator(mode="after")
    def _need(self) -> "SDShape":
        miss = [k for k in NEEDS[self.shape] if getattr(self, k) is None]
        if miss:
            raise ValueError(f"{self.shape} needs {miss}")
        if self.points and any(len(pt) != 2 for pt in self.points):
            raise ValueError("points are [x, y] pairs")
        if self.shape == "solid_circle" and self.reinforced and (not self.bars or not self.cover or not self.bar_size):
            raise ValueError("a reinforced solid_circle needs bars, cover and bar_size")
        if (self.edge_bars or self.corner_bars) and self.shape not in ("solid_rect", "polygon"):
            raise ValueError("edge_bars / corner_bars are for solid_rect and polygon")
        return self


class SDP(_EditP):
    name: str = Field(min_length=1, description="the Section Designer section")
    material: str = Field(description="the section's base material")
    design_type: Literal[tuple(DESIGN_TYPE)] = Field(description=(  # type: ignore[valid-type]
        "no_design | general_steel | concrete_column_check (the program checks the drawn bars) | "
        "concrete_column_design (it designs the bars)"))
    shapes: list[SDShape] = Field(min_length=1, max_length=300, description="the shapes and bars, drawn in order")
    replace: bool = Field(default=False, description="change an existing section (its shapes are all redrawn)")


def _add_shape(sd: Any, name: str, i: int, s: SDShape, mat: str) -> str:
    sh = f"MCP{i}"
    m = s.material or mat
    c = s.center or [0.0, 0.0]
    if s.shape == "solid_rect":
        o = api_call("SDShape.SetSolidRect", sd.SetSolidRect, name, sh, m, "", c[0], c[1], s.height, s.width,
                     s.rotation, -1, s.reinforced, s.rebar_material or "")
    elif s.shape == "solid_circle":
        o = api_call("SDShape.SetSolidCircle", sd.SetSolidCircle, name, sh, m, "", c[0], c[1], s.diameter, -1,
                     s.reinforced, s.bars or 8, s.rotation, s.cover or 0.05, s.bar_size or "", s.rebar_material or "")
    elif s.shape == "polygon":
        pts = s.points or []
        o = api_call("SDShape.SetPolygon", sd.SetPolygon, name, sh, m, "", len(pts), [q[0] for q in pts],
                     [q[1] for q in pts], [0.0] * len(pts), -1, s.reinforced, s.rebar_material or "")
    elif s.shape == "i_section":
        o = api_call("SDShape.SetISection", sd.SetISection, name, sh, m, "", c[0], c[1], s.rotation, -1, s.height,
                     s.width, s.flange_thickness, s.web_thickness, s.bottom_width or s.width,
                     s.bottom_flange_thickness or s.flange_thickness)
    elif s.shape in ("tee", "angle", "channel", "tube"):
        meth = {"tee": "SetTee", "angle": "SetAngle", "channel": "SetChannel", "tube": "SetTube"}[s.shape]
        o = api_call(f"SDShape.{meth}", getattr(sd, meth), name, sh, m, "", c[0], c[1], s.rotation, -1, s.height,
                     s.width, s.flange_thickness, s.web_thickness)
    elif s.shape == "pipe":
        o = api_call("SDShape.SetPipe", sd.SetPipe, name, sh, m, "", c[0], c[1], -1, s.diameter, s.thickness)
    elif s.shape == "plate":
        o = api_call("SDShape.SetPlate", sd.SetPlate, name, sh, m, c[0], c[1], s.rotation, -1, s.thickness, s.width)
    elif s.shape == "rebar_single":
        o = api_call("SDShape.SetReinfSingle", sd.SetReinfSingle, name, sh, c[0], c[1], s.bar_size,
                     s.rebar_material or "")
    elif s.shape == "rebar_line":
        o = api_call("SDShape.SetReinfLine", sd.SetReinfLine, name, sh, s.start[0], s.start[1], s.end[0], s.end[1],
                     s.spacing, s.bar_size, s.end_bars, s.rebar_material or "")
    else:  # rebar_circle
        o = api_call("SDShape.SetReinfCircle", sd.SetReinfCircle, name, sh, c[0], c[1], s.diameter, s.bars,
                     s.rotation, s.bar_size, s.rebar_material or "")
    got = str(o[0]) if o and isinstance(o[0], str) and o[0] else sh
    if s.edge_bars:
        api_call("SDShape.SetReinfEdge", sd.SetReinfEdge, name, got, 1, s.edge_bars.bar_size, s.edge_bars.spacing,
                 s.edge_bars.cover, True)
    if s.corner_bars:
        api_call("SDShape.SetReinfCorner", sd.SetReinfCorner, name, got, 1, s.corner_bars, True)
    return got


# --------------------------------------------------------------------------- ETABS: through the program's tables

SD_SECTION_TABLE = "Frame Section Property Definitions - Section Designer"
SD_SHAPE_PREFIX = "Section Designer Shapes - "
# 'Concrete Polygon' is left out: ETABS 23.2 refuses it with return code 1 and no log, leaves a broken shape, and the
# imports after it fail too (measured, 0.22.0)
ETABS_TABLE = {"solid_rect": "Concrete Rectangle", "solid_circle": "Concrete Circle",
               "i_section": "Steel I-Section", "tube": "Steel Tube", "pipe": "Steel Pipe", "plate": "Steel Plate",
               "rebar_single": "Reinforcing - Single Bar", "rebar_line": "Reinforcing - Line Bar",
               "rebar_circle": "Reinforcing - Circle Bar"}
ETABS_DESIGN = {"no_design": ("No Check/Design", ""), "general_steel": ("General Steel Section", ""),
                "concrete_column_check": ("Concrete Column", "No"), "concrete_column_design": ("Concrete Column", "Yes")}


def _n(v: float | None) -> str:
    return "" if v is None else repr(float(v))


def etabs_refusals(shapes: list[SDShape]) -> list[str]:
    out = []
    for i, s in enumerate(shapes, 1):
        if s.shape not in ETABS_TABLE:
            out.append(f"shape {i}: {s.shape} (ETABS takes solid_rect, solid_circle, i_section, tube, pipe, plate, "
                       "rebar_single, rebar_line, rebar_circle; its polygon import fails inside the program - draw "
                       "polygons in ETABS's Section Designer)")
        elif s.reinforced or s.edge_bars or s.corner_bars:
            out.append(f"shape {i}: bars inside a {s.shape} (in ETABS draw them as rebar_single / rebar_line / "
                       "rebar_circle shapes)")
    return out


def _etabs_rows(name: str, i: int, s: SDShape, mat: str) -> list[dict[str, str]]:
    base = {"SectionType": "Frame", "SectionName": name, "ShapeName": f"MCP{i}"}
    c = s.center or [0.0, 0.0]
    m = s.material or mat
    xy = {"XCenter": _n(c[0]), "YCenter": _n(c[1])}
    if s.shape == "solid_rect":
        return [{**base, "Material": m, **xy, "Rotation": _n(s.rotation), "Height": _n(s.height),
                 "Width": _n(s.width), "Reinforcing": "No"}]
    if s.shape == "solid_circle":
        return [{**base, "Material": m, **xy, "Diameter": _n(s.diameter), "Reinforcing": "No"}]
    if s.shape == "i_section":
        return [{**base, "Material": m, **xy, "Rotation": _n(s.rotation), "Height": _n(s.height),
                 "TopWidth": _n(s.width), "TopThick": _n(s.flange_thickness), "WebThick": _n(s.web_thickness),
                 "BotWidth": _n(s.bottom_width or s.width),
                 "BotThick": _n(s.bottom_flange_thickness or s.flange_thickness), "FilletRad": "0"}]
    if s.shape == "tube":
        return [{**base, "Material": m, **xy, "Rotation": _n(s.rotation), "Height": _n(s.height),
                 "FlangeWidth": _n(s.width), "FlangeThick": _n(s.flange_thickness), "WebThick": _n(s.web_thickness),
                 "CornerRad": "0"}]
    if s.shape == "pipe":
        return [{**base, "Material": m, **xy, "Diameter": _n(s.diameter), "Thickness": _n(s.thickness)}]
    if s.shape == "plate":
        return [{**base, "Material": m, **xy, "Rotation": _n(s.rotation), "Width": _n(s.width),
                 "Thickness": _n(s.thickness)}]
    rm = s.rebar_material or ""
    if s.shape == "rebar_single":
        return [{**base, "Material": rm, **xy, "RebarSize": s.bar_size or ""}]
    if s.shape == "rebar_line":
        return [{**base, "Material": rm, "X1": _n(s.start[0]), "Y1": _n(s.start[1]), "X2": _n(s.end[0]),
                 "Y2": _n(s.end[1]), "RebarSize": s.bar_size or "", "HasEndBars": "Yes" if s.end_bars else "No",
                 "LayoutType": "Spacing", "MaxSpacing": _n(s.spacing)}]
    return [{**base, "Material": rm, **xy, "Rotation": _n(s.rotation), "Diameter": _n(s.diameter),
             "NumberBars": str(s.bars), "RebarSize": s.bar_size or ""}]          # rebar_circle


def etabs_import_sd(sap: Any, p: "SDP") -> dict[str, Any]:
    o = api_call("DatabaseTables.GetAllTables", sap.DatabaseTables.GetAllTables)
    keys = [str(k) for k in as_list(o[1])]
    if SD_SECTION_TABLE not in keys:
        raise CSIValidationError("This program has no Section Designer tables to import into.")
    new: dict[str, list[dict[str, str]]] = {}
    for i, s in enumerate(p.shapes, 1):
        key = SD_SHAPE_PREFIX + ETABS_TABLE[s.shape]
        if key not in keys:
            raise CSIValidationError(f"Table '{key}' is not in this program.")
        new.setdefault(key, []).extend(_etabs_rows(p.name, i, s, p.material))
    touched = [SD_SECTION_TABLE] + sorted({k for k in keys if k.startswith(SD_SHAPE_PREFIX)} if p.replace
                                          else set(new))
    dt, designed = ETABS_DESIGN[p.design_type]
    items = []
    for key in touched:
        version, fields, rows = read_table(sap, key)
        if key == SD_SECTION_TABLE:
            kept = [r for r in rows if r.get("Name") != p.name]
            add = [{"Name": p.name, "Material": p.material, "DesignType": dt, "IsDesigned": designed}]
        else:
            kept = [r for r in rows if not (r.get("SectionName") == p.name and r.get("SectionType", "Frame")
                                            == "Frame")]
            add = new.get(key, [])
        if not kept and not add:
            continue
        unknown = sorted({f for r in add for f in r} - set(fields))
        if unknown:
            raise CSIValidationError(f"Table '{key}' has no fields {unknown}.", hint=f"Fields: {fields}")
        if not rows:          # an empty table: only the fields given; the program fills its defaults
            fields = [f for f in fields if any(f in r for r in add)]
        items.append((key, version, fields, kept + add))
    res = write_tables(sap, items)
    return {"tables": [k for k, *_ in items], **res}


@action("define_section_designer_section", "Create or change a Section Designer section in SAP2000 from the "
        "designer's shapes: solid rectangles, circles and polygons (optionally reinforced: edge / corner bars, "
        "circular bars), steel I / tee / angle / channel / tube / pipe / plate shapes and single, line or circular "
        "bars, at their coordinates (x along local 2, y along local 3), with the design type (none, general steel, "
        "concrete column check / design). Answers with the shapes and the section properties read back.[[E: ETABS "
        "takes concrete solid_rect and solid_circle (without bars inside), i_section, tube, pipe, plate and single / "
        "line / circular bars, through its Section Designer tables (draw polygons in the program).]]",
        "Definitions", "edit", SDP, programs="ES")
def define_section_designer_section(p: SDP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    secs = bridge.try_name_list(sap, ["PropFrame"])[0] or []
    if p.name in secs and not p.replace:
        raise CSIValidationError(f"Frame section '{p.name}' exists.", hint="replace: true redraws it.")
    mats = set(bridge.try_name_list(sap, ["PropMaterial"])[0] or [])
    need = {p.material} | {s.material for s in p.shapes if s.material} | \
        {s.rebar_material for s in p.shapes if s.rebar_material}
    missing = sorted(need - mats)
    if missing:
        raise CSIValidationError(f"Materials not defined: {missing}.", hint="define_material creates them.")
    sizes = set(bridge.try_name_list(sap, ["PropRebar"])[0] or [])
    bar_sizes = {s.bar_size for s in p.shapes if s.bar_size} | {s.corner_bars for s in p.shapes if s.corner_bars} | \
        {s.edge_bars.bar_size for s in p.shapes if s.edge_bars}
    unknown = sorted(bar_sizes - sizes) if sizes else []
    if unknown:
        raise CSIValidationError(f"Bar sizes not defined: {unknown}.", hint=f"Sizes: {sorted(sizes)[:40]}")
    for i, s in enumerate(p.shapes, 1):
        bars = s.shape.startswith("rebar_") or s.reinforced
        if bars and not s.rebar_material:
            raise CSIValidationError(f"Shape {i} ({s.shape}) has bars: give its rebar_material.")
    if p.software == "ETABS":
        bad = etabs_refusals(p.shapes)
        if bad:
            raise CSIValidationError("ETABS cannot take: " + "; ".join(bad[:10]))
        if p.name in secs:
            t = _try(sap, "PropFrame.GetTypeOAPI", p.name)
            if not t or int(t[0]) != 13:
                raise CSIValidationError(f"'{p.name}' is a frame section of another type; ETABS replaces Section "
                                         "Designer sections only through its tables.", hint="Use another name.")
    plan = {"section": p.name, "material": p.material, "design_type": p.design_type, "shapes": len(p.shapes),
            "units": describe_units(p.units), "existed": p.name in secs,
            **({"route": "the program's Section Designer tables (one import)"} if p.software == "ETABS" else {})}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    if p.software == "ETABS":
        with bridge.units_scope(sap, p.units):
            snap, was_locked = _start_edit(p, sap, "before_sd_section", "define a Section Designer section")
            imp = etabs_import_sd(sap, p)
            back = read_sd(sap, p.name)
            props = section_props(sap, p.name)
        if not back or len(back["shapes"]) != len(p.shapes):
            raise CSIValidationError(f"The program holds {len(back['shapes']) if back else 0} shapes in '{p.name}', "
                                     f"{len(p.shapes)} were given.", hint=f"Read back: {back}")
        note = ({"note": "ETABS 23.2 stores this column as 'checked' but its concrete frame design reports Section "
                         "Designer columns as designed (required As, no PMM ratio) - measured; SAP2000 checks them."}
                if p.design_type == "concrete_column_check" else {})
        return {**plan, "import": imp, "read_back": back, "properties": props, "model_was_unlocked": was_locked,
                **note, **({"snapshot": snap} if snap else {})}
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_sd_section", "define a Section Designer section")
        api_call("PropFrame.SetSDSection", sap.PropFrame.SetSDSection, p.name, p.material, DESIGN_TYPE[p.design_type],
                 -1, "", "")
        sd = sap.PropFrame.SDShape
        before = read_sd(sap, p.name)
        if before and before["shapes"]:
            api_call("SDShape.Delete", sd.Delete, p.name, "", True)
        names = [_add_shape(sd, p.name, i, s, p.material) for i, s in enumerate(p.shapes, 1)]
        back = read_sd(sap, p.name)
        props = section_props(sap, p.name)
    if not back or len(back["shapes"]) != len(p.shapes):
        raise CSIValidationError(f"The program holds {len(back['shapes']) if back else 0} shapes in '{p.name}', "
                                 f"{len(p.shapes)} were drawn.", hint=f"Read back: {back}")
    out = {**plan, "shape_names": names, "read_back": back, "properties": props, "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    if back.get("design_type") != p.design_type:
        out["not_kept_as_given"] = {"design_type": (p.design_type, back.get("design_type"))}
    return out
