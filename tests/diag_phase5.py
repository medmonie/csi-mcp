r"""Diagnostic for the first Phase 5 live run (raw API, new scratch instances).

    run_diag_phase5.bat      Output: tests\results\diag_phase5.txt

1 ETABS / SAP2000: LoadPatterns.AutoSeismic.SetASCE716 returned 1 in ETABS 23.2 on a quake pattern. Variants of the
  call (approximate / program / user period, one direction, Ct types, site classes, the _1 form) and what the
  'Load Pattern Definitions - Auto Seismic - ASCE 7-16' table holds before and after.
2 SAP2000: design table names (preferences / overwrites / results) with AISC 360-16 set.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_phase5"
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


def seismic(sw):
    sap = bridge.sap_model(sw)
    sap.InitializeNewModel(3)          # kip, in
    if sw == "ETABS":
        sap.File.NewGridOnly(2, 144.0, 144.0, 2, 2, 240.0, 240.0)
    else:
        sap.File.NewBlank()
    print("add EQX quake:", sap.LoadPatterns.Add("EQX", 5, 0.0, True))
    print("type:", sap.LoadPatterns.GetLoadType("EQX"))
    print("auto seismic code before:", run("GetAutoSeismicCode", sap.LoadPatterns.GetAutoSeismicCode, "EQX"))
    for k in tables(sap, ["auto seismic"]):
        print("table:", k)
    a = sap.LoadPatterns.AutoSeismic
    base = dict(nDir=[True, False, False, False, False, False], Eccen=0.05, PeriodFlag=3, CtType=0, UserT=0.5,
                UserZ=False, TopZ=0.0, BottomZ=0.0, R=8.0, Omega=3.0, Cd=5.5, I=1.0, Ss=1.5, S1=0.6, Tl=8.0,
                SiteClass=2, Fa=0.0, Fv=0.0)
    variants = [("as sent by 0.13.0 (user T)", {}), ("approximate period", {"PeriodFlag": 1}),
                ("program period", {"PeriodFlag": 2}), ("PeriodFlag 0", {"PeriodFlag": 0}),
                ("Ct type 1", {"CtType": 1}), ("site class D (4)", {"SiteClass": 4}),
                ("site class 1", {"SiteClass": 1}), ("site class 0", {"SiteClass": 0}),
                ("UserZ with top / bottom", {"UserZ": True, "TopZ": 288.0, "BottomZ": 0.0}),
                ("site E with Fa Fv", {"SiteClass": 5, "Fa": 1.2, "Fv": 2.0})]
    for label, change in variants:
        v = {**base, **change}
        for meth in ("SetASCE716", "SetASCE716_1"):
            if hasattr(a, meth):
                r = run(f"{meth} {label}", getattr(a, meth), "EQX", *v.values())
                if r == 0 or (isinstance(r, (list, tuple)) and r[-1] == 0):
                    run("  GetASCE716 back", a.GetASCE716, "EQX", [False] * 6, 0.0, 0, 0, 0.0, False, 0.0, 0.0,
                        0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0)
                    print("  code now:", run("GetAutoSeismicCode", sap.LoadPatterns.GetAutoSeismicCode, "EQX"))
    for k in tables(sap, ["auto seismic"]):
        table_rows(sap, k)


def sap_design():
    sap = bridge.sap_model("SAP2000")
    print("code before:", run("GetCode", sap.DesignSteel.GetCode))
    print("SetCode:", run("SetCode", sap.DesignSteel.SetCode, "AISC 360-16"))
    for k in tables(sap, ["steel design", "steel frame design", "concrete design"]):
        print("table:", k)


for sw, parts in (("ETABS", [lambda: seismic("ETABS")]), ("SAP2000", [lambda: seismic("SAP2000"), sap_design])):
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=True)
        for part in parts:
            com(part)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED {sw}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        try:
            com(bridge.disconnect, sw, True, False)
        except Exception as exc:  # noqa: BLE001
            print("close", exc)
sys.exit(finish())
