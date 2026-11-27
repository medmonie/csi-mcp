"""Editing database tables (the CSI 'interactive database' route) for definitions the API of a program has no setter
for - e.g. ETABS user response-spectrum / time-history functions and RS / modal-history case options.

SetTableForEditingArray replaces the WHOLE table: rows left out are deleted from the model. So every edit here reads
the table first, keeps all other rows, and changes or adds only the rows it is asked to. Field keys come from
GetTableForEditingArray (not GetAllFieldsInTable, whose names differ, e.g. 'Damping Ratio' vs 'DampRatio').
ApplyEditedTables runs with the import log; any error or fatal error raises with the log lines that say why.
"""
from __future__ import annotations

from typing import Any, Callable

from csi_bridge import CSIApiError, CSIValidationError, as_list


def read_table(sap: Any, key: str) -> tuple[int, list[str], list[dict[str, str]]]:
    o = sap.DatabaseTables.GetTableForEditingArray(key, "", 0, [], 0, [])
    if not isinstance(o, (list, tuple)) or len(o) < 5 or int(o[-1]) != 0 or not as_list(o[1]):
        # SAP2000 27.1 refuses GetTableForEditingArray for a table with no rows (measured: lanes, vehicles, hinge
        # assignments); its field keys and version then come from GetAllFieldsInTable, and the table is empty
        f = sap.DatabaseTables.GetAllFieldsInTable(key)
        if isinstance(f, (list, tuple)) and len(f) >= 3 and int(f[-1]) == 0 and as_list(f[2]):
            return int(f[0]), [str(k) for k in as_list(f[2])], []
        raise CSIApiError(f"DatabaseTables.GetTableForEditingArray('{key}')", hint="Table not in this program?")
    version, fields, n, data = int(o[0]), [str(f) for f in as_list(o[1])], int(o[2]), [str(v) for v in as_list(o[3])]
    if n == 0:
        # an empty table can come back with stray field names (measured, SAP2000: ['Frame', 'None', 'None'] for
        # 'Lane Definition Data' after another table's edit): take its fields from GetAllFieldsInTable
        try:
            f = sap.DatabaseTables.GetAllFieldsInTable(key)
        except Exception:  # noqa: BLE001 - keep the fields read above
            f = None
        if isinstance(f, (list, tuple)) and len(f) >= 3 and int(f[-1]) == 0 and as_list(f[2]):
            return int(f[0]), [str(k) for k in as_list(f[2])], []
    nf = len(fields)
    rows = [dict(zip(fields, data[i * nf:(i + 1) * nf])) for i in range(n)]
    return version, fields, rows


def _apply(sap: Any) -> dict[str, Any]:
    a = sap.DatabaseTables.ApplyEditedTables(True, 0, 0, 0, 0, "")
    vals = list(a) if isinstance(a, (list, tuple)) else [a]
    log = next((v for v in reversed(vals) if isinstance(v, str)), "")
    nums = [int(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if len(vals) > 1 and isinstance(vals[-1], int) and not isinstance(vals[-1], bool) and isinstance(vals[-2], str):
        nums = nums[:-1]     # (fatal, errors, warnings, info, log, return code): the last number is the return code
    lines = [ln.strip() for ln in log.splitlines() if ln.strip()]
    # the log header always holds 'Import Control for Errors: Abort import after 20 errors' - not an error. The counts
    # ApplyEditedTables returns decide; error text is only lines that start with ERROR / FATAL (measured, ETABS 23.2).
    bad = []
    for i, ln in enumerate(lines):
        if ln.upper().startswith(("ERROR", "FATAL", "*** ERROR", "***ERROR")):
            # SAP2000 writes '*** Error Message 1 ***' and the reason on the next lines (measured)
            bad.append(" ".join([ln] + [x for x in lines[i + 1:i + 4] if not x.startswith("***")]))
    counts = dict(zip(("fatal", "errors", "warnings", "info"), nums[-4:])) if len(nums) >= 4 else {}
    # SAP2000 counts 'field was not found; default values will be used' (a field left out of a new row) as an
    # error although it imports the row with the program's values (measured) - that is the intended behaviour here
    soft = [b for b in bad if "default values will be used" in b.lower()]
    if soft and len(soft) == len(bad):
        return {"import_counts": counts, "program_defaults_used": soft[:10]}
    if bad or counts.get("fatal") or counts.get("errors"):
        raise CSIApiError("DatabaseTables.ApplyEditedTables", detail=" | ".join(bad[:6] or lines[-6:]),
                          hint=f"The program refused the table edit; see the import log lines. Counts: {counts}")
    warn = [ln for ln in lines if "WARNING" in ln.upper()][:6]
    return {"import_counts": counts, **({"import_warnings": warn} if warn else {})}


def write_table(sap: Any, key: str, version: int, fields: list[str], rows: list[dict[str, str]]) -> dict[str, Any]:
    """Set the whole table (rows in field order) and apply it; cancels the pending edit when the set is refused."""
    flat = [str(r.get(f, "")) for r in rows for f in fields]
    s = sap.DatabaseTables.SetTableForEditingArray(key, version, fields, len(rows), flat)
    if not isinstance(s, (list, tuple)) or int(s[-1]) != 0:
        sap.DatabaseTables.CancelTableEditing()
        raise CSIApiError(f"DatabaseTables.SetTableForEditingArray('{key}')")
    try:
        return _apply(sap)
    except CSIApiError:
        try:
            sap.DatabaseTables.CancelTableEditing()
        except Exception:  # noqa: BLE001 - the apply error is the one to report
            pass
        raise


def edit_table(sap: Any, key: str, new_rows: list[dict[str, Any]],
               drop: Callable[[dict[str, str]], bool] | None = None,
               update: Callable[[dict[str, str]], dict[str, Any] | None] | None = None) -> dict[str, Any]:
    """Keep every row, except those `drop` selects; let `update` change fields of kept rows; append new_rows."""
    version, fields, rows = read_table(sap, key)
    unknown = sorted({k for r in new_rows for k in r} - set(fields))
    if unknown:
        raise CSIValidationError(f"Table '{key}' has no fields {unknown}.", hint=f"Fields: {fields}")
    kept, changed = [], 0
    for r in rows:
        if drop and drop(r):
            continue
        if update:
            ch = update(r)
            if ch:
                bad = sorted(set(ch) - set(fields))
                if bad:
                    raise CSIValidationError(f"Table '{key}' has no fields {bad}.", hint=f"Fields: {fields}")
                r = {**r, **{k: str(v) for k, v in ch.items()}}
                changed += 1
        kept.append(r)
    if not rows:      # an empty table: write only the fields given, the program fills its defaults for the rest
        fields = [f for f in fields if any(f in r for r in new_rows)]
    out = kept + [{f: "" if r.get(f) is None else str(r.get(f)) for f in fields} for r in new_rows]
    res = write_table(sap, key, version, fields, out)
    return {"table": key, "rows_before": len(rows), "rows_after": len(out), "rows_changed": changed,
            "rows_added": len(new_rows), **res}
