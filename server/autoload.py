"""Automatic wind and seismic loads for every code the program has (Phase 5, 0.17.0).

SAP2000 has one typed API call per code (LoadPatterns.AutoWind.SetEurocode12005_1, ...AutoSeismic.SetEurocode82004_1,
...): the parameter names and types are read from the program's type library at run time, so every code SAP2000 offers
is reachable and checked, with no per-code mapping that could drift from the program. ETABS has no wind API and no
setters for most seismic codes: it has one input table per code ('Load Pattern Definitions - Auto Wind - Eurocode1
2005', ...); the table's own field list, units and descriptions are read from the program.

Rule D1: every value comes from the designer. describe_auto_load_code lists what the code needs (SAP2000: every call
parameter; ETABS: the table's importable fields with units and descriptions) so the values can be asked for.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import Field

from actions import SoftwareP, _dry, action, bridge
from csi_bridge import CSIValidationError, api_call, as_list
from model_read import _try
from modelling import _start_edit
from objects import _EditP

KIND_TYPE = {"wind": 6, "seismic": 5}
SAP_OBJ = {"wind": "AutoWind", "seismic": "AutoSeismic"}
SAP_TABLE = {"wind": "Auto Wind - ", "seismic": "Auto Seismic - "}
ETABS_TABLE = {"wind": "Load Pattern Definitions - Auto Wind - ", "seismic": "Load Pattern Definitions - Auto Seismic - "}
# filled by the action from the load pattern / load range, not asked for as code parameters
SAP_OWN = {"name"}
SAP_RANGE = {"userz": False, "topz": 0.0, "bottomz": 0.0, "userexposure": False}
ETABS_OWN = {"Name", "IsAuto", "GUID"}
ETABS_RANGE = {"TopStory", "BotStory"}
ETABS_DIAPH = {"Set", "WidthType", "Story", "Diaphragm", "Width", "Depth", "X", "Y"}   # rows ETABS adds per diaphragm
NOT_CODES = {"exposure", "exposure_1", "userload", "none", "usercoefficient", "userload_1",
             "diaphragmeccentricityoverride", "userloadvalue"}


def norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


# --------------------------------------------------------------------------- SAP2000: type library

def _ctype_kind(t: Any) -> str:
    s = repr(t)
    if "SAFEARRAY" in s or "VARIANT" in s and "BOOL" not in s:
        return "array"
    if "BOOL" in s:
        return "bool"
    if "c_double" in s or "c_float" in s:
        return "float"
    if "c_long" in s or "c_int" in s or "c_short" in s:
        return "int"
    if "BSTR" in s or "c_wchar" in s:
        return "str"
    return "any"


def sap_methods(sap: Any, kind: str) -> dict[str, dict[str, Any]]:
    """{code id: {'set': name, 'get': name, 'params': [(name, kind, flags)], 'get_params': [...]}} - the highest
    _n form of each Set<code>."""
    obj = getattr(sap.LoadPatterns, SAP_OBJ[kind])
    specs = None
    for get in (lambda: type(obj)._methods_, lambda: type(obj)._type_._methods_):
        try:
            specs = get()
            break
        except Exception:  # noqa: BLE001
            continue
    if not specs:
        raise CSIValidationError(f"The program's {SAP_OBJ[kind]} object lists no methods.")
    sig: dict[str, list[tuple[str, str, int]]] = {}
    for sp in specs:
        name, argtypes, pflags = sp[1], sp[2], sp[3]
        params = []
        for at, pf in zip(argtypes, pflags):
            pname = pf[1] if len(pf) > 1 else ""
            if pname == "pRetVal":
                continue
            params.append((pname, _ctype_kind(at), int(pf[0])))
        sig[name] = params
    codes: dict[str, dict[str, Any]] = {}
    for name in sig:
        if not name.startswith("Set"):
            continue
        base = name[3:]
        m = re.match(r"^(.*?)(?:_(\d+))?$", base)
        code, ver = m.group(1), int(m.group(2) or 0)
        if norm(code) in NOT_CODES or norm(base) in NOT_CODES:
            continue
        cur = codes.get(code)
        if cur is None or ver > cur["ver"]:
            get = "Get" + base
            codes[code] = {"set": name, "get": get if get in sig else None, "params": sig[name],
                           "get_params": sig.get(get, []), "ver": ver}
    return codes


def resolve_code(codes: list[str], code: str) -> str:
    n = norm(code)
    exact = [c for c in codes if norm(c) == n]
    if exact:
        return exact[0]
    near = [c for c in codes if n in norm(c) or norm(c) in n]
    if len(near) == 1:
        return near[0]
    raise CSIValidationError(f"Code '{code}' is not one this program offers{' (several match: ' + str(near) + ')' if near else ''}.",
                             hint=f"Codes: {sorted(codes)}")


def _sap_table_fields(sap: Any, kind: str, code: str) -> list[dict[str, Any]]:
    """The program's own field descriptions for the code (its 'Auto Wind - <code>' table), to explain the call's
    parameters (names differ a little; matched by the caller)."""
    try:
        o = sap.DatabaseTables.GetAllTables()
        keys = [str(k) for k in as_list(o[1])]
    except Exception:  # noqa: BLE001
        return []
    pre = SAP_TABLE[kind]
    cands = [k for k in keys if k.startswith(pre) and (norm(code) in norm(k[len(pre):]) or
                                                     norm(k[len(pre):]) in norm(code))]
    return table_fields(sap, cands[0]) if cands else []


def table_fields(sap: Any, key: str) -> list[dict[str, Any]]:
    try:
        f = sap.DatabaseTables.GetAllFieldsInTable(key)
        keys, desc, units, imp = (as_list(f[2]), as_list(f[4]), as_list(f[5]), as_list(f[6]))
        return [{"field": str(keys[i]), "units": str(units[i]) or None, "importable": bool(imp[i]),
                 "description": str(desc[i])} for i in range(len(keys))]
    except Exception:  # noqa: BLE001
        return []


def etabs_tables(sap: Any, kind: str) -> dict[str, str]:
    """{code: table key} for the program's '... Auto Wind - <code>' input tables (all tables, not only the ones
    with data: a code's table is listed by GetAvailableTables only once a load uses it - measured)."""
    o = sap.DatabaseTables.GetAllTables()
    keys, imp = [str(k) for k in as_list(o[1])], as_list(o[3])
    pre = ETABS_TABLE[kind]
    out = {}
    for i, k in enumerate(keys):
        if k.startswith(pre) and int(imp[i] or 0) >= 1:
            code = k[len(pre):]
            if norm(code) in ("loadstodiaphragms", "loadstostories", "userloads", "usercoefficient",
                              "applieddiaphragmforces", "appliedstoryforces"):
                continue
            out[code] = k
    return out


# --------------------------------------------------------------------------- describe

class DescribeP(SoftwareP):
    kind: Literal["wind", "seismic"] = Field(description="wind | seismic")
    code: str | None = Field(default=None, description=(
        "a code to describe (as the program names it, e.g. Eurocode1 2005, IS 875 2015, NBCC 2015; spaces / case "
        "ignored); empty: the list of codes"))


@action("describe_auto_load_code", "List the wind or seismic codes the program offers for automatic loads, or for "
        "one code the parameters it needs (names, types, units, the program's own descriptions) - to ask the "
        "designer for them before set_auto_load.", "Definitions", "read", DescribeP, programs="ES")
def describe_auto_load_code(p: DescribeP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    if p.software == "SAP2000":
        meths = sap_methods(sap, p.kind)
        if not p.code:
            return {"kind": p.kind, "codes": sorted(meths), "count": len(meths),
                    "note": "Codes as SAP2000's API names them; describe one to see its parameters."}
        code = resolve_code(list(meths), p.code)
        m = meths[code]
        fields = _sap_table_fields(sap, p.kind, code)
        fmap = {norm(f["field"]): f for f in fields}
        params = []
        for name, kd, _ in m["params"]:
            if norm(name) in SAP_OWN:
                continue
            f = fmap.get(norm(name))
            params.append({"name": name, "type": kd, **({"units": f["units"], "description": f["description"]}
                                                       if f else {}),
                           **({"optional": f"default {SAP_RANGE[norm(name)]} (program decides)"}
                              if norm(name) in SAP_RANGE else {})})
        return {"kind": p.kind, "code": code, "api_call": f"LoadPatterns.{SAP_OBJ[p.kind]}.{m['set']}",
                "parameters": params, "program_table_fields": fields,
                "note": "Give every parameter (except the optional load range) in set_auto_load 'parameters' by "
                        "these names; integer options (terrain, site class ...) are the program's codes - the "
                        "program_table_fields describe them. Values in the units the code uses (e.g. Eurocode wind "
                        "speed in m/s, ASCE in mph); lengths in the call's units."}
    tabs = etabs_tables(sap, p.kind)
    if not p.code:
        return {"kind": p.kind, "codes": sorted(tabs), "count": len(tabs),
                "note": "Codes as ETABS' input tables name them; describe one to see its fields."}
    code = resolve_code(list(tabs), p.code)
    fields = [f for f in table_fields(sap, tabs[code]) if f["importable"]]
    return {"kind": p.kind, "code": code, "table": tabs[code],
            "parameters": [f for f in fields if f["field"] not in ETABS_OWN | ETABS_RANGE | ETABS_DIAPH],
            "load_range_fields": [f for f in fields if f["field"] in ETABS_RANGE],
            "per_diaphragm_fields": [f for f in fields if f["field"] in ETABS_DIAPH],
            "note": "Give the parameters in set_auto_load 'parameters' by these field names, with the values as the "
                    "table takes them (Yes / No, the option words the descriptions list). Fields not given take "
                    "ETABS' own value, shown in the read back."}


# --------------------------------------------------------------------------- set

class AutoLoadP(_EditP):
    load_pattern: str = Field(description="an existing load pattern of the kind's type (wind / quake)")
    kind: Literal["wind", "seismic"] = Field(description="wind | seismic")
    code: str = Field(description="the code named by the designer (describe_auto_load_code lists them)")
    parameters: dict[str, Any] = Field(description=(
        "the code's parameters by name (describe_auto_load_code), every value from the designer. Arrays (e.g. "
        "seismic direction flags) as lists."))
    top_z: float | None = Field(default=None, description="[[S: user top elevation of the load (default: program)]]")
    bottom_z: float | None = Field(default=None, description="[[S: user bottom elevation (default: program)]]")
    top_story: str | None = Field(default=None, description="[[E: top story (default: the highest story)]]")
    bottom_story: str | None = Field(default=None, description="[[E: bottom story (default: Base)]]")


def _coerce(name: str, kd: str, v: Any) -> Any:
    try:
        if kd == "bool":
            if isinstance(v, str):
                return v.strip().lower() in ("true", "yes", "1")
            return bool(v)
        if kd == "int":
            if isinstance(v, float) and not v.is_integer():
                raise ValueError
            return int(v)
        if kd == "float":
            return float(v)
        if kd == "array":
            return list(v) if isinstance(v, (list, tuple)) else [v]
        if kd == "str":
            return str(v)
    except (TypeError, ValueError):
        raise CSIValidationError(f"Parameter '{name}' must be {kd}; got {v!r}.") from None
    return v


def _placeholder(kd: str) -> Any:
    return {"bool": False, "int": 0, "float": 0.0, "str": "", "array": []}.get(kd, 0)


def _same(a: Any, b: Any) -> bool:
    if isinstance(b, bool) or isinstance(a, bool):
        return bool(a) == bool(b)
    if isinstance(b, (int, float)) and isinstance(a, (int, float)):
        return abs(float(a) - float(b)) <= 1e-6 * max(1.0, abs(float(b)))
    if isinstance(b, (list, tuple)):
        a2 = list(a) if isinstance(a, (list, tuple)) else [a]
        return len(a2) >= len(b) and all(_same(x, y) for x, y in zip(a2, b))
    return str(a).strip().lower() == str(b).strip().lower()


def _check_pattern(sap: Any, name: str, kind: str) -> None:
    pats = bridge.try_name_list(sap, ["LoadPatterns"])[0] or []
    if name not in pats:
        raise CSIValidationError(f"Load pattern '{name}' is not defined.", hint=f"Patterns: {pats[:30]}")
    t = _try(sap, "LoadPatterns.GetLoadType", name)
    if t and int(t[0]) != KIND_TYPE[kind]:
        raise CSIValidationError(f"'{name}' is not a {'wind' if kind == 'wind' else 'quake'} pattern "
                                 f"(type code {t[0]}).", hint=f"define_load_pattern with type "
                                 f"{'wind' if kind == 'wind' else 'quake'} first.")


@action("set_auto_load", "Make a wind or quake load pattern an automatic load by any code the program offers "
        "(Eurocode 1 / 8, IS 875 / 1893, NBCC, ASCE 7, BS 6399, Chinese, Italian NTC, AS/NZS 1170 ...) with the "
        "designer's parameters - describe_auto_load_code lists the codes and each code's parameters. Answers with "
        "the parameters read back from the program. (ASCE 7-16 also has the typed set_auto_wind / set_auto_seismic.)",
        "Definitions", "edit", AutoLoadP, programs="ES",
        fields={"top_z": "S", "bottom_z": "S", "top_story": "E", "bottom_story": "E"})
def set_auto_load(p: AutoLoadP) -> dict[str, Any]:
    import db_edit
    from program_specific import read_stories
    sap = bridge.sap_model(p.software)
    _check_pattern(sap, p.load_pattern, p.kind)
    given = {norm(k): (k, v) for k, v in p.parameters.items()}
    if p.software == "SAP2000":
        meths = sap_methods(sap, p.kind)
        code = resolve_code(list(meths), p.code)
        m = meths[code]
        args, missing, used = [], [], set()
        for name, kd, _ in m["params"]:
            n = norm(name)
            if n in SAP_OWN:
                args.append(p.load_pattern)
                continue
            if n in ("userz", "topz", "bottomz") and p.top_z is not None:
                val = {"userz": True, "topz": p.top_z, "bottomz": p.bottom_z or 0.0}[n]
                if n in given:
                    raise CSIValidationError(f"Give the load range by top_z / bottom_z, not by '{name}'.")
            elif n in given:
                val = given[n][1]
                used.add(n)
            elif n in SAP_RANGE:
                val = SAP_RANGE[n]
            else:
                missing.append(name)
                continue
            args.append(_coerce(name, kd, val))
        unknown = [given[n][0] for n in given if n not in used]
        if missing or unknown:
            raise CSIValidationError(
                f"{code}: " + (f"missing {missing}" if missing else "") + ("; " if missing and unknown else "")
                + (f"not parameters of this code: {unknown}" if unknown else ""),
                hint=f"Parameters of {m['set']}: {[n for n, _, _ in m['params'] if norm(n) not in SAP_OWN]} "
                     "(describe_auto_load_code explains them).")
        plan = {"load_pattern": p.load_pattern, "kind": p.kind, "code": code,
                "api_call": f"LoadPatterns.{SAP_OBJ[p.kind]}.{m['set']}",
                "parameters": {n: a for (n, _, _), a in zip(m["params"], args) if norm(n) not in SAP_OWN},
                "units": p.units}
        if p.dry_run:
            return _dry(plan, model_locked=bridge.is_locked(sap))
        with bridge.units_scope(sap, p.units):
            snap, was_locked = _start_edit(p, sap, f"before_auto_{p.kind}", f"set an automatic {p.kind} load")
            obj = getattr(sap.LoadPatterns, SAP_OBJ[p.kind])
            api_call(f"LoadPatterns.{SAP_OBJ[p.kind]}.{m['set']}", getattr(obj, m["set"]), *args)
            back: dict[str, Any] = {}
            if m["get"]:
                gp = m["get_params"][1:]
                o = _try(sap, f"LoadPatterns.{SAP_OBJ[p.kind]}.{m['get']}", p.load_pattern,
                         *[_placeholder(kd) for _, kd, _ in gp])
                if o is None:
                    o = _try(sap, f"LoadPatterns.{SAP_OBJ[p.kind]}.{m['get']}", p.load_pattern,
                             *[_placeholder(kd) for _, kd, fl in gp if fl & 1])
                if o is not None:
                    names = [n for n, _, _ in m["get_params"][1:]]
                    back = {n: (list(v) if isinstance(v, tuple) else v) for n, v in zip(names, o)}
            cfn = "GetAutoWindCode" if p.kind == "wind" else "GetAutoSeismicCode"
            c = _try(sap, f"LoadPatterns.{cfn}", p.load_pattern)
        skip = {"topz", "bottomz"} if p.top_z is None else set()     # the program works the range out
        mism = {n: {"given": plan["parameters"][n], "read_back": back.get(n)} for n in plan["parameters"]
                if n in back and norm(n) not in skip and not _same(back[n], plan["parameters"][n])}
        # a value the chosen options do not use reads back as 0 / False (e.g. Ct with a user period - measured): not
        # kept by the program, reported; any other difference is an error
        unused = {n: v for n, v in mism.items() if not v["read_back"] and v["read_back"] not in ("",)
                  and isinstance(v["read_back"], (int, float, bool))}
        real = {n: v for n, v in mism.items() if n not in unused}
        if real:
            raise CSIValidationError(f"Read back differs: {real}")
        return {**plan, "read_back": {"code": str(c[0]) if c else None, **back}, **_notes(p.software, p.kind, code),
                **({"not_kept_by_program": {n: v["given"] for n, v in unused.items()},
                    "not_kept_note": "The program keeps no value for these with the options given (they are not "
                                     "used with them)."} if unused else {}),
                "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}

    # ETABS: the code's input table
    tabs = etabs_tables(sap, p.kind)
    code = resolve_code(list(tabs), p.code)
    key = tabs[code]
    fields_info = [f for f in table_fields(sap, key) if f["importable"]]
    fnames = {norm(f["field"]): f["field"] for f in fields_info}
    unknown = [k for n, (k, _) in given.items() if n not in fnames or fnames[n] in ETABS_OWN | ETABS_RANGE]
    if unknown:
        raise CSIValidationError(f"{code}: not fields of '{key}' (or set elsewhere): {unknown}",
                                 hint="Parameters: " + str([f["field"] for f in fields_info
                                                           if f["field"] not in ETABS_OWN | ETABS_RANGE | ETABS_DIAPH])
                                 + "; the load range by top_story / bottom_story.")
    names = [s_["name"] for s_ in read_stories(sap)["stories"]]
    top = p.top_story or (names[-1] if names else "Base")
    bot = p.bottom_story or "Base"
    for s_ in (top, bot):
        if s_ != "Base" and s_ not in names:
            raise CSIValidationError(f"Story '{s_}' is not in the model.", hint=f"Stories: {names}")
    want = {fnames[n]: ("Yes" if v is True else "No" if v is False else f"{v:.10g}" if isinstance(v, float) else str(v))
            for n, (_, v) in given.items()}
    plan = {"load_pattern": p.load_pattern, "kind": p.kind, "code": code, "table": key, "parameters": want,
            "top_story": top, "bottom_story": bot, "units": p.units}
    if p.dry_run:
        return _dry(plan, model_locked=bridge.is_locked(sap))
    with bridge.units_scope(sap, p.units):
        snap, was_locked = _start_edit(p, sap, f"before_auto_{p.kind}", f"set an automatic {p.kind} load")
        version, fields, rows = db_edit.read_table(sap, key)
        rows = [r for r in rows if r.get("Name") != p.load_pattern]
        row = {"Name": p.load_pattern, "IsAuto": "No", "TopStory": top, "BotStory": bot, **want}
        rows.append({f: row.get(f, "") for f in fields})
        db_edit.write_table(sap, key, version, fields, rows)
        _, _, rows2 = db_edit.read_table(sap, key)
        mine = [r for r in rows2 if r.get("Name") == p.load_pattern]
        cfn = "GetAutoWindCode" if p.kind == "wind" else "GetAutoSeismicCode"
        c = _try(sap, f"LoadPatterns.{cfn}", p.load_pattern)
    if not mine:
        raise CSIValidationError(f"'{key}' has no row for '{p.load_pattern}' after the write.")
    head = mine[0]
    mism = {f: {"given": v, "read_back": head.get(f)} for f, v in want.items() if not _same(_num(head.get(f)), _num(v))}
    if mism:
        raise CSIValidationError(f"Read back differs: {mism}")
    back = {k: v for k, v in head.items() if k not in ("GUID",) and k not in ETABS_DIAPH and v not in ("", None)}
    program_values = {k: v for k, v in back.items() if k not in want and k not in ETABS_OWN | ETABS_RANGE}
    diaph = [{k: r.get(k) for k in ("Story", "Diaphragm", "Width", "Depth", "X", "Y")} for r in mine
             if r.get("Diaphragm")]
    return {**plan, "read_back": {"code": str(c[0]) if c else None, **back,
                                  **({"exposure_widths": diaph} if diaph else {})},
            "program_values_not_given": program_values, **_notes(p.software, p.kind, code),
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}


def _notes(software: str, kind: str, code: str) -> dict[str, str]:
    """What the program does with the load that the designer should know (measured on 27 Nov 2026,
    tests\\run_live_autoload.bat / run_live_wind.bat)."""
    if kind != "wind":
        return {}
    dist = ("SAP2000 applies the pressure at each diaphragm's elevation over its tributary height, the lowest "
            "diaphragm down to the bottom of the load" if software == "SAP2000" else
            "ETABS integrates the pressure over the height and gives each storey's load to the levels below and "
            "above as simple spans; the lowest half-storey goes to the supports")
    out = {"distribution": dist + " (measured)."}
    if "eurocode1" in norm(code):
        out["eurocode_leeward"] = (
            "Measured: SAP2000 takes the leeward pressure as qp at z_min (the bottom of the load) for every level; "
            "ETABS takes windward and leeward qp at each height z. EN 1991-1-4 7.2.2 (h <= b) uses one reference "
            "height z_e = h for the leeward face - check the result against your own calculation, or apply your "
            "own forces with set_wind_user_loads." if software in ("SAP2000", "ETABS") else "")
    return out


def _num(v: Any) -> Any:
    try:
        return float(v)
    except (TypeError, ValueError):
        return v
