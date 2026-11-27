r"""Diagnostic (finishing Phase 5): what each program offers for strips, soil, tendons, cables, solids, hinges, staged
construction, moving loads, towers and file export.

    run_diag_phase5c.bat      Output: tests\results\diag_phase5c.txt

For each program (new scratch instance, blank / grid model): which SapModel objects exist among the candidates,
their methods with parameter names (type library), and the database tables (all tables) whose key holds one of
the topic words, with their fields.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_phase5c"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish  # noqa: E402,I001

CANDIDATES = ["CableObj", "PropCable", "TendonObj", "PropTendon", "SolidObj", "PropSolid", "PropAreaSpring",
              "PropPointSpring", "PropLineSpring", "PropAreaSoil", "SoilProp", "DesignStrip", "StripObj", "Strip",
              "Tower", "Story", "LineElm", "Lane", "LaneObj", "VehicleClass", "Vehicle", "BridgeObj",
              "LoadCases.StaticNonlinearStaged", "LoadCases.Moving", "LoadCases.MovingLoad", "PropFrame",
              "FrameObj", "AreaObj", "File", "Analyze", "PointObj", "DatabaseTables", "EditGeneral",
              "PropFrame.SetModifiers", "SectCut", "GenDispl", "NamedSet"]
WANT = ("hinge", "tendon", "cable", "solid", "strip", "soil", "spring", "stage", "lane", "vehicle", "moving",
        "tower", "export", "import", "subgrade", "pt ", "post", "slab rib", "punching")
TOPICS = ("strip", "tendon", "soil", "spring", "cable", "solid", "hinge", "staged", "stage", "lane", "vehicle",
          "moving", "tower", "subgrade")


def methods(obj, label, filt=None):
    specs = None
    for get in (lambda: type(obj)._methods_, lambda: type(obj)._type_._methods_):
        try:
            specs = get()
            break
        except Exception:  # noqa: BLE001
            continue
    if not specs:
        print(f"{label}: (no typed methods)", flush=True)
        return
    names = [s[1] for s in specs]
    print(f"{label}: {len(specs)} methods", flush=True)
    for s in specs:
        name, pf = s[1], s[3]
        if filt and not any(w in name.lower() for w in filt):
            continue
        params = [f"{'*' if (p[0] & 2) else ''}{p[1]}" for p in pf if p[1] != "pRetVal"]
        print(f"   {name}({', '.join(params)})", flush=True)
    return names


def get_obj(sap, path):
    o = sap
    for part in path.split("."):
        o = getattr(o, part)
    return o


def probe(sw):
    sap = bridge.sap_model(sw)
    sap.InitializeNewModel(6)
    if sw == "SAP2000":
        sap.File.NewBlank()
    else:
        sap.File.NewGridOnly(2, 3.5, 3.5, 3, 3, 6.0, 6.0)
    for c in CANDIDATES:
        try:
            o = get_obj(sap, c)
        except Exception:  # noqa: BLE001
            print(f"{c}: -", flush=True)
            continue
        if c in ("FrameObj", "AreaObj", "File", "Analyze", "PointObj", "DatabaseTables", "PropFrame",
                 "EditGeneral"):
            methods(o, c, WANT)
        else:
            methods(o, c)
    o = sap.DatabaseTables.GetAllTables()
    keys, imp = [str(k) for k in o[1]], list(o[3])
    hits = [(k, imp[i]) for i, k in enumerate(keys) if any(t in k.lower() for t in TOPICS)]
    print(f"\nTABLES ({len(hits)} of {len(keys)}):", flush=True)
    for k, it in hits:
        try:
            f = sap.DatabaseTables.GetAllFieldsInTable(k)
            names = [str(x) for x in f[2]]
            print(f"  [{it}] {k}: {names}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{it}] {k}: EXC {exc}", flush=True)


for sw in ("SAFE", "SAP2000", "ETABS"):
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=False)
        com(probe, sw)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED {sw}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        try:
            com(bridge.disconnect, sw, True, False)
        except Exception as exc:  # noqa: BLE001
            print("close", exc)
sys.exit(finish())
