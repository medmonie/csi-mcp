"""Phase 2: typed analysis results through SapModel.Results (read only) and analysis-log diagnostics.

get_results reads one result type for the chosen cases / combinations with the program's own output setup
(Results.Setup): every object of the group 'All' in one call, or the named objects. The output selection and the
step options the user had are put back afterwards. Long results go in full to CSV / XLSX / JSON (export_path);
the reply keeps the first rows, the governing max / min of every component with where and in which case it
occurs, and optionally an envelope per object.

get_analysis_log reads the <model>.LOG / .OUT the solver wrote next to the model: stability check (negative
eigenvalues), warnings, cases solved, modes found, equations, start / complete time.
"""
from __future__ import annotations

import contextlib
import datetime as dt
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

import safety
from actions import Params, SOFTWARE, UNITS, _selection, action, bridge
from csi_bridge import COMError, CSIError, CSIValidationError, Software, UnitPreset, api_call, as_list, describe_units
from table_tools import TableError, py, records

OBJ_HEAD = ["LoadCase", "StepType", "StepNum"]

# result_type -> (Results method, per-object (takes Name, ItemTypeElm), column names of the out arrays after the
# count, programs that have it, short description)
RESULTS: dict[str, tuple[str, bool, list[str], str, str]] = {
    "joint_displacements": ("JointDispl", True, ["Joint", "Element"] + OBJ_HEAD + ["U1", "U2", "U3", "R1", "R2", "R3"],
                            "ESF", "joint displacements and rotations (global)"),
    "joint_reactions": ("JointReact", True, ["Joint", "Element"] + OBJ_HEAD + ["F1", "F2", "F3", "M1", "M2", "M3"],
                        "ESF", "support reactions per joint (global)"),
    "frame_forces": ("FrameForce", True, ["Frame", "Station", "Element", "ElementStation"] + OBJ_HEAD +
                     ["P", "V2", "V3", "T", "M2", "M3"], "ESF", "frame internal forces at the output stations (local)"),
    "frame_joint_forces": ("FrameJointForce", True, ["Frame", "Element", "PointElm"] + OBJ_HEAD +
                           ["F1", "F2", "F3", "M1", "M2", "M3"], "ESF", "frame end forces on the joints"),
    "area_forces": ("AreaForceShell", True, ["Area", "Element", "PointElm"] + OBJ_HEAD +
                    ["F11", "F22", "F12", "FMax", "FMin", "FAngle", "FVM", "M11", "M22", "M12", "MMax", "MMin",
                     "MAngle", "V13", "V23", "VMax", "VAngle"], "ESF", "shell forces / moments per length at nodes"),
    "area_stresses": ("AreaStressShell", True, ["Area", "Element", "PointElm"] + OBJ_HEAD +
                      ["S11Top", "S22Top", "S12Top", "SMaxTop", "SMinTop", "SAngleTop", "SVMTop", "S11Bot", "S22Bot",
                       "S12Bot", "SMaxBot", "SMinBot", "SAngleBot", "SVMBot", "S13Avg", "S23Avg", "SMaxAvg",
                       "SAngleAvg"], "ESF", "shell stresses top / bottom at nodes"),
    "mode_shapes": ("ModeShape", True, ["Joint", "Element"] + OBJ_HEAD + ["U1", "U2", "U3", "R1", "R2", "R3"],
                    "ESF", "modal displacements (arbitrary scale)"),
    "base_reactions": ("BaseReact", False, OBJ_HEAD + ["FX", "FY", "FZ", "MX", "MY", "MZ"], "ESF",
                       "base reaction totals"),
    "modal_periods": ("ModalPeriod", False, OBJ_HEAD + ["Period", "Frequency", "CircFreq", "EigenValue"], "ESF",
                      "periods and frequencies"),
    "modal_mass_ratios": ("ModalParticipatingMassRatios", False, OBJ_HEAD +
                          ["Period", "UX", "UY", "UZ", "SumUX", "SumUY", "SumUZ", "RX", "RY", "RZ", "SumRX",
                           "SumRY", "SumRZ"], "ESF", "modal participating mass ratios"),
    "modal_participation_factors": ("ModalParticipationFactors", False, OBJ_HEAD +
                                    ["Period", "UX", "UY", "UZ", "RX", "RY", "RZ", "ModalMass", "ModalStiff"], "ESF",
                                    "modal participation factors, modal mass and stiffness"),
    "buckling_factors": ("BucklingFactor", False, OBJ_HEAD + ["Factor"], "ESF", "buckling factors"),
    "story_drifts": ("StoryDrifts", False, ["Story", "LoadCase", "StepType", "StepNum", "Direction", "Drift", "Label",
                                            "X", "Y", "Z"], "EF", "story drift ratios (ETABS / SAFE)"),
    "joint_drifts": ("JointDrifts", False, ["Story", "Label", "Joint", "LoadCase", "StepType", "StepNum",
                                            "DisplacementX", "DisplacementY", "DriftX", "DriftY"], "EF",
                     "joint drifts (ETABS / SAFE)"),
    "pier_forces": ("PierForce", False, ["Story", "Pier", "LoadCase", "Location", "P", "V2", "V3", "T", "M2", "M3"],
                    "E", "pier forces (ETABS)"),
    "spandrel_forces": ("SpandrelForce", False, ["Story", "Spandrel", "LoadCase", "Location", "P", "V2", "V3", "T",
                                                 "M2", "M3"], "E", "spandrel forces (ETABS)"),
    "section_cuts": ("SectionCutAnalysis", False, ["SectionCut"] + OBJ_HEAD + ["F1", "F2", "F3", "M1", "M2", "M3"],
                     "ESF", "section cut forces (analysis sign convention)"),
}
NUMERIC_IDS = {"Station", "ElementStation", "StepNum", "X", "Y", "Z"}
LETTER = {"ETABS": "E", "SAP2000": "S", "SAFE": "F"}
STEP = {"envelope": 1, "step_by_step": 2, "last_step": 3}
STEP_OPTIONS = ("NLStatic", "DirectHist", "ModalHist", "MultiStepStatic", "MultiValuedCombo")
ITEM_OBJECT, ITEM_GROUP = 0, 2


class ResultsP(Params):
    software: Software = SOFTWARE
    result_type: Literal[
        "joint_displacements", "joint_reactions", "frame_forces", "frame_joint_forces", "area_forces",
        "area_stresses", "mode_shapes", "base_reactions", "modal_periods", "modal_mass_ratios",
        "modal_participation_factors", "buckling_factors", "story_drifts", "joint_drifts", "pier_forces",
        "spandrel_forces", "section_cuts"] = Field(description=(
            "joint_displacements | joint_reactions | frame_forces | frame_joint_forces | area_forces | "
            "area_stresses | mode_shapes | base_reactions | modal_periods | modal_mass_ratios | "
            "modal_participation_factors | buckling_factors | [[EF: story_drifts | joint_drifts | ]][[E: pier_forces | "
            "spandrel_forces | ]]section_cuts"))
    load_cases_or_combos: list[str] | None = Field(default=None, description=(
        "cases / combinations to output; default every case that ran and every combination"))
    names: list[str] | None = Field(default=None, max_length=2000, description=(
        "object names (joints, frames or areas, by result type); default every object (group)"))
    group: str | None = Field(default=None, description="a group instead of names; default the group of all objects")
    step_option: Literal["envelope", "step_by_step", "last_step"] = Field(default="envelope", description=(
        "nonlinear / history / multi-step cases and multi-valued combinations: envelope (default) | step_by_step | "
        "last_step"))
    envelope_by_object: bool = Field(default=False, description=(
        "also give max / min of each component per object over the selected cases, steps and stations"))
    component: str | None = Field(default=None, description="sort the returned rows by |this component|, e.g. M3")
    max_rows: int = Field(default=50, ge=1, le=2000, description="rows returned, default 50; statistics cover all")
    export: Literal["auto", "csv", "xlsx", "json", "none"] = Field(default="auto", description=(
        "write all rows to the output folder: auto (default) = CSV when the rows are cut | csv | xlsx | json | none"))
    units: UnitPreset = UNITS


# --------------------------------------------------------------------------- output setup (COM thread)

class _OutputSetup:
    """Select cases / combos and step options for output; put back what the user had."""

    def __init__(self, sap: Any) -> None:
        self.sap, self.setup = sap, sap.Results.Setup
        self.saved_cases: dict[str, bool] = {}
        self.saved_combos: dict[str, bool] = {}
        self.saved_opts: dict[str, int] = {}

    def __enter__(self) -> "_OutputSetup":
        return self

    def select(self, cases: list[str], combos: list[str], step: str) -> None:
        sap, s = self.sap, self.setup
        for c in bridge.name_list(sap, "LoadCases"):
            o = _quiet("Results.Setup.GetCaseSelectedForOutput", s.GetCaseSelectedForOutput, c)
            if o is not None:
                self.saved_cases[c] = bool(o[0])
        for c in bridge.try_name_list(sap, ["RespCombo"])[0] or []:
            o = _quiet("Results.Setup.GetComboSelectedForOutput", s.GetComboSelectedForOutput, c)
            if o is not None:
                self.saved_combos[c] = bool(o[0])
        for opt in STEP_OPTIONS:
            o = _quiet(f"Results.Setup.GetOption{opt}", getattr(s, f"GetOption{opt}", None))
            if o is not None:
                self.saved_opts[opt] = int(o[0])
        api_call("Results.Setup.DeselectAllCasesAndCombosForOutput", s.DeselectAllCasesAndCombosForOutput)
        for c in cases:
            api_call("Results.Setup.SetCaseSelectedForOutput", s.SetCaseSelectedForOutput, c, True)
        for c in combos:
            api_call("Results.Setup.SetComboSelectedForOutput", s.SetComboSelectedForOutput, c, True)
        for opt in self.saved_opts:
            _quiet(f"Results.Setup.SetOption{opt}", getattr(s, f"SetOption{opt}"), STEP[step])

    def __exit__(self, *exc: Any) -> None:
        s = self.setup
        with contextlib.suppress(Exception):
            api_call("Results.Setup.DeselectAllCasesAndCombosForOutput", s.DeselectAllCasesAndCombosForOutput)
        for c, sel in self.saved_cases.items():
            if sel:
                _quiet("Results.Setup.SetCaseSelectedForOutput", s.SetCaseSelectedForOutput, c, True)
        for c, sel in self.saved_combos.items():
            if sel:
                _quiet("Results.Setup.SetComboSelectedForOutput", s.SetComboSelectedForOutput, c, True)
        for opt, v in self.saved_opts.items():
            _quiet(f"Results.Setup.SetOption{opt}", getattr(s, f"SetOption{opt}"), v)


def _quiet(method: str, func: Any, *args: Any) -> list[Any] | None:
    if func is None:
        return None
    try:
        return api_call(method, func, *args)
    except (CSIError, COMError, AttributeError, TypeError, OSError):
        return None


def _all_group(sap: Any) -> str:
    groups = bridge.name_list(sap, "GroupDef")
    return next((g for g in groups if g.lower() == "all"), "ALL")


def _call(sap: Any, method: str, per_object: bool, cols: list[str], name: str | None = None,
          item: int = ITEM_GROUP) -> list[list[Any]]:
    """One Results call -> rows. An empty result (no rows for this selection) is returned as []."""
    func = getattr(sap.Results, method)
    args = (name, item) if per_object else ()
    try:
        outs = api_call(f"Results.{method}", func, *args)
    except CSIError as exc:
        # A non-zero return code means nothing matched this selection (e.g. no buckling case); a COM failure
        # (no return code) is a real error.
        if getattr(exc, "ret", None) is not None:
            return []
        raise
    n = int(outs[0]) if outs else 0
    if n == 0:
        return []
    arrays = [as_list(outs[i]) for i in range(1, 1 + len(cols))]
    if any(len(a) != n for a in arrays):
        raise CSIValidationError(f"Results.{method}: arrays do not have {n} items each.")
    return [list(r) for r in zip(*arrays)]


def read_results(sap: Any, software: str, rt: str, cases: list[str], combos: list[str], step: str,
                 names: list[str] | None, group: str | None):
    import pandas as pd
    method, per_object, cols, progs, _ = RESULTS[rt]
    if LETTER[software] not in progs:
        raise CSIValidationError(f"{rt} is not available in {software}.")
    with _OutputSetup(sap) as out:
        out.select(cases, combos, step)
        rows: list[list[Any]] = []
        if per_object and names:
            for n in names:
                rows += _call(sap, method, True, cols, n, ITEM_OBJECT)
        elif per_object:
            rows = _call(sap, method, True, cols, group or _all_group(sap), ITEM_GROUP)
        else:
            rows = _call(sap, method, False, cols)
    df = pd.DataFrame(rows, columns=cols)
    for c in df.columns:
        if c in NUMERIC_IDS or c not in ("Joint", "Element", "Frame", "Area", "PointElm", "LoadCase", "StepType",
                                         "Story", "Label", "Pier", "Spandrel", "Location", "SectionCut", "Direction"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        else:
            df[c] = df[c].astype(str)
    return df


def _governing(df, comps: list[str], where: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for c in comps:
        s = df[c].dropna()
        if s.empty:
            continue
        i_max, i_min, i_abs = s.idxmax(), s.idxmin(), s.abs().idxmax()
        loc = lambda i: {k: py(df.at[i, k]) for k in where if str(df.at[i, k]) not in ("", "nan")}  # noqa: E731
        out[c] = {"max": py(s[i_max]), "max_at": loc(i_max), "min": py(s[i_min]), "min_at": loc(i_min),
                  "abs_max": py(s[i_abs]), "abs_max_at": loc(i_abs)}
    return out


@action("get_results", "Typed analysis results through the program's results API for chosen cases / combinations: "
        "joint displacements / reactions, frame forces at stations, frame joint forces, shell forces / stresses, "
        "mode shapes, base reactions, modal periods / mass ratios / participation factors, buckling factors[[EF:, "
        "story and joint drifts]][[E:, pier / spandrel forces]], section cuts. All objects in one call (or named objects / "
        "a group); governing max / min with location, optional envelope per object; long results to CSV / XLSX. "
        "The user's output selection is put back.", "Results", "read", ResultsP, long_running=True,
        options={"result_type": {k: v[3] for k, v in RESULTS.items() if v[3] != "ESF"}})
def get_results(p: ResultsP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    if not bridge.is_locked(sap):
        raise TableError("The model has no analysis results.", hint="Run run_analysis first (with the user's go-ahead).")
    cases, combos = _selection(sap, p.load_cases_or_combos)
    status = bridge.case_status(sap)
    if not p.load_cases_or_combos:
        cases = [c for c in cases if status.get(c) == "Finished"]
    not_run = [c for c in cases if status.get(c) != "Finished"]
    if not_run:
        raise TableError(f"These cases have no results: {not_run}.", hint="Run them, or leave them out.")
    with bridge.units_scope(sap, p.units):
        df = read_results(sap, p.software, p.result_type, cases, combos, p.step_option, p.names, p.group)
    method, _, cols, _, desc = RESULTS[p.result_type]
    ids = [c for c in cols if not _is_value(c)]
    comps = [c for c in cols if _is_value(c)]
    res: dict[str, Any] = {"software": p.software, "result_type": p.result_type, "description": desc,
                           "source": f"Results.{method}", "units": describe_units(p.units),
                           "cases": cases, "combinations": combos, "step_option": p.step_option,
                           "total_rows": int(len(df))}
    if df.empty:
        res.update(rows=[], governing={}, note="No rows for this selection (check the cases / combinations and "
                                               "that these objects exist in the analysis).")
        return res
    res["governing"] = _governing(df, comps, ids)
    obj_col = next((c for c in ("Frame", "Area", "Joint", "Pier", "Spandrel", "SectionCut", "Story") if c in df.columns),
                   None)
    if p.envelope_by_object and obj_col:
        g = df.groupby(obj_col)[comps]
        env = g.max().add_suffix("_max").join(g.min().add_suffix("_min")).reset_index()
        res["envelope_by_object"] = {"by": obj_col, "count": int(len(env)), "rows": records(env, p.max_rows),
                                     "truncated": len(env) > p.max_rows}
        if len(env) > p.max_rows:
            res["envelope_by_object"]["export_path"] = str(safety.export_frame(
                env, f"{p.software}_{p.result_type}_envelope", "csv" if p.export in ("auto", "none") else p.export))
    shown = df
    if p.component:
        col = next((c for c in comps if c.lower() == p.component.lower()), None)
        if col is None:
            raise CSIValidationError(f"component must be one of {comps}.")
        shown = df.reindex(df[col].abs().sort_values(ascending=False).index)
    res["rows"] = records(shown, p.max_rows)
    res["truncated"] = len(df) > p.max_rows
    fmt = {"auto": "csv" if len(df) > p.max_rows else None, "none": None}.get(p.export, p.export)
    res["export_path"] = str(safety.export_frame(shown, f"{p.software}_{p.result_type}", fmt)) if fmt else None
    return res


def _is_value(col: str) -> bool:
    return col not in {"Joint", "Element", "Frame", "Area", "PointElm", "LoadCase", "StepType", "StepNum", "Station",
                       "ElementStation", "Story", "Label", "Pier", "Spandrel", "Location", "SectionCut", "Direction",
                       "X", "Y", "Z"}


# --------------------------------------------------------------------------- analysis log

class LogP(Params):
    software: Software = SOFTWARE
    model_path: str | None = Field(default=None, description="a saved model file; default the open model")
    max_lines: int = Field(default=60, ge=5, le=500, description="most warning / info lines returned (default 60)")


_SPACED = re.compile(r"\b(?:[A-Z] ){3,}[A-Z]\b")
_KV = re.compile(r"^\s*([A-Z][A-Z0-9 /().,'-]+?)\s*=\s*(.+?)\s*$")
_BAD = re.compile(r"WARNING|ERROR|INSTAB|ILL-COND|SINGULAR|LOST\b.*ACCURACY|NOT\s+CONVERGE|FAIL|NEGATIVE\s+STIFF",
                  re.I)


def _unspace(line: str) -> str:
    """'A N A L Y S I S   C O M P L E T E    2026/..' -> 'ANALYSIS COMPLETE 2026/..'"""
    line = _SPACED.sub(lambda m: m.group().replace(" ", ""), line)
    return re.sub(r"\s+", " ", line).strip()


def parse_log(text: str, max_lines: int = 60) -> dict[str, Any]:
    lines = [_unspace(l) for l in text.replace("\r", "").split("\n")]
    out: dict[str, Any] = {"program": next((l for l in lines if "Build" in l and "64-bit" in l), ""),
                           "counts": {}, "cases_solved": [], "modes_found": None, "negative_eigenvalues": None,
                           "began": None, "completed": None, "messages": [], "periods": []}
    block: list[str] | None = None
    for i, l in enumerate(lines):
        if l.startswith("BEGIN ANALYSIS"):
            out["began"] = l.replace("BEGIN ANALYSIS", "").strip()
        elif l.startswith("ANALYSIS COMPLETE"):
            out["completed"] = l.replace("ANALYSIS COMPLETE", "").strip()
        elif l.startswith("Found mode"):
            m_ = re.search(r"T=\s*([-+0-9.Ee]+)", l)
            if m_:
                out["periods"].append(float(m_.group(1)))
        elif l.startswith("CASE:"):
            name = l[5:].strip()
            if name not in out["cases_solved"]:
                out["cases_solved"].append(name)
        m = _KV.match(l)
        if m:
            key, val = m.group(1).strip(), m.group(2).strip()
            if key.startswith("NUMBER OF NEGATIVE EIGENVALUES") or key.startswith("NUMBER FOUND AT FINAL CONVERGED"):
                # linear cases: "NUMBER OF NEGATIVE EIGENVALUES = 0, OK."; nonlinear (force-controlled) cases:
                # "NUMBER FOUND AT FINAL CONVERGED STATE = 0, OK." - keep the worst over all cases
                m_ = re.match(r"-?\d+", val)
                if m_:
                    n_ = int(m_.group())
                    prev = out["negative_eigenvalues"]
                    out["negative_eigenvalues"] = n_ if not isinstance(prev, int) else max(prev, n_)
                    out["stability_checks"] = out.get("stability_checks", 0) + 1
            elif key == "NUMBER OF EIGEN MODES FOUND":
                out["modes_found"] = int(val) if val.isdigit() else val
            elif key.startswith(("NUMBER OF", "WITH ", "TOTAL NUMBER")) and re.fullmatch(r"-?\d+", val):
                out["counts"][key.title()] = int(val)
        if re.match(r"^\*\s*\*\s*\*", l):          # '* * * W A R N I N G * * *' / INFO blocks
            kind = re.sub(r"[*\s]", "", l)
            block = [kind]
            out["messages"].append(block)
            continue
        if block is not None:
            if l:
                block.append(l)
            elif len(block) > 1:
                block = None
        elif _BAD.search(l) and "SHOULD BE ZERO" not in l and "LIMITED INSTABILITY" not in l:
            out["messages"].append(["LINE", l])
    msgs = [{"kind": b[0], "text": " ".join(b[1:])[:400]} for b in out["messages"]]
    out["messages"] = msgs[:max_lines]
    out["warnings"] = sum(1 for m in msgs if m["kind"] not in ("INFO",))
    neg = out["negative_eigenvalues"]
    out["stable"] = (neg == 0) if isinstance(neg, int) else None
    out["finished"] = out["completed"] is not None
    return out


@action("get_analysis_log", "Diagnostics from the solver's log next to the model (<model>.LOG / .OUT): stability "
        "check (negative stiffness eigenvalues), warnings and info messages, cases solved, modes found with their "
        "periods, equations, start / complete time.", "Results", "read", LogP)
def get_analysis_log(p: LogP) -> dict[str, Any]:
    if p.model_path:
        model = Path(p.model_path).expanduser()
    else:
        name = bridge.model_filename(bridge.sap_model(p.software), p.software)
        if not name:
            raise CSIValidationError("The open model is not saved; the solver log sits next to the model file.")
        model = Path(name)
    out: dict[str, Any] = {"software": p.software, "model_file": str(model), "files": {}}
    log = None
    for ext in (".LOG", ".OUT"):
        f = model.with_suffix(ext)
        if f.is_file():
            out["files"][ext[1:]] = {"path": str(f), "modified": _mtime(f)}
            if ext == ".LOG":
                log = f
    if log is None:
        raise TableError(f"No {model.with_suffix('.LOG').name} next to the model.",
                         hint="Run the analysis (the solver writes the log when it runs), or give model_path.")
    out.update(parse_log(log.read_text(encoding="latin-1", errors="replace"), p.max_lines))
    if "OUT" in out["files"]:
        extra = parse_log(Path(out["files"]["OUT"]["path"]).read_text(encoding="latin-1", errors="replace"),
                          p.max_lines)["messages"]
        out["out_file_messages"] = extra
    return out


def _mtime(f: Path) -> str:
    return dt.datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")


# --------------------------------------------------------------------------- report R3 data (COM thread)

def default_case(sap: Any, status: dict[str, str]) -> str | None:
    """First combination, else the first finished linear static case that is not internal (~...)."""
    combos = bridge.try_name_list(sap, ["RespCombo"])[0] or []
    if combos:
        return combos[0]
    for c in bridge.name_list(sap, "LoadCases"):
        if status.get(c) != "Finished" or c.startswith("~"):
            continue
        t = _quiet("LoadCases.GetTypeOAPI", sap.LoadCases.GetTypeOAPI, c)
        if t and int(t[0]) == 1:
            return c
    return None


def _peak_by(df, key: str, cols: list[str]) -> dict[str, tuple[float, ...]]:
    """Per key, the row with the largest magnitude over cols (envelope combos give Max and Min rows)."""
    out: dict[str, tuple[float, ...]] = {}
    for k, g in df.groupby(key):
        mag = (g[cols].astype(float) ** 2).sum(axis=1)
        out[str(k)] = tuple(float(x) for x in g.loc[mag.idxmax(), cols])
    return out


def diagram_data(sap: Any, sw: str, case: str, views: list[str], frame_comp: str, shell_comp: str,
                 max_points: int = 20000, wall_comp: str = "F22") -> dict[str, Any]:
    cases, combos = _selection(sap, [case])
    out: dict[str, Any] = {"case": case, "skipped": []}

    def get(rt: str):
        try:
            return read_results(sap, sw, rt, cases, combos, "envelope", None, None)
        except (CSIError, CSIValidationError, TableError, COMError, AttributeError) as exc:
            out["skipped"].append((rt.replace("_", " "), str(exc)[:200]))
            return None

    if "deformed" in views:
        df = get("joint_displacements")
        if df is not None and not df.empty:
            out["disp"] = _peak_by(df, "Joint", ["U1", "U2", "U3"])
    if "reactions" in views:
        df = get("joint_reactions")
        if df is not None and not df.empty:
            out["react"] = _peak_by(df, "Joint", ["F1", "F2", "F3", "M1", "M2", "M3"])
    if "frame_forces" in views:
        df = get("frame_forces")
        if df is not None and not df.empty:
            comps = ["P", "V2", "V3", "T", "M2", "M3"]
            forces: dict[str, list[tuple[float, dict[str, float]]]] = {}
            for (fr, sta), g in df.groupby(["Frame", "Station"]):
                vals = {c: float(g[c].astype(float).loc[g[c].astype(float).abs().idxmax()]) for c in comps}
                forces.setdefault(str(fr), []).append((float(sta), vals))
            out["forces"] = forces
            axes = {}
            if len(forces) <= 5000:
                for fr in forces:
                    m = _quiet("FrameObj.GetTransformationMatrix", getattr(sap.FrameObj, "GetTransformationMatrix", None), fr)
                    vals = [float(x) for x in as_list(m[0])] if m else []
                    if len(vals) == 9:   # local -> global: columns are the local 1, 2, 3 axes
                        axes[fr] = ((vals[0], vals[3], vals[6]), (vals[1], vals[4], vals[7]), (vals[2], vals[5], vals[8]))
            out["axes"] = axes
    if "shell_contours" in views:
        df = get("area_forces")
        if df is not None and not df.empty:
            pts = df["PointElm"].unique()
            if len(pts) > max_points:
                out["skipped"].append(("shell contours", f"{len(pts)} element joints (more than {max_points})"))
            else:
                xyz = {}
                for p_ in pts:
                    c = _quiet("PointElm.GetCoordCartesian", sap.PointElm.GetCoordCartesian, str(p_), 0.0, 0.0, 0.0)
                    if c:
                        xyz[str(p_)] = (float(c[0]), float(c[1]), float(c[2]))
                comps = [c_ for c_ in dict.fromkeys([shell_comp, wall_comp]) if c_ in df.columns]
                elems = []
                for el, g in df.groupby("Element", sort=False):
                    first = g.drop_duplicates("PointElm")
                    nodes = [str(n) for n in first["PointElm"]]
                    if len(nodes) < 3 or any(n not in xyz for n in nodes):
                        continue
                    # value at each element corner (envelopes: the larger magnitude of max and min)
                    corner = {c_: g.groupby("PointElm", sort=False)[c_].apply(
                        lambda s_: float(s_.astype(float).loc[s_.astype(float).abs().idxmax()])) for c_ in comps}
                    elems.append({"nodes": nodes, "xyz": [xyz[n] for n in nodes],
                                  "values": {c_: [corner[c_][n] for n in first["PointElm"]] for c_ in comps}})
                out["shells"] = elems
    if "story_charts" in views and sw == "ETABS":
        df = get("story_drifts")
        if df is not None and not df.empty:
            out["drifts"] = [{"Story": s, "Series": f"drift {d_}", "Drift": float(g["Drift"].abs().max())}
                             for (s, d_), g in df.groupby(["Story", "Direction"])]
        out["shears"] = _story_shears(sap, sw, cases, combos, out["skipped"])
    return out


def _story_shears(sap: Any, sw: str, cases: list[str], combos: list[str], skipped: list) -> list[dict[str, Any]]:
    from actions import _read_selected
    from table_tools import rows_to_dataframe
    try:
        keys = [t["key"] for t in bridge.available_tables(sap)]
        if "Story Forces" not in keys:
            skipped.append(("story shear chart", "table 'Story Forces' not available"))
            return []
        fields, rows, _, _ = _read_selected(sw, sap, "Story Forces", cases, combos)
        df = rows_to_dataframe(fields, rows)
    except (CSIError, TableError) as exc:
        skipped.append(("story shear chart", str(exc)[:200]))
        return []
    if "Location" in df.columns:
        df = df[df["Location"].astype(str).str.lower() == "bottom"]
    out = []
    for s, g in df.groupby("Story"):
        for comp in ("VX", "VY"):
            if comp in g.columns:
                out.append({"Story": s, "Series": comp, "Shear": float(g[comp].astype(float).abs().max())})
    return out
