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
from pathlib import Path
from typing import Any

log = logging.getLogger("csi_mcp.safety")


def output_root() -> Path:
    root = Path(os.environ.get("CSI_MCP_OUTPUT") or (Path.home() / "Documents" / "CsiMcp"))
    root.mkdir(parents=True, exist_ok=True)
    return root


def stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def safe_stem(text: str, limit: int = 60) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip()).strip("._") or "table"
    return stem[:limit]


def export_frame(df: Any, stem: str, fmt: str, folder: Path | None = None) -> Path:
    """Write a DataFrame as CSV (UTF-8 with BOM, opens cleanly in Excel), XLSX (openpyxl) or JSON records."""
    folder = folder or (output_root() / "exports")
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{safe_stem(stem)}_{stamp()}.{fmt}"
    if fmt == "csv":
        df.to_csv(path, index=False, encoding="utf-8-sig")
    elif fmt == "xlsx":
        try:
            import openpyxl  # noqa: F401
        except ImportError as exc:
            raise ValueError("XLSX export needs openpyxl: run setup_server.bat again (or use export csv).") from exc
        if len(df) > 1_048_575:
            raise ValueError(f"{len(df)} rows do not fit one Excel sheet (1,048,575); use export csv.")
        df.to_excel(path, index=False, sheet_name=safe_stem(stem, 31))
    else:
        df.to_json(path, orient="records", indent=1, force_ascii=False)
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
    shutil.copy2(src, dest)
    saved = dt.datetime.fromtimestamp(src.stat().st_mtime).isoformat(timespec="seconds")
    return {"snapshot": str(dest), "model_file": str(src), "model_last_saved": saved,
            "note": "The snapshot is the model as last saved; changes not yet saved in the program are not in it."}
