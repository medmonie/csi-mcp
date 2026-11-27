r"""Live test of 0.9.0 (Phase 3 batch 3: objects) in NEW scratch instances.

    run_live_phase3c.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_phase3c.txt

Only the empty model comes from the raw API. Definitions (0.8.0 actions) and every object, assignment and load come
from the actions; all at the base level, section 0.4 x 0.4 (E = 25 GPa, shear stiffness raised by modifiers):
  A releases   points A (fixed) - M - B (fixed), frames A-M and M-B released for M2 / M3 at A and B; 10 kN at M:
               midspan moment PL/4 = 10.000 kNm, deflection PL^3/(48 EI) = 0.250 mm
  B temperature  bar C-D fixed at both ends, uniform +30 deg: axial force -E A alpha dT (alpha read back)
  C point spring  E fixed, F guided (UZ free), 12 EI / L^3 = 10000 kN/m plus a 10000 kN/m spring at F; 100 kN at F:
               deflection 100 / 20000 = 5.000 mm
  D subgrade   free plate on area springs ks = 20000 kN/m3 (corners held in plane), q = 10 kN/m2: settlement q / ks =
               0.500 mm everywhere
  E ETABS wall built with create_areas (the 0.6 item: an API-built wall was unstable in the steel deck template):
               100 kN lateral at the top: stable, base shear 100 kN
  groups, selection, delete; read back of releases, offsets, insertion point, modifiers, local axis, point mass
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase3c_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001
from csi_bridge import api_call  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv

KN_M_C = 6
E, B, L = 25.0e6, 0.4, 4.0
I = B ** 4 / 12
EXT = {"SAP2000": ".sdb", "ETABS": ".edb", "SAFE": ".fdb"}


def blank(sw: str) -> float:
    sap = bridge.sap_model(sw)
    api_call("InitializeNewModel", sap.InitializeNewModel, KN_M_C)
    if sw == "SAP2000":
        api_call("File.NewBlank", sap.File.NewBlank)
        return 0.0
    api_call("File.NewGridOnly", sap.File.NewGridOnly, 1, L, L, 2, 2, 6.0, 6.0)
    return float(api_call("Story.GetStories_2", sap.Story.GetStories_2)[0])


def res(sw, kind, names, case, comps):
    r = show(f"{kind} {case} {names[:3]}", "get_results", {"software": sw, "result_type": kind, "names": names,
                                                           "load_cases_or_combos": [case]}, limit=500)
    if not isinstance(r, dict) or not r.get("rows"):
        return None
    vals = [row[c] for row in r["rows"] for c in comps if row.get(c) is not None]
    return max(vals, key=abs) if vals else None


def near(got, want, rel=1e-4, absolute=1e-9):
    return got is not None and abs(got - want) <= rel * abs(want) + absolute


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)

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
        zb = com(blank, sw)
        # ETABS holds free joints at the Base level fixed in the analysis (found 26 Nov: a free base joint gave all
        # zero results), so the ETABS checks are built at Story1; a base-level copy of check A is kept as a record.
        z = zb + L if sw == "ETABS" else zb
        S = {"software": sw}
        # ------------------------------------------------------------------ definitions (0.8.0)
        m = show("material", "define_material", {**S, "name": "C25T", "type": "concrete", "E": E, "poisson": 0.2,
                                                 "unit_weight": 25.0, "fc": 25000.0}, limit=400)
        alpha = m["read_back"]["thermal_coeff"] if isinstance(m, dict) else None
        show("section", "define_frame_section", {**S, "name": "SQ400", "shape": "rectangle", "material": "C25T",
                                                 "depth": B, "width": B, "modifiers": [1, 1000, 1000, 1, 1, 1, 0, 0]},
             limit=300)
        if sw == "SAP2000":
            show("plate section", "define_area_section", {**S, "name": "PL250", "kind": "shell_thin",
                                                          "material": "C25T", "thickness": 0.25}, limit=300)
        else:
            show("slab section", "define_area_section", {**S, "name": "PL250", "kind": "slab", "material": "C25T",
                                                         "thickness": 0.25}, limit=300)
            show("area spring", "define_spring_property", {**S, "name": "SUBGR", "kind": "area",
                                                           "stiffness": [0, 0, 20000]}, limit=300)
        for name in ("PL", "TEMP", "TGRAD", "SPR", "Q", "WALLX"):
            show(f"pattern {name}", "define_load_pattern", {**S, "name": name, "type": "other"}, limit=200)
        # ------------------------------------------------------------------ A releases
        d = show("points dry run", "create_points", {**S, "points": [{"xyz": [0, 0, z]}], "dry_run": True}, limit=200)
        pa = show("supports A, B fixed", "create_points", {**S, "points": [{"xyz": [0, 0, z], "name": "A"},
                                                                          {"xyz": [L, 0, z], "name": "B"}],
                                                          "restraint": "fixed"}, limit=300)
        A, Bp = pa["point_names"]
        fm = show("frames A-M, M-B", "create_frames", {**S, "section": "SQ400",
                                                       "frames": [{"start": A, "end": [L / 2, 0, z]},
                                                                  {"start": [L / 2, 0, z], "end": Bp}]}, limit=600)
        f_am, f_mb = (c["name"] for c in fm["created"])
        M = fm["created"][0]["points"][1]
        check("the middle point is shared", fm["created"][1]["points"][0] == M, fm["created"])
        r1 = show("release A end", "assign_frame_properties", {**S, "frame_names": [f_am],
                                                               "releases": {"i": ["M2", "M3"]}}, limit=500)
        r2 = show("release B end", "assign_frame_properties", {**S, "frame_names": [f_mb],
                                                               "releases": {"j": ["M2", "M3"]}}, limit=500)
        if isinstance(r1, dict) and isinstance(r2, dict):
            check("releases read back", r1["read_back"][0].get("releases_i") == "M2 M3"
                  and r2["read_back"][0].get("releases_j") == "M2 M3", (r1["read_back"], r2["read_back"]))
        show("10 kN at M", "assign_point_or_frame_load", {**S, "target_type": "point", "object_names": [M],
                                                          "load_pattern": "PL", "values": [10.0],
                                                          "direction": "Gravity"}, limit=200)
        # ------------------------------------------------------------------ B temperature
        pc = show("bar ends C, D fixed", "create_points", {**S, "points": [{"xyz": [0, 3, z]}, {"xyz": [L, 3, z]}],
                                                          "restraint": "fixed"}, limit=300)
        fb = show("bar C-D", "create_frames", {**S, "section": "SQ400",
                                               "frames": [{"start": pc["point_names"][0],
                                                           "end": pc["point_names"][1]}]}, limit=400)
        bar = fb["created"][0]["name"]
        tl = show("+30 deg on the bar", "assign_temperature_load", {**S, "target_type": "frame",
                                                                    "object_names": [bar], "load_pattern": "TEMP",
                                                                    "value": 30.0}, limit=400,
                  expect_error=sw == "SAFE")
        if sw == "SAFE":
            check("SAFE refuses frame temperature loads", not isinstance(tl, dict), type(tl).__name__)
        if isinstance(tl, dict):
            check("temperature read back", tl["read_back"][0]["loads"] == [{"pattern": "TEMP", "type": 1,
                                                                            "value": 30.0}], tl["read_back"])
        pg = show("bar ends G, H fixed", "create_points", {**S, "points": [{"xyz": [0, 12, z]}, {"xyz": [L, 12, z]}],
                                                          "restraint": "fixed"}, limit=300)
        fg = show("bar G-H", "create_frames", {**S, "section": "SQ400",
                                               "frames": [{"start": pg["point_names"][0],
                                                           "end": pg["point_names"][1]}]}, limit=400)
        gbar = fg["created"][0]["name"]
        show("gradient 10 deg/m along local 2", "assign_temperature_load",
             {**S, "target_type": "frame", "object_names": [gbar], "load_pattern": "TGRAD", "kind": "gradient_2",
              "value": 10.0}, limit=400, expect_error=sw == "SAFE")
        pg3 = show("bar ends G3, H3 fixed", "create_points", {**S, "points": [{"xyz": [0, 15, z]},
                                                                            {"xyz": [L, 15, z]}],
                                                             "restraint": "fixed"}, limit=300)
        fg3 = show("bar G3-H3", "create_frames", {**S, "section": "SQ400",
                                                  "frames": [{"start": pg3["point_names"][0],
                                                              "end": pg3["point_names"][1]}]}, limit=400)
        gbar3 = fg3["created"][0]["name"]
        show("gradient 10 deg/m along local 3", "assign_temperature_load",
             {**S, "target_type": "frame", "object_names": [gbar3], "load_pattern": "TGRAD", "kind": "gradient_3",
              "value": 10.0}, limit=400, expect_error=sw == "SAFE")
        ax = show("bar local axes", "get_object_assignments", {**S, "object_type": "frames", "names": [gbar],
                                                               "include_loads": False}, limit=400)
        base_m = None
        if sw == "ETABS":
            pb = show("record: base-level supports", "create_points", {**S, "points": [{"xyz": [0, -3, zb]},
                                                                                     {"xyz": [L, -3, zb]}],
                                                                      "restraint": "fixed"}, limit=300)
            fb0 = show("record: base-level beam", "create_frames", {**S, "section": "SQ400", "frames": [
                {"start": pb["point_names"][0], "end": [L / 2, -3, zb]},
                {"start": [L / 2, -3, zb], "end": pb["point_names"][1]}]}, limit=400)
            base_m = fb0["created"][0]["points"][1]
            ra = show("record: free base joint read back", "get_object_assignments",
                      {**S, "object_type": "points", "names": [base_m], "non_default_only": False}, limit=600)
            show("record: 10 kN at the base joint", "assign_point_or_frame_load",
                 {**S, "target_type": "point", "object_names": [base_m], "load_pattern": "PL", "values": [10.0],
                  "direction": "Gravity"}, limit=200)
        # ------------------------------------------------------------------ C point spring
        pe = show("E fixed", "create_points", {**S, "points": [{"xyz": [0, 6, z]}], "restraint": "fixed"}, limit=200)
        pf = show("F guided", "create_points", {**S, "points": [{"xyz": [L, 6, z]}],
                                                "restraint": [True, True, False, True, True, True]}, limit=200)
        F = pf["point_names"][0]
        ps = show("spring and mass at F", "assign_point_properties",
                  {**S, "point_names": [F], "spring_stiffness": [0, 0, 10000, 0, 0, 0],
                   "mass": [0.5, 0.5, 0.5, 0, 0, 0]}, limit=600)
        if isinstance(ps, dict):
            b = ps["read_back"][0]
            check("spring / mass read back", b.get("spring_K", [0, 0, 0])[2] == 10000
                  and b.get("mass", [0])[0] == 0.5, b)
        show("frame E-F", "create_frames", {**S, "section": "SQ400",
                                            "frames": [{"start": pe["point_names"][0], "end": F}]}, limit=300)
        show("100 kN at F", "assign_point_or_frame_load", {**S, "target_type": "point", "object_names": [F],
                                                           "load_pattern": "SPR", "values": [100.0],
                                                           "direction": "Gravity"}, limit=200)
        # ------------------------------------------------------------------ D slab on subgrade
        ar = show("plate", "create_areas", {**S, "section": "PL250", "areas": [{"points": [
            [10, 0, z], [14, 0, z], [14, 4, z], [10, 4, z]]}]}, limit=500)
        plate = ar["created"][0]["name"]
        corners = ar["created"][0]["points"]
        show("corners held in plane", "assign_point_properties",
             {**S, "point_names": corners, "restraint": [True, True, False, False, False, True]}, limit=300)
        if sw == "SAP2000":
            sg = show("subgrade springs", "assign_area_properties", {**S, "area_names": [plate],
                                                                     "subgrade_modulus": 20000.0}, limit=400)
        else:
            sg = show("area spring property", "assign_area_properties", {**S, "area_names": [plate],
                                                                         "spring_property": "SUBGR"}, limit=400)
            if isinstance(sg, dict):
                check("area spring read back", sg["read_back"][0].get("spring_property") == "SUBGR", sg["read_back"])
        show("q = 10 kN/m2", "assign_area_load", {**S, "area_names": [plate], "load_pattern": "Q",
                                                  "load_type": "uniform", "value": 10.0}, limit=300)
        # ------------------------------------------------------------------ E ETABS wall
        wall_base = []
        if sw == "ETABS":
            show("wall section", "define_area_section", {**S, "name": "W300", "kind": "wall", "material": "C25T",
                                                         "thickness": 0.3}, limit=300)
            wa = show("wall by create_areas", "create_areas", {**S, "section": "W300", "areas": [{"points": [
                [20, 0, zb], [26, 0, zb], [26, 0, zb + L], [20, 0, zb + L]]}]}, limit=500)
            wpts = wa["created"][0]["points"]
            wall_base, wall_top = wpts[:2], wpts[2:]
            show("wall base fixed", "assign_point_properties", {**S, "point_names": wall_base, "restraint": "fixed"},
                 limit=300)
            show("100 kN at the wall top", "assign_point_or_frame_load",
                 {**S, "target_type": "point", "object_names": wall_top, "load_pattern": "WALLX", "values": [50.0],
                  "direction": "X"}, limit=200)
        # ------------------------------------------------------------------ properties read back, groups, select, delete
        tmp = show("temporary frame", "create_frames", {**S, "section": "SQ400",
                                                        "frames": [{"start": [0, 9, z], "end": [L, 9, z]}]}, limit=300)
        t_name, t_pts = tmp["created"][0]["name"], tmp["created"][0]["points"]
        fa = show("offsets, insertion, modifiers, axis", "assign_frame_properties",
                  {**S, "frame_names": [t_name], "end_offsets": {"length_i": 0.2, "length_j": 0.3,
                                                                  "rigid_zone_factor": 0.5},
                   "insertion_point": {"cardinal_point": 8}, "modifiers": [1, 1, 1, 0.1, 0.7, 0.35, 1, 1],
                   "local_axis_angle": 30}, limit=800)
        if sw == "SAP2000":
            fo = bridge.sap_model(sw).FrameObj
            for label, fn in (("GetModifiers(name)", lambda: fo.GetModifiers(t_name)),
                              ("GetModifiers(name, [0]*8)", lambda: fo.GetModifiers(t_name, [0.0] * 8)),
                              ("SetModifiers(name, v, 0) then Get", lambda: (fo.SetModifiers(t_name, [1, 1, 1, 0.1, 0.7,
                                                                                                     0.35, 1, 1], 0),
                                                                             fo.GetModifiers(t_name))),
                              ("PropFrame.GetModifiers(SQ400)",
                               lambda: bridge.sap_model(sw).PropFrame.GetModifiers("SQ400"))):
                try:
                    print(f"raw SAP2000 {label} -> {com(fn)!r}", flush=True)
                except Exception as exc:  # noqa: BLE001
                    print(f"raw SAP2000 {label} -> {type(exc).__name__}: {exc}", flush=True)
        if isinstance(fa, dict):
            b = fa["read_back"][0]
            check("frame properties read back", b.get("end_offsets", {}).get("length_j") == 0.3
                  and b.get("insertion_point", {}).get("cardinal_point") == 8
                  and b.get("modifiers", {}).get("I33") == 0.35 and b.get("local_axis_angle") == 30, b)
        g = show("group define", "set_group", {**S, "operation": "define", "name": "CHECKS"}, limit=200)
        g = show("group add", "set_group", {**S, "operation": "add", "name": "CHECKS", "frames": [f_am, f_mb, bar],
                                            "points": [M]}, limit=300)
        if isinstance(g, dict):
            check("group holds 3 frames, 1 point", g["group_holds"] == {"frame": 3, "point": 1}, g["group_holds"])
        g = show("group remove", "set_group", {**S, "operation": "remove", "name": "CHECKS", "frames": [bar]},
                 limit=300)
        if isinstance(g, dict):
            check("group holds 2 frames after remove", g["group_holds"].get("frame") == 2, g["group_holds"])
        sel = show("select group", "select_objects", {**S, "mode": "group", "group": "CHECKS"}, limit=300)
        if isinstance(sel, dict):
            check("selection = the group", sel["selected"].get("frame") == 2, sel["selected"])
        sel = show("clear selection", "select_objects", {**S, "mode": "clear"}, limit=200)
        if isinstance(sel, dict):
            check("selection cleared", sel["selected_total"] == 0, sel)
        dl = show("delete temporary frame", "delete_objects", {**S, "kind": "frames", "names": [t_name]}, limit=300)
        dp = show("delete point A (connected)", "delete_objects", {**S, "kind": "points", "names": [A]}, limit=400)
        if isinstance(dl, dict) and isinstance(dp, dict):
            check("frame deleted with its 2 free end points; A kept (connected)", dl["deleted"] == 1
                  and sorted(dl.get("points_removed_with_them", [])) == sorted(t_pts) and dp["deleted"] == 0
                  and dp.get("kept") == [A], (dl, dp))
        # ------------------------------------------------------------------ analysis and hand checks
        path = str(OUT / f"objects_{sw.lower()}{EXT[sw]}")
        show("save", "open_or_save_model", {**S, "action": "save", "file_path": path}, limit=200)
        show("run", "run_analysis", S, limit=400)
        lg = show("analysis log", "get_analysis_log", S, limit=500)
        if isinstance(lg, dict):
            check("stable", lg.get("stable") is True, lg.get("stable"))
        mm = res(sw, "frame_forces", [f_am, f_mb], "PL", ["M2", "M3"])
        check("A: midspan moment PL/4 = 10.000 kNm (pinned by releases)", near(abs(mm or 0), 10.0, 1e-4), mm)
        um = res(sw, "joint_displacements", [M], "PL", ["U3"])
        check("A: midspan deflection PL^3/48EI = 0.250 mm", near(abs(um or 0), 10 * L ** 3 / (48 * E * I), 1e-3), um)
        if alpha and sw != "SAFE":
            n = res(sw, "frame_forces", [bar], "TEMP", ["P"])
            want = -E * B * B * alpha * 30.0
            if sw == "SAFE":
                print(f"B: SAFE axial force under a uniform +30 deg = {n} (SAFE analyses out-of-plane bending only)",
                      flush=True)
            else:
                check(f"B: bar axial force -E A alpha dT = {want:.2f} kN", near(n, want, 1e-4), n)
            want = E * I * alpha * 10.0
            mg2 = res(sw, "frame_forces", [gbar], "TGRAD", ["M2", "M3"])
            mg3 = res(sw, "frame_forces", [gbar3], "TGRAD", ["M2", "M3"])
            print(f"B2: gradient along local 2 -> |M| = {mg2}; along local 3 -> |M| = {mg3}; E I alpha g = {want}",
                  flush=True)
            if True:
                check(f"B2: both gradients give |M| = E I alpha g = {want:.4f} kNm",
                      near(abs(mg2 or 0), want, 1e-4) and near(abs(mg3 or 0), want, 1e-4), (mg2, mg3))
        uf = res(sw, "joint_displacements", [F], "SPR", ["U3"])
        check("C: spring point deflection 100 / (12EI/L^3 + 10000) = 5.000 mm", near(abs(uf or 0), 0.005, 1e-4), uf)
        settle = [res(sw, "joint_displacements", [c], "Q", ["U3"]) for c in corners]
        check("D: plate settlement q / ks = 0.500 mm at every corner",
              all(near(abs(v or 0), 0.0005, 1e-3) for v in settle), settle)
        if base_m is not None:
            u0 = res(sw, "joint_displacements", [base_m], "PL", ["U3"])
            m0 = res(sw, "frame_forces", [fb0["created"][0]["name"], fb0["created"][1]["name"]], "PL", ["M2", "M3"])
            print(f"record: ETABS free Base-level joint under 10 kN: U3 = {u0}, beam moment = {m0} (the same beam at "
                  f"Story1 gives PL/4 = 10)", flush=True)
        if sw == "ETABS":
            br = show("wall base reaction", "get_results", {**S, "result_type": "base_reactions",
                                                           "load_cases_or_combos": ["WALLX"]}, limit=500)
            fx = br["rows"][0]["FX"] if isinstance(br, dict) and br.get("rows") else None
            check("E: API-built ETABS wall stable, base shear = 100 kN", near(abs(fx or 0), 100.0, 1e-6), fx)
            top = res(sw, "joint_displacements", wall_top, "WALLX", ["U1"])
            print(f"wall top U1 = {top} m (single shell or ETABS auto mesh; hand bending + shear for a cantilever "
                  f"wall would be about 4.1e-05 m)", flush=True)
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
