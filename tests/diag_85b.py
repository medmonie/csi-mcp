r"""Diagnostic 2 (roadmap 8.5): SAP2000 SetOConcrete_1 refusals, concrete curves with a section, and the
time-dependent material tables (ETABS / SAFE have no typed calls). Output: tests\results\diag_85b.txt"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_85b_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish, show  # noqa: E402,I001
import db_edit  # noqa: E402

targets = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or ["SAP2000", "ETABS", "SAFE"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}


def raw(label, fn):
    try:
        r = com(fn)
        print(f"  {label}: {r!r}"[:1800], flush=True)
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: EXC {type(exc).__name__}: {exc}"[:700], flush=True)
        return None


def table(sap, key, show_rows=6):
    try:
        v, flds, rows = com(db_edit.read_table, sap, key)
        print(f"  TABLE {key}: v{v} fields {flds}", flush=True)
        for r in rows[:show_rows]:
            print("     ", r, flush=True)
        return v, flds, rows
    except Exception as exc:  # noqa: BLE001
        print(f"  TABLE {key}: EXC {exc}"[:500], flush=True)
        return None


def run(sw):
    S = {"software": sw, "units": "kN_m_C"}
    nm = {**S, "file_path": str(OUT / f"d85b_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=100)
    for n, fc in (("C30", 30000.0), ("C40", 40000.0)):
        show(n, "define_material", {**S, "name": n, "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                    "unit_weight": 25.0, "fc": fc}, limit=100)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    pm = sap.PropMaterial
    g = raw("GetOConcrete_1 C30", lambda: pm.GetOConcrete_1("C30"))
    if g:
        vals = list(g[:10])
        raw("Set verbatim defaults", lambda: pm.SetOConcrete_1("C30", *vals))
        raw("Set verbatim + Temp 0", lambda: pm.SetOConcrete_1("C30", *vals, 0.0))
        v2 = vals[:]
        v2[3] = 1
        raw("Set SSType 1 only", lambda: pm.SetOConcrete_1("C30", *v2))
        raw("Get", lambda: pm.GetOConcrete_1("C30"))
        v3 = v2[:]
        v3[4] = 4
        raw("Set SSType 1 hys 4", lambda: pm.SetOConcrete_1("C30", *v3))
        v4 = v2[:]
        v4[5] = 0.002
        raw("Set SSType 1 strain 0.002", lambda: pm.SetOConcrete_1("C30", *v4))
        raw("Get", lambda: pm.GetOConcrete_1("C30"))
        raw("GetSSCurve C30", lambda: pm.GetSSCurve("C30"))
        raw("Old SetOConcrete C30 simple", lambda: pm.SetOConcrete("C30", 30000.0, False, 0.0, 1, 2, 0.002, 0.005,
                                                                   -0.1))
        raw("Get", lambda: pm.GetOConcrete_1("C30"))
        raw("GetSSCurve C30", lambda: pm.GetSSCurve("C30"))
        v5 = vals[:]
        v5[3] = 0
        raw("Set SSType 0 (user)", lambda: pm.SetOConcrete_1("C40", 40000.0, *v5[1:]))
        raw("Get C40", lambda: pm.GetOConcrete_1("C40"))
        if sw == "SAP2000":
            raw("SetOConcrete_2 sig", lambda: [m for m in dir(pm) if "Concrete" in m])
            raw("GetOConcrete_2 C30", lambda: pm.GetOConcrete_2("C30"))
    raw("frame section", lambda: sap.PropFrame.SetRectangle("COL", "C30", 0.5, 0.5))
    raw("GetSSCurve C30 with section", lambda: pm.GetSSCurve("C30", 0, [], [], [], "COL", 0.0, 0.0))
    raw("GetSSCurve C30 with section kw", lambda: pm.GetSSCurve("C30", SectName="COL"))

    # time dependence
    if sw == "SAP2000":
        td = pm.TimeDep
        raw("SetConcreteCEBFIP90", lambda: td.SetConcreteCEBFIP90("C30", True, True, True, 0.25, 70.0, 0.2, 5.0, 3.0,
                                                                  1, 5))
        for k in ("Material Properties 01 - General", "Material Properties 03b - Concrete Data",
                  "Material Properties 05a - Time Dependence - CEB-FIP 90"):
            table(sap, k)
    else:
        keys = ("Material Properties - General", "Material Properties - Concrete Data",
                "Material Properties - Time Dependence - CEB-FIP 90",
                "Material Properties - Time Dependence - ACI 209R-92", "Slab Property Definitions - Layers",
                "Wall Property Definitions - Layers", "Deck Property Definitions")
        for k in keys:
            table(sap, k, 3)
        t = table(sap, "Material Properties - Time Dependence - CEB-FIP 90")
        if t:
            v, flds, rows = t
            row = {f: "" for f in flds}
            row.update({"Material": "C30", "Name": "C30"})
            for f in flds:
                fl = f.lower()
                if "cement" in fl:
                    row[f] = "0.25"
                elif "humid" in fl:
                    row[f] = "70"
                elif "notion" in fl:
                    row[f] = "0.2"
                elif "betasc" in fl or "shrinkcoef" in fl:
                    row[f] = "5"
                elif "shrinkstart" in fl or "start" in fl:
                    row[f] = "3"
            print("  try row", row, flush=True)
            raw("import CEB-FIP 90 row", lambda: db_edit.write_table(sap, "Material Properties - Time Dependence - "
                                                                         "CEB-FIP 90", v, flds, rows + [row]))
            table(sap, "Material Properties - Time Dependence - CEB-FIP 90")
            table(sap, "Material Properties - Concrete Data", 3)
            table(sap, "Material Properties - General", 3)


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
