r"""Diagnostic (0.24.0 / roadmap 8.6, 8.7, 8.9, 8.10): which calls each program accepts and what it reads back.
8.7 loads (support displacement, area wind pressure, SAP2000 frame / area strain, deformation, gravity), with one
analysis of a fixed-fixed beam to measure sign conventions; 8.9 geometry edits (SAP2000 join / trim / extend, area
merge / expand-shrink / divide, point align; ETABS / SAFE runtime members and single-point move); 8.10 grid systems,
coordinate systems and constraints (typed calls and tables); 8.6 load cases (buckling, direct-integration and FNA
time history, steady state, PSD, hyperstatic, staged time dependence; typed calls and tables).
Output: tests\results\diag_86.txt"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "diag_86_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import bridge, com, finish, show  # noqa: E402,I001
from csi_bridge import as_list  # noqa: E402

targets = [a.upper() for a in sys.argv[1:] if not a.startswith("--")] or ["SAP2000", "ETABS", "SAFE"]
EXT = {"ETABS": ".edb", "SAP2000": ".sdb", "SAFE": ".fdb"}


def raw(label, fn):
    try:
        r = com(fn)
        print(f"  {label}: {r!r}"[:1500], flush=True)
        return r
    except Exception as exc:  # noqa: BLE001
        print(f"  {label}: EXC {type(exc).__name__}: {exc}"[:600], flush=True)
        return None


def members(label, obj):
    try:
        print(f"  {label} members: {sorted(m for m in dir(obj) if not m.startswith('_'))}"[:3000], flush=True)
    except Exception as exc:  # noqa: BLE001
        print(f"  {label} members: EXC {exc}", flush=True)


def table(sap, key, rows=True):
    raw(f"fields {key}", lambda: sap.DatabaseTables.GetAllFieldsInTable(key))
    if rows:
        raw(f"display {key}", lambda: sap.DatabaseTables.GetTableForDisplayArray(key, [], "All"))
        raw(f"editing {key}", lambda: sap.DatabaseTables.GetTableForEditingArray(key, "All"))


def select(sap, kind, names):
    com(lambda: sap.SelectObj.ClearSelection())
    obj = getattr(sap, kind)
    for n in names:
        com(lambda n=n: obj.SetSelected(n, True))


def run(sw):
    S = {"software": sw, "units": "kN_m_C"}
    z = 3.0 if sw == "ETABS" else 0.0
    nm = {**S, "file_path": str(OUT / f"d86_{sw}{EXT[sw]}"), "discard_open_model": True}
    if sw != "SAP2000":
        nm.update(kind="grid_only", stories=1, story_height=3.0, spacing_x=6.0, spacing_y=6.0)
    show("new model", "new_model", nm, limit=300)
    show("C", "define_material", {**S, "name": "C", "type": "concrete", "E": 2.5e7, "poisson": 0.2,
                                  "unit_weight": 25.0, "fc": 30000.0}, limit=200)
    show("B", "define_frame_section", {**S, "name": "B", "shape": "rectangle", "material": "C", "depth": 0.5,
                                       "width": 0.3}, limit=200)
    for pat, typ in (("DISP", "other"), ("STR", "other"), ("DEF", "other"), ("GRAV", "other"), ("W", "wind")):
        show(pat, "define_load_pattern", {**S, "name": pat, "type": typ, "self_weight_multiplier": 0.0}, limit=200)
    sap = bridge.sap_model(sw)
    com(lambda: sap.SetPresentUnits(6))
    po, fo, ao = sap.PointObj, sap.FrameObj, sap.AreaObj
    # fixed-fixed beam 6 m along X
    f1 = raw("AddByCoord F1", lambda: fo.AddByCoord(0, 0, z, 6, 0, z, "", "B", "F1", "Global"))
    fname = str(f1[0]) if f1 else "F1"
    pts = raw("F1 points", lambda: fo.GetPoints(fname, "", ""))
    pi, pj = str(pts[0]), str(pts[1])
    for p in (pi, pj):
        raw(f"restrain {p}", lambda p=p: po.SetRestraint(p, [True] * 6))

    print("\n---- 8.7 loads", flush=True)
    raw("SetLoadDispl J U3 -0.01 (Global)", lambda: po.SetLoadDispl(pj, "DISP", [0, 0, -0.01, 0, 0, 0], False, "Global"))
    raw("GetLoadDispl J", lambda: po.GetLoadDispl(pj))
    raw("SetLoadDispl free point", lambda: po.SetLoadDispl(pi, "W", [0, 0, -0.005, 0, 0, 0], True, "Local"))
    raw("GetLoadDispl I", lambda: po.GetLoadDispl(pi))
    raw("DeleteLoadDispl I W", lambda: po.DeleteLoadDispl(pi, "W"))
    raw("GetLoadDispl I after delete", lambda: po.GetLoadDispl(pi))
    if sw == "SAP2000":
        raw("Frame SetLoadStrain DOF1 0.001", lambda: fo.SetLoadStrain(fname, "STR", 1, 0.001, True, ""))
        raw("Frame GetLoadStrain", lambda: fo.GetLoadStrain(fname))
        raw("Frame SetLoadDeformation U1 0.002", lambda: fo.SetLoadDeformation(
            fname, "DEF", [True, False, False, False, False, False], [0.002, 0, 0, 0, 0, 0]))
        raw("Frame GetLoadDeformation", lambda: fo.GetLoadDeformation(fname))
        raw("Frame SetLoadGravity z -1", lambda: fo.SetLoadGravity(fname, "GRAV", 0, 0, -1, True, "Global"))
        raw("Frame GetLoadGravity", lambda: fo.GetLoadGravity(fname))
        raw("Frame SetLoadTargetForce", lambda: fo.SetLoadTargetForce(
            fname, "W", [True, False, False, False, False, False], [10.0, 0, 0, 0, 0, 0], [0.5, 0, 0, 0, 0, 0]))
        raw("Frame GetLoadTargetForce", lambda: fo.GetLoadTargetForce(fname))
        raw("Frame DeleteLoadTargetForce", lambda: fo.DeleteLoadTargetForce(fname, "W"))
    # an area: vertical wall at x = 10 (SAFE: a slab)
    show("WALL", "define_area_section", {**S, "name": "WALL", "kind": "wall" if sw != "SAP2000" else "shell_thin",
                                         "material": "C", "thickness": 0.2,
                                         **({"shell_type": "shell_thin"} if sw == "ETABS" else {})}, limit=300)
    if sw == "SAFE":
        show("SLAB", "define_area_section", {**S, "name": "SLAB", "kind": "slab", "material": "C", "thickness": 0.2},
             limit=300)
        a = raw("Area AddByCoord slab", lambda: ao.AddByCoord(4, [10, 12, 12, 10], [0, 0, 2, 2], [0, 0, 0, 0], "",
                                                               "SLAB", "A1", "Global"))
    else:
        zb = 0.0 if sw == "SAP2000" else 0.0
        a = raw("Area AddByCoord wall", lambda: ao.AddByCoord(4, [10, 12, 12, 10], [0, 0, 0, 0],
                                                               [zb, zb, zb + 3, zb + 3], "", "WALL", "A1", "Global"))
    aname = next((str(x) for x in reversed(a or []) if isinstance(x, str) and x), "A1")
    raw("Area SetLoadWindPressure windward 0.8", lambda: ao.SetLoadWindPressure(aname, "W", 1, 0.8))
    raw("Area GetLoadWindPressure", lambda: ao.GetLoadWindPressure(aname))
    raw("Area SetLoadWindPressure leeward 0.5", lambda: ao.SetLoadWindPressure(aname, "W", 2, 0.5))
    raw("Area GetLoadWindPressure 2", lambda: ao.GetLoadWindPressure(aname))
    raw("Area SetLoadWindPressure on DISP (not wind)", lambda: ao.SetLoadWindPressure(aname, "DISP", 1, 0.8))
    raw("Area GetLoadWindPressure 3", lambda: ao.GetLoadWindPressure(aname))
    raw("LoadPatterns.AutoWind.GetAutoCode W", lambda: sap.LoadPatterns.AutoWind.GetAutoCode("W"))
    if sw == "SAP2000":
        raw("Area SetLoadWindPressure_1 dist 2", lambda: ao.SetLoadWindPressure_1(aname, "W", 1, 0.7, 2))
        raw("Area GetLoadWindPressure_1", lambda: ao.GetLoadWindPressure_1(aname))
        raw("Area SetLoadStrain comp 1", lambda: ao.SetLoadStrain(aname, "STR", 1, 0.001, True, ""))
        raw("Area GetLoadStrain", lambda: ao.GetLoadStrain(aname))
        raw("Area SetLoadGravity", lambda: ao.SetLoadGravity(aname, "GRAV", 0, 0, -1, True, "Global"))
        raw("Area GetLoadGravity", lambda: ao.GetLoadGravity(aname))
        raw("Area DeleteLoadStrain", lambda: ao.DeleteLoadStrain(aname, "STR"))
        raw("Area DeleteLoadGravity", lambda: ao.DeleteLoadGravity(aname, "GRAV"))
    # analysis of the beam: delete the area so it does not disturb
    raw("delete area", lambda: ao.Delete(aname))
    show("run", "run_analysis", {**S}, limit=1500)
    cases = ["DISP"] + (["STR", "DEF", "GRAV"] if sw == "SAP2000" else [])
    show("frame forces", "get_results", {**S, "result_type": "frame_forces", "load_cases_or_combos": cases,
                                         "names": [fname], "max_rows": 40}, limit=5000)
    show("reactions", "get_results", {**S, "result_type": "joint_reactions", "load_cases_or_combos": cases,
                                      "max_rows": 20}, limit=3000)
    show("unlock", "open_or_save_model", {**S, "action": "save"}, limit=200)
    raw("SetModelIsLocked False", lambda: sap.SetModelIsLocked(False))

    print("\n---- 8.9 geometry", flush=True)
    for nme in ("EditFrame", "EditArea", "EditPoint", "EditGeneral"):
        members(nme, getattr(sap, nme, None))
    zz = z + (0 if sw == "ETABS" else 5.0) if sw != "SAFE" else 0.0
    yo = 20.0
    mk = lambda x1, y1, x2, y2, n: raw(f"AddByCoord {n}", lambda: fo.AddByCoord(x1, y1 + yo, zz, x2, y2 + yo, zz,
                                                                                    "", "B", n, "Global"))
    for args in ((0, 0, 3, 0, "J1"), (3, 0, 6, 0, "J2"), (0, 2, 8, 2, "T1"), (6, 0, 6, 4, "T2"),
                 (0, 6, 4, 6, "X1"), (6, 5, 6, 8, "X2")):
        mk(*args)
    names = set(str(x) for x in as_list(com(lambda: fo.GetNameList())[1]))
    print("  frames:", sorted(names), flush=True)
    if sw == "SAP2000":
        ef, ea, ep = sap.EditFrame, sap.EditArea, sap.EditPoint
        raw("Join J1 J2", lambda: ef.Join("J1", "J2"))
        raw("J1 points after join", lambda: fo.GetPoints("J1", "", ""))
        raw("Trim T1 J end by T2", lambda: ef.Trim("T1", False, True, "T2", ""))
        raw("T1 points after trim", lambda: fo.GetPoints("T1", "", ""))
        raw("Extend X1 J end to X2", lambda: ef.Extend("X1", False, True, "X2", ""))
        raw("X1 points after extend", lambda: fo.GetPoints("X1", "", ""))
        for n in ("J1", "T1", "X1"):
            p = com(lambda n=n: fo.GetPoints(n, "", ""))
            for q in p[:2]:
                raw(f"  coord {n}.{q}", lambda q=q: po.GetCoordCartesian(str(q), 0, 0, 0))
        # areas
        show("SH", "define_area_section", {**S, "name": "SH", "kind": "shell_thin", "material": "C",
                                           "thickness": 0.2}, limit=200)
        a1 = raw("A10", lambda: ao.AddByCoord(4, [0, 2, 2, 0], [40, 40, 42, 42], [0] * 4, "", "SH", "A10", "Global"))
        a2 = raw("A11", lambda: ao.AddByCoord(4, [2, 4, 4, 2], [40, 40, 42, 42], [0] * 4, "", "SH", "A11", "Global"))
        select(sap, "AreaObj", ["A10", "A11"])
        raw("EditArea.Merge", lambda: ea.Merge(0, []))
        raw("area names after merge", lambda: ao.GetNameList())
        an = [str(x) for x in as_list(com(lambda: ao.GetNameList())[1])]
        for n in an:
            raw(f"  points {n}", lambda n=n: ao.GetPoints(n))
        a3 = raw("A12", lambda: ao.AddByCoord(4, [10, 12, 12, 10], [40, 40, 42, 42], [0] * 4, "", "SH", "A12",
                                              "Global"))
        for ot in (0, 1, 2):
            select(sap, "AreaObj", ["A12"])
            raw(f"ExpandShrink type {ot} 0.5", lambda ot=ot: ea.ExpandShrink(ot, 0.5))
            g = com(lambda: ao.GetPoints("A12"))
            raw("  A12 corners", lambda g=g: [com(lambda q=q: po.GetCoordCartesian(str(q), 0, 0, 0))[:3]
                                              for q in as_list(g[1])])
        select(sap, "AreaObj", ["A12"])
        raw("ExpandShrink -0.25", lambda: ea.ExpandShrink(1, -0.25))
        g = com(lambda: ao.GetPoints("A12"))
        raw("  A12 corners", lambda: [com(lambda q=q: po.GetCoordCartesian(str(q), 0, 0, 0))[:3]
                                      for q in as_list(g[1])])
        # align
        ps = []
        for i, (x, y) in enumerate(((20, 40), (21, 40.3), (22, 39.8))):
            o = com(lambda x=x, y=y: po.AddCartesian(x, y, 0, "", f"AL{i}", "Global"))
            ps.append(str(o[0]))
        for mt in (1, 2, 3, 4):
            select(sap, "PointObj", ps)
            raw(f"Align type {mt} ordinate 40", lambda mt=mt: ep.Align(mt, 40.0, 0, []))
            raw("  coords", lambda: [com(lambda q=q: po.GetCoordCartesian(q, 0, 0, 0))[:3] for q in ps])
        raw("ChangeCoordinates_1 AL0", lambda: ep.ChangeCoordinates_1(ps[0], 20, 41, 0, False))
        raw("  AL0", lambda: po.GetCoordCartesian(ps[0], 0, 0, 0))
    else:
        # single-point move (align route in ETABS / SAFE)
        p = com(lambda: fo.GetPoints("J1", "", ""))
        q = str(p[1])
        raw(f"coord {q} before", lambda: po.GetCoordCartesian(q, 0, 0, 0))
        select(sap, "PointObj", [q])
        raw("EditGeneral.Move point only (0, 0.5, 0)", lambda: sap.EditGeneral.Move(0, 0.5, 0))
        raw(f"coord {q} after", lambda: po.GetCoordCartesian(q, 0, 0, 0))
        raw("J1 points", lambda: fo.GetPoints("J1", "", ""))
        raw("J2 points", lambda: fo.GetPoints("J2", "", ""))
        raw("PointObj.AddCartesian at J1 / J2 joint", lambda: po.AddCartesian(3, yo + 0.5, zz, "", "", "Global"))
        raw("FrameObj.Delete J2", lambda: fo.Delete("J2", 0))
        raw("frames after delete", lambda: fo.GetNameList())
        raw("FrameObj.GetLabelFromName J1", lambda: fo.GetLabelFromName("J1", "", ""))
        raw("AreaObj members", lambda: [m for m in dir(ao) if m.startswith(("Get", "Set")) and "Load" in m])
    sap.SelectObj.ClearSelection()

    print("\n---- 8.10 grids / coordinate systems / constraints", flush=True)
    o = com(lambda: sap.DatabaseTables.GetAllTables())
    keys = [str(k) for k in as_list(o[1])]
    gk = [k for k in keys if any(w in k for w in ("Grid", "Coordinate System", "Constraint"))]
    print("  tables:", gk, flush=True)
    for k in gk:
        table(sap, k)
    if sw == "SAP2000":
        cs = sap.CoordSys
        raw("SetCoordSys CS2", lambda: cs.SetCoordSys("CS2", 10, 5, 0, 30, 0, 0))
        raw("GetCoordSys CS2", lambda: cs.GetCoordSys("CS2"))
        raw("GetCoordSys_2 CS2", lambda: cs.GetCoordSys_2("CS2"))
        raw("GetCoordSys_2 GLOBAL", lambda: cs.GetCoordSys_2("GLOBAL"))
        raw("GetCoordSysType CS2", lambda: cs.GetCoordSysType("CS2"))
        raw("GetNameList", lambda: cs.GetNameList())
        for k in gk:
            table(sap, k)
        c = sap.ConstraintDef
        T6 = [True] * 6
        raw("SetBody B1", lambda: c.SetBody("B1", T6, "Global"))
        raw("SetEqual E1", lambda: c.SetEqual("E1", [True, False, True, False, False, False], "Global"))
        raw("SetLocal L1", lambda: c.SetLocal("L1", [True, False, False, False, False, False]))
        raw("SetWeld W1", lambda: c.SetWeld("W1", T6, 0.01, "Global"))
        raw("SetLine N1", lambda: c.SetLine("N1", T6, "Global"))
        raw("SetPlate P1", lambda: c.SetPlate("P1", 3, "Global"))
        raw("SetRod R1", lambda: c.SetRod("R1", 1, "Global"))
        raw("SetBeam M1", lambda: c.SetBeam("M1", 1, "Global"))
        raw("SetDiaphragm D9", lambda: c.SetDiaphragm("D9", 3, "Global"))
        for n in ("B1", "E1", "L1", "W1", "N1", "P1", "R1", "M1", "D9"):
            raw(f"GetConstraintType {n}", lambda n=n: c.GetConstraintType(n))
        raw("GetBody B1", lambda: c.GetBody("B1"))
        raw("GetEqual E1", lambda: c.GetEqual("E1"))
        raw("GetWeld W1", lambda: c.GetWeld("W1"))
        raw("GetPlate P1", lambda: c.GetPlate("P1"))
        raw("SetConstraint pi B1", lambda: po.SetConstraint(pi, "B1", 0, True))
        raw("SetConstraint pj B1", lambda: po.SetConstraint(pj, "B1", 0, True))
        raw("SetConstraint pj E1 (replace false)", lambda: po.SetConstraint(pj, "E1", 0, False))
        raw("GetConstraint pj", lambda: po.GetConstraint(pj))
        raw("DeleteConstraint pj", lambda: po.DeleteConstraint(pj))
        raw("GetConstraint pj after", lambda: po.GetConstraint(pj))
        raw("Count", lambda: c.Count())
        raw("GetNameList", lambda: c.GetNameList())
        for k in keys:
            if "Constraint" in k:
                table(sap, k)
    else:
        g = sap.GridSys
        members("GridSys", g)
        raw("GetNameTypeList", lambda: g.GetNameTypeList())
        names = [str(x) for x in as_list(com(lambda: g.GetNameList())[1])]
        for n in names:
            raw(f"GetGridSys_2 {n}", lambda n=n: g.GetGridSys_2(n))
            raw(f"GetGridSysCartesian {n}", lambda n=n: g.GetGridSysCartesian(n))
        raw("SetGridSys G2 (10, 5, 30)", lambda: g.SetGridSys("G2", 10, 5, 30))
        raw("SetGridSys existing (origin 1, 2, 0)", lambda: g.SetGridSys(names[0], 1, 2, 0) if names else None)
        raw("GetGridSys_2 G2", lambda: g.GetGridSys_2("G2"))
        raw("GetNameTypeList after", lambda: g.GetNameTypeList())
        members("CoordSys", getattr(sap, "CoordSys", None))
        c = sap.ConstraintDef
        members("ConstraintDef", c)
        raw("SetBody (E/F)", lambda: c.SetBody("B1", [True] * 6, "Global"))
        for k in gk:
            table(sap, k)

    print("\n---- 8.6 load cases", flush=True)
    lc = sap.LoadCases
    members("LoadCases", lc)
    for nme in ("Buckling", "DirHistLinear", "DirHistNonlinear", "ModHistNonlinear", "ModHistLinear", "HyperStatic",
                "SteadyState", "PSD", "StaticLinearMultistep", "StaticNonlinearStaged"):
        members(nme, getattr(lc, nme, None))
    fn = sap.Func
    members("Func", fn)
    raw("FuncTH.SetUser STEP", lambda: fn.FuncTH.SetUser("STEP", 2, [0.0, 10.0], [1.0, 1.0]))
    raw("FuncTH.GetUser STEP", lambda: fn.FuncTH.GetUser("STEP"))
    if sw == "SAP2000":
        raw("FuncSS.SetUser SS1", lambda: fn.FuncSS.SetUser("SS1", 2, [0.0, 100.0], [1.0, 1.0]))
        raw("FuncSS.GetUser SS1", lambda: fn.FuncSS.GetUser("SS1"))
        raw("FuncPSD.SetUser PSD1", lambda: fn.FuncPSD.SetUser("PSD1", 2, [0.1, 100.0], [1.0, 1.0]))
        raw("FuncPSD.GetUser PSD1", lambda: fn.FuncPSD.GetUser("PSD1"))
        for n in ("STEP", "SS1", "PSD1"):
            raw(f"Func.GetTypeOAPI {n}", lambda n=n: fn.GetTypeOAPI(n))
    raw("ModalEigen.SetCase MODAL", lambda: lc.ModalEigen.SetCase("MODALX"))
    b = lc.Buckling
    raw("Buckling.SetCase", lambda: b.SetCase("BUCK"))
    raw("Buckling.SetLoads", lambda: b.SetLoads("BUCK", 1, ["Load"], ["DISP"], [1.0]))
    raw("Buckling.SetParameters", lambda: b.SetParameters("BUCK", 6, 1e-9))
    raw("Buckling.GetLoads", lambda: b.GetLoads("BUCK"))
    raw("Buckling.GetParameters", lambda: b.GetParameters("BUCK"))
    raw("Buckling.GetInitialCase", lambda: b.GetInitialCase("BUCK"))
    TH = (1, ["Load"], ["DISP"], ["STEP"], [1.0], [1.0], [0.0], ["Global"], [0.0])
    d = lc.DirHistLinear
    raw("DirHistLinear.SetCase", lambda: d.SetCase("THL"))
    raw("DirHistLinear.SetLoads", lambda: d.SetLoads("THL", *TH))
    raw("DirHistLinear.SetTimeStep", lambda: d.SetTimeStep("THL", 200, 0.005))
    raw("DirHistLinear.GetTimeIntegration (default)", lambda: d.GetTimeIntegration("THL"))
    raw("DirHistLinear.SetTimeIntegration Newmark", lambda: d.SetTimeIntegration("THL", 1, 0, 0.25, 0.5, 1.0, 0))
    raw("DirHistLinear.GetTimeIntegration", lambda: d.GetTimeIntegration("THL"))
    raw("DirHistLinear.GetDampProportional (default)", lambda: d.GetDampProportional("THL"))
    raw("DirHistLinear.SetDampProportional mass/stiff 0 0", lambda: d.SetDampProportional("THL", 1, 0, 0, 0, 0, 0, 0))
    raw("DirHistLinear.GetDampProportional", lambda: d.GetDampProportional("THL"))
    raw("DirHistLinear.GetLoads", lambda: d.GetLoads("THL"))
    raw("DirHistLinear.GetTimeStep", lambda: d.GetTimeStep("THL"))
    dn = lc.DirHistNonlinear
    raw("DirHistNonlinear.SetCase", lambda: dn.SetCase("THN"))
    raw("DirHistNonlinear.SetLoads", lambda: dn.SetLoads("THN", *TH))
    raw("DirHistNonlinear.SetTimeStep", lambda: dn.SetTimeStep("THN", 200, 0.005))
    raw("DirHistNonlinear.SetGeometricNonlinearity 1", lambda: dn.SetGeometricNonlinearity("THN", 1))
    raw("DirHistNonlinear.GetGeometricNonlinearity", lambda: dn.GetGeometricNonlinearity("THN"))
    raw("DirHistNonlinear.GetSolControlParameters", lambda: dn.GetSolControlParameters("THN"))
    raw("DirHistNonlinear.GetTimeIntegration", lambda: dn.GetTimeIntegration("THN"))
    raw("DirHistNonlinear.GetDampProportional", lambda: dn.GetDampProportional("THN"))
    raw("DirHistNonlinear.GetMassSource", lambda: dn.GetMassSource("THN"))
    raw("DirHistNonlinear.GetLoads", lambda: dn.GetLoads("THN"))
    mn = lc.ModHistNonlinear
    raw("ModHistNonlinear.SetCase", lambda: mn.SetCase("FNA"))
    raw("ModHistNonlinear.SetModalCase", lambda: mn.SetModalCase("FNA", "MODALX"))
    raw("ModHistNonlinear.SetLoads", lambda: mn.SetLoads("FNA", *TH))
    raw("ModHistNonlinear.SetTimeStep", lambda: mn.SetTimeStep("FNA", 200, 0.005))
    raw("ModHistNonlinear.GetDampConstant (default)", lambda: mn.GetDampConstant("FNA"))
    raw("ModHistNonlinear.SetDampConstant 0.02", lambda: mn.SetDampConstant("FNA", 0.02))
    raw("ModHistNonlinear.GetDampConstant", lambda: mn.GetDampConstant("FNA"))
    raw("ModHistNonlinear.GetDampType", lambda: mn.GetDampType("FNA"))
    raw("ModHistNonlinear.GetSolControlParameters", lambda: mn.GetSolControlParameters("FNA"))
    raw("ModHistNonlinear.GetModalCase", lambda: mn.GetModalCase("FNA"))
    raw("ModHistNonlinear.GetLoads", lambda: mn.GetLoads("FNA"))
    ml = lc.ModHistLinear
    raw("ModHistLinear.GetMotionType (existing?)", lambda: ml.GetMotionType("FNA"))
    if sw == "SAP2000":
        ss = lc.SteadyState
        SS = (1, ["Load"], ["DISP"], ["SS1"], [1.0], [0.0], ["Global"], [0.0])
        raw("SteadyState.SetCase", lambda: ss.SetCase("SS"))
        raw("SteadyState.SetLoads", lambda: ss.SetLoads("SS", *SS))
        raw("SteadyState.SetFreqData", lambda: ss.SetFreqData("SS", 0.0, 10.0, 10, False, False, False, "MODALX", 0, [],
                                                               0, []))
        raw("SteadyState.SetDampConstant 0 0", lambda: ss.SetDampConstant("SS", 0.0, 0.0))
        raw("SteadyState.GetDampType", lambda: ss.GetDampType("SS"))
        raw("SteadyState.GetFreqData", lambda: ss.GetFreqData("SS"))
        raw("SteadyState.GetLoads", lambda: ss.GetLoads("SS"))
        raw("SteadyState.GetDampConstant", lambda: ss.GetDampConstant("SS"))
        pd = lc.PSD
        PS = (1, ["Load"], ["DISP"], ["PSD1"], [1.0], [0.0], ["Global"], [0.0])
        raw("PSD.SetCase", lambda: pd.SetCase("PSD"))
        raw("PSD.SetLoads", lambda: pd.SetLoads("PSD", *PS))
        raw("PSD.SetFreqData", lambda: pd.SetFreqData("PSD", 0.1, 10.0, 10, True, False, False, "MODALX", 0, [], 0, []))
        raw("PSD.GetFreqData", lambda: pd.GetFreqData("PSD"))
        raw("PSD.GetLoads", lambda: pd.GetLoads("PSD"))
        raw("PSD.GetDampType", lambda: pd.GetDampType("PSD"))
        ms = lc.StaticLinearMultistep
        raw("StaticLinearMultistep.SetCase", lambda: ms.SetCase("MS"))
        raw("StaticLinearMultistep.SetLoads", lambda: ms.SetLoads("MS", 1, ["Load"], ["DISP"], [1.0]))
        raw("StaticLinearMultistep.GetLoads", lambda: ms.GetLoads("MS"))
        raw("StaticLinearMultistep.GetLoads_1", lambda: ms.GetLoads_1("MS"))
    h = lc.HyperStatic
    raw("HyperStatic.SetCase", lambda: h.SetCase("HYP"))
    raw("HyperStatic.SetBaseCase DISP", lambda: h.SetBaseCase("HYP", "DISP"))
    raw("HyperStatic.GetBaseCase", lambda: h.GetBaseCase("HYP"))
    st = lc.StaticNonlinearStaged
    raw("Staged.SetCase", lambda: st.SetCase("STG"))
    raw("Staged.GetMaterialNonlinearity (default)", lambda: st.GetMaterialNonlinearity("STG"))
    raw("Staged.SetMaterialNonlinearity True", lambda: st.SetMaterialNonlinearity("STG", True))
    raw("Staged.GetMaterialNonlinearity", lambda: st.GetMaterialNonlinearity("STG"))
    raw("Staged.GetResultsSaved", lambda: st.GetResultsSaved("STG"))
    for n in ("BUCK", "THL", "THN", "FNA", "SS", "PSD", "MS", "HYP", "STG", "MODALX"):
        raw(f"GetTypeOAPI_1 {n}", lambda n=n: lc.GetTypeOAPI_1(n))
    raw("case names", lambda: lc.GetNameList())
    ck = [k for k in keys if "Load Case" in k or "Case" in k and "Definitions" in k]
    print("  case tables:", ck, flush=True)
    for k in ck:
        if any(w in k for w in ("Buckling", "Direct", "Nonlinear", "Hyper", "FNA", "Modal History", "Steady", "Power",
                                "Summary", "Time History", "Multistep")):
            table(sap, k)


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
        print("EXCEPTION", type(exc).__name__, exc, traceback.format_exc()[-1500:], flush=True)
    finally:
        try:
            print(com(bridge.disconnect, sw, True, False), flush=True)
        except Exception as exc:  # noqa: BLE001
            print("close", exc, flush=True)
sys.exit(finish())
