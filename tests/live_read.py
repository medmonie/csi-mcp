r"""Runs the read actions against the programs that are OPEN on this PC (no changes to any model).
Started by run_live_read.bat, which saves the output to results\live_read.txt.

    live_read.py [ETABS] [SAP2000] [SAFE]      default: every program that is running

Also checks that the user's display units and table display selection are exactly as before.
"""
import sys

from live_common import bridge, check, com, finish, show

targets = [a.upper() for a in sys.argv[1:]] or ["ETABS", "SAP2000", "SAFE"]
progs = show("list_programs", "list_programs")
running = [s for s in targets if isinstance(progs, dict) and progs["programs"].get(s, {}).get("reachable")]
print("running:", running, "| not running:", [s for s in targets if s not in running], flush=True)


def gui_state(sw):
    sap = bridge.sap_model(sw)
    return sap.GetPresentUnits(), bridge.display_selection(sap)


for sw in running:
    print(f"\n################ {sw}", flush=True)
    before = com(gui_state, sw)
    st = show(f"{sw} get_model_status", "get_model_status", {"software": sw})
    show(f"{sw} list_definitions", "list_definitions", {"software": sw, "max_items": 15})
    show(f"{sw} help get_model_status", "help", {"topic": "get_model_status"}, limit=600)
    tables = show(f"{sw} list_available_tables", "list_available_tables", {"software": sw, "max_results": 400}, limit=1500)
    keys = [t["key"] for t in tables["tables"]] if isinstance(tables, dict) else []
    model_tables = [k for k in keys if any(w in k.lower() for w in ("coordinates", "connectivity", "story definitions",
                                                                     "material properties - summary"))]
    for key in model_tables[:2]:
        show(f"{sw} query '{key}'", "query_database_table", {"software": sw, "table_key": key, "max_rows": 3})
    if isinstance(st, dict) and st.get("model_locked"):
        for rt in ("base_reactions", "modal_periods", "joint_displacements", "story_drifts"):
            show(f"{sw} summary {rt}", "get_analysis_summary", {"software": sw, "result_type": rt},
                 expect_error=(rt == "story_drifts" and sw != "ETABS"))
    else:
        print(f"({sw}: model has no results, summaries not called)", flush=True)
    after = com(gui_state, sw)
    check(f"{sw} display units unchanged", before[0] == after[0], (before[0], after[0]))
    check(f"{sw} display selection unchanged", before[1] == after[1])

sys.exit(finish())
