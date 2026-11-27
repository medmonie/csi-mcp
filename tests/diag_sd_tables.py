r"""Diagnostic (0.22.0): the Section Designer tables of ETABS / SAFE - fields, descriptions, and a trial import of a
rectangle with single bars, read back through the API. Output: tests\results\diag_sd_tables.txt"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "sd_diag_output"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, com, finish, show  # noqa: E402,I001
from csi_bridge import as_list  # noqa: E402
import db_edit  # noqa: E402

targets = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or ["ETABS", "SAFE"]
WANT = ("Frame Section Property Definitions - Section Designer", "Section Designer Shapes - Concrete Rectangle",
        "Section Designer Shapes - Concrete Polygon", "Section Designer Shapes - Concrete Circle",
        "Section Designer Shapes - Reinforcing - Single Bar", "Section Designer Shapes - Reinforcing - Line Bar",
        "Section Designer Shapes - Reinforcing - Circle Bar", "Section Designer Shapes - Reinforcing Along Shape Edges",
        "Section Designer Shapes - Reinforcing At Shape Corners", "Section Designer Shapes - Steel I-Section",
        "Section Designer Shapes - Steel Plate", "Section Designer Shapes - Steel Tube",
        "Section Designer Shapes - Steel Pipe", "Section Designer Shapes - Concrete Tee",
        "Section Designer Shapes - Concrete L", "Section Designer Shapes - Concrete Box")


def run(sw):
    ext = {"ETABS": ".edb", "SAFE": ".fdb"}[sw]
    show("new model", "new_model", {"software": sw, "units": "kN_m_C", "file_path": str(OUT / f"sd_{sw}{ext}"),
                                    "discard_open_model": True, "kind": "grid_only", "stories": 1, "story_height": 3.0,
                                    "spacing_x": 6.0, "spacing_y": 6.0}, limit=200)
    S = {"software": sw, "units": "kN_m_C"}
    show("concrete", "define_material", {**S, "name": "C30", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                         "unit_weight": 25.0, "fc": 30000.0}, limit=200)
    show("rebar", "define_material", {**S, "name": "R420", "type": "rebar", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 77.0, "fy": 420000.0, "fu": 620000.0}, limit=200)
    show("steel", "define_material", {**S, "name": "ST", "type": "steel", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 77.0, "fy": 355000.0, "fu": 510000.0}, limit=200)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    sizes = [str(n) for n in (com(lambda: sap.PropRebar.GetNameList())[1] or [])]
    print("rebar sizes", sizes, flush=True)
    o = com(lambda: sap.DatabaseTables.GetAllTables())
    keys = [str(k) for k in as_list(o[1])]
    print("SD tables present:", [k for k in keys if "Section Designer" in k], flush=True)
    for k in WANT:
        if k not in keys:
            print("MISSING", k, flush=True)
            continue
        f = com(lambda k=k: sap.DatabaseTables.GetAllFieldsInTable(k))
        print(f"\n== {k}", flush=True)
        for a, b, c, d in zip(as_list(f[2]), as_list(f[3]), as_list(f[4]), as_list(f[5]) if len(f) > 6 else []):
            print(f"   {a} | {b} | {c} | {d}", flush=True)
        try:
            v, flds, rows = com(db_edit.read_table, sap, k)
            print("   edit fields:", flds, "rows:", rows[:3], flush=True)
        except Exception as exc:  # noqa: BLE001
            print("   edit read:", exc, flush=True)
    # SAP2000-made reference: draw the same in SAP2000? not here. Trial import in this program:
    size = next((n for n in sizes if n.startswith("25")), sizes[0] if sizes else "")
    trials = [
        ("Frame Section Property Definitions - Section Designer",
         [{"Name": "SDX", "Material": "C30", "DesignType": "Concrete Column", "IsDesigned": "No"}]),
        ("Section Designer Shapes - Concrete Rectangle",
         [{"SectionType": "Frame", "SectionName": "SDX", "ShapeName": "R1", "Material": "C30", "XCenter": "0",
           "YCenter": "0", "Height": "0.4", "Width": "0.4", "Rotation": "0", "Reinforcing": "No"}]),
        ("Section Designer Shapes - Reinforcing - Single Bar",
         [{"SectionType": "Frame", "SectionName": "SDX", "ShapeName": f"B{i}", "XCenter": str(x), "YCenter": str(y),
           "RebarSize": size, "RebarMat": "R420"} for i, (x, y) in enumerate(((-0.14, -0.14), (0.14, -0.14),
                                                                              (0.14, 0.14), (-0.14, 0.14)), 1)]),
    ]
    for key, new in trials:
        if key not in keys:
            continue
        try:
            v, flds, rows = com(db_edit.read_table, sap, key)
            full = [{f: r.get(f, "") for f in flds} for r in new]
            print(f"\n-- import {key}: unknown fields {[f for r in new for f in r if f not in flds]}", flush=True)
            res = com(db_edit.write_table, sap, key, v, flds, rows + full)
            print("   ", res, flush=True)
        except Exception as exc:  # noqa: BLE001
            print("   import failed:", type(exc).__name__, exc, getattr(exc, "hint", ""), flush=True)
    print("GetTypeOAPI SDX", com(lambda: sap.PropFrame.GetTypeOAPI("SDX")), flush=True)
    print("GetSDSection SDX", com(lambda: sap.PropFrame.GetSDSection("SDX", "", 0, [], [], 0, 0, "", "")), flush=True)
    print("GetSectProps SDX", com(lambda: sap.PropFrame.GetSectProps("SDX")), flush=True)
    for key, _ in trials:
        if key in keys:
            try:
                print("read", key, com(db_edit.read_table, sap, key)[2][:6], flush=True)
            except Exception as exc:  # noqa: BLE001
                print("read", key, exc, flush=True)


for sw in targets:
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=True)
        run(sw)
    except Exception as exc:  # noqa: BLE001
        FAILED.append(f"{sw}: {exc}")
        print("EXCEPTION", type(exc).__name__, exc, flush=True)
    finally:
        try:
            com(bridge.disconnect, sw, True, False)
        except Exception as exc:  # noqa: BLE001
            print("close", exc, flush=True)
sys.exit(finish())
