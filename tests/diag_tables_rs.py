"""Diagnostic (scratch instances): ETABS / SAFE have no API setter for user RS / TH functions or RS damping / modal
and directional combination; find the database tables that hold them and try the table edit route.

    run_diag_tables_rs.bat [ETABS] [SAFE] [SAP2000]      Output: tests\\results\\diag_tables_rs.txt
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from live_common import bridge, com, finish  # noqa: E402
from csi_bridge import api_call, as_list  # noqa: E402

targets = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or ["ETABS", "SAFE"]
WORDS = ("function", "spectrum", "time history", "load case", "modal history", "damping")


def setup(sw):
    sap = bridge.sap_model(sw)
    api_call("InitializeNewModel", sap.InitializeNewModel, 6)
    if sw == "SAP2000":
        api_call("File.NewBlank", sap.File.NewBlank)
    else:
        api_call("File.NewGridOnly", sap.File.NewGridOnly, 1, 4.0, 4.0, 2, 2, 6.0, 6.0)
    return sap


def tables(sap):
    o = api_call("DatabaseTables.GetAllTables", sap.DatabaseTables.GetAllTables)
    keys, names, imp = as_list(o[1]), as_list(o[2]), as_list(o[3])
    return [(str(k), str(n), int(i)) for k, n, i in zip(keys, names, imp)
            if any(w in str(k).lower() for w in WORDS)]


def fields(sap, key):
    o = sap.DatabaseTables.GetAllFieldsInTable(key)
    return o


def edit_rows(sap, key):
    return sap.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])


def try_set(sap, key, field_keys, rows):
    flat = [str(v) for r in rows for v in r]
    o = sap.DatabaseTables.SetTableForEditingArray(key, 0, list(field_keys), len(rows), flat)
    print(f"    SetTableForEditingArray -> {o!r}", flush=True)
    a = sap.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    print(f"    ApplyEditedTables -> {a!r}", flush=True)


def main(sw):
    sap = setup(sw)
    tl = tables(sap)
    print(f"\n######## {sw}: {len(tl)} tables matching {WORDS}", flush=True)
    for k, n, i in tl:
        print(f"  [{i}] {k}", flush=True)
    for k, _, i in tl:
        lk = k.lower()
        if ("spectrum" in lk or "time history" in lk or "modal history" in lk) and i > 0:
            try:
                f = fields(sap, k)
                print(f"\n  FIELDS {k}: keys={list(as_list(f[3]))} importable={list(as_list(f[7]))} "
                      f"units={list(as_list(f[6]))} ret={f[-1]}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  FIELDS {k}: {exc}", flush=True)
            try:
                e = edit_rows(sap, k)
                print(f"  EDIT {k}: {e!r}"[:900], flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  EDIT {k}: {exc}", flush=True)
    # try to add a user RS function and a user TH function through the tables
    for key, rows_of in (("Functions - Response Spectrum - User Defined",
                          lambda fk: [[{"Name": "RSFLAT", "Period": "0", "Value": "0.5", "DampRatio": "0.05"}.get(
                              f, "") for f in fk], [{"Name": "RSFLAT", "Period": "10", "Value": "0.5",
                                                     "DampRatio": "0.05"}.get(f, "") for f in fk]]),
                         ("Functions - Time History - User Defined",
                          lambda fk: [[{"Name": "STEP", "Time": "0", "Value": "1"}.get(f, "") for f in fk],
                                      [{"Name": "STEP", "Time": "10", "Value": "1"}.get(f, "") for f in fk]])):
        if key not in [k for k, _, _ in tl]:
            print(f"\n  {key}: not in this program", flush=True)
            continue
        f = fields(sap, key)
        fk = [str(x) for x in as_list(f[3])]
        print(f"\n  TRY {key} with fields {fk}", flush=True)
        try:
            try_set(sap, key, fk, rows_of(fk))
        except Exception as exc:  # noqa: BLE001
            print(f"    failed: {type(exc).__name__}: {exc}", flush=True)
    try:
        n = sap.Func.GetNameList(0, [])
        print(f"\n  Func.GetNameList -> {n!r}", flush=True)
        for name in ("RSFLAT", "STEP"):
            print(f"  Func.GetValues({name}) -> {sap.Func.GetValues(name, 0, [], [])!r}", flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  Func read: {exc}", flush=True)
    # RS and modal history cases through the API, then their table rows
    try:
        lc = sap.LoadCases
        print("\n  RS SetCase", lc.ResponseSpectrum.SetCase("RSX"), flush=True)
        print("  RS SetLoads", lc.ResponseSpectrum.SetLoads("RSX", 1, ["U1"], ["RSFLAT"], [9.80665], ["Global"],
                                                           [0.0]), flush=True)
        print("  RS GetModalComb_1", lc.ResponseSpectrum.GetModalComb_1("RSX"), flush=True)
        print("  RS GetDirComb", lc.ResponseSpectrum.GetDirComb("RSX"), flush=True)
        print("  RS GetDampConstant", lc.ResponseSpectrum.GetDampConstant("RSX"), flush=True)
        print("  RS GetModalCase", lc.ResponseSpectrum.GetModalCase("RSX"), flush=True)
        print("  MH SetCase", lc.ModalHistoryLinear.SetCase("THX"), flush=True)
        print("  MH SetLoads", lc.ModalHistoryLinear.SetLoads("THX", 1, ["Load"], ["Dead"], ["STEP"], [1.0], [1.0],
                                                             [0.0], ["Global"], [0.0]), flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  cases: {type(exc).__name__}: {exc}", flush=True)
    for k, _, i in tables(sap):
        lk = k.lower()
        if lk.startswith("load case definitions") and ("spectrum" in lk or "modal history" in lk or "history" in lk):
            try:
                e = edit_rows(sap, k)
                print(f"\n  CASE TABLE {k}: {e!r}"[:1500], flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"  CASE TABLE {k}: {exc}", flush=True)


for sw in targets:
    try:
        com(bridge.launch_new, sw, visible=False)
        com(main, sw)
    except Exception as exc:  # noqa: BLE001
        print(f"{sw}: {type(exc).__name__}: {exc}", flush=True)
    finally:
        try:
            com(bridge.disconnect, sw, True, False)
        except Exception as exc:  # noqa: BLE001
            print(f"close {sw}: {exc}", flush=True)
sys.exit(finish())
