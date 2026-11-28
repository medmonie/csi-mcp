r"""Diagnostic 3 (0.24.0): ETABS 'Load Case Definitions - Damping - Hysteretic' - which rows the program keeps for a
steady-state case and which import forms it takes. Output: tests\results\diag_86c.txt"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_86c_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish, show  # noqa: E402,I001
import db_edit  # noqa: E402

H = "Load Case Definitions - Damping - Hysteretic"
SW = "ETABS"


def rows(sap, key=H):
    try:
        v, f, r = com(db_edit.read_table, sap, key)
        print(f"  ROWS {key} fields={f} ({len(r)}):", flush=True)
        for x in r[:30]:
            print("    ", dict(x), flush=True)
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"  ROWS {key}: EXC {exc}"[:800], flush=True)
        return []


def imp(sap, label, new_rows, drop=None, update=None):
    print(f"--- {label}", flush=True)
    try:
        r = com(db_edit.edit_table, sap, H, new_rows, drop, update)
        print(f"  IMPORT ok: {r}"[:1500], flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  IMPORT EXC {type(exc).__name__}: {exc} {getattr(exc, 'hint', '')}"[:1500], flush=True)
    rows(sap)


def ss(S, name):
    show(name, "define_advanced_load_case", {**S, "name": name, "type": "steady_state",
                                             "loads": [{"name": "F", "function": "SSF"}], "first_frequency": 1.0,
                                             "last_frequency": 1.0, "frequency_increments": 1}, limit=600)


def run():
    S = {"software": SW, "units": "kN_m_C"}
    show("new model", "new_model", {**S, "file_path": str(OUT / "d86c.edb"), "discard_open_model": True,
                                    "kind": "grid_only", "stories": 1, "story_height": 3.0, "spacing_x": 6.0,
                                    "spacing_y": 6.0}, limit=200)
    show("F", "define_load_pattern", {**S, "name": "F", "type": "other", "self_weight_multiplier": 0.0}, limit=100)
    show("SSF", "define_function", {**S, "name": "SSF", "kind": "steady_state", "points": [[0.0, 1.0], [100.0, 1.0]]},
         limit=300)
    sap = bridge.sap_model(SW)
    ss(S, "SS1")
    print("after SS1 defined:", flush=True)
    have = rows(sap)
    try:
        g = com(lambda: sap.DatabaseTables.GetTableForEditingArray(H, ""))
        print("  RAW editing array:", repr(g)[:1500], flush=True)
    except Exception as exc:  # noqa: BLE001
        print("  RAW EXC", exc, flush=True)
    # forms to try, each on its own case
    ss(S, "SS2")
    imp(sap, "SS2 update StiffCoeff only", [],
        update=lambda r: {"StiffCoeff": "0.04"} if r.get("Name") == "SS2" else None)
    ss(S, "SS3")
    imp(sap, "SS3 update DampType Constant + coeffs", [],
        update=lambda r: {"DampType": "Constant", "MassCoeff": "0", "StiffCoeff": "0.04"}
        if r.get("Name") == "SS3" else None)
    ss(S, "SS4")
    imp(sap, "SS4 drop + add full row (Hz, Frequency 0)",
        [{"Name": "SS4", "DampType": "Constant", "FreqUnit": "Hz", "Frequency": "0", "MassCoeff": "0",
          "StiffCoeff": "0.04"}], drop=lambda r: r.get("Name") == "SS4")
    ss(S, "SS5")
    imp(sap, "SS5 add full row without drop",
        [{"Name": "SS5", "DampType": "Constant", "FreqUnit": "Hz", "Frequency": "0", "MassCoeff": "0",
          "StiffCoeff": "0.04"}])
    for n in ("SS1", "SS2", "SS3", "SS4", "SS5"):
        show(f"read {n}", "get_load_case_details", {"software": SW, "names": [n]}, limit=400)
    _ = have


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
try:
    com(bridge.launch_new, SW, visible=True)
    run()
except Exception as exc:  # noqa: BLE001
    import traceback
    print("EXCEPTION", type(exc).__name__, exc, traceback.format_exc()[-1500:], flush=True)
finally:
    try:
        print(com(bridge.disconnect, SW, True, False), flush=True)
    except Exception as exc:  # noqa: BLE001
        print("close", exc, flush=True)
sys.exit(finish())
