r"""Diagnostic 4 (0.22.0): ETABS Section Designer imports one by one - I, bars, column; polygon row layouts.
Output: tests\results\diag_sd_tables4.txt"""
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
POLY = "Section Designer Shapes - Concrete Polygon"
PTS = [[0, 0], [0.6, 0], [0.6, 0.15], [0.15, 0.15], [0.15, 0.6], [0, 0.6]]


def raw(sap, name, poly_rows, poly_fields=None):
    items = []
    v, f, rows = db_edit.read_table(sap, SEC)
    items.append((SEC, v, f, rows + [{"Name": name, "Material": "C30", "DesignType": "No Check/Design"}]))
    v, f, rows = db_edit.read_table(sap, POLY)
    print("   POLY edit fields", f, "existing", len(rows), flush=True)
    fields = poly_fields or [k for k in f if any(k in r for r in poly_rows)]
    items.append((POLY, v, fields, rows + poly_rows))
    for key, ver, flds, rr in items:
        flat = [str(r.get(k, "")) for r in rr for k in flds]
        print("   set", key, flds, sap.DatabaseTables.SetTableForEditingArray(key, ver, flds, len(rr), flat)[-1],
              flush=True)
    a = sap.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    print("   RAW", [x if not isinstance(x, str) else x[-700:] for x in a], flush=True)
    if len(a) == 6 and a[-1] != 0:
        sap.DatabaseTables.CancelTableEditing()
    print("   SD", sap.PropFrame.GetSDSection(name, "", 0, [], [], 0, 0, "", ""), flush=True)
    print("   props", sap.PropFrame.GetSectProps(name), flush=True)


def run():
    sw = "ETABS"
    S = {"software": sw, "units": "kN_m_C"}
    show("new model", "new_model", {**S, "file_path": str(OUT / "sd4_ETABS.edb"), "discard_open_model": True,
                                    "kind": "grid_only", "stories": 1, "story_height": 3.0, "spacing_x": 6.0,
                                    "spacing_y": 6.0}, limit=200)
    show("concrete", "define_material", {**S, "name": "C30", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                         "unit_weight": 25.0, "fc": 30000.0}, limit=200)
    show("rebar", "define_material", {**S, "name": "R420", "type": "rebar", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 0.0, "fy": 420000.0, "fu": 620000.0}, limit=200)
    show("steel", "define_material", {**S, "name": "ST", "type": "steel", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 0.0, "fy": 355000.0, "fu": 510000.0}, limit=200)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    show("SD I", "define_section_designer_section", {**S, "name": "SDI", "material": "ST",
                                                     "design_type": "general_steel", "shapes": [
        {"shape": "i_section", "center": [0, 0], "height": 0.3, "width": 0.15, "flange_thickness": 0.0107,
         "web_thickness": 0.0071}]}, limit=2500)
    show("SD tube pipe plate", "define_section_designer_section", {**S, "name": "SDS", "material": "ST",
                                                                   "design_type": "no_design", "shapes": [
        {"shape": "tube", "center": [0, 0], "height": 0.3, "width": 0.2, "flange_thickness": 0.01,
         "web_thickness": 0.01},
        {"shape": "pipe", "center": [0.5, 0], "diameter": 0.3, "thickness": 0.01},
        {"shape": "plate", "center": [1.0, 0], "width": 0.3, "thickness": 0.02}]}, limit=2500)
    show("SD column", "define_section_designer_section", {**S, "name": "SDCOL", "material": "C30",
                                                          "design_type": "concrete_column_check", "shapes": [
        {"shape": "solid_rect", "center": [0, 0], "height": 0.4, "width": 0.4},
        {"shape": "rebar_single", "center": [-0.14, -0.14], "bar_size": "25", "rebar_material": "R420"},
        {"shape": "rebar_single", "center": [0.14, 0.14], "bar_size": "25", "rebar_material": "R420"}]}, limit=2500)
    show("SD circle bars", "define_section_designer_section", {**S, "name": "SDR", "material": "C30",
                                                               "design_type": "concrete_column_check", "shapes": [
        {"shape": "solid_circle", "center": [0, 0], "diameter": 0.5},
        {"shape": "rebar_circle", "center": [0, 0], "diameter": 0.38, "bars": 8, "bar_size": "25",
         "rebar_material": "R420"}]}, limit=2500)
    show("SD line bars", "define_section_designer_section", {**S, "name": "SDLB", "material": "C30",
                                                             "design_type": "concrete_column_check", "shapes": [
        {"shape": "solid_rect", "center": [0, 0], "height": 0.4, "width": 0.4},
        {"shape": "rebar_line", "start": [-0.1, 0], "end": [0.1, 0], "spacing": 0.1, "bar_size": "25",
         "rebar_material": "R420"}]}, limit=2500)
    base = {"SectionType": "Frame", "ShapeName": "P1", "Material": "C30", "Reinforcing": "No"}
    print("\n== polygon A: every row full", flush=True)
    com(raw, sap, "PA", [{**base, "SectionName": "PA", "X": str(x), "Y": str(y)} for x, y in PTS])
    print("\n== polygon B: first row full, then X / Y only (with names)", flush=True)
    com(raw, sap, "PB", [{**base, "SectionName": "PB", "X": str(x), "Y": str(y)} if i == 0 else
                         {"SectionType": "Frame", "SectionName": "PB", "ShapeName": "P1", "X": str(x), "Y": str(y)}
                         for i, (x, y) in enumerate(PTS)])
    print("\n== polygon C: all fields incl. Color / ZOrder", flush=True)
    com(raw, sap, "PC", [{**base, "SectionName": "PC", "X": str(x), "Y": str(y), "Color": "Black", "ZOrder": "1",
                          "RebarMat": ""} for x, y in PTS])
    print("\n== polygon D: points clockwise", flush=True)
    com(raw, sap, "PD", [{**base, "SectionName": "PD", "X": str(x), "Y": str(y)} for x, y in reversed(PTS)])


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
