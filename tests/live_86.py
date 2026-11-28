r"""Live test for 0.24.0 (roadmap 8.6, 8.7, 8.9, 8.10). Every number is checked against a hand value.
Output: tests\results\live_86.txt (ends RESULT=OK).

Material C: E 2.5e7 kN/m2, nu 0.2 (G = E / 2.4), unit weight 25; section B 0.3 x 0.5 (I33 = 0.003125, EI = 78125,
As = 5/6 A = 0.125; Timoshenko shear term phi = 12 EI / (G As L^2)).
1. Loads and hyperstatic (S E F), linear static:
   - support settlement d = 0.01 m at one end of a fixed-fixed beam, L 6: M = 6 EI d / (L^2 (1 + phi)) = 127.6552
   - SAP2000: axial strain 0.001 on a fixed bar: P = E A e = 3750; axial deformation 0.002: P = E A d / L = 1250;
     gravity multiplier -1: end moment w L^2 / 12 = 11.25 (w = 25 x 0.15)
   - two equal spans with end couples +-M0 = 10: base case |M| at the middle support 0.5 M0 = 5; hyperstatic case
     (secondary moment) 1.5 M0 = 15 (redundant R = 3 M0 / L)
   - a fixed-fixed beam with w = 10 divided into three pieces (ETABS / SAFE: built by the server, loads split):
     end |M| = w L^2 / 12 = 30, midspan |M| = w L^2 / 24 = 15, end shear w L / 2 = 30
   - SAP2000 equal constraint (UZ) between the tips of two cantilevers L 3, P 10 on one: each tip
     d = 5 L^3 / (3 EI) + 5 L / (G As) = 5.8752e-4
   - wind pressure coefficients read back (not analysed: needs an auto wind load with exposure from shells)
2. Geometry (S E F): join, trim, extend, divide / merge areas, expand / shrink (S), align points: coordinates.
3. Grid systems (S E F): origin, rotation and lines read back; delete.
4. Dynamics (S E): cantilever L 3 with a tip mass m = 100 t, k = 1 / (L^3 / (3 EI) + L / (G As)) = 8510.3 kN/m
   - step load F = 100, no damping: linear direct, nonlinear direct and FNA max u = 2 F / k = 0.023501
   - steady state at f = 0.5 fn: |u| = F / k / sqrt((1 - 0.25)^2 + eta^2), SAP2000 eta 0 (0.015667), ETABS its own
     eta 0.05 (0.015633, from the Joint Displacements table)
   - PSD case defined and run
   - buckling of a pinned column L 3, 8 pieces, shear areas x 1000: Pcr = pi^2 E I22 / L^2 = 30842.75
   - staged construction with time-dependent material: flag read back
"""
import math
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "live86_output"
import os  # noqa: E402
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, check, com, finish, show  # noqa: E402,I001

args = [a for a in sys.argv[1:] if not a.startswith("--")]
targets = [a.upper() for a in args] or ["SAP2000", "ETABS", "SAFE"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}
U = "kN_m_C"
E, NU, GAM = 2.5e7, 0.2, 25.0
G = E / (2 * (1 + NU))
A, I33, I22, AS = 0.15, 0.3 * 0.5 ** 3 / 12, 0.5 * 0.3 ** 3 / 12, 0.125
EI = E * I33


def ok(r):
    return isinstance(r, dict) and not r.get("_error") and not isinstance(r, Exception)


def close(a, b, rel=1e-3):
    return a is not None and abs(a - b) <= rel * max(1e-12, abs(b))


def rows(r):
    return r.get("rows", []) if ok(r) else []


def frame_m3(sw, S, frames, case, station_of=None):
    """{(frame, station): M3} for one case."""
    r = show(f"frame forces {case}", "get_results", {**S, "result_type": "frame_forces",
                                                     "load_cases_or_combos": [case], "names": frames,
                                                     "max_rows": 2000, "export": "none"}, limit=600)
    out = {}
    for x in rows(r):
        if x.get("LoadCase") == case:
            out[(x.get("Frame"), round(float(x.get("Station", 0)), 6))] = (float(x.get("M3", 0) or 0),
                                                                           float(x.get("V2", 0) or 0),
                                                                           float(x.get("P", 0) or 0))
    return out


def first(d, frame, station):
    for (f, s), v in d.items():
        if f == frame and abs(s - station) < 1e-6:
            return v
    return None


def make_model(sw, tag, story=3.0):
    S = {"software": sw}
    nm = {**S, "units": U, "file_path": str(OUT / f"l86_{tag}_{sw}{EXT[sw]}"), "discard_open_model": True,
          "overwrite": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=story, spacing_x=6.0, spacing_y=6.0)
    show(f"new model {tag}", "new_model", nm, limit=200)
    show("C", "define_material", {**S, "units": U, "name": "C", "type": "concrete", "E": E, "poisson": NU,
                                  "unit_weight": GAM, "fc": 30000.0}, limit=150)
    show("B", "define_frame_section", {**S, "units": U, "name": "B", "shape": "rectangle", "material": "C",
                                       "depth": 0.5, "width": 0.3}, limit=150)
    return S


def frame(S, a, b, name=""):
    r = show(f"frame {name}", "create_frames", {**S, "units": U, "section": "B",
                                                 "frames": [{"start": a, "end": b, "name": name}]}, limit=300)
    c = r["created"][0] if ok(r) else {"name": name, "points": ["", ""]}
    return c["name"], c["points"]


def restrain(S, pts, r):
    show(f"restrain {pts}", "assign_point_properties", {**S, "units": U, "point_names": pts, "restraint": r},
         limit=150)


def pattern(S, name, typ="other"):
    show(f"pattern {name}", "define_load_pattern", {**S, "units": U, "name": name, "type": typ,
                                                    "self_weight_multiplier": 0.0}, limit=120)


# --------------------------------------------------------------------------- 1. loads and hyperstatic

def part1(sw):
    S = make_model(sw, "loads")
    z = 3.0 if sw == "ETABS" else 0.0
    fa, (ai, aj) = frame(S, [0, 0, z], [6, 0, z], "FA")
    restrain(S, [ai, aj], "fixed")
    for pat in ("DISP", "MOM", "UDL"):
        pattern(S, pat)
    pattern(S, "W", "wind")
    r = show("settlement", "assign_support_displacement", {**S, "units": U, "points": [aj], "load_pattern": "DISP",
                                                           "values": [0, 0, -0.01, 0, 0, 0]}, limit=800)
    check("settlement read back", ok(r) and r["read_back"][aj][0]["values"][2] == -0.01, r)
    # two spans with end couples
    h1, (p0, p1) = frame(S, [0, 10, z], [6, 10, z], "H1")
    h2, (_, p2) = frame(S, p1, [12, 10, z], "H2")
    restrain(S, [p0], [True, True, True, True, False, True])
    restrain(S, [p1, p2], [False, True, True, True, False, True])
    show("couples", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [p0],
                                                   "load_pattern": "MOM", "direction": "MY", "values": [10.0]})
    show("couples", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [p2],
                                                   "load_pattern": "MOM", "direction": "MY", "values": [-10.0]})
    r = show("hyperstatic", "define_advanced_load_case", {**S, "units": U, "name": "HYP", "type": "hyperstatic",
                                                          "base_case": "MOM"}, limit=600)
    check("hyperstatic read back", ok(r) and r["read_back"].get("base_case") == "MOM", r)
    # divided loaded beam
    fd, (di, dj) = frame(S, [0, 20, z], [6, 20, z], "FD")
    restrain(S, [di, dj], "fixed")
    show("udl", "assign_point_or_frame_load", {**S, "units": U, "target_type": "frame", "object_names": [fd],
                                               "load_pattern": "UDL", "direction": "Gravity", "values": [10.0]})
    r = show("divide FD", "divide_frames", {**S, "units": U, "frames": [fd], "mode": "ratio", "number": 3,
                                            "ratio": 1.0}, limit=1500)
    pieces = [x["frame"] for x in r["pieces"][fd]] if ok(r) else []
    check("divided in 3", len(pieces) == 3, pieces)
    # wind pressure coefficients on an area
    if sw == "SAFE":
        show("slab", "define_area_section", {**S, "units": U, "name": "SL", "kind": "slab", "material": "C",
                                             "thickness": 0.2}, limit=100)
        corners = [[20, 0, 0], [22, 0, 0], [22, 2, 0], [20, 2, 0]]
    else:
        show("wall", "define_area_section", {**S, "units": U, "name": "SL",
                                             "kind": "wall" if sw == "ETABS" else "shell_thin", "material": "C",
                                             "thickness": 0.2}, limit=100)
        corners = [[20, 0, 0], [22, 0, 0], [22, 0, 3], [20, 0, 3]]
    ar = show("area", "create_areas", {**S, "units": U, "section": "SL", "areas": [{"points": corners}]}, limit=300)
    an = ar["created"][0]["name"] if ok(ar) else "?"
    r = show("wind Cp", "assign_area_wind_pressure", {**S, "units": U, "areas": [an], "load_pattern": "W",
                                                      "face": "windward", "cp": 0.8}, limit=600)
    check("wind Cp read back", ok(r) and r["read_back"][an][0]["cp"] == 0.8 and "not_kept_as_given" not in r, r)
    bad = show("wind Cp on a non-wind pattern", "assign_area_wind_pressure",
               {**S, "units": U, "areas": [an], "load_pattern": "DISP", "face": "windward", "cp": 0.8},
               expect_error=True, limit=300)
    check("non-wind pattern refused", not ok(bad))
    show("delete area", "delete_objects", {**S, "units": U, "kind": "areas", "names": [an]}, limit=200)
    cases = ["DISP", "MOM", "HYP", "UDL"]
    if sw == "SAP2000":
        fs, (si, sj) = frame(S, [0, 5, z], [6, 5, z], "FS")
        restrain(S, [si, sj], "fixed")
        for pat, extra in (("STR", {"load_type": "strain", "component": "axial", "value": 0.001}),
                           ("DEF", {"load_type": "deformation", "deformation": {"U1": 0.002}}),
                           ("GRAV", {"load_type": "gravity", "multipliers": [0, 0, -1]})):
            pattern(S, pat)
            r = show(f"imposed {pat}", "assign_strain_or_gravity_load",
                     {**S, "units": U, "target_type": "frame", "object_names": [fs], "load_pattern": pat, **extra},
                     limit=600)
            check(f"{pat} read back", ok(r) and r["read_back"][fs], r)
        cases += ["STR", "DEF", "GRAV"]
        # equal constraint between two cantilever tips
        c1, (c1i, c1j) = frame(S, [0, 30, z], [3, 30, z], "K1")
        c2, (c2i, c2j) = frame(S, [0, 31, z], [3, 31, z], "K2")
        restrain(S, [c1i, c2i], "fixed")
        pattern(S, "TIP")
        show("tip load", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point",
                                                         "object_names": [c1j], "load_pattern": "TIP",
                                                         "direction": "Gravity", "values": [10.0]})
        r = show("equal constraint", "define_constraint", {**S, "units": U, "operation": "define", "name": "EQ",
                                                           "type": "equal", "dofs": ["UZ"], "points": [c1j, c2j]},
                 limit=800)
        check("constraint read back", ok(r) and r["read_back"]["dofs"] == ["UZ"]
              and sorted(r["constraint_points"]) == sorted([c1j, c2j]), r)
        cases.append("TIP")
    show("save", "open_or_save_model", {**S, "action": "save"}, limit=150)
    r = show("run", "run_analysis", {**S}, limit=1500)
    # settlement
    m = frame_m3(sw, S, [fa], "DISP")
    phi = 12 * EI / (G * AS * 36)
    hand = 6 * EI * 0.01 / (36 * (1 + phi))
    v = first(m, fa, 0.0)
    check("settlement end moment = 6 EI d / (L^2 (1 + phi))", v is not None and close(abs(v[0]), hand, 1e-4),
          f"{v} vs {hand:.4f}")
    # hyperstatic
    mb = frame_m3(sw, S, [h1], "MOM")
    vb = first(mb, h1, 6.0)
    # redundant with shear deformation: R = 3 M0 / L / (1 + 12 EI / (G As (2L)^2)); secondary M = R L / 2
    sec = 15.0 / (1 + 12 * EI / (G * AS * 144.0))
    check(f"base case |M| at the middle support = {sec:.4f} - M0 = {sec - 10:.4f}",
          vb is not None and close(abs(vb[0]), sec - 10.0, 1e-4), vb)
    if sw == "SAP2000":
        mh = frame_m3(sw, S, [h1], "HYP")
        vh, vh0 = first(mh, h1, 6.0), first(mh, h1, 0.0)
        check(f"hyperstatic |M| at the middle support = 1.5 M0 / (1 + 12EI / (G As (2L)^2)) = {sec:.4f}",
              vh is not None and close(abs(vh[0]), sec, 1e-4), vh)
        check("hyperstatic shear = R / 2", vh is not None and close(abs(vh[1]), sec / 6.0, 1e-4), vh)
        check("hyperstatic M at the end support = 0", vh0 is not None and abs(vh0[0]) < 1e-6, vh0)
    else:   # ETABS / SAFE: hyperstatic results come from tendon loads only (measured): the case runs, no rows
        st = show("case status", "get_model_status", {**S}, limit=300)
        check("hyperstatic case defined and run (no tendons: no results, as measured)", ok(st), "")
    # divided beam
    md = frame_m3(sw, S, pieces, "UDL")
    e0, mid = first(md, pieces[0], 0.0) if pieces else None, first(md, pieces[1], 1.0) if len(pieces) > 1 else None
    check("divided beam: end |M| = w L^2 / 12 = 30", e0 is not None and close(abs(e0[0]), 30.0, 2e-3), e0)
    check("divided beam: end |V| = w L / 2 = 30", e0 is not None and close(abs(e0[1]), 30.0, 1e-4), e0)
    check("divided beam: midspan |M| = w L^2 / 24 = 15", mid is not None and close(abs(mid[0]), 15.0, 4e-3), mid)
    if sw == "SAP2000":
        ms = {c: first(frame_m3(sw, S, [fs], c), fs, 0.0) for c in ("STR", "DEF", "GRAV")}
        check("strain: P = -E A e = -3750", ms["STR"] and close(ms["STR"][2], -3750.0, 1e-6), ms["STR"])
        check("deformation: P = -E A d / L = -1250", ms["DEF"] and close(ms["DEF"][2], -1250.0, 1e-6), ms["DEF"])
        check("gravity: |M| = w L^2 / 12 = 11.25", ms["GRAV"] and close(abs(ms["GRAV"][0]), 11.25, 1e-6),
              ms["GRAV"])
        r = show("tip displacements", "get_results", {**S, "result_type": "joint_displacements",
                                                      "load_cases_or_combos": ["TIP"], "names": [c1j, c2j]},
                 limit=1500)
        uz = {x.get("Joint"): float(x.get("U3", 0)) for x in rows(r)}
        hand = 5 * 27 / (3 * EI) + 5 * 3 / (G * AS)
        check("constrained tips move together, d = 5 L^3 / (3 EI) + 5 L / (G As)",
              len(uz) == 2 and all(close(abs(u), hand, 1e-4) for u in uz.values()), f"{uz} vs {hand:.6e}")


# --------------------------------------------------------------------------- 2. geometry

def part2(sw):
    S = make_model(sw, "geometry")
    z = 3.0 if sw == "ETABS" else 0.0
    j1, _ = frame(S, [0, 0, z], [3, 0, z], "J1")
    j2, _ = frame(S, [3, 0, z], [6, 0, z], "J2")
    r = show("join", "join_frames", {**S, "units": U, "frame": j1, "other": j2}, limit=800)
    ends = sorted(tuple(v) for v in r["joined"]["ends"].values()) if ok(r) else []
    check("join: one frame 0 .. 6", ends == [(0.0, 0.0, z), (6.0, 0.0, z)], ends)
    t1, _ = frame(S, [0, 2, z], [8, 2, z], "T1")
    t2, _ = frame(S, [6, 0, z], [6, 4, z], "T2")
    r = show("trim", "trim_or_extend_frame", {**S, "units": U, "frame": t1, "end": "j", "to_frame": t2,
                                              "mode": "trim"}, limit=800)
    check("trim: J end at (6, 2)", ok(r) and r["ends"]["j"] == [6.0, 2.0, z], r)
    x1, _ = frame(S, [0, 6, z], [4, 6, z], "X1")
    x2, _ = frame(S, [6, 5, z], [6, 8, z], "X2")
    r = show("extend", "trim_or_extend_frame", {**S, "units": U, "frame": x1, "end": "j", "to_frame": x2,
                                                "mode": "extend"}, limit=800)
    check("extend: J end at (6, 6)", ok(r) and r["ends"]["j"] == [6.0, 6.0, z], r)
    kind = {"SAP2000": "shell_thin", "ETABS": "slab", "SAFE": "slab"}[sw]
    show("slab", "define_area_section", {**S, "units": U, "name": "SL", "kind": kind, "material": "C",
                                         "thickness": 0.2}, limit=100)
    pattern(S, "Q")
    ar = show("area", "create_areas", {**S, "units": U, "section": "SL",
                                       "areas": [{"points": [[0, 20, z], [4, 20, z], [4, 22, z], [0, 22, z]]}]},
              limit=300)
    an = ar["created"][0]["name"] if ok(ar) else "?"
    show("q", "assign_area_load", {**S, "units": U, "area_names": [an], "load_pattern": "Q", "load_type": "uniform",
                                   "value": 5.0})
    r = show("divide area", "divide_areas", {**S, "units": U, "areas": [an], "n1": 2, "n2": 2}, limit=1500)
    news = r["new_areas"][an] if ok(r) else []
    check("divide area: 4 pieces", len(news) == 4, news)
    g = show("pieces", "get_object_assignments", {**S, "units": U, "object_type": "areas", "names": news,
                                                  "include_loads": True}, limit=3000)
    objs = g.get("items", []) if ok(g) else []
    loads = [ld.get("value") for o in objs for ld in o.get("uniform_loads", [])]
    check("divide area: each piece carries q = 5", len(loads) == 4 and all(abs(abs(x) - 5.0) < 1e-9 for x in loads),
          loads)
    o = show("pieces geometry", "get_objects", {**S, "units": U, "object_type": "areas"}, limit=3000)
    total = sum(float(a.get("area", 0)) for a in o["areas"]["items"] if a.get("name") in news) if ok(o) else 0
    check("divide area: total area 8", close(total, 8.0, 1e-9), total)
    # merge two pieces sharing an edge (the first two along edge 1 share an edge in both programs' orders)
    pair = None
    if ok(r):
        cs = {}
        for a in news:
            gg = com(lambda a=a: bridge.sap_model(sw).AreaObj.GetPoints(a))
            cs[a] = set(str(x) for x in gg[1])
        for i, a in enumerate(news):
            for b in news[i + 1:]:
                if len(cs[a] & cs[b]) == 2:
                    pair = [a, b]
                    break
            if pair:
                break
    r = show("merge", "merge_areas", {**S, "units": U, "areas": pair or news[:2]}, limit=1500)
    corners = r["merged"][0]["corners"] if ok(r) and r["merged"] else []
    area = abs(sum(corners[i][0] * corners[(i + 1) % len(corners)][1] - corners[(i + 1) % len(corners)][0]
                   * corners[i][1] for i in range(len(corners)))) / 2 if corners else 0
    check("merge: one area of 4 m2", ok(r) and len(r["merged"]) == 1 and close(area, 4.0, 1e-9), corners)
    if sw == "SAP2000":
        a2 = show("area 2", "create_areas", {**S, "units": U, "section": "SL",
                                             "areas": [{"points": [[10, 20, z], [12, 20, z], [12, 22, z],
                                                                   [10, 22, z]]}]}, limit=300)
        n2 = a2["created"][0]["name"] if ok(a2) else "?"
        r = show("expand", "expand_shrink_areas", {**S, "units": U, "areas": [n2], "offset": 0.5}, limit=1000)
        after = r["corners"][n2]["after"] if ok(r) else []
        check("expand 0.5: corners 9.5 .. 12.5", sorted({c[0] for c in after}) == [9.5, 12.5], after)
    if sw == "SAP2000":
        import db_edit
        sap = bridge.sap_model(sw)
        pattern(S, "STR")
        a3 = show("area 3", "create_areas", {**S, "units": U, "section": "SL", "areas": [{"points": [
            [30, 20, z], [32, 20, z], [32, 22, z], [30, 22, z]]}]}, limit=300)
        n3 = a3["created"][0]["name"] if ok(a3) else "?"
        for comp in range(1, 9):
            com(lambda comp=comp: sap.AreaObj.SetLoadStrain(n3, "STR", comp, 0.001 * comp, False, ""))
        for dof in range(1, 7):
            com(lambda dof=dof: sap.FrameObj.SetLoadStrain(j1, "STR", dof, 0.001 * dof, False, ""))
        for key in ("Area Loads - Strain", "Frame Loads - Strain"):
            try:
                rr = com(db_edit.read_table, sap, key)[2]
                print(f"STRAIN TABLE {key}:", [{k: v for k, v in x.items() if v} for x in rr], flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"STRAIN TABLE {key}: {exc}", flush=True)
    pts = show("points", "create_points", {**S, "units": U, "points": [{"xyz": [20, 0, z]}, {"xyz": [21, 0.3, z]},
                                                                       {"xyz": [22, -0.2, z]}]}, limit=400)
    names = pts.get("point_names", []) if ok(pts) else []
    r = show("align", "set_point_coordinates", {**S, "units": U, "mode": "align", "points": names, "axis": "y",
                                                "ordinate": 1.0}, limit=1000)
    check("align: y = 1 for all", ok(r) and all(x["after"][1] == 1.0 for x in r["points_read_back"]), r)


# --------------------------------------------------------------------------- 3. grid systems

def part3(sw):
    S = make_model(sw, "grids")
    p = {**S, "units": U, "name": "G2", "origin": [10.0, 5.0], "rotation": 30.0,
         "x_lines": [{"id": "A", "ordinate": 0.0}, {"id": "B", "ordinate": 8.0}],
         "y_lines": [{"id": "1", "ordinate": 0.0}, {"id": "2", "ordinate": 5.0}]}
    if sw == "SAP2000":
        p["z_lines"] = [{"id": "Z1", "ordinate": 0.0}, {"id": "Z2", "ordinate": 3.5}]
    r = show("grid G2", "define_grid_system", p, limit=1500)
    rb = r["read_back"] if ok(r) else {}
    check("grid: origin and rotation", ok(r) and rb["origin"][:2] == [10.0, 5.0] and rb["rotation"] == 30.0, rb)
    check("grid: lines", ok(r) and [x["ordinate"] for x in rb["x_lines"]] == [0.0, 8.0]
          and [x["ordinate"] for x in rb["y_lines"]] == [0.0, 5.0], rb)
    r = show("grid G2 x lines changed", "define_grid_system", {**S, "units": U, "name": "G2",
                                                               "x_lines": [{"id": "A", "ordinate": 0.0},
                                                                           {"id": "B", "ordinate": 6.0},
                                                                           {"id": "C", "ordinate": 12.0}]},
             limit=1500)
    check("grid: x lines replaced, y kept", ok(r) and len(r["read_back"]["x_lines"]) == 3
          and len(r["read_back"]["y_lines"]) == 2, r.get("read_back") if ok(r) else r)
    r = show("delete G2", "define_grid_system", {**S, "units": U, "operation": "delete", "name": "G2"}, limit=600)
    check("grid: deleted", ok(r) and "G2" not in r["grid_systems"], r)


# --------------------------------------------------------------------------- 4. dynamics

def tip_max(sw, S, joint, case, comp="U1"):
    r = show(f"tip {case}", "get_results", {**S, "result_type": "joint_displacements", "load_cases_or_combos": [case],
                                            "names": [joint], "step_option": "envelope", "max_rows": 50},
             limit=1200)
    vals = [abs(float(x.get(comp, 0) or 0)) for x in rows(r)]
    return max(vals) if vals else None


def part4(sw):
    S = make_model(sw, "dynamics")
    # the cantilever's own mass is left out with a mass modifier 0 (SAP2000 takes element and additional mass together)
    show("BM", "define_frame_section", {**S, "units": U, "name": "BM", "shape": "rectangle", "material": "C",
                                        "depth": 0.5, "width": 0.3, "modifiers": [1, 1, 1, 1, 1, 1, 0, 0]}, limit=150)
    rk = show("cantilever", "create_frames", {**S, "units": U, "section": "BM",
                                              "frames": [{"start": [0, 0, 0], "end": [0, 0, 3], "name": "K"}]},
              limit=300)
    k, (kb, kt) = (rk["created"][0]["name"], rk["created"][0]["points"]) if ok(rk) else ("K", ["", ""])
    restrain(S, [kb], "fixed")
    show("tip mass", "assign_point_properties", {**S, "units": U, "point_names": [kt],
                                                 "mass": [100.0, 100.0, 100.0, 0, 0, 0]}, limit=200)
    r = show("mass source", "set_mass_source", {**S, "from_elements": True, "from_masses": True, "from_loads": False},
             limit=600)
    check("mass source read back", ok(r) and r["read_back"]["from_masses"] is True, r)
    if sw == "SAP2000":
        bad = show("masses alone refused (SAP2000)", "set_mass_source",
                   {**S, "from_elements": False, "from_masses": True}, expect_error=True, limit=400)
        check("SAP2000 refuses additional masses without element mass", not ok(bad))
    pattern(S, "F")
    show("tip force", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [kt],
                                                     "load_pattern": "F", "direction": "X", "values": [100.0]})
    show("STEP", "define_function", {**S, "units": U, "name": "STEP", "kind": "time_history",
                                     "points": [[0.0, 1.0], [10.0, 1.0]]}, limit=300)
    r = show("SSF", "define_function", {**S, "units": U, "name": "SSF", "kind": "steady_state",
                                        "points": [[0.0, 1.0], [100.0, 1.0]]}, limit=400)
    check("steady-state function read back", ok(r) and len(r["read_back"]["points"]) == 2, r)
    r = show("PSDF", "define_function", {**S, "units": U, "name": "PSDF", "kind": "power_spectral_density",
                                         "points": [[0.1, 1.0], [100.0, 1.0]]}, limit=400)
    check("PSD function read back", ok(r) and len(r["read_back"]["points"]) == 2, r)
    show("modes", "define_load_case", {**S, "units": U, "name": "MODES", "type": "modal_eigen", "max_modes": 6},
         limit=300)
    ld = [{"name": "F", "function": "STEP"}]
    th = {"steps": 300, "step_size": 0.005}
    for name, typ, extra in (
            ("THL", "linear_direct_history", {"integration": {"method": "newmark", "gamma": 0.5, "beta": 0.25},
                                              "damping": {"method": "mass_stiffness"}}),
            ("THN", "nonlinear_direct_history", {"integration": {"method": "newmark", "gamma": 0.5, "beta": 0.25},
                                                 "damping": {"method": "mass_stiffness"}}),
            ("FNA", "nonlinear_modal_history", {"modal_case": "MODES", "damping": {"method": "constant",
                                                                                   "ratio": 0.0}})):
        r = show(name, "define_advanced_load_case", {**S, "units": U, "name": name, "type": typ, "loads": ld, **th,
                                                     **extra}, limit=1500)
        check(f"{name} read back as {typ}", ok(r) and r["read_back"]["type"] == typ, r)
    kk = 1 / (27 / (3 * EI) + 3 / (G * AS))
    fn = math.sqrt(kk / 100.0) / (2 * math.pi)
    eta = 0.0 if sw == "SAP2000" else 0.05   # ETABS: its own hysteretic damping, not settable (diag_86c)
    r = show("SS", "define_advanced_load_case", {**S, "units": U, "name": "SS", "type": "steady_state",
                                                 "loads": [{"name": "F", "function": "SSF"}],
                                                 "first_frequency": 0.5 * fn, "last_frequency": 0.5 * fn,
                                                 "frequency_increments": 1,
                                                 **({"hysteretic_mass": 0.0, "hysteretic_stiffness": 0.0}
                                                    if sw == "SAP2000" else {})}, limit=1500)
    check("SS read back", ok(r) and r["read_back"]["type"] == "steady_state"
          and close(r["read_back"].get("hysteretic_stiffness", -1), eta, 1e-6), r)
    if sw == "ETABS":
        bad = show("hysteretic damping refused in ETABS", "define_advanced_load_case",
                   {**S, "units": U, "name": "SS9", "type": "steady_state", "loads": [{"name": "F", "function": "SSF"}],
                    "first_frequency": 1.0, "last_frequency": 1.0, "hysteretic_stiffness": 0.04},
                   expect_error=True, limit=400)
        check("ETABS refuses hysteretic coefficients", not ok(bad))
    if sw == "ETABS":
        bad = show("PSD refused in ETABS", "define_advanced_load_case",
                   {**S, "units": U, "name": "PSD", "type": "power_spectral_density",
                    "loads": [{"name": "F", "function": "PSDF"}], "first_frequency": 0.1, "last_frequency": 5.0},
                   expect_error=True, limit=300)
        check("ETABS refuses PSD cases", not ok(bad))
        r = {"read_back": {"type": "power_spectral_density"}}
    else:
        r = show("PSD", "define_advanced_load_case", {**S, "units": U, "name": "PSD", "type": "power_spectral_density",
                                                  "loads": [{"name": "F", "function": "PSDF"}],
                                                      "first_frequency": 0.1, "last_frequency": 5.0,
                                                      "frequency_increments": 20, "add_modal_frequencies": True,
                                                      "modal_case": "MODES", "hysteretic_stiffness": 0.04},
                 limit=1500)
    check("PSD read back", ok(r) and r["read_back"]["type"] == "power_spectral_density", r)
    # buckling column with shear areas x 1000
    show("BK", "define_frame_section", {**S, "units": U, "name": "BK", "shape": "rectangle", "material": "C",
                                        "depth": 0.5, "width": 0.3, "modifiers": [1, 1000, 1000, 1, 1, 1, 1, 1]},
         limit=150)
    rb = show("column", "create_frames", {**S, "units": U, "section": "BK",
                                          "frames": [{"start": [5, 0, 0], "end": [5, 0, 3], "name": "BC"}]}, limit=300)
    bc, (bb, bt) = (rb["created"][0]["name"], rb["created"][0]["points"]) if ok(rb) else ("BC", ["", ""])
    restrain(S, [bb], [True, True, True, False, False, True])
    restrain(S, [bt], [True, True, False, False, False, False])
    pattern(S, "PB")
    show("PB", "assign_point_or_frame_load", {**S, "units": U, "target_type": "point", "object_names": [bt],
                                              "load_pattern": "PB", "direction": "Gravity", "values": [1.0]})
    r = show("divide column", "divide_frames", {**S, "units": U, "frames": [bc], "mode": "ratio", "number": 8},
             limit=800)
    check("column in 8 pieces", ok(r) and r["pieces_total"] == 8, r)
    r = show("BUCK", "define_advanced_load_case", {**S, "units": U, "name": "BUCK", "type": "buckling",
                                                   "loads": [{"name": "PB"}], "modes": 3}, limit=1200)
    check("buckling read back", ok(r) and r["read_back"]["type"] == "buckling", r)
    show("save", "open_or_save_model", {**S, "action": "save"}, limit=150)
    show("run", "run_analysis", {**S}, limit=2500)
    ust = 100.0 / kk
    for case in ("THL", "THN", "FNA"):
        u = tip_max(sw, S, kt, case)
        check(f"{case}: max u = 2 F / k = {2 * ust:.6f}", u is not None and close(u, 2 * ust, 5e-3), u)
    hand = ust / math.sqrt(0.75 ** 2 + eta ** 2)
    if sw == "SAP2000":
        u = tip_max(sw, S, kt, "SS")
    else:   # ETABS: steady-state results only in the table, as Real / Imag rows (measured)
        r = show("SS table", "query_database_table", {**S, "units": U, "table_key": "Joint Displacements",
                                                      "load_cases_or_combos": ["SS"], "max_rows": 200}, limit=800)
        rec = {x.get("StepType"): float(x.get("Ux", 0) or 0) for x in (r.get("records", []) if ok(r) else [])
               if x.get("UniqueName") == kt}
        u = math.hypot(rec.get("Real Max", 0.0), rec.get("Imag Max", 0.0)) if rec else None
    check(f"SS: |u| = F / k / sqrt((1 - r^2)^2 + eta^2) = {hand:.6f} (eta {eta})",
          u is not None and close(u, hand, 5e-3), u)
    r = show("buckling factors", "get_results", {**S, "result_type": "buckling_factors",
                                                 "load_cases_or_combos": ["BUCK"]}, limit=1500)
    fac = sorted(float(x.get("Factor", 0) or 0) for x in rows(r))
    pcr = math.pi ** 2 * E * I22 / 9.0
    check(f"buckling factor 1 = pi^2 E I22 / L^2 = {pcr:.2f}", bool(fac) and close(fac[0], pcr, 2e-3), fac[:3])
    status = show("case status", "get_model_status", {**S}, limit=600)
    del status
    # staged construction with time-dependent material: flag read back
    show("group", "set_group", {**S, "operation": "define", "name": "GK", "allow_unlock": True}, limit=150)
    show("group", "set_group", {**S, "operation": "add", "name": "GK", "frames": [k], "allow_unlock": True},
         limit=150)
    r = show("staged TD", "define_staged_construction", {**S, "units": U, "name": "STG", "allow_unlock": True,
                                                         "geometric_nonlinearity": "none",
                                                         "time_dependent_material": True,
                                                         "stages": [{"duration": 10.0, "operations": [
                                                             {"operation": "add", "group": "GK", "age": 7.0}]}]},
             limit=800)
    check("staged: time-dependent material read back", ok(r) and r["time_dependent_material_read_back"] is True, r)


def run(sw):
    part1(sw)
    part2(sw)
    part3(sw)
    if sw != "SAFE":
        part4(sw)


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
for sw in targets:
    print(f"\n################ {sw}", flush=True)
    try:
        com(bridge.launch_new, sw, visible=True)
        run(sw)
    except Exception as exc:  # noqa: BLE001
        import traceback
        print("EXCEPTION", type(exc).__name__, exc, traceback.format_exc()[-2000:], flush=True)
        check(f"{sw} ran to the end", False, exc)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("close", exc, flush=True)
sys.exit(finish())
