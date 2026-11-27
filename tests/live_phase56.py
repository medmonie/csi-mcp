r"""Live test of 0.13.0 (Phase 5 program-specific modelling + Phase 6 design) in NEW scratch instances.

    run_live_phase56.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_phase56.txt

Hand checks (every code, combination and value below is the test author's - the designer's - choice):
  steel beam  W-like I-section d = 18, bf = 7.5, tf = 0.57, tw = 0.355 in, Fy = 50 ksi, simply supported L = 240 in,
              wu = 0.145 k/in in pattern WU, combination ULS = 1.0 WU, code AISC 360-16 (named here).
              Mu = 0.145 x 240^2 / 8 = 1044 k-in; Z = bf tf (d - tf) + tw (d - 2tf)^2 / 4 = 99.74 in3;
              phi Mp = 0.9 x 50 x Z = 4488 k-in. With Lb = 240 in (> Lp = 70 in) LTB governs: ratio > 0.2326.
              LTB unbraced length overwrite 0.1 (Lb = 24 in < Lp): ratio = 1044 / 4488 = 0.2326.
  links       SAP2000: two-joint link U1 k = 1000 kN/m (other directions rigid) from a fixed point up to point 2,
              one-joint link U1 k = 500 kN/m at point 3; 10 kN down at 2 and 3 -> U3 = -0.010 and -0.020 m.
  seismic     ETABS: ASCE 7-16, Ss 1.5, S1 0.6, site B (ASCE 7-16 Tables 11.4-1 / 2: Fa = 0.9, Fv = 0.8 - read
              back from the program), R 8, Ie 1, user T = 0.5 s: SDS = 2/3 x 0.9 x 1.5 = 0.9, SD1 = 2/3 x 0.8 x 0.6
              = 0.32, Cs = min(SDS / R, SD1 / (T R)) = min(0.1125, 0.08) = 0.08 (> 0.044 SDS = 0.0396,
              > 0.5 S1 / R = 0.0375) -> base shear = 0.08 W (W as ETABS reports it).
  stories     ETABS: base 0, stories S1 144 in, S2 120 in -> elevations 144 / 264 in; diaphragm and pier read back.
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase56_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
EXT = {"SAP2000": ".sdb", "ETABS": ".edb", "SAFE": ".fdb"}
K = "kip_in_F"
MU = 0.145 * 240 ** 2 / 8
Z = 7.5 * 0.57 * (18 - 0.57) + 0.355 * (18 - 2 * 0.57) ** 2 / 4
RATIO_BRACED = MU / (0.9 * 50 * Z)


def near(got, want, rel=1e-4, absolute=1e-9):
    return got is not None and abs(got - want) <= rel * abs(want) + absolute


def beam_spec(z, y=0.0):
    pin = [True, True, True, True, False, False]
    roll = [False, True, True, True, False, False]
    return {"units": K,
            "materials": [{"name": "STL50", "type": "steel", "E": 29000.0, "poisson": 0.3, "unit_weight": 0.000283565,
                           "fy": 50.0, "fu": 65.0}],
            "frame_sections": [{"name": "I18", "material": "STL50", "shape": "i_section", "depth": 18.0,
                                "width": 7.5, "flange_thickness": 0.57, "web_thickness": 0.355}],
            "load_patterns": [{"name": "WU", "type": "other", "self_weight_multiplier": 0.0}],
            "points": [{"name": "BA", "xyz": [0, y, z], "restraint": pin},
                       {"name": "BB", "xyz": [240, y, z], "restraint": roll}],
            "frames": [{"name": "B1", "start": "BA", "end": "BB", "section": "I18"}],
            "loads": [{"target_type": "frame", "object_names": ["B1"], "load_pattern": "WU", "values": [0.145],
                       "direction": "gravity"}],
            "combinations": [{"name": "ULS", "type": "linear_add", "items": [{"name": "WU", "factor": 1.0,
                                                                             "kind": "case"}]}]}


def b1_ratio(S):
    r = show("design results B1", "get_design_results", {**S, "kind": "steel_frame", "names": ["B1"], "units": K},
             limit=1500)
    rows = [x for x in (r.get("steel", []) if isinstance(r, dict) else []) if x.get("RatioType") == "PMM"]
    return max((x["Ratio"] for x in rows), default=None)


def steel_design(S, sw):
    """Phase 6 on the spec beam: code and combination named by the designer, preferences round trip, design,
    LTB overwrite hand check, design report."""
    st = show("design settings before", "get_design_settings", {**S, "kind": "steel_frame", "units": K}, limit=1500)
    prefs = st.get("preferences", {}) if isinstance(st, dict) else {}
    if isinstance(st, dict) and not st.get("combinations", {}).get("strength"):
        show("run design refused: no combination chosen", "run_design", {**S, "kind": "steel_frame"},
             expect_error=True, limit=500)
    pf = next((f for f in ("SRatioLimit", "DCLimit", "PatLLF") if f in prefs), None)
    ds = {**S, "kind": "steel_frame", "code": "AISC 360-16", "strength_combinations": {"ULS": True}}
    if pf:
        ds["preferences"] = {pf: 0.95}
    sd = show("set code, combination and a preference", "set_design_settings", ds, limit=1500)
    check(f"{sw}: code AISC 360-16 and ULS read back", isinstance(sd, dict)
          and sd.get("settings_now", {}).get("code") == "AISC 360-16", sd)
    st2 = show("design settings after", "get_design_settings", {**S, "kind": "steel_frame", "units": K}, limit=1500)
    if pf and isinstance(st2, dict):
        check(f"{sw}: preference {pf} = 0.95 read back", near(float(st2["preferences"].get(pf, 0)), 0.95), st2)
        show("preference restored", "set_design_settings", {**S, "kind": "steel_frame",
                                                             "preferences": {pf: prefs[pf]}}, limit=800)
    check(f"{sw}: ULS selected", isinstance(st2, dict) and st2["combinations"].get("strength") == ["ULS"], st2)
    show("analysis", "run_analysis", {**S}, limit=500)
    rd = show("run design", "run_design", {**S, "kind": "steel_frame"}, limit=1500)
    check(f"{sw}: design ran with AISC 360-16", isinstance(rd, dict) and rd.get("code") == "AISC 360-16", rd)
    r1 = b1_ratio(S)
    check(f"{sw}: Lb = 240 in -> LTB governs, ratio {r1} > {RATIO_BRACED:.4f}",
          r1 is not None and r1 > RATIO_BRACED * 1.01, r1)
    # SAP2000 lists the overwrites table only once a design has run
    st3 = show("design settings after the design", "get_design_settings", {**S, "kind": "steel_frame", "units": K},
               limit=600)
    otabs = st3.get("overwrites_tables", []) if isinstance(st3, dict) else []
    okey = next(iter(otabs), None)
    if okey is None:
        check(f"{sw}: overwrites table found", False, st3)
        return
    tb = show("overwrites table", "edit_database_table", {**S, "table_key": okey}, limit=1500)
    fields = tb.get("fields", []) if isinstance(tb, dict) else []
    ltb = next((f for f in fields if "ltb" in f.lower() and not f.lower().startswith("k")
                and "cb" not in f.lower()), None)
    key = next((f for f in ("UniqueName", "Frame", "Label") if f in fields), fields[0] if fields else None)
    check(f"{sw}: LTB overwrite field found in '{okey}'", ltb is not None, fields)
    if ltb is None:
        return
    ow = show("LTB overwrite 0.1", "set_design_settings",
              {**S, "kind": "steel_frame", "overwrites": [{"match": {key: "B1"}, "set": {ltb: 0.1}}],
               "allow_unlock": True}, limit=1500)
    if not isinstance(ow, dict):
        return
    show("analysis again (the overwrite unlocked the model)", "run_analysis", {**S}, limit=400)
    show("run design again", "run_design", {**S, "kind": "steel_frame"}, limit=1200)
    r2 = b1_ratio(S)
    check(f"{sw}: braced Lb = 24 in -> ratio = Mu / phi Mp = {RATIO_BRACED:.4f}", near(r2, RATIO_BRACED, 0.005), r2)
    rp = show("design report", "export_report", {**S, "sections": ["cover", "design"], "units": K,
                                                 "output_path": str(OUT / f"{sw} design.pdf"), "overwrite": True},
              limit=800)
    check(f"{sw}: design report written", isinstance(rp, dict) and rp.get("figures", 0) >= 2, rp)


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

for sw in targets:
    print(f"\n################ {sw} (new scratch instance, visible={visible})", flush=True)
    try:
        com(bridge.launch_new, sw, visible=visible)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to start {sw}: {exc}", flush=True)
        FAILED.append(f"{sw} start")
        continue
    t0 = time.perf_counter()
    S = {"software": sw}
    try:
        path = str(OUT / f"phase56_{sw}{EXT[sw]}")
        nm = {**S, "file_path": path, "discard_open_model": True, "units": K}
        if sw != "SAP2000":
            nm.update(kind="grid_only", stories=1, story_height=144.0, spacing_x=240.0, spacing_y=240.0)
        show("new model", "new_model", nm, limit=500)
        if sw == "SAFE":
            for kind in ("concrete_slab", "concrete_frame"):
                st = show(f"design settings {kind}", "get_design_settings", {**S, "kind": kind}, limit=1500)
                check(f"SAFE: {kind} settings read", isinstance(st, dict) and "code" in st, st)
            show("stories refused in SAFE? (allowed E only)", "define_stories",
                 {**S, "stories": [{"name": "A", "height": 3}]}, expect_error=True, limit=400)
            show("shear wall design not in SAFE", "get_design_settings", {**S, "kind": "shear_wall"},
                 expect_error=True, limit=400)
            continue
        if sw == "SAP2000":
            # ------------------------------------------------------------ links (U = F / k)
            L = {**S, "units": "kN_m_C"}
            show("pattern PL", "define_load_pattern", {**L, "name": "PL", "type": "other",
                                                       "self_weight_multiplier": 0.0}, limit=400)
            show("fixed point 1", "create_points", {**L, "points": [{"name": "1", "xyz": [0, 0, 0]}],
                                                    "restraint": "fixed"}, limit=400)
            show("points 2, 3", "create_points", {**L, "points": [{"name": "2", "xyz": [0, 0, 1]},
                                                                  {"name": "3", "xyz": [2, 0, 0]}]}, limit=400)
            for nm_, k in (("K1000", 1000.0), ("K500", 500.0)):
                lp = show(f"link property {nm_}", "define_link_property",
                          {**L, "name": nm_, "stiffness": {"U1": k}, "fixed": ["U2", "U3", "R1", "R2", "R3"]},
                          limit=600)
                check(f"{nm_} read back", isinstance(lp, dict) and lp["read_back"]["stiffness"] == {"U1": k}, lp)
            show("existing link property refused", "define_link_property",
                 {**L, "name": "K500", "stiffness": {"U1": 1.0}}, expect_error=True, limit=300)
            l1 = show("two-joint link 1-2", "create_links", {**L, "property": "K1000",
                                                             "links": [{"point1": "1", "point2": "2", "name": "L1"}]},
                      limit=600)
            l2 = show("one-joint link at 3", "create_links", {**L, "property": "K500",
                                                              "links": [{"point1": "3", "name": "L2"}]}, limit=600)
            check("links read back", isinstance(l1, dict) and isinstance(l2, dict)
                  and l1["created"][0]["point2"] == "2" and l2["created"][0]["one_joint"], (l1, l2))
            show("10 kN down at 2 and 3", "assign_point_or_frame_load",
                 {**L, "target_type": "point", "object_names": ["2", "3"], "load_pattern": "PL",
                  "values": [0, 0, -10, 0, 0, 0]}, limit=400)
            # the link model has no mass: the modal case is switched off (it cannot start without mass)
            show("modal case off", "set_analysis_options", {**S, "run_cases": {"MODAL": False}}, limit=400)
            show("link analysis", "run_analysis", {**S}, limit=400)
            d = show("displacements", "get_results", {**L, "result_type": "joint_displacements", "names": ["2", "3"],
                                                      "load_cases_or_combos": ["PL"]}, limit=1200)
            u = {r["Joint"]: r["U3"] for r in (d.get("rows", []) if isinstance(d, dict) else [])}
            check("U = F / k: point 2 -0.010 m, point 3 -0.020 m",
                  near(u.get("2"), -0.01, 1e-6) and near(u.get("3"), -0.02, 1e-6), u)
            show("seismic refused on a non-quake pattern", "set_auto_seismic",
                 {**S, "load_pattern": "PL", "code": "ASCE 7-16", "directions": ["X"], "period": "user",
                  "user_period": 0.5, "R": 8, "omega": 3, "Cd": 5.5, "importance": 1, "Ss": 1.5, "S1": 0.6,
                  "TL": 8, "site_class": "B"}, expect_error=True, limit=400)
            show("stories refused in SAP2000", "define_stories", {**S, "stories": [{"name": "A", "height": 3}]},
                 expect_error=True, limit=300)
            b = show("steel beam spec", "build_model_from_spec", {**S, "spec": beam_spec(0.0, 100.0), "allow_unlock": True},
                     limit=1500)
            check("beam spec completed", isinstance(b, dict) and b.get("completed") is True, b)
            steel_design(S, sw)
        if sw == "ETABS":
            # ------------------------------------------------------------ stories, diaphragm, pier, seismic
            stc = show("stories", "define_stories", {**S, "units": K, "base_elevation": 0.0,
                                                     "stories": [{"name": "S1", "height": 144.0},
                                                                 {"name": "S2", "height": 120.0}]}, limit=1200)
            el = [(s["name"], s["elevation"]) for s in stc["read_back"]["stories"]] if isinstance(stc, dict) else []
            check("stories read back: S1 144, S2 264 in", el == [("S1", 144.0), ("S2", 264.0)], el)
            b = show("steel beam spec at S1", "build_model_from_spec", {**S, "spec": beam_spec(144.0)}, limit=1500)
            check("beam spec completed", isinstance(b, dict) and b.get("completed") is True, b)
            show("column points", "create_points", {**S, "units": K, "points": [{"name": "CB", "xyz": [480, 0, 0]}],
                                                    "restraint": "fixed"}, limit=400)
            show("column top", "create_points", {**S, "units": K, "points": [{"name": "CT", "xyz": [480, 0, 264]}]},
                 limit=400)
            show("column", "create_frames", {**S, "units": K, "section": "I18",
                                             "frames": [{"start": "CB", "end": "CT", "name": "C1"}]}, limit=500)
            dg = show("diaphragm DR at the column top", "define_diaphragm", {**S, "name": "DR", "points": ["CT"]},
                      limit=600)
            check("diaphragm read back", isinstance(dg, dict) and dg["read_back"]["points"] == {"CT": "DR"}, dg)
            pr = show("pier P1 on the column", "assign_pier_spandrel", {**S, "kind": "pier", "label": "P1",
                                                                         "frames": ["C1"]}, limit=600)
            check("pier read back", isinstance(pr, dict) and pr["read_back"] == {"C1": "P1"}, pr)
            show("pattern EQX", "define_load_pattern", {**S, "name": "EQX", "type": "quake",
                                                        "self_weight_multiplier": 0.0}, limit=400)
            se = {**S, "load_pattern": "EQX", "code": "ASCE 7-16", "directions": ["X"], "period": "user",
                  "user_period": 0.5, "R": 8, "omega": 3, "Cd": 5.5, "importance": 1.0, "Ss": 1.5, "S1": 0.6,
                  "TL": 8, "site_class": "B"}
            show("site class F without Fa / Fv refused", "set_auto_seismic", {**se, "site_class": "F"},
                 expect_error=True, limit=300)
            sa = show("auto seismic ASCE 7-16", "set_auto_seismic", se, limit=1200)
            check("seismic parameters read back (site B: Fa 0.9, Fv 0.8)", isinstance(sa, dict)
                  and sa["read_back"]["R"] == 8 and sa["read_back"]["site_class"] == "B"
                  and sa["read_back"]["directions"] == ["X"] and near(sa["read_back"]["Fa"], 0.9)
                  and near(sa["read_back"]["Fv"], 0.8), sa)
            show("analysis", "run_analysis", {**S}, limit=500)
            br = show("base reactions EQX", "get_results", {**S, "units": K, "result_type": "base_reactions",
                                                            "load_cases_or_combos": ["EQX"]}, limit=800)
            fx = abs(br["rows"][0].get("FX", 0)) if isinstance(br, dict) and br.get("rows") else None
            # W: ETABS 23.2 lists no auto seismic output table; the seismic weight is the mass above the base
            # ('Mass Summary by Story', UX, Base excluded) x g
            lt = show("mass tables", "list_available_tables", {**S, "search_term": "mass summary"}, limit=1200)
            keys = [t["key"] for t in lt.get("tables", [])] if isinstance(lt, dict) else []
            mkey = next((k for k in keys if "story" in k.lower()), None)
            w = None
            if mkey:
                q = show("mass summary by story", "query_database_table", {**S, "table_key": mkey, "units": K,
                                                                           "export": "none"}, limit=2500)
                rows = (q.get("records") or q.get("rows") or []) if isinstance(q, dict) else []
                mass = sum(float(r.get("UX") or 0) for r in rows if str(r.get("Story")) != "Base")
                w = mass * 386.0886
                print(f"record: seismic weight W = {mass:.6g} kip-s2/in x g = {w:.6g} kip", flush=True)
            check("base shear = Cs x W = 0.08 W (Cs by hand: min(SDS / R, SD1 / (T R)))",
                  w is not None and fx is not None and near(fx, 0.08 * w, 5e-3), (fx, w, fx / w if w else None))
            show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
            steel_design({**S}, sw)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED ({type(exc).__name__}): {exc}", flush=True)
        FAILED.append(f"{sw}: {exc}")
    finally:
        print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)
        try:
            com(bridge.disconnect, sw, True, False)
        except Exception as exc:  # noqa: BLE001
            print("close", exc)

sys.exit(finish())
