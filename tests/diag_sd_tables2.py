r"""Diagnostic 2 (0.22.0): how ETABS takes Section Designer shapes through its tables. Output: diag_sd_tables2.txt"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "sd_diag_output"
OUT.mkdir(parents=True, exist_ok=True)
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, com, finish, show  # noqa: E402,I001
import db_edit  # noqa: E402

SEC = "Frame Section Property Definitions - Section Designer"
RECT = "Section Designer Shapes - Concrete Rectangle"
BAR = "Section Designer Shapes - Reinforcing - Single Bar"


def state(sap, name):
    print("   GetSDSection", com(lambda: sap.PropFrame.GetSDSection(name, "", 0, [], [], 0, 0, "", "")), flush=True)
    print("   GetSectProps", com(lambda: sap.PropFrame.GetSectProps(name)), flush=True)
    for k in (RECT, BAR):
        try:
            print("   ", k, com(db_edit.read_table, sap, k)[2][:6], flush=True)
        except Exception as exc:  # noqa: BLE001
            print("   ", k, exc, flush=True)


def multi(sap, tables):
    """Set several tables, then apply once."""
    for key, rows in tables:
        v, flds, old = db_edit.read_table(sap, key)
        fields = [f for f in flds if any(f in r for r in rows)] if not old else flds
        allrows = old + rows
        flat = [str(r.get(f, "")) for r in allrows for f in fields]
        print("   set", key, fields, sap.DatabaseTables.SetTableForEditingArray(key, v, fields, len(allrows), flat),
              flush=True)
    try:
        return db_edit._apply(sap)
    except Exception as exc:  # noqa: BLE001
        sap.DatabaseTables.CancelTableEditing()
        return f"FAILED {exc}"


def rect(name, sn="R1"):
    return {"SectionType": "Frame", "SectionName": name, "ShapeName": sn, "Material": "C30", "XCenter": "0",
            "YCenter": "0", "Rotation": "0", "Height": "0.4", "Width": "0.3", "Reinforcing": "No"}


def bars(name, mat):
    return [{"SectionType": "Frame", "SectionName": name, "ShapeName": f"B{i}", "Material": mat, "XCenter": str(x),
             "YCenter": str(y), "RebarSize": "25"} for i, (x, y) in enumerate(((-0.1, -0.15), (0.1, -0.15),
                                                                                (0.1, 0.15), (-0.1, 0.15)), 1)]


def run():
    sw = "ETABS"
    show("new model", "new_model", {"software": sw, "units": "kN_m_C", "file_path": str(OUT / "sd2_ETABS.edb"),
                                    "discard_open_model": True, "kind": "grid_only", "stories": 1, "story_height": 3.0,
                                    "spacing_x": 6.0, "spacing_y": 6.0}, limit=200)
    S = {"software": sw, "units": "kN_m_C"}
    show("concrete", "define_material", {**S, "name": "C30", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                         "unit_weight": 25.0, "fc": 30000.0}, limit=200)
    show("rebar", "define_material", {**S, "name": "R420", "type": "rebar", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 77.0, "fy": 420000.0, "fu": 620000.0}, limit=200)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    print("\n== A: section + rectangle + bars in ONE apply", flush=True)
    print("  ", com(multi, sap, [(SEC, [{"Name": "SDA", "Material": "C30", "DesignType": "Concrete Column",
                                          "IsDesigned": "No"}]), (RECT, [rect("SDA")]), (BAR, bars("SDA", "R420"))]),
          flush=True)
    state(sap, "SDA")
    print("\n== B: section first, then rectangle (given fields only), separate applies", flush=True)
    print("  ", com(multi, sap, [(SEC, [{"Name": "SDB", "Material": "C30", "DesignType": "No Check/Design"}])]),
          flush=True)
    print("  ", com(multi, sap, [(RECT, [rect("SDB")])]), flush=True)
    state(sap, "SDB")
    print("\n== C: SD made in the GUI-equivalent way? existing rows re-written with an added shape", flush=True)
    print("  ", com(multi, sap, [(SEC, [{"Name": "SDC", "Material": "C30", "DesignType": "No Check/Design"}]),
                                 (RECT, [rect("SDC"), rect("SDC", "R2") | {"XCenter": "0.5"}])]), flush=True)
    state(sap, "SDC")
    print("\n== D: the API SetSDSection (ETABS returned 1 before) with DesignType 0", flush=True)
    print("  ", com(lambda: sap.PropFrame.SetSDSection("SDD", "C30", 0, -1, "", "")), flush=True)
    print("  ", com(lambda: sap.PropFrame.SetSDSection("SDD", "C30")), flush=True)


print("\n################ ETABS", flush=True)
try:
    com(bridge.launch_new, "ETABS", visible=True)
    run()
except Exception as exc:  # noqa: BLE001
    FAILED.append(str(exc))
    print("EXCEPTION", type(exc).__name__, exc, flush=True)
finally:
    try:
        com(bridge.disconnect, "ETABS", True, False)
    except Exception as exc:  # noqa: BLE001
        print("close", exc, flush=True)
sys.exit(finish())
