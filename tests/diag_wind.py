r"""Diagnostic (Phase 5, auto wind): what SAP2000 / ETABS offer for automatic wind loads.

    run_diag_wind.bat      Output: tests\results\diag_wind.txt

For each program (new scratch instance, blank / grid model, a wind pattern WX):
1 the methods of LoadPatterns.AutoWind with their parameter names (from the type library);
2 the input tables whose key mentions wind, their fields and rows, before and after the pattern is made;
3 LoadPatterns.GetAutoWindCode and the table rows after a few Set calls on WX (ASCE 7-16, user coefficient /
  user loads when the program has them).
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_wind"
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


def methods(obj, label):
    specs = None
    for get in (lambda: type(obj)._methods_, lambda: type(obj)._type_._methods_,
                lambda: obj.__com_interface__._methods_):
        try:
            specs = get()
            break
        except Exception:  # noqa: BLE001
            continue
    if not specs:
        print(f"{label}: no _methods_; dir = {[m for m in dir(obj) if not m.startswith('_')]}", flush=True)
        return
    print(f"{label}: {len(specs)} methods", flush=True)
    for s in specs:
        try:
            name, pf = s[1], s[3]
            params = [f"{'out ' if (p[0] & 2) else ''}{p[1]}" for p in pf]
            print(f"  {name}({', '.join(params)})", flush=True)
        except Exception:  # noqa: BLE001
            print("  ", s, flush=True)


def tables(sap, words):
    o = sap.DatabaseTables.GetAvailableTables()
    keys = [str(k) for k in o[1]]
    return [k for k in keys if any(w in k.lower() for w in words)]


def table_rows(sap, key):
    try:
        o = sap.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
        fields, n, data = list(o[1]), int(o[2]), list(o[3])
        print(f"  table '{key}' fields {fields}", flush=True)
        for i in range(n):
            print("   ", dict(zip(fields, data[i * len(fields):(i + 1) * len(fields)])), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  table '{key}': EXC {exc}", flush=True)


def fields(sap, k):
    try:
        f = sap.DatabaseTables.GetAllFieldsInTable(k)
        # (version, n, keys, names, descriptions, units, importable, ret)
        keys, units, imp = [str(x) for x in f[2]], [str(x) for x in f[5]], list(f[6])
        print(f"  FIELDS {k}:", flush=True)
        for i, kk in enumerate(keys):
            print(f"     {kk} [{units[i]}] {'imp' if imp[i] else ''} - {str(f[4][i])[:90]}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  FIELDS {k}: EXC {exc}", flush=True)


def write_row(sap, key, row):
    o = sap.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
    ver, flds = int(o[0]), [str(x) for x in o[1]]
    n = int(o[2])
    data = [str(x) for x in o[3]]
    rows = [dict(zip(flds, data[i * len(flds):(i + 1) * len(flds)])) for i in range(n)]
    rows = [r for r in rows if r.get("Name", r.get("LoadPat")) != row.get("Name", row.get("LoadPat"))]
    rows.append({f: str(row.get(f, "")) for f in flds})
    flat = [r.get(f, "") for r in rows for f in flds]
    r1 = sap.DatabaseTables.SetTableForEditingArray(key, ver, flds, len(rows), flat)
    r2 = sap.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    print(f"  write {key}: set {r1}, apply {r2}", flush=True)


def wind(sw):
    sap = bridge.sap_model(sw)
    sap.InitializeNewModel(6)          # kN, m
    if sw == "ETABS":
        print("NewSteelDeck:", run("NewSteelDeck", sap.File.NewSteelDeck, 3, 3.5, 3.5, 3, 3, 6.0, 6.0))
        keys = ["Load Pattern Definitions - Auto Wind - ASCE 7-16", "Load Pattern Definitions - Auto Wind - User Loads",
                "Load Pattern Definitions - Auto Wind - Loads to Diaphragms",
                "Load Pattern Definitions - Auto Wind - Loads to Stories"]
    else:
        sap.File.New3DFrame(0, 3, 3.5, 3, 6.0, 3, 6.0)
        keys = ["Auto Wind - ASCE 7-16", "Auto Wind - User", "Auto Wind Exposure For Horizontal Diaphragms",
                "Auto Wind Loads To Horizontal Diaphragms", "Auto Wind Loads To Joints", "Auto Wind Angles"]
    lp = sap.LoadPatterns
    run("add WX wind", lp.Add, "WX", 6, 0.0, True)
    for k in keys:
        fields(sap, k)
    if sw == "SAP2000":
        a = lp.AutoWind
        run("SetASCE716 (diaphragm exposure)", a.SetASCE716, "WX", 1, 0.0, 0.8, 0.5, 1, 0.15, 0.15, False,
            0.0, 0.0, 45.0, 3, 1.0, 0.85, 0.85, 0.0, False)
        run("GetASCE716", a.GetASCE716, "WX", 0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, False, 0.0, 0.0, 0.0, 0, 0.0,
            0.0, 0.0, 0.0, False)
        table_rows(sap, keys[0])
    else:
        row = {"Name": "WX", "IsAuto": "No", "ExposeFrom": "Diaphragms", "Angle": "0", "WindwardCp": "0.8",
               "LeewardCp": "0.5", "ASCECase": "Create All Cases", "e1": "0.15", "e2": "0.15", "UserZ": "No",
               "TopStory": "Story3", "BotStory": "Base", "WindSpeed": "45", "ExposureType": "C", "Kzt": "1",
               "GustFactor": "0.85", "Kd": "0.85", "Ke": "1", "SolidGross": "0", "IncludeParapet": "No"}
        try:
            write_row(sap, keys[0], row)
        except Exception as exc:  # noqa: BLE001
            print("  write EXC", exc, flush=True)
        table_rows(sap, keys[0])
        run("GetAutoWindCode", lp.GetAutoWindCode, "WX")
        sap.Analyze.SetRunCaseFlag("Modal", False) if hasattr(sap.Analyze, "SetRunCaseFlag") else None
        run("RunAnalysis", sap.Analyze.RunAnalysis)
        for k in keys[2:]:
            table_rows(sap, k)
        table_rows(sap, "Base Reactions")


for sw in ("SAP2000", "ETABS"):
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=False)
        com(wind, sw)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED {sw}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        try:
            com(bridge.disconnect, sw, True, False)
        except Exception as exc:  # noqa: BLE001
            print("close", exc)
sys.exit(finish())
