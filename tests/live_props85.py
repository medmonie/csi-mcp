r"""Live test for 0.23.0 (roadmap 8.5): layered shells, deck sections, stress-strain curves, time dependence.
Every check is against a hand value. Output: tests\results\live_props85.txt (ends RESULT=OK).

A. Layered shell (S E F): cantilever strip L 4 m, b 1 m, 8 shells, root fixed, tip load P = 10 kN, Poisson 0.
   - plain shell t 0.2, E 2.5e7: hand PL^3 / (3 E b t^3 / 12) = 0.0128 m
   - layered, two 0.1 m layers of the same material: hand 0.0128 m (+ transverse shear, 0.15 %)
   - layered, top layer E1 = 2.5e7, bottom E2 = 5e7: transformed section EI = 9.16667e-4 E1 -> 0.0093091 m;
     ratio to the uniform strip = 16666.7 / 22916.7 = 0.72727 (mesh error cancels)
B. Deck (E F): filled deck 3 x 3 m on weightless edge beams, corners pinned, self weight only:
   hand sum FZ = 9 m2 x [gamma_c (tc + hr (wrt + wrb) / 2 / sr) + deck unit weight]
C. Stress-strain (S E F): simple concrete = fc [2 e/e0 - (e/e0)^2] up to e0 (ETABS / SAFE start linear E e to
   0.2 fc); simple steel hardening f = Fye [1 + r e^(1-r) (Fue / Fye - 1)], r = (e - esh) / (eu - esh); user curve
   read back point by point.
D. Time dependence (S): CEB-FIP 90 parameters read back; not offered in ETABS / SAFE.
"""
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "props85_output"
import os  # noqa: E402
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}
U = "kN_m_C"
E1, E2, P, L, B, T = 2.5e7, 5.0e7, 10.0, 4.0, 1.0, 0.2


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def ok(r):
    return isinstance(r, dict) and not r.get("_error") and not isinstance(r, Exception)


def layer(name, d, mat):
    return {"name": name, "distance": d, "thickness": 0.1, "behaviour": "shell", "material": mat, "angle": 0.0,
            "integration_points": 2, "s11": "linear", "s22": "linear", "s12": "linear"}


def strip(sw, S, z, y0, section):
    areas = [{"points": [[0.5 * i, y0, z], [0.5 * (i + 1), y0, z], [0.5 * (i + 1), y0 + B, z],
                         [0.5 * i, y0 + B, z]]} for i in range(8)]
    r = show(f"strip {section}", "create_areas", {**S, "units": U, "section": section, "areas": areas}, limit=300)
    pts = sorted({p for a in (r.get("created", []) if ok(r) else []) for p in a["points"]})
    sap = bridge.sap_model(sw)
    xyz = {p: com(lambda p=p: sap.PointObj.GetCoordCartesian(p))[:3] for p in pts}
    root = [p for p, c in xyz.items() if abs(c[0]) < 1e-6]
    tip = [p for p, c in xyz.items() if abs(c[0] - L) < 1e-6]
    return root, tip


def part_a(sw, S, z):
    for name, e in (("CA", E1), ("CB", E2)):
        show(name, "define_material", {**S, "units": U, "name": name, "type": "concrete", "E": e, "poisson": 0.0,
                                       "unit_weight": 0.0, "fc": 30000.0}, limit=200)
    if sw == "SAP2000":
        show("plain shell", "define_area_section", {**S, "units": U, "name": "SH20", "kind": "shell_thin",
                                                     "material": "CA", "thickness": T}, limit=200)
    else:
        show("plain slab", "define_area_section", {**S, "units": U, "name": "SH20", "kind": "slab",
                                                    "material": "CA", "thickness": T, "shell_type": "shell_thin"},
             limit=200)
    uni = [layer("TOP", 0.05, "CA"), layer("BOT", -0.05, "CA")]
    two = [layer("TOP", 0.05, "CA"), layer("BOT", -0.05, "CB")]
    dry = show("layered dry run", "define_layered_shell", {**S, "units": U, "name": "LUNI", "layers": uni,
                                                            "dry_run": True}, limit=600)
    check(f"{sw}: dry run changes nothing and gives depth 0.2", ok(dry) and dry["would"]["extent"]["depth"] == 0.2
          and "LUNI" not in (bridge.try_name_list(bridge.sap_model(sw), ["PropArea"])[0] or []), dry)
    for name, lay in (("LUNI", uni), ("LTWO", two)):
        r = show(f"layered {name}", "define_layered_shell", {**S, "units": U, "name": name, "layers": lay},
                 limit=1500)
        check(f"{sw}: {name} layers read back as given", ok(r) and r["read_back"] == lay
              and not r.get("not_kept_as_given"), r)
    d = show("details", "get_definition_details", {**S, "units": U, "kind": "area_sections",
                                                   "names": ["LTWO"]}, limit=1500)
    items = (d.get("items") or d.get("area_sections") or []) if ok(d) else []
    check(f"{sw}: get_definition_details shows the layers", bool(items) and items[0].get("layers") == two, d)
    show("tip pattern", "define_load_pattern", {**S, "name": "PTIP", "type": "other", "self_weight_multiplier": 0},
         limit=200)
    tips = {}
    for i, sec in enumerate(("SH20", "LUNI", "LTWO")):
        root, tip = strip(sw, S, z, 3.0 * i, sec)
        show(f"fix root {sec}", "assign_point_properties", {**S, "point_names": root, "restraint": "fixed"},
             limit=200)
        show(f"tip load {sec}", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point",
                                                               "object_names": tip, "load_pattern": "PTIP",
                                                               "values": [P / len(tip) if tip else 0],
                                                               "direction": "Gravity"}, limit=200)
        tips[sec] = tip
    return tips


def part_b(sw, S, z):
    show("deck concrete", "define_material", {**S, "units": U, "name": "CDK", "type": "concrete", "E": 2.5e7,
                                              "poisson": 0.2, "unit_weight": 25.0, "fc": 30000.0}, limit=200)
    show("deck steel", "define_material", {**S, "units": U, "name": "SDK", "type": "steel", "E": 2.0e8,
                                           "poisson": 0.3, "unit_weight": 0.0, "fy": 345000.0, "fu": 450000.0},
         limit=200)
    show("beam material (no weight)", "define_material", {**S, "units": U, "name": "NW", "type": "steel",
                                                          "E": 2.0e8, "poisson": 0.3, "unit_weight": 0.0,
                                                          "fy": 345000.0, "fu": 450000.0}, limit=200)
    deck = {"deck_type": "filled", "slab_material": "CDK", "deck_material": "SDK", "slab_depth": 0.065,
            "rib_depth": 0.075, "rib_width_top": 0.15, "rib_width_bottom": 0.12, "rib_spacing": 0.3,
            "deck_shear_thickness": 0.0009, "deck_unit_weight": 0.12, "stud_diameter": 0.019, "stud_height": 0.125,
            "stud_fu": 400000.0}
    r = show("filled deck", "define_deck_section", {**S, "units": U, "name": "DKF", **deck}, limit=1200)
    check(f"{sw}: filled deck read back as given", ok(r) and r["read_back"] == deck
          and not r.get("not_kept_as_given"), r)
    for name, extra in (("DKU", {"deck_type": "unfilled", "deck_material": "SDK", "rib_depth": 0.075,
                                 "rib_width_top": 0.15, "rib_width_bottom": 0.12, "rib_spacing": 0.3,
                                 "deck_shear_thickness": 0.0009, "deck_unit_weight": 0.12}),
                        ("DKS", {"deck_type": "solid_slab", "slab_material": "CDK", "slab_depth": 0.15,
                                 "stud_diameter": 0.019, "stud_height": 0.125, "stud_fu": 400000.0})):
        r = show(f"deck {name}", "define_deck_section", {**S, "units": U, "name": name, **extra}, limit=1200)
        given = {k: v for k, v in extra.items()}
        check(f"{sw}: {extra['deck_type']} deck read back as given", ok(r) and not r.get("not_kept_as_given")
              and all(r["read_back"].get(k) == v for k, v in given.items()), r)
    show("beam section", "define_frame_section", {**S, "units": U, "name": "BW", "shape": "rectangle",
                                                  "material": "NW", "depth": 0.5, "width": 0.3}, limit=200)
    x0, y0, a = 6.0, 0.0, 3.0
    c = [[x0, y0, z], [x0 + a, y0, z], [x0 + a, y0 + a, z], [x0, y0 + a, z]]
    show("deck area", "create_areas", {**S, "units": U, "section": "DKF", "areas": [{"points": c}]}, limit=300)
    fr = show("edge beams", "create_frames", {**S, "units": U, "section": "BW", "frames": [
        {"start": c[i], "end": c[(i + 1) % 4]} for i in range(4)]}, limit=600)
    corners = sorted({p for f in (fr.get("created", []) if ok(fr) else []) for p in f.get("points", [])})
    show("pinned corners", "assign_point_properties", {**S, "point_names": corners, "restraint": "pinned"},
         limit=200)
    show("self weight pattern", "define_load_pattern", {**S, "name": "SWT", "type": "other",
                                                         "self_weight_multiplier": 1.0}, limit=200)
    return a * a * (25.0 * (0.065 + 0.075 * (0.15 + 0.12) / 2 / 0.3) + 0.12)


def part_c(sw, S):
    sap = bridge.sap_model(sw)
    show("C30 for curves", "define_material", {**S, "units": U, "name": "CS", "type": "concrete", "E": 2.5e7,
                                               "poisson": 0.2, "unit_weight": 25.0, "fc": 30000.0}, limit=200)
    hys = "takeda" if sw == "SAP2000" else "concrete"
    r = show("concrete simple", "set_material_stress_strain", {**S, "units": U, "material": "CS", "curve": "simple",
                                                               "strain_at_fc": 0.002, "ultimate_strain": 0.005,
                                                               "final_slope": -0.1, "hysteresis": hys}, limit=2500)
    pts = (r.get("read_back") or {}).get("points") or [] if ok(r) else []
    e0, fc, E = 0.002, 30000.0, 2.5e7
    para = [x for x in pts if -e0 < x["strain"] < 0]
    good = [x for x in para if abs(-x["stress"] - fc * (2 * (-x["strain"] / e0) - (-x["strain"] / e0) ** 2))
            <= 1e-6 * fc]
    lin = [x for x in para if x not in good and abs(x["stress"] - E * x["strain"]) <= 1e-6 * fc]
    peak = [x for x in pts if abs(x["strain"] + e0) < 1e-12 and abs(x["stress"] + fc) <= 1e-6 * fc]
    print(f"  concrete curve: {len(para)} points below e0, {len(good)} on the parabola, {len(lin)} on E e", flush=True)
    check(f"{sw}: simple concrete curve = fc [2 e/e0 - (e/e0)^2] (ETABS / SAFE: E e start) and peak (e0, fc)",
          ok(r) and len(good) >= 2 and len(good) + len(lin) == len(para) and bool(peak)
          and not r.get("not_kept_as_given"), pts)
    if sw == "SAP2000":
        show("SAP2000 refuses 'concrete' hysteresis on concrete", "set_material_stress_strain", {
            **S, "material": "CS", "curve": "simple", "hysteresis": "concrete"}, expect_error=True, limit=400)
    show("steel for curves", "define_material", {**S, "units": U, "name": "STC", "type": "steel", "E": 2.0e8,
                                                 "poisson": 0.3, "unit_weight": 77.0, "fy": 355000.0,
                                                 "fu": 510000.0}, limit=200)
    r = show("steel simple", "set_material_stress_strain", {**S, "units": U, "material": "STC", "curve": "simple",
                                                           "strain_at_hardening": 0.02, "strain_at_max_stress": 0.1,
                                                           "strain_at_rupture": 0.2, "final_slope": -0.1},
             limit=2500)
    com(lambda: sap.SetPresentUnits(6))
    g = com(lambda: sap.PropMaterial.GetOSteel_1("STC"))
    fye, fue = float(g[2]), float(g[3])
    pts = (r.get("read_back") or {}).get("points") or [] if ok(r) else []
    hard = [x for x in pts if 0.02 < x["strain"] < 0.1]

    def holzer(e):
        rr = (e - 0.02) / (0.1 - 0.02)
        return fye * (1 + rr * math.exp(1 - rr) * (fue / fye - 1))

    goodh = [x for x in hard if abs(x["stress"] - holzer(x["strain"])) <= 1e-6 * fue]
    yld = [x for x in pts if abs(x["strain"] - fye / 2.0e8) < 1e-9 and abs(x["stress"] - fye) <= 1e-6 * fye]
    print(f"  steel: Fye {fye}, Fue {fue}; hardening points {len(hard)}, on the formula {len(goodh)}", flush=True)
    check(f"{sw}: simple steel hardening = Fye [1 + r e^(1-r) (Fue/Fye - 1)] and yield (Fye/E, Fye)",
          ok(r) and len(hard) >= 1 and len(goodh) == len(hard) and bool(yld), pts)
    user = [{"strain": -0.004, "stress": -40000.0}, {"strain": -0.002, "stress": -60000.0},
            {"strain": -0.001, "stress": -45000.0}, {"strain": 0.0, "stress": 0.0},
            {"strain": 0.0002, "stress": 3000.0}, {"strain": 0.001, "stress": 0.0}]
    show("C60 for user curve", "define_material", {**S, "units": U, "name": "CU", "type": "concrete", "E": 3.0e7,
                                                   "poisson": 0.2, "unit_weight": 25.0, "fc": 60000.0}, limit=200)
    r = show("user curve", "set_material_stress_strain", {**S, "units": U, "material": "CU", "curve": "user",
                                                         "points": user}, limit=2500)
    got = (r.get("read_back") or {}).get("points") or [] if ok(r) else []
    check(f"{sw}: user curve read back point by point (origin labelled A)", ok(r) and not r.get("not_kept_as_given")
          and [(x["strain"], x["stress"]) for x in got] == [(x["strain"], x["stress"]) for x in user]
          and [x["point_id"] for x in got if x["strain"] == 0.0] == [1], got)
    d = show("details", "get_definition_details", {**S, "units": U, "kind": "materials", "names": ["CS", "CU"]},
             limit=1500)
    items = (d.get("items") or d.get("materials") or []) if ok(d) else []
    check(f"{sw}: get_definition_details shows the curve settings", len(items) == 2
          and items[0].get("stress_strain", {}).get("curve") == "simple"
          and items[1].get("stress_strain", {}).get("curve") == "user", d)


def part_d(sw, S):
    p = {**S, "units": U, "material": "CS", "consider_age": True, "consider_creep": True, "consider_shrinkage": True,
         "cement_coefficient": 0.25, "relative_humidity": 70.0, "notional_size": 0.2, "shrinkage_coefficient": 5.0,
         "shrinkage_start_age": 3.0, "creep_integration": "dirichlet_series", "series_terms": 5,
         "scale_factors": [1.0, 1.2, 0.8]}
    if sw != "SAP2000":
        show("time dependence not offered", "set_material_time_dependence", p, expect_error=True, limit=400)
        return
    r = show("time dependence", "set_material_time_dependence", p, limit=1500)
    rb = r.get("read_back", {}) if ok(r) else {}
    check(f"{sw}: CEB-FIP 90 time dependence read back as given", ok(r) and not r.get("not_kept_as_given")
          and rb.get("relative_humidity") == 70.0 and rb.get("creep_integration") == "dirichlet_series"
          and rb.get("scale_factors") == {"age": 1.0, "creep": 1.2, "shrinkage": 0.8}, r)


def tip_u3(sw, S, tips):
    names = [p for t in tips.values() for p in t]
    dr = show("tip deflections", "get_results", {**S, "units": U, "result_type": "joint_displacements",
                                                 "names": names, "load_cases_or_combos": ["PTIP"]}, limit=1500)
    rows = dr.get("rows", []) if ok(dr) else []
    by = {str(r.get("Joint") or r.get("Obj") or r.get("Name")): num(r.get("U3")) for r in rows}
    return {sec: (sum(abs(by.get(p, 0.0)) for p in t) / len(t) if t else 0.0) for sec, t in tips.items()}, rows


def run(sw):
    S = {"software": sw}
    nm = {**S, "units": U, "file_path": str(OUT / f"props85_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=8.0, spacing_y=8.0)
    show("new model", "new_model", nm, limit=300)
    z = 3.0 if sw == "ETABS" else 0.0
    tips = part_a(sw, S, z)
    w_hand = part_b(sw, S, z) if sw != "SAP2000" else None
    part_c(sw, S)
    part_d(sw, S)
    cases = [c for c in (bridge.try_name_list(bridge.sap_model(sw), ["LoadCases"])[0] or [])]
    run_cases = {c: c in ("PTIP", "SWT") for c in cases}
    show("only PTIP / SWT run", "set_analysis_options", {**S, "run_cases": run_cases}, limit=400)
    show("analysis", "run_analysis", {**S}, limit=800)
    u3, rows = tip_u3(sw, S, tips)
    EI_u = E1 * B * T ** 3 / 12
    ybar = (E1 * 0.05 - E2 * 0.05) / (E1 + E2)          # transformed-section centroid from mid-depth
    EI_c = E1 * (0.1 ** 3 / 12 + 0.1 * (0.05 - ybar) ** 2) + E2 * (0.1 ** 3 / 12 + 0.1 * (-0.05 - ybar) ** 2)
    hand_u, hand_c = P * L ** 3 / (3 * EI_u), P * L ** 3 / (3 * EI_c)
    print(f"  tip U3 {u3}; hand uniform {hand_u:.6e}, composite {hand_c:.6e} (EI {EI_c:.2f})", flush=True)
    tol = 0.02
    check(f"{sw}: plain shell tip = PL^3/3EI within 2 %", abs(u3.get("SH20", 0) - hand_u) <= tol * hand_u, u3)
    check(f"{sw}: uniform layered shell tip = PL^3/3EI within 2 %", abs(u3.get("LUNI", 0) - hand_u) <= tol * hand_u,
          u3)
    check(f"{sw}: two-material layered tip = PL^3/3EI(transformed) within 2 %",
          abs(u3.get("LTWO", 0) - hand_c) <= tol * hand_c, u3)
    ratio = u3.get("LTWO", 0) / u3.get("LUNI") if u3.get("LUNI") else 0
    check(f"{sw}: layered ratio two-material / uniform = EI_u / EI_c = {EI_u / EI_c:.5f} within 1 %",
          abs(ratio - EI_u / EI_c) <= 0.01 * EI_u / EI_c, ratio)
    if w_hand is not None:
        br = show("base reactions", "get_results", {**S, "units": U, "result_type": "base_reactions",
                                                    "load_cases_or_combos": ["SWT"]}, limit=800)
        fz = [abs(num(r.get("FZ"))) for r in (br.get("rows", []) if ok(br) else [])]
        print(f"  deck self weight FZ {fz}, hand {w_hand:.5f} kN (alt. ribs = sr - avg width: "
              f"{9 * (25 * (0.065 + 0.075 * (0.3 - 0.135) / 0.3) + 0.12):.5f})", flush=True)
        check(f"{sw}: filled deck self weight = 9 m2 x (gamma_c (tc + hr wr / sr) + deck weight) within 0.5 %",
              bool(fz) and abs(fz[0] - w_hand) <= 0.005 * w_hand, {"program": fz, "hand": w_hand})


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
for sw in targets:
    print(f"\n################ {sw}", flush=True)
    t0 = len(FAILED)
    try:
        com(bridge.launch_new, sw, visible=True)
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
