"""Phase 4: new models and models built from a JSON / spreadsheet spec (feeds e.g. the valve-chamber agent).

new_model         - a new blank or grid-only model in the running program, saved at once to the given file (so
                    snapshots and the change log have a file). An open model is discarded only with
                    discard_open_model: true (ask the user first).
build_model_from_spec - one spec (JSON object, .json file or .xlsx workbook with a sheet per section) becomes the
                    model: every step is first validated with the parameters of the action that will run it, and
                    references (sections, points, load patterns) are checked against the spec and the model; only
                    then do the steps run, in dependency order, through the same actions Claude uses one by one.
                    The run stops at the first refusal and says which step, what was done and where the snapshot is.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, ValidationError, model_validator

from actions import (
    ACTIONS, ALLOW_UNLOCK, DRY_RUN, SNAPSHOT, SOFTWARE, UNITS, Params, _dry, _refresh_view, _take_snapshot, action,
    bridge,
)
from csi_bridge import UNIT_PRESETS, CSIValidationError, Software, UnitPreset, api_call, describe_units
from model_read import _names

# section key -> (action, kind)   kind: list = one call per item | one = a single call | rows = compact rows
STEPS: list[tuple[str, str, str]] = [
    ("materials", "define_material", "list"),
    ("functions", "define_function", "list"),
    ("frame_sections", "define_frame_section", "list"),
    ("area_sections", "define_area_section", "list"),
    ("springs", "define_spring_property", "list"),
    ("load_patterns", "define_load_pattern", "list"),
    ("points", "create_points", "rows"),
    ("frames", "create_frames", "rows"),
    ("areas", "create_areas", "rows"),
    ("point_assignments", "assign_point_properties", "list"),
    ("frame_assignments", "assign_frame_properties", "list"),
    ("area_assignments", "assign_area_properties", "list"),
    ("groups", "set_group", "list"),
    ("loads", "assign_point_or_frame_load", "list"),
    ("area_loads", "assign_area_load", "list"),
    ("temperature_loads", "assign_temperature_load", "list"),
    ("load_cases", "define_load_case", "list"),
    ("combinations", "define_combination", "list"),
    ("mass_source", "set_mass_source", "one"),
    ("analysis_options", "set_analysis_options", "one"),
    ("tables", "edit_database_table", "list"),
]
KEYS = [k for k, _, _ in STEPS]
TOP = set(KEYS) | {"units", "save_as", "new_model", "description"}

# A portal frame (kN, m): the template's example rows and the documentation of the format.
EXAMPLE: dict[str, Any] = {
    "description": "Portal frame 6 m x 4 m, pinned bases; gravity on the beam, lateral load at the top",
    "units": "kN_m_C",
    "materials": [{"name": "C30", "type": "concrete", "E": 2.6e7, "poisson": 0.2, "unit_weight": 25.0,
                   "thermal_coeff": 1e-5, "fc": 30000.0}],
    "frame_sections": [{"name": "COL400", "material": "C30", "shape": "rectangle", "depth": 0.4, "width": 0.4},
                       {"name": "BM300x600", "material": "C30", "shape": "rectangle", "depth": 0.6, "width": 0.3}],
    "load_patterns": [{"name": "DL", "type": "dead", "self_weight_multiplier": 1.0},
                      {"name": "LL", "type": "live", "self_weight_multiplier": 0.0},
                      {"name": "WX", "type": "wind", "self_weight_multiplier": 0.0}],
    "points": [{"name": "1", "xyz": [0, 0, 0], "restraint": "pinned"},
               {"name": "2", "xyz": [6, 0, 0], "restraint": "pinned"},
               {"name": "3", "xyz": [0, 0, 4]}, {"name": "4", "xyz": [6, 0, 4]}],
    "frames": [{"name": "C1", "start": "1", "end": "3", "section": "COL400"},
               {"name": "C2", "start": "2", "end": "4", "section": "COL400"},
               {"name": "B1", "start": "3", "end": "4", "section": "BM300x600"}],
    "loads": [{"target_type": "frame", "object_names": ["B1"], "load_pattern": "LL", "values": [10],
               "direction": "gravity"},
              {"target_type": "point", "object_names": ["3"], "load_pattern": "WX", "values": [20, 0, 0, 0, 0, 0]}],
    "combinations": [{"name": "ULS1", "type": "linear_add", "items": [
        {"name": "DL", "factor": 1.2, "kind": "case"}, {"name": "LL", "factor": 1.6, "kind": "case"}]}],
}


# --------------------------------------------------------------------------- new model

class NewModelP(Params):
    software: Software = SOFTWARE
    file_path: str = Field(description="where the new model is saved right away (.sdb / .edb / .fdb)")
    kind: Literal["blank", "grid_only"] = Field(default="blank", description=(
        "blank: no objects[[EF: | grid_only: stories and grid lines (SAFE draws new areas only in such a model, "
        "at its base)]]"))
    stories: int = Field(default=1, ge=1, le=200, description="grid_only: number of stories")
    story_height: float = Field(default=3.0, gt=0, description="grid_only: typical story height (length units)")
    bottom_story_height: float | None = Field(default=None, gt=0, description="grid_only: default story_height")
    grid_lines_x: int = Field(default=2, ge=1, le=200, description="grid_only: grid lines along X")
    grid_lines_y: int = Field(default=2, ge=1, le=200, description="grid_only: grid lines along Y")
    spacing_x: float = Field(default=6.0, gt=0, description="grid_only: X grid spacing")
    spacing_y: float = Field(default=6.0, gt=0, description="grid_only: Y grid spacing")
    units: UnitPreset = UNITS
    discard_open_model: bool = Field(default=False, description=(
        "true only after the user agreed: the model open in the program now is closed without saving"))
    overwrite: bool = Field(default=False, description="replace file_path if it exists")
    dry_run: bool = DRY_RUN


EXT = {"SAP2000": ".sdb", "ETABS": ".edb", "SAFE": ".fdb"}


@action("new_model", "Start a new blank[[EF: or grid-only]] model in the running program (model units = `units`) "
        "and save it to file_path. The open model is closed without saving, so this needs discard_open_model: true "
        "when a model is open - ask the user first.", "Model", "edit", NewModelP,
        options={"kind": {"grid_only": "EF"}})
def new_model(p: NewModelP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    path = Path(p.file_path)
    if path.suffix.lower() != EXT[p.software]:
        raise CSIValidationError(f"A {p.software} model file ends in {EXT[p.software]}.")
    if not path.parent.is_dir():
        raise CSIValidationError(f"Folder not found: {path.parent}")
    if path.exists() and not p.overwrite:
        raise CSIValidationError(f"{path} exists.", hint="Pass overwrite: true or another file_path.")
    current = bridge.model_filename(sap, p.software)
    n_obj = sum(len(_names(sap, c)) for c in ("PointObj", "FrameObj", "AreaObj"))
    open_model = bool(current) or n_obj > 0
    plan = {"kind": p.kind, "file_path": str(path), "units": describe_units(p.units),
            "open_model": current or ("(unsaved, %d objects)" % n_obj if n_obj else None)}
    if p.kind == "grid_only":
        plan.update(stories=p.stories, story_height=p.story_height, grid=[p.grid_lines_x, p.grid_lines_y],
                    spacing=[p.spacing_x, p.spacing_y])
    if open_model and not p.discard_open_model:
        raise CSIValidationError(f"{p.software} has a model open ({plan['open_model']}); a new model closes it "
                                 "without saving.", hint="Save it first if needed, and pass discard_open_model: true "
                                                         "once the user agrees.")
    if p.dry_run:
        return _dry(plan)
    api_call("InitializeNewModel", sap.InitializeNewModel, UNIT_PRESETS[p.units])
    if p.kind == "blank":
        api_call("File.NewBlank", sap.File.NewBlank)
    else:
        api_call("File.NewGridOnly", sap.File.NewGridOnly, p.stories, p.story_height,
                 p.bottom_story_height or p.story_height, p.grid_lines_x, p.grid_lines_y, p.spacing_x, p.spacing_y)
    api_call("File.Save", sap.File.Save, str(path))
    _refresh_view(sap)
    return {**plan, "created": True, "saved_as": bridge.model_filename(sap, p.software) or str(path)}


# --------------------------------------------------------------------------- spec reading

NAME_KEYS = {"name", "start", "end", "section", "material", "load_pattern", "pattern", "group", "key", "modal_case",
             "function", "property", "spring_property", "diaphragm"}


def _cell(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, str):
        t = v.strip()
        if t == "":
            return None
        if t[0] in "[{":
            try:
                return json.loads(t)
            except ValueError:
                return t
        if t.lower() in ("true", "false"):
            return t.lower() == "true"
        try:
            f = float(t)
            return int(f) if f.is_integer() and "." not in t and "e" not in t.lower() else f
        except ValueError:
            return t
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return v


def _nest(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        v = _cell(v)
        if v is None or not str(k).strip() or str(k).startswith("Unnamed"):
            continue
        parts = str(k).strip().split(".")
        d = out
        for q in parts[:-1]:
            d = d.setdefault(q, {})
        if parts[-1] in NAME_KEYS and isinstance(v, (int, float)) and not isinstance(v, bool):
            v = str(int(v)) if float(v).is_integer() else str(v)
        d[parts[-1]] = v
    return out


def read_spec_file(path: str) -> dict[str, Any]:
    f = Path(path)
    if not f.is_file():
        raise CSIValidationError(f"Spec file not found: {path}")
    if f.suffix.lower() == ".json":
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise CSIValidationError(f"{f.name} is not valid JSON: {exc}") from exc
    if f.suffix.lower() not in (".xlsx", ".xlsm"):
        raise CSIValidationError("Spec file must be .json or .xlsx.")
    import pandas as pd
    book = pd.read_excel(f, sheet_name=None, dtype=object)
    spec: dict[str, Any] = {}
    for sheet, df in book.items():
        key = sheet.strip().lower().replace(" ", "_")
        rows = [_nest(r) for r in df.to_dict("records")]
        rows = [r for r in rows if r]
        if key == "settings":
            for r in rows:
                if "key" in r:
                    spec[str(r["key"])] = r.get("value")
        elif key in KEYS:
            kind = next(k for s, _, k in STEPS if s == key)
            spec[key] = rows[0] if kind == "one" and rows else rows
    return spec


def write_template(path: str) -> dict[str, Any]:
    """The EXAMPLE as an XLSX (a sheet per section, headers = fields; lists as JSON) and as JSON next to it."""
    import pandas as pd
    f = Path(path)
    if f.suffix.lower() != ".xlsx":
        raise CSIValidationError("The template is an .xlsx file.")
    if not f.parent.is_dir():
        raise CSIValidationError(f"Folder not found: {f.parent}")

    def flat(d: dict[str, Any], pre: str = "") -> dict[str, Any]:
        o: dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, dict):
                o.update(flat(v, f"{pre}{k}."))
            else:
                o[f"{pre}{k}"] = json.dumps(v) if isinstance(v, list) else v
        return o

    with pd.ExcelWriter(f) as xw:
        pd.DataFrame([{"key": "units", "value": EXAMPLE["units"]},
                      {"key": "description", "value": EXAMPLE["description"]}]).to_excel(xw, sheet_name="settings", index=False)
        for key, _, kind in STEPS:
            rows = EXAMPLE.get(key)
            if rows is None:
                continue
            rows = [rows] if kind == "one" else rows
            pd.DataFrame([flat(r) for r in rows]).to_excel(xw, sheet_name=key, index=False)
    j = f.with_suffix(".json")
    j.write_text(json.dumps(EXAMPLE, indent=1), encoding="utf-8")
    return {"template_xlsx": str(f), "template_json": str(j), "sheets": ["settings"] + [k for k in KEYS
                                                                                         if k in EXAMPLE]}


# --------------------------------------------------------------------------- planning

def _group_rows(key: str, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact rows -> create_* calls (consecutive rows with the same restraint / section go in one call)."""
    calls: list[dict[str, Any]] = []
    for r in rows:
        r = dict(r)
        if key == "points":
            if "xyz" not in r and all(c in r for c in "xyz"):
                r["xyz"] = [r.pop("x"), r.pop("y"), r.pop("z")]
            item = {"xyz": r.get("xyz"), **({"name": str(r["name"])} if r.get("name") not in (None, "") else {})}
            group = {"restraint": r["restraint"]} if r.get("restraint") not in (None, "", "free") else {}
            bucket = "points"
        elif key == "frames":
            item = {"start": r.get("start"), "end": r.get("end"),
                    **({"name": str(r["name"])} if r.get("name") not in (None, "") else {})}
            item = {k: (str(v) if isinstance(v, (int, float)) and k in ("start", "end") else v) for k, v in item.items()}
            group = {"section": r.get("section")}
            bucket = "frames"
        else:
            pts = r.get("points")
            if isinstance(pts, str):
                pts = pts.split()
            item = {"points": [str(q) if isinstance(q, (int, float)) else q for q in (pts or [])],
                    **({"name": str(r["name"])} if r.get("name") not in (None, "") else {})}
            group = {"section": r.get("section"), **({"opening": True} if r.get("opening") else {})}
            bucket = "areas"
        if calls and {k: v for k, v in calls[-1].items() if k != bucket} == group and len(calls[-1][bucket]) < 5000:
            calls[-1][bucket].append(item)
        else:
            calls.append({**group, bucket: [item]})
    return calls


def _plan(spec: dict[str, Any], sw: str, units: str, allow_unlock: bool, sap: Any,
          use_model: bool = True) -> tuple[list, list[str]]:
    import catalog
    import programs
    unknown = sorted(set(spec) - TOP)
    errors: list[str] = []
    if unknown:
        errors.append(f"unknown sections {unknown}; sections are {KEYS}")
    plan: list[tuple[str, str, dict[str, Any]]] = []
    for key, name, kind in STEPS:
        if key not in spec or spec[key] in (None, [], {}):
            continue
        act = ACTIONS[name]
        if sw not in programs.expand(act.programs):
            errors.append(f"{key}: {name} is not available in {sw}")
            continue
        val = spec[key]
        if kind == "one":
            items = [val] if isinstance(val, dict) else []
            if not items:
                errors.append(f"{key}: give one object")
        elif not isinstance(val, list):
            errors.append(f"{key}: give a list")
            continue
        else:
            items = _group_rows(key, val) if kind == "rows" else val
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"{key}[{i}]: give an object")
                continue
            prm = {"software": sw, **item}
            fields = act.params.model_fields
            if "units" in fields and "units" not in item:
                prm["units"] = units
            for k, v in (("dry_run", False), ("snapshot", False), ("allow_unlock", allow_unlock)):
                if k in fields:
                    prm[k] = v
            if name == "edit_database_table":
                prm.setdefault("mode", "edit")
            try:
                validated = act.params.model_validate(prm)
                catalog.check_program_options(act, validated, sw)
            except ValidationError as exc:
                msgs = "; ".join(f"{'.'.join(str(x) for x in e['loc'])}: {e['msg']}" for e in exc.errors()[:4])
                errors.append(f"{key}[{i}] ({name}): {msgs}")
                continue
            except CSIValidationError as exc:
                errors.append(f"{key}[{i}] ({name}): {exc}")
                continue
            plan.append((key, name, prm))
    errors += _references(spec, sap, use_model)
    if use_model:
        errors += _clashes(spec, sap)
    return plan, errors


DEFINED = {"materials": ("PropMaterial", "material"), "frame_sections": ("PropFrame", "frame section"),
           "area_sections": ("PropArea", "area section"), "load_patterns": ("LoadPatterns", "load pattern"),
           "load_cases": ("LoadCases", "load case"), "combinations": ("RespCombo", "combination"),
           "functions": ("Func", "function")}


def _clashes(spec: dict[str, Any], sap: Any) -> list[str]:
    """A definition whose name the model already has (upper / lower case ignored) needs replace: true."""
    errs = []
    for key, (coll, kind) in DEFINED.items():
        items = spec.get(key) or []
        if not items:
            continue
        try:
            have = {n.lower(): n for n in (bridge.try_name_list(sap, [coll])[0] or [])}
        except Exception:  # noqa: BLE001
            continue
        for i, it in enumerate(items):
            if not isinstance(it, dict) or it.get("replace"):
                continue
            n = str(it.get("name", ""))
            if n.lower() in have:
                errs.append(f"{key}[{i}]: the model already has {kind} '{have[n.lower()]}' - add replace: true to "
                            "change it, or use another name")
    return errs


def _references(spec: dict[str, Any], sap: Any, use_model: bool = True) -> list[str]:
    """Names a step refers to must be made by an earlier step or exist in the model (not when new_model replaces
    it)."""
    def names(key: str) -> set[str]:
        return {str(x.get("name")) for x in spec.get(key) or [] if isinstance(x, dict) and x.get("name") is not None}

    def model(coll: str) -> set[str]:
        if not use_model:
            return set()
        try:
            return set(bridge.try_name_list(sap, [coll])[0] or [])
        except Exception:  # noqa: BLE001
            return set()

    errs: list[str] = []
    fsec = names("frame_sections") | model("PropFrame") | {"none", "None"}
    asec = names("area_sections") | model("PropArea")
    pts = names("points") | (set(_names(sap, "PointObj")) if use_model else set())
    pats = names("load_patterns") | model("LoadPatterns")
    mats = names("materials") | model("PropMaterial")
    for i, s in enumerate(spec.get("frame_sections") or []):
        if isinstance(s, dict) and s.get("material") and str(s["material"]) not in mats:
            errs.append(f"frame_sections[{i}]: material '{s['material']}' is neither in the spec nor the model")
    for i, s in enumerate(spec.get("area_sections") or []):
        if isinstance(s, dict) and s.get("material") and str(s["material"]) not in mats:
            errs.append(f"area_sections[{i}]: material '{s['material']}' is neither in the spec nor the model")
    for i, f in enumerate(spec.get("frames") or []):
        if not isinstance(f, dict):
            continue
        if str(f.get("section")) not in fsec:
            errs.append(f"frames[{i}]: section '{f.get('section')}' is neither in the spec nor the model")
        for end in ("start", "end"):
            v = f.get(end)
            if isinstance(v, (str, int)) and str(v) not in pts:
                errs.append(f"frames[{i}]: point '{v}' is neither in the spec nor the model")
    for i, a in enumerate(spec.get("areas") or []):
        if not isinstance(a, dict):
            continue
        if str(a.get("section")) not in asec:
            errs.append(f"areas[{i}]: section '{a.get('section')}' is neither in the spec nor the model")
        q = a.get("points")
        for v in (q.split() if isinstance(q, str) else q or []):
            if isinstance(v, (str, int)) and str(v) not in pts:
                errs.append(f"areas[{i}]: point '{v}' is neither in the spec nor the model")
    for key in ("loads", "area_loads", "temperature_loads"):
        for i, ld in enumerate(spec.get(key) or []):
            if isinstance(ld, dict) and ld.get("load_pattern") and str(ld["load_pattern"]) not in pats:
                errs.append(f"{key}[{i}]: load pattern '{ld['load_pattern']}' is neither in the spec nor the model")
    return errs[:50]


# --------------------------------------------------------------------------- build

class SpecP(Params):
    software: Software = SOFTWARE
    spec: dict[str, Any] | None = Field(default=None, description=(
        "the model as JSON: sections " + ", ".join(KEYS) + " (each a list of the parameters of the action that "
        "makes it; points / frames / areas as compact rows {name, xyz, restraint} / {name, start, end, section} / "
        "{name, points, section}; mass_source and analysis_options one object), plus units, save_as, new_model"))
    spec_file: str | None = Field(default=None, description=".json, or .xlsx with a sheet per section (header row "
                                                            "= fields; lists as JSON text; a.b = nested field) and a "
                                                            "settings sheet (key, value)")
    write_template: str | None = Field(default=None, description=(
        "instead of building: write an example .xlsx template (portal frame) and its .json here"))
    units: UnitPreset | None = Field(default=None, description="default: the spec's units, else kN_m_C")
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT

    @model_validator(mode="after")
    def _one(self) -> "SpecP":
        if sum(x is not None for x in (self.spec, self.spec_file, self.write_template)) != 1:
            raise ValueError("give one of spec, spec_file, write_template")
        return self


def _counts(sap: Any) -> dict[str, int]:
    out = {k: len(_names(sap, c)) for k, c in (("points", "PointObj"), ("frames", "FrameObj"), ("areas", "AreaObj"))}
    for k, c in (("load_patterns", "LoadPatterns"), ("combinations", "RespCombo")):
        try:
            out[k] = len(bridge.try_name_list(sap, [c])[0] or [])
        except Exception:  # noqa: BLE001
            pass
    return out


@action("build_model_from_spec", "Build or extend the model from one spec - a JSON object, a .json file or an "
        ".xlsx workbook (a sheet per section): materials, sections, springs, patterns, points, frames, areas, "
        "assignments, groups, loads, cases, combinations, mass source, analysis options, table edits. The whole "
        "spec is validated (parameters and references) before anything changes; the steps then run in dependency "
        "order through the same actions, stopping at the first refusal. write_template writes an example workbook.",
        "Bulk editing", "edit", SpecP, long_running=True)
def build_model_from_spec(p: SpecP) -> dict[str, Any]:
    if p.write_template:
        return write_template(p.write_template)
    spec = dict(p.spec) if p.spec is not None else read_spec_file(p.spec_file or "")
    units = p.units or spec.get("units") or "kN_m_C"
    if units not in UNIT_PRESETS:
        raise CSIValidationError(f"units '{units}' is not a preset.", hint=f"Presets: {list(UNIT_PRESETS)[:12]}")
    sap = bridge.sap_model(p.software)
    nm = spec.get("new_model")
    if nm is not None:
        if not isinstance(nm, dict):
            raise CSIValidationError("new_model: give the new_model parameters (file_path, kind ...).")
        nm_params = NewModelP.model_validate({"software": p.software, "units": units, **nm, "dry_run": True})
        new_model(nm_params)                      # checks file, open model and discard flag before anything runs
    plan, errors = _plan(spec, p.software, units, p.allow_unlock, sap, use_model=nm is None)
    summary = [{"step": k, "action": n} for k, n, _ in plan]
    steps_by_key: dict[str, int] = {}
    for k, _, _ in plan:
        steps_by_key[k] = steps_by_key.get(k, 0) + 1
    head = {"units": describe_units(units), "calls_planned": len(plan), "calls_by_section": steps_by_key,
            **({"description": spec["description"]} if spec.get("description") else {})}
    if errors:
        raise CSIValidationError(f"The spec has {len(errors)} problem(s); nothing was changed.",
                                 hint=" | ".join(errors[:12]))
    if p.dry_run:
        return _dry({**head, "steps": summary[:200]}, model_locked=bridge.is_locked(sap))
    snap = None
    if nm is not None:
        new_model(NewModelP.model_validate({"software": p.software, "units": units, **nm, "dry_run": False}))
        sap = bridge.sap_model(p.software)
        clash = _clashes(spec, sap)        # the new model's own defaults (e.g. DEAD, Dead / Live)
        if clash:
            raise CSIValidationError("The new model was created and saved, but the spec clashes with its default "
                                     "definitions; nothing else was changed.", hint=" | ".join(clash[:12]))
    elif p.snapshot:
        snap = _take_snapshot(p.software, sap, "before_build_from_spec")
    before = _counts(sap)
    done: list[dict[str, Any]] = []
    for i, (key, name, prm) in enumerate(plan):
        act = ACTIONS[name]
        try:
            res = act.run(act.params.model_validate(prm))
        except Exception as exc:  # noqa: BLE001 - stop, and say exactly where
            return {**head, "completed": False, "failed_step": {"index": i, "section": key, "action": name,
                                                                 "params": _short(prm),
                                                                 "error": f"{type(exc).__name__}: {exc}",
                                                                 **({"hint": exc.hint} if getattr(exc, "hint", None)
                                                                    else {})},
                    "steps_done": len(done), "done": done[-30:], "counts_before": before, "counts_now": _counts(sap),
                    **({"snapshot": snap} if snap else {}),
                    "note": "Steps before the failed one stay in the model; the snapshot (if taken) restores it."}
        if key in ("points", "frames", "areas"):
            want = [x.get("name") for x in prm[key]]
            got = res.get("point_names", []) if key == "points" else [c["name"] for c in res.get("created", [])]
            clash = [(w, g) for w, g in zip(want, got) if w and str(w) != str(g)]
            if clash:
                return {**head, "completed": False, "failed_step": {
                    "index": i, "section": key, "action": name,
                    "error": f"names not kept {clash[:10]}: an object already existed at that place, so the program "
                             "reused it; later steps would refer to the wrong names"},
                    "steps_done": len(done), "done": done[-30:], "counts_now": _counts(sap),
                    **({"snapshot": snap} if snap else {})}
        done.append({"section": key, "action": name, **_brief(res)})
    save = spec.get("save_as")
    saved = None
    if save or nm is not None:
        api_call("File.Save", sap.File.Save, str(save) if save else "")
        saved = bridge.model_filename(sap, p.software)
    _refresh_view(sap)
    return {**head, "completed": True, "steps_done": len(done), "done": done[:80], "truncated": len(done) > 80,
            "counts_before": before, "counts_now": _counts(sap), **({"saved_as": saved} if saved else {}),
            **({"snapshot": snap} if snap else {})}


def _short(prm: dict[str, Any]) -> dict[str, Any]:
    s = json.dumps(prm, default=str)
    return prm if len(s) < 1500 else {"(params)": s[:1500] + " ..."}


def _brief(res: dict[str, Any]) -> dict[str, Any]:
    if "created" in res:
        return {"created": [c["name"] for c in res["created"]][:20], "count": len(res["created"])}
    keep = ("name", "point_names", "spring_property", "pattern", "load_pattern",
            "succeeded", "failed", "combination", "case", "applied", "rows_added", "rows_changed")
    out = {}
    for k in keep:
        if k in res:
            v = res[k]
            out[k] = v[:20] if isinstance(v, list) else v
    return out
