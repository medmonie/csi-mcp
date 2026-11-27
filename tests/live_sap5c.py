r"""Phase 5 SAP2000 analysis features (0.19.0): staged construction, frame hinges with a pushover, moving loads -
through the actions in a NEW SAP2000, each against a hand calculation.

    run_live_sap5c.bat [--hidden]        Output: tests\results\live_sap5c.txt, model in sap5c_output\

Staged: two equal spans L = 8 m (A pin, B and C rollers). Stage 1 adds span 1 with its 10 kN/m; stage 2 adds span 2
with its 10 kN/m. Span 1 was simply supported when loaded, so the moment at B comes from span 2 only:
M_B = w L^2 / 16 = 40 kN m (one-shot linear analysis: w L^2 / 8 = 80 kN m).
Pushover: steel cantilever 3 m (I 300 x 150 x 10.7 / 7.1), ASCE 41-17 steel beam M3 hinge at the base, pushed to
0.15 m: the base shear levels off at Mp / L = Z Fye / L (the hinge's yield) - the largest base shear is between 1.0
and 1.1 of it, and the hinge is read back.
Moving load: H20-44 truck (8 + 32 kip, 14 ft) on a 20 m simple span lane: the largest midspan-region moment equals the
influence-line maximum within 1 %.
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "sap5c_output"
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


def frame(name, a, b, sec):
    r = show(f"frame {name}", "create_frames", {**S, "units": U, "section": sec,
                                                "frames": [{"start": a, "end": b, "name": name}]}, limit=400)
    return r["created"][0]["points"] if isinstance(r, dict) and r.get("created") else []


def restrain(pt, r):
    show(f"restraint {pt}", "assign_point_properties", {**S, "point_names": [pt], "restraint": r}, limit=200)


def h20_max_moment(L, x, p1, p2, s):
    """Largest moment at x of a simple span from two axles p1 (front) and p2 at spacing s, either direction."""
    best = 0.0
    n = 4000
    for i in range(-n, 2 * n + 1):
        a = L * i / n
        for loads in (((a, p1), (a + s, p2)), ((a, p2), (a + s, p1))):
            m = 0.0
            for pos, pw in loads:
                if 0 <= pos <= L:
                    m += pw * (pos * (L - x) / L if pos <= x else x * (L - pos) / L)
            best = max(best, m)
    return best


def run():
    show("new model", "new_model", {**S, "units": U, "file_path": str(OUT / "sap5c.sdb"), "discard_open_model": True},
         limit=300)
    sap = bridge.sap_model(SW)
    show("concrete", "define_material", {**S, "units": U, "name": "C30", "type": "concrete", "E": 2.5e7,
                                         "poisson": 0.2, "unit_weight": 25.0, "fc": 30000.0}, limit=300)
    show("steel", "define_material", {**S, "units": U, "name": "ST", "type": "steel", "E": 2.0e8, "poisson": 0.3,
                                      "unit_weight": 0.0, "fy": 355000.0, "fu": 510000.0}, limit=300)
    show("beam section", "define_frame_section", {**S, "units": U, "name": "B", "shape": "rectangle",
                                                  "material": "C30", "depth": 0.6, "width": 0.3}, limit=300)
    d, b, tf, tw = 0.3, 0.15, 0.0107, 0.0071
    show("I section", "define_frame_section", {**S, "units": U, "name": "I300", "shape": "i_section", "material": "ST",
                                               "depth": d, "width": b, "flange_thickness": tf, "web_thickness": tw},
         limit=300)
    for name in ("W1", "W2", "PUSH", "MOVE"):
        show(f"pattern {name}", "define_load_pattern", {**S, "name": name, "type": "other",
                                                        "self_weight_multiplier": 0}, limit=200)
    # ---- staged construction
    L, w = 8.0, 10.0
    p1 = frame("S1", [0, 0, 0], [L, 0, 0], "B")
    p2 = frame("S2", [L, 0, 0], [2 * L, 0, 0], "B")
    restrain(p1[0], [True, True, True, True, False, False])
    restrain(p1[1], [False, True, True, False, False, False])
    restrain(p2[1], [False, True, True, False, False, False])
    for pat, fr in (("W1", "S1"), ("W2", "S2")):
        show(f"{pat} load", "assign_point_or_frame_load", {**S, "units": U, "target_type": "frame",
                                                           "object_names": [fr], "load_pattern": pat, "values": [w]},
             limit=200)
    for g, fr, pts in (("G1", "S1", p1), ("G2", "S2", [p2[1]])):
        show(f"group {g}", "set_group", {**S, "operation": "define", "name": g}, limit=200)
        show(f"group {g} add", "set_group", {**S, "operation": "add", "name": g, "frames": [fr], "points": pts},
             limit=200)
    sc = show("staged case", "define_staged_construction", {**S, "name": "STAGED", "geometric_nonlinearity": "none",
                                                            "stages": [
        {"duration": 0, "operations": [{"operation": "add", "group": "G1"},
                                       {"operation": "load", "group": "G1", "load_name": "W1", "factor": 1}]},
        {"duration": 0, "operations": [{"operation": "add", "group": "G2"},
                                       {"operation": "load", "group": "G2", "load_name": "W2", "factor": 1}]}]},
        limit=2500)
    check("staged case read back (2 stages, 2 operations each)", isinstance(sc, dict)
          and [len(s["operations"]) for s in sc.get("read_back", [])] == [2, 2], sc)
    show("one-shot case", "define_load_case", {**S, "name": "ONESHOT", "type": "linear_static",
                                               "loads": [{"name": "W1", "factor": 1}, {"name": "W2", "factor": 1}]},
         limit=300)
    # ---- hinge and pushover
    H = 3.0
    pc = frame("COL", [100, 0, 0], [100, 0, H], "I300")
    restrain(pc[0], [True] * 6)
    show("push load", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [pc[1]],
                                                     "load_pattern": "PUSH", "values": [1, 0, 0, 0, 0, 0]}, limit=200)
    ht = show("hinge tables", "describe_hinge_tables", {**S}, limit=1500)
    key = next((k for k in (ht.get("tables", []) if isinstance(ht, dict) else []) if "41-17" in k
                and "Steel Beam" in k), "Frame Hinge Assigns 19 - Auto ASCE 41-17 - Steel Beam")
    show("hinge fields", "describe_hinge_tables", {**S, "table": key}, limit=4000)
    show("hinge refused on concrete", "assign_frame_hinges", {**S, "frame_names": ["S1"], "table": key,
                                                              "relative_distances": [0.0], "values": {"DOF": "M3"}},
         expect_error=True, limit=400)
    hg = show("hinge", "assign_frame_hinges", {**S, "frame_names": ["COL"], "table": key,
                                               "relative_distances": [0.0], "values": {"DOF": "M3", "BeyondE": "Extrapolated"},
                                               "replace": True},
              limit=2500)
    check("hinge read back on COL at the base", isinstance(hg, dict) and hg.get("read_back", {}).get("COL")
          and all(h["relative_distance"] == 0 for h in hg["read_back"]["COL"]) and not hg.get("warning"), hg)
    po = show("pushover case", "define_load_case", {**S, "units": U, "name": "PO", "type": "nonlinear_static",
                                                    "loads": [{"name": "PUSH", "factor": 1}],
                                                    "load_control": "displacement", "monitor_point": pc[1],
                                                    "monitor_dof": "U1", "target_displacement": 0.15,
                                                    "save_steps": True, "min_saved_states": 20,
                                                    "max_saved_states": 200}, limit=800)
    check("pushover case read back (displacement control 0.15 at the top)", isinstance(po, dict)
          and po.get("load_application_read_back", {}).get("load_control") == 2
          and abs(po["load_application_read_back"]["displacement"] - 0.15) < 1e-9, po)
    # ---- moving load
    LM = 20.0
    pm = frame("MB", [200, 0, 0], [200 + LM, 0, 0], "B")
    restrain(pm[0], [True, True, True, True, False, False])
    restrain(pm[1], [False, True, True, False, False, False])
    show("MB stations", "assign_frame_properties", {**S, "frame_names": ["MB"],
                                                    "output_stations": {"max_segment": 0.5}}, limit=300)
    ln = show("lane", "define_lane", {**S, "units": U, "name": "LN1", "frames": ["MB"], "width": 3.0,
                                      "values": {"DiscAlong": 0.25}}, limit=1500)
    check("lane read back", isinstance(ln, dict) and len(ln.get("read_back", [])) == 1, ln)
    vh = show("vehicles", "define_vehicles", {**S, "vehicles": [
        {"name": "H20", "standard_type": "Hn-44", "scale_factor": 20, "dynamic_allowance": 0, "vehicle_class": "VC1"},
        {"name": "HS20", "standard_type": "HSn-44", "scale_factor": 20, "dynamic_allowance": 0}]}, limit=2000)
    got_cls = {(r.get("VehClass"), r.get("VehName")) for r in (vh.get("vehicle_classes_read_back", [])
                                                              if isinstance(vh, dict) else [])}
    check("vehicles read back in classes (H20 in VC1, HS20)", {("VC1", "H20"), ("HS20", "HS20")} <= got_cls, got_cls)
    show("vehicles again refused", "define_vehicles", {**S, "vehicles": [
        {"name": "X", "standard_type": "Hn-44", "scale_factor": 20, "dynamic_allowance": 0}]}, expect_error=True,
         limit=400)
    mc = show("moving case", "define_moving_load_case", {**S, "name": "MOV", "assignments": [
        {"vehicle_class": "VC1", "scale_factor": 1, "min_lanes_loaded": 1, "max_lanes_loaded": 1, "lanes": ["LN1"]}],
        "multilane_factors": [1.0]}, limit=1500)
    check("moving case read back", isinstance(mc, dict)
          and mc.get("read_back", {}).get("Case - Moving Load 1 - Lane Assignments"), mc)
    # ---- analysis
    show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
    show("analysis", "run_analysis", {**S}, limit=800)
    for case, hand in (("STAGED", w * L * L / 16), ("ONESHOT", w * L * L / 8)):
        r = show(f"S2 forces {case}", "get_results", {**S, "units": U, "result_type": "frame_forces", "names": ["S2"],
                                                      "load_cases_or_combos": [case], "step_option": "last_step"},
                 limit=1500)
        mb = [num(x.get("M3")) for x in rows(r) if abs(num(x.get("Station"))) < 1e-6]
        print(f"  {case}: M at B {mb}, hand {hand}", flush=True)
        check(f"{case}: |M_B| = {hand:g} within 1 %", bool(mb) and abs(abs(mb[-1]) - hand) <= 0.01 * hand, mb)
    zx = b * tf * (d - tf) + tw * (d - 2 * tf) ** 2 / 4
    st = com(lambda: sap.PropMaterial.GetOSteel_1("ST", 0, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    fye = float(st[2])
    vp = zx * fye / H
    br = show("pushover base shear", "get_results", {**S, "units": U, "result_type": "base_reactions",
                                                     "load_cases_or_combos": ["PO"], "step_option": "step_by_step",
                                                     "max_rows": 1000}, limit=1500)
    dr = show("pushover top displacement", "get_results", {**S, "units": U, "result_type": "joint_displacements",
                                                           "names": [pc[1]], "load_cases_or_combos": ["PO"],
                                                           "step_option": "step_by_step", "max_rows": 1000}, limit=1500)
    vs = {num(x.get("StepNum")): abs(num(x.get("FX"))) for x in rows(br)}
    ds = {num(x.get("StepNum")): abs(num(x.get("U1"))) for x in rows(dr)}
    curve = sorted((ds[k], vs[k]) for k in vs if k in ds)
    print(f"  Z {zx:.6e} m3, Fye {fye} kPa, Mp/L {vp:.3f} kN; pushover curve {[(round(a, 4), round(b, 2)) for a, b in curve]}",
          flush=True)
    vy = None
    if len(curve) > 4:
        k1 = next(v / d for d, v in curve if d > 0)
        (d2, v2), (d3, v3) = curve[-2], curve[-1]
        k2 = (v3 - v2) / (d3 - d2)
        dy = (v3 - k2 * d3) / (k1 - k2)            # where the elastic line meets the post-yield line
        vy = k1 * dy
        print(f"  elastic {k1:.2f} kN/m, post-yield {k2:.3f} kN/m, bilinear yield {vy:.3f} kN", flush=True)
    gen = {}
    for gk in ("Hinges Gen 02 - Noninteracting - Deform Control - General",):
        try:
            keys, data, _ = com(bridge.read_table, sap, SW, gk)
            gen = next((dict(zip(keys, r)) for r in data if dict(zip(keys, r)).get("HingeName") == "COLH1"), {})
        except Exception as exc:  # noqa: BLE001
            print("  generated hinge table:", exc, flush=True)
    print("  generated hinge COLH1:", gen, flush=True)
    my = num(gen.get("MRPosMoSF"))
    fd = []
    try:
        keys, data, _ = com(bridge.read_table, sap, SW, "Hinges Gen 03 - Noninteracting - Deform Control - Force-Deform")
        fd = [dict(zip(keys, r)) for r in data if dict(zip(keys, r)).get("HingeName") == "COLH1"]
    except Exception as exc:  # noqa: BLE001
        print("  hinge backbone table:", exc, flush=True)
    print("  generated hinge COLH1 backbone:", [(r.get("FDPoint"), r.get("Force"), r.get("Displ")) for r in fd],
          flush=True)
    fb = next((num(r.get("Force")) for r in fd if str(r.get("FDPoint")).strip() == "B"), 0.0)
    print(f"  hinge yield scale {my} kN m (Z Fye = {zx * fye:.5f}); point B force {fb}; pushover yield x H = "
          f"{vy * H if vy else None}", flush=True)
    check("generated hinge yield scale = Z Fye within 0.1 %", abs(my - zx * fye) <= 1e-3 * zx * fye,
          {"MRPosMoSF": my, "Z_Fye": zx * fye})
    ad = max((h.get("actual_distance", 0.0) for h in (hg.get("read_back", {}).get("COL", [])
                                                      if isinstance(hg, dict) else [])), default=0.0)
    print(f"  hinge at {ad} m from the base (middle of its hinge length)", flush=True)
    check("pushover: yield base shear x (H - hinge position) = hinge scale x point B within 1 %",
          vy is not None and my > 0 and fb > 0 and abs(vy * (H - ad) - my * fb) <= 0.01 * my * fb,
          {"Vy(H-a)": vy * (H - ad) if vy else None, "My_B": my * fb, "a": ad})
    mr = show("moving MB forces", "get_results", {**S, "units": U, "result_type": "frame_forces", "names": ["MB"],
                                                  "load_cases_or_combos": ["MOV"]}, limit=2000)
    k = 4.4482216152605
    hand = max(h20_max_moment(LM, x, 8 * k, 32 * k, 14 * 0.3048) for x in [i * 0.5 for i in range(41)])
    g = mr.get("governing", {}).get("M3", {}) if isinstance(mr, dict) else {}
    got = abs(num(g.get("abs_max")))
    print(f"  moving: program max |M3| {got}, hand {hand:.3f}", flush=True)
    check("moving load: max M3 = influence-line maximum within 1 %", abs(got - hand) <= 0.01 * hand,
          {"program": got, "hand": hand})


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
