r"""Live test of 0.5.0 (Phase 2 results depth + report R3 result diagrams) in NEW scratch instances.

    run_live_phase2.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase2.txt

Per program: build the reference model, save it under tests\results\phase2_output\models, run the analysis, then
read every result type through get_results and check them by hand: live-load base reaction = the applied live load,
joint reactions add up to the base reaction, beam end shears differ by w x L, first modal period = the one the solver
wrote in its log, the user's output selection is unchanged afterwards. get_analysis_log: stable, finished. Report
with results and result diagrams (A4 + A3). The scratch instance is closed without saving.
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase2_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001
from reference_models import BUILDERS  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}
TYPES = ["joint_displacements", "joint_reactions", "frame_forces", "frame_joint_forces", "area_forces",
         "area_stresses", "mode_shapes", "base_reactions", "modal_periods", "modal_mass_ratios",
         "modal_participation_factors", "buckling_factors", "story_drifts", "joint_drifts", "pier_forces",
         "spandrel_forces", "section_cuts"]
ONLY = {"story_drifts": "EF", "joint_drifts": "EF", "pier_forces": "EF", "spandrel_forces": "EF"}
LETTER = {"ETABS": "E", "SAP2000": "S", "SAFE": "F"}


def output_selection(sw):
    sap = bridge.sap_model(sw)
    s = sap.Results.Setup
    cases = {c: bool(s.GetCaseSelectedForOutput(c)[0]) for c in bridge.name_list(sap, "LoadCases")}
    combos = {c: bool(s.GetComboSelectedForOutput(c)[0]) for c in (bridge.try_name_list(sap, ["RespCombo"])[0] or [])}
    return cases, combos


def user_selection(sw):
    """Pretend the user had picked one case for output (to see that it comes back)."""
    sap = bridge.sap_model(sw)
    s = sap.Results.Setup
    s.DeselectAllCasesAndCombosForOutput()
    s.SetCaseSelectedForOutput(bridge.name_list(sap, "LoadCases")[0], True)


def area_total(sw, names):
    o = show(f"{sw} areas for load check", "get_objects", {"software": sw, "object_type": "areas", "max_rows": 5000},
             limit=300)
    return sum(a["area"] for a in o["areas"]["items"] if a["name"] in names) if isinstance(o, dict) else None


if OUT.exists():
    shutil.rmtree(OUT)
(OUT / "models").mkdir(parents=True)
(HERE / "results" / "reports").mkdir(parents=True, exist_ok=True)

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
        try:
            info = com(BUILDERS[sw], bridge.sap_model(sw))
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED reference model {sw}: {type(exc).__name__}: {exc}", flush=True)
            FAILED.append(f"{sw} reference model")
            continue
        path = str(OUT / "models" / f"ref_{sw.lower()}{EXT[sw]}")
        show(f"{sw} save", "open_or_save_model", {"software": sw, "action": "save", "file_path": path}, limit=300)
        live_area = area_total(sw, info["areas"])
        run = show(f"{sw} run_analysis", "run_analysis", {"software": sw}, limit=800)
        if not isinstance(run, dict):
            continue
        com(user_selection, sw)
        before = com(output_selection, sw)

        res = {}
        for rt in TYPES:
            expect_err = LETTER[sw] not in ONLY.get(rt, "ESF")
            r = show(f"{sw} get_results {rt}", "get_results",
                     {"software": sw, "result_type": rt, "max_rows": 5}, limit=1600, expect_error=expect_err)
            if isinstance(r, dict):
                res[rt] = r
        check(f"{sw} user's output selection unchanged", com(output_selection, sw) == before)

        # ---- hand checks
        live = info["live"]
        br = show(f"{sw} base reactions {live}", "get_results",
                  {"software": sw, "result_type": "base_reactions", "load_cases_or_combos": [live]}, limit=900)
        fz = br["rows"][0]["FZ"] if isinstance(br, dict) and br["rows"] else None
        applied = {"SAP2000": 3.0 * 16 + 10.0 * 6 * len(info.get("beams", [])), "ETABS": 2.0 * (live_area or 0),
                   "SAFE": 3.0 * (live_area or 0)}[sw]
        check(f"{sw} {live} base FZ = applied live load {applied:.3f}", fz is not None and abs(fz - applied) < 1e-3 * applied,
              fz)
        jr = show(f"{sw} joint reactions {live}", "get_results",
                  {"software": sw, "result_type": "joint_reactions", "load_cases_or_combos": [live], "max_rows": 2000,
                   "export": "xlsx"}, limit=600)
        if isinstance(jr, dict) and sw != "SAFE":     # SAFE carries the slab on area springs, not joints
            s = sum(r_["F3"] for r_ in jr["rows"])
            check(f"{sw} joint reactions add up to base FZ", fz is not None and abs(s - fz) < 1e-3 * abs(fz), s)
            check(f"{sw} xlsx export written", bool(jr.get("export_path")) and Path(jr["export_path"]).is_file(),
                  jr.get("export_path"))
        if sw == "SAP2000":
            ff = show(f"{sw} beam forces {live}", "get_results",
                      {"software": sw, "result_type": "frame_forces", "names": info["beams"],
                       "load_cases_or_combos": [live], "max_rows": 2000, "envelope_by_object": True}, limit=900)
            if isinstance(ff, dict):
                ok = True
                for b in info["beams"]:
                    st = sorted((r_["Station"], r_["V2"]) for r_ in ff["rows"] if r_["Frame"] == b)
                    ok &= abs(abs(st[0][1] - st[-1][1]) - 60.0) < 1e-3
                check(f"{sw} beam end shears differ by w x L = 60 kN", ok)
        # first period vs the solver log
        log = show(f"{sw} get_analysis_log", "get_analysis_log", {"software": sw}, limit=1500)
        if isinstance(log, dict):
            check(f"{sw} log: finished and stable", log["finished"] and log["stable"] is True,
                  (log["finished"], log["stable"], log["negative_eigenvalues"]))
            mp = res.get("modal_periods")
            if sw != "SAFE":
                check(f"{sw} modes in results = modes found in log", mp and mp["total_rows"] == log["modes_found"],
                      (mp and mp["total_rows"], log["modes_found"]))
        if isinstance(log, dict) and log.get("periods"):
            mp = show(f"{sw} modal periods (all)", "get_results",
                      {"software": sw, "result_type": "modal_periods", "max_rows": 100}, limit=400)
            got = [r_["Period"] for r_ in mp["rows"]] if isinstance(mp, dict) else []
            ok = len(got) == len(log["periods"]) and all(abs(a - b) <= 1e-6 + 1e-5 * b for a, b in zip(got, log["periods"]))
            check(f"{sw} periods from the results API = periods in the solver log (6 decimals)", ok,
                  (got[:3], log["periods"][:3]))
        if sw == "ETABS":
            sd = res.get("story_drifts")
            check(f"{sw} story drifts read", bool(sd) and sd["total_rows"] > 0, sd and sd["total_rows"])

        # ---- newest method variants still read the combinations
        cb = show(f"{sw} combos (GetCaseList_1)", "get_combo_details", {"software": sw}, limit=700)
        lc = show(f"{sw} cases (GetTypeOAPI_1)", "get_load_case_details", {"software": sw}, limit=900)
        if isinstance(lc, dict):
            check(f"{sw} case design type read", any("design_type" in c for c in lc["load_cases"]))

        # ---- reports
        r1 = show(f"{sw} export_report results + result diagrams", "export_report",
                  {"software": sw, "sections": ["cover", "results", "result_diagrams"],
                   "project_info": {"title": f"{sw} reference model - results", "project": "CSI MCP Phase 2"}},
                  limit=1500)
        if isinstance(r1, dict):
            check(f"{sw} result diagrams drawn", r1["figures"] >= 2, (r1["figures"], r1["skipped"]))
            shutil.copy(r1["report"], HERE / "results" / "reports" / f"{sw}_results_A4.pdf")
        r2 = show(f"{sw} export_report result diagrams A3 landscape", "export_report",
                  {"software": sw, "sections": ["result_diagrams"], "page_size": "A3", "orientation": "landscape",
                   "result_case": live}, limit=800)
        if isinstance(r2, dict):
            shutil.copy(r2["report"], HERE / "results" / "reports" / f"{sw}_results_A3.pdf")
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"could not close the scratch {sw}: {exc}", flush=True)
    print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)

sys.exit(finish())
