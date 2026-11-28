"""Phase 8.10 (0.24.0): grid systems / coordinate systems and SAP2000 joint constraints as actions.

Measured (tests/diag_86.py, diag_86b.py, 28 Nov 2026):
- ETABS 23.2 / SAFE 23.3: GridSys.SetGridSys(name, x, y, rz) makes a grid system or moves an existing one (origin,
  rotation about Z); there is no typed setter for grid lines. The 'Grid Definitions - Grid Lines' table takes them
  (Name, LineType 'X (Cartesian)' / 'Y (Cartesian)', ID, Ordinate, BubbleLoc, Visible) with no errors, and GetGridSys_2
  reads them back. GridSys.Delete removes a system.
- SAP2000 27.1: grid systems are coordinate systems. CoordSys.SetCoordSys(name, x, y, z, rz, ry, rx) makes or moves
  one, CoordSys.Delete removes it; grid lines go through the 'Grid Lines' table (CoordSys, AxisDir X / Y / Z, GridID,
  XRYZCoord, LineType Primary, Visible, BubbleLoc) and GetCoordSys_2 reads them back.
- SAP2000 constraints: ConstraintDef.SetBody / SetEqual / SetLocal / SetWeld / SetLine (degrees of freedom),
  SetPlate / SetRod / SetBeam / SetDiaphragm (axis 1 X, 2 Y, 3 Z, 4 auto); PointObj.SetConstraint / DeleteConstraint;
  GetEqual / GetWeld answer 1 through comtypes, so definitions are read back from the 'Constraint Definitions - <type>'
  tables. ETABS / SAFE have only diaphragm constraints (define_diaphragm).
Values (origins, rotations, ordinates, constrained directions) come from the user.
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from actions import ActionFailed, Params, _dry, _refresh_view, action, bridge
from csi_bridge import CSIValidationError, api_call, as_list, describe_units
from db_edit import edit_table, read_table
from model_read import _try
from modelling import _start_edit, _targets
from objects import MAX, _EditP


def _r(v: Any) -> float:
    return float(f"{float(v):.10g}")


class GridLine(Params):
    id: str = Field(min_length=1, max_length=20, description="grid line label (e.g. 'A', '1')")
    ordinate: float = Field(description="position along the axis, length units, in the grid system's own axes")
    bubble: Literal["start", "end"] | None = Field(default=None, description=(
        "bubble location (default: end for x lines, start for y lines, as the programs draw them)"))
    visible: bool = Field(default=True, description="show the line")


class GridP(_EditP):
    operation: Literal["define", "delete"] = Field(default="define", description=(
        "define: create the grid system or change its origin / rotation / lines | delete: remove the grid system"))
    name: str = Field(min_length=1, max_length=60, description=(
        "the grid system[[S: (a coordinate system in SAP2000; GLOBAL keeps its origin)]]"))
    origin: list[float] | None = Field(default=None, min_length=2, max_length=3, description=(
        "[x, y][[S: or [x, y, z]]] of the grid system origin in global coordinates, length units (omit to keep it; "
        "new systems default to [0, 0])"))
    rotation: float | None = Field(default=None, ge=-360, le=360, description=(
        "rotation about global Z, degrees (omit to keep; new: 0)"))
    rotation_y: float = Field(default=0.0, ge=-360, le=360, description="[[S:rotation about Y, degrees (SAP2000)]]")
    rotation_x: float = Field(default=0.0, ge=-360, le=360, description="[[S:rotation about X, degrees (SAP2000)]]")
    x_lines: list[GridLine] | None = Field(default=None, max_length=500, description="grid lines along X (omit to keep)")
    y_lines: list[GridLine] | None = Field(default=None, max_length=500, description="grid lines along Y (omit to keep)")
    z_lines: list[GridLine] | None = Field(default=None, max_length=500, description=(
        "[[S:grid lines along Z (SAP2000; ETABS / SAFE levels are stories)]]"))

    @model_validator(mode="after")
    def _check(self) -> "GridP":
        for axis in ("x_lines", "y_lines", "z_lines"):
            ids = [ln.id for ln in getattr(self, axis) or []]
            if len(set(ids)) != len(ids):
                raise ValueError(f"{axis}: each id once")
        return self


def read_grid(sap: Any, sw: str, name: str) -> dict[str, Any] | None:
    if sw == "SAP2000":
        g = _try(sap, "CoordSys.GetCoordSys_2", name)
        if not g:
            return None
        nx, ny, nz = int(g[6]), int(g[7]), int(g[8])
        def lines(ids, ords, vis, bub):
            return [{"id": str(i), "ordinate": _r(o), "visible": bool(v), "bubble": str(b).lower()}
                    for i, o, v, b in zip(as_list(ids), as_list(ords), as_list(vis), as_list(bub))]
        return {"name": name, "origin": [_r(g[0]), _r(g[1]), _r(g[2])], "rotation": _r(g[3]),
                "rotation_y": _r(g[4]), "rotation_x": _r(g[5]),
                "x_lines": lines(g[9], g[12], g[18], g[21])[:nx], "y_lines": lines(g[10], g[13], g[19], g[22])[:ny],
                "z_lines": lines(g[11], g[14], g[20], g[23])[:nz]}
    g = _try(sap, "GridSys.GetGridSys_2", name)
    if not g:
        return None
    nx, ny = int(g[4]), int(g[5])
    def lines2(ids, ords, vis, bub):
        return [{"id": str(i), "ordinate": _r(o), "visible": bool(v), "bubble": "end" if str(b) == "b" else "start"}
                for i, o, v, b in zip(as_list(ids), as_list(ords), as_list(vis), as_list(bub))]
    return {"name": name, "type": str(g[3]), "origin": [_r(g[0]), _r(g[1])], "rotation": _r(g[2]),
            "x_lines": lines2(g[6], g[8], g[10], g[12])[:nx], "y_lines": lines2(g[7], g[9], g[11], g[13])[:ny]}


def _systems(sap: Any, sw: str) -> list[str]:
    o = _try(sap, "CoordSys.GetNameList" if sw == "SAP2000" else "GridSys.GetNameList")
    return [str(x) for x in as_list(o[1])] if o else []


@action("define_grid_system", "Define or change a grid system: origin and rotation, and its grid lines along X and Y"
        "[[S: (and Z)]] with labels and ordinates (a list given for an axis replaces that axis's lines), or delete it. "
        "[[S:In SAP2000 a grid system is a coordinate system (also usable for loads and local axes).]] Values from the "
        "user. Answers with the grid system read back.", "Model", "edit", GridP,
        fields={"rotation_y": "S", "rotation_x": "S", "z_lines": "S"})
def define_grid_system(p: GridP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    sw = p.software
    systems = _systems(sap, sw)
    exists = p.name in systems
    if p.operation == "delete":
        if not exists:
            raise CSIValidationError(f"Grid system '{p.name}' is not defined.", hint=f"Defined: {systems}")
        if sw == "SAP2000" and p.name.upper() == "GLOBAL" or len(systems) == 1 and sw != "SAP2000":
            raise CSIValidationError(f"'{p.name}' cannot be deleted (the model needs it).")
        plan = {"operation": "delete", "grid_system": p.name}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_grid", "delete a grid system")
        coll = sap.CoordSys if sw == "SAP2000" else sap.GridSys
        api_call(f"{'CoordSys' if sw == 'SAP2000' else 'GridSys'}.Delete", coll.Delete, p.name)
        _refresh_view(sap)
        left = _systems(sap, sw)
        if p.name in left:
            raise ActionFailed({**plan, "message": "The program kept the grid system."})
        return {**plan, "deleted": True, "grid_systems": left, "model_was_unlocked": was_locked,
                **({"snapshot": snap} if snap else {})}
    if sw != "SAP2000" and p.origin and len(p.origin) == 3:
        raise CSIValidationError("ETABS / SAFE grid systems take an origin [x, y].")
    if sw == "SAP2000" and p.name.upper() == "GLOBAL" and (p.origin and any(p.origin) or p.rotation):
        raise CSIValidationError("GLOBAL keeps its origin and rotation; give its lines only, or another name.")
    with bridge.units_scope(sap, p.units):
        before = read_grid(sap, sw, p.name) if exists else None
        org = list(p.origin) if p.origin else (before["origin"] if before else [0.0, 0.0, 0.0][:3 if sw == "SAP2000" else 2])
        if sw == "SAP2000" and len(org) == 2:
            org = org + [before["origin"][2] if before else 0.0]
        rot = p.rotation if p.rotation is not None else (before["rotation"] if before else 0.0)
        plan: dict[str, Any] = {"operation": "define", "grid_system": p.name, "existed": exists, "origin": org,
                                "rotation": rot, "units": {**describe_units(p.units), "angles": "degrees"}}
        for axis in ("x_lines", "y_lines", "z_lines"):
            if getattr(p, axis) is not None:
                plan[axis] = [ln.model_dump(exclude_none=True) for ln in getattr(p, axis)]
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        snap, was_locked = _start_edit(p, sap, "before_grid", "define a grid system")
        extra: dict[str, Any] = {}
        if sw == "SAP2000":
            ry = p.rotation_y if p.rotation_y or not before else before["rotation_y"]
            rx = p.rotation_x if p.rotation_x or not before else before["rotation_x"]
            if p.name.upper() != "GLOBAL":
                api_call("CoordSys.SetCoordSys", sap.CoordSys.SetCoordSys, p.name, *org, rot, ry, rx)
            axes = {"X": p.x_lines, "Y": p.y_lines, "Z": p.z_lines}
            given = {a for a, v in axes.items() if v is not None}
            if given:
                rows = [{"CoordSys": p.name, "AxisDir": a, "GridID": ln.id, "XRYZCoord": repr(ln.ordinate),
                         "LineType": "Primary", "Visible": "Yes" if ln.visible else "No",
                         "BubbleLoc": (ln.bubble or ("start" if a == "Y" else "end")).title()}
                        for a, lst in axes.items() for ln in lst or []]
                extra["table_edit"] = edit_table(sap, "Grid Lines", rows, drop=lambda r: r.get("CoordSys") == p.name
                                                 and r.get("AxisDir") in given)
        else:
            api_call("GridSys.SetGridSys", sap.GridSys.SetGridSys, p.name, float(org[0]), float(org[1]), float(rot))
            axes = {"X (Cartesian)": p.x_lines, "Y (Cartesian)": p.y_lines}
            given = {a for a, v in axes.items() if v is not None}
            if given:
                rows = [{"Name": p.name, "LineType": a, "ID": ln.id, "Ordinate": repr(ln.ordinate),
                         "BubbleLoc": (ln.bubble or ("end" if a.startswith("X") else "start")).title(),
                         "Visible": "Yes" if ln.visible else "No"} for a, lst in axes.items() for ln in lst or []]
                extra["table_edit"] = edit_table(sap, "Grid Definitions - Grid Lines", rows,
                                                 drop=lambda r: r.get("Name") == p.name and r.get("LineType") in given)
        back = read_grid(sap, sw, p.name)
    _refresh_view(sap)
    out = {**plan, "read_back": back, **extra, "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    wrong = []
    if back is None:
        wrong.append("grid system not found after the change")
    else:
        if any(abs(a - b) > 1e-9 * max(1.0, abs(b)) for a, b in zip(back["origin"], org)):
            wrong.append({"origin": [org, back["origin"]]})
        if abs(back["rotation"] - rot) > 1e-9:
            wrong.append({"rotation": [rot, back["rotation"]]})
        for axis in ("x_lines", "y_lines", "z_lines"):
            want = getattr(p, axis)
            if want is not None:
                got = {ln["id"]: ln["ordinate"] for ln in back.get(axis, [])}
                exp = {ln.id: _r(ln.ordinate) for ln in want}
                if got != exp:
                    wrong.append({axis: {"given": exp, "read_back": got}})
    if wrong:
        out["not_kept_as_given"] = wrong
        out["message"] = "The grid system read back differently."
        raise ActionFailed(out)
    return out


# --------------------------------------------------------------------------- SAP2000 constraints

DOFS = ("UX", "UY", "UZ", "RX", "RY", "RZ")
LOCAL_DOFS = ("U1", "U2", "U3", "R1", "R2", "R3")
AXIS = {"x": 1, "y": 2, "z": 3, "auto": 4}
DOF_TYPES = ("body", "equal", "local", "weld", "line")
AXIS_TYPES = ("plate", "rod", "beam", "diaphragm")
TYPE_CODE = {1: "body", 2: "diaphragm", 3: "plate", 4: "rod", 5: "beam", 6: "equal", 7: "local", 8: "weld",
             13: "line"}


class ConstraintP(_EditP):
    operation: Literal["define", "assign", "unassign", "delete"] = Field(description=(
        "define (create or change a constraint; optionally assign it to points) | assign: add points to it | "
        "unassign: remove all constraints from the points | delete: remove the constraint"))
    name: str | None = Field(default=None, min_length=1, max_length=60, description="the constraint")
    type: Literal["body", "equal", "local", "weld", "line", "plate", "rod", "beam", "diaphragm"] | None = Field(
        default=None, description=(
            "define: body (rigid body) | equal (equal displacements) | local (equal in joint local axes) | weld "
            "(joints within a tolerance) | line | plate | rod | beam | diaphragm"))
    dofs: list[Literal["UX", "UY", "UZ", "RX", "RY", "RZ", "U1", "U2", "U3", "R1", "R2", "R3"]] | None = Field(
        default=None, description=(
            "body / equal / weld / line: constrained directions UX ... RZ | local: U1 ... R3 (joint local axes)"))
    axis: Literal["x", "y", "z", "auto"] | None = Field(default=None, description=(
        "plate / diaphragm: axis normal to the plane | rod / beam: axis along it; x | y | z | auto"))
    tolerance: float | None = Field(default=None, gt=0, description="weld: joints closer than this, length units")
    csys: str = Field(default="Global", description="coordinate system of the directions / axis")
    points: list[str] = Field(default=[], max_length=MAX, description=(
        "define / assign: points to constrain together | unassign: points to free"))
    replace: bool = Field(default=False, description="define: change an existing constraint")

    @model_validator(mode="after")
    def _check(self) -> "ConstraintP":
        if self.operation != "unassign" and not self.name:
            raise ValueError("name is required")
        if self.operation == "define":
            if not self.type:
                raise ValueError("define needs type")
            if self.type in DOF_TYPES and not self.dofs:
                raise ValueError(f"{self.type} needs dofs")
            if self.type == "local" and any(d not in LOCAL_DOFS for d in self.dofs or []):
                raise ValueError("local takes U1 ... R3")
            if self.type in ("body", "equal", "weld", "line") and any(d not in DOFS for d in self.dofs or []):
                raise ValueError(f"{self.type} takes UX ... RZ")
            if self.type in AXIS_TYPES and not self.axis:
                raise ValueError(f"{self.type} needs axis")
            if self.type == "weld" and self.tolerance is None:
                raise ValueError("weld needs tolerance")
        if self.operation in ("assign", "unassign") and not self.points:
            raise ValueError(f"{self.operation} needs points")
        return self


def read_constraint(sap: Any, name: str) -> dict[str, Any] | None:
    t = _try(sap, "ConstraintDef.GetConstraintType", name)
    if not t:
        return None
    kind = TYPE_CODE.get(int(t[0]), str(t[0]))
    out: dict[str, Any] = {"name": name, "type": kind}
    try:
        _, _, rows = read_table(sap, f"Constraint Definitions - {kind.title()}")
        r = next((x for x in rows if x.get("Name") == name), None)
    except Exception:  # noqa: BLE001 - the definition is still reported by type
        r = None
    if r:
        if kind == "local":
            out["dofs"] = [d for d in LOCAL_DOFS if r.get(d) == "Yes"]
        elif "UX" in r:
            out["dofs"] = [d for d in DOFS if r.get(d) == "Yes"]
        if "Axis" in r:
            out["axis"] = str(r["Axis"]).lower()
        if r.get("Tolerance"):
            out["tolerance"] = _r(r["Tolerance"])
        if r.get("CoordSys"):
            out["csys"] = r["CoordSys"]
    return out


def _points_of_constraint(sap: Any, name: str) -> list[str]:
    try:
        _, _, rows = read_table(sap, "Joint Constraint Assignments")
    except Exception:  # noqa: BLE001
        return []
    return [r["Joint"] for r in rows if r.get("Constraint") == name]


@action("define_constraint", "SAP2000 joint constraints: define a body, equal, local, weld, line, plate, rod, beam or "
        "diaphragm constraint (directions or axis from the user) and assign it to points, add or free points, or delete "
        "it. (ETABS / SAFE: diaphragms only, define_diaphragm.) Answers with the constraint and its points read back.",
        "Modelling", "edit", ConstraintP, programs="S")
def define_constraint(p: ConstraintP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    names = bridge.try_name_list(sap, ["ConstraintDef"])[0] or []
    if p.operation in ("assign", "delete") and p.name not in names:
        raise CSIValidationError(f"Constraint '{p.name}' is not defined.", hint=f"Defined: {names[:30]}")
    if p.operation == "define" and p.name in names and not p.replace:
        raise CSIValidationError(f"Constraint '{p.name}' exists.", hint="replace: true changes it.")
    pts = _targets(sap, "PointObj", p.points, None, "points") if p.points else []
    plan: dict[str, Any] = {"operation": p.operation, "constraint": p.name, "points": pts[:200]}
    if p.operation == "define":
        plan.update(type=p.type, csys=p.csys)
        if p.dofs:
            plan["dofs"] = p.dofs
        if p.axis:
            plan["axis"] = p.axis
        if p.tolerance is not None:
            plan["tolerance"] = p.tolerance
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    c, po = sap.ConstraintDef, sap.PointObj
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, "before_constraint", f"{p.operation} a constraint")
        if p.operation == "delete":
            api_call("ConstraintDef.Delete", c.Delete, p.name)
        elif p.operation == "unassign":
            for n in pts:
                api_call(f"PointObj.DeleteConstraint('{n}')", po.DeleteConstraint, n)
        else:
            if p.operation == "define":
                if p.type in DOF_TYPES:
                    order = LOCAL_DOFS if p.type == "local" else DOFS
                    flags = [d in p.dofs for d in order]
                    if p.type == "local":
                        api_call("ConstraintDef.SetLocal", c.SetLocal, p.name, flags)
                    elif p.type == "weld":
                        api_call("ConstraintDef.SetWeld", c.SetWeld, p.name, flags, float(p.tolerance), p.csys)
                    else:
                        fn = getattr(c, f"Set{p.type.title()}")
                        api_call(f"ConstraintDef.Set{p.type.title()}", fn, p.name, flags, p.csys)
                else:
                    fn = getattr(c, f"Set{p.type.title()}")
                    api_call(f"ConstraintDef.Set{p.type.title()}", fn, p.name, AXIS[p.axis], p.csys)
            for n in pts:
                api_call(f"PointObj.SetConstraint('{n}')", po.SetConstraint, n, p.name, 0, False)
        back = read_constraint(sap, p.name) if p.name else None
        held = _points_of_constraint(sap, p.name) if p.name and back else []
    _refresh_view(sap)
    out = {**plan, "read_back": back, "constraint_points": held[:500], "model_was_unlocked": was_locked,
           **({"snapshot": snap} if snap else {})}
    problems = []
    if p.operation == "delete" and back is not None:
        problems.append("the constraint is still defined")
    if p.operation == "define" and back:
        if back["type"] != p.type:
            problems.append({"type": [p.type, back["type"]]})
        if p.dofs and sorted(back.get("dofs", [])) != sorted(p.dofs):
            problems.append({"dofs": [p.dofs, back.get("dofs")]})
        if p.axis and back.get("axis") and back["axis"] != p.axis:
            problems.append({"axis": [p.axis, back.get("axis")]})
    if p.operation in ("define", "assign") and set(pts) - set(held):
        problems.append({"points_not_constrained": sorted(set(pts) - set(held))[:50]})
    if p.operation == "unassign":
        still = [n for n in pts if (_try(sap, "PointObj.GetConstraint", n) or [0])[0]]
        if still:
            problems.append({"points_still_constrained": still[:50]})
    if problems:
        out["not_kept_as_given"] = problems
        out["message"] = "The constraint read back differently."
        raise ActionFailed(out)
    return out
