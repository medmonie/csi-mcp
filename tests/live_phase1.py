r"""Live test of 0.4.0 (Phase 1 model reading + report R2 diagrams) in NEW scratch instances.

    run_live_phase1.bat [ETABS] [SAP2000] [SAFE] [--hidden]      Output: tests\results\live_phase1.txt

Per program: build the reference model (tests/reference_models.py), then read it back with every Phase 1 action and
compare with the program's own per-object API answers (round trip: no missing objects, end points, area corners,
supports, springs, loads, sections, combinations), export the whole model to JSON, and export a report with the
model diagrams. The scratch instance is closed without saving. Output folder: tests\results\phase1_output.
"""
import json
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "phase1_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001
from csi_bridge import api_call, as_list  # noqa: E402
from reference_models import BUILDERS  # noqa: E402

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
BIG = 5000


def raw_truth(sw):
    """What the program says object by object (independent of the bulk readers model_read uses)."""
    sap = bridge.sap_model(sw)
    t = {"points": bridge.name_list(sap, "PointObj"), "frames": {}, "areas": {}, "restrained": set()}
    for n in t["points"]:
        if any(as_list(api_call("PointObj.GetRestraint", sap.PointObj.GetRestraint, n)[0])):
            t["restrained"].add(n)
    for n in bridge.name_list(sap, "FrameObj"):
        t["frames"][n] = tuple(str(x) for x in api_call("FrameObj.GetPoints", sap.FrameObj.GetPoints, n, "", "")[:2])
    for n in bridge.name_list(sap, "AreaObj"):
        t["areas"][n] = [str(x) for x in as_list(api_call("AreaObj.GetPoints", sap.AreaObj.GetPoints, n)[1])]
    t["frame_sections"] = len(bridge.name_list(sap, "PropFrame"))
    return t


def same_ring(a, b):
    """Same closed boundary, whatever the start point."""
    if len(a) != len(b) or set(a) != set(b):
        return False
    i = b.index(a[0])
    return a == b[i:] + b[:i] or a == list(reversed(b[i + 1:] + b[:i + 1]))


if OUT.exists():
    shutil.rmtree(OUT)
OUT.mkdir(parents=True)
(HERE / "results" / "reports").mkdir(parents=True, exist_ok=True)

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
        try:
            info = com(BUILDERS[sw], bridge.sap_model(sw))
            print("reference model:", json.dumps(info, default=str)[:600], flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"FAILED reference model {sw}: {type(exc).__name__}: {exc}", flush=True)
            FAILED.append(f"{sw} reference model")
            continue
        truth = com(raw_truth, sw)
        print(f"truth: {len(truth['points'])} points, {len(truth['frames'])} frames, {len(truth['areas'])} areas, "
              f"{len(truth['restrained'])} restrained", flush=True)

        # ---- objects: nothing missing, connectivity identical
        o = show(f"{sw} get_objects", "get_objects", {"software": sw, "max_rows": BIG}, limit=1200)
        if isinstance(o, dict):
            pts = {p["name"]: p for p in o["points"]["items"]}
            frs = {f["name"]: f for f in o["frames"]["items"]}
            ars = {a["name"]: a for a in o["areas"]["items"]}
            check(f"{sw} every point read", set(pts) == set(truth["points"]), len(pts))
            check(f"{sw} every frame read with its end points",
                  set(frs) == set(truth["frames"]) and all((frs[n]["point1"], frs[n]["point2"]) == e
                                                           for n, e in truth["frames"].items()), len(frs))
            bad = [n for n, ring in truth["areas"].items() if n not in ars or not same_ring(ars[n]["points"].split(), ring)]
            check(f"{sw} every area read with its corner points", not bad and len(ars) == len(truth["areas"]), bad[:5])
            check(f"{sw} supports", {n for n, p in pts.items() if p["restraint"]} == truth["restrained"],
                  o.get("supports"))
            if sw == "SAP2000":
                check(f"{sw} plate area 16 m2", ars[info["areas"][0]]["area"] == 16, ars[info["areas"][0]]["area"])
            if sw == "SAFE":
                check(f"{sw} panels 36 m2", all(ars[a]["area"] == 36 for a in info["areas"]))

        # ---- assignments and loads
        ar = show(f"{sw} area assignments", "get_object_assignments",
                  {"software": sw, "object_type": "areas", "max_rows": BIG}, limit=1500)
        if isinstance(ar, dict):
            byname = {a["name"]: a for a in ar["items"]}
            want = {"SAP2000": {info["dead"]: 2.0, "LIVE": 3.0}, "ETABS": {"Live": 2.0}, "SAFE": {"Live": 3.0}}[sw]
            ok = all({u["pattern"]: abs(u["value"]) for u in byname[a].get("uniform_loads", [])} == want
                     for a in info["areas"])
            check(f"{sw} area uniform loads round trip {want}", ok, byname[info["areas"][0]].get("uniform_loads"))
            if sw == "SAFE":
                check(f"{sw} soil springs on every panel", all(byname[a].get("spring_property") == "SOIL"
                                                                for a in info["areas"]))
        fr = show(f"{sw} frame assignments", "get_object_assignments",
                  {"software": sw, "object_type": "frames", "max_rows": BIG}, limit=1500)
        if isinstance(fr, dict) and sw == "SAP2000":
            byname = {a["name"]: a for a in fr["items"]}
            check(f"{sw} beam distributed loads round trip", all(
                any(d["pattern"] == "LIVE" and abs(d["values"][0]) == 10 for d in byname[b].get("distributed_loads", []))
                for b in info["beams"]), len(info["beams"]))
        pt = show(f"{sw} point assignments", "get_object_assignments",
                  {"software": sw, "object_type": "points", "max_rows": BIG}, limit=1500)
        if isinstance(pt, dict):
            restrained = {a["name"] for a in pt["items"] if a.get("restraint")}
            check(f"{sw} point restraints round trip", restrained == truth["restrained"], len(restrained))
            if sw == "SAFE":
                loaded = {a["name"]: a["loads"] for a in pt["items"] if a.get("loads")}
                check(f"{sw} column point loads round trip", set(loaded) == set(info["column_points"]) and all(
                    v[0]["F1_F2_F3_M1_M2_M3"][2] == -500 for v in loaded.values()), len(loaded))

        # ---- definitions, cases, combinations, grids, stories, groups, mass, selection
        m = show(f"{sw} materials", "get_definition_details", {"software": sw, "kind": "materials"}, limit=1200)
        if isinstance(m, dict):
            c30 = next((x for x in m["items"] if x["name"] == "C30"), {})
            check(f"{sw} C30 is concrete with E > 0", c30.get("type") == "Concrete" and (c30.get("E") or 0) > 0, c30)
        fs = show(f"{sw} frame sections", "get_definition_details", {"software": sw, "kind": "frame_sections",
                                                                      "max_rows": 20}, limit=1500)
        if isinstance(fs, dict):
            check(f"{sw} every frame section read", fs["count"] == truth["frame_sections"],
                  (fs["count"], truth["frame_sections"]))
        asec = show(f"{sw} area sections", "get_definition_details", {"software": sw, "kind": "area_sections"},
                    limit=1500)
        if isinstance(asec, dict) and sw in ("SAP2000", "SAFE"):
            name, th = ("SHELL200", 0.2) if sw == "SAP2000" else ("MAT500", 0.5)
            got = next((x for x in asec["items"] if x["name"] == name), {})
            check(f"{sw} {name} thickness {th}", got.get("thickness") == th, got)
        show(f"{sw} load patterns", "get_definition_details", {"software": sw, "kind": "load_patterns"}, limit=800)
        if sw != "SAP2000":
            show(f"{sw} springs", "get_definition_details", {"software": sw, "kind": "springs"}, limit=600)
        lc = show(f"{sw} load cases", "get_load_case_details", {"software": sw}, limit=1500)
        if isinstance(lc, dict):
            dead = next((c for c in lc["load_cases"] if c["name"] == info["dead"]), {})
            check(f"{sw} {info['dead']} case = pattern {info['dead']} x 1", any(
                l["name"] == info["dead"] and l["factor"] == 1 for l in dead.get("loads", [])), dead)
        cb = show(f"{sw} combinations", "get_combo_details", {"software": sw}, limit=1500)
        if isinstance(cb, dict):
            want = {"SAP2000": ("COMB2", {info["dead"]: 1.2, "LIVE": 1.6})}.get(sw, ("COMB1", {"Dead": 1.2, "Live": 1.6}))
            got = next((c for c in cb["combinations"] if c["name"] == want[0]), {})
            check(f"{sw} {want[0]} factors round trip", {i["name"]: i["factor"] for i in got.get("items", [])} == want[1],
                  got)
        g = show(f"{sw} grids", "get_grids", {"software": sw}, limit=1500)
        if isinstance(g, dict) and sw != "SAP2000":
            gs = g["grid_systems"][0]
            check(f"{sw} grid 3 x 3 lines at 6 m", len(gs["x_lines"]) == 3 and len(gs["y_lines"]) == 3 and
                  gs["x_lines"][1]["ordinate"] == 6, gs)
        if sw != "SAP2000":
            s = show(f"{sw} stories", "get_stories", {"software": sw}, limit=1200)
            if isinstance(s, dict):
                check(f"{sw} stories", len(s["stories_top_down"]) == (3 if sw == "ETABS" else 1), s["stories_top_down"])
        else:
            show(f"{sw} stories refused", "get_stories", {"software": sw}, expect_error=True, limit=300)
        show(f"{sw} groups", "get_groups", {"software": sw}, limit=800)
        show(f"{sw} mass source", "get_mass_source", {"software": sw}, limit=800)
        show(f"{sw} selection", "get_selected", {"software": sw}, limit=500)

        # ---- whole model to JSON
        snap = show(f"{sw} export_model_snapshot", "export_model_snapshot", {"software": sw}, limit=1200)
        if isinstance(snap, dict):
            c = snap["counts"]
            check(f"{sw} snapshot complete", Path(snap["file"]).is_file() and c["points"] == len(truth["points"]) and
                  c["frames"] == len(truth["frames"]) and c["areas"] == len(truth["areas"]) and
                  c["assignments"]["areas"] == len(truth["areas"]), c)

        # ---- report with diagrams (R2)
        r = show(f"{sw} export_report with diagrams", "export_report",
                 {"software": sw, "sections": ["cover", "model", "diagrams", "definitions"],
                  "project_info": {"title": f"{sw} reference model - diagrams", "project": "CSI MCP Phase 1"}},
                 limit=1500)
        if isinstance(r, dict):
            check(f"{sw} diagrams drawn (overview + plans + loads)", r["figures"] >= 3, r["figures"])
            shutil.copy(r["report"], HERE / "results" / "reports" / f"{sw}_diagrams_A4.pdf")
        r3 = show(f"{sw} export_report diagrams A3 landscape", "export_report",
                  {"software": sw, "sections": ["diagrams"], "page_size": "A3", "orientation": "landscape"}, limit=800)
        if isinstance(r3, dict):
            shutil.copy(r3["report"], HERE / "results" / "reports" / f"{sw}_diagrams_A3.pdf")
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"could not close the scratch {sw}: {exc}", flush=True)
    print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)

sys.exit(finish())
