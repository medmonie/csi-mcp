r"""Diagnostic: where the ETABS reference model's 522 m displacement comes from. Scratch instance; output:
tests\results\diag_etabs_ref.txt"""
import os
import sys
import tempfile

from live_common import bridge, com, finish  # noqa: I001
from csi_bridge import as_list, api_call  # noqa: E402


def peak_case(sap, label, case):
    return peak(sap, label, case)


def peak(sap, label, case="Dead"):
    path = os.path.join(tempfile.gettempdir(), "CsiMcpLive", "diag_etabs.edb")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    sap.SetModelIsLocked(False)
    print(label, "save", sap.File.Save(path), "run", sap.Analyze.RunAnalysis())
    sap.Results.Setup.DeselectAllCasesAndCombosForOutput()
    sap.Results.Setup.SetCaseSelectedForOutput(case)
    r = sap.Results.JointDispl("ALL", 2)
    names, elms, u3, r1 = as_list(r[1]), as_list(r[2]), as_list(r[8]), as_list(r[9])
    worst = sorted(zip(names, elms, u3, r1), key=lambda t: -abs(t[2]))[:4]
    for n, e, uz, rx in worst:
        try:
            x, y, z = api_call("PointObj.GetCoordCartesian", sap.PointObj.GetCoordCartesian, n, 0.0, 0.0, 0.0)[:3]
        except Exception:  # noqa: BLE001
            print(f"   obj {n!r} elm {e!r} (not a point object) Uz={uz:.4g} Rx={rx:.4g}")
            continue
        lab = sap.PointObj.GetLabelFromName(n)
        frames = sap.PointObj.GetConnectivity(n) if hasattr(sap.PointObj, "GetConnectivity") else "?"
        print(f"   point {n} label {lab[:2]} at ({x:.2f},{y:.2f},{z:.2f}) Uz={uz:.4g} Rx={rx:.4g} conn={frames}")


def run():
    conn = bridge.launch_new("ETABS", visible=True)
    try:
        sap = conn.sap_model
        sap.InitializeNewModel(6)
        print("NewSteelDeck", sap.File.NewSteelDeck(3, 3.5, 4.0, 3, 3, 6.0, 6.0))
        print("areas", sap.AreaObj.GetNameList(), "points", sap.PointObj.Count(), "frames", sap.FrameObj.Count())
        decks = [str(a) for a in as_list(sap.AreaObj.GetNameList()[1])]
        for pt in ("63", "64", "65", "66"):
            print("  deck point", pt, sap.PointObj.GetCoordCartesian(pt, 0.0, 0.0, 0.0)[:3],
                  "conn", sap.PointObj.GetConnectivity(pt)[:3], "label", sap.PointObj.GetLabelFromName(pt)[:2])
        print("  deck 3 props", sap.PropArea.GetDeck("Deck1") if hasattr(sap.PropArea, "GetDeck") else "")
        for d in decks:
            sap.AreaObj.SetLoadUniform(d, "Live", 2.0, 10, True, "Global", 0)
        peak_case(sap, "B Live 2 kN/m2 on decks:", "Live")
        def point_at(x, y, z):
            for n in as_list(sap.PointObj.GetNameList()[1]):
                c = sap.PointObj.GetCoordCartesian(n, 0.0, 0.0, 0.0)
                if abs(c[0] - x) < 1e-3 and abs(c[1] - y) < 1e-3 and abs(c[2] - z) < 1e-3:
                    return n
        corners = [point_at(x, y, z) for x, y, z in ((0, 0, 0), (6, 0, 0), (6, 0, 4), (0, 0, 4))]
        print("  wall corners", corners, "restraint of base corners", [sap.PointObj.GetRestraint(c)[0] for c in corners[:2]])
        sap.SetModelIsLocked(False)
        print("  C30 SetMaterial", sap.PropMaterial.SetMaterial("C30", 2),
              "C30 props", sap.PropMaterial.GetMPIsotropic("C30") if hasattr(sap.PropMaterial, "GetMPIsotropic") else "",
              "4000Psi props", sap.PropMaterial.GetMPIsotropic("4000Psi") if hasattr(sap.PropMaterial, "GetMPIsotropic") else "")
        print("  wall prop C30", sap.PropArea.SetWall("W250C", 1, 1, "C30", 0.25),
              "wall prop 4000Psi", sap.PropArea.SetWall("W250P", 1, 1, "4000Psi", 0.25))
        w = sap.AreaObj.AddByPoint(4, corners, "", "W250P", "")
        print("  wall (4000Psi)", w)
        peak_case(sap, "D wall 4000Psi, Dead:", "Dead")
        sap.SetModelIsLocked(False)
        print("  switch wall to C30", sap.AreaObj.SetProperty(w[-2] if isinstance(w[-2], str) else w[1], "W250C", 0))
        peak_case(sap, "E wall C30, Dead:", "Dead")
    finally:
        bridge.disconnect("ETABS", True, False)


com(run)
sys.exit(finish())
