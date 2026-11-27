"""Tables to Excel (0.20.0): one workbook with the database tables the user names, a sheet per table - as the
programs' own File > Export > Tables to Excel.

Each sheet: the table's name, program and model, units and the cases / combinations selected, then the field names,
a row with each field's units (from the program's table field list, in the units of this export) and the rows, with
numbers stored as numbers, the header frozen and filters on. An 'Index' sheet lists the tables with their sheets and
row counts. Uses openpyxl, which the server already has.
"""
from __future__ import annotations

import datetime as dt
import re
from pathlib import Path
from typing import Any

from pydantic import Field

import safety
from actions import (SOFTWARE, UNITS, Params, _read_selected, _selection, _status_core, action, bridge)
from autoload import table_fields
from csi_bridge import Software, UnitPreset, describe_units, user_path
from table_tools import TableError, filter_by_cases, resolve_exact_key, rows_to_dataframe, suggest_keys

MAX_ROWS = 1_048_000


class ExcelP(Params):
    software: Software = SOFTWARE
    table_keys: list[str] = Field(min_length=1, max_length=100, description=(
        "database tables to export, keys as list_available_tables gives them (e.g. 'Joint Reactions', "
        "'Element Forces - Frames', 'Story Drifts')"))
    load_cases_or_combos: list[str] | None = Field(default=None, description=(
        "cases / combinations for the result tables; default all"))
    units: UnitPreset = UNITS
    output_path: str | None = Field(default=None, description=(
        "full path of the .xlsx; default Documents\\CsiMcp\\exports\\<model>_tables_<date-time>.xlsx"))
    overwrite: bool = Field(default=False, description="replace an existing file at output_path")


def _sheet_name(key: str, used: set[str]) -> str:
    base = re.sub(r"[\[\]:*?/\\]", " ", key).strip()[:31] or "Table"
    name, n = base, 2
    while name.lower() in used:
        suffix = f" ({n})"
        name = base[:31 - len(suffix)] + suffix
        n += 1
    used.add(name.lower())
    return name


def _target(p: ExcelP, model_file: str | None) -> Path:
    if p.output_path:
        path = Path(user_path(p.output_path)).resolve()
        if path.suffix.lower() != ".xlsx":
            raise TableError("output_path must end in .xlsx")
        if not path.parent.is_dir():
            raise TableError(f"Folder does not exist: {path.parent}")
        if path.exists() and not p.overwrite:
            raise TableError(f"{path} exists.", hint="Give another output_path, or overwrite: true.")
        return path
    folder = safety.output_root() / "exports"
    folder.mkdir(parents=True, exist_ok=True)
    stem = safety.safe_stem(Path(model_file or f"{p.software}_model").stem)
    return folder / f"{stem}_tables_{safety.stamp()}.xlsx"


@action("export_tables_to_excel", "Database tables to one Excel workbook, a sheet per table (as the programs' Export "
        "> Tables to Excel): table name, model, units and cases on top, field names and their units, the rows with "
        "numbers as numbers, header frozen and filters on, plus an Index sheet. Writes only the .xlsx; the model is "
        "not changed.", "Output", "read", ExcelP, long_running=True)
def export_tables_to_excel(p: ExcelP) -> dict[str, Any]:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    sap = bridge.sap_model(p.software)
    available = [t["key"] for t in bridge.available_tables(sap)]
    keys, missing = [], []
    for k in p.table_keys:
        hit = resolve_exact_key(k, available)
        (keys if hit else missing).append(hit or k)
    if missing:
        raise TableError(f"Tables not available in {p.software} now: {missing}.",
                         hint="; ".join(f"{m}: {suggest_keys(m, available)[:5]}" for m in missing) +
                         ". Result tables appear after the analysis.")
    st = _status_core(p.software, counts=False)
    path = _target(p, st.get("model_file"))
    cases, combos = _selection(sap, p.load_cases_or_combos)
    units = describe_units(p.units)
    units_text = f"{units['force']}, {units['length']}, {units['temperature']}"
    wb = Workbook()
    index = wb.active
    index.title = "Index"
    bold, head_fill = Font(bold=True), PatternFill("solid", fgColor="E8E8E8")
    index.append([f"{p.software} tables - {st.get('model_file') or '(model not saved)'}"])
    index.append([f"Units: {units_text}  |  Cases / combinations: "
                  f"{', '.join(p.load_cases_or_combos) if p.load_cases_or_combos else 'all'}  |  Exported "
                  f"{dt.datetime.now():%Y-%m-%d %H:%M} by CSI MCP"])
    index.append([])
    index.append(["Table", "Sheet", "Rows", "Note"])
    for c in index[4]:
        c.font, c.fill = bold, head_fill
    index["A1"].font = Font(bold=True, size=12)
    used: set[str] = {"index"}
    out, warnings = [], []
    with bridge.units_scope(sap, p.units):
        for key in keys:
            fields, rows, _, warn = _read_selected(p.software, sap, key, cases, combos)
            warnings += warn
            df = filter_by_cases(rows_to_dataframe(fields, rows), p.load_cases_or_combos)
            meta = {f["field"]: f.get("units") or "" for f in table_fields(sap, key)}
            name = _sheet_name(key, used)
            note = ""
            if len(df) > MAX_ROWS:
                note = f"first {MAX_ROWS} of {len(df)} rows (Excel sheet limit); query_database_table exports CSV"
                df = df.iloc[:MAX_ROWS]
            ws = wb.create_sheet(name)
            ws.append([key])
            ws["A1"].font = Font(bold=True, size=12)
            ws.append([f"{st.get('software')} {st.get('version') or ''} - {st.get('model_file') or ''}"])
            ws.append([f"Units: {units_text}  |  Cases / combinations: "
                       f"{', '.join(p.load_cases_or_combos) if p.load_cases_or_combos else 'all'}"])
            cols = list(df.columns)
            ws.append(cols)                                # CSI's own export order: field names, then units
            ws.append([meta.get(c, "") for c in cols])
            for c in ws[4]:
                c.font, c.fill = bold, head_fill
            for c in ws[5]:
                c.font, c.fill = Font(italic=True, color="555555"), head_fill
            for r in df.itertuples(index=False):
                ws.append([None if (isinstance(v, float) and v != v) else
                           (v.item() if hasattr(v, "item") else v) for v in r])
            ws.freeze_panes = "A6"
            if cols:
                ws.auto_filter.ref = f"A5:{get_column_letter(len(cols))}{max(5, ws.max_row)}"
            for i, c in enumerate(cols, 1):
                sample = [str(c)] + [str(v) for v in df[c].head(200).tolist()]
                ws.column_dimensions[get_column_letter(i)].width = min(40, max(8, max(len(s) for s in sample) + 2))
                for cell in ws[get_column_letter(i)][3:5]:
                    cell.alignment = Alignment(wrap_text=False)
            index.append([key, name, len(df), note])
            out.append({"table": key, "sheet": name, "rows": int(len(df)), "columns": len(cols),
                        **({"note": note} if note else {})})
    index.column_dimensions["A"].width = 50
    index.column_dimensions["B"].width = 34
    index.column_dimensions["D"].width = 60
    try:
        # All CSI-provided strings in this workbook are data, including strings beginning with '='.
        safety.literal_cells(wb)
        with safety.staged_file(path, p.overwrite) as tmp:
            wb.save(tmp)
    except PermissionError as exc:
        raise TableError(f"Cannot write {path}: {exc}", hint="Close the workbook if it is open in Excel.") from exc
    finally:
        wb.close()
    return {"workbook": str(path), "sheets": out, "units": units,
            "cases_or_combos": p.load_cases_or_combos or "all", "warnings": warnings}
