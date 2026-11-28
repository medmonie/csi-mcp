r"""Diagnostic 2 (0.24.0): (1) analysis of a fixed-fixed beam with a support settlement (all) and SAP2000 strain /
deformation / gravity loads, plus a hyperstatic case on the settlement case; (2) ETABS table imports for buckling,
direct-integration and FNA time-history, steady-state and PSD cases and time-history functions; (3) grid-line imports
(ETABS / SAFE 'Grid Definitions - Grid Lines', SAP2000 'Grid Lines'). Full field lists, one per line.
Output: tests\results\diag_86b.txt"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_86b_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish, show  # noqa: E402,I001
from csi_bridge import as_list  # noqa: E402
import db_edit  # noqa: E402

targets = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or ["SAP2000", "ETABS", "SAFE"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}


def raw(label, fn):
    try:
        r = com(fn)
        print(f"  {label}: {r!r}"[:2500], flush=True)
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: EXC {type(exc).__name__}: {exc}"[:1500], flush=True)
        return None


def fields(sap, key):
    g = raw(f"fields {key} (count)", lambda: sap.DatabaseTables.GetAllFieldsInTable(key)[1])
    try:
        g = com(lambda: sap.DatabaseTables.GetAllFieldsInTable(key))
        print(f"  FIELDS {key}:", flush=True)
        for k, d, u, imp in zip(as_list(g[2]), as_list(g[4]), as_list(g[5]), as_list(g[6])):
            print(f"    {k} [{u}] {'import' if imp else '-'}: {str(d)[:400]}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print("   fields EXC", exc, flush=True)


def rows(sap, key):
    try:
        v, f, r = com(db_edit.read_table, sap, key)
        print(f"  ROWS {key} ({len(r)}):", flush=True)
        for x in r[:30]:
            print("    ", {k: val for k, val in x.items() if val not in ("", None)}, flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  ROWS {key}: EXC {exc}"[:800], flush=True)


def imp(sap, key, new_rows, drop=None):
    try:
        r = com(db_edit.edit_table, sap, key, new_rows, drop)
        print(f"  IMPORT {key}: {r}"[:2500], flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  IMPORT {key}: EXC {type(exc).__name__}: {exc} {getattr(exc, 'hint', '')}"[:2500], flush=True)
    rows(sap, key)


def run(sw):
    S = {"software": sw, "units": "kN_m_C"}
    z = 3.0 if sw == "ETABS" else 0.0
    nm = {**S, "file_path": str(OUT / f"d86b_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=200)
    show("C", "define_material", {**S, "name": "C", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                  "unit_weight": 25.0, "fc": 30000.0}, limit=100)
    show("B", "define_frame_section", {**S, "name": "B", "shape": "rectangle", "material": "C", "depth": 0.5,
                                       "width": 0.3}, limit=100)
    for pat in ("DISP", "STR", "DEF", "GRAV"):
        show(pat, "define_load_pattern", {**S, "name": pat, "type": "other", "self_weight_multiplier": 0.0},
             limit=100)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    po, fo = sap.PointObj, sap.FrameObj
    f1 = raw("AddByCoord F1", lambda: fo.AddByCoord(0, 0, z, 6, 0, z, "", "B", "F1", "Global"))
    fname = str(f1[0])
    pts = com(lambda: fo.GetPoints(fname, "", ""))
    pi, pj = str(pts[0]), str(pts[1])
    for p in (pi, pj):
        raw(f"restrain {p}", lambda p=p: po.SetRestraint(p, [True] * 6))
    raw("SetLoadDispl J U3 -0.01", lambda: po.SetLoadDispl(pj, "DISP", [0, 0, -0.01, 0, 0, 0], True, "Global"))
    if sw == "SAP2000":
        raw("strain", lambda: fo.SetLoadStrain(fname, "STR", 1, 0.001, True, ""))
        raw("deformation", lambda: fo.SetLoadDeformation(fname, "DEF", [True] + [False] * 5, [0.002, 0, 0, 0, 0, 0]))
        raw("gravity", lambda: fo.SetLoadGravity(fname, "GRAV", 0, 0, -1, True, "Global"))
    raw("HyperStatic HYP on DISP", lambda: (sap.LoadCases.HyperStatic.SetCase("HYP"),
                                           sap.LoadCases.HyperStatic.SetBaseCase("HYP", "DISP")))
    show("save", "open_or_save_model", {"software": sw, "action": "save"}, limit=200)
    show("run", "run_analysis", {"software": sw}, limit=2500)
    cases = ["DISP", "HYP"] + (["STR", "DEF", "GRAV"] if sw == "SAP2000" else [])
    show("frame forces", "get_results", {**S, "result_type": "frame_forces", "load_cases_or_combos": cases,
                                         "names": [fname], "max_rows": 60}, limit=9000)
    show("reactions", "get_results", {**S, "result_type": "joint_reactions", "load_cases_or_combos": cases,
                                      "max_rows": 20}, limit=5000)
    raw("SetModelIsLocked False", lambda: sap.SetModelIsLocked(False))

    o = com(lambda: sap.DatabaseTables.GetAllTables())
    keys = [str(k) for k in as_list(o[1])]
    print("  function tables:", [k for k in keys if "Function" in k], flush=True)
    if sw == "ETABS":
        for k in keys:
            if k.startswith("Functions - Time History") or k.startswith("Functions - Steady") or "Power Spectral" in k:
                fields(sap, k)
        ks = [k for k in keys if k.startswith("Load Case Definitions - ") and any(
            w in k for w in ("Buckling", "Direct", "Nonlinear Modal", "Steady", "Power", "Hyperstatic", "Damping"))]
        for k in ks:
            fields(sap, k)
        th = next((k for k in keys if k.startswith("Functions - Time History - User")), None)
        if th:
            fl = com(db_edit.read_table, sap, th)[1]
            print("  TH user fields:", fl, flush=True)
            guess = [{"Name": "STEP", "Time": 0, "Value": 1}, {"Name": "STEP", "Time": 10, "Value": 1}]
            imp(sap, th, [{k: v for k, v in r.items() if k in fl} for r in guess])
        raw("Func names", lambda: sap.Func.GetNameList())
        raw("ModalEigen MODALX", lambda: sap.LoadCases.ModalEigen.SetCase("MODALX"))
        imp(sap, "Load Case Definitions - Buckling",
            [{"Name": "BUCK", "LoadType": "Load", "LoadName": "DISP", "LoadSF": 1, "NumModes": 6, "EigenTol": "1E-09"}])
        imp(sap, "Load Case Definitions - Time History - Linear Direct Integration",
            [{"Name": "THL", "LoadType": "Load", "LoadName": "DISP", "Function": "STEP", "LoadSF": 1,
              "NumSteps": 200, "StepSize": 0.005}])
        imp(sap, "Load Case Definitions - Time History - Nonlin Direct Integration",
            [{"Name": "THN", "LoadType": "Load", "LoadName": "DISP", "Function": "STEP", "LoadSF": 1,
              "NumSteps": 200, "StepSize": 0.005}])
        imp(sap, "Load Case Definitions - Time History - Nonlinear Modal",
            [{"Name": "FNA", "LoadType": "Load", "LoadName": "DISP", "Function": "STEP", "LoadSF": 1,
              "ModalCase": "MODALX", "NumSteps": 200, "StepSize": 0.005}])
        for n in ("BUCK", "THL", "THN", "FNA"):
            raw(f"GetTypeOAPI_1 {n}", lambda n=n: sap.LoadCases.GetTypeOAPI_1(n))
        rows(sap, "Load Case Definitions - Damping - Modal")
        rows(sap, "Load Case Definitions - Damping - Hysteretic")
        rows(sap, "Load Case Definitions - Summary")

    print("\n---- grid lines", flush=True)
    if sw == "SAP2000":
        fields(sap, "Grid Lines")
        fields(sap, "Coordinate Systems")
        raw("SetCoordSys CS2", lambda: sap.CoordSys.SetCoordSys("CS2", 10, 5, 0, 30, 0, 0))
        imp(sap, "Grid Lines", [
            {"CoordSys": "CS2", "AxisDir": "X", "GridID": "A", "XRYZCoord": 0, "LineType": "Primary", "Visible": "Yes",
             "BubbleLoc": "End"},
            {"CoordSys": "CS2", "AxisDir": "X", "GridID": "B", "XRYZCoord": 6, "LineType": "Primary", "Visible": "Yes",
             "BubbleLoc": "End"},
            {"CoordSys": "CS2", "AxisDir": "Y", "GridID": "1", "XRYZCoord": 0, "LineType": "Primary", "Visible": "Yes",
             "BubbleLoc": "Start"},
            {"CoordSys": "CS2", "AxisDir": "Z", "GridID": "Z1", "XRYZCoord": 0, "LineType": "Primary",
             "Visible": "Yes", "BubbleLoc": "End"}])
        raw("GetCoordSys_2 CS2", lambda: sap.CoordSys.GetCoordSys_2("CS2"))
        imp(sap, "Coordinate Systems", [{"Name": "CS3", "Type": "Cartesian", "X": 1, "Y": 2, "Z": 3, "AboutZ": 45,
                                          "AboutY": 0, "AboutX": 0}])
        raw("GetCoordSys CS3", lambda: sap.CoordSys.GetCoordSys("CS3"))
        raw("CoordSys.Delete CS3", lambda: sap.CoordSys.Delete("CS3"))
        raw("GetNameList", lambda: sap.CoordSys.GetNameList())
        rows(sap, "Grid Lines")
    else:
        fields(sap, "Grid Definitions - General")
        fields(sap, "Grid Definitions - Grid Lines")
        raw("SetGridSys G2", lambda: sap.GridSys.SetGridSys("G2", 10, 5, 30))
        imp(sap, "Grid Definitions - Grid Lines", [
            {"Name": "G2", "LineType": "X (Cartesian)", "ID": "A", "Ordinate": 0, "BubbleLoc": "End", "Visible": "Yes"},
            {"Name": "G2", "LineType": "X (Cartesian)", "ID": "B", "Ordinate": 8, "BubbleLoc": "End", "Visible": "Yes"},
            {"Name": "G2", "LineType": "Y (Cartesian)", "ID": "1", "Ordinate": 0, "BubbleLoc": "Start",
             "Visible": "Yes"},
            {"Name": "G2", "LineType": "Y (Cartesian)", "ID": "2", "Ordinate": 5, "BubbleLoc": "Start",
             "Visible": "Yes"}])
        raw("GetGridSys_2 G2", lambda: sap.GridSys.GetGridSys_2("G2"))
        imp(sap, "Grid Definitions - Grid Lines", [], drop=lambda r: r.get("Name") == "G1" and r.get("ID") == "B")
        raw("GetGridSys_2 G1 after drop of B", lambda: sap.GridSys.GetGridSys_2("G1"))
        imp(sap, "Grid Definitions - General", [{"Name": "G3", "Type": "Cartesian", "Ux": 0, "Uy": 0, "Rz": 0,
                                                  "StoryRange": "Default", "BubbleSize": 1.25, "Color": "Gray6"}])
        raw("GetNameTypeList", lambda: sap.GridSys.GetNameTypeList())
        raw("GridSys.Delete G3", lambda: sap.GridSys.Delete("G3"))
        raw("GetNameTypeList after delete", lambda: sap.GridSys.GetNameTypeList())


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
for sw in targets:
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=True)
        run(sw)
    except Exception as exc:  # noqa: BLE001
        import traceback
        print("EXCEPTION", type(exc).__name__, exc, traceback.format_exc()[-1500:], flush=True)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("close", exc, flush=True)
sys.exit(finish())
