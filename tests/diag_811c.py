r"""Diagnostic 3 (roadmap 8.11): panel zone connectivity / local-axis codes (SAP2000, ETABS) read back through
GetPanelZone and the assignment tables. Output: tests\results\diag_811c.txt"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_811c_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish, show  # noqa: E402,I001
import db_edit  # noqa: E402

targets = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or ["SAP2000", "ETABS"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}
TABLE = {"SAP2000": "Joint Panel Zone Assignments", "ETABS": "Panel Zone Property Definitions"}


def raw(label, fn):
    try:
        r = com(fn)
        print(f"  {label}: {r!r}"[:900], flush=True)
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: EXC {type(exc).__name__}: {exc}"[:400], flush=True)
        return None


def tab(sap, key):
    try:
        v, f, rows = com(db_edit.read_table, sap, key)
        for r in rows[-3:]:
            print("     ", {k: r[k] for k in f if k in ("Joint", "Name", "PZFrom", "Source", "PZConnect", "Connect",
                                                          "AxesFrom", "AxisSource", "PZAxesAngle", "Axis2Angle",
                                                          "DoublerPl", "DblPlThick")}, flush=True)
    except Exception as exc:  # noqa: BLE001
        print("  table EXC", exc, flush=True)


def run(sw):
    S = {"software": sw, "units": "kN_m_C"}
    nm = {**S, "file_path": str(OUT / f"d811c_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=100)
    show("steel", "define_material", {**S, "name": "ST", "type": "steel", "E": 2e8, "poisson": 0.3,
                                      "unit_weight": 77.0, "fy": 345000.0, "fu": 450000.0}, limit=100)
    show("I", "define_frame_section", {**S, "name": "W1", "shape": "i_section", "material": "ST", "depth": 0.4,
                                       "width": 0.2, "flange_thickness": 0.015, "web_thickness": 0.01}, limit=100)
    fr = show("frames", "create_frames", {**S, "section": "W1", "frames": [
        {"start": [0, 0, 3.0], "end": [6, 0, 3.0]}, {"start": [0, 0, 0], "end": [0, 0, 3.0]}]}, limit=300)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    b = fr["created"][0]["name"]
    pj = str(com(lambda: sap.FrameObj.GetPoints(b))[0])
    po = sap.PointObj
    for conn in (0, 1, 2, 3):
        raw(f"SetPanelZone column conn {conn}", lambda conn=conn: po.SetPanelZone(pj, 1, 0.0, 0.0, 0.0, "", conn, 1,
                                                                                    0.0))
        raw("  read", lambda: po.GetPanelZone(pj))
        tab(sap, TABLE[sw])
    for ax in (0, 1, 2, 3):
        raw(f"SetPanelZone springs axis {ax} angle 30", lambda ax=ax: po.SetPanelZone(pj, 2, 0.0, 1e6, 2e6, "", 1, ax,
                                                                                         30.0))
        raw("  read", lambda: po.GetPanelZone(pj))
        tab(sap, TABLE[sw])
    raw("SetPanelZone column axis 2 angle 30", lambda: po.SetPanelZone(pj, 1, 0.0, 0.0, 0.0, "", 1, 2, 30.0))
    raw("  read", lambda: po.GetPanelZone(pj))
    tab(sap, TABLE[sw])


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
for sw in targets:
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=True)
        run(sw)
    except Exception as exc:  # noqa: BLE001
        print("EXCEPTION", type(exc).__name__, exc, flush=True)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("close", exc, flush=True)
sys.exit(finish())
