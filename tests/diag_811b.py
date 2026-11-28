r"""Diagnostic 2 (roadmap 8.11): area edge constraints, ETABS / SAFE lateral bracing units and deletion (API and
table), panel zones from a link property / brace connectivity / user local axis. Output: tests\results\diag_811b.txt"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_811b_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish, show  # noqa: E402,I001
from csi_bridge import as_list  # noqa: E402
import db_edit  # noqa: E402

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


def table(sap, key, n=8):
    try:
        v, flds, rows = com(db_edit.read_table, sap, key)
        print(f"  TABLE {key}: v{v} fields {flds}", flush=True)
        for r in rows[:n]:
            print("     ", r, flush=True)
        return v, flds, rows
    except Exception as exc:  # noqa: BLE001
        print(f"  TABLE {key}: EXC {exc}"[:400], flush=True)
        return None


def run(sw):
    S = {"software": sw, "units": "kN_m_C"}
    nm = {**S, "file_path": str(OUT / f"d811b_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=100)
    show("steel", "define_material", {**S, "name": "ST", "type": "steel", "E": 2e8, "poisson": 0.3,
                                      "unit_weight": 77.0, "fy": 345000.0, "fu": 450000.0}, limit=100)
    show("conc", "define_material", {**S, "name": "CC", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                     "unit_weight": 25.0, "fc": 30000.0}, limit=100)
    show("I", "define_frame_section", {**S, "name": "W1", "shape": "i_section", "material": "ST", "depth": 0.4,
                                       "width": 0.2, "flange_thickness": 0.015, "web_thickness": 0.01}, limit=100)
    if sw == "SAP2000":
        show("shell", "define_area_section", {**S, "name": "SL", "kind": "shell_thin", "material": "CC",
                                              "thickness": 0.2}, limit=100)
    else:
        show("slab", "define_area_section", {**S, "name": "SL", "kind": "slab", "material": "CC", "thickness": 0.2,
                                             "shell_type": "shell_thin"}, limit=100)
    z1 = 3.0
    fr = show("frames", "create_frames", {**S, "section": "W1", "frames": [
        {"start": [0, 0, z1], "end": [6, 0, z1]}, {"start": [0, 0, 0], "end": [0, 0, z1]},
        {"start": [6, 0, 0], "end": [6, 0, z1]}]}, limit=300)
    names = [f["name"] for f in (fr.get("created") or [])] if isinstance(fr, dict) else ["1"]
    za = z1 if sw == "ETABS" else 0.0
    ar = show("areas", "create_areas", {**S, "section": "SL", "areas": [
        {"points": [[0, 10, za], [4, 10, za], [4, 14, za], [0, 14, za]]},
        {"points": [[4, 10, za], [8, 10, za], [8, 14, za], [4, 14, za]]}]}, limit=300)
    anames = [a["name"] for a in (ar.get("created") or [])] if isinstance(ar, dict) else ["1"]
    sap = bridge.sap_model(sw)
    raw("GetDatabaseUnits", lambda: sap.GetDatabaseUnits())
    raw("GetPresentUnits", lambda: sap.GetPresentUnits())
    com(lambda: sap.SetPresentUnits(6))
    fo, po, ao = sap.FrameObj, sap.PointObj, sap.AreaObj
    an = anames[0]
    raw("GetEdgeConstraint", lambda: ao.GetEdgeConstraint(an))
    raw("SetEdgeConstraint True", lambda: ao.SetEdgeConstraint(an, True))
    raw("GetEdgeConstraint after", lambda: ao.GetEdgeConstraint(an))
    raw("GetEdgeConstraint other", lambda: ao.GetEdgeConstraint(anames[-1]))
    raw("SetEdgeConstraint False", lambda: ao.SetEdgeConstraint(an, False))
    raw("GetEdgeConstraint off", lambda: ao.GetEdgeConstraint(an))
    raw("SetEdgeConstraint True again", lambda: ao.SetEdgeConstraint(an, True))
    b = names[0]
    # lateral bracing units
    for pu in (6, 5, 3):
        com(lambda pu=pu: sap.SetPresentUnits(pu))
        raw(f"units {pu}: GetPresentUnits", lambda: sap.GetPresentUnits())
        raw(f"units {pu}: SetLateralBracing abs 2.0", lambda: fo.SetLateralBracing(b, 1, 1, 2.0, 2.0, False))
        raw(f"units {pu}: GetLateralBracing", lambda: fo.GetLateralBracing(b))
    com(lambda: sap.SetPresentUnits(6))
    raw("SetLateralBracing uniform rel", lambda: fo.SetLateralBracing(b, 2, 2, 0.0, 1.0, True))
    raw("GetLateralBracing", lambda: fo.GetLateralBracing(b))
    keys = [str(k) for k in as_list(com(lambda: sap.DatabaseTables.GetAllTables())[1])]
    bt = [k for k in keys if "Bracing" in k or "Panel Zone" in k or "Edge Constraint" in k or "Design Proc" in k
          or "Local Axes" in k and "Joint" in k or "Point Assignments - Local" in k]
    print("tables:", bt, flush=True)
    for k in bt:
        table(sap, k)
    for k in [k for k in bt if "Bracing" in k]:
        t = table(sap, k)
        if t:
            v, flds, rows = t
            raw(f"import empty {k}", lambda k=k, v=v, flds=flds: db_edit.write_tables(sap, [(k, v, flds, [])]))
            table(sap, k)
            raw("GetLateralBracing after table delete", lambda: fo.GetLateralBracing(b))
    # API delete in various forms
    raw("SetLateralBracing point rel", lambda: fo.SetLateralBracing(b, 1, 1, 0.5, 0.5, True))
    for t in (1, 2, 3):
        raw(f"DeleteLateralBracing {t}", lambda t=t: fo.DeleteLateralBracing(b, t))
        raw("  GetLateralBracing", lambda: fo.GetLateralBracing(b))
    raw("DeleteLateralBracing itemtype 0 explicit", lambda: fo.DeleteLateralBracing(b, 3, 0))
    raw("  GetLateralBracing", lambda: fo.GetLateralBracing(b))
    # panel zone with link / braces / user axis
    pj = str(com(lambda: fo.GetPoints(b))[0])
    pl = sap.PropLink
    raw("PropLink.SetLinear LKZ", lambda: pl.SetLinear("LKZ", [True, False, False, False, False, True],
                                                    [False] * 6, [1e6, 0, 0, 0, 0, 5e5], [0] * 6, 0.0, 0.0))
    raw("PropLink names", lambda: pl.GetNameList())
    raw("SetPanelZone link LKZ", lambda: po.SetPanelZone(pj, 3, 0.0, 0.0, 0.0, "LKZ", 1, 1, 0.0))
    raw("  read", lambda: po.GetPanelZone(pj))
    raw("SetPanelZone column conn 2", lambda: po.SetPanelZone(pj, 1, 0.0, 0.0, 0.0, "", 2, 1, 0.0))
    raw("  read", lambda: po.GetPanelZone(pj))
    raw("SetPanelZone springs axis user 30", lambda: po.SetPanelZone(pj, 2, 0.0, 1e6, 2e6, "", 1, 2, 30.0))
    raw("  read", lambda: po.GetPanelZone(pj))
    raw("SetPanelZone doubler 0.012", lambda: po.SetPanelZone(pj, 1, 0.012, 0.0, 0.0, "", 1, 1, 0.0))
    raw("  read", lambda: po.GetPanelZone(pj))
    for k in bt:
        if "Panel" in k:
            table(sap, k)


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
