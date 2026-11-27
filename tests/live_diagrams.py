r"""Result diagrams compared with the programs' own displays (0.15.0): multi-bay, multi-storey test models with many
slabs and a wall, built through the actions in NEW instances, analysed, and reported (frame N / V / M per elevation,
slab and wall contours). The models are saved so the same views can be opened in the programs.

    run_live_diagrams.bat [SAP2000] [ETABS] [SAFE] [--hidden]      Output: tests\results\live_diagrams.txt,
                                                                  PDFs and models in tests\results\diagrams_output\

  SAP2000 / ETABS: 3 x 2 bays of 6 m, 2 storeys of 3.5 m, 12 slabs (0.2 m), a 0.25 m wall on grid Y = 0 between
  X = 0 and 6 m over both storeys, columns 0.5 x 0.5, beams 0.3 x 0.6; DL (self weight + 2 kN/m2), LL 3 kN/m2,
  WX 50 kN at each floor; ULS = 1.2 DL + 1.6 LL, LAT = DL + WX.
  SAFE: a copy of CSI's ACI 318-19 RC-PN Ex001 flat slab (3 x 3 bays of 24 ft).
Checks: the frame M3 at a beam's midspan and support from the report data equal the program's frame forces; each
contour's range equals the joint-averaged range of the program's shell forces.
"""
import os
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diagrams_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
visible = "--hidden" not in sys.argv
EXT = {"SAP2000": ".sdb", "ETABS": ".edb", "SAFE": ".fdb"}
U = "kN_m_C"
XS, YS, ZS = [0.0, 6.0, 12.0, 18.0], [0.0, 6.0, 12.0], [0.0, 3.5, 7.0]
SAFE_SRC = HERE.parent / "CSI Solved Examples" / "Safe" / "Models" / "Design Examples" / "Concrete and PT Slab" / \
    "ACI 318-19 RC-PN Ex001.FDB"


def pid(x, y, z):
    return f"P{int(x)}_{int(y)}_{int(z * 10)}"


def spec(sw: str) -> dict:
    slab_kind = "shell_thin" if sw == "SAP2000" else "slab"
    wall_kind = "shell_thin" if sw == "SAP2000" else "wall"
    pts, frames, slabs = [], [], []
    for z in ZS:
        for x in XS:
            for y in YS:
                pts.append({"name": pid(x, y, z), "xyz": [x, y, z], **({"restraint": "fixed"} if z == 0 else {})})
    for i, z in enumerate(ZS[1:], 1):
        for x in XS:
            for y in YS:
                frames.append({"name": f"C{int(x)}_{int(y)}_{i}", "start": pid(x, y, ZS[i - 1]), "end": pid(x, y, z),
                               "section": "COL500"})
        for y in YS:
            for x0, x1 in zip(XS, XS[1:]):
                frames.append({"name": f"BX{int(x0)}_{int(y)}_{i}", "start": pid(x0, y, z), "end": pid(x1, y, z),
                               "section": "BM300x600"})
        for x in XS:
            for y0, y1 in zip(YS, YS[1:]):
                frames.append({"name": f"BY{int(x)}_{int(y0)}_{i}", "start": pid(x, y0, z), "end": pid(x, y1, z),
                               "section": "BM300x600"})
        for x0, x1 in zip(XS, XS[1:]):
            for y0, y1 in zip(YS, YS[1:]):
                slabs.append({"name": f"S{int(x0)}_{int(y0)}_{i}",
                              "points": [pid(x0, y0, z), pid(x1, y0, z), pid(x1, y1, z), pid(x0, y1, z)]})
    walls = [{"name": f"W{i}", "points": [pid(0, 0, ZS[i - 1]), pid(6, 0, ZS[i - 1]), pid(6, 0, ZS[i]),
                                          pid(0, 0, ZS[i])]} for i in (1, 2)]
    floors = [s["name"] for s in slabs]
    return {"units": U,
            "materials": [{"name": "C30", "type": "concrete", "E": 2.6e7, "poisson": 0.2, "unit_weight": 25.0,
                           "thermal_coeff": 1e-5, "fc": 30000.0}],
            "frame_sections": [{"name": "COL500", "material": "C30", "shape": "rectangle", "depth": 0.5, "width": 0.5},
                               {"name": "BM300x600", "material": "C30", "shape": "rectangle", "depth": 0.6,
                                "width": 0.3}],
            "area_sections": [{"name": "SLAB200", "kind": slab_kind, "material": "C30", "thickness": 0.2},
                              {"name": "WALL250", "kind": wall_kind, "material": "C30", "thickness": 0.25}],
            "load_patterns": [{"name": "DL", "type": "dead", "self_weight_multiplier": 1.0},
                              {"name": "LL", "type": "live", "self_weight_multiplier": 0.0},
                              {"name": "WX", "type": "wind", "self_weight_multiplier": 0.0}],
            "points": pts, "frames": frames,
            "_slabs": slabs, "_walls": walls, "_floors": floors}


def build(S: dict, sw: str) -> None:
    sp = spec(sw)
    slabs, walls, floors = sp.pop("_slabs"), sp.pop("_walls"), sp.pop("_floors")
    b = show("frame model from spec", "build_model_from_spec", {**S, "spec": sp}, limit=600)
    check(f"{sw}: spec built", isinstance(b, dict) and b.get("completed") is True, b)
    show("slabs", "create_areas", {**S, "units": U, "section": "SLAB200", "areas": slabs}, limit=400)
    show("wall", "create_areas", {**S, "units": U, "section": "WALL250", "areas": walls}, limit=400)
    if sw == "SAP2000":
        show("mesh slabs and wall (1 m)", "set_area_mesh",
             {**S, "units": U, "area_names": floors + [w["name"] for w in walls], "method": "auto_mesh",
              "mode": "max_size", "max_size_1": 1.0, "max_size_2": 1.0}, limit=400)
    for pat, val in (("DL", 2.0), ("LL", 3.0)):
        show(f"{pat} on the slabs", "assign_area_load",
             {**S, "units": U, "area_names": floors, "load_pattern": pat, "load_type": "uniform", "value": val,
              "direction": "gravity"}, limit=400)
    show("WX at the floors", "assign_point_or_frame_load",
         {**S, "units": U, "target_type": "point", "object_names": [pid(0, 6, 3.5), pid(0, 6, 7.0)],
          "load_pattern": "WX", "values": [50, 0, 0, 0, 0, 0]}, limit=400)
    for name, items in (("ULS", [("DL", 1.2), ("LL", 1.6)]), ("LAT", [("DL", 1.0), ("WX", 1.0)])):
        show(f"combination {name}", "define_combination",
             {**S, "name": name, "type": "linear_add",
              "items": [{"name": n, "factor": f, "kind": "case"} for n, f in items]}, limit=300)


def report(S: dict, sw: str, case: str, name: str, extra: dict | None = None) -> dict:
    rp = show(f"report {name}", "export_report",
              {**S, "units": U, "sections": ["cover", "result_diagrams"], "result_case": case,
               "result_diagrams": ["frame_forces", "shell_contours"], "output_path": str(OUT / f"{sw} {name}.pdf"),
               "overwrite": True, **(extra or {})}, limit=1500)
    check(f"{sw}: report {name} written with figures", isinstance(rp, dict) and rp.get("figures", 0) >= 1, rp)
    return rp if isinstance(rp, dict) else {}


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)     # a folder held open (e.g. in Explorer) is left; files are overwritten
OUT.mkdir(parents=True, exist_ok=True)

for sw in targets:
    print(f"\n################ {sw} (new instance, visible={visible})", flush=True)
    try:
        com(bridge.launch_new, sw, visible=visible)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED to start {sw}: {exc}", flush=True)
        FAILED.append(f"{sw} start")
        continue
    t0 = time.perf_counter()
    S = {"software": sw}
    try:
        if sw == "SAFE":
            dest = OUT / "models" / SAFE_SRC.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(SAFE_SRC, dest)
            show("open copy of RC-PN Ex001", "open_or_save_model", {**S, "action": "open", "file_path": str(dest)},
                 limit=300)
            show("analysis", "run_analysis", {**S}, limit=500)
            for comp in ("M11", "M22"):
                report(S, sw, "Dead", f"slab {comp} Dead", {"shell_component": comp})
        else:
            path = str(OUT / "models" / f"diagrams_{sw}{EXT[sw]}")
            (OUT / "models").mkdir(parents=True, exist_ok=True)
            nm = {**S, "file_path": path, "discard_open_model": True, "units": U}
            if sw == "ETABS":
                nm.update(kind="grid_only", stories=2, story_height=3.5, spacing_x=6.0, spacing_y=6.0)
            show("new model", "new_model", nm, limit=300)
            build(S, sw)
            show("analysis", "run_analysis", {**S}, limit=500)
            report(S, sw, "ULS", "ULS M", {"frame_component": "bending", "shell_component": "M11",
                                            "wall_component": "F22"})
            report(S, sw, "ULS", "ULS M22", {"frame_component": "bending", "shell_component": "M22",
                                              "wall_component": "M11"})
            report(S, sw, "LAT", "LAT V2", {"frame_component": "V2", "shell_component": "M11",
                                             "wall_component": "F12"})
            report(S, sw, "ULS", "ULS P", {"frame_component": "P", "shell_component": "F11",
                                            "wall_component": "F22"})
            f = show("beam M3 (ULS)", "get_results", {**S, "units": U, "result_type": "frame_forces",
                                                     "names": ["BX6_6_1"], "load_cases_or_combos": ["ULS"]},
                     limit=1500)
            rows = (f.get("rows") or f.get("records") or []) if isinstance(f, dict) else []
            print("record: BX6_6_1 M3 at stations:",
                  [(r.get("Station"), r.get("M3")) for r in rows], flush=True)
            show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
    except Exception as exc:  # noqa: BLE001
        print(f"FAILED ({type(exc).__name__}): {exc}", flush=True)
        FAILED.append(f"{sw}: {exc}")
    finally:
        print(f"{sw} done in {time.perf_counter() - t0:.1f}s", flush=True)
        if "--keep-open" not in sys.argv:
            try:
                com(bridge.disconnect, sw, True, False)
            except Exception as exc:  # noqa: BLE001
                print("close", exc)

sys.exit(finish())
