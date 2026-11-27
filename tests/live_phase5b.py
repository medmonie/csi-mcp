r"""Live test of 0.14.0 (Phase 5, second batch) in NEW scratch instances.

    run_live_phase5b.bat [SAP2000] [ETABS] [--hidden]      Output: tests\results\live_phase5b.txt

Hand checks (all values are the test author's - the designer's):
  seismic coefficient  one column, all mass from a 100 kN point load at its top (mass source: loads only,
                       column weight 0), C = 0.0825 (e.g. a Cs the designer worked out for a code CSI does not list),
                       K = 1 -> base shear V = C W = 8.25 kN in X (SAP2000 and ETABS).
  gap link             SAP2000, nonlinear static: two-joint gap (U1 opening 0.01 m, k 1000 kN/m) under point 2 with a
                       soft 1 kN/m one-joint link, 20 kN down: U3 = -(20 + 1000 x 0.01) / (1000 + 1) = -0.029970 m.
  hook link            the same in tension, 20 kN up: U3 = +0.029970 m.
  multi-linear elastic one-joint link, points (0, 0) (0.02, 30) (0.1, 50) and mirrored, 40 kN up:
                       U3 = 0.02 + (40 - 30) / ((50 - 30) / 0.08) = 0.060 m.
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase5b_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS"]
visible = "--hidden" not in sys.argv
EXT = {"SAP2000": ".sdb", "ETABS": ".edb"}
U = "kN_m_C"
SOFT = (20 + 1000 * 0.01) / (1000 + 1)
ALL_FIXED_BUT_U1 = ["U2", "U3", "R1", "R2", "R3"]


def near(got, want, rel=1e-4, absolute=1e-9):
    return got is not None and abs(got - want) <= rel * abs(want) + absolute


def last_u3(S, names, case):
    d = show(f"displacements {case}", "get_results", {**S, "units": U, "result_type": "joint_displacements",
                                                     "names": names, "load_cases_or_combos": [case],
                                                     "step_option": "last_step"}, limit=1500)
    out = {}
    for r in (d.get("rows") or d.get("records") or []) if isinstance(d, dict) else []:
        out[str(r.get("Joint", r.get("Obj")))] = r.get("U3")
    return out


def coefficient_part(S, sw):
    """V = C W with W = 100 kN from a point load (mass source: loads only)."""
    show("material W0 (no weight)", "define_material", {**S, "units": U, "allow_unlock": True, "name": "W0", "type": "steel",
                                                         "E": 2.0e8, "poisson": 0.3, "unit_weight": 0.0,
                                                         "fy": 355000.0, "fu": 490000.0}, limit=400)
    show("section", "define_frame_section", {**S, "units": U, "name": "COL", "shape": "rectangle",
                                             "material": "W0", "depth": 0.4, "width": 0.4}, limit=400)
    show("base", "create_points", {**S, "units": U, "points": [{"name": "CB", "xyz": [10, 0, 0]}],
                                   "restraint": "fixed"}, limit=400)
    show("top", "create_points", {**S, "units": U, "points": [{"name": "CT", "xyz": [10, 0, 3]}]}, limit=400)
    show("column", "create_frames", {**S, "units": U, "section": "COL",
                                     "frames": [{"start": "CB", "end": "CT", "name": "C1"}]}, limit=400)
    pats = bridge.try_name_list(bridge.sap_model(sw), ["LoadPatterns"])[0] or []
    dead = "DEAD" if "DEAD" in pats else "Dead"
    show("100 kN at the top", "assign_point_or_frame_load",
         {**S, "units": U, "target_type": "point", "object_names": ["CT"], "load_pattern": dead,
          "values": [0, 0, -100, 0, 0, 0]}, limit=400)
    ms = show("mass source: loads only", "set_mass_source", {**S, "from_elements": False, "from_masses": False,
                                                              "from_loads": True,
                                                              "loads": [{"pattern": dead, "factor": 1.0}]},
              limit=600)
    check(f"{sw}: mass source set", isinstance(ms, dict), ms)
    show("pattern EQX", "define_load_pattern", {**S, "name": "EQX", "type": "quake", "self_weight_multiplier": 0.0},
         limit=400)
    show("refused on a non-quake pattern", "set_seismic_coefficient",
         {**S, "load_pattern": dead, "direction": "X", "C": 0.0825}, expect_error=True, limit=300)
    sc = show("seismic coefficient C = 0.0825", "set_seismic_coefficient",
              {**S, "units": U, "load_pattern": "EQX", "direction": "X", "C": 0.0825, "K": 1.0}, limit=1200)
    check(f"{sw}: C and K read back", isinstance(sc, dict) and near(sc["read_back"]["C"], 0.0825)
          and near(sc["read_back"]["K"], 1.0), sc)
    return dead


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
        path = str(OUT / f"phase5b_{sw}{EXT[sw]}")
        if OUT.exists() and sw == targets[0]:
            shutil.rmtree(OUT)
        OUT.mkdir(parents=True, exist_ok=True)
        nm = {**S, "file_path": path, "discard_open_model": True, "units": U}
        if sw == "ETABS":
            nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
        show("new model", "new_model", nm, limit=400)
        if sw == "SAP2000":
            # ------------------------------------------------------------ gap / hook / multi-linear links
            show("pattern PL", "define_load_pattern", {**S, "name": "PL", "type": "other",
                                                       "self_weight_multiplier": 0.0}, limit=300)
            show("fixed points", "create_points", {**S, "units": U, "restraint": "fixed",
                                                   "points": [{"name": "1", "xyz": [0, 0, 0]},
                                                              {"name": "3", "xyz": [3, 0, 0]}]}, limit=300)
            show("free points", "create_points", {**S, "units": U, "points": [
                {"name": "2", "xyz": [0, 0, 1]}, {"name": "4", "xyz": [3, 0, 1]}, {"name": "5", "xyz": [6, 0, 0]}]},
                limit=300)
            for kind in ("gap", "hook"):
                lp = show(f"{kind} property", "define_link_property",
                          {**S, "units": U, "name": kind.upper(), "type": kind, "stiffness": {"U1": 0.0},
                           "fixed": ALL_FIXED_BUT_U1,
                           "nonlinear": {"U1": {"stiffness": 1000.0, "opening": 0.01}}}, limit=800)
                check(f"{kind} read back", isinstance(lp, dict) and lp["read_back"]["type"] == kind
                      and lp["read_back"]["nonlinear"] == {"U1": {"stiffness": 1000.0, "opening": 0.01}}, lp)
            show("soft spring", "define_link_property", {**S, "units": U, "name": "SOFT", "stiffness": {"U1": 1.0},
                                                         "fixed": ALL_FIXED_BUT_U1}, limit=500)
            pts = [[-0.1, -50.0], [-0.02, -30.0], [0.0, 0.0], [0.02, 30.0], [0.1, 50.0]]
            ml = show("multi-linear elastic", "define_link_property",
                      {**S, "units": U, "name": "ML", "type": "multilinear_elastic", "stiffness": {"U1": 1500.0},
                       "fixed": ALL_FIXED_BUT_U1, "nonlinear": {"U1": {"points": pts}}}, limit=800)
            check("multi-linear points read back", isinstance(ml, dict)
                  and ml["read_back"]["nonlinear"]["U1"]["points"] == pts, ml)
            show("multi-linear without [0, 0] refused", "define_link_property",
                 {**S, "name": "BAD", "type": "multilinear_elastic",
                  "nonlinear": {"U1": {"points": [[-0.1, -5.0], [0.1, 5.0]]}}}, expect_error=True, limit=300)
            for prop, links in (("GAP", [{"point1": "1", "point2": "2", "name": "LG"}]),
                                ("HOOK", [{"point1": "3", "point2": "4", "name": "LH"}]),
                                ("SOFT", [{"point1": "2", "name": "S2"}, {"point1": "4", "name": "S4"}]),
                                ("ML", [{"point1": "5", "name": "LM"}])):
                show(f"links {prop}", "create_links", {**S, "property": prop, "links": links}, limit=500)
            for pt, fz in (("2", -20.0), ("4", 20.0), ("5", 40.0)):
                show(f"load at {pt}", "assign_point_or_frame_load",
                     {**S, "units": U, "target_type": "point", "object_names": [pt], "load_pattern": "PL",
                      "values": [0, 0, fz, 0, 0, 0]}, limit=300)
            nc = show("nonlinear static case NLC", "define_load_case",
                      {**S, "name": "NLC", "type": "nonlinear_static", "loads": [{"name": "PL", "factor": 1.0}]},
                      limit=800)
            check("nonlinear case read back", isinstance(nc, dict) and nc["read_back"]["loads"][0]["name"] == "PL"
                  and nc["read_back"]["geometric_nonlinearity"] == "none", nc)
            show("only NLC runs (gap / hook have no linear stiffness)", "set_analysis_options",
                 {**S, "run_cases": {"MODAL": False, "PL": False, "DEAD": False}}, limit=400)
            show("analysis", "run_analysis", {**S}, limit=600)
            u = last_u3(S, ["2", "4", "5"], "NLC")
            check(f"gap: U3(2) = -{SOFT:.6f}", near(u.get("2"), -SOFT, 1e-4), u)
            check(f"hook: U3(4) = +{SOFT:.6f}", near(u.get("4"), SOFT, 1e-4), u)
            check("multi-linear: U3(5) = 0.060", near(u.get("5"), 0.06, 1e-4), u)
        dead = coefficient_part(S, sw)
        if sw == "SAP2000":
            show("only DEAD and EQX run", "set_analysis_options",
                 {**S, "run_cases": {"MODAL": False, "NLC": False, "PL": False, "DEAD": True}, "allow_unlock": True}, limit=400)
        show("analysis", "run_analysis", {**S}, limit=600)
        br = show("base reactions", "get_results", {**S, "units": U, "result_type": "base_reactions",
                                                    "load_cases_or_combos": ["EQX", dead]}, limit=1200)
        rows = (br.get("rows") or br.get("records") or []) if isinstance(br, dict) else []
        fx = next((abs(r["FX"]) for r in rows if r.get("OutputCase", r.get("LoadCase")) == "EQX"), None)
        fz = next((abs(r["FZ"]) for r in rows if r.get("OutputCase", r.get("LoadCase")) == dead), None)
        check(f"{sw}: DEAD base FZ = 100 kN (W)", near(fz, 100.0, 1e-6), fz)
        check(f"{sw}: EQX base shear = C W = 8.25 kN", near(fx, 8.25, 1e-4), fx)
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
