r"""Live test of 0.6.0 (Phase 3 first batch: area mesh, joint patterns, area loads) in NEW scratch instances.

    run_live_phase3.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_phase3.txt

SAP2000 - hydrostatic cantilever wall, B = 2 m wide, H = 4 m high, fixed along the base, water to the top
(unit weight 10 kN/m3), built with the raw API and then loaded only through the MCP actions:
    set_area_mesh 4 x 8 (dry run first)          -> the solver log shows 32 shell elements
    define_joint_pattern hydrostatic            -> 40 kN/m2 at the base, 0 at the top
    assign_area_load surface_pressure x pattern
    statics: sum of base reactions = 1/2 g H^2 B = 160 kN, base moment = g H^3 B / 6 = 213.3 kNm
    the direction the pressure acts (face convention) is measured and reported
    finer meshes 8 x 16 and 16 x 32 give the same statics; the tip deflection converges (reported)
ETABS / SAFE (reference models) - assign_area_load uniform on the decks / panels: dry run changes nothing, the
    live-load base reaction becomes the new load x area; surface_pressure and set_area_mesh are refused with the
    alternative.
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase3_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001
from csi_bridge import api_call  # noqa: E402
from reference_models import BUILDERS  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
from live_phase3_wall import B, H, GAMMA, build_wall  # noqa: E402


def unlock(sw):
    sap = bridge.sap_model(sw)
    api_call("SetModelIsLocked", sap.SetModelIsLocked, False)


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

for sw in targets:
    print(f"\n################ {sw} (new scratch instance, visible={visible})", flush=True)
    t0 = time.perf_counter()
    try:
        com(bridge.launch_new, sw, visible=visible)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to start {sw}: {exc}", flush=True)
        FAILED.append(f"{sw} start")
        continue
    try:
        if sw == "SAP2000":
            wall, top = com(build_wall, bridge.sap_model(sw))
            path = str(OUT / "hydro_wall.sdb")
            show("save", "open_or_save_model", {"software": sw, "action": "save", "file_path": path}, limit=200)
            d = show("mesh dry run", "set_area_mesh", {"software": sw, "area_names": [wall], "n1": 4, "n2": 8,
                                                       "dry_run": True}, limit=600)
            m = show("mesh 4 x 8", "set_area_mesh", {"software": sw, "area_names": [wall], "n1": 4, "n2": 8,
                                                     "snapshot": True}, limit=800)
            a = show("read mesh back", "get_object_assignments", {"software": sw, "object_type": "areas"}, limit=800)
            if isinstance(a, dict):
                check("auto mesh read back 4 x 8", a["items"][0].get("auto_mesh") == {"mode": "divisions", "n1": 4,
                                                                                     "n2": 8}, a["items"][0])
            pt = show("hydrostatic pattern", "define_joint_pattern",
                      {"software": sw, "name": "WATER", "method": "hydrostatic", "fluid_level": H,
                       "unit_weight": GAMMA}, limit=1200)
            if isinstance(pt, dict):
                v = {round(s["z"], 3): s["value"] for s in pt["sample_values"]}
                check("pattern = 40 at the base, 0 at the top", abs(v.get(0.0, -1) - GAMMA * H) < 1e-6 and
                      abs(v.get(H, -1)) < 1e-9, v)
            show("pressure dry run", "assign_area_load", {"software": sw, "area_names": [wall], "load_pattern": "HYDRO",
                                                          "load_type": "surface_pressure", "value": 1.0,
                                                          "joint_pattern": "WATER", "dry_run": True}, limit=600)
            show("surface pressure x WATER", "assign_area_load",
                 {"software": sw, "area_names": [wall], "load_pattern": "HYDRO", "load_type": "surface_pressure",
                  "face": "bottom", "value": 1.0, "joint_pattern": "WATER"}, limit=800)
            a = show("read pressure back", "get_object_assignments", {"software": sw, "object_type": "areas"},
                     limit=800)
            if isinstance(a, dict):
                check("surface pressure read back", a["items"][0].get("surface_pressure_loads") ==
                      [{"pattern": "HYDRO", "face": "bottom", "value": 1.0, "joint_pattern": "WATER"}],
                      a["items"][0].get("surface_pressure_loads"))
            jp = show("joint pattern definition", "get_definition_details", {"software": sw, "kind": "joint_patterns"},
                      limit=600)
            tips = {}
            for n1, n2 in ((4, 8), (8, 16), (16, 32)):
                if (n1, n2) != (4, 8):
                    com(unlock, sw)
                    show(f"mesh {n1} x {n2}", "set_area_mesh", {"software": sw, "area_names": [wall], "n1": n1,
                                                                "n2": n2}, limit=300)
                show(f"run {n1} x {n2}", "run_analysis", {"software": sw}, limit=500)
                lg = show("analysis log", "get_analysis_log", {"software": sw}, limit=800)
                if isinstance(lg, dict):
                    check(f"{n1} x {n2}: {n1 * n2} shell elements in the solver log, stable",
                          lg["counts"].get("Number Of Shell Elements") == n1 * n2 and lg["stable"] is True,
                          (lg["counts"].get("Number Of Shell Elements"), lg["stable"]))
                br = show("base reaction", "get_results", {"software": sw, "result_type": "base_reactions",
                                                           "load_cases_or_combos": ["HYDRO"]}, limit=600)
                if isinstance(br, dict) and br["rows"]:
                    r0 = br["rows"][0]
                    check(f"{n1} x {n2}: |FY| = 1/2 g H^2 B = 160 kN", abs(abs(r0["FY"]) - 0.5 * GAMMA * H * H * B) < 0.01,
                          r0["FY"])
                    check(f"{n1} x {n2}: |MX| = g H^3 B / 6 = 213.33 kNm", abs(abs(r0["MX"]) - GAMMA * H ** 3 * B / 6) < 0.01,
                          r0["MX"])
                    if (n1, n2) == (4, 8):
                        jd = show("top displacement", "get_results",
                                  {"software": sw, "result_type": "joint_displacements", "names": top,
                                   "load_cases_or_combos": ["HYDRO"]}, limit=500)
                        uy = jd["rows"][0]["U2"] if isinstance(jd, dict) and jd["rows"] else None
                        print(f"FACE CONVENTION: wall local 3 = -Y (corners drawn X then Z); pressure on the bottom "
                              f"face: base FY = {r0['FY']:+.3f} kN, top U2 = {uy}", flush=True)
                        check("top displacement measured (face convention recorded above)", uy is not None, uy)
                jd = show("tip deflection", "get_results", {"software": sw, "result_type": "joint_displacements",
                                                            "load_cases_or_combos": ["HYDRO"], "component": "U2",
                                                            "max_rows": 1}, limit=400)
                if isinstance(jd, dict):
                    tips[(n1, n2)] = abs(jd["governing"]["U2"]["abs_max"])
            print("TIP DEFLECTION by mesh (m):", tips, flush=True)
            v = list(tips.values())
            check("tip deflection converges (changes get smaller)", len(v) == 3 and abs(v[2] - v[1]) <= abs(v[1] - v[0]),
                  v)
            dv = show("divide 2 x 2 (physical)", "set_area_mesh", {"software": sw, "area_names": [wall],
                                                                     "method": "divide", "n1": 2, "n2": 2,
                                                                     "allow_unlock": True}, limit=800)
            if isinstance(dv, dict):
                check("divide made 4 areas", dv.get("areas_created") == 4, dv.get("new_areas"))
                check("divide: the 1 new base joint got the base fixity", dv.get("edge_joints_restrained") == 1,
                      dv.get("edge_joints_restrained"))
                show("run after divide", "run_analysis", {"software": sw}, limit=500)
                lg = show("analysis log", "get_analysis_log", {"software": sw}, limit=800)
                if isinstance(lg, dict):
                    check("after divide: stable", lg["stable"] is True, lg["stable"])
                br = show("base reaction", "get_results", {"software": sw, "result_type": "base_reactions",
                                                           "load_cases_or_combos": ["HYDRO"]}, limit=600)
                if isinstance(br, dict) and br["rows"]:
                    r0 = br["rows"][0]
                    check("after divide: |FY| = 160 kN (loads carried to the new areas)",
                          abs(abs(r0["FY"]) - 0.5 * GAMMA * H * H * B) < 0.01, r0["FY"])
        else:
            info = com(BUILDERS[sw], bridge.sap_model(sw))
            o = show("areas", "get_objects", {"software": sw, "object_type": "areas", "max_rows": 1000}, limit=300)
            area = sum(a["area"] for a in o["areas"]["items"] if a["name"] in info["areas"])
            d = show("uniform dry run", "assign_area_load", {"software": sw, "area_names": info["areas"],
                                                             "load_pattern": "Live", "load_type": "uniform",
                                                             "value": 5.0, "dry_run": True}, limit=600)
            r = show("uniform 5 kN/m2 (replace)", "assign_area_load",
                     {"software": sw, "area_names": info["areas"], "load_pattern": "Live", "load_type": "uniform",
                      "value": 5.0}, limit=600)
            a = show("read back", "get_object_assignments", {"software": sw, "object_type": "areas",
                                                             "names": info["areas"][:1]}, limit=800)
            if isinstance(a, dict):
                check(f"{sw} uniform load read back 5", a["items"][0]["uniform_loads"][0]["value"] == 5.0,
                      a["items"][0]["uniform_loads"])
            show("ghost area refused", "assign_area_load", {"software": sw, "area_names": ["NO_SUCH"],
                                                            "load_pattern": "Live", "load_type": "uniform",
                                                            "value": 1.0}, expect_error=True, limit=300)
            show("surface pressure refused", "assign_area_load",
                 {"software": sw, "area_names": info["areas"][:1], "load_pattern": "Live",
                  "load_type": "surface_pressure", "value": 1.0}, expect_error=True, limit=400)
            show("mesh refused", "set_area_mesh", {"software": sw, "area_names": info["areas"][:1]},
                 expect_error=True, limit=400)
            show("save", "open_or_save_model", {"software": sw, "action": "save",
                                                "file_path": str(OUT / f"ref_{sw.lower()}{'.edb' if sw == 'ETABS' else '.fdb'}")},
                 limit=200)
            show("run", "run_analysis", {"software": sw}, limit=400)
            br = show("base reaction Live", "get_results", {"software": sw, "result_type": "base_reactions",
                                                            "load_cases_or_combos": ["Live"]}, limit=500)
            if isinstance(br, dict) and br["rows"]:
                check(f"{sw} Live base FZ = 5 x {area:.3f} m2", abs(br["rows"][0]["FZ"] - 5.0 * area) < 1e-3 * 5 * area,
                      br["rows"][0]["FZ"])
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"could not close the scratch {sw}: {exc}", flush=True)
    print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)

sys.exit(finish())
