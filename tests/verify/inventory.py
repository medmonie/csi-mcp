r"""V1 step 1 - inventory of CSI verification models (read only, no analysis).

    run_verify_inventory.bat [--hidden]        Output: tests\results\verify\inventory\<model>.json + inventory.txt

Copies each selected model from "CSI Solved Examples" to tests\results\verify\models (the originals are never
opened), opens the copy in a scratch instance of its program and records what the expected-value files need:
units, load cases with type and loads, combinations, points (coordinates, restraint), frames (end points, section),
area count, groups. Nothing is analysed or saved.
"""
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
ROOT = TESTS.parent
sys.path.insert(0, str(TESTS))
OUT = TESTS / "results" / "verify"
os.environ["CSI_MCP_OUTPUT"] = str(OUT / "output")

from live_common import FAILED, bridge, com, finish  # noqa: E402,I001
from verify_models import MODELS, model_copy  # noqa: E402

visible = "--hidden" not in sys.argv
only = [a.upper() for a in sys.argv[1:] if not a.startswith("--")]
(OUT / "inventory").mkdir(parents=True, exist_ok=True)


def quiet(action, params):
    try:
        return server_run(action, params)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"{type(exc).__name__}: {exc}"}


def server_run(action, params):
    import server
    return server.executor.run(server.dispatch, action, params)


for sw in ("SAP2000", "ETABS", "SAFE"):
    if only and sw not in only:
        continue
    names = [m for m, (prog, _) in MODELS.items() if prog == sw]
    print(f"\n################ {sw}: {len(names)} models", flush=True)
    try:
        com(bridge.launch_new, sw, visible=visible)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to start {sw}: {exc}", flush=True)
        FAILED.append(f"{sw} start")
        continue
    try:
        for name in names:
            t0 = time.perf_counter()
            path = model_copy(name)
            r = quiet("open_or_save_model", {"software": sw, "action": "open", "file_path": str(path)})
            if "error" in r:
                print(f"FAILED open {name}: {r['error']}", flush=True)
                FAILED.append(f"open {name}")
                continue
            inv = {"model": name, "software": sw, "file": str(path)}
            inv["status"] = quiet("get_model_status", {"software": sw})
            inv["cases"] = quiet("get_load_case_details", {"software": sw, "max_rows": 200})
            inv["combos"] = quiet("get_combo_details", {"software": sw, "max_rows": 200})
            inv["objects"] = quiet("get_objects", {"software": sw, "max_rows": 600, "units": "kip_in_F"})
            inv["groups"] = quiet("get_groups", {"software": sw})
            if sw != "SAP2000":
                inv["stories"] = quiet("get_stories", {"software": sw, "units": "kip_in_F"})
            (OUT / "inventory" / f"{name}.json").write_text(json.dumps(inv, indent=1, default=str), encoding="utf-8")
            o = inv["objects"]
            cnt = {k: o.get(k, {}).get("count") for k in ("points", "frames", "areas")} if isinstance(o, dict) else o
            cases = [c["name"] for c in inv["cases"].get("load_cases", [])] if isinstance(inv["cases"], dict) else []
            print(f"{name}: {cnt} cases {cases} ({time.perf_counter() - t0:.1f}s)", flush=True)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"could not close {sw}: {exc}", flush=True)

sys.exit(finish())
