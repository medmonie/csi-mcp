r"""Live test of 0.10.0 (Phase 3, last part: response-spectrum / time-history functions and cases).

    run_live_phase3d.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_phase3d.txt

Single-mass cantilever (as live_phase3b): 4 m, 0.4 x 0.4, E = 25 GPa, shear stiffness raised, no element mass; mass
only from a 10 kN vertical load -> m = 10 / g, k = 3 E I / L^3 = 2500 kN/m, T = 0.12690 s.
  flat spectrum Sa = 0.5 g (define_function, from user points), response_spectrum case in X:
        base shear m Sa = 5.000 kN, tip displacement Sa / w^2 = 2.000 mm
  step function 1 from t = 0 (define_function), linear_modal_history with the 10 kN tip load, step size Td / 40:
        SAP2000 damping 0 -> peak 2 F / k = 8.000 mm; ETABS (model modal damping 0.05) -> (1 + e^(-pi z / sqrt(1-z^2)))
        F / k = 7.418 mm
SAP2000 through the API; ETABS functions and case options through database tables (other rows kept); SAFE refuses.
"""
import math
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase3d_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001
from csi_bridge import api_call  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv

E, B, L, P, W, G = 25.0e6, 0.4, 4.0, 10.0, 10.0, 9.80665
I = B ** 4 / 12
K = 3 * E * I / L ** 3
M = W / G
T = 2 * math.pi * math.sqrt(M / K)
EXT = {"SAP2000": ".sdb", "ETABS": ".edb", "SAFE": ".fdb"}


def blank(sw):
    sap = bridge.sap_model(sw)
    api_call("InitializeNewModel", sap.InitializeNewModel, 6)
    if sw == "SAP2000":
        api_call("File.NewBlank", sap.File.NewBlank)
        return 0.0
    api_call("File.NewGridOnly", sap.File.NewGridOnly, 1, L, L, 2, 2, 6.0, 6.0)
    return float(api_call("Story.GetStories_2", sap.Story.GetStories_2)[0])


def peak(sw, kind, names, case, comp):
    r = show(f"{kind} {case}", "get_results", {"software": sw, "result_type": kind, "names": names,
                                               "load_cases_or_combos": [case]}, limit=600)
    if not isinstance(r, dict) or not r.get("rows"):
        return None
    return max((row[comp] for row in r["rows"] if row.get(comp) is not None), key=abs, default=None)


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
print(f"hand: k = {K:.3f} kN/m, m = {M:.6f} t, T = {T:.6f} s", flush=True)

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
        z = com(blank, sw)
        S = {"software": sw}
        if sw == "SAFE":
            show("functions refused in SAFE", "define_function", {**S, "name": "F", "kind": "time_history",
                                                                  "points": [[0, 1], [1, 1]]}, expect_error=True,
                 limit=400)
            show("RS case refused in SAFE", "define_load_case",
                 {**S, "name": "RSX", "type": "response_spectrum",
                  "loads": [{"kind": "acceleration", "name": "U1", "function": "F"}]}, expect_error=True, limit=400)
            continue
        show("material", "define_material", {**S, "name": "C25T", "type": "concrete", "E": E, "poisson": 0.2,
                                             "unit_weight": 25.0, "fc": 25000.0}, limit=200)
        show("section", "define_frame_section", {**S, "name": "SQ400", "shape": "rectangle", "material": "C25T",
                                                 "depth": B, "width": B, "modifiers": [1, 1000, 1000, 1, 1, 1, 0, 0]},
             limit=200)
        for name, kind in (("SDL", "superdead"), ("MASSLOAD", "other")):
            show(f"pattern {name}", "define_load_pattern", {**S, "name": name, "type": kind}, limit=200)
        base = show("base fixed", "create_points", {**S, "points": [{"xyz": [0, 0, z]}], "restraint": "fixed"},
                    limit=200)
        fr = show("column", "create_frames", {**S, "section": "SQ400", "frames": [
            {"start": base["point_names"][0], "end": [0, 0, z + L]}]}, limit=400)
        tip = fr["created"][0]["points"][1]
        show("tip load SDL", "assign_point_or_frame_load", {**S, "target_type": "point", "object_names": [tip],
                                                            "load_pattern": "SDL", "values": [P], "direction": "X"},
             limit=200)
        show("tip mass load", "assign_point_or_frame_load", {**S, "target_type": "point", "object_names": [tip],
                                                             "load_pattern": "MASSLOAD", "values": [W],
                                                             "direction": "Gravity"}, limit=200)
        show("mass source", "set_mass_source", {**S, "from_elements": False, "from_masses": False,
                                                "from_loads": True, "loads": [{"pattern": "MASSLOAD"}]}, limit=300)
        show("modal case", "define_load_case", {**S, "name": "MODAL6", "type": "modal_eigen", "max_modes": 6},
             limit=300)
        # ------------------------------------------------------------------ functions
        d = show("RS function dry run", "define_function", {**S, "name": "FLAT", "kind": "response_spectrum",
                                                            "points": [[0, 0.5], [10, 0.5]], "dry_run": True},
                 limit=300)
        f = show("flat spectrum 0.5 g", "define_function", {**S, "name": "FLAT", "kind": "response_spectrum",
                                                            "points": [[0, 0.5], [10, 0.5]]}, limit=800)
        if isinstance(f, dict):
            check("FLAT read back", f["read_back"]["points"] == [[0.0, 0.5], [10.0, 0.5]], f["read_back"])
        s = show("step function", "define_function", {**S, "name": "STEP", "kind": "time_history",
                                                      "points": [[0, 1], [10, 1]]}, limit=800)
        if isinstance(s, dict):
            check("STEP read back", s["read_back"]["points"] == [[0.0, 1.0], [10.0, 1.0]], s["read_back"])
        if sw == "ETABS" and isinstance(s, dict):
            te = s.get("table_edit", {})
            check("ETABS table edit kept the other rows", te.get("rows_after") == te.get("rows_before", -9) + 2, te)
        # ------------------------------------------------------------------ cases
        r = show("RS case X", "define_load_case", {**S, "name": "RSX", "type": "response_spectrum",
                                                   "modal_case": "MODAL6", "modal_combination": "cqc",
                                                   "loads": [{"kind": "acceleration", "name": "U1", "function": "FLAT",
                                                              "factor": G}]}, limit=1200)
        if isinstance(r, dict):
            b = r["read_back"]
            check("RSX read back: U1, FLAT, g, MODAL6, CQC", b.get("loads") == [{"direction": "U1", "function": "FLAT",
                                                                                "factor": G}]
                  and b.get("modal_case") == "MODAL6" and b.get("modal_combination") == "cqc", b)
        damping, zeta = ({"damping": 0.0}, 0.0) if sw == "SAP2000" else ({}, 0.05)
        td = T / math.sqrt(1 - zeta ** 2)
        h = show("modal history, step load", "define_load_case",
                 {**S, "name": "THX", "type": "linear_modal_history", "modal_case": "MODAL6",
                  "loads": [{"name": "SDL", "function": "STEP", "factor": 1.0}], "steps": 80, "step_size": td / 40,
                  **damping}, limit=1200)
        if isinstance(h, dict):
            b = h["read_back"]
            check("THX read back: 80 steps of Td/40, MODAL6", b.get("steps") == 80
                  and abs(b.get("step_size", 0) - td / 40) < 1e-9 and b.get("modal_case") == "MODAL6", b)
        # ------------------------------------------------------------------ analysis and hand checks
        show("save", "open_or_save_model", {**S, "action": "save", "file_path": str(OUT / f"sdof{EXT[sw]}")},
             limit=200)
        show("run", "run_analysis", S, limit=400)
        lg = show("log", "get_analysis_log", S, limit=400)
        if isinstance(lg, dict):
            check("stable", lg.get("stable") is True, lg.get("stable"))
        mp = show("periods", "get_results", {**S, "result_type": "modal_periods",
                                             "load_cases_or_combos": ["MODAL6"]}, limit=600)
        if isinstance(mp, dict) and mp.get("rows"):
            check(f"T1 = {T:.5f} s", abs(max(x["Period"] for x in mp["rows"]) - T) < 2e-4 * T,
                  max(x["Period"] for x in mp["rows"]))
        br = show("RS base reaction", "get_results", {**S, "result_type": "base_reactions",
                                                      "load_cases_or_combos": ["RSX"]}, limit=600)
        fx = br["rows"][0]["FX"] if isinstance(br, dict) and br.get("rows") else None
        check("RS base shear m Sa = 5.000 kN", fx is not None and abs(abs(fx) - M * 0.5 * G) < 1e-4 * 5, fx)
        u = peak(sw, "joint_displacements", [tip], "RSX", "U1")
        check("RS tip displacement Sa / w^2 = 2.000 mm", u is not None and abs(abs(u) - 0.5 * G / (2 * math.pi / T)
                                                                               ** 2) < 1e-4 * 0.002, u)
        want = (1 + math.exp(-math.pi * zeta / math.sqrt(1 - zeta ** 2))) * P / K
        u = peak(sw, "joint_displacements", [tip], "THX", "U1")
        check(f"TH peak = {want * 1000:.4f} mm (damping {zeta})", u is not None and abs(abs(u) - want) < 1e-4 * want,
              u)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED ({type(exc).__name__}): {exc}", flush=True)
        FAILED.append(f"{sw}: {exc}")
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"close {sw}: {exc}", flush=True)
        print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)

sys.exit(finish())
