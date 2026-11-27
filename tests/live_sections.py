r"""Phase 8.4 + ETABS staged construction (0.22.0), in NEW instances, each against a hand calculation.

    run_live_sections.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_sections.txt

Non-prismatic (SAP2000, ETABS): cantilever L = 4 m, b = 0.3 m, depth 0.8 m at the support tapering linearly to 0.4 m
at the tip (one variable segment, EI33 cubic: exact for a linear depth), E = 25 GPa, nu = 0.2, tip load P = 100 kN:
tip deflection = integral of P (L - x)^2 / (E I(x)) + P / (G As(x)), As = 5/6 b h (Simpson, 2000 strips) within 1 %.
SAFE: the section read back.
Section Designer (SAP2000, ETABS): a T of two rectangles (flange 1.0 x 0.2, web 0.3 x 0.6): area, I33 (about the horizontal
axis), I22 by hand within 0.5 %; an L polygon's area; an I shape's area; a 0.4 x 0.4 column of one rectangle and 8 single
25 mm bars, 'concrete column check', ACI 318-19 under axial load only: PMM ratio = Pu / phi Pn,max
(0.65 x 0.80 x [0.85 f'c (Ag - Ast) + fy Ast]) within 2 %.
Staged construction (ETABS): two spans L = 8 m, stage 1 adds span 1 with 10 kN/m, stage 2 adds span 2 with 10 kN/m:
M_B = w L^2 / 16 = 40 kN m (one-shot: w L^2 / 8 = 80 kN m).
Section Designer (ETABS, through its tables): the same tee, polygon, I shape and column check; circle / line bars drawn.
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "sections_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}
U = "kN_m_C"
E, NU = 2.5e7, 0.2
FC, FY = 30000.0, 420000.0


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def rows(r):
    return r.get("rows", []) if isinstance(r, dict) else []


def created(r):
    return r["created"][0] if isinstance(r, dict) and r.get("created") else {}


def taper_tip(P, L, b, h0, h1):
    G = E / (2 * (1 + NU))
    n = 2000
    tot = 0.0
    for i in range(n + 1):
        x = L * i / n
        h = h0 + (h1 - h0) * x / L
        f = P * (L - x) ** 2 / (E * b * h ** 3 / 12) + P / (G * 5 / 6 * b * h)
        tot += f * (1 if i in (0, n) else 4 if i % 2 else 2)
    return tot * L / n / 3


def base(sw, S):
    nm = {**S, "units": U, "file_path": str(OUT / f"sections_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=8.0, spacing_y=8.0)
    show("new model", "new_model", nm, limit=300)
    show("concrete", "define_material", {**S, "units": U, "name": "C30", "type": "concrete", "E": E, "poisson": NU,
                                         "unit_weight": 25.0, "fc": FC}, limit=300)   # mass for the modal case; loads carry no self weight
    for name, d in (("D80", 0.8), ("D40", 0.4)):
        show(f"section {name}", "define_frame_section", {**S, "units": U, "name": name, "shape": "rectangle",
                                                         "material": "C30", "depth": d, "width": 0.3}, limit=300)


def nonprismatic(sw, S):
    seg = {"start_section": "D80", "end_section": "D40", "length": 1.0, "length_type": "variable", "ei33": "cubic",
           "ei22": "cubic"}
    r = show("non-prismatic", "define_nonprismatic_section", {**S, "units": U, "name": "TAPER", "segments": [seg]},
             limit=1500)
    check(f"{sw}: non-prismatic segments read back as given", isinstance(r, dict) and not r.get("_error")
          and r.get("read_back") == [seg] and not r.get("not_kept_as_given"), r)
    d = show("details", "get_definition_details", {**S, "units": U, "kind": "frame_sections", "names": ["TAPER"]},
             limit=1500)
    items = (d.get("items") or d.get("frame_sections") or []) if isinstance(d, dict) else []
    check(f"{sw}: get_definition_details shows the segments", bool(items) and items[0].get("segments") == [seg], d)
    show("refused: missing section", "define_nonprismatic_section", {**S, "name": "BAD", "segments": [
        {**seg, "end_section": "NOPE"}]}, expect_error=True, limit=300)
    if sw == "SAFE":
        return
    L, P = 4.0, 100.0
    z = 0.0 if sw == "SAP2000" else 3.0
    fr = show("cantilever", "create_frames", {**S, "units": U, "section": "TAPER", "frames": [
        {"start": [0, 0, z], "end": [L, 0, z]}]}, limit=500)
    c = created(fr)
    pts = c.get("points", [])
    show("fixed", "assign_point_properties", {**S, "point_names": [pts[0]], "restraint": [True] * 6}, limit=200)
    show("pattern", "define_load_pattern", {**S, "name": "PTIP", "type": "other", "self_weight_multiplier": 0},
         limit=200)
    show("tip load", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [pts[1]],
                                                    "load_pattern": "PTIP", "values": [P], "direction": "Gravity"},
         limit=200)
    show("analysis", "run_analysis", {**S}, limit=600)
    dr = show("tip deflection", "get_results", {**S, "units": U, "result_type": "joint_displacements",
                                                "names": [pts[1]], "load_cases_or_combos": ["PTIP"]}, limit=1200)
    u3 = [num(x.get("U3")) for x in rows(dr)]
    hand = taper_tip(P, L, 0.3, 0.8, 0.4)
    print(f"  tip U3 {u3}, hand {hand:.6e} m (prismatic D80 would be {P * L ** 3 / (3 * E * 0.3 * 0.512 / 12):.6e})",
          flush=True)
    check(f"{sw}: tapered cantilever tip deflection = hand integral within 1 %",
          bool(u3) and abs(abs(u3[0]) - hand) <= 0.01 * hand, {"program": u3, "hand": hand})
    com(lambda: bridge.sap_model(sw).SetModelIsLocked(False))      # test set-up: unlock for the next part


def staged_etabs(S):
    L, w, z = 8.0, 10.0, 3.0
    for name in ("W1", "W2"):
        show(f"pattern {name}", "define_load_pattern", {**S, "name": name, "type": "other",
                                                        "self_weight_multiplier": 0}, limit=200)
    show("beam section", "define_frame_section", {**S, "units": U, "name": "B", "shape": "rectangle",
                                                  "material": "C30", "depth": 0.6, "width": 0.3}, limit=300)
    f1 = created(show("span 1", "create_frames", {**S, "units": U, "section": "B", "frames": [
        {"start": [0, 20, z], "end": [L, 20, z]}]}, limit=400))
    f2 = created(show("span 2", "create_frames", {**S, "units": U, "section": "B", "frames": [
        {"start": [L, 20, z], "end": [2 * L, 20, z]}]}, limit=400))
    p1, p2 = f1.get("points", []), f2.get("points", [])
    for pt, r in ((p1[0], [True, True, True, True, False, False]), (p1[1], [False, True, True, False, False, False]),
                  (p2[1], [False, True, True, False, False, False])):
        show(f"restraint {pt}", "assign_point_properties", {**S, "point_names": [pt], "restraint": r}, limit=200)
    for pat, fr in (("W1", f1.get("name")), ("W2", f2.get("name"))):
        show(f"{pat} load", "assign_point_or_frame_load", {**S, "units": U, "target_type": "frame",
                                                           "object_names": [fr], "load_pattern": pat, "values": [w],
                                                           "direction": "Gravity"}, limit=200)
    for g, fr, pts in (("G1", f1.get("name"), p1), ("G2", f2.get("name"), [p2[1]])):
        show(f"group {g}", "set_group", {**S, "operation": "define", "name": g}, limit=200)
        show(f"group {g} add", "set_group", {**S, "operation": "add", "name": g, "frames": [fr], "points": pts},
             limit=200)
    sc = show("staged case", "define_staged_construction", {**S, "name": "STAGED", "geometric_nonlinearity": "none",
                                                            "stages": [
        {"duration": 0, "operations": [{"operation": "add", "group": "G1"},
                                       {"operation": "load", "group": "G1", "load_name": "W1", "factor": 1}]},
        {"duration": 0, "operations": [{"operation": "add", "group": "G2"},
                                       {"operation": "load", "group": "G2", "load_name": "W2", "factor": 1}]}]},
        limit=2500)
    check("ETABS: staged case read back (2 stages, 2 operations each)", isinstance(sc, dict) and not sc.get("_error")
          and [len(s["operations"]) for s in sc.get("read_back", [])] == [2, 2], sc)
    show("one-shot case", "define_load_case", {**S, "name": "ONESHOT", "type": "linear_static",
                                               "loads": [{"name": "W1", "factor": 1}, {"name": "W2", "factor": 1}]},
         limit=300)
    show("analysis", "run_analysis", {**S}, limit=800)
    for case, hand in (("STAGED", w * L * L / 16), ("ONESHOT", w * L * L / 8)):
        r = show(f"span 2 forces {case}", "get_results", {**S, "units": U, "result_type": "frame_forces",
                                                          "names": [f2.get("name")], "load_cases_or_combos": [case],
                                                          "step_option": "last_step"}, limit=1500)
        mb = [num(x.get("M3")) for x in rows(r) if abs(num(x.get("Station"))) < 1e-6]
        print(f"  {case}: M at B {mb}, hand {hand}", flush=True)
        check(f"ETABS {case}: |M_B| = {hand:g} within 1 %", bool(mb) and abs(abs(mb[-1]) - hand) <= 0.01 * hand, mb)


def section_designer(S, sap, sw):
    show("rebar", "define_material", {**S, "units": U, "name": "R420", "type": "rebar", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 0.0, "fy": FY, "fu": 620000.0}, limit=300)
    show("steel", "define_material", {**S, "units": U, "name": "ST", "type": "steel", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 0.0, "fy": 355000.0, "fu": 510000.0}, limit=300)
    # T of two rectangles
    fl, fd, wb, wd = 1.0, 0.2, 0.3, 0.6
    yf, yw = 0.3, -0.1
    r = show("SD tee", "define_section_designer_section", {**S, "units": U, "name": "SDT", "material": "C30",
                                                           "design_type": "no_design", "shapes": [
        {"shape": "solid_rect", "center": [0, yf], "height": fd, "width": fl},
        {"shape": "solid_rect", "center": [0, yw], "height": wd, "width": wb}]}, limit=2500)
    a1, a2 = fl * fd, wb * wd
    area = a1 + a2
    yc = (a1 * yf + a2 * yw) / area
    ix = fl * fd ** 3 / 12 + a1 * (yf - yc) ** 2 + wb * wd ** 3 / 12 + a2 * (yw - yc) ** 2   # about horizontal axis
    iy = fd * fl ** 3 / 12 + wd * wb ** 3 / 12
    pr = r.get("properties", {}) if isinstance(r, dict) else {}
    print(f"  hand: area {area:.6f}, I about horizontal {ix:.6e}, about vertical {iy:.6e}; program {pr}", flush=True)
    ok = bool(pr) and abs(pr["area"] - area) <= 0.005 * area
    check(f"{sw}: SD tee area by hand within 0.5 %", ok, pr)
    check(f"{sw}: SD tee I33 = I about the horizontal axis, I22 about the vertical, within 0.5 %",
          bool(pr) and abs(pr["i33"] - ix) <= 0.005 * ix and abs(pr["i22"] - iy) <= 0.005 * iy, pr)
    check(f"{sw}: SD tee shapes read back", isinstance(r, dict) and [s.get("shape") for s in
                                                                      r.get("read_back", {}).get("shapes", [])]
          == ["solid_rect", "solid_rect"] and r["read_back"]["shapes"][0].get("width") == fl, r)
    # L polygon
    pts = [[0, 0], [0.6, 0], [0.6, 0.15], [0.15, 0.15], [0.15, 0.6], [0, 0.6]]
    if sw == "ETABS":
        show("ETABS refuses a polygon (its import fails inside the program)", "define_section_designer_section", {
            **S, "name": "SDL", "material": "C30", "design_type": "no_design",
            "shapes": [{"shape": "polygon", "points": pts}]}, expect_error=True, limit=400)
    rl = {} if sw == "ETABS" else show("SD polygon", "define_section_designer_section", {**S, "units": U, "name": "SDL", "material": "C30",
                                                                "design_type": "no_design",
                                                                "shapes": [{"shape": "polygon", "points": pts}]},
              limit=2000)
    al = 0.6 * 0.15 + 0.15 * 0.45
    if sw == "SAP2000":
        check(f"{sw}: SD L polygon area by hand within 0.5 %", isinstance(rl, dict) and rl.get("properties")
              and abs(rl["properties"]["area"] - al) <= 0.005 * al, rl)
    # steel I
    ri = show("SD I", "define_section_designer_section", {**S, "units": U, "name": "SDI", "material": "ST",
                                                          "design_type": "general_steel", "shapes": [
        {"shape": "i_section", "center": [0, 0], "height": 0.3, "width": 0.15, "flange_thickness": 0.0107,
         "web_thickness": 0.0071}]}, limit=2000)
    ai = 2 * 0.15 * 0.0107 + (0.3 - 2 * 0.0107) * 0.0071
    check(f"{sw}: SD I-shape area by hand within 0.5 %", isinstance(ri, dict) and ri.get("properties")
          and abs(ri["properties"]["area"] - ai) <= 0.005 * ai, ri)
    # reinforced circle read back (SAP2000; ETABS refuses bars inside a solid shape)
    names = [str(n) for n in (com(lambda: sap.PropRebar.GetNameList())[1] or [])]
    size = next((n for n in names if n.startswith("25")), None) or next((n for n in names if "25" in n), names[0])
    circ = {"shape": "solid_circle", "center": [0, 0], "diameter": 0.5, "reinforced": True, "bars": 8, "cover": 0.05,
            "bar_size": size, "rebar_material": "R420"}
    if sw == "ETABS":
        show("ETABS refuses bars inside a solid shape", "define_section_designer_section", {
            **S, "name": "SDC", "material": "C30", "design_type": "concrete_column_design", "shapes": [circ]},
            expect_error=True, limit=400)
        rl2 = show("SD line and circle bars", "define_section_designer_section", {
            **S, "units": U, "name": "SDR", "material": "C30", "design_type": "concrete_column_check", "shapes": [
                {"shape": "solid_circle", "center": [0, 0], "diameter": 0.5},
                {"shape": "rebar_circle", "center": [0, 0], "diameter": 0.38, "bars": 8, "bar_size": size,
                 "rebar_material": "R420"},
                {"shape": "rebar_line", "start": [-0.1, 0], "end": [0.1, 0], "spacing": 0.1, "bar_size": size,
                 "rebar_material": "R420"}]}, limit=2500)
        # ETABS draws the circle as a polygon: its area is 0.6 % under pi d^2 / 4 (measured)
        check("ETABS: SD circle area by hand within 1 % with circle / line bars drawn (3 shapes)",
              isinstance(rl2, dict) and not rl2.get("_error") and len(rl2.get("read_back", {}).get("shapes", [])) == 3
              and abs(rl2["properties"]["area"] - 3.141592653589793 * 0.25 ** 2) <= 0.01 * 0.19635, rl2)
    if sw == "SAP2000":
        rc = show("SD circle", "define_section_designer_section", {**S, "units": U, "name": "SDC", "material": "C30",
                                                                   "design_type": "concrete_column_design",
                                                                   "shapes": [circ]}, limit=2000)
        sh = rc.get("read_back", {}).get("shapes", [{}])[0] if isinstance(rc, dict) else {}
        check(f"{sw}: SD reinforced circle read back (8 bars, cover, size)", sh.get("bars") == 8
              and sh.get("bar_size") == size and abs(num(sh.get("cover")) - 0.05) < 1e-6, rc)
    show("refused: bars without rebar material", "define_section_designer_section", {**S, "name": "BAD",
         "material": "C30", "design_type": "no_design", "shapes": [{"shape": "rebar_single", "center": [0, 0],
                                                                     "bar_size": size}]}, expect_error=True, limit=300)
    # column check with single bars
    show("units", "set_units", {**S, "unit_preset": U}, limit=200)
    ab = float(com(lambda: sap.PropRebar.GetRebarProps(size, 0.0, 0.0))[0])
    B, c = 0.4, 0.4 / 2 - 0.06
    bars = [[x, y] for x, y in ((-c, -c), (0, -c), (c, -c), (-c, 0), (c, 0), (-c, c), (0, c), (c, c))]
    rs = show("SD column", "define_section_designer_section", {**S, "units": U, "name": "SDCOL", "material": "C30",
                                                               "design_type": "concrete_column_check", "shapes": [
        {"shape": "solid_rect", "center": [0, 0], "height": B, "width": B},
        *({"shape": "rebar_single", "center": b, "bar_size": size, "rebar_material": "R420"} for b in bars)]},
        limit=2500)
    check(f"{sw}: SD column has 9 shapes", isinstance(rs, dict) and len(rs.get("read_back", {}).get("shapes", []))
          == 9, rs)
    H = 1.0 if sw == "SAP2000" else 3.0      # ETABS: the story height of this model
    fr = created(show("column", "create_frames", {**S, "units": U, "section": "SDCOL", "frames": [
        {"start": [50, 0, 0], "end": [50, 0, H]}]}, limit=400))
    pts = fr.get("points", [])
    show("fixed base", "assign_point_properties", {**S, "point_names": [pts[0]], "restraint": [True] * 6}, limit=200)
    show("top held", "assign_point_properties", {**S, "point_names": [pts[1]],
                                                 "restraint": [True, True, False, True, True, False]}, limit=200)
    ag, ast = B * B, 8 * ab
    phi_pn = 0.65 * 0.80 * (0.85 * FC * (ag - ast) + FY * ast)
    pu = 0.7 * phi_pn
    show("pattern", "define_load_pattern", {**S, "name": "PAX", "type": "other", "self_weight_multiplier": 0},
         limit=200)
    show("axial load", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [pts[1]],
                                                      "load_pattern": "PAX", "values": [pu], "direction": "Gravity"},
         limit=200)
    show("combination", "define_combination", {**S, "name": "CAX", "items": [{"name": "PAX", "factor": 1.0}]},
         limit=300)
    show("analysis", "run_analysis", {**S}, limit=600)
    ds = {**S, "kind": "concrete_frame", "code": "ACI 318-19", "strength_combinations": {"CAX": True}}
    if sw == "SAP2000":
        ds["auto_generate_combinations"] = False
    dr = show("design settings", "set_design_settings", ds, limit=1500)
    check(f"{sw}: design combination CAX read back as selected", isinstance(dr, dict) and not dr.get("_error")
          and dr.get("settings_now", {}).get("combinations", {}).get("strength") == ["CAX"], dr)
    show("design", "run_design", {**S, "kind": "concrete_frame"}, limit=800)
    res = show("design results", "get_design_results", {**S, "units": U, "kind": "concrete_frame",
                                                         "names": [fr.get("name")]}, limit=2500)
    cols = res.get("columns", []) if isinstance(res, dict) else []
    ratio = max((num(c.get("PMMRatio")) for c in cols), default=0.0)
    print(f"  Ab {ab:.6e}, phi Pn,max {phi_pn:.3f} kN, Pu {pu:.3f} -> hand 0.7000; program {ratio}", flush=True)
    if sw == "SAP2000":
        check(f"{sw}: SD column checked: PMM ratio = Pu / phi Pn,max within 2 %",
              bool(cols) and all(c.get("Option") == "check" for c in cols) and abs(ratio - 0.7) <= 0.014, cols)
    else:
        # ETABS 23.2 designs a Section Designer concrete column even when it is stored 'Designed? = No' (measured:
        # DesignOpt 'Design', no PMM ratio, As = the drawn bars); its design ran with the selected combination
        check(f"{sw}: SD column designed with the selected combination CAX (ETABS reports SD columns as designed)",
              bool(cols) and all(c.get("PMMCombo") == "CAX" for c in cols), cols)


def run(sw):
    S = {"software": sw}
    base(sw, S)
    sap = bridge.sap_model(sw)
    nonprismatic(sw, S)
    if sw == "ETABS":
        staged_etabs(S)
        com(lambda: sap.SetModelIsLocked(False))      # test set-up: unlock for the next part
    if sw in ("SAP2000", "ETABS"):
        section_designer(S, sap, sw)
    if sw == "SAFE":
        show("SD refused in SAFE (no setters, no tables)", "define_section_designer_section", {
            **S, "name": "X", "material": "C30", "design_type": "no_design",
            "shapes": [{"shape": "solid_rect", "center": [0, 0], "height": 0.5, "width": 0.3}]},
            expect_error=True, limit=300)


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
