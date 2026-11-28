r"""Diagnostic (roadmap 8.11): lateral bracing, T/C limits, design procedure, panel zones, point local axes, area edge
constraints, link deletion and the SelectObj calls in SAP2000, ETABS and SAFE. Output: tests\results\diag_811.txt"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_811_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish, show  # noqa: E402,I001

targets = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or ["SAP2000", "ETABS", "SAFE"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}


def raw(label, fn):
    try:
        r = com(fn)
        print(f"  {label}: {r!r}"[:1500], flush=True)
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: EXC {type(exc).__name__}: {exc}"[:600], flush=True)
        return None


def run(sw):
    S = {"software": sw, "units": "kN_m_C"}
    nm = {**S, "file_path": str(OUT / f"d811_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=150)
    show("steel", "define_material", {**S, "name": "ST", "type": "steel", "E": 2e8, "poisson": 0.3,
                                      "unit_weight": 77.0, "fy": 345000.0, "fu": 450000.0}, limit=150)
    show("conc", "define_material", {**S, "name": "CC", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                     "unit_weight": 25.0, "fc": 30000.0}, limit=150)
    show("I", "define_frame_section", {**S, "name": "W1", "shape": "i_section", "material": "ST", "depth": 0.4,
                                       "width": 0.2, "flange_thickness": 0.015, "web_thickness": 0.01}, limit=150)
    show("R", "define_frame_section", {**S, "name": "R1", "shape": "rectangle", "material": "CC", "depth": 0.5,
                                       "width": 0.3}, limit=150)
    z0 = 0.0
    z1 = 3.0
    fr = show("frames", "create_frames", {**S, "section": "W1", "frames": [
        {"start": [0, 0, z1], "end": [6, 0, z1]}, {"start": [0, 0, z0], "end": [0, 0, z1]},
        {"start": [6, 0, z0], "end": [6, 0, z1]}]}, limit=600)
    fr2 = show("concrete frame", "create_frames", {**S, "section": "R1", "frames": [
        {"start": [0, 6, z1], "end": [6, 6, z1]}]}, limit=400)
    names = [f["name"] for f in (fr.get("created") or [])] if isinstance(fr, dict) else []
    cname = [f["name"] for f in (fr2.get("created") or [])] if isinstance(fr2, dict) else []
    print("frames", names, cname, flush=True)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    fo, po, ao, so = sap.FrameObj, sap.PointObj, sap.AreaObj, sap.SelectObj
    b = names[0] if names else "1"
    c = cname[0] if cname else "4"
    # design procedure
    raw("GetDesignProcedure steel", lambda: fo.GetDesignProcedure(b))
    raw("GetDesignProcedure concrete", lambda: fo.GetDesignProcedure(c))
    for code in range(0, 16):
        r = raw(f"SetDesignProcedure steel {code}", lambda code=code: fo.SetDesignProcedure(b, code))
        if r == 0 or r == [0]:
            raw("  -> read", lambda: fo.GetDesignProcedure(b))
    for code in range(0, 16):
        r = raw(f"SetDesignProcedure concrete {code}", lambda code=code: fo.SetDesignProcedure(c, code))
        if r == 0 or r == [0]:
            raw("  -> read", lambda: fo.GetDesignProcedure(c))
    # lateral bracing
    raw("GetLateralBracing default", lambda: fo.GetLateralBracing(b))
    raw("SetLateralBracing point top 0.5", lambda: fo.SetLateralBracing(b, 1, 1, 0.5, 0.5, True))
    raw("SetLateralBracing point bottom 0.25", lambda: fo.SetLateralBracing(b, 1, 2, 0.25, 0.25, True))
    raw("SetLateralBracing uniform all 0-1", lambda: fo.SetLateralBracing(b, 2, 3, 0.0, 1.0, True))
    raw("SetLateralBracing point abs 2 m", lambda: fo.SetLateralBracing(b, 1, 3, 2.0, 2.0, False))
    raw("GetLateralBracing", lambda: fo.GetLateralBracing(b))
    raw("DeleteLateralBracing point", lambda: fo.DeleteLateralBracing(b, 1))
    raw("GetLateralBracing after delete point", lambda: fo.GetLateralBracing(b))
    raw("DeleteLateralBracing all", lambda: fo.DeleteLateralBracing(b, 3))
    raw("GetLateralBracing after delete all", lambda: fo.GetLateralBracing(b))
    # T/C limits
    raw("GetTCLimits default", lambda: fo.GetTCLimits(b))
    raw("SetTCLimits comp 0", lambda: fo.SetTCLimits(b, True, 0.0, False, 0.0))
    raw("GetTCLimits", lambda: fo.GetTCLimits(b))
    raw("SetTCLimits comp -5 tens 100", lambda: fo.SetTCLimits(b, True, -5.0, True, 100.0))
    raw("GetTCLimits b", lambda: fo.GetTCLimits(b))
    raw("SetTCLimits comp 5 (positive)", lambda: fo.SetTCLimits(b, True, 5.0, False, 0.0))
    raw("GetTCLimits c", lambda: fo.GetTCLimits(b))
    raw("SetTCLimits off", lambda: fo.SetTCLimits(b, False, 0.0, False, 0.0))
    raw("GetTCLimits off", lambda: fo.GetTCLimits(b))
    # panel zone on the beam-column joint
    pts = raw("GetPoints beam", lambda: fo.GetPoints(b))
    pj = str(pts[0]) if pts else "1"
    raw("GetPanelZone default", lambda: po.GetPanelZone(pj))
    for pt in (1, 2, 3, 4):
        raw(f"SetPanelZone type {pt}", lambda pt=pt: po.SetPanelZone(pj, pt, 0.01, 1e6, 2e6, "", 1, 1, 0.0))
        raw("  -> read", lambda: po.GetPanelZone(pj))
    raw("SetPanelZone conn 2 axis user 30", lambda: po.SetPanelZone(pj, 3, 0.0, 1e6, 2e6, "", 2, 2, 30.0))
    raw("  -> read", lambda: po.GetPanelZone(pj))
    raw("DeletePanelZone", lambda: po.DeletePanelZone(pj))
    raw("GetPanelZone after delete", lambda: po.GetPanelZone(pj))
    # point local axes
    raw("GetLocalAxes", lambda: po.GetLocalAxes(pj))
    raw("SetLocalAxes 90 0 0", lambda: po.SetLocalAxes(pj, 90.0, 0.0, 0.0))
    raw("GetLocalAxes after", lambda: po.GetLocalAxes(pj))
    # areas and edge constraint
    ar = show("area", "create_areas", {**S, "areas": [{"points": [[0, 10, z1 if sw == "ETABS" else 0],
                                                                  [4, 10, z1 if sw == "ETABS" else 0],
                                                                  [4, 14, z1 if sw == "ETABS" else 0],
                                                                  [0, 14, z1 if sw == "ETABS" else 0]]}]}, limit=400)
    an = (ar.get("created") or [{}])[0].get("name", "1") if isinstance(ar, dict) else "1"
    raw("GetEdgeConstraint", lambda: ao.GetEdgeConstraint(an))
    raw("SetEdgeConstraint True", lambda: ao.SetEdgeConstraint(an, True))
    raw("GetEdgeConstraint after", lambda: ao.GetEdgeConstraint(an))
    raw("SetEdgeConstraint False", lambda: ao.SetEdgeConstraint(an, False))
    raw("GetEdgeConstraint off", lambda: ao.GetEdgeConstraint(an))
    # links
    lk = None
    if sw != "SAFE":
        show("link prop", "define_link_property", {**S, "name": "LK", "type": "linear",
                                                   "stiffness": {"U1": 1e6, "U2": 1e6, "U3": 1e6}}, limit=300)
        lk = show("link", "create_links", {**S, "property": "LK", "links": [{"point1": pj, "name": "LK1"}]}, limit=400)
    lname = None
    if isinstance(lk, dict):
        lname = ((lk.get("created") or [{}])[0] or {}).get("name")
    raw("LinkObj names", lambda: sap.LinkObj.GetNameList())
    if lname:
        raw("LinkObj.Delete", lambda: sap.LinkObj.Delete(lname))
        raw("LinkObj names after", lambda: sap.LinkObj.GetNameList())
    # selection API
    print("SelectObj members:", sorted(m for m in dir(so) if not m.startswith("_")), flush=True)
    for label, fn in (("PropertyFrame W1", lambda: so.PropertyFrame("W1", False)),
                      ("PropertyArea", lambda: so.PropertyArea("Default", False)),
                      ("PropertyMaterial ST", lambda: so.PropertyMaterial("ST", False)),
                      ("CoordinateRange", lambda: so.CoordinateRange(-1, 7, -1, 1, -1, 4, False, "Global", False)),
                      ("PlaneXY", lambda: so.PlaneXY(pj, False)),
                      ("InvertSelection", lambda: so.InvertSelection()),
                      ("PreviousSelection", lambda: so.PreviousSelection())):
        raw("ClearSelection", lambda: so.ClearSelection())
        raw(label, fn)
        raw(f"  GetSelected after {label}", lambda: so.GetSelected())
    raw("ClearSelection", lambda: so.ClearSelection())
    raw("FrameObj.SetSelected", lambda: fo.SetSelected(b, True))
    raw("AreaObj.SetSelected", lambda: ao.SetSelected(an, True))
    raw("PointObj.SetSelected", lambda: po.SetSelected(pj, True))
    raw("GetSelected", lambda: so.GetSelected())
    raw("InvertSelection", lambda: so.InvertSelection())
    raw("GetSelected inverted", lambda: so.GetSelected())
    raw("FrameObj.GetSection", lambda: fo.GetSection(b))
    raw("FrameObj.GetMaterialOverwrite", lambda: fo.GetMaterialOverwrite(b))
    raw("AreaObj.GetProperty", lambda: ao.GetProperty(an))
    raw("PropFrame.GetMaterial W1", lambda: sap.PropFrame.GetMaterial("W1"))


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
for sw in targets:
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=True)
        run(sw)
    except Exception as exc:  # noqa: BLE001
        print("EXCEPTION", type(exc).__name__, exc, flush=True)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("close", exc, flush=True)
sys.exit(finish())
