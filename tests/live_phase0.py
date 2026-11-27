r"""Live test of 0.3.0 (Phase 0 + report R1) in NEW scratch instances; your open models are not touched.

    run_live_phase0.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase0.txt

Per program: build the reference model (tests/reference_models.py) and save it as
tests\reference_models\ref_<program>.<ext> -> dry runs change nothing -> edit with snapshot -> change log ->
run_analysis with snapshot -> summaries -> query with automatic CSV export -> export_report A4 portrait (default) and
A3 landscape with tables -> save (the reference model keeps its results) -> close the instance.
Output folder for this test: tests\results\phase0_output (reports, exports, change log).
"""
import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase0_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001
from reference_models import BUILDERS  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}
REF = HERE / "reference_models"


def counts(sw):
    sap = bridge.sap_model(sw)
    return {c: bridge.count(sap, c) for c in ("PointObj", "FrameObj", "AreaObj", "LoadPatterns", "RespCombo")}


def log_lines() -> int:
    f = OUT / "change_log.jsonl"
    return len(f.read_text(encoding="utf-8").splitlines()) if f.exists() else 0


def media_box(pdf: str):
    m = re.search(rb"/MediaBox\s*\[\s*0\s+0\s+([\d.]+)\s+([\d.]+)\s*\]", Path(pdf).read_bytes())
    return (round(float(m.group(1))), round(float(m.group(2)))) if m else None


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
REF.mkdir(exist_ok=True)
(HERE / "results" / "reports").mkdir(exist_ok=True)

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
            print("reference model:", json.dumps(info, default=str)[:600], flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED reference model {sw}: {type(exc).__name__}: {exc}", flush=True)
            FAILED.append(f"{sw} reference model")
            continue
        path = str(REF / f"ref_{sw.lower()}{EXT[sw]}")
        show(f"{sw} save reference", "open_or_save_model", {"software": sw, "action": "save", "file_path": path})
        before = com(counts, sw)
        print("counts:", before, flush=True)
        sections = show(f"{sw} list_definitions", "list_definitions", {"software": sw, "max_items": 5}, limit=900)
        section = sections["frame_sections"]["items"][0] if isinstance(sections, dict) and \
            sections["frame_sections"].get("items") else None

        # ---- dry runs: nothing may change, nothing is logged
        logs = log_lines()
        start, end = info["brace"]
        if section:
            show(f"{sw} dry_run create_frame", "create_frame_element",
                 {"software": sw, "start_xyz": start, "end_xyz": end, "section_name": section, "dry_run": True},
                 limit=700)
        show(f"{sw} dry_run point load", "assign_point_or_frame_load",
             {"software": sw, "target_type": "point", "object_names": ["NO_SUCH"], "load_pattern": info["dead"],
              "values": [10], "dry_run": True}, expect_error=True, limit=500)
        show(f"{sw} dry_run run_analysis", "run_analysis", {"software": sw, "dry_run": True}, limit=700)
        show(f"{sw} dry_run set_units", "set_units", {"software": sw, "unit_preset": "N_mm_C", "dry_run": True},
             limit=300)
        check(f"{sw} dry runs changed nothing", com(counts, sw) == before, com(counts, sw))
        check(f"{sw} dry runs not logged", log_lines() == logs, log_lines())

        # ---- edit with snapshot, logged
        if section:
            r = show(f"{sw} create_frame with snapshot", "create_frame_element",
                     {"software": sw, "start_xyz": start, "end_xyz": end, "section_name": section, "snapshot": True},
                     limit=900)
            snap = r.get("snapshot", {}).get("snapshot") if isinstance(r, dict) else None
            check(f"{sw} snapshot file", bool(snap) and Path(snap).is_file(), snap)
            check(f"{sw} change log +1", log_lines() == logs + 1, log_lines())
        show(f"{sw} snapshot_model", "snapshot_model", {"software": sw, "label": "phase0"}, limit=500)

        # ---- analysis + results
        run = show(f"{sw} run_analysis with snapshot", "run_analysis", {"software": sw, "snapshot": True}, limit=1200)
        if isinstance(run, dict):
            for rt in ("base_reactions", "modal_periods", "joint_displacements") + (("story_drifts",) if sw == "ETABS" else ()):
                res = show(f"{sw} summary {rt}", "get_analysis_summary", {"software": sw, "result_type": rt},
                           limit=1500, expect_error=(rt == "modal_periods" and sw == "SAFE"))
                if rt == "joint_displacements" and isinstance(res, dict):
                    peak = max(abs(g["abs_max"]["value"] or 0) for c, g in res["summary"]["governing"].items()
                               if c.startswith("U"))
                    check(f"{sw} reference model is stable (max displacement < 1 m)", peak < 1.0, peak)
            q = show(f"{sw} query Joint Displacements (auto export)", "query_database_table",
                     {"software": sw, "table_key": "Joint Displacements", "max_rows": 5}, limit=1200)
            if isinstance(q, dict):
                check(f"{sw} auto export when cut", (q["export_path"] is not None) == q["truncated"] and
                      (not q["export_path"] or Path(q["export_path"]).is_file()), q.get("export_path"))

        # ---- reports
        r1 = show(f"{sw} export_report default", "export_report",
                  {"software": sw, "project_info": {"title": f"{sw} reference model", "project": "CSI MCP Phase 0",
                                                    "job_no": "REF-01", "prepared_by": "CSI MCP live test"}},
                  limit=1500)
        if isinstance(r1, dict):
            check(f"{sw} report A4 portrait", media_box(r1["report"]) == (595, 842) and r1["pages"] >= 2,
                  (media_box(r1["report"]), r1["pages"]))
            shutil.copy(r1["report"], HERE / "results" / "reports" / f"{sw}_A4_portrait.pdf")
        r2 = show(f"{sw} export_report A3 landscape + tables", "export_report",
                  {"software": sw, "page_size": "A3", "orientation": "landscape", "max_rows_per_table": 40,
                   "table_keys": ["Joint Displacements", "Base Reactions"],
                   "project_info": {"title": f"{sw} reference model (A3)"}}, limit=1500)
        if isinstance(r2, dict):
            check(f"{sw} report A3 landscape", media_box(r2["report"]) == (1191, 842), media_box(r2["report"]))
            shutil.copy(r2["report"], HERE / "results" / "reports" / f"{sw}_A3_landscape.pdf")
        show(f"{sw} save reference with results", "open_or_save_model", {"software": sw, "action": "save"}, limit=300)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"could not close the scratch {sw}: {exc}", flush=True)
    print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)

print("\nchange log lines:", log_lines())
sys.exit(finish())
