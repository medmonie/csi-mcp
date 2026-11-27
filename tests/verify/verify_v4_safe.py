r"""V4 (SAFE) - CSI's SAFE slab design examples run through the MCP design actions (copies; originals untouched).

    run_verify_v4_safe.bat [--hidden]      Output: tests\results\verify_v4_safe.txt

  ACI 318-19 RC-SL Ex001 (one-way slab strip): strip moment 55.22 k-in, As+ = 0.213 in2
  ACI 318-19 RC-PN Ex001 (punching at grid B-2): shear stress 0.193 ksi, capacity 0.158 ksi, D/C 1.22

Each: open a fresh copy -> get_design_settings (nothing is set) -> run_analysis -> run_design -> results -> report.
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
TESTS = HERE.parent
sys.path.insert(0, str(TESTS))
OUT = TESTS / "results" / "verify" / "v4_safe"
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
SRC = ROOT / "CSI Solved Examples" / "Safe" / "Models" / "Design Examples"
visible = "--hidden" not in sys.argv
U = "kip_in_F"

EXAMPLES = [("Concrete and PT Slab", "ACI 318-19 RC-SL Ex001.FDB"),
            ("Concrete and PT Slab", "ACI 318-19 RC-PN Ex001.FDB")]


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


def num(r, words, exclude=()):
    for k, v in r.items():
        kl = k.lower()
        if all(w in kl for w in words) and not any(x in kl for x in exclude) and isinstance(v, float):
            return v
    return None


OUT.mkdir(parents=True, exist_ok=True)
summary = []
S = {"software": "SAFE"}
try:
    com(bridge.launch_new, "SAFE", visible=visible)
    for folder, file in EXAMPLES:
        t0 = time.perf_counter()
        name = Path(file).stem
        print(f"\n################ {name}", flush=True)
        path = copy(folder, file)
        show("open copy", "open_or_save_model", {**S, "action": "open", "file_path": str(path)}, limit=300)
        st = show("design settings (as in the example)", "get_design_settings",
                  {**S, "kind": "concrete_slab", "units": U}, limit=2500)
        code = st.get("code") if isinstance(st, dict) else None
        show("analysis", "run_analysis", {**S}, limit=500)
        show("run design", "run_design", {**S, "kind": "concrete_slab", "units": U}, limit=1500)
        res = show("design results", "get_design_results", {**S, "kind": "concrete_slab", "units": U,
                                                            "tables": True}, limit=6000)
        if not isinstance(res, dict):
            summary.append((name, "no results"))
            continue
        if "RC-SL" in name:
            strips = res.get("strips", [])
            m = max((abs(float(r["FBotMoment"])) for r in strips), default=None)
            a = max((float(r["FBotArea"]) for r in strips), default=None)
            # SAFE 23.3 integrates 55.63 k-in over the strip (CSI's table 55.22, FE mesh; As matches): 1 %
            check("RC-SL Ex001: strip moment = 55.22 k-in (1 %)", near(m, 55.22, 0.01), m)
            check("RC-SL Ex001: As+ = 0.213 in2", near(a, 0.213, 0.005), a)
            summary.append((name, f"M {m}, As {a}"))
        else:
            pun = res.get("punching", [])
            print("punching rows:", pun[:12], flush=True)
            # grid B-2: the interior column at x = 288 in, y = 288 in (grids at 24 ft)
            best = next((r for r in pun if r.get("table", "").endswith("Punching Shear Data")
                         and abs(float(r.get("GlobalX", -1)) - 288) < 1 and abs(float(r.get("GlobalY", -1)) - 288) < 1),
                        {})
            print("grid B-2 row:", best, flush=True)
            ratio, stress, cap = best.get("Ratio"), best.get("ShrStrMax"), best.get("ShrStrCap")
            check("RC-PN Ex001 grid B-2: D/C = 1.22", near(ratio, 1.22, 0.005), ratio)
            check("RC-PN Ex001 grid B-2: shear stress 0.193 ksi", near(stress, 0.193, 0.005), stress)
            check("RC-PN Ex001 grid B-2: capacity 0.158 ksi", near(cap, 0.158, 0.005), cap)
            summary.append((name, f"D/C {ratio}, stress {stress}, capacity {cap}"))
        check(f"{name}: code reported", bool(code), code)
        rp = show("report (design section)", "export_report",
                  {**S, "sections": ["cover", "design"], "units": U, "design_kinds": ["concrete_slab"],
                   "output_path": str(OUT / f"{name} design.pdf"), "overwrite": True}, limit=1200)
        check(f"{name}: design report written", isinstance(rp, dict) and rp.get("pages", 0) >= 2, rp)
        print(f"{name} done in {time.perf_counter() - t0:.1f}s", flush=True)
except Exception as exc:  # noqa: BLE001
    print(f"FAILED ({type(exc).__name__}): {exc}", flush=True)
    FAILED.append(str(exc))
finally:
    try:
        com(bridge.disconnect, "SAFE", True, False)
    except Exception as exc:  # noqa: BLE001
        print("close", exc)

print("\nV4 SAFE SUMMARY", flush=True)
for n, s in summary:
    print(f"  {n}: {s}", flush=True)
sys.exit(finish())
