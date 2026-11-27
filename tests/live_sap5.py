r"""Phase 5 SAP2000 objects (0.19.0): cables, solids, tendon objects - through the actions in a NEW SAP2000, each
against a hand calculation.

    run_live_sap5.bat [--hidden]        Output: tests\results\live_sap5.txt, model in sap5_output\

Cable: 20 m span between pins, steel A = 0.001 m2 (w = 0.077 kN/m), shape by maximum sag 1.0 m, 20 segments;
nonlinear static self weight with large displacements: end horizontal reaction = catenary H (f = H/w (cosh(wL/2H) - 1))
within 1 %.
Solids: two 1 m cubes stacked, E = 2.5e7, free to expand sideways, 100 kN on the top face (25 kN per corner): top
settlement = P h / (E A) = 8e-6 m within 0.1 %.
Tendon: 10 m simply supported beam, straight tendon 0.2 m below the beam axis, 1000 kN, no friction or losses:
M3 = P e = 200 kN m and P = -1000 kN at midspan within 1 %.
"""
import math
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "sap5_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

visible = "--hidden" not in sys.argv
SW = "SAP2000"
S = {"software": SW}
U = "kN_m_C"


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def rows(r):
    return r.get("rows", []) if isinstance(r, dict) else []


def catenary_h(w, L, f):
    lo, hi = 1e-6, 1e6
    for _ in range(200):
        h = math.sqrt(lo * hi)
        sag = h / w * (math.cosh(w * L / (2 * h)) - 1)
        lo, hi = (h, hi) if sag > f else (lo, h)
    return math.sqrt(lo * hi)


def run():
    show("new model", "new_model", {**S, "units": U, "file_path": str(OUT / "sap5.sdb"), "discard_open_model": True},
         limit=300)
    show("steel", "define_material", {**S, "units": U, "name": "ST", "type": "steel", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 77.0, "fy": 355000.0, "fu": 510000.0}, limit=300)
    show("concrete", "define_material", {**S, "units": U, "name": "C30", "type": "concrete", "E": 2.5e7,
                                         "poisson": 0.2, "unit_weight": 0.0, "fc": 30000.0}, limit=300)
    show("tendon material", "define_material", {**S, "units": U, "name": "T1860", "type": "tendon", "E": 1.95e8,
                                                "poisson": 0.3, "unit_weight": 0.0, "fy": 1.674e6, "fu": 1.86e6},
         limit=300)
    for name in ("SW", "PSOL", "PT"):
        show(f"pattern {name}", "define_load_pattern", {**S, "name": name, "type": "dead" if name == "SW" else "other",
                                                        "self_weight_multiplier": 1 if name == "SW" else 0}, limit=300)
    # ---- cable
    L, f, A, gam = 20.0, 1.0, 0.001, 77.0
    w = gam * A
    show("cable property", "define_cable_property", {**S, "units": U, "name": "CB", "material": "ST", "area": A},
         limit=300)
    cb = show("cable", "create_cables", {**S, "units": U, "cables": [{"start": [0, 0, 10], "end": [L, 0, 10],
                                                                     "name": "C1"}],
                                         "cable_property": "CB", "shape": "max_sag", "value": f, "segments": 20},
              limit=2000)
    ends = cb["created"][0]["points"] if isinstance(cb, dict) and cb.get("created") else []
    check("cable created, program shape SagMax = 1", isinstance(cb, dict) and not cb.get("warning")
          and abs(num(cb.get("created", [{}])[0].get("program_shape", {}).get("max_sag")) - f) < 1e-4, cb)
    show("cable pins", "assign_point_properties", {**S, "point_names": ends,
                                                   "restraint": [True, True, True, False, False, False]}, limit=300)
    show("NL self weight", "define_load_case", {**S, "name": "NLSW", "type": "nonlinear_static",
                                                "loads": [{"name": "SW", "factor": 1.0}],
                                                "geometric_nonlinearity": "p_delta_large_displacements"}, limit=600)
    # ---- solids
    sp = show("solid property", "define_solid_property", {**S, "name": "SOL", "material": "C30"}, limit=300)
    check("solid property read back", isinstance(sp, dict) and sp.get("read_back", {}).get("material") == "C30", sp)

    def cube(z):
        return [[50, 0, z], [51, 0, z], [50, 1, z], [51, 1, z], [50, 0, z + 1], [51, 0, z + 1], [50, 1, z + 1],
                [51, 1, z + 1]]
    so = show("solids", "create_solids", {**S, "units": U, "solid_property": "SOL",
                                          "solids": [{"points": cube(0), "name": "S1"},
                                                     {"points": cube(1), "name": "S2"}]}, limit=2000)
    cr = so.get("created", []) if isinstance(so, dict) else []
    check("solids created, volume 1 each", len(cr) == 2 and all(abs(num(c.get("volume")) - 1) < 1e-9 for c in cr)
          and not so.get("warning"), so)
    if len(cr) == 2:
        base, top = cr[0]["points"][:4], cr[1]["points"][4:]
        for pt, r in zip(base, ([1, 1, 1], [0, 1, 1], [1, 0, 1], [0, 0, 1])):
            show(f"solid support {pt}", "assign_point_properties",
                 {**S, "point_names": [pt], "restraint": [bool(v) for v in r] + [False] * 3}, limit=200)
        show("solid top loads", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point",
                                                               "object_names": top, "load_pattern": "PSOL",
                                                               "values": [0, 0, -25, 0, 0, 0]}, limit=300)
    # ---- tendon in a beam
    show("beam section", "define_frame_section", {**S, "units": U, "name": "B400", "shape": "rectangle",
                                                  "material": "C30", "depth": 0.8, "width": 0.4}, limit=300)
    fr = show("beam", "create_frames", {**S, "units": U, "section": "B400", "frames": [
        {"start": [100, 0, 0], "end": [110, 0, 0], "name": "BM"}]}, limit=500)
    bp = fr["created"][0]["points"] if isinstance(fr, dict) and fr.get("created") else []
    if bp:
        show("beam pin", "assign_point_properties", {**S, "point_names": [bp[0]],
                                                     "restraint": [True, True, True, True, False, False]}, limit=200)
        show("beam roller", "assign_point_properties", {**S, "point_names": [bp[1]],
                                                        "restraint": [False, True, True, False, False, False]},
             limit=200)
    show("beam stations", "assign_frame_properties", {**S, "frame_names": ["BM"],
                                                      "output_stations": {"min_sections": 10}}, limit=300)
    tp = show("tendon property", "define_tendon_property", {**S, "units": U, "name": "TP", "material": "T1860",
                                                           "strand_area": 1.0e-4, "modelling": "loads"}, limit=300)
    check("tendon property (loads) read back", isinstance(tp, dict)
          and tp.get("read_back", {}).get("modelling") == "loads", tp)
    P, e = 1000.0, 0.2
    tn = show("tendon", "create_tendon_objects", {**S, "units": U, "tendons": [
        {"points": [[100, 0, -e], [105, 0, -e], [110, 0, -e]], "name": "T1"}], "tendon_property": "TP",
        "load_pattern": "PT", "force_or_stress": "force", "jacking": P, "jack_from": "start",
        "curvature_coefficient": 0, "wobble_coefficient": 0, "anchorage_set_slip": 0, "loss_elastic_shortening": 0,
        "loss_creep": 0, "loss_shrinkage": 0, "loss_relaxation": 0}, limit=2000)
    check("tendon created and read back", isinstance(tn, dict) and tn.get("created")
          and len(tn["created"][0].get("path", [])) == 3, tn)
    # ---- analysis and checks
    show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
    show("analysis", "run_analysis", {**S}, limit=800)
    rr = show("cable reactions", "get_results", {**S, "units": U, "result_type": "joint_reactions", "names": ends,
                                                 "load_cases_or_combos": ["NLSW"]}, limit=1500)
    hx = max((abs(num(r.get("F1"))) for r in rows(rr)), default=0.0)
    hh = catenary_h(w, L, f)
    print(f"  cable: w {w} kN/m, catenary H {hh:.5f} kN (parabola {w * L * L / (8 * f):.5f})", flush=True)
    check("cable horizontal reaction = catenary H within 1 %", abs(hx - hh) <= 0.01 * hh, {"F1": hx, "hand": hh})
    if len(cr) == 2:
        d = show("solid top displacements", "get_results", {**S, "units": U, "result_type": "joint_displacements",
                                                            "names": cr[1]["points"][4:],
                                                            "load_cases_or_combos": ["PSOL"]}, limit=1500)
        u3 = [num(r.get("U3")) for r in rows(d)]
        hand = -100.0 * 2 / (2.5e7 * 1.0)
        check("solid top settlement = P h / (E A) within 0.1 %", len(u3) == 4
              and all(abs(v - hand) <= 1e-3 * abs(hand) for v in u3), {"U3": u3, "hand": hand})
    ff = show("beam forces PT", "get_results", {**S, "units": U, "result_type": "frame_forces", "names": ["BM"],
                                                "load_cases_or_combos": ["PT"]}, limit=3000)
    mid = min(rows(ff), key=lambda r: abs(num(r.get("Station")) - 5.0)) if rows(ff) else {}
    print("  beam midspan:", mid, flush=True)
    check("tendon: |M3| = P e within 1 %", abs(abs(num(mid.get("M3"))) - P * e) <= 0.01 * P * e, mid)
    check("tendon: P = -P within 1 %", abs(num(mid.get("P")) + P) <= 0.01 * P, mid)


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
print(f"################ {SW} (new instance, visible={visible})", flush=True)
try:
    com(bridge.launch_new, SW, visible=visible)
    run()
except Exception as exc:  # noqa: BLE001
    FAILED.append(f"{type(exc).__name__}: {exc}")
    print("EXCEPTION", exc, flush=True)
finally:
    try:
        print(com(bridge.disconnect, SW, True, False), flush=True)
    except Exception as exc:  # noqa: BLE001
        print("close", exc, flush=True)
sys.exit(finish())
