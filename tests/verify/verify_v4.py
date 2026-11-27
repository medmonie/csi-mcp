r"""V4 - CSI's own design verification examples run through the MCP design actions (copies; originals untouched).

    run_verify_v4.bat [--hidden]      Output: tests\results\verify_v4.txt

For each example: open a fresh copy -> get_design_settings (the code the example holds; nothing is set) ->
run_analysis -> run_design -> get_design_results -> compare with the values CSI publishes in the example PDF:
  ACI 318-19 Ex001 (beam):     As = 2.37 in2, Av/s = 0.041 in2/in at the critical section (d = 13.5 in)
  ACI 318-19 Ex002 (column):   D/C = 1.000
  AISC 360-16 Ex001 (W18X50):  phi Mn = 378.750 / 307.108 / 94.361 k-ft for Lb = 5 / 11.667 / 35 ft
                               (from the ratio: phi Mn = Mu / ratio, Mu = 1.74 x 35^2 / 8 = 266.4375 k-ft)
  AISC 360-16 Ex002 (built-up column): phi Pn = 497.9 kips (Pu = 1.2 x 70 + 1.6 x 210 = 420 kips)
A report with the design section (tables, charts, D/C diagrams) is written for each example.
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
sys.path.insert(0, str(TESTS))
OUT = TESTS / "results" / "verify" / "v4"
os.environ["CSI_MCP_OUTPUT"] = str(OUT / "output")

from live_common import FAILED, bridge, check, com, finish  # noqa: E402,I001
from live_common import show as _show  # noqa: E402
import faulthandler  # noqa: E402


def show(*a, **k):
    """A step that hangs for 4 minutes dumps every thread's stack and ends the run (no silent hours)."""
    faulthandler.dump_traceback_later(240, exit=True, file=sys.stdout)
    try:
        return _show(*a, **k)
    finally:
        faulthandler.cancel_dump_traceback_later()


ROOT = TESTS.parent
SRC = ROOT / "CSI Solved Examples" / "Etabs" / "Models" / "Design Examples"
visible = "--hidden" not in sys.argv
U = "kip_in_F"

EXAMPLES = [("Concrete Frame", "ACI 318-19 Ex001.EDB", "concrete_frame"),
            ("Concrete Frame", "ACI 318-19 Ex002.EDB", "concrete_frame"),
            ("Steel Frame", "AISC 360-16 Ex001.edb", "steel_frame"),
            ("Steel Frame", "AISC 360-16 Ex002.edb", "steel_frame")]


def copy(folder: str, name: str) -> Path:
    src = next(p for p in (SRC / folder).iterdir() if p.name.lower() == name.lower())
    dest_dir = OUT / "models"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    for old in dest_dir.glob(src.stem + ".*"):
        old.unlink(missing_ok=True)
    shutil.copy2(src, dest)
    return dest


def near(got, want, rel):
    return got is not None and abs(got - want) <= rel * abs(want)


OUT.mkdir(parents=True, exist_ok=True)
summary = []
S = {"software": "ETABS"}
try:
    com(bridge.launch_new, "ETABS", visible=visible)
    for folder, file, kind in EXAMPLES:
        t0 = time.perf_counter()
        name = Path(file).stem
        print(f"\n################ {name} ({kind})", flush=True)
        path = copy(folder, file)
        show("open copy", "open_or_save_model", {**S, "action": "open", "file_path": str(path)}, limit=300)
        st = show("design settings (as in the example)", "get_design_settings", {**S, "kind": kind, "units": U},
                  limit=2500)
        code = st.get("code") if isinstance(st, dict) else None
        show("analysis", "run_analysis", {**S}, limit=500)
        # the CSI examples are delivered with no combinations selected: the program's own default design
        # combinations are what the example uses (as when design is run in the program)
        rd = show("run design", "run_design", {**S, "kind": kind, "units": U, "program_default_combinations": True},
                  limit=1500)
        res = show("design results", "get_design_results", {**S, "kind": kind, "units": U, "tables": True},
                   limit=4000)
        if not isinstance(res, dict):
            summary.append((name, "no results"))
            continue
        if name == "ACI 318-19 Ex001":
            b = res.get("beams", [])
            bot = max((r["BotArea"] for r in b), default=None)
            # the example's Av/s is at the critical section, d = 13.5 in from the support (ACI 9.4.3.2)
            av = next((r["VMajorArea"] for r in b if abs(r["Location"] - 13.5) < 0.01), None)
            check("ACI 318-19 Ex001: As = 2.37 in2", near(bot, 2.37, 0.005), bot)
            check("ACI 318-19 Ex001: Av/s = 0.041 in2/in", near(av, 0.041, 0.02), av)
            summary.append((name, f"As {bot}, Av/s {av}"))
        elif name == "ACI 318-19 Ex002":
            c = res.get("columns", [])
            ratio = max((r["PMMRatio"] for r in c), default=None)
            check("ACI 318-19 Ex002: column D/C = 1.000", near(ratio, 1.0, 0.0006), ratio)
            summary.append((name, f"D/C {ratio}"))
        elif name == "AISC 360-16 Ex001":
            rows = [r for r in res.get("steel", []) if r["RatioType"] == "PMM"]
            mu = 1.74 * 35 ** 2 / 8
            phimn = sorted((mu / r["Ratio"] for r in rows if r["Ratio"] > 0), reverse=True)
            print(f"phi Mn from the ratios (Mu = {mu} k-ft): {phimn}; rows {rows}", flush=True)
            want = [378.750, 307.108, 94.361]
            check("AISC 360-16 Ex001: phi Mn = 378.750 / 307.108 / 94.361 k-ft", len(phimn) == 3
                  and all(near(g, w_, 0.002) for g, w_ in zip(phimn, want)), phimn)
            summary.append((name, f"phi Mn {[round(x, 3) for x in phimn]}"))
        elif name == "AISC 360-16 Ex002":
            rows = res.get("steel", [])
            r0 = max((r["Ratio"] for r in rows), default=None)
            phipn = 420.0 / r0 if r0 else None
            check("AISC 360-16 Ex002: phi Pn = 497.9 kips", near(phipn, 497.9, 0.001), phipn)
            summary.append((name, f"phi Pn {phipn}"))
        check(f"{name}: code reported", bool(code), code)
        rp = show("report (design section)", "export_report",
                  {**S, "sections": ["cover", "design"], "units": U,
                   "output_path": str(OUT / f"{name} design.pdf"), "overwrite": True}, limit=1200)
        check(f"{name}: design report written", isinstance(rp, dict) and rp.get("pages", 0) >= 2, rp)
        print(f"{name} done in {time.perf_counter() - t0:.1f}s", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"FAILED ({type(exc).__name__}): {exc}", flush=True)
    FAILED.append(str(exc))
finally:
    try:
        com(bridge.disconnect, "ETABS", True, False)
    except Exception as exc:  # noqa: BLE001
        print("close", exc)

print("\nV4 SUMMARY", flush=True)
for n, s in summary:
    print(f"  {n}: {s}", flush=True)
sys.exit(finish())
