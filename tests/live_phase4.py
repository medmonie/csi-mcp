r"""Live test of 0.12.0 (Phase 4: new model, model from a spec, database-table editing, geometry) in NEW scratch
instances.

    run_live_phase4.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_phase4.txt

Everything comes from the actions, starting with new_model; checks are statics, so they hold whatever the stiffness:
  1 spec     SAP2000 / ETABS: the portal frame of the template (pinned bases, 6 m x 4 m): LIVE 10 kN/m on the beam ->
             reactions add up to 60 kN; WIND 20 kN at the top -> each base takes 10 kN horizontally and +-20 x 4 / 6 =
             13.333 kN vertically. SAFE: 6 x 4 m slab on four pinned corners, 10 kN/m2 -> 240 kN.
             A spec with errors is refused with every problem listed and nothing changed.
  2 tables   Load Pattern Definitions edited through edit_database_table: LIVE self-weight multiplier 0 -> 0.5 (dry run
             diff first): LIVE reactions become 60 (240) + 0.5 x DEAD; a whole-table read in two unit sets differs by
             the length factor; a mismatched `expect` and a results table are refused.
  3 geometry SAP2000: divide the beam in 3 (LIVE total unchanged), replicate the frame 5 m along Y (loads copied ->
             LIVE x 2), mirror / radial copies checked by coordinates, merge a point 0.5 mm away, extrude, reconnect.
             ETABS: move, linear copy (restraints copied, loads listed as not copied), reconnect, refusals. SAFE: move
             and copy of the slab (the copy carries no load: totals unchanged).
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase4_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001
import model_spec  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
EXT = {"SAP2000": ".sdb", "ETABS": ".edb", "SAFE": ".fdb"}


def near(got, want, rel=1e-4, absolute=1e-6):
    return got is not None and abs(got - want) <= rel * abs(want) + absolute


def base(sw, case):
    r = show(f"base reactions {case}", "get_results", {"software": sw, "result_type": "base_reactions",
                                                      "load_cases_or_combos": [case]}, limit=600)
    rows = r.get("rows") if isinstance(r, dict) else None
    return rows[0] if rows else {}


def react(sw, joint, case):
    r = show(f"reaction {joint} {case}", "get_results", {"software": sw, "result_type": "joint_reactions",
                                                        "names": [joint], "load_cases_or_combos": [case]}, limit=600)
    rows = r.get("rows") if isinstance(r, dict) else None
    return rows[0] if rows else {}


def portal_spec(sw):
    import copy
    spec = copy.deepcopy(model_spec.EXAMPLE)
    if sw == "SAFE":
        spec = {"units": "kN_m_C", "materials": spec["materials"],
                "area_sections": [{"name": "SLAB250", "kind": "slab", "material": "C30", "thickness": 0.25}],
                "load_patterns": spec["load_patterns"][:2],
                "points": [{"name": n, "xyz": xyz, "restraint": "pinned"} for n, xyz in
                           (("1", [0, 0, 0]), ("2", [6, 0, 0]), ("3", [6, 4, 0]), ("4", [0, 4, 0]))],
                "areas": [{"name": "S1", "points": ["1", "2", "3", "4"], "section": "SLAB250"}],
                "area_loads": [{"area_names": ["S1"], "load_pattern": "LL", "load_type": "uniform", "value": 10.0,
                                "direction": "gravity"}]}
    for q in spec["points"][:2]:      # pinned in the frame plane (RY free), held out of plane so the 3-D model is stable
        q["restraint"] = [True, True, True, True, False, True]
    return spec


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
        S = {"software": sw}
        path = str(OUT / f"phase4_{sw}{EXT[sw]}")
        nm = {**S, "file_path": path, "discard_open_model": True}
        if sw != "SAP2000":
            nm.update(kind="grid_only", stories=1, story_height=4.0, spacing_x=6.0, spacing_y=4.0)
        show("new model", "new_model", nm, limit=600)
        # ------------------------------------------------------------------ 1 spec
        bad = portal_spec(sw)
        if sw == "SAFE":
            bad["areas"][0]["section"] = "NOPE"
        else:
            bad["frames"][2]["section"] = "NOPE"
        bad["load_patterns"][0]["type"] = "gravity-ish"
        r = show("spec with errors", "build_model_from_spec", {**S, "spec": bad}, expect_error=True, limit=1500)
        hint = str(getattr(r, "hint", ""))
        check("spec errors listed (section and pattern type), nothing changed",
              "NOPE" in hint and "load_patterns[0]" in hint, hint[:400])
        spec = portal_spec(sw)
        d = show("spec dry run", "build_model_from_spec", {**S, "spec": spec, "dry_run": True}, limit=1200)
        b = show("spec build", "build_model_from_spec", {**S, "spec": spec}, limit=2500)
        check("spec completed", isinstance(b, dict) and b.get("completed") is True,
              b.get("failed_step") if isinstance(b, dict) else b)
        # ------------------------------------------------------------------ 2 tables
        lt = show("find the load pattern table", "list_available_tables", {**S, "search_term": "load pattern"},
                  limit=1200)
        key = next((t["key"] for t in lt.get("tables", []) if t["key"].lower().startswith("load pattern def")), None)
        check("load pattern table found", key is not None, key)
        rd = show("table read", "edit_database_table", {**S, "table_key": key}, limit=1500)
        fields = rd["fields"] if isinstance(rd, dict) else []
        sw_field = next((f for f in fields if "selfwt" in f.lower().replace(" ", "")), None)
        check("self weight field", sw_field is not None, fields)
        upd = {"match": {fields[0]: "LL"}, "set": {sw_field: 0.5}, "expect": 1}
        stale = sw == "SAP2000"      # SAP2000 27.1: the table lags the API (diag_phase4): refused, not written
        dr = show("table dry run", "edit_database_table", {**S, "table_key": key, "mode": "edit", "dry_run": True,
                                                           "update": [upd]}, limit=1200, expect_error=stale)
        if stale:
            check("SAP2000: out-of-date table refused before any write", "would delete them" in str(dr), str(dr)[:300])
            show("self weight through the API instead", "define_load_pattern",
                 {**S, "name": "LL", "type": "live", "self_weight_multiplier": 0.5, "replace": True}, limit=500)
            show("save", "open_or_save_model", {**S, "action": "save"}, limit=300)
            show("reopen", "open_or_save_model", {**S, "action": "open", "file_path": path}, limit=300)
            rr = show("table after reopening", "edit_database_table", {**S, "table_key": key}, limit=1500)
            names = [r.get(fields[0]) for r in rr.get("rows", [])] if isinstance(rr, dict) else []
            print(f"record: SAP2000 table after reopening holds {names}", flush=True)
        if isinstance(dr, dict):
            ch = dr["would"]["changed_preview"]
            check("dry run diff: one row, self weight 0 -> 0.5", dr["would"]["rows_changed"] == 1
                  and ch[0].get(sw_field, "").endswith("-> 0.5"), ch)
        show("table expect mismatch", "edit_database_table", {**S, "table_key": key, "mode": "edit",
                                                              "update": [{**upd, "expect": 2}]}, expect_error=True,
             limit=500)
        ap = None if stale else show("table apply", "edit_database_table",
                                     {**S, "table_key": key, "mode": "edit", "update": [upd], "snapshot": True},
                                     limit=1500)
        if isinstance(ap, dict):
            check("table applied and verified", ap.get("applied") and ap["verification"]["rows_verified"] == 1,
                  ap.get("verification"))
            check("other rows kept", ap["rows_in_table_now"] == ap["rows_before"], ap["rows_in_table_now"])
        # units: the coordinates table in m and in mm
        ct = show("find a coordinates table", "list_available_tables", {**S, "search_term": "coordinates"}, limit=800)
        ckey = next((t["key"] for t in ct.get("tables", []) if t.get("import_type") in (1, 2, 3)), None)
        if ckey:
            a_m = show("coordinates in m", "edit_database_table", {**S, "table_key": ckey, "units": "kN_m_C"},
                       limit=800)
            a_mm = show("coordinates in mm", "edit_database_table", {**S, "table_key": ckey, "units": "kN_mm_C"},
                        limit=800)
            ratios = set()
            for r1, r2 in zip(a_m.get("rows", []), a_mm.get("rows", [])):
                for k, v in r1.items():
                    try:
                        x, y = float(v), float(r2[k])
                    except (TypeError, ValueError):
                        continue
                    if abs(x) > 1e-9:
                        ratios.add(round(y / x, 6))
            check(f"'{ckey}' values follow `units` (m -> mm = x 1000)", 1000.0 in ratios, sorted(ratios)[:8])
        else:
            print("no importable coordinates table in this program (units check skipped)", flush=True)
        if sw == "SAP2000":
            show("results table refused", "edit_database_table", {**S, "table_key": "Base Reactions", "mode": "edit",
                                                                  "add_rows": [{"x": 1}]}, expect_error=True,
                 limit=400)
        # ------------------------------------------------------------------ analysis 1
        show("analysis", "run_analysis", {**S}, limit=600)
        dead = base(sw, "DL").get("FZ")
        live = base(sw, "LL").get("FZ")
        q = 240.0 if sw == "SAFE" else 60.0
        check(f"LIVE = {q} + 0.5 x DEAD ({dead})", dead is not None and near(live, q + 0.5 * dead, 1e-5), live)
        if sw != "SAFE":
            r1, r2 = react(sw, "1", "WX"), react(sw, "2", "WX")
            # the split between the bases depends on the beam's axial stiffness; their sum is statics
            check("WIND: base shears add up to 20 kN", near(abs(r1.get("F1", 0) + r2.get("F1", 0)), 20.0, 1e-6),
                  (r1.get("F1"), r2.get("F1")))
            check("WIND: vertical reactions +- 13.333 kN", near(abs(r1.get("F3", 0)), 13.3333333, 1e-4)
                  and near(abs(r2.get("F3", 0)), 13.3333333, 1e-4), (r1.get("F3"), r2.get("F3")))
        # ------------------------------------------------------------------ 3 geometry
        G = {**S, "allow_unlock": True}
        if sw == "SAP2000":
            dv = show("divide beam in 3", "divide_frames", {**G, "frames": ["B1"], "mode": "ratio", "number": 3},
                      limit=1200)
            pieces = [x["frame"] for x in dv["pieces"]["B1"]] if isinstance(dv, dict) else []
            check("3 pieces, 2 m each", len(pieces) == 3 and dv["pieces"]["B1"][0]["j"][0] == 2.0, dv)
            rp = show("replicate frame 5 m along Y", "replicate_objects",
                      {**G, "points": ["1", "2"], "frames": ["C1", "C2", *pieces], "offset": [0, 5, 0], "number": 1},
                      limit=1500)
            check("replicate: 5 frames, 6 points at y = 5", isinstance(rp, dict) and rp["new_counts"]["frames"] == 5
                  and rp["new_counts"]["points"] == 6
                  and all(c[1] == 5.0 for c in rp["new_point_coordinates"].values()), rp)
            mi = show("mirror C1 about x = -3", "replicate_objects",
                      {**G, "frames": ["C1"], "mode": "mirror", "mirror_plane": "parallel_z", "point_1": [-3, 0, 0],
                       "point_2": [-3, 1, 0]}, limit=1000)
            xs = sorted(c[0] for c in mi["new_point_coordinates"].values()) if isinstance(mi, dict) else []
            check("mirror: new column at x = -6", xs == [-6.0, -6.0], mi)
            ra = show("radial C2 90 deg about Z at the origin", "replicate_objects",
                      {**G, "frames": ["C2"], "mode": "radial", "axis": "z", "axis_point": [0, 0, 0], "angle": 90.0,
                       "number": 1}, limit=1000)
            xy = sorted((round(c[0], 6), round(c[1], 6)) for c in ra["new_point_coordinates"].values()) \
                if isinstance(ra, dict) else []
            check("radial: new column at (0, 6)", xy == [(0.0, 6.0), (0.0, 6.0)], ra)
            extra = (mi["new"]["frames"] if isinstance(mi, dict) else []) + (ra["new"]["frames"] if isinstance(ra, dict)
                                                                             else [])
            if extra:
                show("delete the mirror / radial copies (their bases carry no restraint)", "delete_objects",
                     {**G, "kind": "frames", "names": extra}, limit=500)
            cp = show("a point 0.5 mm from point 4: the program merges it on creation", "create_points",
                      {**G, "points": [{"xyz": [6.0005, 0, 4], "name": "NEAR"}]}, limit=400, expect_error=True)
            check("merge on creation reported", "merged points on creation" in str(cp), str(cp)[:300])
            show("record: points after the edits", "get_objects", {**S, "object_type": "points"}, limit=4000)
            show("two free points 10 mm apart", "create_points", {**G, "points": [{"xyz": [50, 0, 0], "name": "Q1"},
                                                                               {"xyz": [50.01, 0, 0], "name": "Q2"}]},
                 limit=300)
            md = show("merge dry run", "merge_points", {**G, "tolerance": 0.02, "dry_run": True}, limit=600)
            mg = show("merge", "merge_points", {**G, "tolerance": 0.02}, limit=600)
            check("merge removed Q1 or Q2", isinstance(mg, dict) and mg["points_removed_count"] >= 1
                  and set(mg["points_removed"]) & {"Q1", "Q2"}, mg)
            show("point to extrude", "create_points", {**G, "points": [{"xyz": [20, 0, 0], "name": "X0"}],
                                                       "restraint": "fixed"}, limit=300)
            ex = show("extrude 2 frames up", "extrude_objects", {**G, "kind": "points_to_frames", "names": ["X0"],
                                                                 "section": "COL400", "offset": [0, 0, 2], "number": 2},
                      limit=800)
            check("extrude: 2 frames", isinstance(ex, dict) and ex["new_total"] == 2, ex)
            show("analysis 2", "run_analysis", {**S}, limit=400)
            live2 = base(sw, "LL").get("FZ")
            dead2 = base(sw, "DL").get("FZ")
            check("after divide + replicate: LIVE = 2 x 60 + 0.5 x DEAD (beam load copied with the frame)",
                  near(live2, 120.0 + 0.5 * dead2, 1e-5), (live2, dead2))
            show("SAP2000 refuses nothing above; ETABS-only checks follow in ETABS", "get_model_status", {**S},
                 limit=200)
        elif sw == "ETABS":
            mv = show("move beam up 0.5 m", "move_objects", {**G, "frames": ["B1"], "offset": [0, 0, 0.5]}, limit=800)
            check("move: points 3, 4 at z = 4.5", isinstance(mv, dict) and mv["points_moved"] == 2
                  and all(m["after"][2] == 4.5 for m in mv["moved"]), mv)
            show("move back", "move_objects", {**G, "frames": ["B1"], "offset": [0, 0, -0.5]}, limit=400)
            rp = show("copy the frame 5 m along Y", "replicate_objects", {**G, "frames": ["C1", "C2", "B1"],
                                                                         "offset": [0, 5, 0]}, limit=1500)
            ok = isinstance(rp, dict) and rp["new_counts"]["frames"] == 3 and "loads" in rp["not_copied"]
            check("copy: 3 frames, loads listed as not copied", ok, rp)
            if ok:
                base_pts = [n for n, c in rp["new_point_coordinates"].items() if c[2] == 0.0]
                ag = show("restraints of point 1 and the copies", "get_object_assignments",
                          {**S, "object_type": "points", "names": ["1", *base_pts]}, limit=800)
                rs = [i.get("restraint") for i in ag.get("items", [])] if isinstance(ag, dict) else []
                check("copied base points restrained like point 1", len(rs) == 3 and rs[0] and len(set(rs)) == 1, rs)
            for title, act, prm in (("divide refused", "divide_frames", {"frames": ["B1"], "mode": "ratio"}),
                                    ("merge refused", "merge_points", {"tolerance": 0.001}),
                                    ("mirror refused", "replicate_objects", {"frames": ["C1"], "mode": "mirror",
                                                                             "mirror_plane": "parallel_z",
                                                                             "point_1": [0, 0, 0],
                                                                             "point_2": [0, 1, 0]})):
                show(title, act, {**G, **prm}, expect_error=True, limit=400)
            show("analysis 2", "run_analysis", {**S}, limit=400)
            live2, dead2 = base(sw, "LL").get("FZ"), base(sw, "DL").get("FZ")
            check("after the copy: LIVE = 60 + 0.5 x DEAD (the copy carries self weight, no beam load)",
                  near(live2, 60.0 + 0.5 * dead2, 1e-5), (live2, dead2))
        else:
            mv = show("move slab 1 m along X", "move_objects", {**G, "areas": ["S1"], "offset": [1, 0, 0]}, limit=800)
            check("move: 4 corners moved by 1 m", isinstance(mv, dict) and mv["points_moved"] == 4
                  and all(abs(m["after"][0] - m["before"][0] - 1.0) < 1e-9 for m in mv["moved"]), mv)
            def raw_restraints():
                sap = bridge.sap_model("SAFE")
                out = {}
                for q in ("1", "2"):
                    for label, call_ in (("(name)", lambda: sap.PointObj.GetRestraint(q)),
                                         ("(name, [F]*6)", lambda: sap.PointObj.GetRestraint(q, [False] * 6))):
                        try:
                            out[q + " " + label] = call_()
                        except Exception as exc:  # noqa: BLE001
                            out[q + " " + label] = f"{type(exc).__name__}: {exc}"
                return out
            print(f"record: SAFE raw GetRestraint of the slab corners: {com(raw_restraints)}", flush=True)
            show("original corners' restraints", "get_object_assignments",
                 {**S, "object_type": "points", "names": ["1", "2", "3", "4"]}, limit=800)
            rp = show("copy the slab 10 m along X", "replicate_objects", {**G, "areas": ["S1"], "offset": [10, 0, 0]},
                      limit=1200)
            check("copy: one slab with 4 new corners", isinstance(rp, dict) and rp["new_counts"]["areas"] == 1
                  and rp["new_counts"]["points"] == 4, rp)
            show("analysis 2", "run_analysis", {**S}, limit=400)
            live2, dead2 = base(sw, "LL").get("FZ"), base(sw, "DL").get("FZ")
            new_area = (rp.get("new", {}).get("areas") or [None])[0] if isinstance(rp, dict) else None
            ag = show("the copy's section and corners", "get_object_assignments",
                      {**S, "object_type": "areas", "names": ["S1", new_area]}, limit=1200) if new_area else None
            items = ag.get("items", []) if isinstance(ag, dict) else []
            check("copy: same section as S1", len(items) == 2 and items[0].get("property") == items[1].get("property"),
                  [i.get("property") for i in items])
            pa = show("corner restraints", "get_object_assignments",
                      {**S, "object_type": "points", "names": rp["new"]["points"]}, limit=800) if new_area else None
            rs = [i.get("restraint") for i in pa.get("items", [])] if isinstance(pa, dict) else []
            check("copy: each corner restrained like the original corner",
                  rs == ["U1 U2 U3 R1 R3", "U1 U2 U3 R1 R3", "pinned", "pinned"], rs)
            check("after the copy: DEAD doubles, LIVE = 240 + 0.5 x DEAD (the copy carries no live load)",
                  near(dead2, 2 * dead, 1e-5) and near(live2, 240.0 + 0.5 * dead2, 1e-5), (live2, dead2, dead))
        cc = show("change connectivity dry run", "change_connectivity",
                  {**G, "frame": "C1", "points": ["1", "3"], "dry_run": True}, limit=300,
                  expect_error=sw == "SAFE")
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
