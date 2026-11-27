r"""V1 - verify the MCP's results against CSI's own verification examples.

    run_verify_v1.bat [SAP2000] [ETABS] [SAFE] [--hidden] [--only "Example 1-001"]
    Output: tests\results\verify_v1.txt, tests\results\verify\v1_results.csv

For each model in expected_v1.CHECKS: fresh copy (originals untouched) -> open -> run_analysis -> get_analysis_log
-> every check read through get_results exactly as Claude would call it -> compare with CSI's published program
value. What this tests is the MCP (units, sign, the right joint / station / mode / case, envelopes), not CSI's solver;
the independent (hand / textbook) value is reported beside it.

Grades: MATCH  within the rounding of the published value or 0.05 %
        CLOSE  within 1 %   (version-to-version solver differences, published rounding)
        DIFF   more than 1 % - investigate
        MISSING the row could not be found (station not output, joint not in the results)
"""
import csv
import math
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
sys.path.insert(0, str(TESTS))
sys.path.insert(0, str(HERE))
OUT = TESTS / "results" / "verify"
os.environ["CSI_MCP_OUTPUT"] = str(OUT / "output")

from live_common import FAILED, bridge, com, finish  # noqa: E402,I001
from csi_bridge import api_call  # noqa: E402
from expected_v1 import CHECKS  # noqa: E402
from verify_models import MODELS, model_copy  # noqa: E402
import server  # noqa: E402

args = sys.argv[1:]
visible = "--hidden" not in args
only_models = [args[i + 1] for i, a in enumerate(args) if a == "--only" and i + 1 < len(args)]
programs = [a.upper() for a in args if a.upper() in ("SAP2000", "ETABS", "SAFE")] or ["SAP2000", "ETABS", "SAFE"]


def run(action, params):
    try:
        return server.executor.run(server.dispatch, action, params)
    except Exception as exc:  # noqa: BLE001
        return {"_error": f"{type(exc).__name__}: {getattr(exc, 'payload', None) or exc}"}


def printed_tol(v: float) -> float:
    """Half a unit of the last printed digit of the published value. Whole numbers below 1000 (0, 5, 50 ...) are
    taken as exact (no rounding slack), zero gets 5e-4 in the published units."""
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


def grade(ours: float, pub: float, scale: float = 0.0) -> tuple[str, float]:
    """scale: the largest published magnitude of the same quantity in the example; a published zero is matched
    when ours is below 1e-5 of it (round-off next to the real values)."""
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


def pick(check, rows, sw, frames_len, obj_xy):
    sel, comp = check["sel"], check["comp"]
    rows = [r for r in rows if str(r.get("LoadCase")) == check["case"]]
    if "joint" in sel:
        rows = [r for r in rows if str(r.get("Joint")) == sel["joint"]]
    elif "frame" in sel:
        rows = [r for r in rows if str(r.get("Frame")) == sel["frame"]]
        L = frames_len.get(sel["frame"])
        if sel["at"] != "absmax" and L:
            target = sel["at"] * L
            near = [r for r in rows if abs(float(r["Station"]) - target) <= 1e-3 * L + 1e-6]
            if not near:
                st = sorted({round(float(r["Station"]), 4) for r in rows})
                return None, f"no output station at {target:g} (stations {st[:12]})"
            rows = near
    elif "mode" in sel:
        rows = [r for r in rows if int(round(float(r.get("StepNum") or 0))) == sel["mode"]]
    elif "xy" in sel:
        x, y = sel["xy"]
        if sw == "SAFE":
            names = sorted({str(r["Element"]) for r in rows})
            xyz = elm_coords(sw, names)
            key = "Element"
        else:
            xyz, key = obj_xy, "Joint"
        if not xyz:
            return None, "no joint coordinates"
        best = min(xyz, key=lambda n: (xyz[n][0] - x) ** 2 + (xyz[n][1] - y) ** 2)
        d = math.dist(xyz[best][:2], (x, y))
        if d > 1e-2 * max(1.0, abs(x), abs(y)):
            return None, f"no joint at ({x}, {y}); nearest {best} at {xyz[best][:2]}"
        rows = [r for r in rows if str(r[key]) == best]
    if not rows:
        return None, "no row"
    vals = [float(r[comp]) for r in rows if r.get(comp) is not None]
    if not vals:
        return None, f"no {comp}"
    v = max(vals, key=abs) if (check["abs"] or len(vals) > 1) else vals[0]
    return (abs(v) if check["abs"] else v), (rows[0].get("StepType") or "")


by_model = defaultdict(list)
for c in CHECKS:
    by_model[c["model"]].append(c)
(OUT).mkdir(parents=True, exist_ok=True)
results = []
logs = {}

for sw in programs:
    models = [m for m in by_model if MODELS[m][0] == sw and (not only_models or m in only_models)]
    if not models:
        continue
    print(f"\n################ {sw}: {len(models)} models, {sum(len(by_model[m]) for m in models)} checks", flush=True)
    try:
        com(bridge.launch_new, sw, visible=visible)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to start {sw}: {exc}", flush=True)
        FAILED.append(f"{sw} start")
        continue
    try:
        for model in models:
            t0 = time.perf_counter()
            path = model_copy(model)
            r = run("open_or_save_model", {"software": sw, "action": "open", "file_path": str(path)})
            if "_error" in r:
                print(f"{model}: OPEN FAILED {r['_error']}", flush=True)
                results += [{**c, "status": "ERROR", "ours": None, "detail": "open failed"} for c in by_model[model]]
                continue
            ra = run("run_analysis", {"software": sw})
            if "_error" in ra:
                print(f"{model}: ANALYSIS: {str(ra['_error'])[:300]}", flush=True)
            lg = run("get_analysis_log", {"software": sw})
            logs[model] = lg
            cache = {}
            objs = {}
            for c in by_model[model]:
                key = (c["rt"], c["case"], c["units"])
                if key not in cache:
                    cache[key] = run("get_results", {"software": sw, "result_type": c["rt"], "units": c["units"],
                                                     "load_cases_or_combos": [c["case"]], "max_rows": 2000,
                                                     "export": "none"})
                if c["units"] not in objs:
                    objs[c["units"]] = run("get_objects", {"software": sw, "units": c["units"], "max_rows": 5000})
                res, ob = cache[key], objs[c["units"]]
                if "_error" in res:
                    results.append({**c, "status": "ERROR", "ours": None, "detail": str(res["_error"])[:200]})
                    continue
                if res.get("truncated"):
                    results.append({**c, "status": "ERROR", "ours": None, "detail": "result rows cut at 2000"})
                    continue
                flen = {f["name"]: f["length"] for f in ob.get("frames", {}).get("items", [])} if "_error" not in ob else {}
                oxy = {p["name"]: (p["x"], p["y"], p["z"]) for p in ob.get("points", {}).get("items", [])} \
                    if "_error" not in ob else {}
                ours, detail = pick(c, res.get("rows", []), sw, flen, oxy)
                if ours is None:
                    results.append({**c, "status": "MISSING", "ours": None, "detail": detail})
                    continue
                scale = max(abs(x["published"]) for x in by_model[model] if x["comp"] == c["comp"])
                st, rel = grade(ours, c["published"], scale)
                results.append({**c, "status": st, "ours": ours, "rel": rel, "detail": detail})
            mine = [x for x in results if x["model"] == model]
            tally = {s: sum(1 for x in mine if x["status"] == s) for s in ("MATCH", "CLOSE", "DIFF", "MISSING", "ERROR")}
            stable = lg.get("stable") if isinstance(lg, dict) else None
            print(f"{model}: {tally} log stable={stable} ({time.perf_counter() - t0:.1f}s)", flush=True)
            for x in mine:
                if x["status"] not in ("MATCH",):
                    print(f"   {x['status']:7} {x['case']:9} {x['rt']}/{x['comp']} sel={x['sel']} published "
                          f"{x['published']} ours {x['ours']} ({x.get('rel', 0) * 100 if x.get('rel') is not None else 0:.3f}%)"
                          f" {x['detail']} {x['note']}", flush=True)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"could not close {sw}: {exc}", flush=True)

with open(OUT / "v1_results.csv", "w", newline="", encoding="utf-8-sig") as fh:
    w = csv.writer(fh)
    w.writerow(["model", "case", "result_type", "component", "selection", "units", "published", "ours", "rel_diff",
                "status", "independent", "note", "detail"])
    for x in results:
        w.writerow([x["model"], x["case"], x["rt"], x["comp"], x["sel"], x["units"], x["published"], x["ours"],
                    x.get("rel"), x["status"], x["independent"], x["note"], x["detail"]])
tot = {s: sum(1 for x in results if x["status"] == s) for s in ("MATCH", "CLOSE", "DIFF", "MISSING", "ERROR")}
print(f"\nTOTAL {len(results)} checks: {tot}", flush=True)
for p in programs:
    sub = [x for x in results if MODELS[x["model"]][0] == p]
    if sub:
        print(f"  {p}: " + str({s: sum(1 for x in sub if x['status'] == s) for s in tot}), flush=True)
if tot["DIFF"] or tot["ERROR"] or tot["MISSING"]:
    FAILED.append(f"verification: {tot}")
sys.exit(finish())
