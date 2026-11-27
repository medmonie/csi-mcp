r"""Phase 5 (0.17.0): automatic wind / seismic loads by any code the program offers - describe_auto_load_code and
set_auto_load, in NEW instances.

    run_live_autoload.bat [SAP2000] [ETABS] [--describe] [--hidden]      Output: tests\results\live_autoload.txt

--describe: list the wind and seismic codes and print the parameters of the codes used below (no loads set).
Otherwise: the 3 x 2 bay, 2-storey building of live_wind with a diaphragm per floor; Eurocode 1 (2005) wind on
pattern WEU and Eurocode 8 (2004) seismic on EEU, read back, analysed, base shears printed and checked against the
hand calculation (see the checks).
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "autoload_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001



args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS"]
visible = "--hidden" not in sys.argv
describe_only = "--describe" in sys.argv
EXT = {"SAP2000": ".sdb", "ETABS": ".edb"}
U = "kN_m_C"
WIND = {"SAP2000": "Eurocode12005", "ETABS": "Eurocode1 2005"}
QUAKE = {"SAP2000": "Eurocode82004", "ETABS": "Eurocode 8 2004"}


def describe(S: dict, sw: str) -> None:
    for kind in ("wind", "seismic"):
        show(f"{kind} codes", "describe_auto_load_code", {**S, "kind": kind}, limit=4000)
    show("Eurocode 1 wind", "describe_auto_load_code", {**S, "kind": "wind", "code": WIND[sw]}, limit=12000)
    show("Eurocode 8 seismic", "describe_auto_load_code", {**S, "kind": "seismic", "code": QUAKE[sw]}, limit=12000)


FT = 0.3048
VB, RHO, Z0, ZMIN, KR = 26.0, 1.25, 0.05, 2.0, 0.19        # EN 1991-1-4, terrain II (recommended values)
AG, S_B, TB, TC, TD, Q, T_USER = 0.16, 1.2, 0.15, 0.5, 2.0, 3.9, 0.4


def qp(z: float) -> float:
    """EN 1991-1-4 peak velocity pressure, kN/m2 (co = 1, k1 = 1)."""
    z = max(z, ZMIN)
    cr = KR * __import__("math").log(z / Z0)
    iv = 1.0 / __import__("math").log(z / Z0)
    vm = cr * VB
    return (1 + 7 * iv) * 0.5 * RHO * vm ** 2 / 1000.0


def ec_forces(rule: str, width: float, levels: list[float], cpw=0.8, cpl=0.5) -> list[float]:
    """The two programs' Eurocode 1 wind as measured (27 Nov 2026): SAP2000 - pressure at the level elevation,
    leeward qp at z_min, over the tributary height with the lowest level down to the base; ETABS - windward and
    leeward qp at each height z, integrated over the height, storeys split to the levels as simple spans.
    (EN 1991-1-4 7.2.2 would take the leeward qp at z_e = h.)"""
    if rule == "sap":
        p = lambda z: (qp(z) * cpw + qp(ZMIN) * cpl) * width         # noqa: E731
        out = []
        for i, z in enumerate(levels):
            below = z if i == 0 else (z - levels[i - 1]) / 2
            above = (levels[i + 1] - z) / 2 if i + 1 < len(levels) else 0.0
            out.append(p(z) * (below + above))
        return out
    p = lambda z: qp(z) * (cpw + cpl) * width                         # noqa: E731
    zs = [0.0] + list(levels)
    F = [0.0] * len(zs)
    n = 4000
    for i in range(len(zs) - 1):
        a, b = zs[i], zs[i + 1]
        d = (b - a) / n
        for k in range(n):
            z = a + (k + 0.5) * d
            f = p(z) * d
            F[i + 1] += f * (z - a) / (b - a)
            F[i] += f * (b - z) / (b - a)
    return F[1:]


def raw_table(sap, key, pats):
    def rd():
        sap.SetPresentUnits(6)
        try:
            sap.DatabaseTables.SetLoadPatternsSelectedForDisplay(pats)
        except Exception:  # noqa: BLE001
            pass
        o = sap.DatabaseTables.GetTableForDisplayArray(key, [], "All", 0, [], 0, [])
        fl, n, data = [str(x) for x in o[2]], int(o[3]), [str(x) for x in o[4]]
        return [dict(zip(fl, data[i * len(fl):(i + 1) * len(fl)])) for i in range(n)]
    try:
        rows = com(rd)
    except Exception as exc:  # noqa: BLE001
        print(f"  TABLE {key}: EXC {exc}", flush=True)
        return []
    print(f"  TABLE {key}:", flush=True)
    for r in rows:
        print("    ", r, flush=True)
    return rows


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def run(sw: str) -> None:
    import live_wind as lw                       # the same building (spec, diaphragms)
    S = {"software": sw}
    nm = {**S, "units": U, "file_path": str(OUT / f"auto_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw == "ETABS":
        nm.update(kind="grid_only", stories=2, story_height=3.5, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=300)
    if describe_only:
        describe(S, sw)
        return
    sp = lw.spec(sw)
    sp["load_patterns"] = [{"name": "DL", "type": "dead", "self_weight_multiplier": 1.0},
                           {"name": "WEU", "type": "wind", "self_weight_multiplier": 0.0},
                           {"name": "EEU", "type": "quake", "self_weight_multiplier": 0.0}]
    slabs = sp.pop("_slabs")
    b = show("frame model from spec", "build_model_from_spec", {**S, "spec": sp}, limit=300)
    check(f"{sw}: spec built", isinstance(b, dict) and b.get("completed") is True, b)
    show("slabs", "create_areas", {**S, "units": U, "section": "SLAB200",
                                   "areas": [{k: v for k, v in s_.items() if k != "level"} for s_ in slabs]}, limit=200)
    for lvl in (1, 2):
        show(f"diaphragm DF{lvl}", "define_diaphragm",
             {**S, "name": f"DF{lvl}", "areas": [s_["name"] for s_ in slabs if s_["level"] == lvl]}, limit=300)
    if sw == "SAP2000":
        wp = {"ExposureFrom": 1, "DirAngle": 0, "Cpw": 0.8, "Cpl": 0.5, "WindSpeed": VB, "Terrain": 2,
              "Orography": 1.0, "K1": 1.0, "CsCd": 1.0, "Rho": RHO, "UserExposure": False}
        qpar = {"DirFlag": 1, "Eccen": 0.05, "PeriodFlag": 3, "Ct": 0.075, "UserT": T_USER, "EURO2004Country": 1,
                "EURO2004SpectrumType": 1, "EURO2004GroundType": 2, "EURO2004Ag": AG, "EURO2004S": S_B,
                "EURO2004Tb": TB, "EURO2004Tc": TC, "EURO2004Td": TD, "EURO2004Beta": 0.2, "EURO2004Q": Q,
                "EURO2004Lambda": 1.0}
    else:
        wp = {"Exposure": "Diaphragms", "CPWindward": 0.8, "CPLeeward": 0.5, "WindSpeed": VB, "Terrain": "II",
              "Orography": 1.0, "K1": 1.0, "CsCd": 1.0, "Rho": RHO, "Angle": 0}
        qpar = {"XDir": True, "XDirPlusE": False, "XDirMinusE": False, "YDir": False, "YDirPlusE": False,
                "YDirMinusE": False, "EccRatio": 0.05, "PeriodType": "User Defined", "UserT": T_USER,
                "Country": "CEN Default", "Ag": AG, "SpecType": 1, "GroundType": "B", "Beta": 0.2, "q": Q,
                "Lambda": 1.0}
    w = show("WEU: Eurocode 1 wind", "set_auto_load", {**S, "units": U, "load_pattern": "WEU", "kind": "wind",
                                                       "code": "Eurocode 1 2005", "parameters": wp}, limit=4000)
    check(f"{sw}: Eurocode 1 wind set and read back", isinstance(w, dict) and not w.get("_error"), w)
    e = show("EEU: Eurocode 8 seismic", "set_auto_load", {**S, "units": U, "load_pattern": "EEU", "kind": "seismic",
                                                          "code": "Eurocode 8 2004", "parameters": qpar}, limit=4000)
    check(f"{sw}: Eurocode 8 seismic set and read back", isinstance(e, dict) and not e.get("_error"), e)
    show("missing parameter refused", "set_auto_load",
         {**S, "load_pattern": "WEU", "kind": "wind", "code": "Eurocode 1 2005", "parameters": {"WindSpeed": 26},
          "dry_run": True}, expect_error=(sw == "SAP2000"), limit=1500)
    show("unknown parameter refused", "set_auto_load",
         {**S, "load_pattern": "WEU", "kind": "wind", "code": "Eurocode 1 2005",
          "parameters": {**wp, "NoSuchField": 1}, "dry_run": True}, expect_error=True, limit=1500)
    show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
    show("analysis", "run_analysis", {**S}, limit=500)
    sap = bridge.sap_model(sw)
    if sw == "SAP2000":
        ex = raw_table(sap, "Auto Wind Exposure For Horizontal Diaphragms", ["WEU"])
        raw_table(sap, "Auto Wind - Eurocode1 2005", ["WEU"])
        sq = raw_table(sap, "Auto Seismic - Eurocode8 2004", ["EEU"])
        width = max([num(r.get("TribWidth")) for r in ex if r.get("LoadPat") == "WEU"] or [12.0])
    else:
        width = max([num(x.get("Width")) for x in (w.get("read_back", {}) if isinstance(w, dict) else {})
                     .get("exposure_widths", [])] or [12.0])
        sq = []
    br = show("base reactions", "get_results",
              {**S, "units": U, "result_type": "base_reactions", "load_cases_or_combos": ["WEU", "EEU"]}, limit=600)
    recs = br.get("rows", []) if isinstance(br, dict) else []

    def pick(case, f):
        return next((num(r.get(f)) for r in recs if (r.get("LoadCase") or r.get("OutputCase")) == case), None)
    fx, my = pick("WEU", "FX"), pick("WEU", "MY")
    hand = ec_forces("sap" if sw == "SAP2000" else "etabs", width, [3.5, 7.0])
    if fx is not None and my is not None:
        f2 = (abs(my) - 3.5 * abs(fx)) / 3.5
        lv = [abs(fx) - f2, f2]
        print(f"  WEU by level: program {[round(x, 3) for x in lv]}, hand {[round(x, 3) for x in hand]} "
              f"(width {width}; qp(3.5) {qp(3.5):.4f}, qp(7) {qp(7.0):.4f} kN/m2)", flush=True)
        check(f"{sw}: Eurocode 1 story forces = hand (the program's measured rule) within 0.1 %",
              all(abs(g - h) <= 0.001 * h for g, h in zip(lv, hand)), {"program": lv, "hand": hand})
    sd = AG * S_B * 2.5 / Q
    eq = pick("EEU", "FX")
    if sw == "SAP2000":
        r = next((x for x in sq if x.get("LoadPat") == "EEU"), {})
        W, V, coeff = num(r.get("WeightUsed")), num(r.get("BaseShear")), num(r.get("CoeffUsed"))
        check("SAP2000: EC8 table shows CEN Default, ground B, S 1.2 / Tb 0.15 / Tc 0.5",
              str(r.get("Country", "")).startswith("CEN") and r.get("GroundType") == "B"
              and abs(num(r.get("SoilFact")) - 1.2) < 1e-6 and abs(num(r.get("Tc")) - 0.5) < 1e-6, r)
    else:
        mt = show("mass by story", "query_database_table", {**S, "units": U, "table_key": "Mass Summary by Story",
                                                           "max_rows": 20}, limit=1500)
        mrows = mt.get("records", []) if isinstance(mt, dict) else []
        W = sum(num(x.get("UX")) for x in mrows if x.get("Story") != "Base") * 9.80665
        V, coeff = abs(eq or 0), None
    print(f"  EEU: W {W:.3f} kN, base shear (table) {V:.3f}, coefficient (table) {coeff}, base reaction FX {eq}; "
          f"hand Sd/g = {sd:.6f} -> V = {sd * W:.3f}", flush=True)
    check(f"{sw}: Eurocode 8 base shear = Sd(T) W (hand) within 0.5 %",
          eq is not None and W > 0 and abs(abs(eq) - sd * W) <= 0.005 * sd * W, {"FX": eq, "W": W, "Sd": sd})


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
for sw in targets:
    print(f"\n################ {sw} (new instance, visible={visible})", flush=True)
    t0 = len(FAILED)
    try:
        com(bridge.launch_new, sw, visible=visible)
        run(sw)
    except Exception as exc:  # noqa: BLE001
        FAILED.append(f"{sw}: {type(exc).__name__}: {exc}")
        print("EXCEPTION", exc, flush=True)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("close", exc, flush=True)
    print(f"{sw}: {'OK' if len(FAILED) == t0 else 'FAILED'}", flush=True)
sys.exit(finish())
