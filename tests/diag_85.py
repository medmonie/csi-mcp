r"""Diagnostic (0.23.0 / roadmap 8.5): layered shells, deck sections, stress-strain curves and time-dependent
material properties in SAP2000, ETABS and SAFE - which calls and codes each program accepts, and what it reads back.
Output: tests\results\diag_85.txt"""
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_85_output"
import os  # noqa: E402
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish, show  # noqa: E402,I001
from csi_bridge import as_list  # noqa: E402

targets = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or ["SAP2000", "ETABS", "SAFE"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}


def raw(label, fn):
    try:
        r = com(fn)
        print(f"  {label}: {r!r}"[:1500], flush=True)
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: EXC {type(exc).__name__}: {exc}"[:600], flush=True)
        return None


def run(sw):
    S = {"software": sw, "units": "kN_m_C"}
    nm = {**S, "file_path": str(OUT / f"d85_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=200)
    show("C30", "define_material", {**S, "name": "C30", "type": "concrete", "E": 2.5e7, "poisson": 0.0,
                                    "unit_weight": 25.0, "fc": 30000.0}, limit=200)
    show("C60", "define_material", {**S, "name": "C60", "type": "concrete", "E": 5.0e7, "poisson": 0.0,
                                    "unit_weight": 25.0, "fc": 60000.0}, limit=200)
    show("ST", "define_material", {**S, "name": "ST", "type": "steel", "E": 2.0e8, "poisson": 0.3,
                                   "unit_weight": 77.0, "fy": 355000.0, "fu": 510000.0}, limit=200)
    show("R", "define_material", {**S, "name": "R", "type": "rebar", "E": 2.0e8, "poisson": 0.3,
                                  "unit_weight": 77.0, "fy": 420000.0, "fu": 620000.0}, limit=200)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    pa, pm = sap.PropArea, sap.PropMaterial
    print("PropArea members:", sorted(m for m in dir(pa) if not m.startswith("_"))[:200], flush=True)
    print("PropMaterial members:", sorted(m for m in dir(pm) if not m.startswith("_")), flush=True)

    # ---- layered shells
    lay = (["TOP", "BOT"], [0.05, -0.05], [0.1, 0.1], [1, 1], [2, 2], ["C30", "C60"], [0.0, 0.0], [1, 1], [1, 1],
           [1, 1])
    lay2 = lay[:8] + ([1, 1],) + lay[8:]
    if sw == "SAP2000":
        for st in (1, 2, 6):
            nme = f"SH{st}"
            raw(f"SetShell_1 type {st}", lambda st=st, nme=nme: pa.SetShell_1(nme, st, False, "C30", 0.0, 0.2, 0.2))
            raw(f"GetShell_1 {nme}", lambda nme=nme: pa.GetShell_1(nme))
        targets_ = ["SH6", "SH2"]
    else:
        for st in (1, 2, 3, 4, 5, 6, 7):
            nme = f"SL{st}"
            raw(f"SetSlab type {st}", lambda st=st, nme=nme: pa.SetSlab(nme, 0, st, "C30", 0.2))
            raw(f"GetSlab {nme}", lambda nme=nme: pa.GetSlab(nme))
        for st in (1, 6):
            nme = f"WL{st}"
            raw(f"SetWall type {st}", lambda st=st, nme=nme: pa.SetWall(nme, 1, st, "C30", 0.2))
            raw(f"GetWall {nme}", lambda nme=nme: pa.GetWall(nme))
        targets_ = [f"SL{st}" for st in (6, 7, 4, 5, 1)] + ["WL6"]
    for t in targets_:
        raw(f"SetShellLayer_1 {t}", lambda t=t: pa.SetShellLayer_1(t, 2, *lay))
        raw(f"GetShellLayer_1 {t}", lambda t=t: pa.GetShellLayer_1(t))
        raw(f"GetShellLayer_2 {t}", lambda t=t: pa.GetShellLayer_2(t))
        raw(f"after layers type {t}", lambda t=t: pa.GetShell_1(t) if sw == "SAP2000" else pa.GetSlab(t))
        raw(f"GetTypeOAPI {t}", lambda t=t: pa.GetTypeOAPI(t))
    raw("SetShellLayer_2 L2", lambda: (pa.SetShell_1("L2", 6, False, "C30", 0.0, 0.2, 0.2) if sw == "SAP2000"
                                        else pa.SetSlab("L2", 0, 6, "C30", 0.2)))
    raw("SetShellLayer_2 call", lambda: pa.SetShellLayer_2("L2", 2, *lay2))
    raw("GetShellLayer_2 L2", lambda: pa.GetShellLayer_2("L2"))
    raw("GetModifiers SL/SH", lambda: pa.GetModifiers(targets_[0]))

    # ---- decks (ETABS / SAFE)
    if sw != "SAP2000":
        for dt in (1, 2, 3):
            nme = f"DK{dt}"
            raw(f"SetDeck type {dt}", lambda dt=dt, nme=nme: pa.SetDeck(nme, dt, 3, "C30", 0.15))
            raw(f"GetDeck {nme}", lambda nme=nme: pa.GetDeck(nme))
        raw("SetDeckFilled DK1", lambda: pa.SetDeckFilled("DK1", 0.065, 0.075, 0.15, 0.12, 0.3, 0.0009, 0.12,
                                                         0.019, 0.125, 400000.0))
        raw("GetDeckFilled DK1", lambda: pa.GetDeckFilled("DK1"))
        raw("SetDeckUnfilled DK2", lambda: pa.SetDeckUnfilled("DK2", 0.075, 0.15, 0.12, 0.3, 0.0009, 0.12))
        raw("GetDeckUnfilled DK2", lambda: pa.GetDeckUnfilled("DK2"))
        raw("SetDeckSolidSlab DK3", lambda: pa.SetDeckSolidSlab("DK3", 0.15, 0.019, 0.125, 400000.0))
        raw("GetDeckSolidSlab DK3", lambda: pa.GetDeckSolidSlab("DK3"))
        raw("SetDeck_1 DKN", lambda: pa.SetDeck_1("DKN", 1, "C30", "ST", 0.065, 0.075, 0.15, 0.12, 0.3, 0.0009,
                                                  0.12, 0.019, 0.125, 400000.0))
        raw("GetDeck_1 DKN", lambda: pa.GetDeck_1("DKN"))
        raw("GetDeck_1 DK1", lambda: pa.GetDeck_1("DK1"))
        raw("GetDeck DKN", lambda: pa.GetDeck("DKN"))
        raw("GetTypeOAPI DKN", lambda: pa.GetTypeOAPI("DKN"))

    # ---- stress-strain curves
    raw("GetOConcrete_1 C30", lambda: pm.GetOConcrete_1("C30"))
    raw("GetSSCurve C30 (default)", lambda: pm.GetSSCurve("C30"))
    raw("SetOConcrete_1 C30 simple (SSType 1)", lambda: pm.SetOConcrete_1("C30", 30000.0, False, 1.0, 1, 4, 0.002,
                                                                           0.005, -0.1, 0.0, 0.0))
    raw("GetOConcrete_1 C30", lambda: pm.GetOConcrete_1("C30"))
    raw("GetSSCurve C30 (simple)", lambda: pm.GetSSCurve("C30"))
    raw("SetOConcrete_1 C30 Mander (SSType 2)", lambda: pm.SetOConcrete_1("C30", 30000.0, False, 1.0, 2, 4, 0.002,
                                                                           0.005, -0.1, 0.0, 0.0))
    raw("GetSSCurve C30 (mander)", lambda: pm.GetSSCurve("C30"))
    raw("SetOConcrete_1 C60 user (SSType 0)", lambda: pm.SetOConcrete_1("C60", 60000.0, False, 1.0, 0, 4, 0.002,
                                                                           0.005, -0.1, 0.0, 0.0))
    pts = ([-3, -2, -1, 0, 1], [-0.004, -0.002, -0.001, 0.0, 0.0002], [-40000.0, -60000.0, -45000.0, 0.0, 3000.0])
    raw("SetSSCurve C60", lambda: pm.SetSSCurve("C60", 5, *pts))
    raw("GetSSCurve C60", lambda: pm.GetSSCurve("C60"))
    raw("GetOConcrete_1 C60", lambda: pm.GetOConcrete_1("C60"))
    pts0 = ([-2, -1, 0, 1, 2], [-0.004, -0.002, 0.0, 0.0002, 0.001], [-40000.0, -60000.0, 0.0, 3000.0, 0.0])
    raw("SetSSCurve C60 ids from -2", lambda: pm.SetSSCurve("C60", 5, *pts0))
    raw("GetSSCurve C60 b", lambda: pm.GetSSCurve("C60"))
    raw("GetOSteel_1 ST", lambda: pm.GetOSteel_1("ST"))
    raw("GetSSCurve ST (default)", lambda: pm.GetSSCurve("ST"))
    raw("SetOSteel_1 ST simple (SSType 1)", lambda: pm.SetOSteel_1("ST", 355000.0, 510000.0, 355000.0, 510000.0,
                                                                    1, 1, 0.02, 0.1, 0.2, -0.1))
    raw("GetSSCurve ST (simple)", lambda: pm.GetSSCurve("ST"))
    raw("SetOSteel_1 ST user (SSType 0)", lambda: pm.SetOSteel_1("ST", 355000.0, 510000.0, 355000.0, 510000.0,
                                                                  0, 1, 0.02, 0.1, 0.2, -0.1))
    spts = ([-2, -1, 0, 1, 2], [-0.02, -0.001775, 0.0, 0.001775, 0.02], [-400000.0, -355000.0, 0.0, 355000.0,
                                                                          400000.0])
    raw("SetSSCurve ST", lambda: pm.SetSSCurve("ST", 5, *spts))
    raw("GetSSCurve ST", lambda: pm.GetSSCurve("ST"))
    raw("GetOSteel_1 ST", lambda: pm.GetOSteel_1("ST"))
    raw("GetORebar_1 R", lambda: pm.GetORebar_1("R"))
    raw("GetSSCurve R", lambda: pm.GetSSCurve("R"))
    raw("GetMPIsotropic C30", lambda: pm.GetMPIsotropic("C30"))

    # ---- time-dependent
    td = raw("PropMaterial.TimeDep", lambda: pm.TimeDep)
    if td is not None:
        print("  TimeDep members:", sorted(m for m in dir(td) if not m.startswith("_")), flush=True)
        raw("SetConcreteCEBFIP90", lambda: td.SetConcreteCEBFIP90("C30", True, True, True, 0.25, 70.0, 0.2, 5.0, 3.0,
                                                                  1, 5))
        raw("GetConcreteCEBFIP90", lambda: td.GetConcreteCEBFIP90("C30"))
        raw("SetConcreteScaleFactors", lambda: td.SetConcreteScaleFactors("C30", 1.0, 1.2, 0.8))
        raw("GetConcreteScaleFactors", lambda: td.GetConcreteScaleFactors("C30"))
        for m in sorted(m for m in dir(td) if m.startswith("Get")):
            raw(f"TimeDep.{m}", lambda m=m: getattr(td, m)("C30"))

    # ---- tables
    o = com(lambda: sap.DatabaseTables.GetAllTables())
    keys = [str(k) for k in as_list(o[1])]
    hits = [k for k in keys if any(w in k for w in ("Layer", "Deck", "Stress-Strain", "Time", "Creep", "Shrink",
                                                    "Area Section", "Material Properties", "Shell"))]
    print("tables:", hits, flush=True)
    for k in hits:
        if any(w in k for w in ("Layer", "Deck", "Stress-Strain", "Time", "Creep")):
            f = raw(f"fields {k}", lambda k=k: sap.DatabaseTables.GetAllFieldsInTable(k))
            raw(f"display {k}", lambda k=k: sap.DatabaseTables.GetTableForDisplayArray(k, [], "All"))


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
