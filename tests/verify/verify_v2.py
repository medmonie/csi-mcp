r"""V2 - rebuild CSI verification examples through the MCP edit actions and check them against CSI's published values.

    run_verify_v2.bat [SAP2000] [ETABS] [--hidden]
    Output: tests\results\verify_v2.txt, tests\results\verify\v2\<model>.rebuild.json

For each model: open a fresh copy (originals untouched) -> read it with export_model_snapshot (the read actions) ->
new blank model (raw API: only InitializeNewModel / NewBlank / NewGridOnly with the example's stories) -> rebuild
every material, section, pattern, point, frame, assignment, load, case and combination through the edit actions ->
analyse -> grade every check of expected_v1 for that model, as V1 does (MATCH / CLOSE / DIFF / MISSING).
Anything the actions cannot rebuild is listed (that is a finding, not a skip).
"""
import json
import math
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(HERE))
OUT = TESTS / "results" / "verify" / "v2"
os.environ["CSI_MCP_OUTPUT"] = str(OUT / "output")

from live_common import FAILED, bridge, com, finish  # noqa: E402,I001
from csi_bridge import api_call  # noqa: E402
from expected_v1 import CHECKS  # noqa: E402
from verify_models import model_copy  # noqa: E402
import server  # noqa: E402

V2 = {"SAP2000": ["Example 1-001"], "ETABS": ["Example 01"], "SAFE": ["S03a-Thin"]}
READ_ONLY: set = set()      # programs whose example is only read (none now)
UNIT = "kip_in_F"
UNIT_CODE = 3            # eUnits kip_in_F
args = sys.argv[1:]
visible = "--hidden" not in args
programs = [a.upper() for a in args if a.upper() in V2] or list(V2)
MAT_KIND = {"Concrete": "concrete", "Steel": "steel", "Rebar": "rebar", "Tendon": "tendon", "Aluminum": "aluminum",
            "ColdFormed": "cold_formed", "NoDesign": "other"}
DOF = ("U1", "U2", "U3", "R1", "R2", "R3")
REST = {"fixed": [True] * 6, "pinned": [True, True, True, False, False, False], "": [False] * 6}


def run(action, params):
    try:
        return server.executor.run(server.dispatch, action, params)
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {getattr(exc, 'payload', None) or exc}"}


def restraint(text):
    if text in REST:
        return REST[text]
    toks = set(text.split())
    return [d in toks for d in DOF]


# ----------------------------------------------------------------------- grading (same rules as verify_v1)
def printed_tol(v):
    if float(v) == 0.0:
        return 5e-4
    if float(v).is_integer() and abs(v) < 1000:
        return 0.0
    s = repr(float(v))
    if "e" in s:
        mant, exp = s.split("e")
        dec = len(mant.split(".")[1]) if "." in mant else 0
        return 0.5 * 10 ** (int(exp) - dec)
    dec = len(s.split(".")[1].rstrip("0")) if "." in s else 0
    return 0.5 * 10 ** (-dec)


def grade(ours, pub, scale=0.0):
    diff = ours - pub
    if pub == 0 and scale and abs(ours) <= 1e-5 * scale:
        return "MATCH", 0.0
    rel = abs(diff) / abs(pub) if pub else (0.0 if abs(diff) < 1e-9 else math.inf)
    if abs(diff) <= printed_tol(pub) * 1.001 + 1e-12 or rel <= 5e-4:
        return "MATCH", rel
    if rel <= 0.01:
        return "CLOSE", rel
    return "DIFF", rel


def elm_coords(sw, names):
    sap = bridge.sap_model(sw)
    out = {}
    for n in names:
        try:
            c = api_call("PointElm.GetCoordCartesian", sap.PointElm.GetCoordCartesian, n, 0.0, 0.0, 0.0)
            out[n] = (float(c[0]), float(c[1]), float(c[2]))
        except Exception:  # noqa: BLE001
            pass
    return out


def pick(check, rows, frames_len, sw="", obj_xy=None):
    sel, comp = check["sel"], check["comp"]
    rows = [r for r in rows if str(r.get("LoadCase")) == check["case"]]
    if "joint" in sel:
        rows = [r for r in rows if str(r.get("Joint")) == sel["joint"]]
    elif "frame" in sel:
        rows = [r for r in rows if str(r.get("Frame")) == sel["frame"]]
        L = frames_len.get(sel["frame"])
        if sel["at"] != "absmax" and L:
            near = [r for r in rows if abs(float(r["Station"]) - sel["at"] * L) <= 1e-3 * L + 1e-6]
            if not near:
                return None, f"no output station at {sel['at'] * L:g}"
            rows = near
    elif "xy" in sel:
        x, y = sel["xy"]
        if sw == "SAFE":
            xyz, key = com(elm_coords, sw, sorted({str(r["Element"]) for r in rows})), "Element"
        else:
            xyz, key = obj_xy or {}, "Joint"
        if not xyz:
            return None, "no joint coordinates"
        best = min(xyz, key=lambda n: (xyz[n][0] - x) ** 2 + (xyz[n][1] - y) ** 2)
        if math.dist(xyz[best][:2], (x, y)) > 1e-2 * max(1.0, abs(x), abs(y)):
            return None, f"no joint at ({x}, {y}); nearest {best} at {xyz[best][:2]}"
        rows = [r for r in rows if str(r[key]) == best]
    if not rows:
        return None, "no row"
    vals = [float(r[comp]) for r in rows if r.get(comp) is not None]
    if not vals:
        return None, f"no {comp}"
    v = max(vals, key=abs) if (check["abs"] or len(vals) > 1) else vals[0]
    return (abs(v) if check["abs"] else v), ""


# ----------------------------------------------------------------------- rebuild
def blank(sw, stories):
    sap = bridge.sap_model(sw)
    api_call("InitializeNewModel", sap.InitializeNewModel, UNIT_CODE)
    if sw == "SAP2000":
        api_call("File.NewBlank", sap.File.NewBlank)
    elif sw == "SAFE":           # SAFE 23.3 draws new objects only at the base of a grid-only model (measured)
        api_call("File.NewGridOnly", sap.File.NewGridOnly, 1, 144.0, 144.0, 2, 2, 100.0, 100.0)
    else:
        h = stories[0]["elevation"] if stories else 120.0
        api_call("File.NewGridOnly", sap.File.NewGridOnly, max(1, len(stories)), h, h, 2, 2, 100.0, 100.0)


def rebuild(sw, snap, log):
    S = {"software": sw, "units": UNIT}
    S0 = {"software": sw}
    a = snap["assignments"]
    used_mats = {f["material"] for f in snap["frame_sections"]} | {x["material"] for x in snap["area_sections"]
                                                                   if x["name"] in {ar["property"] for ar in snap["areas"]}}
    used_secs = {f["section"] for f in snap["frames"]}

    def step(title, action, params):
        r = run(action, params)
        ok = "_error" not in r
        log.append({"step": title, "ok": ok, **({} if ok else {"error": str(r["_error"])[:400]})})
        if not ok:
            print(f"   REBUILD FAILED {title}: {str(r['_error'])[:300]}", flush=True)
        return r if ok else None

    for m in snap["materials"]:
        if m["name"] not in used_mats:
            continue
        kind = MAT_KIND.get(m["type"], "other")
        p = {**S, "name": m["name"], "type": kind, "E": m["E"], "poisson": m["poisson"],
             "unit_weight": m["weight_per_volume"], "thermal_coeff": m["thermal_coeff"], "replace": True}
        if kind == "concrete":
            p["fc"] = m.get("fc") or 4.0
        if kind in ("steel", "rebar", "tendon"):
            p.update(fy=m.get("fy"), fu=m.get("fu"))
        step(f"material {m['name']}", "define_material", p)
    used_area_secs = {ar["property"] for ar in snap["areas"]}
    for x in snap["area_sections"]:
        if x["name"] not in used_area_secs:
            continue
        p = {**S, "name": x["name"], "material": x["material"], "thickness": x["thickness"], "replace": True,
             "kind": x["kind"] if x["kind"] in ("slab", "wall") else {1: "shell_thin", 2: "shell_thick", 3: "plate_thin",
                                                                       4: "plate_thick", 5: "membrane"}.get(
                 x.get("shell_type"), "shell_thin")}
        if x.get("bending_thickness") and x["kind"] not in ("slab", "wall"):
            p["bending_thickness"] = x["bending_thickness"]
        step(f"area section {x['name']}", "define_area_section", p)
    used_springs = {fa.get("line_spring_property") for fa in a["frames"]} | {
        x.get("spring_property") for x in a["areas"]} | {x.get("spring_property") for x in a["points"]}
    for sp in snap.get("spring_properties", []):
        if sp["name"] not in used_springs:
            continue
        step(f"spring {sp['name']}", "define_spring_property",
             {**S, "name": sp["name"], "kind": sp["kind"], "stiffness": sp["stiffness"], "replace": True,
              **({"nonlinear": sp["nonlinear"]} if sp.get("nonlinear") else {})})
    for f in snap["frame_sections"]:
        if f["name"] not in used_secs:
            continue
        p = {**S, "name": f["name"], "material": f["material"], "replace": True}
        if f["type"] == "Rectangular":
            p.update(shape="rectangle", depth=f["t3_depth"], width=f["t2_width"])
        elif f["type"] == "Circle":
            p.update(shape="circle", depth=f["t3_depth"])
        elif f["type"] == "General":
            pr = f["properties"]
            p.update(shape="general", depth=f["t3_depth"], width=f["t2_width"], general_properties=pr)
        else:
            log.append({"step": f"section {f['name']}", "ok": False, "error": f"type {f['type']} not mapped"})
            continue
        if f.get("modifiers"):
            p["modifiers"] = list(f["modifiers"].values())
        step(f"section {f['name']}", "define_frame_section", p)
    for lp in snap["load_patterns"]:
        if lp["name"].startswith("~"):
            continue
        step(f"pattern {lp['name']}", "define_load_pattern",
             {**S0, "name": lp["name"], "type": lp["type"].lower(), "replace": True,
              "self_weight_multiplier": lp["self_weight_multiplier"]})
    used_pts = {f[k] for f in snap["frames"] for k in ("point1", "point2")} | {
        q for ar in snap["areas"] for q in str(ar.get("points", "")).split()}
    for pt in snap["points"]:
        loaded = any(pa["name"] == pt["name"] and pa.get("loads") for pa in a["points"])
        if pt["name"] not in used_pts and not pt["restraint"] and not loaded:
            log.append({"step": f"point {pt['name']}", "ok": True, "note": "not connected, no load: not rebuilt"})
            continue
        r = step(f"point {pt['name']}", "create_points",
                 {**S, "points": [{"xyz": [pt["x"], pt["y"], pt["z"]], "name": pt["name"]}],
                  "restraint": restraint(pt["restraint"])})
        if r and r["point_names"] != [pt["name"]]:
            log.append({"step": f"point {pt['name']}", "ok": False, "error": f"got name {r['point_names']}"})
    for ar in snap["areas"]:
        step(f"area {ar['name']}", "create_areas", {**S, "section": ar["property"], "areas": [
            {"points": ar["points"].split(), "name": ar["name"]}]})
    for aa in a["areas"]:
        for ld in aa.get("uniform_loads", []):
            step(f"area {aa['name']} load {ld['pattern']}", "assign_area_load",
                 {**S, "area_names": [aa["name"]], "load_pattern": ld["pattern"], "load_type": "uniform",
                  "value": ld["value"], "direction": ld["direction"], "replace": False})
        if aa.get("spring_property"):
            step(f"area {aa['name']} spring", "assign_area_properties",
                 {**S, "area_names": [aa["name"]], "spring_property": aa["spring_property"]})
    for fr in snap["frames"]:
        step(f"frame {fr['name']}", "create_frames", {**S, "section": fr["section"] or "none", "frames": [
            {"start": fr["point1"], "end": fr["point2"], "name": fr["name"]}]})
    for fa in a["frames"]:
        rel = {}
        if fa.get("releases_i") or fa.get("releases_j"):
            rel = {"releases": {"i": fa.get("releases_i", "").split(), "j": fa.get("releases_j", "").split()}}
        extra = {}
        if fa.get("modifiers"):
            extra["modifiers"] = list(fa["modifiers"].values())
        if fa.get("local_axis_angle"):
            extra["local_axis_angle"] = fa["local_axis_angle"]
        if fa.get("output_stations"):
            extra["output_stations"] = fa["output_stations"]
        if fa.get("end_offsets"):
            extra["end_offsets"] = fa["end_offsets"]
        if fa.get("line_spring_property"):
            extra["spring_property"] = fa["line_spring_property"]
        if rel or extra:
            step(f"frame {fa['name']} properties", "assign_frame_properties",
                 {**S, "frame_names": [fa["name"]], **rel, **extra})
        for dl in fa.get("distributed_loads", []):
            step(f"frame {fa['name']} distributed {dl['pattern']}", "assign_point_or_frame_load",
                 {**S, "target_type": "frame", "object_names": [fa["name"]], "load_pattern": dl["pattern"],
                  "values": dl["values"] + dl["rel_dist"], "direction": dl["direction"], "replace": False,
                  "frame_load_type": dl["type"]})
        for pl in fa.get("point_loads", []):
            step(f"frame {fa['name']} point {pl['pattern']}", "assign_point_or_frame_load",
                 {**S, "target_type": "frame", "object_names": [fa["name"]], "load_pattern": pl["pattern"],
                  "frame_load_shape": "point", "values": [pl["value"], pl["rel_dist"]], "direction": pl["direction"],
                  "replace": False, "frame_load_type": pl["type"]})
    for pa in a["points"]:
        if pa["name"] not in used_pts and not pa.get("loads"):
            continue
        props = {}
        if pa.get("diaphragm") and sw != "SAP2000":
            props["diaphragm"] = pa["diaphragm"].strip()
        if pa.get("spring_K"):
            props["spring_stiffness"] = pa["spring_K"]
        if pa.get("mass"):
            props["mass"] = pa["mass"]
        if props:
            step(f"point {pa['name']} properties", "assign_point_properties", {**S, "point_names": [pa["name"]],
                                                                               **props})
        for ld in pa.get("loads", []):
            if ld["csys"].upper() != "GLOBAL":
                log.append({"step": f"point {pa['name']} load", "ok": False, "error": f"csys {ld['csys']}"})
                continue
            step(f"point {pa['name']} load {ld['pattern']}", "assign_point_or_frame_load",
                 {**S, "target_type": "point", "object_names": [pa["name"]], "load_pattern": ld["pattern"],
                  "values": ld["F1_F2_F3_M1_M2_M3"], "replace": False})
    pats = {lp["name"] for lp in snap["load_patterns"]}
    for c in snap["load_cases"]:
        if c["name"].startswith("~") or c["type"] != "LinearStatic":
            continue
        if c["name"] in pats and c.get("loads") == [{"type": "Load", "name": c["name"], "factor": 1.0}]:
            continue                       # made with its pattern
        step(f"case {c['name']}", "define_load_case", {**S0, "name": c["name"], "type": "linear_static",
                                                       "replace": True, "loads": [
            {"kind": "load_pattern" if x["type"] == "Load" else "acceleration", "name": x["name"],
             "factor": x["factor"]} for x in c["loads"]]})
    ao = snap.get("analysis_options", {})
    now = run("set_analysis_options", {**S0, "run_cases": {"*": True}, "dry_run": True})
    have = set((now.get("would") or now).get("before", {}).get("run_cases", {})) if "_error" not in now else set()
    wanted = {c: v for c, v in ao.get("run_cases", {}).items() if c in have}
    opts = {"run_cases": {"*": False, **{c: v for c, v in wanted.items() if v}}}
    if ao.get("floor_mesh"):
        opts["floor_mesh"] = ao["floor_mesh"]
        S0u = {**S}
        opts.update(units=S0u["units"])
    if ao.get("active_dof") and sw != "SAFE":
        opts["active_dof"] = ao["active_dof"]
    step("analysis options", "set_analysis_options", {**S0, **opts})
    for cb in snap["combinations"]:
        step(f"combination {cb['name']}", "define_combination",
             {**S0, "name": cb["name"], "replace": True, "type": {"LinearAdd": "linear_add", "Envelope": "envelope",
                                                                   "AbsAdd": "absolute_add", "SRSS": "srss",
                                                                   "RangeAdd": "range_add"}.get(cb["type"], "linear_add"),
              "items": [{"name": i["name"], "factor": i["factor"], "kind": i["kind"]} for i in cb["items"]]})


# ----------------------------------------------------------------------- raw comparison (diagnostic)
def _t(sap, path, *a):
    obj = sap
    for part in path.split("."):
        obj = getattr(obj, part)
    try:
        r = list(obj(*a))
        return [tuple(round(float(y), 9) if isinstance(y, float) else y for y in x) if isinstance(x, tuple)
                else (round(float(x), 9) if isinstance(x, float) else x) for x in r]
    except Exception as exc:  # noqa: BLE001
        return f"err {exc}"


def raw_props(sw, frames, points):
    """What the model holds per frame / point / section / material, straight from the API, for the diff."""
    sap = bridge.sap_model(sw)
    api_call("SetPresentUnits", sap.SetPresentUnits, UNIT_CODE)
    out = {}
    for f in frames:
        sec = _t(sap, "FrameObj.GetSection", f)
        out[f"frame {f} section"] = sec
        sname = sec[0] if isinstance(sec, list) else ""
        out[f"frame {f} releases"] = _t(sap, "FrameObj.GetReleases", f)
        out[f"frame {f} offsets"] = _t(sap, "FrameObj.GetEndLengthOffset", f)
        out[f"frame {f} modifiers"] = _t(sap, "FrameObj.GetModifiers", f, [0.0] * 8)
        out[f"frame {f} insertion"] = _t(sap, "FrameObj.GetInsertionPoint", f)
        out[f"frame {f} stations"] = _t(sap, "FrameObj.GetOutputStations", f)
        out[f"frame {f} points"] = _t(sap, "FrameObj.GetPoints", f)
        if sw != "SAP2000":
            sp = _t(sap, "FrameObj.GetSpringAssignment", f)
            out[f"frame {f} line spring"] = sp
            if isinstance(sp, list) and sp[0] not in ("", "None"):
                out[f"line spring {sp[0]}"] = _t(sap, "PropLineSpring.GetLineSpringProp", sp[0])
        out[f"section {sname} props"] = _t(sap, "PropFrame.GetSectProps", sname)
        out[f"section {sname} modifiers"] = _t(sap, "PropFrame.GetModifiers", sname, [0.0] * 8)
        mat = _t(sap, "PropFrame.GetMaterial", sname)
        m = mat[0] if isinstance(mat, list) else ""
        out[f"material {m} iso"] = _t(sap, "PropMaterial.GetMPIsotropic", m)
        out[f"material {m} weight"] = _t(sap, "PropMaterial.GetWeightAndMass", m)
    for pt in points:
        out[f"point {pt} restraint"] = _t(sap, "PointObj.GetRestraint", pt)
        out[f"point {pt} spring"] = _t(sap, "PointObj.GetSpring", pt)
        out[f"point {pt} coord"] = _t(sap, "PointObj.GetCoordCartesian", pt)
    if sw == "SAFE":
        for ar in (bridge.name_list(sap, "AreaObj") or [])[:40]:
            out[f"area {ar} property"] = _t(sap, "AreaObj.GetProperty", ar)
            out[f"area {ar} points"] = _t(sap, "AreaObj.GetPoints", ar)
            out[f"area {ar} load"] = _t(sap, "AreaObj.GetLoadUniform", ar)
    out["active dof"] = _t(sap, "Analyze.GetActiveDOF")
    return out


def mesh_tables(sw):
    """Diagnostic: the tables that hold meshing / analysis options (SAFE meshes with options the API lacks)."""
    if sw != "SAFE":
        return {}
    out = {}
    for word in ("mesh", "option", "automesh", "analysis"):
        lt = run("list_available_tables", {"software": sw, "search_term": word})
        for t in (lt.get("tables") or []) if "_error" not in lt else []:
            k = t["key"]
            if k in out or t.get("import_type") == 0:
                continue
            try:
                from db_edit import read_table
                _, fields, rows = com(read_table, bridge.sap_model(sw), k)
                out[k] = rows[:12]
            except Exception as exc:  # noqa: BLE001
                out[k] = f"err {exc}"
    return out


# ----------------------------------------------------------------------- main
by_model = {}
for c in CHECKS:
    by_model.setdefault(c["model"], []).append(c)
OUT.mkdir(parents=True, exist_ok=True)
summary = []
for sw in programs:
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=visible)
    except Exception as exc:  # noqa: BLE001
        FAILED.append(f"{sw} start: {exc}")
        continue
    try:
        for model in V2[sw]:
            t0 = time.perf_counter()
            src = model_copy(model)
            run("open_or_save_model", {"software": sw, "action": "open", "file_path": str(src)})
            snap_path = OUT / f"{model}.read.json"
            r = run("export_model_snapshot", {"software": sw, "units": UNIT, "output_path": str(snap_path)})
            if "_error" in r:
                FAILED.append(f"{model} snapshot: {r['_error']}")
                continue
            snap = json.loads(snap_path.read_text(encoding="utf-8"))
            if sw in READ_ONLY:
                fr = [f for f in snap["assignments"]["frames"] if f.get("supports") or f.get("line_spring_property")]
                print(f"{model}: read only. frames with supports/springs: {json.dumps(fr)[:1500]}", flush=True)
                print(f"   points with more than a name: {[p for p in snap['assignments']['points'] if len(p) > 1][:10]}",
                      flush=True)
                for word in ("spring", "support", "restraint", "slab propert", "soil"):
                    lt = run("list_available_tables", {"software": sw, "search_term": word})
                    print(f"   TABLES '{word}': {json.dumps(lt)[:700]}", flush=True)
                    keys = [str(t.get("table_key") or t.get("key") or t) if isinstance(t, dict) else str(t)
                            for t in (lt.get("tables") or lt.get("items") or [])] if "_error" not in lt else []
                    for t in keys[:6]:
                        r = run("query_database_table", {"software": sw, "table_key": t, "max_rows": 40})
                        print(f"   TABLE {t}: {json.dumps(r.get('rows', r))[:1200]}", flush=True)
                continue
            stories = snap.get("stories") or []
            fnames = [f["name"] for f in snap["frames"]]
            pnames = [p["name"] for p in snap["points"]]
            raw_orig = com(raw_props, sw, fnames, pnames)
            mesh_orig = mesh_tables(sw)
            com(blank, sw, stories)
            new_path = OUT / f"{model} (rebuilt){ {'SAP2000': '.sdb', 'ETABS': '.edb', 'SAFE': '.fdb'}[sw] }"
            if new_path.exists():
                new_path.unlink()
            rs = run("open_or_save_model", {"software": sw, "action": "save", "file_path": str(new_path)})
            if "_error" in rs:
                FAILED.append(f"{model} save as: {rs['_error']}")
                continue
            log = []
            rebuild(sw, snap, log)
            bad = [x for x in log if not x["ok"]]
            print(f"{model}: rebuilt with {len(log)} action calls, {len(bad)} not rebuilt", flush=True)
            (OUT / f"{model}.rebuild.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
            raw_new = com(raw_props, sw, fnames, pnames)
            mesh_new = mesh_tables(sw)
            for t in mesh_orig:
                print(f"   MESH TABLE {t}\n      original: {json.dumps(mesh_orig[t])[:900]}\n      rebuilt:  "
                      f"{json.dumps(mesh_new.get(t))[:900]}", flush=True)
            diffs = {k: (raw_orig[k], raw_new.get(k)) for k in raw_orig if raw_orig[k] != raw_new.get(k)}
            print(f"{model}: {len(diffs)} raw differences original -> rebuilt", flush=True)
            for k, (o, n) in diffs.items():
                print(f"   DIFF {k}: {o}  ->  {n}", flush=True)
            ra = run("run_analysis", {"software": sw})
            if "_error" in ra:
                print(f"   ANALYSIS: {str(ra['_error'])[:1500]}", flush=True)
            lg = run("get_analysis_log", {"software": sw})
            cache, res_list, flens = {}, [], {}
            br = run("get_results", {"software": sw, "result_type": "base_reactions", "units": UNIT, "max_rows": 50,
                                     "export": "none"})
            print(f"   base reactions: {json.dumps(br.get('rows', br))[:800]}", flush=True)
            for c in by_model[model]:
                key = (c["rt"], c["case"], c["units"])
                if key not in cache:
                    cache[key] = run("get_results", {"software": sw, "result_type": c["rt"], "units": c["units"],
                                                     "load_cases_or_combos": [c["case"]], "max_rows": 2000,
                                                     "export": "none"})
                rr = cache[key]
                if "_error" in rr:
                    res_list.append({**c, "status": "ERROR", "ours": None, "detail": str(rr["_error"])[:200]})
                    continue
                if c["units"] not in flens:
                    ob = run("get_objects", {"software": sw, "units": c["units"], "max_rows": 5000})
                    flens[c["units"]] = {f["name"]: f["length"] for f in ob.get("frames", {}).get("items", [])} \
                        if "_error" not in ob else {}
                ours, detail = pick(c, rr.get("rows", []), flens[c["units"]], sw)
                if ours is None:
                    res_list.append({**c, "status": "MISSING", "ours": None, "detail": detail})
                    continue
                scale = max(abs(x["published"]) for x in by_model[model] if x["comp"] == c["comp"])
                st, rel = grade(ours, c["published"], scale)
                res_list.append({**c, "status": st, "ours": ours, "rel": rel, "detail": detail})
            tally = {s: sum(1 for x in res_list if x["status"] == s) for s in ("MATCH", "CLOSE", "DIFF", "MISSING",
                                                                                  "ERROR")}
            stable = lg.get("stable") if isinstance(lg, dict) else None
            print(f"{model} (rebuilt): {tally} stable={stable} analysis={'ok' if '_error' not in ra else ra['_error'][:200]}"
                  f" ({time.perf_counter() - t0:.1f}s)", flush=True)
            for x in res_list:
                flag = "" if x["status"] == "MATCH" else "  <--"
                print(f"   {x['status']:7} case {x['case']:5} {x['rt']}/{x['comp']} {x['sel']} published "
                      f"{x['published']} rebuilt {x['ours']}{flag}", flush=True)
            summary.append((model, tally, len(bad)))
            if tally["MATCH"] != len(res_list) or bad:
                FAILED.append(f"{model}: {tally}, {len(bad)} not rebuilt")
    finally:
        com(bridge.disconnect, sw, True, False)

print("\nV2 SUMMARY", flush=True)
for model, tally, nbad in summary:
    print(f"  {model}: {tally}; not rebuilt: {nbad}", flush=True)
sys.exit(finish())
