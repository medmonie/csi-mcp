r"""Phase 5 wind (0.16.0): automatic wind (ASCE 7-16) and user wind loads, through the actions, in NEW instances.

    run_live_wind.bat [SAP2000] [ETABS] [--hidden]      Output: tests\results\live_wind.txt, models in
                                                        tests\results\wind_output\

Model (both programs): 3 x 2 bays of 6 m (18 m in X, 12 m in Y), 2 storeys of 3.5 m, 12 slabs, columns 0.5 x 0.5,
beams 0.3 x 0.6; one diaphragm per floor (D1 at 3.5 m, D2 at 7 m - SAP2000: diaphragm constraints).
1 WX: ASCE 7-16, exposure from diaphragms, angle 0, Cpw 0.8, Cpl 0.5, case 1, V = 40 m/s (89.48 mph), exposure C,
  Kzt 1, G 0.85, Kd 0.85. Check: the story forces the program applies against a hand calculation
  (qz = 0.00256 Kz Kzt Kd Ke V^2 psf, Kz = 2.01 (max(z, 15 ft) / 900 ft)^(2 / 9.5), windward qz G Cpw + leeward
  qh G Cpl on the exposure width the program reports x the tributary height), and the base shear = -sum.
2 WY: user loads Fy = 30 kN on D1 and 20 kN on D2 at (9, 6). Check: base reaction FY = -50 kN.
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "wind_output"
if __name__ == "__main__":
    os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS"]
visible = "--hidden" not in sys.argv
EXT = {"SAP2000": ".sdb", "ETABS": ".edb"}
U = "kN_m_C"
XS, YS, ZS = [0.0, 6.0, 12.0, 18.0], [0.0, 6.0, 12.0], [0.0, 3.5, 7.0]
V_MS = 40.0
FT = 0.3048


def pid(x, y, z):
    return f"P{int(x)}_{int(y)}_{int(z * 10)}"


def spec(sw: str) -> dict:
    pts, frames, slabs = [], [], []
    for z in ZS:
        for x in XS:
            for y in YS:
                pts.append({"name": pid(x, y, z), "xyz": [x, y, z], **({"restraint": "fixed"} if z == 0 else {})})
    for i, z in enumerate(ZS[1:], 1):
        for x in XS:
            for y in YS:
                frames.append({"name": f"C{int(x)}_{int(y)}_{i}", "start": pid(x, y, ZS[i - 1]), "end": pid(x, y, z),
                               "section": "COL500"})
        for y in YS:
            for x0, x1 in zip(XS, XS[1:]):
                frames.append({"name": f"BX{int(x0)}_{int(y)}_{i}", "start": pid(x0, y, z), "end": pid(x1, y, z),
                               "section": "BM300x600"})
        for x in XS:
            for y0, y1 in zip(YS, YS[1:]):
                frames.append({"name": f"BY{int(x)}_{int(y0)}_{i}", "start": pid(x, y0, z), "end": pid(x, y1, z),
                               "section": "BM300x600"})
        for x0, x1 in zip(XS, XS[1:]):
            for y0, y1 in zip(YS, YS[1:]):
                slabs.append({"name": f"S{int(x0)}_{int(y0)}_{i}", "level": i,
                              "points": [pid(x0, y0, z), pid(x1, y0, z), pid(x1, y1, z), pid(x0, y1, z)]})
    return {"units": U,
            "materials": [{"name": "C30", "type": "concrete", "E": 2.6e7, "poisson": 0.2, "unit_weight": 25.0,
                           "thermal_coeff": 1e-5, "fc": 30000.0}],
            "frame_sections": [{"name": "COL500", "material": "C30", "shape": "rectangle", "depth": 0.5, "width": 0.5},
                               {"name": "BM300x600", "material": "C30", "shape": "rectangle", "depth": 0.6,
                                "width": 0.3}],
            "area_sections": [{"name": "SLAB200", "kind": "shell_thin" if sw == "SAP2000" else "slab",
                               "material": "C30", "thickness": 0.2}],
            "load_patterns": [{"name": "DL", "type": "dead", "self_weight_multiplier": 1.0},
                              {"name": "WX", "type": "wind", "self_weight_multiplier": 0.0},
                              {"name": "WY", "type": "wind", "self_weight_multiplier": 0.0}],
            "points": pts, "frames": frames, "_slabs": slabs}


def hand_forces(width: float, levels: list[float], h: float, cpw=0.8, cpl=0.5, G=0.85, Kd=0.85, Kzt=1.0, Ke=1.0):
    """ASCE 7-16 directional procedure, exposure C; kN per level, pressures at the level elevation over the
    tributary height (half the storey below and above; the roof half the storey below)."""
    v_mph = V_MS / 0.44704

    def qz(z):                       # kN/m2
        kz = 2.01 * (max(z / FT, 15.0) / 900.0) ** (2 / 9.5)
        return 0.00256 * kz * Kzt * Kd * Ke * v_mph ** 2 * 0.04788026
    qh = qz(h)
    out = []
    for i, z in enumerate(levels):
        below = z - (levels[i - 1] if i else 0.0)
        above = (levels[i + 1] - z) if i + 1 < len(levels) else 0.0
        trib = below / 2 + above / 2
        out.append((qz(z) * G * cpw + qh * G * cpl) * width * trib)
    return out


def hand_etabs(width: float, levels: list[float], h: float, cpw=0.8, cpl=0.5, G=0.85, Kd=0.85, Kzt=1.0, Ke=1.0):
    """ETABS 23 (measured): the pressure (windward qz G Cpw, leeward qh G Cpl) integrated over the height and each
    storey's load given to the levels below and above as simple-span reactions; the part at the base goes to the
    support directly."""
    v_mph = V_MS / 0.44704

    def p(z):
        kz = 2.01 * (max(z / FT, 15.0) / 900.0) ** (2 / 9.5)
        kh = 2.01 * (max(h / FT, 15.0) / 900.0) ** (2 / 9.5)
        q = lambda k: 0.00256 * k * Kzt * Kd * Ke * v_mph ** 2 * 0.04788026   # noqa: E731
        return (q(kz) * cpw + q(kh) * cpl) * G * width
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


def rows_of(r):
    return (r.get("rows") or r.get("records") or []) if isinstance(r, dict) else []


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def run(sw: str) -> None:
    S = {"software": sw}
    nm = {**S, "units": U, "file_path": str(OUT / "models" / f"wind_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw == "ETABS":
        nm.update(kind="grid_only", stories=2, story_height=3.5, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=300)
    sp = spec(sw)
    slabs = sp.pop("_slabs")
    b = show("frame model from spec", "build_model_from_spec", {**S, "spec": sp}, limit=400)
    check(f"{sw}: spec built", isinstance(b, dict) and b.get("completed") is True, b)
    show("slabs", "create_areas", {**S, "units": U, "section": "SLAB200",
                                   "areas": [{k: v for k, v in s_.items() if k != "level"} for s_ in slabs]}, limit=300)
    for lvl in (1, 2):
        d = show(f"diaphragm DF{lvl}", "define_diaphragm",
                 {**S, "name": f"DF{lvl}", "areas": [s_["name"] for s_ in slabs if s_["level"] == lvl]}, limit=600)
        check(f"{sw}: diaphragm DF{lvl} defined", isinstance(d, dict) and not d.get("_error"), d)
    w = show("WX: ASCE 7-16", "set_auto_wind",
             {**S, "units": U, "load_pattern": "WX", "code": "ASCE 7-16", "exposure_from": "diaphragms", "angle": 0,
              "Cpw": 0.8, "Cpl": 0.5, "asce_case": "1", "wind_speed": V_MS, "wind_speed_unit": "m/s",
              "exposure_category": "C", "Kzt": 1.0, "gust_factor": 0.85, "Kd": 0.85}, limit=2500)
    check(f"{sw}: auto wind set and read back", isinstance(w, dict) and not w.get("_error") and
          w.get("read_back", {}).get("code") in ("ASCE7-16", "ASCE 7-16"), w)
    loads = [{"diaphragm": "DF1", "fy": 30.0, "x": 9.0, "y": 6.0}, {"diaphragm": "DF2", "fy": 20.0, "x": 9.0, "y": 6.0}]
    if sw == "ETABS":
        for ld, st in zip(loads, ("Story1", "Story2")):
            ld["story"] = st
    u = show("WY: user loads", "set_wind_user_loads", {**S, "units": U, "load_pattern": "WY", "loads": loads},
             limit=1500)
    check(f"{sw}: user wind loads read back", isinstance(u, dict) and not u.get("_error") and
          len(u.get("read_back", {}).get("loads", [])) == 2, u)
    show("dry run of a wrong pattern (DL is dead)", "set_auto_wind",
         {**S, "load_pattern": "DL", "code": "ASCE 7-16", "Cpw": 0.8, "Cpl": 0.5, "asce_case": "1",
          "wind_speed": 40, "exposure_category": "C", "Kzt": 1, "gust_factor": 0.85, "Kd": 0.85, "dry_run": True},
         expect_error=True)
    show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
    show("analysis", "run_analysis", {**S}, limit=500)

    sap = bridge.sap_model(sw)
    keys = (["Load Pattern Definitions - Auto Wind - Loads to Stories",
             "Load Pattern Definitions - Auto Wind - Loads to Diaphragms",
             "Load Pattern Definitions - Auto Wind - ASCE 7-16"] if sw == "ETABS" else
            ["Auto Wind Loads To Horizontal Diaphragms", "Auto Wind Exposure For Horizontal Diaphragms",
             "Auto Wind - ASCE 7-16"])
    widths = []
    for k in keys:
        def rd(k=k):
            sap.SetPresentUnits(6)
            try:
                sap.DatabaseTables.SetLoadPatternsSelectedForDisplay(["WX", "WY"])
            except Exception:  # noqa: BLE001
                pass
            o = sap.DatabaseTables.GetTableForDisplayArray(k, [], "All", 0, [], 0, [])
            fl, n, data = [str(x) for x in o[2]], int(o[3]), [str(x) for x in o[4]]
            return [dict(zip(fl, data[i * len(fl):(i + 1) * len(fl)])) for i in range(n)], o[-1]
        try:
            rws, ret = com(rd)
            print(f"  TABLE {k} (ret {ret}):", flush=True)
            for r in rws:
                print("    ", r, flush=True)
            for r in rws:
                if (r.get("LoadPat") or r.get("Name")) == "WX":
                    for f in ("TribWidth", "Width"):
                        if num(r.get(f)):
                            widths.append(num(r.get(f)))
        except Exception as exc:  # noqa: BLE001
            print(f"  TABLE {k}: EXC {exc}", flush=True)
    width = max(widths) if widths else 12.0
    hand_e = hand_forces(width, ZS[1:], ZS[-1])                    # half storeys above / below
    hand_s = [hand_e[0] * (ZS[1] + (ZS[2] - ZS[1]) / 2) / ((ZS[2] - ZS[0]) / 2)] + hand_e[1:]   # lowest level to base
    print(f"  exposure width {width} m; hand forces (half storeys): {[round(x, 3) for x in hand_e]}; "
          f"(lowest level down to the base): {[round(x, 3) for x in hand_s]}", flush=True)
    br = show("base reactions", "get_results",
              {**S, "units": U, "result_type": "base_reactions", "load_cases_or_combos": ["WX", "WY"]}, limit=2000)
    recs = rows_of(br)
    print("  base reaction rows:", recs, flush=True)

    def pick(case, f):
        return next((num(r.get(f)) for r in recs if (r.get("LoadCase") or r.get("OutputCase")) == case), None)
    bx, my, by = pick("WX", "FX"), pick("WX", "MY"), pick("WY", "FY")
    if bx is not None and my is not None:
        f2 = (abs(my) - ZS[1] * abs(bx)) / (ZS[2] - ZS[1])
        lv = [abs(bx) - f2, f2]
        print(f"  WX by level from the base reactions: {[round(x, 3) for x in lv]}", flush=True)
        hand = hand_s if sw == "SAP2000" else hand_etabs(width, ZS[1:], ZS[-1])
        rule = ("SAP2000: pressure at each level elevation x tributary height, the lowest level down to the base"
                if sw == "SAP2000" else "ETABS: pressure integrated over the height, storeys split to the levels "
                "below / above as simple spans")
        check(f"{sw}: WX story forces = hand ASCE 7-16 within 0.1 % ({rule})",
              all(abs(g - h_) <= 0.001 * h_ for g, h_ in zip(lv, hand)), {"program": lv, "hand": hand})
    else:
        check(f"{sw}: WX base reactions read", False, recs)
    check(f"{sw}: base shear WY = -50 kN", by is not None and abs(by + 50.0) < 0.05, {"FY": by})


def main() -> int:
    if OUT.exists():
        shutil.rmtree(OUT, ignore_errors=True)
    (OUT / "models").mkdir(parents=True, exist_ok=True)
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
    return finish()


if __name__ == "__main__":
    sys.exit(main())
