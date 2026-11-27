r"""Phase 8.3 (0.21.0): set_frame_rebar in SAP2000, ETABS (and SAFE read-back) in NEW instances; 0.22.0: the column
check also in ETABS (design combinations read from 'Concrete Frame Design Load Combination Data').

    run_live_rebar.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_rebar.txt

Column 0.4 x 0.4 m, 3 m, fixed base, f'c 30 MPa, rebar fy 420 MPa, 8 bars (3 per face) of the program's 25 mm size,
checked (not designed) under an axial load Pu only, ACI 318-19 (named by the test as the designer would):
phi Pn,max = 0.65 x 0.80 x [0.85 f'c (Ag - Ast) + fy Ast] (ACI 318-19 22.4.2.1, tied); the program's PMM ratio must be
Pu / phi Pn,max within 2 % (the code's minimum moment stays on the flat 0.80 cap for e = 27 mm < 0.1 h).
Beam section: covers and the four end areas set and read back.
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "rebar_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}
U = "kN_m_C"
FC, FY, B, H = 30000.0, 420000.0, 0.4, 1.0


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def bar_size(sap):
    names = [str(n) for n in (com(lambda: sap.PropRebar.GetNameList())[1] or [])]
    pick = next((n for n in names if n.startswith("25")), None) or next((n for n in names if "25" in n), names[0])
    g = com(lambda: sap.PropRebar.GetRebarProps(pick, 0.0, 0.0))
    print("  rebar sizes:", names[:20], "picked", pick, "->", g, flush=True)
    return pick, names


def run(sw):
    S = {"software": sw}
    nm = {**S, "units": U, "file_path": str(OUT / f"rebar_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=H, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=300)
    sap = bridge.sap_model(sw)
    show("concrete", "define_material", {**S, "units": U, "name": "C30", "type": "concrete", "E": 2.6e7,
                                         "poisson": 0.2, "unit_weight": 25.0, "fc": FC}, limit=300)
    show("rebar", "define_material", {**S, "units": U, "name": "R420", "type": "rebar", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 77.0, "fy": FY, "fu": 620000.0}, limit=300)
    show("circular section", "define_frame_section", {**S, "units": U, "name": "CIRC", "shape": "circle",
                                                      "material": "C30", "depth": 0.5}, limit=300)
    for name in ("COL", "BM"):
        show(f"section {name}", "define_frame_section", {**S, "units": U, "name": name, "shape": "rectangle",
                                                         "material": "C30", "depth": B, "width": B}, limit=300)
    size, sizes = bar_size(sap)
    # unit conversion: the rebar property is read in the present units; set kN_m_C first through an action
    show("units", "set_units", {**S, "unit_preset": U}, limit=200)
    ab = float(com(lambda: sap.PropRebar.GetRebarProps(size, 0.0, 0.0))[0])
    col = {"pattern": "rectangular", "confinement": "ties", "cover": 0.04, "bars_3_face": 3, "bars_2_face": 3,
           "bar_size": size, "tie_size": next((n for n in sizes if n.startswith("10")), sizes[0]),
           "tie_spacing": 0.15, "ties_2_dir": 3, "ties_3_dir": 3, "mode": "check"}
    r = show("column rebar", "set_frame_rebar", {**S, "units": U, "section": "COL", "rebar_material": "R420",
                                                 "confinement_material": "R420", "column": col}, limit=1500)
    print("  GetTypeRebar COL:", com(lambda: sap.PropFrame.GetTypeRebar("COL")), flush=True)
    check(f"{sw}: column rebar read back as given", isinstance(r, dict) and not r.get("_error")
          and r.get("read_back", {}).get("kind") == "column" and not r.get("not_kept_as_given"), r)
    circ = {"pattern": "circular", "confinement": "spiral", "cover": 0.04, "bars_circular": 8, "bar_size": size,
            "tie_size": col["tie_size"], "tie_spacing": 0.075, "mode": "design"}
    rc = show("circular column rebar", "set_frame_rebar", {**S, "units": U, "section": "CIRC",
                                                           "rebar_material": "R420", "confinement_material": "R420",
                                                           "column": circ}, limit=1500)
    print("  raw GetRebarColumn COL / CIRC:", com(lambda: sap.PropFrame.GetRebarColumn(
        "COL", "", "", 0, 0, 0.0, 0, 0, 0, "", "", 0.0, 0, 0, False)), com(lambda: sap.PropFrame.GetRebarColumn(
        "CIRC", "", "", 0, 0, 0.0, 0, 0, 0, "", "", 0.0, 0, 0, False)), flush=True)
    check(f"{sw}: circular spiral column read back", isinstance(rc, dict) and not rc.get("_error")
          and rc.get("read_back", {}).get("bars_circular") == 8, rc)
    beam = {"cover_top": 0.05, "cover_bottom": 0.06, "top_area_i": 0.0012, "top_area_j": 0.0011,
            "bottom_area_i": 0.0008, "bottom_area_j": 0.0009}
    rb = show("beam rebar", "set_frame_rebar", {**S, "units": U, "section": "BM", "rebar_material": "R420",
                                                "confinement_material": "R420", "beam": beam}, limit=1500)
    print("  GetTypeRebar BM:", com(lambda: sap.PropFrame.GetTypeRebar("BM")), flush=True)
    check(f"{sw}: beam rebar read back as given", isinstance(rb, dict) and not rb.get("_error")
          and rb.get("read_back", {}).get("kind") == "beam" and not rb.get("not_kept_as_given"), rb)
    d = show("definition details", "get_definition_details", {**S, "units": U, "kind": "frame_sections",
                                                              "names": ["COL", "BM"]}, limit=2500)
    recs = {x.get("name"): x for x in (d.get("items") or d.get("frame_sections") or [])} if isinstance(d, dict) else {}
    check(f"{sw}: get_definition_details shows the rebar", bool(recs) and all("rebar" in v for v in recs.values()),
          recs)
    show("not concrete refused", "set_frame_rebar", {**S, "section": "NOPE", "rebar_material": "R420",
                                                     "confinement_material": "R420", "column": col},
         expect_error=True, limit=300)
    if sw == "SAFE":
        return       # SAFE: frame design of columns is not the subject here (read-back only)
    # ---- design check of the column under axial load only
    fr = show("column", "create_frames", {**S, "units": U, "section": "COL", "frames": [
        {"start": [0, 0, 0], "end": [0, 0, H], "name": "C1"}]}, limit=500)
    pts = fr["created"][0]["points"] if isinstance(fr, dict) and fr.get("created") else []
    col_name = fr["created"][0]["name"] if isinstance(fr, dict) and fr.get("created") else "C1"
    show("fixed base", "assign_point_properties", {**S, "point_names": [pts[0]], "restraint": [True] * 6}, limit=200)
    show("top held laterally", "assign_point_properties", {**S, "point_names": [pts[1]],
                                                           "restraint": [True, True, False, True, True, False]},
         limit=200)
    ag, ast = B * B, 8 * ab
    phi_pn = 0.65 * 0.80 * (0.85 * FC * (ag - ast) + FY * ast)
    pu = 0.7 * phi_pn
    show("pattern", "define_load_pattern", {**S, "name": "PAX", "type": "other", "self_weight_multiplier": 0},
         limit=200)
    show("axial load", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [pts[1]],
                                                      "load_pattern": "PAX", "values": [pu], "direction": "Gravity"},
         limit=200)
    show("combination", "define_combination", {**S, "name": "C1", "items": [{"name": "PAX", "factor": 1.0}]},
         limit=300)
    if sw == "ETABS":       # the column top is held laterally: no free mass, so the Modal case cannot start
        show("modal case off", "set_analysis_options", {**S, "run_cases": {"Modal": False}}, limit=300)
    show("analysis", "run_analysis", {**S}, limit=500)
    ds = {**S, "kind": "concrete_frame", "code": "ACI 318-19", "strength_combinations": {"C1": True}}
    if sw == "SAP2000":
        ds["auto_generate_combinations"] = False
    dr = show("design settings", "set_design_settings", ds, limit=1500)
    check(f"{sw}: design combination C1 read back as selected", isinstance(dr, dict) and not dr.get("_error")
          and dr.get("settings_now", {}).get("combinations", {}).get("strength") == ["C1"], dr)
    show("design", "run_design", {**S, "kind": "concrete_frame"}, limit=800)
    res = show("design results", "get_design_results", {**S, "units": U, "kind": "concrete_frame",
                                                         "names": [col_name]}, limit=2500)
    cols = res.get("columns", []) if isinstance(res, dict) else []
    ratio = max((c["PMMRatio"] for c in cols), default=0.0)
    print(f"  Ab {ab:.6e} m2, Ast {ast:.6e}, phi Pn,max {phi_pn:.3f} kN, Pu {pu:.3f} kN -> hand ratio "
          f"{pu / phi_pn:.4f}; program {ratio}; options {[c.get('Option') for c in cols]}", flush=True)
    check(f"{sw}: column checked with the given bars: PMM ratio = Pu / phi Pn,max within 2 %",
          bool(cols) and all(c.get("Option") == "check" for c in cols) and abs(ratio - pu / phi_pn) <= 0.02 * pu / phi_pn,
          {"program": ratio, "hand": pu / phi_pn})


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
for sw in targets:
    print(f"\n################ {sw} (new instance, visible={visible})", flush=True)
    t0 = len(FAILED)
    try:
        com(bridge.launch_new, sw, visible=visible)
        run(sw)
    except Exception as exc:  # noqa: BLE001
        FAILED.append(f"{sw}: {type(exc).__name__}: {exc}")
        print("EXCEPTION", exc, flush=True)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("close", exc, flush=True)
    print(f"{sw}: {'OK' if len(FAILED) == t0 else 'FAILED'}", flush=True)
sys.exit(finish())
