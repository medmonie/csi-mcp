"""
table_tools.py - Pure-Python processing of CSI database tables.

Nothing in this module touches COM, so it is unit-testable on any OS. It turns
raw `GetTableForDisplayArray` output into DataFrames, applies the filter
grammar exposed to the LLM, enforces row limits with summary metadata, and
builds the engineering summaries used by `get_analysis_summary`.
"""

from __future__ import annotations

import difflib
import math
import re
from typing import Any, Iterable, Sequence

import pandas as pd


class TableError(ValueError):
    """Raised for table/column/filter problems the user can fix."""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


# Columns that identify *where* a value occurs (used for locations in summaries).
ID_COLUMNS = (
    "Story", "Label", "UniqueName", "Joint", "Node", "Point", "Frame", "Element", "Area",
    "Pier", "Spandrel", "Wall", "Strip", "Slab", "OutputCase", "Case", "LoadCase", "Combo",
    "StepType", "StepNum", "StepNumber", "Mode", "Direction", "LoadCombo",
    "Object", "Station", "ElementStation", "PointElm", "SectionCut", "Location",
)
CASE_COLUMNS = ("OutputCase", "Case", "LoadCase", "Combo", "LoadCombo")

# result_type -> per-software candidate table keys, plus keywords for fuzzy fallback.
RESULT_TABLES: dict[str, dict[str, Any]] = {
    "base_reactions": {
        "ETABS": ["Base Reactions"],
        "SAP2000": ["Base Reactions"],
        "SAFE": ["Base Reactions", "Reaction Summary"],
        "keywords": ["base reaction"],
    },
    "story_drifts": {
        "ETABS": ["Story Drifts"],
        "SAP2000": [],
        "SAFE": [],
        "keywords": ["story drift"],
    },
    "modal_periods": {
        "ETABS": ["Modal Participating Mass Ratios", "Modal Periods And Frequencies"],
        "SAP2000": ["Modal Participating Mass Ratios", "Modal Periods And Frequencies"],
        "SAFE": ["Modal Participating Mass Ratios", "Modal Periods And Frequencies"],
        "keywords": ["modal participating mass", "modal period"],
    },
    "joint_displacements": {
        "ETABS": ["Joint Displacements"],
        "SAP2000": ["Joint Displacements"],
        "SAFE": ["Joint Displacements", "Nodal Displacements", "Point Displacements"],
        "keywords": ["joint displacement", "nodal displacement", "point displacement"],
    },
}


# --------------------------------------------------------------------------- #
# Conversion helpers
# --------------------------------------------------------------------------- #

def py(value: Any) -> Any:
    """Convert numpy/pandas scalars to JSON-safe Python values (NaN -> None)."""
    if value is None:
        return None
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return value
    return value


def rows_to_dataframe(fields: Sequence[str], rows: Sequence[Sequence[str]]) -> pd.DataFrame:
    df = pd.DataFrame(list(rows), columns=list(fields))
    return coerce_numeric(df)


def coerce_numeric(df: pd.DataFrame) -> pd.DataFrame:
    """Convert columns whose every non-blank value parses as a number."""
    out = df.copy()
    keep_text = {c.lower() for c in ID_COLUMNS} - {"stepnum", "stepnumber", "mode"}
    for col in out.columns:
        if str(col).lower() in keep_text:  # names stay text: joint '001' must not become 1
            continue
        series = out[col]
        if pd.api.types.is_numeric_dtype(series):  # object or pandas-3 string dtype only
            continue
        stripped = series.astype(str).str.strip()
        nonblank = stripped[stripped != ""]
        if nonblank.empty:
            continue
        converted = pd.to_numeric(nonblank, errors="coerce")
        if converted.notna().all():
            if converted.isin([math.inf, -math.inf]).any():
                raise TableError(f"Non-finite number in column '{col}'.")
            out[col] = pd.to_numeric(stripped, errors="coerce")  # blanks -> NaN
    return out


def find_column(columns: Iterable[str], candidates: Sequence[str]) -> str | None:
    lookup = {str(c).lower(): c for c in columns}
    for cand in candidates:
        if cand.lower() in lookup:
            return lookup[cand.lower()]
    return None


def id_columns(df: pd.DataFrame) -> list[str]:
    present = {c.lower(): c for c in df.columns}
    return [present[c.lower()] for c in ID_COLUMNS if c.lower() in present]


def location(row: pd.Series, ids: Sequence[str]) -> dict[str, Any]:
    return {c: py(row[c]) for c in ids if c in row.index and str(row[c]) != ""}


# --------------------------------------------------------------------------- #
# Filtering
# --------------------------------------------------------------------------- #

_NUM_OP = re.compile(r"^\s*(>=|<=|!=|>|<|=)\s*(.+?)\s*$")


def _to_float(text: str) -> float | None:
    try:
        return float(text)
    except ValueError:
        return None


def _mask(series: pd.Series, expr: str) -> pd.Series:
    """Evaluate one filter expression against a column."""
    expr = str(expr).strip()
    if "|" in expr:  # OR of alternatives
        parts = expr.split("|")
        if len(parts) > 64 or not all(part.strip() for part in parts):
            raise TableError("OR filters require 1 to 64 non-empty alternatives.")
        result = _mask(series, parts[0])
        for part in parts[1:]:
            result |= _mask(series, part)
        return result
    if expr.startswith("~"):  # case-insensitive "contains"
        needle = expr[1:].strip().lower()
        return series.astype(str).str.lower().str.contains(needle, regex=False)
    match = _NUM_OP.match(expr)
    if match and match.group(1) != "=":
        op, operand = match.groups()
        number = _to_float(operand)
        if op == "!=":
            if number is not None and pd.api.types.is_numeric_dtype(series):
                return series.notna() & (series != number)
            return series.astype(str).str.strip().str.lower() != operand.lower()
        if number is None:
            raise TableError(f"Filter '{expr}' needs a numeric operand.")
        if not pd.api.types.is_numeric_dtype(series):
            raise TableError(f"Filter '{expr}' uses a numeric operator on a text column.")
        return {">": series > number, "<": series < number, ">=": series >= number, "<=": series <= number}[op]
    if match:  # "=value"
        expr = match.group(2)
    number = _to_float(expr)
    if number is not None and pd.api.types.is_numeric_dtype(series):
        return (series - number).abs() <= 1e-9 * max(1.0, abs(number))
    return series.astype(str).str.strip().str.lower() == expr.lower()


def apply_filters(df: pd.DataFrame, filters: dict[str, str] | None) -> tuple[pd.DataFrame, list[str]]:
    """Filter grammar (per column, case-insensitive column names):

    'Story1'            exact match (case-insensitive)
    'Story1|Story2'     any of several values
    '~DCON'             contains
    '>=0.002', '<5'     numeric comparison  ('>', '<', '>=', '<=', '!=')
    """
    if not filters:
        return df, []
    applied: list[str] = []
    out = df
    for col_req, expr in filters.items():
        col = find_column(out.columns, [col_req])
        if col is None:
            close = difflib.get_close_matches(col_req, [str(c) for c in out.columns], n=5, cutoff=0.4)
            raise TableError(
                f"Filter column '{col_req}' is not in this table.",
                hint=f"Available columns: {list(out.columns)}" + (f". Did you mean {close}?" if close else ""),
            )
        out = out[_mask(out[col], expr)]
        applied.append(f"{col} {expr}")
    return out, applied


def filter_by_cases(df: pd.DataFrame, names: Sequence[str] | None) -> pd.DataFrame:
    """Keep only rows of the requested output cases/combos (if the table has a case column)."""
    if not names:
        return df
    col = find_column(df.columns, CASE_COLUMNS)
    if col is None:
        return df
    wanted = {n.strip().lower() for n in names}
    return df[df[col].astype(str).str.strip().str.lower().isin(wanted)]


# --------------------------------------------------------------------------- #
# Output shaping
# --------------------------------------------------------------------------- #

def numeric_summary(df: pd.DataFrame, max_columns: int = 25) -> dict[str, Any]:
    """Min/max (with locations) for each numeric, non-identifier column."""
    ids = id_columns(df)
    summary: dict[str, Any] = {}
    numeric_cols = [c for c in df.columns if c not in ids and pd.api.types.is_numeric_dtype(df[c])]
    for col in numeric_cols[:max_columns]:
        series = df[col].dropna()
        if series.empty:
            continue
        i_min, i_max = series.idxmin(), series.idxmax()
        summary[col] = {
            "min": py(series.loc[i_min]),
            "min_at": location(df.loc[i_min], ids),
            "max": py(series.loc[i_max]),
            "max_at": location(df.loc[i_max], ids),
        }
    if len(numeric_cols) > max_columns:
        summary["_note"] = f"{len(numeric_cols) - max_columns} further numeric columns not summarised"
    return summary


def records(df: pd.DataFrame, max_rows: int) -> list[dict[str, Any]]:
    head = df.head(max(0, max_rows))
    return [{k: py(v) for k, v in row.items()} for row in head.to_dict(orient="records")]


def sort_frame(df: pd.DataFrame, sort_by: str | None, descending: bool, by_abs: bool) -> pd.DataFrame:
    if not sort_by:
        return df
    col = find_column(df.columns, [sort_by])
    if col is None:
        raise TableError(f"sort_by column '{sort_by}' not in table.", hint=f"Columns: {list(df.columns)}")
    if by_abs and pd.api.types.is_numeric_dtype(df[col]):
        return df.reindex(df[col].abs().sort_values(ascending=not descending).index)
    return df.sort_values(col, ascending=not descending, kind="stable")


# --------------------------------------------------------------------------- #
# Table-key resolution
# --------------------------------------------------------------------------- #

def search_tables(tables: Sequence[dict[str, Any]], term: str) -> list[dict[str, Any]]:
    """All words of `term` must appear in the key or name (case-insensitive)."""
    words = [w for w in term.lower().split() if w]
    if not words:
        return list(tables)
    return [t for t in tables if all(w in f"{t['key']} {t['name']}".lower() for w in words)]


def suggest_keys(requested: str, keys: Sequence[str], n: int = 8) -> list[str]:
    lower = {k.lower(): k for k in keys}
    close = difflib.get_close_matches(requested.lower(), list(lower), n=n, cutoff=0.45)
    return [lower[c] for c in close]


def resolve_exact_key(requested: str, keys: Sequence[str]) -> str | None:
    for k in keys:
        if k.lower() == requested.strip().lower():
            return k
    return None


def resolve_result_table(result_type: str, software: str, keys: Sequence[str]) -> str:
    spec = RESULT_TABLES[result_type]
    if result_type == "story_drifts" and software != "ETABS":
        raise TableError(
            f"Story drifts are an ETABS story concept and are not available in {software}.",
            hint="Use result_type='joint_displacements' and compute inter-level drift from joint pairs.",
        )
    for cand in spec.get(software, []):
        hit = resolve_exact_key(cand, keys)
        if hit:
            return hit
    # Fallback across program versions: the shortest key containing the keyword (the caller reports the key used).
    for kw in spec["keywords"]:
        hits = sorted((k for k in keys if kw in k.lower()), key=len)
        if hits:
            return hits[0]
    raise TableError(
        f"No table for '{result_type}' is currently available in {software}.",
        hint="Run the analysis first (modal_periods also needs a modal load case), then use "
             "list_available_tables to confirm the table key.",
    )


# --------------------------------------------------------------------------- #
# Engineering summaries
# --------------------------------------------------------------------------- #

def _govern(df: pd.DataFrame, col: str, ids: Sequence[str], mode: str) -> dict[str, Any] | None:
    series = _numeric(df, col).dropna()
    if series.empty:
        return None
    if mode == "absmax":
        idx = series.abs().idxmax()
    elif mode == "max":
        idx = series.idxmax()
    else:
        idx = series.idxmin()
    return {"value": py(series.loc[idx]), **location(df.loc[idx], ids)}


def _numeric(df: pd.DataFrame, col: str) -> pd.Series:
    try:
        return pd.to_numeric(df[col].replace("", float("nan")), errors="raise")
    except (TypeError, ValueError) as exc:
        raise TableError(f"Results column '{col}' contains non-numeric data.") from exc


def summarize_base_reactions(df: pd.DataFrame, max_rows: int = 60) -> dict[str, Any]:
    comps = {c: find_column(df.columns, [c, f"Global{c}"]) for c in ("FX", "FY", "FZ", "MX", "MY", "MZ")}
    comps = {k: v for k, v in comps.items() if v}
    if not comps:
        raise TableError("Base reaction table has no FX..MZ columns.", hint=f"Columns: {list(df.columns)}")
    case_col = find_column(df.columns, CASE_COLUMNS)
    step_col = find_column(df.columns, ["StepType"])
    type_col = find_column(df.columns, ["CaseType"])
    ids = [c for c in (case_col, step_col) if c]
    # Mode shapes have arbitrary scale: their 'reactions' must never govern.
    modal = pd.Series(False, index=df.index)
    if step_col:
        modal |= df[step_col].astype(str).str.strip().str.lower().eq("mode")
    if type_col:
        modal |= df[type_col].astype(str).str.lower().str.contains("modal|eigen|ritz")
    excluded = int(modal.sum())
    df = df[~modal]
    governing = {
        comp: {"max": _govern(df, col, ids, "max"), "min": _govern(df, col, ids, "min")}
        for comp, col in comps.items()
    }
    per_case = df[ids + list(comps.values())]
    return {
        "governing": governing,
        "modal_rows_excluded": excluded,
        "per_case": records(per_case, max_rows),
        "per_case_rows_total": int(len(per_case)),
    }


def summarize_story_drifts(df: pd.DataFrame, drift_limit: float | None = None, max_stories: int = 80) -> dict[str, Any]:
    drift = find_column(df.columns, ["Drift"])
    if drift is None:
        raise TableError("Story drift table has no 'Drift' column.", hint=f"Columns: {list(df.columns)}")
    df = df.assign(**{drift: _numeric(df, drift)})
    story = find_column(df.columns, ["Story"])
    direction = find_column(df.columns, ["Direction"])
    ids = id_columns(df)
    result: dict[str, Any] = {"governing": _govern(df, drift, ids, "absmax")}
    if direction:
        result["by_direction"] = {
            str(d): _govern(g, drift, ids, "absmax") for d, g in df.groupby(direction, sort=False)
        }
    if story:
        env = []
        for s, g in df.groupby(story, sort=False):  # keeps ETABS top-down story order
            gov = _govern(g, drift, ids, "absmax")
            if gov:
                env.append({"story": str(s), "max_drift": gov["value"],
                            **{k: v for k, v in gov.items() if k not in ("value", story)}})
        result["story_envelope"] = env[:max_stories]
        if len(env) > max_stories:
            result["story_envelope_note"] = f"{len(env) - max_stories} more stories omitted"
    if drift_limit is not None:
        over = df[df[drift].abs() > drift_limit]
        result["drift_limit"] = drift_limit
        result["rows_exceeding_limit"] = int(len(over))
        if story:
            result["stories_exceeding_limit"] = [str(s) for s in over[story].drop_duplicates()]
    return result


def summarize_modal(df: pd.DataFrame, max_modes: int = 30) -> dict[str, Any]:
    period = find_column(df.columns, ["Period"])
    if period is None:
        raise TableError("Modal table has no 'Period' column.", hint=f"Columns: {list(df.columns)}")
    mode = find_column(df.columns, ["Mode", "StepNum", "StepNumber"])
    case = find_column(df.columns, ["Case", "OutputCase"])
    ratio_cols = [c for c in (find_column(df.columns, [n]) for n in
                  ("UX", "UY", "UZ", "RX", "RY", "RZ", "SumUX", "SumUY", "SumUZ", "SumRZ")) if c]
    for col in [period, *ratio_cols, *([mode] if mode else [])]:
        df = df.assign(**{col: _numeric(df, col)})
    groups = df.groupby(case, sort=False) if case else [("(modal)", df)]
    out: dict[str, Any] = {}
    for case_index, (case_name, g) in enumerate(groups):
        if case_index == 3:
            break
        g = g.sort_values(mode) if mode else g
        entry: dict[str, Any] = {"number_of_modes": int(len(g))}
        first = g.iloc[0]
        entry["fundamental_period"] = py(first[period])
        entry["first_three_periods"] = [py(v) for v in g[period].head(3)]
        dominant = {}
        for comp in ("UX", "UY", "RZ"):
            col = find_column(g.columns, [comp])
            if col and g[col].notna().any():
                row = g.loc[g[col].idxmax()]
                dominant[comp] = {"mode": py(row[mode]) if mode else None, "period": py(row[period]),
                                  "ratio": py(row[col])}
        if dominant:
            entry["dominant_modes"] = dominant
        cumulative = {}
        for comp in ("SumUX", "SumUY", "SumUZ", "SumRZ"):
            col = find_column(g.columns, [comp])
            if col and g[col].notna().any():
                reach = g[g[col] >= 0.9]
                cumulative[comp] = {
                    "at_last_mode": py(g[col].iloc[-1]),
                    "mode_reaching_90pct": py(reach[mode].iloc[0]) if (mode and not reach.empty) else None,
                }
        if cumulative:
            entry["cumulative_mass"] = cumulative
        cols = [c for c in (mode, period) if c] + ratio_cols
        entry["modes"] = records(g[cols], max_modes)
        out[str(case_name)] = entry
    if len(groups) > 3:
        out["_note"] = f"{len(groups) - 3} further modal cases omitted"
    return out


def summarize_joint_displacements(df: pd.DataFrame, top_n: int = 10) -> dict[str, Any]:
    comps = {
        "Ux": find_column(df.columns, ["Ux", "U1"]), "Uy": find_column(df.columns, ["Uy", "U2"]),
        "Uz": find_column(df.columns, ["Uz", "U3"]), "Rx": find_column(df.columns, ["Rx", "R1"]),
        "Ry": find_column(df.columns, ["Ry", "R2"]), "Rz": find_column(df.columns, ["Rz", "R3"]),
    }
    comps = {k: v for k, v in comps.items() if v}
    if not comps:
        raise TableError("Displacement table has no U/R columns.", hint=f"Columns: {list(df.columns)}")
    ids = id_columns(df)
    governing = {
        comp: {"abs_max": _govern(df, col, ids, "absmax"), "max": _govern(df, col, ids, "max"),
               "min": _govern(df, col, ids, "min")}
        for comp, col in comps.items()
    }
    result: dict[str, Any] = {"governing": governing}
    vertical = comps.get("Uz")
    if vertical:
        top = df.loc[_numeric(df, vertical).dropna().abs().nlargest(top_n).index]
        result[f"top_{top_n}_vertical"] = records(top[ids + [vertical]], top_n)
    return result
