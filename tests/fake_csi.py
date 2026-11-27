"""
fake_csi.py - In-memory stand-in for the CSI OAPI that mimics comtypes return
conventions (out-params first, return code last). Lets the MCP tools be tested
without Windows, ETABS, SAP2000 or SAFE.
"""

from __future__ import annotations

import os
import tempfile


class _Names:
    def __init__(self, names, types=None):
        self.names = list(names)
        self.types = types or {}

    def SetPier(self, name, label, *a):
        self.pier = getattr(self, "pier", {})
        self.pier[name] = label
        return 0

    def GetPier(self, name, *a):
        return [getattr(self, "pier", {}).get(name, "None"), 0]

    def SetSpandrel(self, name, label, *a):
        self.spandrel = getattr(self, "spandrel", {})
        self.spandrel[name] = label
        return 0

    def GetSpandrel(self, name, *a):
        return [getattr(self, "spandrel", {}).get(name, "None"), 0]

    def GetNameList(self, *args):
        return [len(self.names), tuple(self.names), 0]

    def Count(self, *args):
        return len(self.names)

    def GetLoadType(self, name):
        return [self.types.get(name, 8), 0]

    def GetTypeOAPI(self, name):
        return [self.types.get(name, 1), 0, 0]

    def GetCaseList(self, name):
        return [2, (0, 0), ("Dead", "Live"), (1.2, 1.6), 0]


# Phase 1 geometry: one bay (6 m) on Story1, two columns fixed at the base, beam F1, slab A1 on the top points.
XYZ = {"1": (0.0, 0.0, 0.0), "2": (6.0, 0.0, 0.0), "3": (0.0, 0.0, 3.0), "4": (6.0, 0.0, 3.0),
       "5": (0.0, 4.0, 3.0), "6": (6.0, 4.0, 3.0)}
FRAMES = {"F1": ("3", "4"), "C1": ("1", "3"), "C2": ("2", "4")}
_XYZ0, _FRAMES0 = dict(XYZ), dict(FRAMES)


def _reset_geometry() -> None:
    """Each new fake model starts from the same geometry (objects added by earlier tests are dropped)."""
    XYZ.clear()
    XYZ.update(_XYZ0)
    FRAMES.clear()
    FRAMES.update(_FRAMES0)
    _Areas.PTS.clear()
    _Areas.PTS.update({"A1": ("3", "4", "6", "5")})


def _story(z):
    return "Base" if z == 0 else "Story1"


class _Points(_Names):
    def __init__(self, model):
        super().__init__([k for k in XYZ if k != "E"])   # "E" appears only after a Divide
        self.model = model
        self.loads = {}
        self.patterns = {}
        self.restraints = {}

    def SetPatternByXYZ(self, name, pat, a, b, c, d, item, restriction, replace):
        targets = self.names if item == 1 else [name]
        for t in targets:
            x, y, z = XYZ[t]
            v = a * x + b * y + c * z + d
            if restriction == 1 and v < 0:
                v = 0.0
            self.patterns[(t, pat)] = v
        return 0

    def GetPatternValue(self, name, pat, *a):
        return [self.patterns.get((name, pat), 0.0), 0]

    def GetAllPoints(self, *a):
        n = self.names
        return [len(n), tuple(n), tuple(XYZ[k][0] for k in n), tuple(XYZ[k][1] for k in n),
                tuple(XYZ[k][2] for k in n), 0]

    def GetCoordCartesian(self, name, *a):
        return [*XYZ[name], 0]

    def GetRestraint(self, name, *a):
        if name in self.restraints:
            return [tuple(self.restraints[name]), 0]
        return [tuple([name in ("1", "2")] * 6), 0]

    def SetSpecialPoint(self, name, special, *a):
        self.special = getattr(self, "special", set())
        if special:
            self.special.add(name)
        return 0

    def SetRestraint(self, name, value, *a):
        self.restraints[name] = tuple(bool(v) for v in value)
        return [tuple(value), 0]

    def GetLabelNameList(self, *a):
        n = self.names
        return [len(n), tuple(n), tuple(n), tuple(_story(XYZ[k][2]) for k in n), 0]

    def GetGroupAssign(self, name, *a):
        return [2, ("All", "EDGE"), 0] if name == "1" else [1, ("All",), 0]

    def GetLoadForce(self, name, *a):
        items = [(pat, v) for (pt, pat), (v, _) in self.loads.items() if pt == name]
        if not items:
            return [0, (), (), (), (), (), (), (), (), (), (), 1]
        cols = list(zip(*[v for _, v in items]))
        return [len(items), tuple(name for _ in items), tuple(p for p, _ in items), tuple(0 for _ in items),
                tuple("Global" for _ in items), *cols, 0]

    def SetLoadForce(self, name, pattern, value, replace=False, csys="Global", item=0):
        if name not in self.names:
            return [value, 1]
        self.loads[(name, pattern)] = (list(value), self.model.units)
        return [value, 0]

    # Phase 3 batch 3 (objects)
    def AddCartesian(self, x, y, z, name="", user="", csys="Global", *a):
        for k, c in XYZ.items():
            if k in self.names and all(abs(c[i] - v) < 1e-9 for i, v in enumerate((x, y, z))):
                return [k, 0]
        new = user or f"P{len(XYZ) + 1}"
        XYZ[new] = (float(x), float(y), float(z))
        self.names.append(new)
        return [new, 0]

    def _put(self, store, name, value):
        getattr(self, store)[name] = value
        return 0

    def SetSpring(self, name, k, item=0, local=True, replace=False):
        self.springs = getattr(self, "springs", {})
        self.springs[name] = tuple(k)
        return [tuple(k), 0]

    def GetSpring(self, name, *a):
        return [getattr(self, "springs", {}).get(name, (0.0,) * 6), 0]

    def DeleteSpring(self, name, *a):
        getattr(self, "springs", {}).pop(name, None)
        return 0

    def SetMass(self, name, m, item=0, local=True, replace=False):
        self.masses = getattr(self, "masses", {})
        self.masses[name] = tuple(m)
        return [tuple(m), 0]

    def GetMass(self, name, *a):
        return [getattr(self, "masses", {}).get(name, (0.0,) * 6), 0]

    def DeleteMass(self, name, *a):
        getattr(self, "masses", {}).pop(name, None)
        return 0

    def DeleteRestraint(self, name, *a):
        self.restraints[name] = (False,) * 6
        return 0

    def SetSpringAssignment(self, name, prop, *a):
        self.spring_prop = getattr(self, "spring_prop", {})
        self.spring_prop[name] = "" if prop == "None" else prop
        return 0

    def GetSpringAssignment(self, name, *a):
        return [getattr(self, "spring_prop", {}).get(name, ""), 0]

    def SetDiaphragm(self, name, option, dia="", *a):
        self.dia = getattr(self, "dia", {})
        self.dia[name] = (option, dia)
        return 0

    def GetDiaphragm(self, name, *a):
        return [*getattr(self, "dia", {}).get(name, (2, "")), 0]

    def DeleteSpecialPoint(self, name, *a):
        used = {p for f in FRAMES.values() for p in f} | {p for v in _Areas.PTS.values() for p in v}
        if name not in used:
            self.names.remove(name)
        return 0

    def SetGroupAssign(self, name, group, remove=False, item=0):
        return self.model.GroupDef.assign(group, 1, name, remove)

    def SetSelected(self, name, sel, item=0):
        return self.model.SelectObj.mark(1, name, sel)


class _Frames(_Names):
    def __init__(self, model):
        super().__init__(list(FRAMES))
        self.model = model
        self.sections = {"F1": "B300x600", "C1": "C500x500", "C2": "C500x500"}
        self.dist = {}
        self.created = []

    def GetAllFrames(self, *a):
        n = [k for k in self.names if k in FRAMES]
        p1 = [XYZ[FRAMES[k][0]] for k in n]
        p2 = [XYZ[FRAMES[k][1]] for k in n]
        z = tuple(0.0 for _ in n)
        return [len(n), tuple(n), tuple(self.sections[k] for k in n), tuple("Story1" for _ in n),
                tuple(FRAMES[k][0] for k in n), tuple(FRAMES[k][1] for k in n),
                *(tuple(p[i] for p in p1) for i in range(3)), *(tuple(p[i] for p in p2) for i in range(3)),
                z, z, z, z, z, z, z, tuple(10 for _ in n), 0]

    def GetPoints(self, name, *a):
        return [*FRAMES[name], 0]

    def GetLabelNameList(self, *a):
        n = [k for k in self.names if k in FRAMES]
        return [len(n), tuple(n), tuple("B" + k for k in n), tuple("Story1" for _ in n), 0]

    def GetReleases(self, name, *a):
        if name in getattr(self, "releases", {}):
            ii, jj = self.releases[name]
            return [ii, jj, (0.0,) * 6, (0.0,) * 6, 0]
        jj = (False, False, False, False, False, name == "F1")
        return [(False,) * 6, jj, (0.0,) * 6, (0.0,) * 6, 0]

    def GetModifiers(self, name, *a):
        if name in getattr(self, "mods", {}):
            return [self.mods[name], 0]
        return [(1, 1, 1, 1, 1, 0.35 if name == "F1" else 0.7, 1, 1), 0]

    def GetLocalAxes(self, name, *a):
        return [getattr(self, "axes", {}).get(name, 0.0), False, 0]

    def GetGroupAssign(self, name, *a):
        return [1, ("All",), 0]

    def GetLoadDistributed(self, name, *a):
        items = [(pat, v) for (fr, pat), v in self.dist.items() if fr == name]
        if not items:
            return [0] + [()] * 11 + [1]
        cols = [tuple(c) for c in zip(*[(pat, t, cs, d, d1, d2, d1, d2, w1, w2)
                                        for pat, (t, d, d1, d2, w1, w2, cs, _) in items])]
        return [len(items), tuple(name for _ in items), *cols, 0]

    def AddByCoord(self, xi, yi, zi, xj, yj, zj, name, prop, user, csys):
        k = len(self.names) + 1
        while f"F{k}" in self.names or f"F{k}" in FRAMES:
            k += 1
        new = user or f"F{k}"
        self.names.append(new)
        self.sections[new] = prop
        self.created.append(((xi, yi, zi, xj, yj, zj), self.model.units))
        pi = self.model.PointObj.AddCartesian(xi, yi, zi)[0]
        pj = self.model.PointObj.AddCartesian(xj, yj, zj)[0]
        FRAMES[new] = (pi, pj)
        return [new, 0]

    def AddByPoint(self, p1, p2, name, prop, user=""):
        k = len(self.names) + 1
        while f"F{k}" in self.names or f"F{k}" in FRAMES:
            k += 1
        new = user or f"F{k}"
        self.names.append(new)
        self.sections[new] = prop
        FRAMES[new] = (p1, p2)
        return [new, 0]

    def SetReleases(self, name, ii, jj, v1, v2, *a):
        if all(ii[k] and jj[k] for k in (0,)) or (ii[3] and jj[3]):
            return [tuple(ii), tuple(jj), tuple(v1), tuple(v2), 1]     # unstable release, like the program
        self.releases = getattr(self, "releases", {})
        self.releases[name] = (tuple(ii), tuple(jj))
        return [tuple(ii), tuple(jj), tuple(v1), tuple(v2), 0]

    def SetEndLengthOffset(self, name, auto, l1, l2, rz, *a):
        self.offsets = getattr(self, "offsets", {})
        self.offsets[name] = (auto, l1, l2, rz)
        return 0

    def GetEndLengthOffset(self, name, *a):
        return [*getattr(self, "offsets", {}).get(name, (False, 0.0, 0.0, 0.0)), 0]

    def SetInsertionPoint(self, name, cp, mirror, stiff, o1, o2, csys="Local", *a):
        self.insertion = getattr(self, "insertion", {})
        self.insertion[name] = (cp, tuple(o1), tuple(o2), csys)
        return [tuple(o1), tuple(o2), 0]

    def GetInsertionPoint_1(self, name, *a):
        cp, o1, o2, cs = getattr(self, "insertion", {}).get(name, (10, (0.0,) * 3, (0.0,) * 3, "Local"))
        return [cp, False, False, True, o1, o2, cs, 0]

    def SetModifiers(self, name, value, *a):
        self.mods = getattr(self, "mods", {})
        self.mods[name] = tuple(value)
        return [tuple(value), 0]

    def SetLocalAxes(self, name, ang, *a):
        self.axes = getattr(self, "axes", {})
        self.axes[name] = ang
        return 0

    def SetSpringAssignment(self, name, prop, *a):
        self.spring_prop = getattr(self, "spring_prop", {})
        self.spring_prop[name] = "" if prop == "None" else prop
        return 0

    def GetSpringAssignment(self, name, *a):
        return [getattr(self, "spring_prop", {}).get(name, ""), 0]

    def SetOutputStations(self, name, kind, seg, mins, no_ends=False, no_loads=False, item=0):
        self.stations = getattr(self, "stations", {})
        self.stations[name] = (kind, seg, mins, no_ends, no_loads)
        return 0

    def GetOutputStations(self, name, *a):
        return [*getattr(self, "stations", {}).get(name, (2, 24.0, 3, False, False)), 0]

    def SetLoadTemperature(self, name, pat, kind, value, jp="", replace=True, item=0):
        self.temp = getattr(self, "temp", {})
        self.temp[name] = ([] if replace else self.temp.get(name, [])) + [(pat, kind, value)]
        return 0

    def GetLoadTemperature(self, name, *a):
        t = getattr(self, "temp", {}).get(name, [])
        return [len(t), tuple(name for _ in t), tuple(x[0] for x in t), tuple(x[1] for x in t),
                tuple(x[2] for x in t), tuple("" for _ in t), 0 if t else 1]

    def Delete(self, name, *a):
        self.names.remove(name)
        FRAMES.pop(name, None)
        return 0

    def SetGroupAssign(self, name, group, remove=False, item=0):
        return self.model.GroupDef.assign(group, 2, name, remove)

    def SetSelected(self, name, sel, item=0):
        return self.model.SelectObj.mark(2, name, sel)

    def GetSection(self, name):
        return [self.sections[name], "", 0]

    def SetSection(self, name, prop, *a):
        self.sections[name] = prop
        return 0

    def GetLabelFromName(self, name):
        return ["B" + name, "Story1  ", 0]

    def SetLoadPoint(self, name, pat, my_type, d, dist, val, csys="Global", rel=True, replace=True, item=0):
        if name not in self.names:
            return 1
        self.pointloads = getattr(self, "pointloads", {})
        self.pointloads[(name, pat)] = (my_type, d, dist, val, csys)
        return 0

    def GetLoadPoint(self, name, *a):
        pl = [(pat, v) for (fr, pat), v in getattr(self, "pointloads", {}).items() if fr == name]
        if not pl:
            return [0] + [()] * 9 + [1]
        return [len(pl), tuple(name for _ in pl), tuple(p for p, _ in pl), tuple(v[0] for _, v in pl),
                tuple(v[4] for _, v in pl), tuple(v[1] for _, v in pl), tuple(v[2] for _, v in pl),
                tuple(0.0 for _ in pl), tuple(v[3] for _, v in pl), 0]

    def SetLoadDistributed(self, name, pat, my_type, d, d1, d2, w1, w2, csys, rel, replace, item):
        if name not in self.names:
            return 1
        self.dist[(name, pat)] = (my_type, d, d1, d2, w1, w2, csys, self.model.units)
        return 0


class _Areas(_Names):
    PTS = {"A1": ("3", "4", "6", "5")}

    def __init__(self, model):
        super().__init__(["A1"])
        self.model = model
        self.uniform = {"A1": [("Live", 10, 2.0)]}
        self.mesh, self.to_frame, self.pressure = {}, {}, {}

    def GetAllAreas(self, *a):
        pts = [p for k in self.names for p in self.PTS[k]]
        delim, end = [], -1
        for k in self.names:
            end += len(self.PTS[k])
            delim.append(end)
        return [len(self.names), tuple(self.names), tuple(1 for _ in self.names), len(pts), tuple(delim), tuple(pts),
                *(tuple(XYZ[p][i] for p in pts) for i in range(3)), 0]

    def GetPoints(self, name, *a):
        return [len(self.PTS[name]), self.PTS[name], 0]

    def GetProperty(self, name, *a):
        return [getattr(self, "props", {}).get(name, "Slab200"), 0]

    # Phase 3 batch 3 (objects)
    def AddByPoint(self, n, pts, name, prop, user=""):
        new = user or f"A{len(self.names) + 1}"
        self.PTS[new] = tuple(pts)
        self.names.append(new)
        self.props = getattr(self, "props", {})
        self.props[new] = prop
        return [tuple(pts), new, 0]

    def AddByCoord(self, n, xs, ys, zs, name, prop, user="", csys="Global"):
        pts = [self.model.PointObj.AddCartesian(x, y, z)[0] for x, y, z in zip(xs, ys, zs)]
        out = self.AddByPoint(n, pts, name, prop, user)
        return [tuple(xs), tuple(ys), tuple(zs), out[1], 0]

    def _store(self, attr, name, value):
        d = getattr(self, attr, {})
        d[name] = value
        setattr(self, attr, d)
        return 0

    def SetProperty(self, name, prop, *a):
        return self._store("props", name, prop)

    def SetModifiers(self, name, value, *a):
        self._store("mods", name, tuple(value))
        return [tuple(value), 0]

    def GetModifiers(self, name, *a):
        return [getattr(self, "mods", {}).get(name, (1.0,) * 10), 0]

    def SetLocalAxes(self, name, ang, *a):
        return self._store("axes", name, ang)

    def GetLocalAxes(self, name, *a):
        return [getattr(self, "axes", {}).get(name, 0.0), False, 0]

    def SetOpening(self, name, value, *a):
        return self._store("opening", name, value)

    def GetOpening(self, name, *a):
        return [getattr(self, "opening", {}).get(name, False), 0]

    def SetDiaphragm(self, name, dia, *a):
        return self._store("dia", name, "" if dia == "None" else dia)

    def GetDiaphragm(self, name, *a):
        return [getattr(self, "dia", {}).get(name, ""), 0]

    def SetSpringAssignment(self, name, prop, *a):
        return self._store("spring_prop", name, "" if prop == "None" else prop)

    def GetSpringAssignment(self, name, *a):
        return [getattr(self, "spring_prop", {}).get(name, ""), 0]

    def SetSpring(self, name, my_type, k, simple, link, face, local1, d, outward, vec, ang, replace, csys="Local",
                  *a):
        self._store("springs", name, (k, face, d))
        return [tuple(vec), 0]

    def GetSpring(self, name, *a):
        sp = getattr(self, "springs", {}).get(name)
        return [1 if sp else 0, 0] if sp else [0, 0]

    def DeleteSpring(self, name, *a):
        getattr(self, "springs", {}).pop(name, None)
        return 0

    def SetLoadTemperature(self, name, pat, kind, value, jp="", replace=True, item=0):
        t = getattr(self, "temp", {})
        t[name] = ([] if replace else t.get(name, [])) + [(pat, kind, value)]
        self.temp = t
        return 0

    def GetLoadTemperature(self, name, *a):
        t = getattr(self, "temp", {}).get(name, [])
        return [len(t), tuple(name for _ in t), tuple(x[0] for x in t), tuple(x[1] for x in t),
                tuple(x[2] for x in t), tuple("" for _ in t), 0 if t else 1]

    def Delete(self, name, *a):
        self.names.remove(name)
        self.PTS.pop(name, None)
        return 0

    def GetLabelFromName(self, name):
        return ["F" + name, "Story1", 0]

    def SetGroupAssign(self, name, group, remove=False, item=0):
        return self.model.GroupDef.assign(group, 5, name, remove)

    def SetSelected(self, name, sel, item=0):
        return self.model.SelectObj.mark(5, name, sel)

    def GetLabelNameList(self, *a):
        return [1, ("A1",), ("F1",), ("Story1",), 0]

    def GetGroupAssign(self, name, *a):
        return [1, ("All",), 0]

    # Phase 3 (SAP2000-style area API)
    def SetAutoMesh_1(self, name, mesh_type, n1, n2, s1, s2, *rest):
        self.mesh[name] = (mesh_type, n1, n2, s1, s2, rest[2] if len(rest) > 2 else 0.0)
        return 0

    def GetAutoMesh_1(self, name, *a):
        t, n1, n2, s1, s2, g = self.mesh.get(name, (0, 2, 2, 0.0, 0.0, 0.0))
        return [t, n1, n2, s1, s2, False, False, False, 0.0, g, False, False, False, False, "ALL", False, 0.0, True,
                False, 0]

    def SetLoadUniform(self, name, pat, value, d, replace, csys, item):
        if name not in self.names:
            return 0            # like ETABS / SAFE: success for a name that does not exist
        self.uniform[name] = ([] if replace else self.uniform.get(name, [])) + [(pat, d, value)]
        return 0

    def SetLoadUniformToFrame(self, name, pat, value, d, dist, replace, csys, item):
        self.to_frame[name] = [(pat, csys, d, value, dist)]
        return 0

    def GetLoadUniformToFrame(self, name, *a):
        u = self.to_frame.get(name, [])
        return [len(u), tuple(name for _ in u), *(tuple(x[i] for x in u) for i in range(5)), 0 if u else 1]

    def SetLoadSurfacePressure(self, name, pat, face, value, jp, replace, item):
        self.pressure[name] = [(pat, face, value, jp)]
        return 0

    def GetLoadSurfacePressure(self, name, *a):
        u = self.pressure.get(name, [])
        return [len(u), tuple(name for _ in u), *(tuple(x[i] for x in u) for i in range(4)), 0 if u else 1]

    def GetLoadUniform(self, name, *a):
        u = self.uniform.get(name, [])
        return [len(u), tuple(name for _ in u), tuple(x[0] for x in u), tuple("Global" for _ in u),
                tuple(x[1] for x in u), tuple(x[2] for x in u), 0 if u else 1]


class _Patterns2(_Names):
    """PatternDef (SAP2000 joint patterns)."""

    def SetPattern(self, name):
        if name not in self.names:
            self.names.append(name)
        return 0


class _EditArea:
    def __init__(self, model):
        self.model = model

    def Divide_1(self, *a):   # SAP2000 27.1 through comtypes: every argument count is refused
        raise TypeError("Invalid number of parameters.")

    def Divide(self, name, mesh_type, n, names, n1, n2, *a):
        """New areas; one new joint E at the middle of the original first edge (corners 0 and 1)."""
        new = [f"{name}-{i}" for i in range(int(n1) * int(n2))]
        areas = self.model.AreaObj
        corners = areas.PTS.get(name, ("3", "4", "6", "5"))
        a0, a1 = XYZ[corners[0]], XYZ[corners[1]]
        XYZ["E"] = tuple((a0[i] + a1[i]) / 2 for i in range(3))
        areas.names = [x for x in areas.names if x != name] + new
        for x in new:
            areas.PTS[x] = (corners[0], "E", corners[2], corners[3])
        return [len(new), tuple(new), 0]


class _EditGeneral:
    """Selection-based tools: points of the selected frames / areas move or are copied."""
    def __init__(self, model):
        self.model = model

    def _sel_points(self):
        m, pts = self.model, []
        for t, n in m.SelectObj.sel:
            cand = [n] if t == 1 else list(FRAMES[n]) if t == 2 else list(m.AreaObj.PTS[n]) if t == 5 else []
            pts += [q for q in cand if q not in pts]
        return pts

    def Move(self, dx, dy, dz):
        for q in self._sel_points():
            x, y, z = XYZ[q]
            XYZ[q] = (x + dx, y + dy, z + dz)
        return 0

    def ReplicateLinear(self, dx, dy, dz, n, num, names, types, remove=False):
        m, out = self.model, []
        sel = list(m.SelectObj.sel)
        for k in range(1, n + 1):
            for t, name in sel:
                if t == 2:
                    a, b = (XYZ[q] for q in FRAMES[name])
                    pa = m.PointObj.AddCartesian(a[0] + k * dx, a[1] + k * dy, a[2] + k * dz)[0]
                    pb = m.PointObj.AddCartesian(b[0] + k * dx, b[1] + k * dy, b[2] + k * dz)[0]
                    out.append(m.FrameObj.AddByPoint(pa, pb, "", m.FrameObj.sections.get(name, "B300x600"))[0])
        return [len(out), tuple(out), tuple(2 for _ in out), 0]

    def ExtrudePointToFrameLinear(self, name, prop, dx, dy, dz, n, names):
        m, out, prev = self.model, [], name
        for k in range(1, n + 1):
            x, y, z = XYZ[name]
            q = m.PointObj.AddCartesian(x + k * dx, y + k * dy, z + k * dz)[0]
            out.append(m.FrameObj.AddByPoint(prev, q, "", prop)[0])
            prev = q
        return [len(out), tuple(out), 0]


class _EditFrame:
    def __init__(self, model):
        self.model = model

    def DivideByRatio(self, name, n, ratio, names):
        m = self.model
        i, j = FRAMES[name]
        a, b = XYZ[i], XYZ[j]
        pts = [i] + [m.PointObj.AddCartesian(*(a[c] + (b[c] - a[c]) * k / n for c in range(3)))[0]
                     for k in range(1, n)] + [j]
        sec = m.FrameObj.sections.get(name, "B300x600")
        m.FrameObj.Delete(name)
        out = [m.FrameObj.AddByPoint(pts[k], pts[k + 1], "", sec)[0] for k in range(n)]
        return [tuple(out), 0]

    def ChangeConnectivity(self, name, p1, p2):
        FRAMES[name] = (p1, p2)
        return 0


class _EditPoint:
    def __init__(self, model):
        self.model = model

    def Merge(self, tol, n, names):
        m = self.model
        sel = [q for t, q in m.SelectObj.sel if t == 1]
        gone = []
        for i, a in enumerate(sel):
            for b in sel[i + 1:]:
                if b not in gone and a not in gone and sum((XYZ[a][c] - XYZ[b][c]) ** 2 for c in range(3)) ** .5 < tol:
                    gone.append(b)
                    for f, (p1, p2) in list(FRAMES.items()):
                        FRAMES[f] = (a if p1 == b else p1, a if p2 == b else p2)
        m.PointObj.names = [q for q in m.PointObj.names if q not in gone]
        return [len(gone), tuple(gone), 0]


class _DesignBase:
    CODES = ()

    def __init__(self, model, code):
        self.model, self.code, self.strength, self.defl, self.ran = model, code, [], [], False

    def GetCode(self):
        return [self.code, 0]

    def SetCode(self, code):
        if code not in self.CODES:
            return 1
        self.code = code
        return 0

    def GetComboStrength(self):
        return [len(self.strength), tuple(self.strength), 0]

    def SetComboStrength(self, name, sel):
        if sel and name not in self.strength:
            self.strength.append(name)
        if not sel and name in self.strength:
            self.strength.remove(name)
        return 0

    def GetResultsAvailable(self):
        return self.ran

    def StartDesign(self):
        self.ran = True
        return 0


class _DesignSteel(_DesignBase):
    CODES = ("AISC 360-16", "AISC 360-22")

    def GetComboDeflection(self):
        return [len(self.defl), tuple(self.defl), 0]

    def SetComboDeflection(self, name, sel):
        if sel and name not in self.defl:
            self.defl.append(name)
        return 0

    def GetSummaryResults(self, name, *a):
        names = list(FRAMES) if a[-1] == 1 else [name]
        ratios = {"F1": 0.85, "C1": 1.12, "C2": 0.4}
        n = [x for x in names if x in ratios]
        return [len(n), tuple(n), tuple(ratios[x] for x in n), tuple(1 for _ in n), tuple(0.0 for _ in n),
                tuple("ULS" for _ in n), tuple("" for _ in n), tuple("" for _ in n), 0]

    def GetDesignSection(self, name):
        return [self.model.FrameObj.sections.get(name, ""), 0]

    def VerifyPassed(self):
        return [3, 1, 0, ("C1",), 0]

    def VerifySections(self):
        return [0, (), 0]


class _DesignConcrete(_DesignBase):
    CODES = ("ACI 318-19",)

    def GetSummaryResultsColumn(self, name, *a):
        n = [x for x in (list(FRAMES) if a[-1] == 1 else [name]) if x.startswith("C")]
        return [len(n), tuple(n), tuple(2 for _ in n), tuple(0.0 for _ in n), tuple("ULS" for _ in n),
                tuple(0.0 for _ in n), tuple(0.95 for _ in n), tuple("ULS" for _ in n), tuple(0.0 for _ in n),
                tuple("ULS" for _ in n), tuple(0.0 for _ in n), tuple("" for _ in n), tuple("" for _ in n), 0]

    def GetSummaryResultsBeam(self, name, *a):
        n = [x for x in (list(FRAMES) if a[-1] == 1 else [name]) if x.startswith("F")]
        z = tuple(0.0 for _ in n)
        u = tuple("ULS" for _ in n)
        return [len(n), tuple(n), z, u, tuple(0.0004 for _ in n), u, tuple(0.0012 for _ in n), u,
                tuple(0.0005 for _ in n), u, z, u, z, tuple("" for _ in n), tuple("" for _ in n), 0]


class _SDShape:
    def __init__(self, pf):
        self.pf = pf

    def _sh(self, name):
        return self.pf.sd[name]["shapes"]

    def _area(self, name):
        sec = self.pf.sec[name]
        area = sum(v[1].get("area", 0.0) for v in self._sh(name).values())
        self.pf.sec[name] = (sec[0], sec[1], area, sec[3])

    def Delete(self, name, shape, all_=False):
        self._sh(name).clear()
        self._area(name)
        return 0

    def SetSolidRect(self, name, shape, mat, ss, x, y, h, w, rot, color=-1, reinf=False, matrebar=""):
        shape = shape or f"R{len(self._sh(name)) + 1}"
        self._sh(name)[shape] = (101, {"mat": mat, "x": x, "y": y, "h": h, "w": w, "rot": rot, "reinf": reinf,
                                       "mr": matrebar, "area": h * w})
        self._area(name)
        return [shape, 0]

    def GetSolidRect(self, name, shape, *a):
        t, d = self._sh(name).get(shape, (0, None))
        if t != 101:
            return ["", "", 0, 0.0, 0.0, 0.0, 0.0, 0.0, False, "", 1]
        return [d["mat"], "", -1, d["x"], d["y"], d["h"], d["w"], d["rot"], d["reinf"], d["mr"], 0]

    def SetReinfSingle(self, name, shape, x, y, size="", mr=""):
        shape = shape or f"B{len(self._sh(name)) + 1}"
        self._sh(name)[shape] = (301, {"x": x, "y": y, "size": size, "mr": mr})
        return [shape, 0]

    def GetReinfSingle(self, name, shape, *a):
        t, d = self._sh(name).get(shape, (0, None))
        if t != 301:
            return [0.0, 0.0, "", "", 1]
        return [d["x"], d["y"], d["size"], d["mr"], 0]


class _DesignConcreteEtabs(_DesignConcrete):
    """ETABS 23.2: concrete frame design has SetComboStrength but no GetComboStrength (type-library dump)."""

    def __getattribute__(self, name):
        if name == "GetComboStrength":
            raise AttributeError(name)
        return super().__getattribute__(name)


class _Grids(_Names):
    def __init__(self):
        super().__init__(["G1"])

    def GetGridSys_2(self, name, *a):
        return [0.0, 0.0, 0.0, "Cartesian", 2, 2, ("A", "B"), ("1", "2"), (0.0, 6.0), (0.0, 4.0), (True, True),
                (True, True), ("End", "End"), ("Start", "Start"), 0]


class _Stories(_Names):
    data = None

    def GetStories_2(self, *a):
        if self.data:
            base, names, h, m, sim = self.data
            el, z = [], base
            for x in h:
                z += x
                el.append(z)
            return [base, len(names), tuple(names), tuple(el), tuple(h), tuple(m), tuple(sim), (False,) * len(h),
                    (0.0,) * len(h), (0,) * len(h), 0]
        return [0.0, 3, ("Story1", "Story2", "Story3"), (3.0, 6.0, 9.0), (3.0, 3.0, 3.0), (False, False, True),
                ("Story3", "Story3", ""), (False,) * 3, (0.0,) * 3, (0, 0, 0), 0]

    def SetStories_2(self, base, n, names, heights, master, similar, *a):
        self.data = (base, list(names), list(heights), list(master), [("" if x == "None" else x) for x in similar])
        self.names = list(names)
        return 0


class _Labels(_Names):
    def SetPier(self, name, *a):
        self.names.append(name)
        return 0

    def SetSpandrel(self, name, *a):
        self.names.append(name)
        return 0


class _AutoSeismic:
    def __init__(self):
        self.asce = {}
        self.uc = {}

    def SetUserCoefficient(self, name, *args):
        self.uc[name] = list(args)
        return 0

    def GetUserCoefficient(self, name, *a):
        return [*self.uc[name], 0]

    def SetASCE716(self, name, *args):
        self.asce[name] = list(args)
        return 0

    def GetASCE716(self, name, *a):
        if name not in self.asce:
            return [0] * 17 + [1]
        v = self.asce[name]
        return [tuple(v[0]), *v[1:], 0]


class _PropLink(_Names):
    def __init__(self):
        super().__init__([])
        self.lin = {}

    def SetLinear(self, name, dof, fixed, ke, ce, *a):
        if name not in self.names:
            self.names.append(name)
        self.lin[name] = (tuple(dof), tuple(fixed), tuple(ke), tuple(ce))
        self.kind = getattr(self, "kind", {})
        self.kind[name] = 1
        return 0

    def GetLinear(self, name, *a):
        return [*self.lin[name], 0.0, 0.0, False, False, "", "", 0]

    def GetTypeOAPI(self, name, *a):
        return [getattr(self, "kind", {}).get(name, 1), 0]

    def _nl(self, code, name, dof, fixed, nl, ke, ce, k=None, dis=None):
        if name not in self.names:
            self.names.append(name)
        self.kind = getattr(self, "kind", {})
        self.kind[name] = code
        self.nld = getattr(self, "nld", {})
        self.nld[name] = (tuple(dof), tuple(fixed), tuple(nl), tuple(ke), tuple(ce), tuple(k or [0.0] * 6),
                          tuple(dis or [0.0] * 6))
        return 0

    def SetGap(self, name, dof, fixed, nl, ke, ce, k, dis, *a):
        return self._nl(3, name, dof, fixed, nl, ke, ce, k, dis)

    def SetHook(self, name, dof, fixed, nl, ke, ce, k, dis, *a):
        return self._nl(4, name, dof, fixed, nl, ke, ce, k, dis)

    def GetGap(self, name, *a):
        return [*self.nld[name], 0.0, 0.0, "", "", 0]

    GetHook = GetGap

    def SetMultiLinearElastic(self, name, dof, fixed, nl, ke, ce, *a):
        return self._nl(8, name, dof, fixed, nl, ke, ce)

    def GetMultiLinearElastic(self, name, *a):
        return [*self.nld[name][:5], 0.0, 0.0, "", "", 0]

    def SetMultiLinearPoints(self, name, dof, n, f, d, *a):
        self.mlp = getattr(self, "mlp", {})
        self.mlp[(name, dof)] = (tuple(f), tuple(d))
        return 0

    def GetMultiLinearPoints(self, name, dof, *a):
        f, d = self.mlp[(name, dof)]
        return [len(f), f, d, 1, 0.0, 0.0, 0.0, 0.0, 0.0, 0]


class _LinkObj(_Names):
    def __init__(self):
        super().__init__([])
        self.data = {}

    def AddByPoint(self, p1, p2, name, single, prop, user=""):
        n = user or str(len(self.names) + 1)
        self.names.append(n)
        self.data[n] = (p1, p1 if single else p2, prop)
        return [n, 0]

    def GetPoints(self, name, *a):
        p1, p2, _ = self.data[name]
        return [p1, p2, 0]

    def GetProperty(self, name, *a):
        return [self.data[name][2], 0]


class _Groups(_Names):
    def __init__(self, names):
        super().__init__(names)
        self.members = {"EDGE": [(1, "1"), (2, "F1")], "All": [(1, "1"), (1, "2"), (2, "F1"), (5, "A1")]}

    def GetAssignments(self, name, *a):
        m = self.members.get(name, [])
        return [len(m), tuple(t for t, _ in m), tuple(n for _, n in m), 0]

    def SetGroup(self, name, *a):
        if name not in self.names:
            self.names.append(name)
            self.members[name] = []
        return 0

    def Delete(self, name):
        self.names.remove(name)
        self.members.pop(name, None)
        return 0

    def assign(self, group, kind, name, remove):
        if group not in self.names:
            return 1
        m = self.members.setdefault(group, [])
        if remove:
            self.members[group] = [x for x in m if x != (kind, name)]
        elif (kind, name) not in m:
            m.append((kind, name))
        return 0


class _Select:
    def __init__(self, model=None):
        self.model = model
        self.sel = [(2, "F1"), (5, "A1")]

    def GetSelected(self, *a):
        return [len(self.sel), tuple(t for t, _ in self.sel), tuple(n for _, n in self.sel), 0]

    def mark(self, kind, name, on):
        if on and (kind, name) not in self.sel:
            self.sel.append((kind, name))
        if not on:
            self.sel = [x for x in self.sel if x != (kind, name)]
        return 0

    def ClearSelection(self):
        self.sel = []
        return 0

    def All(self, deselect=False):
        m = self.model
        self.sel = [] if deselect else ([(1, n) for n in m.PointObj.names] + [(2, n) for n in m.FrameObj.names] +
                                        [(5, n) for n in m.AreaObj.names])
        return 0

    def Group(self, name, deselect=False):
        for t, n in self.model.GroupDef.members.get(name, []):
            self.mark(t, n, not deselect)
        return 0


class _Diaphragms(_Names):
    def SetDiaphragm(self, name, semi):
        if name not in self.names:
            self.names.append(name)
        self.semi = getattr(self, "semi", {})
        self.semi[name] = semi
        return 0

    def GetDiaphragm(self, name, *a):
        return [getattr(self, "semi", {}).get(name, False), 0]


class _Materials(_Names):
    """Stateful materials (Phase 3 definitions); C30 and A615Gr60 as before."""

    DEFAULTS = {1: (199947979.0, 0.3, 1.17e-05, 76.9729), 2: (24855578.0, 0.2, 9.9e-06, 24.9926),
                6: (199947979.0, 0.3, 1.17e-05, 76.9729), 7: (196500582.0, 0.3, 1.17e-05, 76.9729)}

    def __init__(self, names):
        super().__init__(names)
        self.mat = {"C30": {"type": 2, "iso": [24855578.0, 0.2, 9.9e-06], "w": 24.9926, "fc": 30000.0},
                    "A615Gr60": {"type": 6, "iso": [199947979.0, 0.3, 1.17e-05], "w": 76.9729, "fy": 413685.0,
                                 "fu": 620528.0}}
        self.mass_source = [True, False, True, ("Dead",), (1.0,)]

    def GetMaterial(self, name, *a):
        return [self.mat[name]["type"], 0, "", "", 0] if name in self.mat else [0, 0, "", "", 1]

    def SetMaterial(self, name, mat_type, *a):
        e, u, alpha, w = self.DEFAULTS.get(int(mat_type), (1.0e7, 0.2, 1e-05, 10.0))
        self.mat[name] = {"type": int(mat_type), "iso": [e, u, alpha], "w": w, "fc": 27579.0, "fy": 344738.0,
                          "fu": 448159.0}
        if name not in self.names:
            self.names.append(name)
        return 0

    def AddMaterial(self, name, mat_type, region, standard, grade, user_name=""):
        if region != "United States":
            return [name, 1]
        stored = user_name or f"{standard} {grade}"
        self.SetMaterial(stored, mat_type)
        return [stored, 0]

    def GetMPIsotropic(self, name, *a):
        e, u, alpha = self.mat[name]["iso"]
        return [e, u, alpha, e / (2 * (1 + u)), 0]

    def SetMPIsotropic(self, name, e, u, alpha, *a):
        self.mat[name]["iso"] = [e, u, alpha]
        return 0

    def GetWeightAndMass(self, name, *a):
        w = self.mat[name]["w"]
        return [w, w / 9.80665, 0]

    def SetWeightAndMass(self, name, option, value, *a):
        self.mat[name]["w"] = value if option == 1 else value * 9.80665
        return 0

    def GetOConcrete_1(self, name, *a):
        return [self.mat[name]["fc"], False, 1.0, 2, 4, 0.00221, 0.005, -0.1, 0.0, 0.0, 0]

    def SetOConcrete_1(self, name, fc, *rest):
        assert len(rest) >= 7, rest
        self.mat[name]["fc"] = fc
        return 0

    def GetOSteel_1(self, name, *a):
        m = self.mat[name]
        return [m["fy"], m["fu"], 1.1 * m["fy"], 1.1 * m["fu"], 1, 1, 0.02, 0.14, 0.2, -0.1, 0]

    def SetOSteel_1(self, name, fy, fu, efy, efu, *rest):
        assert len(rest) == 6, rest
        self.mat[name].update(fy=fy, fu=fu, efy=efy)
        return 0

    GetORebar_1 = GetOSteel_1
    SetORebar_1 = SetOSteel_1

    def GetMassSource_1(self, *a):
        e, m, ld, pats, sfs = self.mass_source
        return [e, m, ld, len(pats), tuple(pats), tuple(sfs), 0]

    def SetMassSource_1(self, e, m, ld, n, pats, sfs):
        self.mass_source = [e, m, ld, tuple(pats), tuple(sfs)]
        return [e, m, ld, 0]


class _PropFrames(_Names):
    """Stateful frame sections: {name: (type code, (t3, t2, tf, tw, t2b, tfb), area, material)}."""

    def __init__(self, names):
        super().__init__(names)
        self.sec = {"B300x600": (8, (0.6, 0.3, 0, 0, 0, 0), 0.18, "C30"),
                    "C500x500": (8, (0.5, 0.5, 0, 0, 0, 0), 0.25, "C30")}
        self.modifiers = {}

    def _put(self, name, code, dims, area, mat):
        self.sec[name] = (code, tuple(dims) + (0,) * (6 - len(dims)), area, mat)
        if name not in self.names:
            self.names.append(name)
        return 0

    def GetAllFrameProperties_2(self, *a):
        n = list(self.sec)
        col = lambda i: tuple(self.sec[k][1][i] for k in n)  # noqa: E731
        return [len(n), tuple(n), tuple(self.sec[k][0] for k in n), col(0), col(1), col(2), col(3), col(4), col(5),
                tuple(self.sec[k][2] for k in n), 0]

    def GetNameList(self, *a):
        return [len(self.sec), tuple(self.sec), 0]

    def GetTypeOAPI(self, name, *a):
        return [self.sec[name][0], 0]

    def GetRectangle(self, name, *a):
        _, d, _, m = self.sec[name]
        return ["", m, d[0], d[1], 0, "", "", 0]

    def GetMaterial(self, name, *a):
        return [self.sec[name][3], 0] if name in self.sec else ["C30", 0]

    def GetSectProps(self, name, *a):
        area = self.sec[name][2]
        return [area] + [0.001] * 11 + [0]

    def SetRectangle(self, name, mat, t3, t2, *a):
        return self._put(name, 8, (t3, t2), t3 * t2, mat)

    # ---- non-prismatic and Section Designer (0.22.0)
    def SetNonPrismatic(self, name, n, starts, ends, lens, types, e33, e22, color=-1, notes="", guid=""):
        if any(x not in self.sec for x in list(starts) + list(ends)):
            return 1
        self.np = getattr(self, "np", {})
        self.np[name] = (n, tuple(starts), tuple(ends), tuple(lens), tuple(types), tuple(e33), tuple(e22))
        return self._put(name, 14, (), self.sec[starts[0]][2], self.sec[starts[0]][3])

    def GetNonPrismatic(self, name, *a):
        if name not in getattr(self, "np", {}):
            return [0, (), (), (), (), (), (), -1, "", "", 1]
        return [*self.np[name], -1, "", "", 0]

    def SetSDSection(self, name, mat, design=0, color=-1, notes="", guid=""):
        self.sd = getattr(self, "sd", {})
        self.sd[name] = {"mat": mat, "design": design, "shapes": {}}
        return self._put(name, 13, (), 0.0, mat)

    def GetSDSection(self, name, *a):
        d = getattr(self, "sd", {}).get(name)
        if d is None:
            return ["", 0, (), (), 0, -1, "", "", 1]
        sh = d["shapes"]
        return [d["mat"], len(sh), tuple(sh), tuple(v[0] for v in sh.values()), d["design"], -1, "", "", 0]

    @property
    def SDShape(self):
        return _SDShape(self)

    def SetCircle(self, name, mat, t3, *a):
        return self._put(name, 9, (t3,), 3.141592653589793 * t3 * t3 / 4, mat)

    def SetISection(self, name, mat, t3, t2, tf, tw, t2b, tfb, *a):
        return self._put(name, 1, (t3, t2, tf, tw, t2b, tfb), t2 * tf + t2b * tfb + (t3 - tf - tfb) * tw, mat)

    def SetPipe(self, name, mat, t3, tw, *a):
        return self._put(name, 7, (t3, 0, 0, tw), 3.141592653589793 * (t3 - tw) * tw, mat)

    def SetTube(self, name, mat, t3, t2, tf, tw, *a):
        return self._put(name, 6, (t3, t2, tf, tw), 2 * t2 * tf + 2 * (t3 - 2 * tf) * tw, mat)

    def SetAngle(self, name, mat, t3, t2, tf, tw, *a):
        return self._put(name, 4, (t3, t2, tf, tw), t2 * tf + (t3 - tf) * tw, mat)

    SetChannel = SetAngle
    SetTee = SetAngle

    def SetGeneral(self, name, mat, t3, t2, area, *a):
        return self._put(name, 10, (t3, t2), area, mat)

    def ImportProp(self, name, mat, file_name, prop, *a):
        if prop != "W14X90":
            return 1
        return self._put(name, 1, (0.356, 0.369, 0.018, 0.011, 0.369, 0.018), 0.0171, mat)

    def SetModifiers(self, name, value):
        self.modifiers[name] = tuple(value)
        return [tuple(value), 0]


class _PropAreas(_Names):
    """Stateful area sections: {name: (kind, subtype, shell type, material, thickness, bending)}."""

    def __init__(self, names):
        super().__init__(names)
        self.sec = {"Slab200": ("slab", 0, 1, "C30", 0.2, 0.2)}
        self.modifiers = {}

    def _put(self, name, *rec):
        self.sec[name] = rec
        if name not in self.names:
            self.names.append(name)
        return 0

    def GetSlab(self, name, *a):
        k = self.sec.get(name)
        return [k[1], k[2], k[3], k[4], 0, "", "", 0] if k and k[0] == "slab" else [0, 0, "", 0, 0, "", "", 1]

    def GetWall(self, name, *a):
        k = self.sec.get(name)
        return [k[1], k[2], k[3], k[4], 0, "", "", 0] if k and k[0] == "wall" else [0, 0, "", 0, 0, "", "", 1]

    def GetShell_1(self, name, *a):
        k = self.sec.get(name)
        if k and k[0] == "shell":
            return [k[2], False, k[3], 0.0, k[4], k[5], 0, "", "", 0]
        return [0, False, "", 0, 0, 0, 0, "", "", 1]

    def SetSlab(self, name, slab_type, shell_type, mat, t, *a):
        return self._put(name, "slab", slab_type, shell_type, mat, t, t)

    def SetWall(self, name, wall_type, shell_type, mat, t, *a):
        return self._put(name, "wall", wall_type, shell_type, mat, t, t)

    def SetShell_1(self, name, shell_type, drilling, mat, ang, t, bending, *a):
        return self._put(name, "shell", 0, shell_type, mat, t, bending)

    def SetModifiers(self, name, value):
        self.modifiers[name] = tuple(value)
        return [tuple(value), 0]

    def GetModifiers(self, name, *a):
        return [self.modifiers.get(name, (1.0,) * 10), 0]


class _Springs(_Names):
    def __init__(self, point):
        super().__init__([])
        self.point = point
        self.k = {}

    def SetPointSpringProp(self, name, option, k, *a):
        self.k[name] = tuple(k)
        self.names.append(name)
        return [tuple(k), 0]

    def GetPointSpringProp(self, name, *a):
        return [1, self.k[name], "Global", "", "", 0.0, 0, "", "", 0]

    def SetAreaSpringProp(self, name, u1, u2, u3, nl, *a):
        self.k[name] = (u1, u2, u3)
        self.names.append(name)
        return 0

    def GetAreaSpringProp(self, name, *a):
        u1, u2, u3 = self.k[name]
        return [u1, u2, u3, 0, 1, "", 0.0, 0.0, 0, "", "", 0]

    def SetLineSpringProp(self, name, u1, u2, u3, r1, nl2, nl3, *a):
        self.k[name] = (u1, u2, u3, r1, nl3)
        self.names.append(name)
        return 0

    def GetLineSpringProp(self, name, *a):
        u1, u2, u3, r1, nl = self.k[name]
        return [u1, u2, u3, r1, nl, nl, 0, "", "", 0]


class _StaticLinear:
    def __init__(self, cases):
        self.cases = cases
        self.loads = {}

    def GetLoads(self, name, *a):
        lds = self.loads.get(name, [("Load", name, 1.0)])
        return [len(lds), tuple(x[0] for x in lds), tuple(x[1] for x in lds), tuple(x[2] for x in lds), 0]

    def SetCase(self, name):
        self.cases.add(name, 1, 0)
        return 0

    def SetLoads(self, name, n, kinds, names, sfs):
        assert n == len(kinds) == len(names) == len(sfs)
        self.loads[name] = list(zip(kinds, names, sfs))
        return [tuple(kinds), tuple(names), tuple(sfs), 0]


class _StaticNonlinear(_StaticLinear):
    def SetCase(self, name):
        self.cases.add(name, 2, 0)
        self.geom = getattr(self, "geom", {})
        return 0

    def SetGeometricNonlinearity(self, name, t):
        self.geom[name] = t
        return 0

    def GetGeometricNonlinearity(self, name, *a):
        return [self.geom.get(name, 0), 0]

    def SetInitialCase(self, name, c):
        self.init = getattr(self, "init", {})
        self.init[name] = c
        return 0

    def GetInitialCase(self, name, *a):
        return [getattr(self, "init", {}).get(name, ""), 0]


class _Modal:
    def __init__(self, cases, subtype):
        self.cases, self.subtype = cases, subtype
        self.modes = {}
        self.loads = {}

    def SetCase(self, name):
        self.cases.add(name, 3, self.subtype)
        return 0

    def SetNumberModes(self, name, mx, mn):
        self.modes[name] = (mx, mn)
        return 0

    def GetNumberModes(self, name, *a):
        mx, mn = self.modes.get(name, (12, 1))
        return [mx, mn, 0]

    def SetLoads(self, name, n, kinds, names, *a):
        self.loads[name] = list(zip(kinds, names))
        return 0

    def GetLoads(self, name, *a):
        lds = self.loads.get(name, [])
        return [len(lds), tuple(k for k, _ in lds), tuple(n for _, n in lds), tuple(99.0 for _ in lds),
                tuple(False for _ in lds), 0]


class _RS:
    def __init__(self, cases):
        self.c, self.cases = cases, {}

    def SetCase(self, name):
        self.c.add(name, 4, 0)
        self.cases[name] = {"loads": [], "modal": "Modal", "comb": "CQC", "dir": "SRSS", "damp": 0.05}
        return 0

    def SetLoads(self, name, n, dirs, funcs, sfs, csys, angs):
        self.cases[name]["loads"] = list(zip(dirs, funcs, sfs))
        return [tuple(dirs), tuple(funcs), tuple(sfs), tuple(csys), tuple(angs), 0]

    def SetModalCase(self, name, mc):
        self.cases[name]["modal"] = mc
        return 0

    def SetModalComb_1(self, name, t, *a):
        self.cases[name]["comb"] = {1: "CQC", 2: "SRSS", 3: "Absolute"}[t]
        return 0

    def SetDirComb(self, name, t, sf):
        self.cases[name]["dir"] = {1: "SRSS", 2: "Absolute"}[t]
        return 0

    def SetDampConstant(self, name, d):
        self.cases[name]["damp"] = d
        return 0

    def GetLoads(self, name, *a):
        ld = self.cases[name]["loads"]
        return [len(ld), tuple(x[0] for x in ld), tuple(x[1] for x in ld), tuple(x[2] for x in ld),
                tuple("Global" for _ in ld), tuple(0.0 for _ in ld), 0]

    def GetModalCase(self, name, *a):
        return [self.cases[name]["modal"], 0]

    def GetModalComb_1(self, name, *a):
        return [{"CQC": 1, "SRSS": 2, "Absolute": 3}[self.cases[name]["comb"]], 1.0, 0.0, 1, 60.0, 0]

    def GetDirComb(self, name, *a):
        return [{"SRSS": 1, "Absolute": 2}[self.cases[name]["dir"]], 0.0, 0]

    def GetDampConstant(self, name, *a):
        return [self.cases[name]["damp"], 0]


class _MH:
    def __init__(self, cases):
        self.c, self.cases = cases, {}

    def SetCase(self, name):
        self.c.add(name, 6, 2)
        self.cases[name] = {"loads": [], "modal": "Modal", "steps": 100, "dt": 0.1, "damp": 0.05}
        return 0

    def SetLoads(self, name, n, types, names, funcs, sfs, tf, at, csys, ang):
        self.cases[name]["loads"] = list(zip(types, names, funcs, sfs))
        return [0]

    def SetModalCase(self, name, mc):
        self.cases[name]["modal"] = mc
        return 0

    def SetTimeStep(self, name, n, dt):
        self.cases[name].update(steps=n, dt=dt)
        return 0

    def SetDampConstant(self, name, d):
        self.cases[name]["damp"] = d
        return 0

    def GetLoads(self, name, *a):
        ld = self.cases[name]["loads"]
        return [len(ld), *(tuple(x[i] for x in ld) for i in range(4)), (), (), (), (), 0]

    def GetTimeStep(self, name, *a):
        return [self.cases[name]["steps"], self.cases[name]["dt"], 0]

    def GetModalCase(self, name, *a):
        return [self.cases[name]["modal"], 0]

    def GetDampConstant(self, name, *a):
        return [self.cases[name]["damp"], 0]


class _FuncSub:
    def __init__(self, owner, kind):
        self.o, self.kind = owner, kind

    def SetUser(self, name, n, xs, ys, damp=0.05):
        self.o.f[name] = (self.kind, list(xs), list(ys), damp)
        if name not in self.o.names:
            self.o.names.append(name)
        return [tuple(xs), tuple(ys), 0]


class _Func(_Names):
    def __init__(self, etabs_style=False):
        super().__init__(["UnifRS", "UnifTH"])
        self.f = {"UnifRS": ("rs", [0.0, 1.0], [1.0, 1.0], 0.05), "UnifTH": ("th", [0.0, 1.0], [1.0, 1.0], 0.0)}
        self.FuncRS = _FuncSub(self, "rs")
        self.FuncTH = _FuncSub(self, "th")
        self.etabs_style = etabs_style

    def GetNameList(self, *a):
        return [len(self.f), tuple(self.f), 0]

    def GetValues(self, name, *a):
        if name not in self.f:
            return [0, (), (), 1]
        _, xs, ys, _ = self.f[name]
        lead = [0.0] if self.etabs_style else []          # ETABS returns one leading extra entry
        return [len(xs), tuple(lead + xs), tuple(lead + ys), 0]


class _Cases(_Names):
    def __init__(self, names, types):
        super().__init__(names, types)
        self.sub = {"Modal": 1}
        self.StaticLinear = _StaticLinear(self)
        self.StaticNonlinear = _StaticNonlinear(self)
        self.ModalEigen = _Modal(self, 1)
        self.ModalRitz = _Modal(self, 2)
        self.ResponseSpectrum = _RS(self)
        self.ModHistLinear = _MH(self)

    def add(self, name, code, sub):
        self.types[name] = code
        self.sub[name] = sub
        if name not in self.names:
            self.names.append(name)

    def GetTypeOAPI(self, name, *a):
        return [self.types.get(name, 1), self.sub.get(name, 0), 0]

    def Delete(self, name):
        self.names.remove(name)
        return 0


class _Patterns(_Names):
    def __init__(self, names, types, cases=None):
        super().__init__(names, types)
        self.sw = {"Dead": 1.0}
        self.cases = cases

    def GetSelfWTMultiplier(self, name, *a):
        return [self.sw.get(name, 0.0), 0]

    def Add(self, name, code, sw=0.0, add_case=True):
        if name in self.names:
            return 1
        self.names.append(name)
        self.types[name] = code
        self.sw[name] = sw
        if add_case and self.cases is not None:
            self.cases.add(name, 1, 0)
        return 0

    def SetLoadType(self, name, code):
        self.types[name] = code
        return 0

    def SetSelfWTMultiplier(self, name, sw):
        self.sw[name] = sw
        return 0


class _Combos(_Names):
    def __init__(self):
        super().__init__(["DCON1", "ENV"], {"DCON1": 0, "ENV": 1})
        self.items = {"DCON1": [(0, "Dead", 1.2), (0, "Live", 1.6)], "ENV": [(0, "Dead", 1.2), (0, "Live", 1.6)]}

    def GetCaseList(self, name, *a):
        it = self.items[name]
        return [len(it), tuple(k for k, _, _ in it), tuple(n for _, n, _ in it), tuple(f for _, _, f in it), 0]

    def Add(self, name, combo_type):
        if name in self.names:
            return 1
        self.names.append(name)
        self.types[name] = combo_type
        self.items[name] = []
        return 0

    def Delete(self, name):
        self.names.remove(name)
        del self.items[name]
        return 0

    def SetCaseList(self, name, kind, cname, sf):
        self.items[name].append((kind, cname, sf))
        return [kind, 0]


class _SourceMass(_Names):
    def __init__(self):
        super().__init__(["MSSSRC1"])
        self.src = {"MSSSRC1": [True, False, False, True, (), ()]}

    def GetDefault(self, *a):
        return [next(n for n, v in self.src.items() if v[3]), 0]

    def GetMassSource(self, name, *a):
        e, m, ld, d, pats, sfs = self.src[name]
        return [e, m, ld, d, len(pats), tuple(pats), tuple(sfs), 0]

    def SetMassSource(self, name, e, m, ld, is_default, n, pats, sfs):
        if is_default:
            for v in self.src.values():
                v[3] = False
        self.src[name] = [e, m, ld, is_default, tuple(pats), tuple(sfs)]
        if name not in self.names:
            self.names.append(name)
        return [tuple(pats), tuple(sfs), 0]


class _ResultsSetup:
    def __init__(self):
        self.cases = {"Dead": True}
        self.combos = {}
        self.opts = {"NLStatic": 1, "MultiValuedCombo": 1}
        self.calls = []

    def DeselectAllCasesAndCombosForOutput(self):
        self.cases, self.combos = {}, {}
        return 0

    def GetCaseSelectedForOutput(self, name, *a):
        return [self.cases.get(name, False), 0]

    def GetComboSelectedForOutput(self, name, *a):
        return [self.combos.get(name, False), 0]

    def SetCaseSelectedForOutput(self, name, sel=True):
        self.cases[name] = sel
        return 0

    def SetComboSelectedForOutput(self, name, sel=True):
        self.combos[name] = sel
        return 0

    def GetOptionNLStatic(self, *a):
        return [self.opts["NLStatic"], 0]

    def SetOptionNLStatic(self, v):
        self.opts["NLStatic"] = v
        return 0

    def GetOptionMultiValuedCombo(self, *a):
        return [self.opts["MultiValuedCombo"], 0]

    def SetOptionMultiValuedCombo(self, v):
        self.opts["MultiValuedCombo"] = v
        return 0


class _Results:
    """Results for F1 (beam, 6 m, w = 10 kN/m Dead): M3 = w x (6 - x) / 2 at 3 stations; reactions 30 kN."""

    def __init__(self, model):
        self.model = model
        self.Setup = _ResultsSetup()
        self.seen = []

    def _sel(self):
        return [c for c, v in self.Setup.cases.items() if v] + [c for c, v in self.Setup.combos.items() if v]

    def FrameForce(self, name, item, *a):
        self.seen.append(("FrameForce", name, item, self.model.units, tuple(self._sel())))
        rows = []
        frames = ["F1", "C1"] if item == 2 else [name]
        for case in self._sel():
            f = {"Dead": 1.0, "Live": 0.5, "DCON1": 1.6}.get(case, 1.0)
            for fr in frames:
                for x in (0.0, 3.0, 6.0):
                    m3 = 10 * f * x * (6 - x) / 2 if fr == "F1" else 0.0
                    v2 = 10 * f * (3 - x) if fr == "F1" else 0.0
                    rows.append((fr, x, fr + "-1", x, case, "", 0.0, -1.0 * f, v2, 0.0, 0.0, 0.0, m3))
        if not rows:
            return [0] + [()] * 13 + [1]
        cols = list(zip(*rows))
        return [len(rows), *cols, 0]

    def JointReact(self, name, item, *a):
        rows = [(j, j, c, "", 0.0, 0.0, 0.0, 30.0 * {"Live": 0.5}.get(c, 1.0), 0.0, 0.0, 0.0)
                for c in self._sel() for j in ("1", "2")]
        if not rows:
            return [0] + [()] * 11 + [1]
        return [len(rows), *list(zip(*rows)), 0]

    def BaseReact(self, *a):
        rows = [(c, "", 0.0, 0.0, 0.0, 60.0, 0.0, -180.0, 0.0) for c in self._sel()]
        return [len(rows), *list(zip(*rows)), 0.0, 0.0, 0.0, 0]

    def BucklingFactor(self, *a):
        return [0, (), (), (), (), 1]


class _File:
    def __init__(self, model):
        self.model = model

    def OpenFile(self, path):
        self.model.filename = path
        return 0

    def Save(self, path=""):
        self.model.filename = path or self.model.filename
        self.model.saved += 1
        return 0

    def NewBlank(self):
        return 0


class _Analyze:
    def __init__(self, model):
        self.model = model

    def RunAnalysis(self):
        self.model.locked = True
        self.model.analysed = True
        return 0

    def GetCaseStatus(self):
        names = ["Dead", "Live", "Modal"]
        st = [4, 4, 4] if self.model.analysed else [1, 1, 1]
        return [3, tuple(names), tuple(st), 0]

    def GetRunCaseFlag(self):
        f = getattr(self, "flags", {"Dead": True, "Live": True, "Modal": True})
        return [len(f), tuple(f), tuple(f.values()), 0]

    def SetRunCaseFlag(self, name, run, all_=False):
        self.flags = getattr(self, "flags", {"Dead": True, "Live": True, "Modal": True})
        for k in (list(self.flags) if all_ else [name]):
            self.flags[k] = run
        return 0

    def GetActiveDOF(self, dof=None):
        return [tuple(getattr(self, "dof", (True,) * 6)), 0]

    def SetActiveDOF(self, dof):
        self.dof = tuple(dof)
        return 0


def _drift_rows():
    rows = []
    for story, base in (("Story3", 0.0021), ("Story2", 0.0034), ("Story1", 0.0018)):
        for case in ("Dead", "Live", "DCON1", "ENV"):
            for direction, f in (("X", 1.0), ("Y", 0.7)):
                rows.append([story, case, "Combination", "", direction, f"{base * f:.6f}", "12", "1", "2", "3"])
    return ["Story", "OutputCase", "CaseType", "StepType", "Direction", "Drift", "Label", "X", "Y", "Z"], rows


TABLES = {
    "Story Drifts": _drift_rows(),
    "Base Reactions": (
        ["OutputCase", "CaseType", "StepType", "FX", "FY", "FZ", "MX", "MY", "MZ"],
        [["Dead", "LinStatic", "", "0", "0", "5230.5", "100", "-40", "0"],
         ["Live", "LinStatic", "", "0", "0", "1200", "20", "-10", "0"],
         ["DCON1", "Combination", "", "1.2", "-3", "8196.6", "152", "-64", "1"],
         ["Modal", "LinModEigen", "Mode", "-900", "0", "0", "0", "0", "99"]],
    ),
    "Modal Participating Mass Ratios": (
        ["Case", "Mode", "Period", "UX", "UY", "UZ", "SumUX", "SumUY", "SumUZ", "RZ", "SumRZ"],
        [["Modal", "1", "1.250", "0.70", "0.01", "0", "0.70", "0.01", "0", "0.05", "0.05"],
         ["Modal", "2", "1.100", "0.02", "0.72", "0", "0.72", "0.73", "0", "0.03", "0.08"],
         ["Modal", "3", "0.900", "0.05", "0.02", "0", "0.77", "0.75", "0", "0.68", "0.76"],
         ["Modal", "4", "0.400", "0.15", "0.17", "0", "0.92", "0.92", "0", "0.10", "0.86"]],
    ),
    "Joint Displacements": (
        ["Story", "Label", "UniqueName", "OutputCase", "CaseType", "StepType", "Ux", "Uy", "Uz", "Rx", "Ry", "Rz"],
        [["Story1", "1", "10", "DCON1", "Combination", "", "0.001", "0.002", "-0.012", "0", "0", "0"],
         ["Story1", "2", "11", "DCON1", "Combination", "", "0.004", "-0.001", "-0.020", "0", "0", "0"],
         ["Story1", "1", "10", "Dead", "LinStatic", "", "0.000", "0.001", "-0.009", "0", "0", "0"]],
    ),
}


class _Tables:
    def __init__(self, model):
        self.model = model
        self.sel_cases = ["Dead"]
        self.sel_combos = []
        self.read_units = []

    EDIT_FIELDS = {
        "Functions - Response Spectrum - User Defined": ("Name", "Period", "Value", "DampRatio", "GUID"),
        "Functions - Time History - User Defined": ("Name", "Time", "Value", "GUID"),
        "Load Case Definitions - Response Spectrum": ("Name", "LoadName", "Function", "TransAccSF", "ModalCase",
                                                      "ModalCombo", "DirCombo", "GUID"),
        "Load Case Definitions - Time History - Linear Modal": ("Name", "LoadType", "LoadName", "Function", "LoadSF",
                                                                "ModalCase", "NumSteps", "StepSize", "GUID"),
        "Analysis Options - Automatic Mesh Settings for Floors": ("MeshOpt", "Localized", "MergeJoints", "MaxMeshSize"),
        "Generic Test Table": ("Name", "Kind", "Value", "GUID"),
        "Load Pattern Definitions - Auto Seismic - ASCE 7-16": ("Name", "XDir", "XDirPlusE", "TopStory", "BotStory"),
        "Load Pattern Definitions - Auto Seismic - User Coefficient": ("Name", "XDir", "XDirPlusE", "XDirMinusE",
                                                                      "EccRatio", "TopStory", "BotStory", "C", "K"),
        "Steel Frame Design Preferences - AISC 360-16": ("FrameType", "PatLLF", "SRatioLimit", "MaxIter"),
        "Load Pattern Definitions": ("LoadPat", "DesignType", "SelfWtMult", "GUID"),
        "Spring Property Definitions - Line Springs": ("Name", "StiffOpt", "VertStiff", "RotStiff", "NonlinOpt",
                                                       "Color", "GUID", "Notes"),
        "Concrete Frame Design Load Combination Data": ("ComboType", "ComboName"),
    }

    def _edit_rows(self, key):
        m = self.model
        if key == "Concrete Frame Design Load Combination Data":
            return [("Strength", c) for c in m.DesignConcrete.strength]
        if key.startswith("Steel Frame Design Preferences"):
            return [getattr(self, "steel_prefs", ("SMF", "0.75", "0.95", "1"))]
        if key == "Load Pattern Definitions":          # stale, like SAP2000 27.1: only the first pattern
            return [(m.LoadPatterns.names[0], "Dead", "1", "g")]
        if key == "Load Pattern Definitions - Auto Seismic - User Coefficient":
            return [tuple(r) for r in getattr(self, "uc_rows", [])]
        if key == "Load Pattern Definitions - Auto Seismic - ASCE 7-16":
            got = m.LoadPatterns.AutoSeismic.asce
            st = getattr(self, "seis_st", {})
            return [(n, *("Yes" if v[0][i] else "No" for i in (0, 1)), *st.get(n, ("Base", "Base")))
                    for n, v in got.items()]
        if key == "Generic Test Table":
            if not hasattr(self, "generic"):
                self.generic = [{"Name": "A", "Kind": "Beam", "Value": "1.5", "GUID": "g1"},
                                {"Name": "B", "Kind": "Beam", "Value": "2", "GUID": "g2"},
                                {"Name": "C", "Kind": "Column", "Value": "3", "GUID": "g3"}]
            return [tuple(r.get(f, "") for f in self.EDIT_FIELDS[key]) for r in self.generic]
        if key == "Analysis Options - Automatic Mesh Settings for Floors":
            return [getattr(self, "floor_mesh", ("General", "", "", "1.2"))]
        if key == "Spring Property Definitions - Line Springs":
            return [tuple(r.get(f, "") for f in self.EDIT_FIELDS[key]) for r in getattr(self, "line_springs", [])]
        if key == "Functions - Response Spectrum - User Defined":
            return [(n, repr(x), repr(y), repr(d) if i == 0 else "", "")
                    for n, (kind, xs, ys, d) in m.Func.f.items() if kind == "rs"
                    for i, (x, y) in enumerate(zip(xs, ys))]
        if key == "Functions - Time History - User Defined":
            return [(n, repr(x), repr(y), "") for n, (kind, xs, ys, d) in m.Func.f.items() if kind == "th"
                    for x, y in zip(xs, ys)]
        if key == "Load Case Definitions - Response Spectrum":
            rs = m.LoadCases.ResponseSpectrum
            return [(n, d, f, repr(sf), c["modal"], c["comb"] if i == 0 else "", c["dir"] if i == 0 else "", "")
                    for n, c in rs.cases.items() for i, (d, f, sf) in enumerate(c["loads"])]
        mh = m.LoadCases.ModHistLinear
        return [(n, t, ld, f, repr(sf), c["modal"], str(c["steps"]) if i == 0 else "",
                 repr(c["dt"]) if i == 0 else "", "")
                for n, c in mh.cases.items() for i, (t, ld, f, sf) in enumerate(c["loads"])]

    def GetTableForEditingArray(self, key, group, version, fields, n, data):
        if key not in self.EDIT_FIELDS:
            return [0, (), 0, (), 1]
        rows = self._edit_rows(key)
        return [1, self.EDIT_FIELDS[key], len(rows), tuple(v for r in rows for v in r), 0]

    def SetTableForEditingArray(self, key, version, fields, n, data):
        fields = list(fields)
        rows = [dict(zip(fields, data[i * len(fields):(i + 1) * len(fields)])) for i in range(n)]
        self.pending = (key, rows)
        return [version, tuple(fields), tuple(data), 0]

    def CancelTableEditing(self):
        self.pending = None
        return 0

    def ApplyEditedTables(self, fill, *a):
        key, rows = self.pending
        m = self.model
        if key.startswith("Steel Frame Design Preferences"):
            r = rows[0]
            self.steel_prefs = (r["FrameType"], r["PatLLF"], r["SRatioLimit"], r["MaxIter"])
            return [0, 0, 0, 0, "Import Control for Errors:  Abort import after 20 errors"]
        if key == "Load Pattern Definitions - Auto Seismic - User Coefficient":
            self.uc_rows = [[r[f] for f in self.EDIT_FIELDS[key]] for r in rows]
            return [0, 0, 0, 0, "Import Control for Errors:  Abort import after 20 errors"]
        if key == "Load Pattern Definitions - Auto Seismic - ASCE 7-16":
            self.seis_st = {r["Name"]: (r["TopStory"], r["BotStory"]) for r in rows}
            return [0, 0, 0, 0, "Import Control for Errors:  Abort import after 20 errors"]
        if key == "Generic Test Table":
            if any(r["Name"] == "BAD" for r in rows):
                return [0, 1, 0, 0, "Import Control for Errors:  Abort import after 20 errors\nERROR: bad name"]
            self.generic = [{**r, "GUID": r["GUID"] or "new", "Value": r["Value"].rstrip("0").rstrip(".")
                             if "." in r["Value"] else r["Value"]} for r in rows]
            self.applied_generic = getattr(self, "applied_generic", 0) + 1
            return [0, 0, 0, 1, "Import Control for Errors:  Abort import after 20 errors"]
        if key == "Analysis Options - Automatic Mesh Settings for Floors":
            r = rows[0]
            self.floor_mesh = (r["MeshOpt"], r["Localized"], r["MergeJoints"], r["MaxMeshSize"])
            return [0, 0, 0, 0, "Import Control for Errors:  Abort import after 20 errors"]
        if key == "Spring Property Definitions - Line Springs":
            self.line_springs = rows
            for r in rows:
                if r["Name"] not in m.PropLineSpring.names:
                    m.PropLineSpring.names.append(r["Name"])
            return [0, 0, 0, 0, "Import Control for Errors:  Abort import after 20 errors"]
        if key.startswith("Functions"):
            kind = "rs" if "Spectrum" in key else "th"
            keep = {n: v for n, v in m.Func.f.items() if v[0] != kind}
            for r in rows:
                xk = "Period" if kind == "rs" else "Time"
                if float(r["Value"]) < 0:
                    return [0, 1, 0, 0, "Import Control for Errors:  Abort import after 20 errors\n"
                            "ERROR: value must not be negative for " + r["Name"]]
                k, xs, ys, d = keep.get(r["Name"], (kind, [], [], 0.05))
                keep[r["Name"]] = (k, xs + [float(r[xk])], ys + [float(r["Value"])],
                                   float(r["DampRatio"]) if r.get("DampRatio") else d)
            m.Func.f = keep
            m.Func.names = list(keep)
        elif "Response Spectrum" in key:
            rs = m.LoadCases.ResponseSpectrum
            for r in rows:
                if r["ModalCombo"]:
                    rs.cases[r["Name"]].update(comb=r["ModalCombo"], dir=r["DirCombo"], modal=r["ModalCase"])
        else:
            mh = m.LoadCases.ModHistLinear
            for r in rows:
                if r["NumSteps"]:
                    mh.cases[r["Name"]].update(steps=int(r["NumSteps"]), dt=float(r["StepSize"]),
                                               modal=r["ModalCase"])
        self.pending = None
        return [0, 0, 0, len(rows), "Import Control for Errors:  Abort import after 20 errors\nIMPORT OK"]

    def GetAvailableTables(self):
        keys = list(TABLES) if self.model.analysed else []
        keys += ["Frame Assignments - Sections"]
        types = [0] * len(keys)
        keys.append("Generic Test Table")
        types.append(2)
        for k in self.EDIT_FIELDS:
            if k == "Concrete Frame Design Load Combination Data" and not getattr(self.model, "concrete_members",
                                                                                    True):
                continue          # ETABS lists it once the model has concrete frames
            if k not in keys:
                keys.append(k)
                types.append(2)
        return [len(keys), tuple(keys), tuple(keys), tuple(types), 0]

    def GetLoadCasesSelectedForDisplay(self):
        return [len(self.sel_cases), tuple(self.sel_cases), 0]

    def GetLoadCombinationsSelectedForDisplay(self):
        return [len(self.sel_combos), tuple(self.sel_combos), 0]

    def SetLoadCasesSelectedForDisplay(self, names):
        self.sel_cases = list(names)
        return [names, 0]

    def SetLoadCombinationsSelectedForDisplay(self, names):
        self.sel_combos = list(names)
        return [names, 0]

    def GetTableForDisplayArray(self, key, fields, group, version, inc, n, data):
        self.read_units.append(self.model.units)
        if key not in TABLES:
            return [fields, 0, (), 0, (), 1]
        cols, rows = TABLES[key]
        flat = tuple(v for r in rows for v in r)
        return [fields, 1, tuple(cols), len(rows), flat, 0]


class _View:
    def RefreshView(self, *a):
        return 0


class FakeSapModel:
    def __init__(self):
        _reset_geometry()
        self.units = 6
        self.locked = False
        self.analysed = False
        self.saved = 0
        self.filename = os.path.join(tempfile.gettempdir(), "tower.edb")
        self.PointObj = _Points(self)
        self.FrameObj = _Frames(self)
        self.AreaObj = _Areas(self)
        self.PropMaterial = _Materials(["C30", "A615Gr60"])
        self.PropFrame = _PropFrames(["B300x600", "C500x500"])
        self.PropArea = _PropAreas(["Slab200"])
        self.LoadCases = _Cases(["Dead", "Live", "Modal"], {"Modal": 3})
        self.LoadPatterns = _Patterns(["Dead", "Live"], {"Dead": 1, "Live": 3}, self.LoadCases)
        self.LoadPatterns.AutoSeismic = _AutoSeismic()
        self.RespCombo = _Combos()
        self.SourceMass = _SourceMass()
        self.PropPointSpring = _Springs(True)
        self.PropAreaSpring = _Springs(False)
        self.PropLineSpring = _Springs(False)
        self.Story = _Stories(["Story1", "Story2", "Story3"])
        self.GridSys = _Grids()
        self.GroupDef = _Groups(["All", "EDGE"])
        self.SelectObj = _Select(self)
        self.Diaphragm = _Diaphragms(["D1"])
        self.PierLabel = _Labels([])
        self.SpandrelLabel = _Labels([])
        self.PropLink = _PropLink()
        self.LinkObj = _LinkObj()
        self.Func = _Func()
        self.Results = _Results(self)
        self.PatternDef = _Patterns2(["Default"])
        self.EditArea = _EditArea(self)
        self.EditGeneral = _EditGeneral(self)
        self.DesignSteel = _DesignSteel(self, "AISC 360-16")
        self.DesignConcrete = _DesignConcrete(self, "ACI 318-19")
        self.EditFrame = _EditFrame(self)
        self.EditPoint = _EditPoint(self)
        self.File = _File(self)
        self.Analyze = _Analyze(self)
        self.DatabaseTables = _Tables(self)
        self.View = _View()

    def GetPresentUnits(self):
        return self.units

    def SetPresentUnits(self, code):
        self.units = code
        return 0

    def GetModelIsLocked(self):
        return self.locked

    def SetModelIsLocked(self, value):
        self.locked = bool(value)
        if not value:
            self.analysed = False
        return 0

    def GetProjectInfo(self):
        return [2, ("Company Name", "Project Name"), ("TIC", "Tower A"), 0]

    def GetVersion(self):
        return ["22.1.0", 22.1, 0]

    def GetModelFilename(self, include_path=True):
        return self.filename


class FakeApi:
    def __init__(self):
        self.SapModel = FakeSapModel()

    def ApplicationExit(self, save):
        return 0
