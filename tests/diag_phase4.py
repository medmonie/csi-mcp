r"""Diagnostic for the first Phase 4 live run (raw API, new scratch instances).

    run_diag_phase4.bat      Output: tests\results\diag_phase4.txt

1 SAP2000: does GetTableForEditingArray see a load pattern added since the last save? (Load Pattern Definitions
  returned only DEAD after DL / LL / WX were added.) Read before save, after save, after RefreshView.
2 SAP2000: EditPoint.Merge with a point 0.5 mm from a frame end: selected pair only, tolerance 0.001 / 0.01.
3 ETABS: EditGeneral.Move with only a frame selected vs its points selected.
4 SAFE: a slab drawn by AddByCoord with corner restraints: property, restraints, DEAD reaction.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_phase4"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish  # noqa: E402,I001


def run(label, fn, *a):
    try:
        r = fn(*a)
        print(f"{label}: {r}", flush=True)
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"{label}: EXC {type(exc).__name__}: {exc}", flush=True)
        return None


def table_names(sap, key):
    o = sap.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
    fields, n, data = list(o[1]), int(o[2]), list(o[3])
    return [data[i * len(fields)] for i in range(n)], o[-1]


def sap_part():
    sap = bridge.sap_model("SAP2000")
    sap.InitializeNewModel(6)
    sap.File.NewBlank()
    p = str(OUT / "diag_sap.sdb")
    print("save new:", sap.File.Save(p))
    print("add DL:", sap.LoadPatterns.Add("DL", 1, 1.0, True))
    print("pattern list:", sap.LoadPatterns.GetNameList())
    run("table before save", table_names, sap, "Load Pattern Definitions")
    run("table group All", lambda: sap.DatabaseTables.GetTableForEditingArray("Load Pattern Definitions", "All", 0,
                                                                              [], 0, []))
    sap.View.RefreshView(0, False)
    run("table after refresh", table_names, sap, "Load Pattern Definitions")
    print("save:", sap.File.Save(p))
    run("table after save", table_names, sap, "Load Pattern Definitions")
    print("add LL (after save):", sap.LoadPatterns.Add("LL", 3, 0.0, True))
    run("table after LL", table_names, sap, "Load Pattern Definitions")
    run("display table", lambda: sap.DatabaseTables.GetTableForDisplayArray("Load Pattern Definitions", [], "", 0,
                                                                             [], 0, []))
    run("table after display read", table_names, sap, "Load Pattern Definitions")
    # 2 merge
    sap.PropMaterial.SetMaterial("C30", 2)
    sap.PropFrame.SetRectangle("R", "C30", 0.4, 0.4)
    a = sap.PointObj.AddCartesian(0, 0, 0, "", "A")
    b = sap.PointObj.AddCartesian(6, 0, 0, "", "B")
    f = sap.FrameObj.AddByPoint("A", "B", "", "R", "F")
    n = sap.PointObj.AddCartesian(6.0005, 0, 0, "", "NEAR")
    print("points", a, b, n, "frame", f)
    for tol in (0.001, 0.01):
        sap.SelectObj.ClearSelection()
        sap.PointObj.SetSelected("B", True)
        sap.PointObj.SetSelected("NEAR", True)
        print(f"merge pair tol {tol}:", run("merge", sap.EditPoint.Merge, tol, 0, []),
              "points now", sap.PointObj.GetNameList())
    sap.SelectObj.ClearSelection()
    sap.SelectObj.All(False)
    print("merge all 0.01:", run("merge", sap.EditPoint.Merge, 0.01, 0, []), sap.PointObj.GetNameList())


def etabs_part():
    sap = bridge.sap_model("ETABS")
    sap.InitializeNewModel(6)
    sap.File.NewGridOnly(1, 4.0, 4.0, 2, 2, 6.0, 4.0)
    sap.PropMaterial.SetMaterial("C30", 2)
    sap.PropFrame.SetRectangle("R", "C30", 0.4, 0.4)
    sap.PointObj.AddCartesian(0, 0, 4, "", "P3")
    sap.PointObj.AddCartesian(6, 0, 4, "", "P4")
    print("frame", sap.FrameObj.AddByPoint("P3", "P4", "", "R", "B1"))
    names = sap.FrameObj.GetNameList()
    print("frames", names)
    fr = list(names[1])[0]
    sap.SelectObj.ClearSelection()
    print("select frame", sap.FrameObj.SetSelected(fr, True), sap.SelectObj.GetSelected())
    print("move frame only", sap.EditGeneral.Move(0, 0, 0.5))
    pts = sap.FrameObj.GetPoints(fr)
    print("after frame-only move", [sap.PointObj.GetCoordCartesian(q, 0, 0, 0) for q in pts[:2]])
    sap.SelectObj.ClearSelection()
    for q in pts[:2]:
        sap.PointObj.SetSelected(q, True)
    print("move points", sap.EditGeneral.Move(0, 0, 0.5))
    print("after point move", [sap.PointObj.GetCoordCartesian(q, 0, 0, 0) for q in pts[:2]])


def safe_part():
    sap = bridge.sap_model("SAFE")
    sap.InitializeNewModel(6)
    sap.File.NewGridOnly(1, 4.0, 4.0, 2, 2, 6.0, 4.0)
    sap.File.Save(str(OUT / "diag_safe.fdb"))
    sap.PropMaterial.SetMaterial("C30", 2)
    print("slab", sap.PropArea.SetSlab("S250", 0, 1, "C30", 0.25))
    for xs in ([0, 6, 6, 0], [10, 16, 16, 10]):
        o = sap.AreaObj.AddByCoord(4, xs, [0, 0, 4, 4], [0, 0, 0, 0], "", "S250", "", "Global")
        print("area", o)
    names = list(sap.AreaObj.GetNameList()[1])
    for ar in names:
        print(ar, "prop", sap.AreaObj.GetProperty(ar), "points", sap.AreaObj.GetPoints(ar))
        for q in sap.AreaObj.GetPoints(ar)[1]:
            print("  restraint", q, sap.PointObj.SetRestraint(q, [True, True, True, False, False, False]),
                  sap.PointObj.GetCoordCartesian(q, 0, 0, 0))
    print("dead SW", sap.LoadPatterns.GetSelfWTMultiplier("Dead"))
    print("analyse", sap.Analyze.RunAnalysis())
    sap.Results.Setup.DeselectAllCasesAndCombosForOutput()
    sap.Results.Setup.SetCaseSelectedForOutput("Dead")
    print("base react Dead", sap.Results.BaseReact())
    print("expected 2 x 25 x 0.25 x 24 = 300")


for sw, part in (("SAP2000", sap_part), ("ETABS", etabs_part), ("SAFE", safe_part)):
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=True)
        com(part)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED {sw}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        try:
            com(bridge.disconnect, sw, True, False)
        except Exception as exc:  # noqa: BLE001
            print("close", exc)
sys.exit(finish())
