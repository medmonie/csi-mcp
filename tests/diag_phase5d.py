r"""Diagnostic (Phase 5, SAFE / ETABS batch): row formats of strips and PT tendons from a COPY of CSI's
'ACI 318-19 PT-SL Ex001.FDB', and ETABS -> SAFE through files.

    run_diag_phase5d.bat      Output: tests\results\diag_phase5d.txt
"""
import os
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_phase5d"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish  # noqa: E402,I001

SRC = HERE.parent / "CSI Solved Examples" / "Safe" / "Models" / "Design Examples" / "Concrete and PT Slab" / \
    "ACI 318-19 PT-SL Ex001.FDB"
KEYS = ["Strip Object Connectivity", "Tendon Object Connectivity", "Tendon Assignments - Properties",
        "Tendon Load Assignments - Jacking Stress", "Tendon Load Assignments - Loss Options", "Tendon Section Properties",
        "Material Properties - Tendon Data", "Spring Property Definitions - Area Springs", "Area Assignments - Area Springs",
        "Concrete Slab Design Overwrites - Strip Based", "Load Pattern Definitions", "Point Object Connectivity",
        "Slab Property Definitions", "Load Case Definitions - Summary"]


def rows(sap, key, limit=40):
    try:
        o = sap.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
        fields, n, data = [str(x) for x in o[1]], int(o[2]), [str(x) for x in o[3]]
        print(f"\n== {key}: {n} rows, fields {fields}", flush=True)
        for i in range(min(n, limit)):
            r = dict(zip(fields, data[i * len(fields):(i + 1) * len(fields)]))
            print("   ", {k: v for k, v in r.items() if v not in ("", "None")}, flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\n== {key}: EXC {exc}", flush=True)


def safe_pt():
    tmp = Path(tempfile.mkdtemp()) / "pt.FDB"
    shutil.copy(SRC, tmp)
    sap = bridge.sap_model("SAFE")
    print("open", sap.File.OpenFile(str(tmp)), flush=True)
    sap.SetPresentUnits(6)
    for k in KEYS:
        rows(sap, k, 60 if "Tendon Object" in k else 30)
    print("tendons:", list(sap.TendonObj.GetNameList()[1])[:20], flush=True)
    for t in list(sap.TendonObj.GetNameList()[1])[:2]:
        print("geometry", t, sap.TendonObj.GetTendonGeometry(t, 0, [], [], [], "Global"), flush=True)


def etabs_export():
    sap = bridge.sap_model("ETABS")
    sap.InitializeNewModel(6)
    print("NewSteelDeck", sap.File.NewSteelDeck(2, 3.5, 3.5, 3, 3, 6.0, 6.0), flush=True)
    d = Path(tempfile.mkdtemp())
    print("save", sap.File.Save(str(d / "deck.EDB")), flush=True)
    for ft, ext in ((1, "e2k"), (2, "xlsx"), (3, "mdb"), (4, "txt"), (5, "xml")):
        try:
            r = sap.File.ExportFile(str(d / f"deck.{ext}"), ft)
            f = d / f"deck.{ext}"
            print(f"ExportFile type {ft}: {r}; exists {f.exists()} size {f.stat().st_size if f.exists() else 0}",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"ExportFile type {ft}: EXC {exc}", flush=True)
    return d


def safe_import(d):
    sap = bridge.sap_model("SAFE")
    for name, ft in (("deck.e2k", 1), ("deck.txt", 4), ("deck.xml", 5)):
        for typ in (0, 1, 2, 3):
            try:
                sap.InitializeNewModel(6)
                r = sap.File.ImportFile(str(d / name), ft, typ)
                n_area = sap.AreaObj.Count()
                n_frame = sap.FrameObj.Count()
                stories = list(sap.Story.GetNameList()[1]) if hasattr(sap, "Story") else []
                print(f"SAFE ImportFile({name}, {ft}, {typ}): {r}; areas {n_area}, frames {n_frame}, stories {stories}",
                      flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"SAFE ImportFile({name}, {ft}, {typ}): EXC {exc}", flush=True)


try:
    print("################ SAFE PT example copy", flush=True)
    com(bridge.launch_new, "SAFE", visible=False)
    com(safe_pt)
finally:
    print(com(bridge.disconnect, "SAFE", True, False), flush=True)
d = None
try:
    print("\n################ ETABS export", flush=True)
    com(bridge.launch_new, "ETABS", visible=False)
    d = com(etabs_export)
finally:
    print(com(bridge.disconnect, "ETABS", True, False), flush=True)
if d:
    try:
        print("\n################ SAFE import", flush=True)
        com(bridge.launch_new, "SAFE", visible=False)
        com(safe_import, d)
    finally:
        print(com(bridge.disconnect, "SAFE", True, False), flush=True)
sys.exit(finish())
