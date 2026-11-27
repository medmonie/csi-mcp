r"""Diagnostic (0.15.2): units of ETABS mesh option tables, and whether the mesh-size edit lets the analysis run.
Uses a copy of tests\results\diagrams_output_b\models\diagrams_ETABS.EDB."""
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_common import bridge, com, finish  # noqa: E402
import db_edit  # noqa: E402
import results  # noqa: E402

HERE = Path(__file__).resolve().parent
src = HERE / "results" / "diagrams_output_b" / "models" / "diagrams_ETABS.EDB"
tmp = Path(tempfile.mkdtemp()) / "wallmesh.EDB"
shutil.copy(src, tmp)
KEYS = ("Analysis Options - Automatic Rectangular Mesh Options for Walls",
        "Analysis Options - Automatic Mesh Settings for Floors")


def sizes(sap, label):
    for preset in ("kN_m_C", "kip_in_F", "N_mm_C"):
        with bridge.units_scope(sap, preset):
            print(f"  [{label}] {preset}:", [db_edit.read_table(sap, k)[2][0] for k in KEYS], flush=True)
    print("  present units:", sap.GetPresentUnits(), " database units:",
          getattr(sap, "GetDatabaseUnits", lambda: "n/a")(), flush=True)


def run(sap, label):
    sap.SetModelIsLocked(False)
    r = sap.Analyze.RunAnalysis()
    print(f"  [{label}] RunAnalysis ->", r, flush=True)
    for a in ("W1", "S0_0_1"):
        try:
            df = results.read_results(sap, "ETABS", "area_forces", ["DL"], [], "envelope", [a], None)
            print(f"  [{label}] {a}: {df['Element'].nunique()} elements", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  [{label}] {a}: {type(exc).__name__} {str(exc)[:120]}", flush=True)


com(bridge.launch_new, "ETABS", visible=False)
try:
    sap = bridge.sap_model("ETABS")
    print("open", com(lambda: sap.File.OpenFile(str(tmp))), flush=True)
    com(sizes, sap, "as saved")
    for preset, val in (("kN_m_C", "1"), ("kip_in_F", "39.37")):
        def w(preset=preset, val=val):
            sap.SetModelIsLocked(False)
            with bridge.units_scope(sap, preset):
                for k in KEYS:
                    ver, f, rows = db_edit.read_table(sap, k)
                    rows[0]["MaxMeshSize"] = val
                    db_edit.write_table(sap, k, ver, f, rows)
        print(f"\nwrite {val} under {preset}", flush=True)
        com(w)
        com(sizes, sap, f"after {preset}")
        com(run, sap, f"after {preset}")
finally:
    print(com(bridge.disconnect, "ETABS", True, False), flush=True)
sys.exit(finish())
