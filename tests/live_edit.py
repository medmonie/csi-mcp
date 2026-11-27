r"""Full edit cycle in a NEW scratch instance of each program (the user's open models are never touched).
Started by run_live_edit.bat, which saves the output to results\live_edit.txt.

    live_edit.py [ETABS] [SAP2000] [SAFE] [--hidden]      default: all three, visible

Per program: start a new instance -> template model from the API (SAP2000 2-D portal, ETABS steel deck; SAFE blank)
-> save under %TEMP%\CsiMcpLive -> create frame, point + frame loads (with one wrong name on purpose) -> units round
trip -> run analysis -> lock guard -> summaries and table queries -> close the instance without saving.
"""
import os
import sys
import tempfile
import time

from live_common import FAILED, bridge, build_template, check, com, finish, frame_points, show  # noqa: I001
from csi_bridge import UNIT_CODES  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
SCRATCH = os.path.join(tempfile.gettempdir(), "CsiMcpLive")
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}


for sw in targets:
    print(f"\n################ {sw} (new scratch instance, visible={visible})", flush=True)
    t0 = time.perf_counter()
    try:
        conn = com(bridge.launch_new, sw, visible=visible)
        print(f"started via {conn.attached_via} in {time.perf_counter() - t0:.1f}s", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to start {sw}: {type(exc).__name__}: {exc} {getattr(exc, 'hint', '')}", flush=True)
        FAILED.append(f"{sw} start")
        continue
    try:
        try:
            info = com(build_template, sw)
            print("template:", {k: (v[:12] if isinstance(v, list) else v) for k, v in info.items()}, flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED template {sw}: {type(exc).__name__}: {exc}", flush=True)
            FAILED.append(f"{sw} template")
            info = {"sections": [], "patterns": [], "brace": ([0, 0, 0], [6, 0, 0])}

        show(f"{sw} get_model_status", "get_model_status", {"software": sw})
        show(f"{sw} list_definitions", "list_definitions", {"software": sw, "max_items": 10})
        os.makedirs(os.path.join(SCRATCH, sw), exist_ok=True)
        path = os.path.join(SCRATCH, sw, f"live_{sw.lower()}{EXT[sw]}")
        show(f"{sw} save as", "open_or_save_model", {"software": sw, "action": "save", "file_path": path})
        st = show(f"{sw} status after save", "get_model_status", {"software": sw}, limit=400)
        check(f"{sw} model_file after save", isinstance(st, dict) and str(st.get("model_file", "")).lower()
              .endswith(EXT[sw]), st.get("model_file") if isinstance(st, dict) else st)

        frame = None
        if info.get("sections"):
            start, end = info["brace"]
            r = show(f"{sw} create_frame_element", "create_frame_element",
                     {"software": sw, "start_xyz": start, "end_xyz": end, "section_name": info["sections"][0]})
            frame = r.get("frame_name") if isinstance(r, dict) else None
            if frame and sw == "SAFE":   # test set-up: fix both ends (an unsupported beam gives meaningless results)
                for pt in com(frame_points, sw, frame):
                    com(lambda n=pt: bridge.sap_model(sw).PointObj.SetRestraint(n, [True] * 6))
            show(f"{sw} create_frame_element wrong section", "create_frame_element",
                 {"software": sw, "start_xyz": start, "end_xyz": end, "section_name": "NO_SUCH_SECTION"},
                 expect_error=True, limit=600)
        else:
            print(f"({sw}: no frame sections in the template - frame steps skipped)", flush=True)

        if frame and info.get("dead"):
            pts = com(frame_points, sw, frame)
            print("frame points:", pts, flush=True)
            show(f"{sw} point load (one wrong name)", "assign_point_or_frame_load",
                 {"software": sw, "target_type": "point", "object_names": [str(pts[1]), "NO_SUCH_POINT"],
                  "load_pattern": info["dead"], "values": [25]}, expect_error=True, limit=1500)
            show(f"{sw} frame load", "assign_point_or_frame_load",
                 {"software": sw, "target_type": "frame", "object_names": [frame], "load_pattern": info["dead"],
                  "values": [10, 15], "direction": "Gravity"})

        units_before = com(lambda: bridge.sap_model(sw).GetPresentUnits())
        show(f"{sw} set_units Ton_m_C", "set_units", {"software": sw, "unit_preset": "Ton_m_C"})
        show(f"{sw} set_units back", "set_units",
             {"software": sw, "unit_preset": UNIT_CODES[units_before]})

        show(f"{sw} list_available_tables (before run)", "list_available_tables",
             {"software": sw, "search_term": "", "max_results": 5}, limit=1200)
        run = show(f"{sw} run_analysis", "run_analysis", {"software": sw}, expect_error=(sw == "SAFE" and not frame))
        if isinstance(run, dict):
            sums: dict = {}
            if info.get("sections"):
                show(f"{sw} lock guard", "create_frame_element",
                     {"software": sw, "start_xyz": [0, 0, 0], "end_xyz": [0, 6, 0], "section_name": info["sections"][0]},
                     expect_error=True, limit=800)
            for rt in ("base_reactions", "modal_periods", "joint_displacements", "story_drifts"):
                no_modal = rt == "modal_periods" and not any(c.lower() == "modal" for c in info.get("cases", []))
                sums[rt] = show(f"{sw} summary {rt}", "get_analysis_summary", {"software": sw, "result_type": rt},
                                expect_error=(rt == "story_drifts" and sw != "ETABS") or no_modal)
            show(f"{sw} summary joint_displacements {info.get('combo')}", "get_analysis_summary",
                 {"software": sw, "result_type": "joint_displacements", "combos": [info.get("combo")]})
            show(f"{sw} list tables 'displacement'", "list_available_tables",
                 {"software": sw, "search_term": "displacement"}, limit=1200)
            q = show(f"{sw} query Joint Displacements", "query_database_table",
                     {"software": sw, "table_key": "Joint Displacements", "load_cases_or_combos": [info.get("combo")],
                      "max_rows": 4, "sort_by": "U3" if sw == "SAP2000" else "Uz"})
            if isinstance(q, dict):
                check(f"{sw} query rows only for the combo",
                      all(r.get("OutputCase") == info.get("combo") for r in q["records"]))
            show(f"{sw} query Base Reactions filtered", "query_database_table",
                 {"software": sw, "table_key": "Base Reactions", "filters": {"OutputCase": "~" + info.get("dead", "")}},
                 )
            show(f"{sw} save", "open_or_save_model", {"software": sw, "action": "save"})
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"could not close the scratch {sw}: {exc}", flush=True)
    print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)

sys.exit(finish())
