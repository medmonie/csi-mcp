"""Phase 0 edit-safety and output policy: output folder, table export, change log, model snapshots.

Output folder: CSI_MCP_OUTPUT, else %USERPROFILE%\\Documents\\CsiMcp
    exports\\        full tables written when a query is cut to max_rows (or when asked)
    reports\\        PDFs from export_report (default location)
    change_log.jsonl one line per edit action that ran (not dry runs)
Snapshots go next to the model: <model folder>\\_csi_mcp_snapshots\\<model>_<yyyymmdd-hhmmss>[_label].<ext>
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any

log = logging.getLogger("csi_mcp.safety")


def output_root() -> Path:
    root = Path(os.environ.get("CSI_MCP_OUTPUT") or (Path.home() / "Documents" / "CsiMcp"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S-%f")


@contextmanager
def staged_file(target: Path, overwrite: bool = False):
    """Publish a completed sibling file without truncating an existing destination on failure."""
    fd, filename = tempfile.mkstemp(dir=target.parent, suffix=target.suffix)
    os.close(fd)
    tmp = Path(filename)
    try:
        yield tmp
        if overwrite:
            os.replace(tmp, target)
        elif os.name == "nt":
            os.rename(tmp, target)  # Windows refuses an existing destination atomically.
        else:
            os.link(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def safe_stem(text: str, limit: int = 60) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip()).strip("._") or "table"
    return stem[:limit]


def check_input_file(path: Path) -> None:
    if path.stat().st_size > 64 * 1024 * 1024:
        raise ValueError("Input exceeds the 64 MiB file limit.")
    if path.suffix.lower() in (".xlsx", ".xlsm"):
        with zipfile.ZipFile(path) as book:
            if sum(entry.file_size for entry in book.infolist()) > 128 * 1024 * 1024:
                raise ValueError("Workbook exceeds the 128 MiB uncompressed limit.")


def _csv_text(value):
    """Prefix text Excel would run as a formula; numbers kept as text (e.g. '-1.5', '+3') stay as they are."""
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        try:
            float(value)
            return value
        except ValueError:
            return "'" + value
    return value


def literal_cells(workbook):
    for sheet in workbook:
        for row in sheet.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"


def export_frame(df: Any, stem: str, fmt: str, folder: Path | None = None) -> Path:
    """Write a DataFrame as CSV (UTF-8 with BOM, opens cleanly in Excel), XLSX (openpyxl) or JSON records."""
    folder = folder or (output_root() / "exports")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe_stem(stem)}_{stamp()}.{fmt}"
    with staged_file(path) as tmp:
        if fmt == "csv":
            df.map(_csv_text).rename(columns=_csv_text).to_csv(tmp, index=False, encoding="utf-8-sig")
        elif fmt == "xlsx":
            import pandas as pd
            if len(df) > 1_048_575:
                raise ValueError(f"{len(df)} rows do not fit one Excel sheet (1,048,575); use export csv.")
            with pd.ExcelWriter(tmp, engine="openpyxl") as writer:
                df.to_excel(writer, index=False, sheet_name=safe_stem(stem, 31))
                literal_cells(writer.book)
        elif fmt == "json":
            df.to_json(tmp, orient="records", indent=1, force_ascii=False, double_precision=15)
        else:
            raise ValueError("Unknown export format.")
    return path


def log_change(entry: dict[str, Any]) -> None:
    """Append one JSON line to the change log; never raises (logging must not break an edit)."""
    try:
        line = {"time": dt.datetime.now().isoformat(timespec="seconds"), **entry}
        with (output_root() / "change_log.jsonl").open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, ensure_ascii=False, default=str) + "\n")
    except Exception as exc:  # noqa: BLE001
        log.warning("change log not written: %s", exc)


def snapshot_file(model_path: str, label: str = "") -> dict[str, Any]:
    """Copy the model file as last saved on disk. The open program is not touched (no Save As)."""
    src = Path(model_path)
    if not src.is_file():
        raise FileNotFoundError(f"model file not found on disk: {src}")
    folder = src.parent / "_csi_mcp_snapshots"
    folder.mkdir(exist_ok=True)
    name = f"{src.stem}_{stamp()}" + (f"_{safe_stem(label, 40)}" if label else "") + src.suffix
    dest = folder / name
    with staged_file(dest) as tmp:
        shutil.copy2(src, tmp)
    saved = dt.datetime.fromtimestamp(src.stat().st_mtime).isoformat(timespec="seconds")
    return {"snapshot": str(dest), "model_file": str(src), "model_last_saved": saved,
            "note": "The snapshot is the model as last saved; changes not yet saved in the program are not in it."}
