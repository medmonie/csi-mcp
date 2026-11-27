r"""Phase 0 reference models, built through the raw API with the signatures from docs/api (gen_api_matrix.py).

kN, m, C. Loads and sizes are test values for exercising the MCP, not a design.

    SAP2000  2-D portal (2 storeys x 3 m, 2 bays x 6 m, New2DFrame) + a 4 x 4 m shell plate (SetShell_1, 200 mm,
             pinned corners); patterns DEAD (self weight) + LIVE; plate area loads; frame load on the beams;
             COMB1 = 1.4 DEAD, COMB2 = 1.2 DEAD + 1.6 LIVE.
    ETABS    3-storey steel deck building (NewSteelDeck, 3 x 3 grid at 6 m, storeys 4 + 3.5 + 3.5 m); Live 2 kN/m2
             on the deck slabs; COMB1 = 1.2 Dead + 1.6 Live. (A concrete wall is left to Phase 3, see build_etabs.)
    SAFE     12 x 12 m mat at the base level (4 panels of 6 x 6 m, SetSlab 500 mm, C30) on area soil springs (SetAreaSpringProp,
             20000 kN/m3 vertical, small lateral) with 9 column loads (500 kN Dead each) at the grid points;
             Live 3 kN/m2; COMB1 = 1.2 Dead + 1.6 Live.
"""
from __future__ import annotations

from typing import Any

from csi_bridge import api_call, as_list

KN_M_C = 6
GRAVITY = 10        # load direction: gravity (positive acts downward)


def _combo(sap: Any, name: str, parts: list[tuple[str, float]]) -> None:
    api_call("RespCombo.Add", sap.RespCombo.Add, name, 0)
    for case, factor in parts:
        api_call("RespCombo.SetCaseList", sap.RespCombo.SetCaseList, name, 0, case, factor)


def _area_name(outs: list[Any]) -> str:
    return next(str(o) for o in outs if isinstance(o, str))


def _add_area(sap: Any, xs: list[float], ys: list[float], zs: list[float], prop: str) -> str:
    outs = api_call("AreaObj.AddByCoord", sap.AreaObj.AddByCoord, len(xs), xs, ys, zs, "", prop, "", "Global")
    return _area_name(outs)


def _point_at(sap: Any, x: float, y: float, z: float, tol: float = 1e-3) -> str:
    """Name of the existing point at (x, y, z); a new point if there is none."""
    for name in as_list(api_call("PointObj.GetNameList", sap.PointObj.GetNameList)[1]):
        c = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, name, 0.0, 0.0, 0.0)
        if abs(c[0] - x) < tol and abs(c[1] - y) < tol and abs(c[2] - z) < tol:
            return str(name)
    outs = api_call("PointObj.AddCartesian", sap.PointObj.AddCartesian, float(x), float(y), float(z), "")
    return _area_name(outs)


def _area_points(sap: Any, area: str) -> list[str]:
    outs = api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, area)
    return [str(x) for x in as_list(outs[1])]


def build_sap2000(sap: Any) -> dict[str, Any]:
    api_call("InitializeNewModel", sap.InitializeNewModel, KN_M_C)
    api_call("File.New2DFrame", sap.File.New2DFrame, 0, 2, 3.0, 2, 6.0)
    api_call("PropMaterial.SetMaterial", sap.PropMaterial.SetMaterial, "C30", 2)
    api_call("PropArea.SetShell_1", sap.PropArea.SetShell_1, "SHELL200", 1, True, "C30", 0.0, 0.2, 0.2)
    plate = _add_area(sap, [20.0, 24.0, 24.0, 20.0], [0.0, 0.0, 4.0, 4.0], [0.0, 0.0, 0.0, 0.0], "SHELL200")
    for pt in _area_points(sap, plate):
        api_call("PointObj.SetRestraint", sap.PointObj.SetRestraint, pt, [True, True, True, False, False, False])
    patterns = [str(x) for x in as_list(api_call("LoadPatterns.GetNameList", sap.LoadPatterns.GetNameList)[1])]
    dead = patterns[0]
    api_call("LoadPatterns.Add", sap.LoadPatterns.Add, "LIVE", 3, 0.0, True)
    api_call("AreaObj.SetLoadUniform", sap.AreaObj.SetLoadUniform, plate, dead, 2.0, GRAVITY, True, "Global", 0)
    api_call("AreaObj.SetLoadUniform", sap.AreaObj.SetLoadUniform, plate, "LIVE", 3.0, GRAVITY, True, "Global", 0)
    beams = []
    for name in as_list(api_call("FrameObj.GetNameList", sap.FrameObj.GetNameList)[1]):
        p1, p2 = api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, name, "", "")[:2]
        z1 = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, p1, 0.0, 0.0, 0.0)[2]
        z2 = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, p2, 0.0, 0.0, 0.0)[2]
        if abs(z1 - z2) < 1e-6 and z1 > 0:
            beams.append(str(name))
            api_call("FrameObj.SetLoadDistributed", sap.FrameObj.SetLoadDistributed, name, "LIVE", 1, GRAVITY,
                     0.0, 1.0, 10.0, 10.0, "Global", True, True, 0)
    _combo(sap, "COMB1", [(dead, 1.4)])
    _combo(sap, "COMB2", [(dead, 1.2), ("LIVE", 1.6)])
    return {"dead": dead, "live": "LIVE", "combos": ["COMB1", "COMB2"], "areas": [plate], "beams": beams,
            "brace": ([0, 0, 0], [6, 0, 3])}


def build_etabs(sap: Any) -> dict[str, Any]:
    api_call("InitializeNewModel", sap.InitializeNewModel, KN_M_C)
    api_call("File.NewSteelDeck", sap.File.NewSteelDeck, 3, 3.5, 4.0, 3, 3, 6.0, 6.0)
    api_call("PropMaterial.SetMaterial", sap.PropMaterial.SetMaterial, "C30", 2)
    decks = [str(x) for x in as_list(api_call("AreaObj.GetNameList", sap.AreaObj.GetNameList)[1])]
    # A concrete wall added through the API (SetWall + AddByPoint on the grid joints) made this template unstable
    # (hundreds of metres at the wall mesh nodes / deck overhang corners, tests/diag_etabs_ref.py) on any grid line.
    # Walls are left to Phase 3 (wall / area creation with mesh and edge-restraint control).
    for deck in decks:
        api_call("AreaObj.SetLoadUniform", sap.AreaObj.SetLoadUniform, deck, "Live", 2.0, GRAVITY, True, "Global", 0)
    _combo(sap, "COMB1", [("Dead", 1.2), ("Live", 1.6)])
    return {"dead": "Dead", "live": "Live", "combos": ["COMB1"], "areas": decks,
            "brace": ([0, 12, 4], [6, 12, 7.5])}   # Story1 -> Story2 joints on grid line y = 12


def build_safe(sap: Any) -> dict[str, Any]:
    api_call("InitializeNewModel", sap.InitializeNewModel, KN_M_C)
    api_call("File.NewGridOnly", sap.File.NewGridOnly, 1, 3.0, 3.0, 3, 3, 6.0, 6.0)
    outs = api_call("Story.GetStories_2", sap.Story.GetStories_2)
    z = float(outs[0])   # base elevation: SAFE 23.3 refuses areas at the story level of a grid-only model (code 1)
    api_call("PropMaterial.SetMaterial", sap.PropMaterial.SetMaterial, "C30", 2)
    api_call("PropArea.SetSlab", sap.PropArea.SetSlab, "MAT500", 0, 1, "C30", 0.5)   # slab, thin shell
    api_call("PropAreaSpring.SetAreaSpringProp", sap.PropAreaSpring.SetAreaSpringProp, "SOIL", 100.0, 100.0,
             20000.0, 0, 1)
    panels = []
    for x0 in (0.0, 6.0):
        for y0 in (0.0, 6.0):
            a = _add_area(sap, [x0, x0 + 6, x0 + 6, x0], [y0, y0, y0 + 6, y0 + 6], [z] * 4, "MAT500")
            api_call("AreaObj.SetSpringAssignment", sap.AreaObj.SetSpringAssignment, a, "SOIL", 0)
            api_call("AreaObj.SetLoadUniform", sap.AreaObj.SetLoadUniform, a, "Live", 3.0, GRAVITY, True,
                     "Global", 0)
            panels.append(a)
    columns = sorted({p for a in panels for p in _area_points(sap, a)})
    for pt in columns:
        api_call("PointObj.SetLoadForce", sap.PointObj.SetLoadForce, pt, "Dead",
                 [0.0, 0.0, -500.0, 0.0, 0.0, 0.0], True, "Global", 0)
    _combo(sap, "COMB1", [("Dead", 1.2), ("Live", 1.6)])
    return {"dead": "Dead", "live": "Live", "combos": ["COMB1"], "areas": panels, "column_points": columns,
            "slab_z": z, "brace": ([0, 0, z], [6, 0, z])}


BUILDERS = {"SAP2000": build_sap2000, "ETABS": build_etabs, "SAFE": build_safe}
