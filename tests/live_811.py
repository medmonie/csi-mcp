r"""Live test for 0.25.0 (roadmap 8.11): assignments and selection, checked against hand values.
Output: tests\results\live_811.txt (ends RESULT=OK).

A. Tension-only braces (S E): node N (0, 0, 3) on three pin-ended bars from A (-4, 0, 0), B (4, 0, 0), C (0, 0, 0),
   N held in Y and in rotation, H = 100 kN in +X at N, all bars EA equal.
   - linear static: braces share H - AN = +H / (2 x 0.8) = +62.5, BN = -62.5, CN = 0
   - nonlinear static with compression limit 0 on AN and BN: BN takes nothing, AN = H / 0.8 = +125, CN = -H x 3/4
     = -75 (statics of the two remaining bars)
B. Point local axes (S): fixed column base rotated a = 90 deg about Z, 10 kN in +X at the top: reaction in the
   point's local axes F1 = 0, F2 = R . (-X) = 10 (global reaction RX = -10); unrotated base: F1 = -10.
C. Read back in each program it applies to: lateral bracing (relative and absolute = relative x 6 m; replace /
   delete in S and E), T/C limits, design procedure, panel zones (column + doubler, springs + angle, link, delete),
   area edge constraint, link deletion.
D. Selection against the known model: by frame section, material, coordinate box, plane, invert (S typed; E / F
   emulated from object data).
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "live_811_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}
U = "kN_m_C"
H = 100.0


def ok(r):
    return isinstance(r, dict) and not r.get("_error") and not isinstance(r, Exception)


def close(a, b, tol):
    return a is not None and abs(a - b) <= tol


def new_model(sw, tag):
    S = {"software": sw}
    nm = {**S, "units": U, "file_path": str(OUT / f"l811_{tag}_{sw}{EXT[sw]}"), "discard_open_model": True,
          "overwrite": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
    show(f"new model {tag}", "new_model", nm, limit=200)
    show("steel", "define_material", {**S, "units": U, "name": "ST", "type": "steel", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 0.0, "fy": 345000.0, "fu": 450000.0}, limit=150)
    show("conc", "define_material", {**S, "units": U, "name": "CC", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                     "unit_weight": 0.0, "fc": 30000.0}, limit=150)
    show("W", "define_frame_section", {**S, "units": U, "name": "W1", "shape": "i_section", "material": "ST",
                                       "depth": 0.4, "width": 0.2, "flange_thickness": 0.015,
                                       "web_thickness": 0.01}, limit=150)
    show("bar", "define_frame_section", {**S, "units": U, "name": "BAR", "shape": "pipe", "material": "ST",
                                         "depth": 0.1, "web_thickness": 0.005}, limit=150)
    show("R", "define_frame_section", {**S, "units": U, "name": "R1", "shape": "rectangle", "material": "CC",
                                       "depth": 0.5, "width": 0.3}, limit=150)
    return S


def frames(S, items, section):
    r = show(f"frames {section}", "create_frames", {**S, "units": U, "section": section, "frames": [
        {"start": a, "end": b, "name": n} for a, b, n in items]}, limit=500)
    return {c["name"]: c["points"] for c in r.get("created", [])} if ok(r) else {}


def axial(sw, S, names, case):
    r = show(f"frame forces {case}", "get_results", {**S, "units": U, "result_type": "frame_forces",
                                                     "load_cases_or_combos": [case], "names": names,
                                                     "max_rows": 500, "export": "none"}, limit=400)
    out = {}
    for x in (r.get("rows", []) if ok(r) else []):
        if x.get("LoadCase") == case and x.get("Frame") in names:
            out.setdefault(x["Frame"], []).append(float(x.get("P", 0) or 0))
    return {k: sum(v) / len(v) for k, v in out.items()}


# --------------------------------------------------------------------------- A + B: analysis checks

def part_tc(sw):
    S = new_model(sw, "tc")
    made = frames(S, [([-4, 0, 0], [0, 0, 3], "AN"), ([4, 0, 0], [0, 0, 3], "BN"), ([0, 0, 0], [0, 0, 3], "CN")],
                  "BAR")
    if len(made) != 3:
        check(f"{sw}: truss bars created", False, made)
        return
    n = made["AN"][1]
    supports = [made[k][0] for k in ("AN", "BN", "CN")]
    show("release moments", "assign_frame_properties", {**S, "units": U, "frame_names": list(made),
                                                         "releases": {"i": ["M2", "M3", "T"], "j": ["M2", "M3"]}},
         limit=300)
    show("pins", "assign_point_properties", {**S, "units": U, "point_names": supports, "restraint": "pinned"},
         limit=200)
    show("node N held in Y and rotation", "assign_point_properties", {
        **S, "units": U, "point_names": [n], "restraint": [False, True, False, True, True, True]}, limit=200)
    show("pattern", "define_load_pattern", {**S, "units": U, "name": "PH", "type": "other",
                                            "self_weight_multiplier": 0.0}, limit=200)
    show("H at N", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [n],
                                                 "load_pattern": "PH", "direction": "X", "values": [H]}, limit=200)
    show("nonlinear case", "define_load_case", {**S, "units": U, "name": "NLH", "type": "nonlinear_static",
                                                "loads": [{"kind": "load_pattern", "name": "PH", "factor": 1.0}]},
         limit=400)
    dry = show("dry run", "assign_frame_tc_limits", {**S, "units": U, "frame_names": ["AN", "BN"],
                                                     "compression_limit": 0.0, "dry_run": True}, limit=400)
    r = show("tension only", "assign_frame_tc_limits", {**S, "units": U, "frame_names": ["AN", "BN"],
                                                        "compression_limit": 0.0}, limit=600)
    check(f"{sw}: dry run then T/C limits read back (compression 0, no tension limit)", ok(dry) and dry["dry_run"]
          and ok(r) and r["read_back"]["AN"] == {"compression_limit": 0.0, "tension_limit": None}
          and not r.get("not_kept_as_given"), r)
    cases = bridge.try_name_list(bridge.sap_model(sw), ["LoadCases"])[0] or []
    show("run PH and NLH only", "set_analysis_options", {**S, "run_cases": {c: c in ("PH", "NLH") for c in cases}},
         limit=300)
    show("run", "run_analysis", {**S}, limit=800)
    lin = axial(sw, S, ["AN", "BN", "CN"], "PH")
    nl = axial(sw, S, ["AN", "BN", "CN"], "NLH")
    print(f"  linear {lin}; nonlinear {nl}", flush=True)
    check(f"{sw}: linear AN = +62.5, BN = -62.5, CN = 0 (kN)", close(lin.get("AN"), 62.5, 0.01)
          and close(lin.get("BN"), -62.5, 0.01) and close(lin.get("CN"), 0.0, 0.01), lin)
    check(f"{sw}: tension-only nonlinear AN = +125, BN = 0, CN = -75 (kN)", close(nl.get("AN"), 125.0, 0.05)
          and close(nl.get("BN"), 0.0, 0.05) and close(nl.get("CN"), -75.0, 0.05), nl)


def part_axes():
    sw = "SAP2000"
    S = new_model(sw, "axes")
    made = frames(S, [([0, 0, 0], [0, 0, 3], "K1"), ([5, 0, 0], [5, 0, 3], "K2")], "W1")
    b1, t1 = made["K1"]
    b2, t2 = made["K2"]
    show("fixed bases", "assign_point_properties", {**S, "units": U, "point_names": [b1, b2], "restraint": "fixed"},
         limit=200)
    r = show("rotate base of K1", "assign_point_local_axes", {**S, "point_names": [b1], "angles": [90, 0, 0]},
             limit=400)
    check(f"{sw}: point local axes read back [90, 0, 0]", ok(r) and r["read_back"][b1]["angles"] == [90.0, 0.0, 0.0],
          r)
    show("pattern", "define_load_pattern", {**S, "units": U, "name": "PX", "type": "other",
                                            "self_weight_multiplier": 0.0}, limit=200)
    show("10 kN +X at the tops", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point",
                                                                "object_names": [t1, t2], "load_pattern": "PX",
                                                                "direction": "X", "values": [10.0]}, limit=200)
    cases = bridge.try_name_list(bridge.sap_model(sw), ["LoadCases"])[0] or []
    show("run PX only", "set_analysis_options", {**S, "run_cases": {c: c == "PX" for c in cases}}, limit=300)
    show("run", "run_analysis", {**S}, limit=800)
    rr = show("reactions", "get_results", {**S, "units": U, "result_type": "joint_reactions", "names": [b1, b2],
                                           "load_cases_or_combos": ["PX"], "export": "none"}, limit=600)
    rx = {x.get("Joint"): x for x in (rr.get("rows", []) if ok(rr) else [])}
    f1 = float(rx.get(b1, {}).get("F1", 99) or 0)
    f2 = float(rx.get(b1, {}).get("F2", 99) or 0)
    g1 = float(rx.get(b2, {}).get("F1", 99) or 0)
    print(f"  rotated base F1 {f1} F2 {f2}; plain base F1 {g1}", flush=True)
    check(f"{sw}: reaction in rotated local axes F1 = 0, F2 = 10; unrotated F1 = -10", close(f1, 0.0, 1e-6)
          and close(f2, 10.0, 1e-6) and close(g1, -10.0, 1e-6), rx)


# --------------------------------------------------------------------------- C + D: read-back and selection

def part_assign(sw):
    S = new_model(sw, "assign")
    z = 3.0
    zb = 0.0
    made = frames(S, [([0, 0, z], [6, 0, z], "BM"), ([0, 0, zb], [0, 0, z], "C1"), ([6, 0, zb], [6, 0, z], "C2")],
                  "W1")
    made.update(frames(S, [([0, 6, z], [6, 6, z], "RB")], "R1"))
    if len(made) != 4:
        check(f"{sw}: frames created", False, made)
        return
    j = made["BM"][0]
    # lateral bracing
    br = [{"kind": "point", "location": "top", "start": 0.5}, {"kind": "uniform", "location": "bottom",
                                                              "start": 0.0, "end": 1.0}]
    r = show("bracing", "assign_lateral_bracing", {**S, "units": U, "frame_names": ["BM"], "bracing": br}, limit=900)
    got = r.get("read_back", {}).get("BM", []) if ok(r) else []
    check(f"{sw}: bracing read back - point top 0.5 (3.0 m), uniform bottom 0-1 (0-6 m)", ok(r)
          and not r.get("not_kept_as_given") and len(got) == 2
          and got[0] == {"kind": "point", "location": "top", "start": 0.5, "start_abs": 3.0}
          and got[1].get("end_abs") == 6.0, got)
    r = show("bracing absolute 1.5 m", "assign_lateral_bracing", {**S, "units": U, "frame_names": ["BM"],
                                                                  "relative": False, "bracing": [
                                                                      {"kind": "point", "location": "all",
                                                                       "start": 1.5}]}, limit=900)
    got = r.get("read_back", {}).get("BM", []) if ok(r) else []
    check(f"{sw}: absolute 1.5 m kept as relative 0.25 (1.5 m)", ok(r) and not r.get("not_kept_as_given")
          and any(g["location"] == "all" and g["start"] == 0.25 and g["start_abs"] == 1.5 for g in got), got)
    if sw != "SAFE":
        r = show("bracing replace", "assign_lateral_bracing", {**S, "units": U, "frame_names": ["BM"],
                                                               "operation": "replace", "bracing": [
                                                                   {"kind": "point", "location": "bottom",
                                                                    "start": 0.25}]}, limit=900)
        got = r.get("read_back", {}).get("BM", []) if ok(r) else []
        check(f"{sw}: replace leaves only the new bracing", ok(r) and not r.get("not_kept_as_given")
              and len(got) == 1 and got[0]["location"] == "bottom", got)
        r = show("bracing delete", "assign_lateral_bracing", {**S, "units": U, "frame_names": ["BM"],
                                                              "operation": "delete"}, limit=600)
        check(f"{sw}: delete leaves no bracing", ok(r) and r["read_back"]["BM"] == [] and
              not r.get("not_kept_as_given"), r)
    else:
        show("SAFE refuses bracing delete", "assign_lateral_bracing", {**S, "frame_names": ["BM"],
                                                                       "operation": "delete"}, expect_error=True,
             limit=300)
    # T/C limits on SAFE (read back only; SAFE has no axial results)
    if sw == "SAFE":
        r = show("T/C limits", "assign_frame_tc_limits", {**S, "units": U, "frame_names": ["C1"],
                                                          "compression_limit": 5.0, "tension_limit": 100.0},
                 limit=500)
        check(f"{sw}: T/C limits read back 5 / 100", ok(r) and r["read_back"]["C1"] ==
              {"compression_limit": 5.0, "tension_limit": 100.0}, r)
    # design procedure
    if sw == "SAP2000":
        r = show("no design", "assign_design_procedure", {**S, "frame_names": ["BM", "RB"], "procedure": "no_design"},
                 limit=400)
        r2 = show("from material", "assign_design_procedure", {**S, "frame_names": ["BM", "RB"],
                                                               "procedure": "from_material"}, limit=400)
        check(f"{sw}: design procedure no_design, then steel / concrete from the material", ok(r) and ok(r2)
              and r["read_back"] == {"BM": "no_design", "RB": "no_design"}
              and r2["read_back"] == {"BM": "steel_frame", "RB": "concrete_frame"}, [r, r2])
    else:
        r = show("composite beam", "assign_design_procedure", {**S, "frame_names": ["BM"],
                                                               "procedure": "composite_beam"}, limit=400)
        r2 = show("no design", "assign_design_procedure", {**S, "frame_names": ["BM", "RB"],
                                                           "procedure": "no_design"}, limit=400)
        r3 = show("from material", "assign_design_procedure", {**S, "frame_names": ["RB"],
                                                               "procedure": "from_material"}, limit=400)
        check(f"{sw}: design procedure composite beam, no design, concrete from the material", ok(r) and ok(r2)
              and ok(r3) and r["read_back"]["BM"] == "composite_beam" and r2["read_back"] ==
              {"BM": "no_design", "RB": "no_design"} and r3["read_back"]["RB"] == "concrete_frame", [r, r2, r3])
    # panel zones
    r = show("panel zone column + doubler", "assign_panel_zone", {**S, "units": U, "point_names": [j],
                                                                  "property": "column",
                                                                  "doubler_thickness": 0.012}, limit=500)
    check(f"{sw}: panel zone from column, doubler 0.012 m", ok(r) and not r.get("not_kept_as_given")
          and r["read_back"][j]["doubler_thickness"] == 0.012, r)
    pz = {**S, "units": U, "point_names": [j], "property": "springs", "k_major": 1.0e6, "k_minor": 2.0e6,
          "local_axis_angle": 30.0}
    if sw == "SAP2000":
        pz["connectivity"] = "braces_to_other"
    r = show("panel zone springs", "assign_panel_zone", pz, limit=500)
    check(f"{sw}: panel zone springs 1e6 / 2e6, user angle 30" + (", braces" if sw == "SAP2000" else ""),
          ok(r) and not r.get("not_kept_as_given") and r["read_back"][j]["k_minor"] == 2.0e6
          and r["read_back"][j]["local_axis_angle"] == 30.0, r)
    if sw != "SAFE":
        show("link property", "define_link_property", {**S, "units": U, "name": "PZL", "type": "linear",
                                                       "stiffness": {"U1": 1.0e6}}, limit=300)
        r = show("panel zone link", "assign_panel_zone", {**S, "units": U, "point_names": [j], "property": "link",
                                                          "link_property": "PZL"}, limit=500)
        check(f"{sw}: panel zone from link property PZL", ok(r) and not r.get("not_kept_as_given")
              and r["read_back"][j]["link_property"] == "PZL", r)
    r = show("panel zone delete", "assign_panel_zone", {**S, "point_names": [j], "operation": "delete"}, limit=400)
    check(f"{sw}: panel zone deleted", ok(r) and r["read_back"][j] is None, r)
    # edge constraint
    if sw == "SAP2000":
        show("shell", "define_area_section", {**S, "units": U, "name": "SL", "kind": "shell_thin", "material": "CC",
                                              "thickness": 0.2}, limit=200)
    else:
        show("slab", "define_area_section", {**S, "units": U, "name": "SL", "kind": "slab", "material": "CC",
                                             "thickness": 0.2, "shell_type": "shell_thin"}, limit=200)
    za = z if sw == "ETABS" else 0.0
    ar = show("area", "create_areas", {**S, "units": U, "section": "SL", "areas": [
        {"points": [[10, 0, za], [14, 0, za], [14, 4, za], [10, 4, za]]}]}, limit=400)
    an = ar["created"][0]["name"] if ok(ar) else "?"
    r1 = show("edge off", "assign_area_edge_constraint", {**S, "area_names": [an], "constrained": False}, limit=300)
    r2 = show("edge on", "assign_area_edge_constraint", {**S, "area_names": [an], "constrained": True}, limit=300)
    check(f"{sw}: edge constraint off then on, read back", ok(r1) and ok(r2) and r1["read_back"] == {an: False}
          and r2["read_back"] == {an: True}, [r1, r2])
    # links
    if sw != "SAFE":
        show("link property", "define_link_property", {**S, "units": U, "name": "LK", "type": "linear",
                                                       "stiffness": {"U1": 1.0e6}}, limit=300)
        # two-joint link (ETABS 23.2 refuses a one-joint link through LinkObj.AddByPoint - measured, not 8.11)
        lk = show("link", "create_links", {**S, "property": "LK", "links": [{"point1": made["C1"][0],
                                                                            "point2": made["C2"][0],
                                                                            "name": "LK1"}]}, limit=300)
        r = show("delete link", "delete_objects", {**S, "kind": "links", "names": ["LK1"]}, limit=300)
        left = bridge.try_name_list(bridge.sap_model(sw), ["LinkObj"])[0] or []
        check(f"{sw}: link created then deleted", ok(lk) and ok(r) and r["deleted"] == 1 and "LK1" not in left, r)
    # selection
    sel = {}
    for label, prm in (("section W1", {"mode": "property", "property_kind": "frame_section", "property": "W1"}),
                       ("material CC", {"mode": "property", "property_kind": "material", "property": "CC"}),
                       ("box", {"mode": "coordinate_range", "box": [-0.5, 6.5, -0.5, 0.5, 2.5, 3.5]}),
                       ("plane", {"mode": "plane", "plane": "xz", "plane_point": made["BM"][0]}),
                       ("invert", {"mode": "invert"})):
        r = show(f"select {label}", "select_objects", {**S, "units": U, **prm}, limit=800)
        sel[label] = r.get("names", {}) if ok(r) else {"error": r}
    bm_pts = sorted(made["BM"])
    check(f"{sw}: select by section W1 = BM, C1, C2", sorted(sel["section W1"].get("frame", [])) ==
          ["BM", "C1", "C2"], sel["section W1"])
    check(f"{sw}: select by material CC = RB and the area", sel["material CC"].get("frame") == ["RB"]
          and (sw == "SAP2000" or sel["material CC"].get("area") == [an]), sel["material CC"])
    check(f"{sw}: select box = BM and its two points", sel["box"].get("frame") == ["BM"]
          and sorted(sel["box"].get("point", [])) == bm_pts, sel["box"])
    check(f"{sw}: select plane XZ through y = 0 = BM, C1, C2 (not RB / area)",
          sorted(sel["plane"].get("frame", [])) == ["BM", "C1", "C2"] and "area" not in sel["plane"], sel["plane"])
    check(f"{sw}: invert of the plane selection holds RB and the area", "RB" in sel["invert"].get("frame", [])
          and an in sel["invert"].get("area", []) and "BM" not in sel["invert"].get("frame", []), sel["invert"])
    show("clear", "select_objects", {**S, "mode": "clear"}, limit=100)


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
for sw in targets:
    print(f"\n################ {sw}", flush=True)
    t0 = len(FAILED)
    try:
        com(bridge.launch_new, sw, visible=True)
        part_assign(sw)
        if sw in ("SAP2000", "ETABS"):
            part_tc(sw)
        if sw == "SAP2000":
            part_axes()
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
