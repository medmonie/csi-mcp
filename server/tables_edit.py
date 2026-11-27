"""Phase 4: generic database-table editing (tier 2 of the roadmap: Get -> diff preview -> Set -> Apply -> import log).

One action, edit_database_table:
  mode read  - the table as it is edited (editing field keys, which differ from the display names), with filters.
  mode edit  - add rows, upsert rows by key fields, update rows matched by field values, delete matched rows, or
               rows from a CSV / XLSX file. dry_run (default true for this action's callers to see) answers with the
               diff: rows added / changed (field old -> new) / deleted. Applying reads the table, keeps every row it
               was not asked to change (SetTableForEditingArray replaces the whole table), applies with the import
               log, cancels the pending edit when the program refuses, and reads the table back to verify that every
               field it was given now holds the value asked for.
Results tables (import type 0) are refused; import type 2 tables need an unlocked model (allow_unlock).
"""
from __future__ import annotations

import math
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, model_validator

from actions import (
    ALLOW_UNLOCK, DRY_RUN, SNAPSHOT, SOFTWARE, UNITS, ActionFailed, Params, _dry, _lock, _refresh_view, _take_snapshot, action, bridge,
)
from csi_bridge import CSIValidationError, Software, UnitPreset, describe_units
from db_edit import read_table, write_table
from safety import check_input_file

Value = str | float | int | bool
PREVIEW = 20


def _txt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "Yes" if v else "No"
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        return repr(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)
    return str(v)


def _num(s: str) -> float | None:
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def same(a: Any, b: Any) -> bool:
    """Table values equal: numerically (relative 1e-9) when both are numbers, else text (case-insensitive)."""
    sa, sb = _txt(a).strip(), _txt(b).strip()
    na, nb = _num(sa), _num(sb)
    if na is not None and nb is not None:
        return abs(na - nb) <= 1e-9 * max(1.0, abs(na), abs(nb))
    return sa.lower() == sb.lower()


def _matches(row: dict[str, str], match: dict[str, Any]) -> bool:
    return all(_key(row.get(k, "")) == _key(v) for k, v in match.items())


def _key(value: Any):
    text = _txt(value).strip().casefold()
    # A string label such as 001 must remain distinct from a numeric ID.
    digits = text.lstrip("+-")
    if len(digits) > 1 and digits[0] == "0" and digits[1].isdigit():
        return text
    try:
        number = Decimal(text)
        return number if number.is_finite() else text
    except InvalidOperation:
        return text


class RowUpdate(Params):
    match: dict[str, Value] = Field(min_length=1, description="field: value - the rows to change (all must match)")
    set: dict[str, Value] = Field(min_length=1, description="field: new value")
    expect: int | None = Field(default=None, ge=0, description="rows that must match (default: at least one)")


class RowDelete(Params):
    match: dict[str, Value] = Field(min_length=1, description="field: value - the rows to delete")
    expect: int | None = Field(default=None, ge=0, description="rows that must match (default: at least one)")


class TableEditP(Params):
    software: Software = SOFTWARE
    table_key: str = Field(description="exact table key (list_available_tables searches them)")
    mode: Literal["read", "edit"] = Field(default="read", description=(
        "read: the table with its editing field keys | edit: change it (dry_run first shows the diff)"))
    where: dict[str, Value | list[Value]] | None = Field(default=None, description=(
        "read: only rows whose fields equal these values (a list = any of them)"))
    max_rows: int = Field(default=200, ge=1, le=5000, description="read: rows returned")
    add_rows: list[dict[str, Value]] = Field(default=[], max_length=20000, description=(
        "edit: new rows {field: value}; fields left out stay blank (the program's default)"))
    upsert_rows: list[dict[str, Value]] = Field(default=[], max_length=20000, description=(
        "edit: rows matched on key_fields are changed (only the fields given), the others added"))
    key_fields: list[str] = Field(default=[], description="edit: the fields that identify a row for upsert_rows / "
                                                          "rows_file (e.g. ['Name'] or ['UniqueName'])")
    update: list[RowUpdate] = Field(default=[], max_length=5000, description="edit: change matched rows")
    delete: list[RowDelete] = Field(default=[], max_length=5000, description="edit: delete matched rows")
    rows_file: str | None = Field(default=None, description=(
        "edit: CSV or XLSX whose header row holds field keys; its rows are upserted on key_fields (added when "
        "key_fields is empty)"))
    sheet: str | None = Field(default=None, description="rows_file XLSX: the sheet (default the first)")
    allow_empty: bool = Field(default=False, description="edit: allow an edit that leaves a non-empty table empty")
    units: UnitPreset = UNITS
    allow_unlock: bool = ALLOW_UNLOCK
    dry_run: bool = DRY_RUN
    snapshot: bool = SNAPSHOT

    @model_validator(mode="after")
    def _check(self) -> "TableEditP":
        if self.mode == "edit" and not (self.add_rows or self.upsert_rows or self.update or self.delete
                                        or self.rows_file):
            raise ValueError("edit: give add_rows, upsert_rows, update, delete and / or rows_file")
        if self.upsert_rows and not self.key_fields:
            raise ValueError("upsert_rows needs key_fields")
        return self


def _file_rows(path: str, sheet: str | None) -> list[dict[str, Value]]:
    import pandas as pd
    f = Path(path)
    if not f.is_file():
        raise CSIValidationError(f"rows_file not found: {path}")
    check_input_file(f)
    if f.suffix.lower() == ".csv":
        df = pd.read_csv(f, dtype=str, keep_default_na=False, nrows=5001)
    elif f.suffix.lower() in (".xlsx", ".xlsm"):
        df = pd.read_excel(f, sheet_name=sheet or 0, dtype=str, keep_default_na=False, nrows=5001)
    else:
        raise CSIValidationError("rows_file must be .csv or .xlsx.")
    if len(df) > 5000:
        raise CSIValidationError("A table edit accepts at most 5000 imported rows per file.")
    df.columns = [str(c).strip() for c in df.columns]
    return [{k: v for k, v in r.items() if k and not k.startswith("Unnamed")} for r in df.to_dict("records")]


def _resolve(sap: Any, key: str) -> dict[str, Any]:
    tables = bridge.available_tables(sap)
    hit = next((t for t in tables if t["key"] == key), None) or next(
        (t for t in tables if t["key"].lower() == key.lower()), None)
    if hit is None:
        close = [t["key"] for t in tables if all(w in t["key"].lower() for w in key.lower().split()[:2])][:10]
        raise CSIValidationError(f"Table '{key}' is not available now.", hint=f"Close keys: {close}. "
                                 "list_available_tables searches the keys.")
    return hit


def _plan(fields: list[str], rows: list[dict[str, str]], p: TableEditP) -> dict[str, Any]:
    """The edited table and its diff, without touching the model."""
    def check_fields(names: Any, where: str) -> None:
        bad = sorted(set(names) - set(fields))
        if bad:
            raise CSIValidationError(f"{where}: the table has no fields {bad}.", hint=f"Fields: {fields}")

    work = [dict(r) for r in rows]
    status = ["same"] * len(work)        # same | changed | deleted
    before = [dict(r) for r in rows]
    for d in p.delete:
        check_fields(d.match, "delete")
        hit = [i for i, r in enumerate(work) if status[i] != "deleted" and _matches(r, d.match)]
        if (d.expect is None and not hit) or (d.expect is not None and len(hit) != d.expect):
            raise CSIValidationError(f"delete {d.match}: {len(hit)} rows match"
                                     + (f", expected {d.expect}" if d.expect is not None else "") + ".")
        for i in hit:
            status[i] = "deleted"
    for u in p.update:
        check_fields(u.match, "update match")
        check_fields(u.set, "update set")
        hit = [i for i, r in enumerate(work) if status[i] != "deleted" and _matches(r, u.match)]
        if (u.expect is None and not hit) or (u.expect is not None and len(hit) != u.expect):
            raise CSIValidationError(f"update {u.match}: {len(hit)} rows match"
                                     + (f", expected {u.expect}" if u.expect is not None else "") + ".")
        for i in hit:
            work[i].update({k: _txt(v) for k, v in u.set.items() if _key(work[i].get(k, "")) != _key(v)})
            if work[i] != before[i]:
                status[i] = "changed"
    upserts = list(p.upsert_rows)
    adds = list(p.add_rows)
    if p.rows_file:
        frows = _file_rows(p.rows_file, p.sheet)
        (upserts if p.key_fields else adds).extend(frows)
    if p.key_fields:
        check_fields(p.key_fields, "key_fields")
    seen_keys = set()
    added: list[dict[str, str]] = []
    for r in upserts:
        check_fields(r, "upsert row")
        missing = [k for k in p.key_fields if k not in r]
        if missing:
            raise CSIValidationError(f"upsert row {r}: key fields {missing} missing.")
        key = {k: r[k] for k in p.key_fields}
        ident = tuple(_key(v) for v in key.values())
        if ident in seen_keys:
            raise CSIValidationError("Duplicate upsert key in this request.")
        seen_keys.add(ident)
        hit = [i for i, w in enumerate(work) if status[i] != "deleted" and _matches(w, key)]
        if len(hit) > 1:
            raise CSIValidationError(f"upsert {key}: {len(hit)} rows match; key_fields must identify one row.")
        if hit:
            i = hit[0]
            work[i].update({k: _txt(v) for k, v in r.items() if _key(work[i].get(k, "")) != _key(v)})
            if work[i] != before[i]:
                status[i] = "changed"
        else:
            added.append({f: _txt(r.get(f)) for f in fields})
    for r in adds:
        check_fields(r, "added row")
        added.append({f: _txt(r.get(f)) for f in fields})
    out = [w for w, s in zip(work, status) if s != "deleted"] + added
    if rows and not out and not p.allow_empty:
        raise CSIValidationError("This edit would leave the table empty.", hint="Pass allow_empty: true if meant.")
    changed = [{"row": i, **{k: f"{before[i].get(k, '')} -> {work[i][k]}" for k in fields
                             if work[i].get(k, "") != before[i].get(k, "")}}
               for i, s in enumerate(status) if s == "changed"]
    deleted = [before[i] for i, s in enumerate(status) if s == "deleted"]
    expect = [work[i] for i, s in enumerate(status) if s == "changed"]
    expect += [{k: v for k, v in r.items() if v != ""} for r in added]
    return {"rows": out, "retained": [w for w,s in zip(work, status) if s == "same"],
            "changed": changed, "deleted": deleted, "added": added, "expect": expect,
            "rows_before": len(rows), "rows_after": len(out)}


# Tables whose first field names objects / definitions the API can list: before a write, every name the program
# lists must be in the table. SAP2000 27.1 (measured 26 Nov): 'Load Pattern Definitions' kept showing only DEAD after
# LoadPatterns.Add('DL') - before and after saving - while writing the table back would delete what it does not hold.
NAME_SOURCES = {
    "load pattern definitions": "LoadPatterns", "material properties - general": "PropMaterial",
    "material list 1 - by object type": None, "joint coordinates": "PointObj", "point object connectivity": "PointObj",
    "connectivity - frame": "FrameObj", "frame assignments - summary": "FrameObj", "connectivity - area": "AreaObj",
    "area assignments - summary": "AreaObj", "groups 1 - definitions": "GroupDef", "group definitions": "GroupDef",
    "load combination definitions": "RespCombo", "combination definitions": "RespCombo",
    "frame section properties 01 - general": "PropFrame", "frame sections": "PropFrame",
    "area section properties": "PropArea", "load case definitions": "LoadCases",
}


def _stale(sap: Any, key: str, fields: list[str], rows: list[dict[str, str]]) -> list[str]:
    """Names the program lists that the table does not hold (empty when the table is current or not checkable)."""
    coll = NAME_SOURCES.get(key.lower())
    if not coll or not fields:
        return []
    api = bridge.name_list(sap, coll)
    held = {str(r.get(fields[0], "")).strip().lower() for r in rows}
    return [n for n in api if n.strip().lower() not in held and not n.startswith("~")]


def _verify(after: list[dict[str, str]], expect: list[dict[str, str]], retained: list[dict[str, str]],
            fields: list[str]) -> dict[str, Any]:
    """Every changed / added row should be found in the table read back, on the fields that were given."""
    def signature(row):
        return tuple(_key(row.get(k, "")) for k in fields)
    wanted = Counter(signature(r) for r in retained)
    pool = []
    for row in after:
        sig = signature(row)
        if wanted[sig]:
            wanted[sig] -= 1
        else:
            pool.append(row)
    missing = []
    for e in expect:
        i = next((j for j, r in enumerate(pool) if _matches(r, e)), None)
        if i is None:
            missing.append(e)
        else:
            pool.pop(i)
    return {"rows_checked": len(expect), "rows_verified": len(expect) - len(missing),
            **({"not_found_after_apply": missing[:10]} if missing else {}),
            **({"missing_retained_rows": sum(wanted.values())} if any(wanted.values()) else {}),
            **({"unexpected_rows": len(pool)} if pool else {})}


@action("edit_database_table", "Read or edit any importable database table - the bulk route for definitions and "
        "assignments the other actions do not cover: add / upsert / update / delete rows or load them from CSV / "
        "XLSX. dry_run answers with the diff (rows added, changed field old -> new, deleted); applying keeps every "
        "other row, reports the import log, cancels on errors and verifies the values read back.", "Bulk editing",
        "edit", TableEditP)
def edit_database_table(p: TableEditP) -> dict[str, Any]:
    sap = bridge.sap_model(p.software)
    t = _resolve(sap, p.table_key)
    key, itype = t["key"], t["import_type"]
    with bridge.units_scope(sap, p.units):
        version, fields, rows = read_table(sap, key)
    head = {"table_key": key, "import_type": itype, "units": describe_units(p.units), "fields": fields}
    if p.mode == "read":
        sel = rows
        if p.where:
            bad = sorted(set(p.where) - set(fields))
            if bad:
                raise CSIValidationError(f"where: the table has no fields {bad}.", hint=f"Fields: {fields}")
            sel = [r for r in rows if all(any(same(r.get(k, ""), x) for x in (v if isinstance(v, list) else [v]))
                                          for k, v in p.where.items())]
        return {**head, "total_rows": len(rows), "matching_rows": len(sel), "returned_rows": min(len(sel), p.max_rows),
                "truncated": len(sel) > p.max_rows, "rows": sel[:p.max_rows]}
    if itype not in (1, 2, 3):
        raise CSIValidationError(f"'{key}' is a results table (not importable).")
    missing = _stale(sap, key, fields, rows)
    if missing:
        raise CSIValidationError(
            f"The program's '{key}' table does not hold {len(missing)} of the model's items ({missing[:10]}); writing "
            "it back would delete them, so nothing was changed.",
            hint="The program has not refreshed this table since those items were added (measured in SAP2000 27.1). "
                 "Use the specific action instead (e.g. define_load_pattern with replace: true), or reopen the "
                 "model and try again.")
    pl = _plan(fields, rows, p)
    diff = {"rows_before": pl["rows_before"], "rows_after": pl["rows_after"], "rows_added": len(pl["added"]),
            "rows_changed": len(pl["changed"]), "rows_deleted": len(pl["deleted"]),
            "added_preview": pl["added"][:PREVIEW], "changed_preview": pl["changed"][:PREVIEW],
            "deleted_preview": pl["deleted"][:PREVIEW]}
    if not (pl["added"] or pl["changed"] or pl["deleted"]):
        return {**head, **diff, "applied": False, "note": "Nothing to change: the table already holds these values."}
    locked = bridge.is_locked(sap)
    if p.dry_run:
        return {**head, **_dry(diff, model_locked=locked,
                               needs_allow_unlock=bool(locked and itype == 2 and not p.allow_unlock))}
    if locked and itype == 2 and not p.allow_unlock:
        raise CSIValidationError(f"'{key}' can be imported only into an unlocked model; the model is locked "
                                 "(analysed).", hint="Pass allow_unlock: true (analysis results are deleted).")
    snap = _take_snapshot(p.software, sap, "before_table_edit") if p.snapshot else None
    was_locked = _lock(sap, p.allow_unlock, "edit a database table") if itype == 2 else False
    with bridge.units_scope(sap, p.units):
        res = write_table(sap, key, version, fields, pl["rows"])
        _, _, after = read_table(sap, key)
    ver = _verify(after, pl["expect"], pl["retained"], fields)
    _refresh_view(sap)
    receipt = {**head, **diff, "applied": True, **res, "rows_in_table_now": len(after), "verification": ver,
            "model_was_unlocked": was_locked, **({"snapshot": snap} if snap else {})}
    if ver.keys() - {"rows_checked", "rows_verified"}:
        raise ActionFailed({**receipt, "message": "CSI applied the edit but the table differs from the plan.",
                            "hint": "Inspect the table and the snapshot before retrying."})
    return receipt
