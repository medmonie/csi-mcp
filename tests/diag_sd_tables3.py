r"""Diagnostic 3 (0.22.0): consecutive Section Designer imports in ETABS through the action, raw log on failure.
Output: tests\results\diag_sd_tables3.txt"""
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


def raw_apply(sap):
    a = sap.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    print("   RAW", a, flush=True)
    return a


def run():
    sw = "ETABS"
    S = {"software": sw, "units": "kN_m_C"}
    show("new model", "new_model", {**S, "file_path": str(OUT / "sd3_ETABS.edb"), "discard_open_model": True,
                                    "kind": "grid_only", "stories": 1, "story_height": 3.0, "spacing_x": 6.0,
                                    "spacing_y": 6.0}, limit=200)
    show("concrete", "define_material", {**S, "name": "C30", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                         "unit_weight": 25.0, "fc": 30000.0}, limit=200)
    sap = bridge.sap_model(sw)
    rect = {"shape": "solid_rect", "center": [0, 0], "height": 0.4, "width": 0.3}
    for name in ("SD1", "SD2"):
        show(f"{name} rect", "define_section_designer_section", {**S, "name": name, "material": "C30",
                                                                 "design_type": "no_design", "shapes": [rect]},
             limit=600)
        v, f, rows = com(db_edit.read_table, sap, SEC)
        print("  SEC rows now:", rows, flush=True)
    # raw: re-write the SEC table exactly as read, apply
    print("\n== rewrite SEC as read", flush=True)
    v, f, rows = com(db_edit.read_table, sap, SEC)
    print("  set", com(lambda: sap.DatabaseTables.SetTableForEditingArray(
        SEC, v, f, len(rows), [r[k] for r in rows for k in f])), flush=True)
    com(raw_apply, sap)
    print("\n== rewrite SEC as read, without GUID / Color", flush=True)
    v, f, rows = com(db_edit.read_table, sap, SEC)
    f2 = [k for k in f if k not in ("GUID", "Color")]
    print("  set", com(lambda: sap.DatabaseTables.SetTableForEditingArray(
        SEC, v, f2, len(rows), [r[k] for r in rows for k in f2])), flush=True)
    com(raw_apply, sap)
    print("\n== add SD3 polygon via the action", flush=True)
    show("SD3 polygon", "define_section_designer_section", {**S, "name": "SD3", "material": "C30",
                                                            "design_type": "no_design", "shapes": [
        {"shape": "polygon", "points": [[0, 0], [0.6, 0], [0.6, 0.15], [0.15, 0.15], [0.15, 0.6], [0, 0.6]]}]},
         limit=1500)


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
