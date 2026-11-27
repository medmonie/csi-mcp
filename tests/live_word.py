r"""0.20.0: export_report as Word (.docx) and RTF, export_tables_to_excel - on a small analysed SAP2000 frame, the files
opened by Microsoft Word and Excel themselves (through COM, when installed) to prove they are valid.

    run_live_word.bat [--hidden]        Output: tests\results\live_word.txt, files in word_output\
"""
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "results" / "word_output"
os.environ["CSI_MCP_OUTPUT"] = str(OUT)

from live_common import FAILED, bridge, check, com, finish, show  # noqa: E402,I001

visible = "--hidden" not in sys.argv
SW = "SAP2000"
S = {"software": SW}
U = "kN_m_C"


def word_pages(path: Path):
    import comtypes.client
    word = comtypes.client.CreateObject("Word.Application")
    try:
        word.Visible = False
        word.DisplayAlerts = 0
        doc = word.Documents.Open(str(path), False, True)     # (file, confirm conversions, read only)
        pages = int(doc.ComputeStatistics(2))                 # wdStatisticPages
        shapes = int(doc.InlineShapes.Count)
        tables = int(doc.Tables.Count)
        doc.Close(False)
        return pages, shapes, tables
    finally:
        word.Quit()


def excel_sheets(path: Path):
    import comtypes.client
    xl = comtypes.client.CreateObject("Excel.Application")
    try:
        xl.Visible = False
        xl.DisplayAlerts = False
        wb = xl.Workbooks.Open(str(path), 0, True)
        names = [wb.Worksheets.Item(i).Name for i in range(1, wb.Worksheets.Count + 1)]
        wb.Close(False)
        return names
    finally:
        xl.Quit()


def run():
    show("new model", "new_model", {**S, "units": U, "file_path": str(OUT / "word.sdb"), "discard_open_model": True},
         limit=300)
    show("concrete", "define_material", {**S, "units": U, "name": "C30", "type": "concrete", "E": 2.5e7,
                                         "poisson": 0.2, "unit_weight": 25.0, "fc": 30000.0}, limit=300)
    show("section", "define_frame_section", {**S, "units": U, "name": "B", "shape": "rectangle", "material": "C30",
                                             "depth": 0.6, "width": 0.3}, limit=300)
    fr = show("frames", "create_frames", {**S, "units": U, "section": "B", "frames": [
        {"start": [0, 0, 0], "end": [0, 0, 4], "name": "C1"}, {"start": [0, 0, 4], "end": [6, 0, 4], "name": "B1"},
        {"start": [6, 0, 0], "end": [6, 0, 4], "name": "C2"}]}, limit=500)
    base = [f["points"][0] for f in fr["created"] if f["name"] in ("C1", "C2")] if isinstance(fr, dict) else []
    show("supports", "assign_point_properties", {**S, "point_names": base, "restraint": [True] * 6}, limit=200)
    show("live", "define_load_pattern", {**S, "name": "LIVE", "type": "live", "self_weight_multiplier": 0}, limit=200)
    show("beam load", "assign_point_or_frame_load", {**S, "units": U, "target_type": "frame", "object_names": ["B1"],
                                                     "load_pattern": "LIVE", "values": [20]}, limit=200)
    show("save", "open_or_save_model", {**S, "action": "save"}, limit=200)
    show("analysis", "run_analysis", {**S}, limit=600)
    common = {**S, "units": U, "table_keys": ["Joint Reactions", "Element Forces - Frames"],
              "load_cases_or_combos": ["DEAD", "LIVE"], "result_case": "LIVE"}
    files = {}
    for fmt in ("docx", "rtf", "pdf"):
        r = show(f"report {fmt}", "export_report", {**common, "format": fmt}, limit=1500)
        ok = isinstance(r, dict) and not r.get("_error") and Path(r.get("report", "")).is_file()
        check(f"report {fmt} written", ok, r if not ok else r["report"])
        if ok:
            files[fmt] = (Path(r["report"]), r)
    for fmt in ("docx", "rtf"):
        if fmt not in files:
            continue
        path, r = files[fmt]
        try:
            pages, shapes, tables = com(word_pages, path)
            print(f"  Word opened the {fmt}: {pages} pages, {shapes} pictures, {tables} tables", flush=True)
            check(f"Word opens the {fmt} report (pictures = figures, tables present)",
                  pages >= 3 and shapes >= r.get("figures", 1) and tables >= 3, (pages, shapes, tables))
        except Exception as exc:  # noqa: BLE001
            print(f"  Word not available to open the {fmt}: {exc}", flush=True)
    x = show("tables to excel", "export_tables_to_excel",
             {**S, "units": U, "table_keys": ["Joint Reactions", "Element Forces - Frames", "Frame Section Properties "
                                              "01 - General"], "load_cases_or_combos": ["DEAD", "LIVE"]}, limit=1500)
    ok = isinstance(x, dict) and not x.get("_error") and Path(x.get("workbook", "")).is_file()
    check("tables to excel written", ok, x)
    if ok:
        rr = next(s for s in x["sheets"] if s["table"] == "Joint Reactions")
        check("joint reactions sheet: 2 joints x 2 cases", rr["rows"] == 4, rr)
        try:
            names = com(excel_sheets, Path(x["workbook"]))
            print("  Excel opened the workbook:", names, flush=True)
            check("Excel opens the workbook with the Index and one sheet per table",
                  names[0] == "Index" and len(names) == 1 + len(x["sheets"]), names)
        except Exception as exc:  # noqa: BLE001
            print("  Excel not available:", exc, flush=True)
    q = show("query as xlsx", "query_database_table", {**S, "units": U, "table_key": "Joint Reactions",
                                                       "export": "xlsx"}, limit=800)
    check("query_database_table export xlsx", isinstance(q, dict) and str(q.get("export_path", "")).endswith(".xlsx"),
          q)


if OUT.exists():
    shutil.rmtree(OUT, ignore_errors=True)
OUT.mkdir(parents=True, exist_ok=True)
print(f"################ {SW} (new instance, visible={visible})", flush=True)
try:
    com(bridge.launch_new, SW, visible=visible)
    run()
except Exception as exc:  # noqa: BLE001
    FAILED.append(f"{type(exc).__name__}: {exc}")
    print("EXCEPTION", exc, flush=True)
finally:
    try:
        print(com(bridge.disconnect, SW, True, False), flush=True)
    except Exception as exc:  # noqa: BLE001
        print("close", exc, flush=True)
sys.exit(finish())
