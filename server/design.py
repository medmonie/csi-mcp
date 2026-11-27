"""Phase 6: the programs' own design - configure, run, read results (decision D1).

Claude never picks a design code, combinations or design values: the code is the one set in the model (read and
stated in every answer) or the one the designer names in the request; preferences (phi factors, deflection limits,
seismic design category, Sds ...) and overwrites change only when the designer gives the values. Default design
combinations are generated only when the designer asks for them.

Preferences and overwrites are edited in the program's own named tables (e.g. 'Concrete Frame Design Preferences -
ACI 318-19'), so every code's own fields are available without a per-code mapping; the field names are the
program's. Results come from the design API (summaries per member) and the program's design tables, exported to
CSV / XLSX; diagrams and charts go into export_report (section 'design').
"""
from __future__ import annotations

import contextlib
from typing import Any, Literal

from pydantic import Field, model_validator

import safety
from actions import ALLOW_UNLOCK, DRY_RUN, SNAPSHOT, SOFTWARE, UNITS, Params, _dry, _take_snapshot, action, bridge
from csi_bridge import COMError, CSIApiError, CSIError, CSIValidationError, Software, UnitPreset, api_call, as_list, \
    describe_units
from db_edit import read_table, write_table
from model_read import _names, _try

Kind = Literal["steel_frame", "concrete_frame", "concrete_slab", "shear_wall", "composite_beam"]
KIND_PROGRAMS = {"steel_frame": "ESF", "concrete_frame": "ESF", "concrete_slab": "EF", "shear_wall": "E",
                 "composite_beam": "E"}
COLL = {"steel_frame": "DesignSteel", "concrete_frame": "DesignConcrete", "concrete_slab": "DesignConcreteSlab",
        "shear_wall": "DesignShearWall", "composite_beam": "DesignCompositeBeam"}
# ETABS / SAFE: 'Steel Frame Design Preferences - ...'; SAP2000: 'Preferences - Steel Design - ...'
TABLE_WORDS = {"steel_frame": ("steel frame design", "steel design"),
               "concrete_frame": ("concrete frame design", "concrete design"),
               "concrete_slab": ("concrete slab design", "slab design"),
               "shear_wall": ("shear wall design", "wall design"), "composite_beam": ("composite beam design",)}
STEEL_RATIO = {1: "PMM", 2: "major shear", 3: "minor shear", 4: "major beam-column capacity", 5: "minor beam-column "
               "capacity", 6: "other"}
KIND_DOC = ("steel_frame | concrete_frame[[EF: | concrete_slab (strips) | composite_beam]][[E: | shear_wall "
            "(piers / spandrels)]]")


def _design(sap: Any, kind: str) -> Any:
    obj = getattr(sap, COLL[kind], None)
    if obj is None:
        raise CSIValidationError(f"{kind.replace('_', ' ')} design is not in this program's API.")
    return obj


def read_code(sap: Any, kind: str) -> str | None:
    obj = _design(sap, kind)
    if not hasattr(obj, "GetCode"):
        # slab design has no code call of its own: ETABS / SAFE design slabs with the concrete design code
        return read_code(sap, "concrete_frame") if kind == "concrete_slab" else None
    try:
        return str(api_call(f"{COLL[kind]}.GetCode", obj.GetCode)[0])
    except (CSIError, COMError, IndexError, TypeError):
        return None


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def design_tables(sap: Any, kind: str, code: str | None, what: str) -> list[dict[str, Any]]:
    """Tables of this design kind: what = preferences | overwrites | results (the rest)."""
    words = TABLE_WORDS[kind]
    out = []
    for t in bridge.available_tables(sap):
        k = t["key"].lower()
        if not any(w in k for w in words):
            continue
        is_pref, is_over = "preference" in k, "overwrite" in k
        if (what == "preferences" and not is_pref) or (what == "overwrites" and not is_over) or (
                what == "results" and (is_pref or is_over)):
            continue
        out.append(t)
    if code and what in ("preferences", "overwrites"):
        hit = [t for t in out if _norm(code) in _norm(t["key"])]
        out = hit or out
    return out


def _one_table(sap: Any, kind: str, code: str | None, what: str) -> dict[str, Any]:
    ts = design_tables(sap, kind, code, what)
    if not ts:
        raise CSIValidationError(f"No {kind.replace('_', ' ')} design {what} table is available for code {code!r}.",
                                 hint="list_available_tables searches the keys; the program shows these tables for "
                                      "the code set in the model.")
    return ts[0]


def _combos(sap: Any, kind: str) -> dict[str, list[str]]:
    obj = _design(sap, kind)
    out = {}
    for label, meth in (("strength", "GetComboStrength"), ("deflection", "GetComboDeflection")):
        if hasattr(obj, meth):
            try:
                o = api_call(f"{COLL[kind]}.{meth}", getattr(obj, meth))
                out[label] = [str(x) for x in as_list(o[1])]
            except (CSIError, COMError, IndexError, TypeError):
                pass
    return out


def read_settings(sap: Any, sw: str, kind: str, units: str) -> dict[str, Any]:
    code = read_code(sap, kind)
    out: dict[str, Any] = {"kind": kind, "code": code, "combinations": _combos(sap, kind),
                           "units": describe_units(units)}
    obj = _design(sap, kind)
    if hasattr(obj, "GetGroup"):
        with contextlib.suppress(CSIError, COMError, IndexError, TypeError):
            out["design_groups"] = [str(x) for x in as_list(api_call(f"{COLL[kind]}.GetGroup", obj.GetGroup)[1])]
    if hasattr(obj, "GetResultsAvailable"):
        with contextlib.suppress(Exception):
            out["results_available"] = bool(obj.GetResultsAvailable())
    try:
        t = _one_table(sap, kind, code, "preferences")
        with bridge.units_scope(sap, units):
            _, fields, rows = read_table(sap, t["key"])
        out["preferences_table"] = t["key"]
        out["preferences"] = rows[0] if len(rows) == 1 else rows[:200]
    except (CSIError, COMError) as exc:
        out["preferences_note"] = str(exc)[:300]
    over = design_tables(sap, kind, code, "overwrites")
    if over:
        out["overwrites_tables"] = [t["key"] for t in over]
    return out


# --------------------------------------------------------------------------- settings

class _KindP(Params):
    software: Software = SOFTWARE
    kind: Kind = Field(description=KIND_DOC)
    units: UnitPreset = UNITS


@action("get_design_settings", "The design set-up the model holds for one design kind: the code (as set in the "
        "model), design combinations, design groups, whether results exist, and the code's preferences (the "
        "program's own table and field names, e.g. phi factors, deflection limits, seismic design category). Read "
        "this before running design and state the code in answers.", "Design", "read", _KindP,
        options={"kind": {"concrete_slab": "EF", "composite_beam": "EF", "shear_wall": "E"}})
def get_design_settings(p: _KindP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    return read_settings(sap, p.software, p.kind, p.units)


class RowUpdateD(Params):
    match: dict[str, str | float | int | bool] = Field(min_length=1, description="field: value selecting the rows")
    set: dict[str, str | float | int | bool] = Field(min_length=1, description="field: new value (from the designer)")


class DesignSetP(_KindP):
    code: str | None = Field(default=None, description=(
        "design code exactly as the designer names it in the request (e.g. 'ACI 318-19', 'AISC 360-16'); never "
        "chosen by Claude. Omit to keep the model's code"))
    preferences: dict[str, str | float | int | bool] | None = Field(default=None, description=(
        "preference fields to change, {field: value} with the program's field names (get_design_settings lists "
        "them); values from the designer"))
    strength_combinations: dict[str, bool] | None = Field(default=None, description=(
        "select / deselect combinations for strength design: {combination: true | false}"))
    deflection_combinations: dict[str, bool] | None = Field(default=None, description=(
        "steel: select / deselect combinations for deflection checks"))
    auto_generate_combinations: bool | None = Field(default=None, description=(
        "[[S:SAP2000: let the program generate its default design combinations (only when the designer asks)]]"))
    design_groups: dict[str, bool] | None = Field(default=None, description="steel: groups selected for design")
    overwrites: list[RowUpdateD] | None = Field(default=None, max_length=2000, description=(
        "member overwrites in the code's overwrites table: rows matched by fields (e.g. {'UniqueName': '12'}) get "
        "the values given (from the designer)"))
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT

    @model_validator(mode="after")
    def _some(self) -> "DesignSetP":
        if not any(v is not None for v in (self.code, self.preferences, self.strength_combinations,
                                           self.deflection_combinations, self.auto_generate_combinations,
                                           self.design_groups, self.overwrites)):
            raise ValueError("give code, preferences, strength_combinations, deflection_combinations, "
                             "auto_generate_combinations, design_groups and / or overwrites")
        return self


def _same(a: Any, b: Any) -> bool:
    from tables_edit import same
    return same(a, b)


@action("set_design_settings", "Configure design with values the designer gives: the code (named in the request), "
        "preferences (program field names), strength / deflection combinations, design groups, member overwrites"
        "[[S:, or the program's default combinations when asked]]. Changes are checked by reading them back; "
        "design results are cleared by the program.", "Design", "edit", DesignSetP,
        options={"kind": {"concrete_slab": "EF", "composite_beam": "EF", "shear_wall": "E"}},
        fields={"auto_generate_combinations": "S"})
def set_design_settings(p: DesignSetP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    obj = _design(sap, p.kind)
    before = read_settings(sap, p.software, p.kind, p.units)
    combos_all = set(bridge.try_name_list(sap, ["RespCombo"])[0] or [])
    for label, sel in (("strength_combinations", p.strength_combinations),
                       ("deflection_combinations", p.deflection_combinations)):
        unknown = sorted(set(sel or {}) - combos_all)
        if unknown:
            raise CSIValidationError(f"{label}: not combinations in the model: {unknown}",
                                     hint=f"Combinations: {sorted(combos_all)[:40]}")
    if p.deflection_combinations and not hasattr(obj, "SetComboDeflection"):
        raise CSIValidationError("Deflection combinations are a steel-frame setting here.")
    pref_key = before.get("preferences_table")
    if p.preferences:
        if not pref_key:
            raise CSIValidationError("No preferences table for this design kind / code.",
                                     hint=before.get("preferences_note", ""))
        fields = list(before["preferences"]) if isinstance(before["preferences"], dict) else \
            list(before["preferences"][0]) if before["preferences"] else []
        bad = sorted(set(p.preferences) - set(fields))
        if bad:
            raise CSIValidationError(f"'{pref_key}' has no fields {bad}.", hint=f"Fields: {fields}")
    plan = {"kind": p.kind, "code_before": before["code"], "code_wanted": p.code,
            "preferences": p.preferences, "strength_combinations": p.strength_combinations,
            "deflection_combinations": p.deflection_combinations, "design_groups": p.design_groups,
            "overwrites": len(p.overwrites or []), "auto_generate_combinations": p.auto_generate_combinations}
    if p.dry_run:
        return _dry(plan, preferences_table=pref_key)
    snap = _take_snapshot(p.software, sap, "before_design_settings") if p.snapshot else None
    done: dict[str, Any] = {}
    if p.code is not None and p.code != before["code"]:
        try:
            api_call(f"{COLL[p.kind]}.SetCode", obj.SetCode, p.code)
        except CSIApiError as exc:
            raise CSIValidationError(f"The program refused code '{p.code}'.",
                                     hint="Use the code name exactly as the program lists it (its Design > "
                                          "Preferences dialog), e.g. 'ACI 318-19'.") from exc
        now = read_code(sap, p.kind)
        if not now or _norm(now) != _norm(p.code):
            if before["code"]:
                with contextlib.suppress(Exception):
                    obj.SetCode(before["code"])
            raise CSIValidationError(f"After SetCode('{p.code}') the model reports code '{now}'; the previous code "
                                     "was put back.", hint="Give the code name exactly as the program lists it.")
        done["code"] = now
    if p.auto_generate_combinations is not None:
        api_call(f"{COLL[p.kind]}.SetComboAutoGenerate", obj.SetComboAutoGenerate, p.auto_generate_combinations)
    for meth, sel in (("SetComboStrength", p.strength_combinations), ("SetComboDeflection", p.deflection_combinations),
                      ("SetGroup", p.design_groups)):
        for name, on in (sel or {}).items():
            api_call(f"{COLL[p.kind]}.{meth}('{name}')", getattr(obj, meth), name, bool(on))
    with bridge.units_scope(sap, p.units):
        if p.preferences:
            key = read_settings(sap, p.software, p.kind, p.units).get("preferences_table") or pref_key
            version, fields, rows = read_table(sap, key)
            if len(rows) != 1:
                raise CSIValidationError(f"'{key}' has {len(rows)} rows; give the change as overwrites-style "
                                         "match / set rows instead.")
            from tables_edit import _txt
            row = {**rows[0], **{k: _txt(v) for k, v in p.preferences.items()}}
            done["preferences_import"] = write_table(sap, key, version, fields, [row])
            _, _, back = read_table(sap, key)
            wrong = {k: back[0].get(k) for k, v in p.preferences.items() if not _same(back[0].get(k), _txt(v))}
            if wrong:
                raise CSIValidationError(f"Preferences read back differently: {wrong}.",
                                         hint="The program may limit or recode these values; see the table.")
        if p.overwrites:
            code = read_code(sap, p.kind)
            ts = design_tables(sap, p.kind, code, "overwrites")
            if not ts:
                raise CSIValidationError("No overwrites table for this design kind / code.")
            key = ts[0]["key"]
            version, fields, rows = read_table(sap, key)
            from tables_edit import _matches, _txt
            changed = 0
            for u in p.overwrites:
                bad = sorted((set(u.match) | set(u.set)) - set(fields))
                if bad:
                    raise CSIValidationError(f"'{key}' has no fields {bad}.", hint=f"Fields: {fields}")
                hit = [r for r in rows if _matches(r, u.match)]
                if not hit:
                    raise CSIValidationError(f"overwrites {u.match}: no row matches in '{key}'.")
                for r in hit:
                    r.update({k: _txt(v) for k, v in u.set.items()})
                    changed += 1
            done["overwrites_import"] = {**write_table(sap, key, version, fields, rows), "rows_changed": changed,
                                         "table": key}
    after = read_settings(sap, p.software, p.kind, p.units)
    for label, sel in (("strength", p.strength_combinations), ("deflection", p.deflection_combinations)):
        for name, on in (sel or {}).items():
            if (name in after["combinations"].get(label, [])) != bool(on):
                raise CSIValidationError(f"{label} combination '{name}' reads back as "
                                         f"{'selected' if not on else 'not selected'}.")
    return {**plan, "done": done, "settings_now": after, **({"snapshot": snap} if snap else {})}


# --------------------------------------------------------------------------- run

class RunDesignP(_KindP):
    run_analysis_first: bool = Field(default=False, description=(
        "run the analysis first when the model has no results (saves the model)"))
    program_default_combinations: bool = Field(default=False, description=(
        "frames with no strength combinations selected: true only when the designer accepts the program's own "
        "code-generated default design combinations (as the program does when design is run with none selected)"))
    dry_run: bool = DRY_RUN


@action("run_design", "Run the program's design for one kind (code and combinations as set in the model - stated "
        "in the answer). Needs analysis results (run_analysis_first: true runs it). Answers with members designed, "
        "failing members and sections that differ from the analysis sections.", "Design", "edit", RunDesignP,
        long_running=True, options={"kind": {"concrete_slab": "EF", "composite_beam": "EF", "shear_wall": "E"}})
def run_design(p: RunDesignP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    obj = _design(sap, p.kind)
    settings = read_settings(sap, p.software, p.kind, p.units)
    if not settings["code"] and p.kind != "concrete_slab":
        raise CSIValidationError("No design code is set in the model for this design kind.",
                                 hint="Ask the designer which code to use, then set_design_settings code.")
    no_combos = p.kind in ("steel_frame", "concrete_frame") and not settings["combinations"].get("strength")
    if no_combos and not p.program_default_combinations:
        raise CSIValidationError("No strength design combinations are selected.",
                                 hint="Ask the designer which combinations to design for (set_design_settings "
                                      "strength_combinations), or whether the program's default design "
                                      "combinations should be used (program_default_combinations: true).")
    locked = bool(bridge.is_locked(sap))
    plan = {"kind": p.kind, "code": settings["code"], "combinations": settings["combinations"],
            "analysis_results": locked, "will_run_analysis": not locked and p.run_analysis_first,
            **({"uses_program_default_combinations": True} if no_combos else {})}
    if p.dry_run:
        return _dry(plan)
    if not locked:
        if not p.run_analysis_first:
            raise CSIValidationError("The model has no analysis results.",
                                     hint="Run the analysis first (run_analysis), or pass run_analysis_first: true.")
        bridge.save_model(p.software)
        api_call("Analyze.RunAnalysis", sap.Analyze.RunAnalysis)
    meth = "StartSlabDesign" if p.kind == "concrete_slab" else "StartDesign"
    if p.kind == "shear_wall":
        meth = "StartDesign" if hasattr(obj, "StartDesign") else meth
    if not hasattr(obj, meth):
        raise CSIValidationError(f"{COLL[p.kind]}.{meth} is not in this program's API.")
    import time
    t0 = time.perf_counter()
    api_call(f"{COLL[p.kind]}.{meth}", getattr(obj, meth))
    out: dict[str, Any] = {**plan, "runtime_seconds": round(time.perf_counter() - t0, 2)}
    with contextlib.suppress(Exception):
        used = _combos(sap, p.kind)
        if used != settings["combinations"]:
            out["combinations_after_design"] = used
    if hasattr(obj, "VerifyPassed"):
        with contextlib.suppress(CSIError, COMError, IndexError, TypeError):
            v = api_call(f"{COLL[p.kind]}.VerifyPassed", obj.VerifyPassed)
            # VerifyPassed: items that did not pass or were not checked, of them failing, not checked, names
            out.update(not_passed_or_not_checked=int(v[0]), failing=int(v[1]), not_checked=int(v[2]),
                       failing_members=[str(x) for x in as_list(v[3])][:200])
    if hasattr(obj, "VerifySections"):
        with contextlib.suppress(CSIError, COMError, IndexError, TypeError):
            v = api_call(f"{COLL[p.kind]}.VerifySections", obj.VerifySections)
            out["design_section_differs_from_analysis"] = [str(x) for x in as_list(v[1])][:200]
    out["next"] = "get_design_results for the member results; export_report sections ['design'] for tables, " \
                  "charts and D/C diagrams."
    return out


# --------------------------------------------------------------------------- results

def _arr(o: Any, i: int) -> list:
    return list(as_list(o[i])) if o is not None and len(o) > i else []


def _targets_calls(names: list[str] | None, group: str | None) -> list[tuple[str, int]]:
    """(name, ItemType) calls: one group call (ItemType 1) for a group or all frames, else per object."""
    if names is None:
        return [(group or "All", 1)]
    return [(n, 0) for n in names]


def steel_results(sap: Any, calls: list[tuple[str, int]]) -> list[dict[str, Any]]:
    rows = []
    for n, it in calls:
        o = _try(sap, "DesignSteel.GetSummaryResults", n, 0, [], [], [], [], [], [], [], it)
        if not o or not o[0]:
            continue
        for i in range(int(o[0])):
            rows.append({"Frame": str(_arr(o, 1)[i]), "Ratio": float(_arr(o, 2)[i]),
                         "RatioType": STEEL_RATIO.get(int(_arr(o, 3)[i]), str(_arr(o, 3)[i])),
                         "Location": float(_arr(o, 4)[i]), "Combo": str(_arr(o, 5)[i]),
                         "Error": str(_arr(o, 6)[i] or ""), "Warning": str(_arr(o, 7)[i] or "")})
    for r in rows:
        ds = _try(sap, "DesignSteel.GetDesignSection", r["Frame"])
        if ds:
            r["DesignSection"] = str(ds[0])
    return rows


def concrete_results(sap: Any, calls: list[tuple[str, int]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    beams, cols = [], []
    for n, it in calls:
        o = _try(sap, "DesignConcrete.GetSummaryResultsColumn", n, 0, [], [], [], [], [], [], [], [], [], [], [], [],
                 it)
        if o and o[0]:
            for i in range(int(o[0])):
                cols.append({"Frame": str(_arr(o, 1)[i]), "Option": {1: "design", 2: "check"}.get(
                    int(_arr(o, 2)[i]), str(_arr(o, 2)[i])), "Location": float(_arr(o, 3)[i]),
                    "PMMCombo": str(_arr(o, 4)[i]), "PMMArea": float(_arr(o, 5)[i]),
                    "PMMRatio": float(_arr(o, 6)[i]), "VMajorCombo": str(_arr(o, 7)[i]),
                    "AvMajor": float(_arr(o, 8)[i]), "VMinorCombo": str(_arr(o, 9)[i]),
                    "AvMinor": float(_arr(o, 10)[i]), "Error": str(_arr(o, 11)[i] or ""),
                    "Warning": str(_arr(o, 12)[i] or "")})
            if it == 0:
                continue
        o = _try(sap, "DesignConcrete.GetSummaryResultsBeam", n, 0, [], [], [], [], [], [], [], [], [], [], [], [],
                 [], [], it)
        if o and o[0]:
            for i in range(int(o[0])):
                beams.append({"Frame": str(_arr(o, 1)[i]), "Location": float(_arr(o, 2)[i]),
                              "TopCombo": str(_arr(o, 3)[i]), "TopArea": float(_arr(o, 4)[i]),
                              "BotCombo": str(_arr(o, 5)[i]), "BotArea": float(_arr(o, 6)[i]),
                              "VMajorCombo": str(_arr(o, 7)[i]), "VMajorArea": float(_arr(o, 8)[i]),
                              "TLCombo": str(_arr(o, 9)[i]), "TLArea": float(_arr(o, 10)[i]),
                              "TTCombo": str(_arr(o, 11)[i]), "TTArea": float(_arr(o, 12)[i]),
                              "Error": str(_arr(o, 13)[i] or ""), "Warning": str(_arr(o, 14)[i] or "")})
    return beams, cols


def frame_ratios(kind: str, rows: dict[str, list[dict[str, Any]]]) -> dict[str, float]:
    """Governing D/C per frame (steel: max ratio; concrete columns: max PMM ratio in check mode)."""
    out: dict[str, float] = {}
    if kind == "steel_frame":
        for r in rows.get("steel", []):
            out[r["Frame"]] = max(out.get(r["Frame"], 0.0), r["Ratio"])
    else:
        for r in rows.get("columns", []):
            if r["PMMRatio"] > 0:
                out[r["Frame"]] = max(out.get(r["Frame"], 0.0), r["PMMRatio"])
    return out


class ResultsP(_KindP):
    names: list[str] | None = Field(default=None, max_length=5000, description="frames; default all frames")
    group: str | None = Field(default=None, description="or a group's frames")
    tables: bool = Field(default=False, description="also list the program's design result tables available now")
    export: Literal["none", "csv", "xlsx"] = Field(default="none", description="write the full results to a file")
    max_rows: int = Field(default=200, ge=1, le=5000, description="rows returned per list")


def punching_results(sap: Any, sw: str) -> list[dict[str, Any]]:
    """Punching shear checks from the program's own design table (ETABS / SAFE: no API getter)."""
    try:
        keys = [t["key"] for t in bridge.available_tables(sap)
                if "punching" in t["key"].lower() and "overwrite" not in t["key"].lower()]
    except Exception:  # noqa: BLE001
        return []
    out: list[dict[str, Any]] = []
    for key in keys[:2]:
        with contextlib.suppress(Exception):
            fields, rows, _ = bridge.read_table(sap, sw, key)
            for r in rows[:2000]:
                rec: dict[str, Any] = {"table": key}
                for f, v in zip(fields, r):
                    try:
                        rec[f] = float(v)
                    except (TypeError, ValueError):
                        rec[f] = v
                out.append(rec)
    return out


def gather_results(sap: Any, sw: str, kind: str, names: list[str] | None, group: str | None,
                   units: str) -> dict[str, Any]:
    if names:
        missing = sorted(set(names) - set(_names(sap, "FrameObj")))
        if missing:
            raise CSIValidationError(f"Not frames in the model: {missing[:20]}.")
    if group:
        groups = bridge.name_list(sap, "GroupDef")
        if group not in groups:
            raise CSIValidationError(f"Group '{group}' is not defined.", hint=f"Groups: {groups[:30]}")
    calls = _targets_calls(names or None, group)
    out: dict[str, Any] = {"kind": kind, "code": read_code(sap, kind), "units": describe_units(units),
                           "combinations": _combos(sap, kind) if kind in ("steel_frame", "concrete_frame") else {}}
    with bridge.units_scope(sap, units):
        if kind == "steel_frame":
            out["steel"] = steel_results(sap, calls)
        elif kind == "concrete_frame":
            out["beams"], out["columns"] = concrete_results(sap, calls)
        elif kind == "concrete_slab":
            o = _try(sap, "DesignConcreteSlab.GetSummaryResultsFlexureAndShear", *([[]] * 15))
            keys = ("Story", "Strip", "Span", "Location", "FTopCombo", "FTopMoment", "FTopArea", "FBotCombo",
                    "FBotMoment", "FBotArea", "VCombo", "VForce", "VArea", "Status", "Layer")
            out["strips"] = [dict(zip(keys, vals)) for vals in zip(*(as_list(o[i]) for i in range(15)))] if o else []
            out["punching"] = punching_results(sap, sw)
        elif kind == "shear_wall":
            o = _try(sap, "DesignShearWall.GetPierSummaryResults", *([[]] * 39))
            keys = ("Story", "Pier", "Station", "DesignType", "PierSecType", "EdgeBar", "EndBar", "BarSpacing",
                    "ReinfPercent", "CurrPercent", "DCRatio")
            out["piers"] = [dict(zip(keys, vals)) for vals in zip(*(as_list(o[i]) for i in range(11)))] if o else []
    return out


@action("get_design_results", "Design results per member from the program's design (run_design first): steel D/C "
        "ratios with type, location, governing combination, design section, errors / warnings; concrete beam "
        "rebar areas (top, bottom, shear, torsion) and column PMM area or D/C ratio and shear rebar[[EF:; slab "
        "strip flexure and shear]][[E:; shear wall piers]]. States the code, combinations and units; exports to "
        "CSV / XLSX.", "Design", "read", ResultsP,
        options={"kind": {"concrete_slab": "EF", "composite_beam": "EF", "shear_wall": "E"}})
def get_design_results(p: ResultsP) -> dict[str, Any]:
    import pandas as pd
    sap = bridge.sap_model(p.software)
    obj = _design(sap, p.kind)
    if hasattr(obj, "GetResultsAvailable"):
        try:
            ok = bool(obj.GetResultsAvailable())
        except Exception:  # noqa: BLE001
            ok = True
        if not ok:
            raise CSIValidationError("No design results: run_design first (after the analysis).")
    data = gather_results(sap, p.software, p.kind, p.names, p.group, p.units)
    lists = [k for k in ("steel", "beams", "columns", "strips", "piers") if k in data]
    out: dict[str, Any] = {k: v for k, v in data.items() if k not in lists}
    files = []
    for k in lists:
        rows = data[k]
        out[f"{k}_count"] = len(rows)
        out[k] = rows[:p.max_rows]
        if len(rows) > p.max_rows:
            out[f"{k}_truncated"] = True
        if p.export != "none" and rows:
            files.append(str(safety.export_frame(pd.DataFrame(rows), f"{p.software}_{p.kind}_{k}", p.export)))
    ratios = frame_ratios(p.kind, data)
    if ratios:
        top = sorted(ratios.items(), key=lambda kv: -kv[1])
        out["governing"] = [{"frame": f, "ratio": round(r, 4)} for f, r in top[:20]]
        out["over_1"] = [f for f, r in top if r > 1.0][:200]
    if files:
        out["export_paths"] = files
    if p.tables:
        out["result_tables"] = [t["key"] for t in design_tables(sap, p.kind, data["code"], "results")]
    return out
