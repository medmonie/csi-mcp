r"""Live test of 0.8.0 (Phase 3 batch 2: definitions) in NEW scratch instances.

    run_live_phase3b.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_phase3b.txt

Only the empty model and the geometry come from the raw API; every definition is made through the new actions and
read back. A 4 m cantilever of a 0.4 x 0.4 section in a material with E = 25 GPa (shear stiffness raised with the
section modifiers, element mass and weight 0) is checked against hand values:
    tip load 10 kN in a new SDL pattern          delta = P L^3 / (3 E I) = 4.000 mm
    new linear static case SDL x 2               8.000 mm
    new combination ULS = 1.4 SDL + 1.6 LIVE2    5.600 mm (LIVE2 carries nothing)
    new envelope ENV of ULS and SDL              5.600 mm (max)
    mass source only from a 10 kN vertical load (MASSLOAD) -> tip mass m = 10 / g
    new modal case (SAP2000, ETABS): first period T = 2 pi sqrt(m / k), k = 3 E I / L^3 -> 0.1269 s
SAP2000 / ETABS: vertical column fixed at the base, tip loads horizontal (X). SAFE: horizontal beam at the base level
fixed at one end, tip load vertical (no modal case).
Refusals: spring properties in SAP2000, plate / shell kinds in ETABS / SAFE, slab in SAP2000, mass-source name outside
SAP2000.
"""
import math
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase3b_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001
from csi_bridge import api_call  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv

KN_M_C = 6
E, B, L, P, W, G = 25.0e6, 0.4, 4.0, 10.0, 10.0, 9.80665
I = B ** 4 / 12
K = 3 * E * I / L ** 3
DELTA = P / K
T1 = 2 * math.pi * math.sqrt(W / G / K)


def blank(sw: str):
    sap = bridge.sap_model(sw)
    api_call("InitializeNewModel", sap.InitializeNewModel, KN_M_C)
    if sw == "SAP2000":
        api_call("File.NewBlank", sap.File.NewBlank)
        return 0.0
    api_call("File.NewGridOnly", sap.File.NewGridOnly, 1, L, L, 2, 2, 6.0, 6.0)
    return float(api_call("Story.GetStories_2", sap.Story.GetStories_2)[0])   # base elevation


def ends(sw: str, frame: str):
    sap = bridge.sap_model(sw)
    p1, p2 = api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, frame, "", "")[:2]
    return str(p1), str(p2)


def fix(sw: str, point: str):
    sap = bridge.sap_model(sw)
    api_call("PointObj.SetRestraint", sap.PointObj.SetRestraint, point, [True] * 6)


def tip_value(sw: str, case: str, tip: str, comp: str):
    r = show(f"tip {comp} for {case}", "get_results", {"software": sw, "result_type": "joint_displacements",
                                                       "names": [tip], "load_cases_or_combos": [case]}, limit=600)
    if not isinstance(r, dict) or not r.get("rows"):
        return None
    return max((row[comp] for row in r["rows"]), key=abs)


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
print(f"hand values: I = {I:.6e} m4, k = {K:.3f} kN/m, delta = {DELTA * 1000:.3f} mm, T1 = {T1:.4f} s", flush=True)

for sw in targets:
    print(f"\n################ {sw} (new scratch instance, visible={visible})", flush=True)
    t0 = time.perf_counter()
    try:
        com(bridge.launch_new, sw, visible=visible)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to start {sw}: {exc}", flush=True)
        FAILED.append(f"{sw} start")
        continue
    try:
        z0 = com(blank, sw)
        modal = sw != "SAFE"
        # ---------------------------------------------------------------- materials
        mat = {"software": sw, "name": "C25T", "type": "concrete", "E": E, "poisson": 0.2, "unit_weight": 25.0,
               "fc": 25000.0}
        d = show("material dry run", "define_material", {**mat, "dry_run": True}, limit=400)
        r = show("material C25T", "define_material", mat, limit=1200)
        if isinstance(r, dict):
            b = r["read_back"]
            check("material read back: concrete, E, poisson, unit weight, fc",
                  b["type"] == "Concrete" and abs(b["E"] - E) < 1 and b["poisson"] == 0.2
                  and abs(b["weight_per_volume"] - 25.0) < 1e-6 and abs(b["fc"] - 25000.0) < 1e-3, b)
        show("material exists, no replace", "define_material", mat, expect_error=True, limit=300)
        s = show("steel S355", "define_material", {"software": sw, "name": "S355", "type": "steel", "E": 2.1e8,
                                                   "poisson": 0.3, "unit_weight": 78.5, "fy": 355000.0,
                                                   "fu": 510000.0}, limit=1200)
        if isinstance(s, dict):
            check("steel read back fy / fu", abs(s["read_back"]["fy"] - 355000) < 1e-3
                  and abs(s["read_back"]["fu"] - 510000) < 1e-3, (s["read_back"].get("fy"), s["read_back"].get("fu")))
        # ---------------------------------------------------------------- sections
        f = show("frame section SQ400", "define_frame_section",
                 {"software": sw, "name": "SQ400", "shape": "rectangle", "material": "C25T", "depth": B, "width": B,
                  "modifiers": [1, 1000, 1000, 1, 1, 1, 0, 0]}, limit=1500)
        if isinstance(f, dict):
            pr = f["read_back"].get("properties", {})
            check("SQ400: area 0.16, I33 = I22 = b^4 / 12", abs(pr.get("area", 0) - B * B) < 1e-9
                  and abs(pr.get("i33", 0) - I) < 1e-9 and abs(pr.get("i22", 0) - I) < 1e-9, pr)
        h = show("I section HEB300-like", "define_frame_section",
                 {"software": sw, "name": "I300", "shape": "i_section", "material": "S355", "depth": 0.3,
                  "width": 0.3, "flange_thickness": 0.019, "web_thickness": 0.011}, limit=1500)
        if isinstance(h, dict):
            a_hand = 2 * 0.3 * 0.019 + (0.3 - 2 * 0.019) * 0.011
            got = h["read_back"].get("properties", {}).get("area", 0)
            check("I300 area = 2 b tf + (d - 2 tf) tw (no fillets)", abs(got - a_hand) < 1e-9, (got, a_hand))
        if sw == "SAP2000":
            a = show("area section shell_thick", "define_area_section",
                     {"software": sw, "name": "WALL300", "kind": "shell_thick", "material": "C25T", "thickness": 0.3,
                      "modifiers": [1, 1, 1, 0.35, 0.35, 0.35, 1, 1, 1, 1]}, limit=800)
            if isinstance(a, dict):
                check("WALL300 read back: shell type 2, 0.3, modifiers", a["read_back"]["shell_type"] == 2
                      and a["read_back"]["thickness"] == 0.3 and a["read_back"]["modifiers"][3] == 0.35, a["read_back"])
            show("slab refused in SAP2000", "define_area_section", {"software": sw, "name": "S", "kind": "slab",
                                                                   "material": "C25T", "thickness": 0.2},
                 expect_error=True, limit=300)
            show("spring property refused in SAP2000", "define_spring_property",
                 {"software": sw, "name": "K", "kind": "point", "stiffness": [1, 1, 1, 0, 0, 0]}, expect_error=True,
                 limit=300)
        else:
            a = show("slab section", "define_area_section",
                     {"software": sw, "name": "SLAB250", "kind": "slab", "material": "C25T", "thickness": 0.25,
                      "shell_type": "shell_thick"}, limit=800)
            if isinstance(a, dict):
                check("SLAB250 read back: slab, thick shell, 0.25", a["read_back"]["kind"] == "slab"
                      and a["read_back"]["shell_type"] == 2 and a["read_back"]["thickness"] == 0.25, a["read_back"])
            w = show("wall section", "define_area_section", {"software": sw, "name": "W300", "kind": "wall",
                                                             "material": "C25T", "thickness": 0.3}, limit=800)
            if isinstance(w, dict):
                check("W300 read back: wall 0.3", w["read_back"].get("kind") == "wall"
                      and w["read_back"].get("thickness") == 0.3, w["read_back"])
            show("plate refused", "define_area_section", {"software": sw, "name": "P", "kind": "plate_thin",
                                                          "material": "C25T", "thickness": 0.2},
                 expect_error=True, limit=300)
            sp = show("point spring", "define_spring_property",
                      {"software": sw, "name": "PILE", "kind": "point", "stiffness": [5000, 5000, 200000, 0, 0, 0]},
                      limit=600)
            if isinstance(sp, dict):
                check("PILE read back", sp["read_back"].get("stiffness", [0, 0, 0])[2] == 200000, sp["read_back"])
            sa = show("area spring", "define_spring_property",
                      {"software": sw, "name": "SUBGRADE", "kind": "area", "stiffness": [0, 0, 20000]}, limit=600)
            if isinstance(sa, dict):
                check("SUBGRADE read back", sa["read_back"].get("stiffness", [0, 0, 0])[2] == 20000, sa["read_back"])
        # ---------------------------------------------------------------- patterns
        for name, kind in (("SDL", "superdead"), ("LIVE2", "live"), ("MASSLOAD", "other")):
            r = show(f"pattern {name}", "define_load_pattern", {"software": sw, "name": name, "type": kind}, limit=500)
            if isinstance(r, dict):
                check(f"{name} read back type {kind}", r["read_back"]["type"].lower() == kind, r["read_back"])
        # ---------------------------------------------------------------- geometry (raw API through the actions)
        if sw == "SAFE":
            start, end, axis, sign = [0.0, 0.0, z0], [L, 0.0, z0], "U3", -1
        else:
            start, end, axis, sign = [0.0, 0.0, z0], [0.0, 0.0, z0 + L], "U1", 1
        fr = show("cantilever", "create_frame_element", {"software": sw, "start_xyz": start, "end_xyz": end,
                                                         "section_name": "SQ400"}, limit=600)
        frame = fr["frame_name"] if isinstance(fr, dict) else None
        p1, p2 = com(ends, sw, frame)
        com(fix, sw, p1)
        if sw == "SAFE":
            show("tip load SDL", "assign_point_or_frame_load", {"software": sw, "target_type": "point",
                                                                "object_names": [p2], "load_pattern": "SDL",
                                                                "values": [P], "direction": "Gravity"}, limit=300)
        else:
            show("tip load SDL", "assign_point_or_frame_load", {"software": sw, "target_type": "point",
                                                                "object_names": [p2], "load_pattern": "SDL",
                                                                "values": [P], "direction": "X"}, limit=300)
            show("tip mass load", "assign_point_or_frame_load", {"software": sw, "target_type": "point",
                                                                 "object_names": [p2], "load_pattern": "MASSLOAD",
                                                                 "values": [W], "direction": "Gravity"}, limit=300)
        # ---------------------------------------------------------------- cases, combinations, mass source
        c = show("case SDL x 2", "define_load_case", {"software": sw, "name": "SDLX2", "type": "linear_static",
                                                      "loads": [{"name": "SDL", "factor": 2.0}]}, limit=800)
        if isinstance(c, dict):
            check("SDLX2 read back: SDL x 2", c["read_back"].get("loads") == [{"type": "Load", "name": "SDL",
                                                                                "factor": 2.0}], c["read_back"])
        if modal:
            m = show("modal case", "define_load_case", {"software": sw, "name": "MODAL6", "type": "modal_eigen",
                                                        "max_modes": 6}, limit=800)
            ms = show("mass source from MASSLOAD only", "set_mass_source",
                      {"software": sw, "from_elements": False, "from_masses": False, "from_loads": True,
                       "loads": [{"pattern": "MASSLOAD", "factor": 1.0}]}, limit=800)
            if isinstance(ms, dict):
                b = ms["read_back"]
                check("mass source read back", b["from_loads"] and not b["from_elements"]
                      and b["load_patterns"] == [{"pattern": "MASSLOAD", "factor": 1.0}], b)
        if sw != "SAP2000":
            show("mass source name refused", "set_mass_source", {"software": sw, "name": "MS2"}, expect_error=True,
                 limit=300)
        u = show("combination ULS", "define_combination",
                 {"software": sw, "name": "ULS", "items": [{"name": "SDL", "factor": 1.4},
                                                           {"name": "LIVE2", "factor": 1.6}]}, limit=800)
        if isinstance(u, dict):
            check("ULS read back", u["read_back"]["type"] == "LinearAdd"
                  and [(i["name"], i["factor"]) for i in u["read_back"]["items"]] == [("SDL", 1.4), ("LIVE2", 1.6)],
                  u["read_back"])
        e = show("envelope ENV", "define_combination",
                 {"software": sw, "name": "ENV", "type": "envelope", "items": [{"name": "ULS"}, {"name": "SDL"}]},
                 limit=800)
        if isinstance(e, dict):
            check("ENV read back: envelope of a combination and a case", e["read_back"]["type"] == "Envelope"
                  and [i["kind"] for i in e["read_back"]["items"]] == ["combination", "case"], e["read_back"])
        # ---------------------------------------------------------------- analysis and hand checks
        path = str(OUT / f"cantilever_{sw.lower()}{ {'SAP2000': '.sdb', 'ETABS': '.edb', 'SAFE': '.fdb'}[sw] }")
        show("save", "open_or_save_model", {"software": sw, "action": "save", "file_path": path}, limit=200)
        show("run", "run_analysis", {"software": sw}, limit=500)
        lg = show("analysis log", "get_analysis_log", {"software": sw}, limit=600)
        if isinstance(lg, dict):
            check("stable", lg.get("stable") is True, lg.get("stable"))
        for case, factor in (("SDL", 1.0), ("SDLX2", 2.0), ("ULS", 1.4), ("ENV", 1.4)):
            v = tip_value(sw, case, p2, axis)
            want = sign * DELTA * factor
            check(f"tip {axis} {case} = {want * 1000:.3f} mm", v is not None and abs(v - want) < 1e-4 * abs(want)
                  + 1e-9, v)
        if modal:
            mp = show("modal periods", "get_results", {"software": sw, "result_type": "modal_periods",
                                                       "load_cases_or_combos": ["MODAL6"]}, limit=1200)
            if isinstance(mp, dict) and mp.get("rows"):
                t = max(row["Period"] for row in mp["rows"])
                check(f"first period = 2 pi sqrt(m / k) = {T1:.4f} s", abs(t - T1) < 2e-3 * T1, t)
            else:
                check("modal periods returned", False, mp)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED ({type(exc).__name__}): {exc}", flush=True)
        FAILED.append(f"{sw}: {exc}")
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"close {sw}: {exc}", flush=True)
        print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)

sys.exit(finish())
