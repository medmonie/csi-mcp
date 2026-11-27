r"""Phase 5 floors (0.18.0): design strips, PT tendons, tendon property (SAFE, ETABS), towers (ETABS), through the
actions in NEW instances.

    run_live_slabs.bat [SAFE] [ETABS] [--hidden]      Output: tests\results\live_slabs.txt, models in slabs_output\

SAFE: a one-way slab 10 m x 1 m, 0.25 m thick, simply supported along x = 0 and x = 10 (a pin at one corner, rollers at the
others: free to shorten). One parabolic tendon along y = 0.5: ends at mid-depth (-0.125 m), low point -0.21 m at midspan (drape
e = 0.085 m); 4 strands of 98.7 mm2 jacked to 1400 MPa from the start, fixed losses 0 -> P = 552.72 kN. A strip
along the tendon (0.5 m each side, layer A, column strip). Checks: tendons and strips read back; after the analysis
the strip axial force = -P and the strip moment, which SAFE gives about the slab top (measured): P t/2 = 69.09 kN m
at the ends and, moved to mid-depth, P e = 46.98 kN m at midspan (no secondary moment), all within 2 %.
ETABS: towers (allow multiple, add T2 with 2 stories, rename, set active, list), a strip and a tendon on a story slab
(read back).
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "slabs_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAFE", "ETABS"]
visible = "--hidden" not in sys.argv
EXT = {"ETABS": ".edb", "SAFE": ".fdb"}
U = "kN_m_C"
L, T, E_DRAPE = 10.0, 0.25, 0.085
SIG, A_STRAND, N = 1.4e6, 98.7e-6, 4
P = SIG * A_STRAND * N


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def common(S: dict, sw: str, z: float) -> None:
    show("concrete", "define_material", {**S, "units": U, "name": "C35", "type": "concrete", "E": 2.8e7,
                                         "poisson": 0.2, "unit_weight": 25.0, "fc": 35000.0}, limit=300)
    show("tendon material", "define_material", {**S, "units": U, "name": "T1860", "type": "tendon", "E": 1.95e8,
                                                "poisson": 0.3, "unit_weight": 77.0, "fy": 1.674e6, "fu": 1.86e6},
         limit=300)
    tp = show("tendon property", "define_tendon_property", {**S, "units": U, "name": "TP15", "material": "T1860",
                                                           "strand_area": A_STRAND}, limit=300)
    check(f"{sw}: tendon property read back", isinstance(tp, dict) and not tp.get("_error")
          and abs(tp.get("read_back", {}).get("strand_area", 0) - A_STRAND) < 1e-12, tp)
    show("slab section", "define_area_section", {**S, "units": U, "name": "SL250", "kind": "slab", "material": "C35",
                                                 "thickness": T}, limit=300)
    for name, typ in (("PTT", "prestresstransfer"), ("PTF", "prestressfinal")):
        r = show(f"pattern {name}", "define_load_pattern", {**S, "name": name, "type": typ,
                                                            "self_weight_multiplier": 0}, limit=600)
        check(f"{sw}: pattern {name} type {typ}", isinstance(r, dict) and not r.get("_error")
              and str(r.get("read_back", {}).get("type", "")).lower() == typ, r)


def run(sw: str) -> None:
    S = {"software": sw}
    nm = {**S, "units": U, "file_path": str(OUT / f"slabs_{sw}{EXT[sw]}"), "discard_open_model": True,
          "kind": "grid_only", "stories": 1 if sw == "SAFE" else 2, "story_height": 3.0, "spacing_x": 5.0,
          "spacing_y": 1.0}
    show("new model", "new_model", nm, limit=300)
    sap = bridge.sap_model(sw)
    z = float(com(lambda: sap.Story.GetStories_2()[0])) if sw == "SAFE" else 3.0
    print("  slab elevation", z, flush=True)
    common(S, sw, z)
    st = {} if sw == "SAFE" else {"story": "Story1"}
    ar = show("slab", "create_areas", {**S, "units": U, "section": "SL250", "areas": [{"points": [
        [0, 0, z], [L, 0, z], [L, 1, z], [0, 1, z]]}]}, limit=400)
    corners = ar["created"][0]["points"] if isinstance(ar, dict) and ar.get("created") else []
    # statically determinate in plan: pin at (0,0), rollers elsewhere, so the slab is free to shorten (P stays in
    # the slab) and simply supported along x = 0 and x = L
    for pt, r in zip(corners, ([1, 1, 1], [0, 1, 1], [0, 0, 1], [1, 0, 1])):
        show(f"support {pt}", "assign_point_properties", {**S, "point_names": [pt],
                                                          "restraint": [bool(v) for v in r] + [False] * 3}, limit=300)
    tn = show("tendon", "create_tendons", {**S, "units": U, **st, "tendons": [
        {"name": "T1", "start": [0, 0.5], "end": [L, 0.5],
         "span": {"profile": "parabola", "z_left": -T / 2, "z_low": -(T / 2 + E_DRAPE), "z_right": -T / 2,
                  "low_point": 0.5}}],
        "tendon_property": "TP15", "strands": N, "bonded": False, "jacking_stress": SIG, "jack_from": "start",
        "transfer_pattern": "PTT", "final_pattern": "PTF", "losses": "fixed", "loss_at_stressing": 0,
        "loss_long_term": 0}, limit=3000)
    check(f"{sw}: tendon created and read back", isinstance(tn, dict) and not tn.get("_error")
          and tn.get("read_back", [{}])[0].get("strands") == str(N), tn)
    sp = show("strip", "create_strips", {**S, "units": U, **st, "strips": [
        {"name": "SA1", "start": [0, 0.5], "end": [L, 0.5], "width_left": 0.5, "width_right": 0.5, "layer": "A",
         "design_type": "column_strip"}]}, limit=2000)
    check(f"{sw}: strip created and read back", isinstance(sp, dict) and not sp.get("_error")
          and sp.get("read_back", [{}])[0].get("layer") == "A", sp)
    show("repeat refused", "create_strips", {**S, **st, "strips": [
        {"name": "SA1", "start": [0, 0.5], "end": [L, 0.5], "width_left": 0.5, "width_right": 0.5, "layer": "A"}]},
         expect_error=True, limit=400)
    if sw == "ETABS":
        for title, params in (("list", {}), ("allow", {}),
                              ("add", {"tower": "T2", "stories": 2, "typical_story_height": 3.0,
                                       "bottom_story_height": 3.0}),
                              ("rename", {"tower": "T2", "new_name": "TowerB"}),
                              ("set_active", {"tower": "TowerB"}), ("list", {})):
            r = show(f"towers {title}", "manage_towers", {**S, "units": U, "operation": title, **params}, limit=800)
        towers = (r.get("towers") if isinstance(r, dict) else None) or []
        check("ETABS: towers read back (TowerB added, active)", "TowerB" in towers
              and r.get("active") == "TowerB", r)
        show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
        return
    show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
    show("analysis", "run_analysis", {**S}, limit=600)
    t = show("strip forces PTF", "query_database_table",
             {**S, "units": U, "table_key": "Strip Forces", "load_cases_or_combos": ["PTF"], "max_rows": 200},
             limit=1500)
    rows = t.get("records", []) if isinstance(t, dict) else []
    mid = min(rows, key=lambda r: abs(num(r.get("Station") or r.get("GlobalX")) - L / 2)) if rows else {}
    print(f"  strip forces rows {len(rows)}; nearest midspan: {mid}", flush=True)
    m3, pax = num(mid.get("M3")), num(mid.get("P"))
    end = min(rows, key=lambda r: num(r.get("Station") or r.get("GlobalX"))) if rows else {}
    # measured: SAFE gives the strip moment about the area object's plane (the slab top), so the tendon force acts
    # at its depth below the top; about mid-depth that is M3 + P t/2 (P < 0), the classic P e
    m_mid = abs(abs(m3) - abs(pax) * T / 2)
    print(f"  hand: P = {P:.3f} kN, P e = {P * E_DRAPE:.3f} kN m; about the top: mid {P * (T / 2 + E_DRAPE):.3f}, "
          f"ends {P * T / 2:.3f}; program: mid {m3}, end {end.get('M3')}", flush=True)
    check("SAFE: strip M3 at the ends (PTF) = P t/2 within 2 % (about the slab top)",
          abs(abs(num(end.get("M3"))) - P * T / 2) <= 0.02 * P * T / 2, end)
    check("SAFE: strip M3 at midspan (PTF), about mid-depth = P e within 2 %",
          abs(m_mid - P * E_DRAPE) <= 0.02 * P * E_DRAPE, {"M3": m3, "about_mid_depth": m_mid, "hand": P * E_DRAPE})
    check("SAFE: strip axial (PTF) = -P within 2 %", abs(abs(pax) - P) <= 0.02 * P and pax < 0, {"P": pax, "hand": -P})


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
