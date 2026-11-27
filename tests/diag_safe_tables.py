r"""Diagnostic: how SAFE's table display selection behaves (raw API calls, printed). Scratch instance, closed at the end.

    run_diag_safe.bat [SAFE|ETABS|SAP2000]      Output: tests\results\diag_tables.txt
"""
import sys

from live_common import bridge, build_template, com, finish, frame_points, show  # noqa: I001
from csi_bridge import as_list, split_result  # noqa: E402

sw = (sys.argv[1] if len(sys.argv) > 1 else "SAFE").upper()


def raw_read(sap, key, group):
    r = sap.DatabaseTables.GetTableForDisplayArray(key, [], group, 0, [], 0, [])
    ret, outs = split_result(r)
    return {"ret": ret, "fields": as_list(outs[-3])[:6], "n": outs[-2]}


def sel(sap):
    out = {}
    for m in ("GetLoadCasesSelectedForDisplay", "GetLoadCombinationsSelectedForDisplay"):
        try:
            out[m[3:-20]] = repr(getattr(sap.DatabaseTables, m)())
        except Exception as exc:  # noqa: BLE001
            out[m[3:-20]] = f"ERR {exc}"
    return out


def setsel(sap, cases, combos):
    out = {}
    for m, names in (("SetLoadCasesSelectedForDisplay", cases), ("SetLoadCombinationsSelectedForDisplay", combos)):
        try:
            out[m[3:-20]] = repr(getattr(sap.DatabaseTables, m)(names))
        except Exception as exc:  # noqa: BLE001
            out[m[3:-20]] = f"ERR {exc}"
    return out


def run():
    conn = bridge.launch_new(sw, visible=True)
    try:
        info = build_template(sw)
        sap = conn.sap_model
        start, end = info["brace"]
        name = sap.FrameObj.AddByCoord(*start, *end, "", info["sections"][0], "", "Global")[0]
        for pt in frame_points(sw, name):
            sap.PointObj.SetRestraint(pt, [True] * 6)       # a stable model (unsupported ones give garbage results)
        sap.FrameObj.SetLoadDistributed(name, info["dead"], 1, 10, 0.0, 1.0, 10.0, 10.0, "Global", True, True, 0)
        sap.File.Save(__import__("os").path.join(__import__("tempfile").gettempdir(), "CsiMcpLive", f"diag{bridge.spec(sw).model_extensions[0]}"))
        print("run:", sap.Analyze.RunAnalysis())
        dt = sap.DatabaseTables
        cases, combos = bridge.name_list(sap, "LoadCases"), bridge.name_list(sap, "RespCombo")
        br = lambda: raw_read(sap, "Base Reactions", "")  # noqa: E731
        print("1 BR:", br(), "| again:", br(), "| JointReact table:", raw_read(sap, "Joint Reactions", ""))
        print("2 set SAME selection:", setsel(sap, ["Dead", "Live"], combos), "| BR:", br())
        print("3 set combos only:", setsel(sap, [], combos), sel(sap), "| BR:", br())
        print("4 set dead only:", setsel(sap, ["Dead"], []), sel(sap), "| BR:", br())
        print("5 set all incl ~LLRF:", setsel(sap, cases, combos), sel(sap), "| BR:", br(), "| again:", br())
    finally:
        bridge.disconnect(sw, True, False)


com(run)
sys.exit(finish())
