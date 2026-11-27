r"""V2 step 1: read the three V2 examples (copies) with the MCP read actions and save what a rebuild needs.

    run_v2_dump.bat      Output: tests\results\verify\v2\<model>.json (+ tests\results\v2_dump.txt)
"""
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(HERE))
OUT = TESTS / "results" / "verify" / "v2"
os.environ["CSI_MCP_OUTPUT"] = str(OUT / "output")

from live_common import bridge, com, finish, show  # noqa: E402,I001
from verify_models import model_copy  # noqa: E402

V2 = {"SAP2000": ["Example 1-001"], "ETABS": ["Example 01"], "SAFE": ["S03a-Thin"]}
UNITS = {"Example 1-001": "kip_in_F", "Example 01": "kip_in_F", "S03a-Thin": "kip_in_F"}
OUT.mkdir(parents=True, exist_ok=True)

for sw, models in V2.items():
    com(bridge.launch_new, sw, visible=False)
    try:
        for model in models:
            path = model_copy(model)
            show("open " + model, "open_or_save_model", {"software": sw, "action": "open", "file_path": str(path)},
                 limit=300)
            u = UNITS[model]
            snap = OUT / f"{model}.snapshot.json"
            show("snapshot", "export_model_snapshot", {"software": sw, "units": u, "output_path": str(snap)},
                 limit=600)
            extra = {}
            for name, action, params in (
                    ("cases", "get_load_case_details", {}), ("combos", "get_combo_details", {}),
                    ("materials", "get_definition_details", {"kind": "materials"}),
                    ("frame_sections", "get_definition_details", {"kind": "frame_sections"}),
                    ("area_sections", "get_definition_details", {"kind": "area_sections"}),
                    ("springs", "get_definition_details", {"kind": "springs"}),
                    ("patterns", "get_definition_details", {"kind": "load_patterns"}),
                    ("mass", "get_mass_source", {}), ("grids", "get_grids", {}),
                    ("stories", "get_stories", {}), ("status", "get_model_status", {})):
                if name == "stories" and sw == "SAP2000":
                    continue
                if name == "springs" and sw == "SAP2000":
                    continue
                r = show(f"{model} {name}", action, {"software": sw, "units": u, **params} if action not in (
                    "get_load_case_details", "get_combo_details", "get_mass_source", "get_model_status") else
                    {"software": sw, **params}, limit=1500)
                extra[name] = r if isinstance(r, dict) else {"_error": str(r)}
            (OUT / f"{model}.extra.json").write_text(json.dumps(extra, indent=1, default=str), encoding="utf-8")
            print(f"wrote {snap} and the extra file", flush=True)
    finally:
        com(bridge.disconnect, sw, True, False)
sys.exit(finish())
