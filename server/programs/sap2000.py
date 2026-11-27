"""SAP2000-only API features (from docs/api: SAP2000v1 27.1)."""
from __future__ import annotations

from typing import Any

from csi_bridge import api_call, as_list

CAPABILITIES = {
    "area_auto_mesh": "AreaObj.SetAutoMesh_1",
    "area_divide": "EditArea.Divide",
    "joint_patterns": "PatternDef.SetPattern + PointObj.SetPatternByXYZ",
    "surface_pressure": "AreaObj.SetLoadSurfacePressure",
    "stories": "",
    "piers_spandrels": "",
    "bulk_object_readers": "",
}
USAGE = (
    "SAP2000: no stories; use get_grids and object coordinates (objects have names, no labels). Walls and slabs: "
    "set_area_mesh (auto mesh assignment, or divide into new areas with edge restraints carried); hydrostatic or "
    "earth pressure with define_joint_pattern + assign_area_load load_type surface_pressure (face bottom: a positive "
    "pressure pushes from that face toward the shell; confirm the direction on one result). Use section cuts "
    "(get_results section_cuts) where ETABS would use piers."
)
ALTERNATIVES = {
    # option values other programs have (catalog.check_program_options looks them up by value)
    "story_drifts": "SAP2000 has no stories: use joint_displacements and compute the drift between joints at two "
                    "levels.",
    "joint_drifts": "Use joint_displacements and compute the drift between joints at two levels.",
    "pier_forces": "SAP2000 has no piers: use section_cuts.",
    "spandrel_forces": "SAP2000 has no spandrels: use section_cuts.",
    "story_charts": "SAP2000 has no stories; use the deformed / reaction diagrams.",
    "springs": "SAP2000 springs are assigned per object; see get_object_assignments.",
    "drift_limit": "SAP2000 has no story drifts.",

    "stories": "SAP2000 has no stories; use get_grids (Z grid lines) or object coordinates.",
    "piers_spandrels": "SAP2000 has no pier / spandrel labels; use section cuts (get_results section_cuts).",
}

# AreaObj.SetAutoMesh_1 / EditArea.Divide_1 MeshType codes
MESH_TYPES = {"none": 0, "divisions": 1, "max_size": 2, "general": 6}
# PointObj.SetPatternByXYZ restriction codes
RESTRICTIONS = {"all": 0, "positive_only": 1, "negative_only": 2}


def set_auto_mesh(sap: Any, area: str, mode: str, n1: int, n2: int, size1: float, size2: float,
                  general_size: float, mesh_at_points: bool, mesh_at_lines: bool, restraints_on_edges: bool = True,
                  item_type: int = 0) -> None:
    """restraints_on_edges: joints added on an edge whose two corner joints share a restraint get it too (a wall
    fixed at its base corners stays fixed along the whole base)."""
    api_call(f"AreaObj.SetAutoMesh_1('{area}')", sap.AreaObj.SetAutoMesh_1, area, MESH_TYPES[mode], int(n1), int(n2),
             float(size1), float(size2), bool(mesh_at_lines), bool(mesh_at_points), False, 0.0, float(general_size),
             False, False, bool(restraints_on_edges), False, "ALL", False, 0.0, True, False, item_type)


def get_auto_mesh(sap: Any, area: str) -> dict[str, Any] | None:
    try:
        o = api_call("AreaObj.GetAutoMesh_1", sap.AreaObj.GetAutoMesh_1, area)
    except Exception:  # noqa: BLE001
        return None
    code = int(o[0])
    names = {v: k for k, v in MESH_TYPES.items()}
    out: dict[str, Any] = {"mode": names.get(code, f"type {code}")}
    if code == 1:
        out.update(n1=int(o[1]), n2=int(o[2]))
    elif code == 2:
        out.update(max_size1=float(o[3]), max_size2=float(o[4]))
    elif code == 6:
        out.update(max_size=float(o[9]))
    return out


def _area_points(sap: Any, area: str) -> list[str]:
    o = api_call(f"AreaObj.GetPoints('{area}')", sap.AreaObj.GetPoints, area)
    return [str(x) for x in as_list(o[1])]


def _xyz(sap: Any, point: str) -> tuple[float, float, float]:
    o = api_call(f"PointObj.GetCoordCartesian('{point}')", sap.PointObj.GetCoordCartesian, point)
    return float(o[0]), float(o[1]), float(o[2])


def _restraint(sap: Any, point: str) -> tuple[bool, ...]:
    o = api_call(f"PointObj.GetRestraint('{point}')", sap.PointObj.GetRestraint, point)
    return tuple(bool(v) for v in as_list(o[0]))


def _on_segment(p: tuple, a: tuple, b: tuple, tol: float = 1e-6) -> bool:
    ab = [b[i] - a[i] for i in range(3)]
    ap = [p[i] - a[i] for i in range(3)]
    L2 = sum(v * v for v in ab)
    if L2 <= 0:
        return False
    t = sum(ab[i] * ap[i] for i in range(3)) / L2
    if t < -tol or t > 1 + tol:
        return False
    d2 = sum((ap[i] - t * ab[i]) ** 2 for i in range(3))
    return d2 <= (tol * max(1.0, L2 ** 0.5)) ** 2


def divide(sap: Any, area: str, mode: str, n1: int, n2: int, size1: float, size2: float,
           restraints_on_edges: bool = True) -> dict:
    """Physically split the area (the new areas replace it).

    Uses EditArea.Divide: through comtypes, Divide_1 refuses every argument count (measured on SAP2000 27.1), and
    Divide has no restraints-on-edge option. So when restraints_on_edges is set, the server copies them itself:
    every new joint on an original edge whose two corners carry the same restraint gets that restraint."""
    edges: list[tuple[tuple, tuple, tuple[bool, ...]]] = []
    if restraints_on_edges:
        corners = _area_points(sap, area)
        info = [(_xyz(sap, c), _restraint(sap, c)) for c in corners]
        for i in range(len(info)):
            (a, ra), (b, rb) = info[i], info[(i + 1) % len(info)]
            if ra == rb and any(ra):
                edges.append((a, b, ra))
        before = set(corners)
    o = api_call(f"EditArea.Divide('{area}')", sap.EditArea.Divide, area, MESH_TYPES[mode], 0, [],
                 int(n1), int(n2), float(size1), float(size2))
    names = [str(x) for x in as_list(o[1])]
    restrained: list[str] = []
    if edges:
        seen: set[str] = set()
        for nm in names:
            for pt in _area_points(sap, nm):
                if pt in before or pt in seen:
                    continue
                seen.add(pt)
                xyz = _xyz(sap, pt)
                for a, b, r in edges:
                    if _on_segment(xyz, a, b):
                        api_call(f"PointObj.SetRestraint('{pt}')", sap.PointObj.SetRestraint, pt, list(r))
                        restrained.append(pt)
                        break
    return {"areas": names, "edge_joints_restrained": restrained}


def define_pattern(sap: Any, name: str) -> None:
    api_call("PatternDef.SetPattern", sap.PatternDef.SetPattern, name)


def pattern_names(sap: Any) -> list[str]:
    try:
        o = api_call("PatternDef.GetNameList", sap.PatternDef.GetNameList)
        return [str(x) for x in as_list(o[1])]
    except Exception:  # noqa: BLE001
        return []


def set_pattern_xyz(sap: Any, target: str, item_type: int, pattern: str, a: float, b: float, c: float, d: float,
                    restriction: str, replace: bool) -> None:
    api_call("PointObj.SetPatternByXYZ", sap.PointObj.SetPatternByXYZ, target, pattern, float(a), float(b), float(c),
             float(d), item_type, RESTRICTIONS[restriction], bool(replace))


def pattern_value(sap: Any, point: str, pattern: str) -> float | None:
    try:
        return float(api_call("PointObj.GetPatternValue", sap.PointObj.GetPatternValue, point, pattern, 0.0)[0])
    except Exception:  # noqa: BLE001
        return None


def set_surface_pressure(sap: Any, area: str, load_pattern: str, face: int, value: float, joint_pattern: str,
                         replace: bool) -> None:
    api_call(f"AreaObj.SetLoadSurfacePressure('{area}')", sap.AreaObj.SetLoadSurfacePressure, area, load_pattern,
             int(face), float(value), joint_pattern or "", bool(replace), 0)


def get_surface_pressure(sap: Any, area: str) -> list[dict[str, Any]]:
    try:
        o = api_call("AreaObj.GetLoadSurfacePressure", sap.AreaObj.GetLoadSurfacePressure, area)
    except Exception:  # noqa: BLE001
        return []
    return [{"pattern": str(p), "face": FACES.get(int(f), f"edge {int(f)}"), "value": float(v),
             "joint_pattern": str(jp or "")}
            for p, f, v, jp in zip(as_list(o[2]), as_list(o[3]), as_list(o[4]), as_list(o[5]))]


FACES = {-1: "bottom", -2: "top"}
FACE_CODES = {"bottom": -1, "top": -2}
