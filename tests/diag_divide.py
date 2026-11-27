"""Diagnostic: which call form of EditArea.Divide_1 / Divide works through comtypes (SAP2000)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_common import bridge, com, finish  # noqa: E402
from live_phase3_wall import build_wall  # noqa: E402

FORMS = {
    "Divide_1 8 args (refs 0, [])": lambda s, a: s.EditArea.Divide_1(a, 1, 0, [], 2, 2, 0.0, 0.0),
    "Divide_1 8 args (refs 0, None)": lambda s, a: s.EditArea.Divide_1(a, 1, 0, None, 2, 2, 0.0, 0.0),
    "Divide_1 omit refs, keywords": lambda s, a: s.EditArea.Divide_1(a, 1, N1=2, N2=2),
    "Divide_1 name+type only": lambda s, a: s.EditArea.Divide_1(a, 1),
    "Divide 8 args (refs 0, [])": lambda s, a: s.EditArea.Divide(a, 1, 0, [], 2, 2, 0.0, 0.0),
    "Divide name+type only": lambda s, a: s.EditArea.Divide(a, 1),
}

com(bridge.launch_new, "SAP2000", visible=False)
try:
    for label, fn in FORMS.items():
        sap = bridge.sap_model("SAP2000")
        wall, _ = com(build_wall, sap)
        try:
            out = com(fn, sap, wall)
            n = com(lambda: sap.AreaObj.GetNameList()[0])
            print(f"{label}: returned {out!r} -> areas now {n}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"{label}: {type(exc).__name__}: {exc}", flush=True)
finally:
    print(com(bridge.disconnect, "SAP2000", True, False), flush=True)
sys.exit(finish())
