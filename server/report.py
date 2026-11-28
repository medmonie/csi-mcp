"""Report track R1: export_report - a PDF of the model and its results, built by the MCP from API data.

Layout is decided per request: A4 portrait by default; the designer can ask for another page size or orientation.
Every page carries the program / version, model file and units. Values are as the program reports them (no
computation beyond the summaries get_analysis_summary already gives). Tables longer than max_rows_per_table are cut
in the PDF and written in full to a CSV next to it.

Content (sections): cover, model (counts, case status, project information), diagrams (R2: 3-D overview, plan per
story / level, elevation per grid line, one load view per load pattern), definitions (materials, sections,
patterns, cases, combinations with factors), tables (any database tables asked for), results (base reactions, story
drifts in ETABS, modal periods and mass, peak joint displacements).
"""
from __future__ import annotations

from collections import defaultdict

import datetime as dt
import math
from pathlib import Path
from typing import Any, Literal

from pydantic import Field

import safety
from actions import (
    ActionFailed, DefinitionsP, Params, SOFTWARE, SummaryP, UNITS, _read_selected, _selection, _status_core, action,
    bridge, get_analysis_summary, list_definitions,
)
import diagrams as dg
from csi_bridge import CSIError, Software, UnitPreset, api_call, as_list, describe_units
from model_read import report_geometry
import results as rs
from table_tools import (
    TableError, filter_by_cases, id_columns, resolve_exact_key, rows_to_dataframe, suggest_keys,
)

SECTIONS = ("cover", "model", "diagrams", "definitions", "tables", "results", "result_diagrams", "design")
DESIGN_KINDS = ("steel_frame", "concrete_frame", "concrete_slab", "shear_wall")
RESULT_VIEWS = ("deformed", "reactions", "frame_forces", "shell_contours", "story_charts")
VIEWS = ("overview", "plans", "elevations", "loads")
RESULT_TYPES = ("base_reactions", "story_drifts", "modal_periods", "joint_displacements")


class ReportP(Params):
    software: Software = SOFTWARE
    format: Literal["pdf", "docx", "rtf"] = Field(default="pdf", description=(
        "pdf (default) | docx (Word) | rtf (Word / any word processor) - the same content in each"))
    output_path: str | None = Field(default=None, description=(
        "full path of the file (.pdf / .docx / .rtf as format); default: Documents\\CsiMcp\\reports\\<model>_"
        "<date-time>.<format>"))
    overwrite: bool = Field(default=False, description="replace an existing file at output_path")
    sections: list[Literal["cover", "model", "diagrams", "definitions", "tables", "results", "result_diagrams",
                           "design"]] | None = Field(default=None, description=(
        "cover | model | diagrams | definitions | tables | results | result_diagrams | design; default: all (tables "
        "only when table_keys are given; results and result diagrams only when the model has results; design for "
        "every design kind that has design results)"))
    design_kinds: list[Literal["steel_frame", "concrete_frame", "concrete_slab", "shear_wall"]] | None = Field(
        default=None, description="design section: steel_frame | concrete_frame[[EF: | concrete_slab]][[E: | "
                                  "shear_wall]]; default every kind with design results")
    result_case: str | None = Field(default=None, description=(
        "case or combination for the result diagrams; default the first combination, else the first linear static "
        "case"))
    result_diagrams: list[Literal["deformed", "reactions", "frame_forces", "shell_contours", "story_charts"]] | None = \
        Field(default=None, description="deformed | reactions | frame_forces (per elevation) | shell_contours (per "
                                        "level)[[E: | story_charts (drift and shear)]]; default all")
    frame_component: Literal["bending", "P", "V2", "V3", "T", "M2", "M3"] = Field(default="bending", description=(
        "frame force drawn in the elevations: bending (default: the in-plane moment of each member, M3 or M2 from "
        "its local axes) | P | V2 | V3 | T | M2 | M3"))
    shell_component: Literal["F11", "F22", "F12", "M11", "M22", "M12", "V13", "V23", "MMax", "MMin", "VMax"] = Field(
        default="M11", description="shell force / moment for the slab (floor) contours, default M11")
    contour_averaging: Literal["program_default", "joints", "none"] = Field(default="program_default", description=(
        "shell contours: program_default (as the program shows them: SAP2000 averaged at all joints, ETABS / SAFE "
        "not averaged) | joints (averaged at the joints: continuous across elements) | none (element values)"))
    wall_component: Literal["F11", "F22", "F12", "M11", "M22", "M12", "V13", "V23", "MMax", "MMin", "VMax"] = Field(
        default="F22", description="shell force / moment for the wall (vertical shell) contours, default F22")
    drift_limit: float | None = Field(default=None, gt=0, description="drift ratio given by the engineer, drawn on the "
                                                                      "drift chart")
    diagrams: list[Literal["overview", "plans", "elevations", "loads"]] | None = Field(
        default=None, description="views in the diagrams section: overview (3-D) | plans (per story / level) | "
                                  "elevations (per grid line) | loads (per load pattern); default all")
    diagram_patterns: list[str] | None = Field(default=None, max_length=40, description=(
        "load patterns to draw; default every pattern that has loads assigned (up to 20)"))
    max_plans: int = Field(default=12, ge=1, le=60, description="most plans / elevations drawn, the busiest first "
                                                               "(default 12 each)")
    table_keys: list[str] | None = Field(default=None, max_length=30, description=(
        "database tables to include (exact keys from list_available_tables)"))
    load_cases_or_combos: list[str] | None = Field(default=None, description="cases / combinations for tables and "
                                                                             "results; default all")
    max_rows_per_table: int = Field(default=200, ge=1, le=5000, description=(
        "rows per table in the PDF (default 200); longer tables also go in full to a CSV next to the PDF"))
    project_info: dict[str, str] | None = Field(default=None, description=(
        "cover fields, e.g. title, project, client, job_no, prepared_by, checked_by, revision, notes"))
    page_size: Literal["A4", "A3", "A2", "Letter", "Legal"] = Field(default="A4", description="default A4")
    orientation: Literal["portrait", "landscape", "auto"] = Field(default="portrait", description=(
        "portrait (default) | landscape | auto (landscape when a table has more than 10 columns)"))
    units: UnitPreset = UNITS


# --------------------------------------------------------------------------- data (COM thread)

def _combo_rows(sap: Any, combos: list[str], limit: int = 3000) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for combo in combos:
        try:
            outs = api_call("RespCombo.GetCaseList", sap.RespCombo.GetCaseList, combo)
        except CSIError as exc:
            rows.append({"Combination": combo, "Type": "", "Case / combination": f"(not readable: {exc})",
                         "Factor": None})
            continue
        for kind, name, sf in zip(as_list(outs[1]), as_list(outs[2]), as_list(outs[3])):
            rows.append({"Combination": combo, "Type": "combination" if int(kind) == 1 else "case",
                         "Case / combination": str(name), "Factor": float(sf)})
            if len(rows) >= limit:
                return rows
    return rows


def _project_info(sap: Any) -> list[dict[str, str]]:
    try:
        outs = api_call("GetProjectInfo", sap.GetProjectInfo)
        return [{"Item": str(i), "Data": str(d)} for i, d in zip(as_list(outs[1]), as_list(outs[2])) if str(d).strip()]
    except (CSIError, AttributeError, IndexError):
        return []


def gather(p: ReportP) -> dict[str, Any]:
    sections = list(p.sections) if p.sections else [s for s in SECTIONS if s != "tables" or p.table_keys]
    sap = bridge.sap_model(p.software)
    data: dict[str, Any] = {"sections": sections, "skipped": [], "status": _status_core(p.software)}
    try:
        data["case_status"] = bridge.case_status(sap)
    except CSIError as exc:
        data["case_status"] = {}
        data["skipped"].append(("case status", str(exc)))
    if "model" in sections:
        data["project_info_model"] = _project_info(sap)
    if "diagrams" in sections:
        try:
            with bridge.units_scope(sap, p.units):
                data["geometry"] = report_geometry(sap, p.software)
            data["skipped"] += [("load diagrams", m) for m in data["geometry"]["loads"]["skipped"]]
        except (CSIError, KeyError, ValueError) as exc:
            data["skipped"].append(("diagrams", str(exc)[:300]))
    if "result_diagrams" in sections:
        finished = {n for n, st_ in data["case_status"].items() if st_ == "Finished"}
        if not finished:
            data["skipped"].append(("result diagrams", "the model has no analysis results (run the analysis first)"))
        else:
            case = p.result_case or rs.default_case(sap, data["case_status"])
            if case is None:
                data["skipped"].append(("result diagrams", "no combination or linear static case with results"))
            else:
                try:
                    with bridge.units_scope(sap, p.units):
                        if "geometry" not in data:
                            data["geometry"] = report_geometry(sap, p.software)
                        data["rdiag"] = rs.diagram_data(sap, p.software, case, p.result_diagrams or list(RESULT_VIEWS),
                                                        p.frame_component, p.shell_component,
                                                        wall_comp=p.wall_component)
                    data["skipped"] += data["rdiag"]["skipped"]
                except (CSIError, TableError, KeyError, ValueError) as exc:
                    data["skipped"].append(("result diagrams", str(exc)[:300]))
    if "definitions" in sections:
        d = list_definitions(DefinitionsP(software=p.software, max_items=5000))
        combos = d["load_combinations"].get("items", []) if d["load_combinations"].get("available", True) else []
        data["definitions"] = d
        data["combo_rows"] = _combo_rows(sap, combos)
    if "tables" in sections:
        if not p.table_keys:
            data["skipped"].append(("tables", "no table_keys given"))
        else:
            keys = [t["key"] for t in bridge.available_tables(sap)]
            cases, combos = _selection(sap, p.load_cases_or_combos)
            data["tables"] = []
            with bridge.units_scope(sap, p.units):
                for requested in p.table_keys:
                    key = resolve_exact_key(requested, keys)
                    if key is None:
                        data["skipped"].append((f"table '{requested}'", "not available now; closest: "
                                                + ", ".join(suggest_keys(requested, keys)[:4])))
                        continue
                    try:
                        fields, rows, _, _ = _read_selected(p.software, sap, key, cases, combos)
                    except (CSIError, TableError) as exc:
                        data["skipped"].append((f"table '{key}'", str(exc)))
                        continue
                    df = filter_by_cases(rows_to_dataframe(fields, rows), p.load_cases_or_combos)
                    data["tables"].append((key, df))
    if "design" in sections:
        import design as dz
        data["design"] = []
        for kind in p.design_kinds or DESIGN_KINDS:
            import programs as progs
            if p.software not in progs.expand(dz.KIND_PROGRAMS[kind]):
                continue
            try:
                obj = dz._design(sap, kind)
                # a kind without a results flag is only read when asked for: reading slab / pier results with no
                # design can start the program's design engine
                avail = bool(obj.GetResultsAvailable()) if hasattr(obj, "GetResultsAvailable") \
                    else bool(p.design_kinds and kind in p.design_kinds)
            except Exception as exc:  # noqa: BLE001
                data["skipped"].append((f"design {kind}", str(exc)[:200]))
                continue
            if not avail:
                if p.design_kinds:
                    data["skipped"].append((f"design {kind}", "no design results (run_design first)"))
                continue
            try:
                res = dz.gather_results(sap, p.software, kind, None, None, p.units)
                res["settings"] = dz.read_settings(sap, p.software, kind, p.units)
                res["ratios"] = dz.frame_ratios(kind, res)
                if "geometry" not in data:
                    with bridge.units_scope(sap, p.units):
                        data["geometry"] = report_geometry(sap, p.software)
                data["design"].append(res)
            except (CSIError, KeyError, ValueError) as exc:
                data["skipped"].append((f"design {kind}", str(exc)[:300]))
    if "results" in sections:
        finished = [n for n, s in data["case_status"].items() if s == "Finished"]
        data["results"] = {}
        if not finished:
            data["skipped"].append(("results", "the model has no analysis results (run the analysis first)"))
        else:
            for rt in RESULT_TYPES:
                if rt == "story_drifts" and p.software != "ETABS":
                    continue
                try:
                    data["results"][rt] = get_analysis_summary(SummaryP(
                        software=p.software, result_type=rt, combos=p.load_cases_or_combos, units=p.units))
                except (CSIError, TableError, ActionFailed) as exc:
                    data["skipped"].append((rt.replace("_", " "), str(exc).split(". Tried")[0][:300]))
    return data


# --------------------------------------------------------------------------- PDF

def _fmt(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return ""
        if v == int(v) and abs(v) < 1e15:
            return str(int(v))
        return f"{v:.6g}"
    if hasattr(v, "item"):
        return _fmt(v.item())
    return str(v)


class _Pdf:
    def __init__(self, p: ReportP, data: dict[str, Any], path: Path) -> None:
        from reportlab.lib import colors, pagesizes
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import mm

        self.p, self.data, self.path, self.mm, self.colors = p, data, path, mm, colors
        size = {"A4": pagesizes.A4, "A3": pagesizes.A3, "A2": pagesizes.A2, "Letter": pagesizes.LETTER,
                "Legal": pagesizes.LEGAL}[p.page_size]
        landscape = p.orientation == "landscape" or (
            p.orientation == "auto" and any(len(df.columns) > 10 for _, df in data.get("tables", [])))
        self.orientation = "landscape" if landscape else "portrait"
        self.pagesize = pagesizes.landscape(size) if landscape else pagesizes.portrait(size)
        self.margin = 15 * mm
        self.avail = self.pagesize[0] - 2 * self.margin
        ss = getSampleStyleSheet()
        self.st = {
            "title": ParagraphStyle("t", parent=ss["Title"], fontSize=22, leading=26, spaceAfter=10),
            "h1": ParagraphStyle("h1", parent=ss["Heading1"], fontSize=14, spaceBefore=6, spaceAfter=6),
            "h2": ParagraphStyle("h2", parent=ss["Heading2"], fontSize=11, spaceBefore=8, spaceAfter=4),
            "body": ParagraphStyle("b", parent=ss["BodyText"], fontSize=9, leading=11.5),
            "note": ParagraphStyle("n", parent=ss["BodyText"], fontSize=7.5, leading=9.5, textColor=colors.HexColor("#555555")),
            "cellh": ParagraphStyle("ch", parent=ss["BodyText"], fontName="Helvetica-Bold", fontSize=7, leading=8.5),
        }
        st = data["status"]
        self.units = describe_units(p.units)
        self.units_text = f"{self.units['force']}, {self.units['length']}, {self.units['temperature']}"
        self.program = f"{st['software']} {st.get('version') or ''}".strip()
        # diagrams drawn as the program draws them: SAP2000's look, or ETABS / SAFE's
        self.style = "sap" if "SAP" in str(st["software"]).upper() else "etabs"
        self.model_file = st.get("model_file") or "(model not saved)"
        info = p.project_info or {}
        self.title = info.get("title") or f"{st['software']} analysis report"
        self.generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
        self.pages = 0
        self.figures = 0
        self.csv_files: list[str] = []

    # page furniture -------------------------------------------------------
    def _page(self, canvas, doc) -> None:
        self.pages = max(self.pages, doc.page)
        w, h = self.pagesize
        m = self.margin
        canvas.saveState()
        canvas.setStrokeColor(self.colors.HexColor("#999999"))
        canvas.setLineWidth(0.4)
        canvas.line(m, h - m + 4, w - m, h - m + 4)
        canvas.line(m, m - 6, w - m, m - 6)
        canvas.setFont("Helvetica-Bold", 8)
        canvas.drawString(m, h - m + 8, self.title[:90])
        canvas.setFont("Helvetica", 8)
        canvas.drawRightString(w - m, h - m + 8, self.program)
        canvas.setFont("Helvetica", 7)
        model = self.model_file if len(self.model_file) <= 110 else "..." + self.model_file[-107:]
        canvas.drawString(m, m - 15, f"Model: {model}")
        canvas.drawString(m, m - 24, f"Units: {self.units_text}  |  Generated {self.generated} by CSI MCP")
        canvas.drawRightString(w - m, m - 15, f"Page {doc.page}")
        canvas.restoreState()

    # building blocks ------------------------------------------------------
    def para(self, text: str, style: str = "body"):
        from reportlab.platypus import Paragraph
        from xml.sax.saxutils import escape
        return Paragraph(escape(text), self.st[style])

    def kv_table(self, rows: list[tuple[str, str]], first_width: float = 45):
        from reportlab.platypus import Table, TableStyle
        cells = [[self.para(k, "cellh"), self.para(v, "body")] for k, v in rows]
        t = Table(cells, colWidths=[first_width * self.mm, self.avail - first_width * self.mm], hAlign="LEFT")
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, self.colors.HexColor("#bbbbbb")),
                               ("BACKGROUND", (0, 0), (0, -1), self.colors.HexColor("#f0f0f0")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP")]))
        return t

    def data_table(self, columns: list[str], rows: list[list[Any]], id_cols: list[str] | None = None) -> list:
        """One or more tables that fit the page width; wide tables are split into column groups that repeat the
        identifying columns. Header row repeats on every page."""
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.platypus import Spacer, Table, TableStyle

        text = [[_fmt(v) for v in r] for r in rows]
        numeric = [all((not r[i]) or _is_num(r[i]) for r in text[:300]) for i in range(len(columns))]
        for font in (7.0, 6.2, 5.5):
            widths = []
            for i, c in enumerate(columns):
                body = max((stringWidth(r[i], "Helvetica", font) for r in text[:300]), default=0)
                head = min(stringWidth(str(c), "Helvetica-Bold", font), 28 * self.mm)
                widths.append(min(max(body, head, 8 * self.mm - 6) + 6, 60 * self.mm))
            if sum(widths) <= self.avail:
                break
        ids = [columns.index(c) for c in (id_cols or []) if c in columns][:3]
        groups: list[list[int]] = []
        if sum(widths) <= self.avail:
            groups = [list(range(len(columns)))]
        else:
            rest = [i for i in range(len(columns)) if i not in ids]
            base = sum(widths[i] for i in ids)
            cur: list[int] = []
            for i in rest:
                if cur and base + sum(widths[j] for j in cur) + widths[i] > self.avail:
                    groups.append(ids + cur)
                    cur = []
                cur.append(i)
            if cur:
                groups.append(ids + cur)
        out: list = []
        for gi, g in enumerate(groups):
            from reportlab.lib.styles import ParagraphStyle
            hstyle = ParagraphStyle("hx", parent=self.st["cellh"], fontSize=font, leading=font + 1.5)
            head = [self.para_style(str(columns[i]), hstyle) for i in g]
            body = [[r[i] for i in g] for r in text]
            gw = [widths[i] for i in g]
            scale = min(1.0, self.avail / sum(gw)) if gw else 1.0
            t = Table([head] + body, colWidths=[w * scale for w in gw], repeatRows=1, hAlign="LEFT")
            style = [("FONT", (0, 1), (-1, -1), "Helvetica", font),
                     ("GRID", (0, 0), (-1, -1), 0.25, self.colors.HexColor("#bbbbbb")),
                     ("BACKGROUND", (0, 0), (-1, 0), self.colors.HexColor("#e8e8e8")),
                     ("VALIGN", (0, 0), (-1, -1), "TOP"),
                     ("TOPPADDING", (0, 0), (-1, -1), 1.2), ("BOTTOMPADDING", (0, 0), (-1, -1), 1.2),
                     ("LEFTPADDING", (0, 0), (-1, -1), 2.5), ("RIGHTPADDING", (0, 0), (-1, -1), 2.5)]
            for k, i in enumerate(g):
                if numeric[i]:
                    style.append(("ALIGN", (k, 1), (k, -1), "RIGHT"))
            t.setStyle(TableStyle(style))
            if len(groups) > 1:
                out.append(self.para(f"Columns, part {gi + 1} of {len(groups)}", "note"))
            out += [t, Spacer(1, 4)]
        return out

    def para_style(self, text: str, style):
        from reportlab.platypus import Paragraph
        from xml.sax.saxutils import escape
        return Paragraph(escape(text), style)

    def frame_table(self, df, title: str, source: str) -> list:
        """A DataFrame as a titled table, cut to max_rows_per_table with the full table in a CSV."""
        from reportlab.platypus import KeepTogether
        n = len(df)
        limit = self.p.max_rows_per_table
        note = f"Source: {source}. Units: {self.units_text}. Rows: {n}"
        if n > limit:
            csv_path = safety.export_frame(df, f"{self.path.stem}_{title}", "csv", self.path.parent)
            self.csv_files.append(str(csv_path))
            note += f" (first {limit} shown; full table: {csv_path.name})"
        head = [self.para(title, "h2"), self.para(note, "note")]
        if n == 0:
            return head + [self.para("The table is empty for the selected cases / combinations.", "note")]
        part = df.head(limit)
        tables = self.data_table([str(c) for c in part.columns], part.values.tolist(), id_columns(part))
        return [KeepTogether(head + tables[:1])] + tables[1:]

    # sections -------------------------------------------------------------
    def cover(self) -> list:
        from reportlab.platypus import PageBreak, Spacer
        st, info = self.data["status"], dict(self.p.project_info or {})
        rows = [(k.replace("_", " ").capitalize(), v) for k, v in info.items() if k != "title" and v]
        rows += [("Program", self.program), ("Model file", self.model_file), ("Units", self.units_text),
                 ("Model locked (has results)", "yes" if st.get("model_locked") else "no"),
                 ("Analysis cases finished", f"{sum(1 for s in self.data['case_status'].values() if s == 'Finished')}"
                                             f" of {len(self.data['case_status'])}"),
                 ("Report date", self.generated),
                 ("Page size", f"{self.p.page_size} {self.orientation}")]
        return [Spacer(1, 30 * self.mm), self.para(self.title, "title"), Spacer(1, 8 * self.mm), self.kv_table(rows),
                Spacer(1, 6 * self.mm),
                self.para("Values are as reported by the program through its API, in the units shown. Summaries list "
                          "the governing values found in the program's tables; they are not design checks.", "note"),
                PageBreak()]

    def model(self) -> list:
        import pandas as pd
        out = [self.para("Model", "h1")]
        counts = self.data["status"].get("object_counts", {})
        out += self.frame_table(pd.DataFrame([{"Item": k.replace("_", " "), "Count": v} for k, v in counts.items()]),
                                "Object counts", "API object counts")
        cs = self.data.get("case_status", {})
        if cs:
            out += self.frame_table(pd.DataFrame([{"Load case": k, "Status": v} for k, v in cs.items()]),
                                    "Analysis case status", "Analyze.GetCaseStatus")
        if self.data.get("project_info_model"):
            out += self.frame_table(pd.DataFrame(self.data["project_info_model"]), "Project information (model)",
                                    "GetProjectInfo")
        return out

    def diagrams(self) -> list:
        from reportlab.platypus import KeepTogether
        geo = self.data.get("geometry")
        if not geo:
            return []
        views = self.p.diagrams or list(VIEWS)
        w = self.avail
        # two figures per page in portrait, one in landscape
        page_h = self.pagesize[1] - 2 * self.margin - 10 * self.mm
        h = min(w * 0.62, (page_h - 32 * self.mm) / 2) if self.orientation == "portrait" else \
            min(w * 0.62, page_h - 28 * self.mm)
        intro = [self.para("Model diagrams", "h1"),
                 self.para(f"Drawn from the model geometry read through the API ({len(geo['points'])} points, "
                               f"{len(geo['frames'])} frames, {len(geo['areas'])} areas). Coordinates in "
                               f"{self.units['length']}; frame labels are section names; triangles are supports "
                               "(filled = fixed).", "note")]
        out: list = []

        def block(title: str, note: str, drawing) -> None:
            head = intro if not out else []          # keep the section heading with its first figure
            out.append(KeepTogether(head + [self.para(title, "h2"), self.para(note, "note"), drawing]))
            self.figures += 1

        if "overview" in views:
            block("3-D overview", "Isometric view of all frames, areas and supports.", dg.overview(geo, w, h))
        if "plans" in views:
            levels = dg.plan_levels(geo, self.p.max_plans)
            for name, z, n in levels:
                block(f"Plan - {name}", f"Level {dg._fmt(z)} {self.units['length']}: {n} objects at or arriving at "
                                        "this level; columns shown as squares.", dg.plan(geo, z, w, h))
        if "elevations" in views:
            for title, axis, c, n in dg.elevation_planes(geo, self.p.max_plans):
                block(f"Elevation - {title}", f"{n} vertical / inclined members in this plane.",
                      dg.elevation(geo, axis, c, w, h))
        if "loads" in views:
            loads = geo["loads"]
            used = {pat for kind in ("areas", "frames", "points") for _, pat, _ in loads[kind]}
            pats = self.p.diagram_patterns or [p for p in geo["patterns"] if p in used][:20]
            for pat in pats:
                if pat not in used:
                    self.data["skipped"].append((f"load diagram '{pat}'", "no loads assigned to objects in this "
                                                                         "pattern (self weight is not drawn)"))
                    continue
                d, (na, nf, npt) = dg.load_view(geo, pat, w, h)
                block(f"Loads - {pat}", f"Assigned loads in pattern {pat} ({na} areas, {nf} frames, {npt} points; "
                                        f"forces in {self.units['force']}, lengths in {self.units['length']}). "
                                        "Read by " + loads["method"] + ".", d)
        return out or intro

    def result_diagrams(self) -> list:
        from reportlab.platypus import KeepTogether
        r, geo = self.data.get("rdiag"), self.data.get("geometry")
        if not r or not geo:
            return []
        w = self.avail
        page_h = self.pagesize[1] - 2 * self.margin - 10 * self.mm
        h = min(w * 0.62, (page_h - 32 * self.mm) / 2) if self.orientation == "portrait" else \
            min(w * 0.62, page_h - 28 * self.mm)
        u = self.units
        intro = [self.para(f"Result diagrams - {r['case']}", "h1"),
                 self.para(f"Case / combination {r['case']}, read through the program's results API. Forces in "
                           f"{u['force']}, lengths in {u['length']}. Envelope combinations: the larger magnitude "
                           "of max and min at each point.", "note")]
        out: list = []

        def block(title: str, note: str, drawing) -> None:
            head = intro if not out else []
            out.append(KeepTogether(head + [self.para(title, "h2"), self.para(note, "note"), drawing]))
            self.figures += 1

        one_level = len({round(p["z"], 3) for p in geo["points"]}) <= 1
        if "disp" in r:
            d, umax = dg.deformed(geo, r["disp"], w, h, one_level)
            block("Deformed shape", f"Largest joint displacement {dg._fmt(umax)} {u['length']} (object joints; "
                                    "displacements magnified, see the scale).", d)
        if "react" in r:
            fz = sum(v[2] for v in r["react"].values())
            block("Support reactions", f"{len(r['react'])} joints with reactions; sum of F3 = {dg._fmt(fz)} "
                                       f"{u['force']}.", dg.reactions(geo, r["react"], w, h))
        if "forces" in r:
            comp = self.p.frame_component
            for title, axis, c, _ in dg.elevation_planes(geo, self.p.max_plans):
                d, vmax, what = dg.frame_diagram(geo, r["forces"], axis, c, w, h, comp, r.get("axes"),
                                                 style=self.style)
                if vmax:
                    moment = comp == "bending" or comp.startswith(("M", "T"))
                    note = (f"{what} at the output stations; end and peak values in {u['force']}"
                            + (f"-{u['length']}" if moment else "") + ". ")
                    if self.style == "etabs":
                        note += "Drawn as the program draws it by default: one colour, not filled. "
                    if comp == "bending":
                        note += ("In-plane bending of each member (M3 or M2 from its local axes), drawn toward its "
                                 "local -2 (M3) / -3 (M2) side: sagging of a beam with local 2 up plots below it.")
                    block(f"{what} - {title}", note, d)
        if r.get("shells"):
            unit = lambda c_: u["force"] + ("" if c_.startswith(("F", "V")) else f"-{u['length']}")  # noqa: E731
            stories = geo.get("stories") or []

            def level_name(z: float) -> str:
                return next((s_["name"] for s_ in stories if abs(s_["elevation"] - z) < 1e-3), f"Z = {dg._fmt(z)}")

            def contour(title: str, comp: str, els: list[dict], where: str) -> None:
                if max((abs(x) for e in els for x in e["vals"]), default=0.0) < 1e-6:
                    self.data["skipped"].append((f"{comp} contour, {where}",
                                                 f"{comp} is zero there (|value| < 1e-6, e.g. membrane decks)"))
                    return
                raw = [x for e in els for x in e["vals"]]
                avg = {"program_default": None, "joints": True, "none": False}[self.p.contour_averaging]
                d, (lo, hi) = dg.shell_contour(els, comp, w, h, averaged=avg, style=self.style)
                un = f"{unit(comp)}/{u['length']}"
                if avg is not None:
                    how = ((f"averaged at the joints (contour_averaging joints) from {dg._fmt(lo)} to {dg._fmt(hi)} "
                            f"{un}; unaveraged element values from {dg._fmt(min(raw))} to {dg._fmt(max(raw))} {un}. ")
                           if avg else f"not averaged (contour_averaging none), from {dg._fmt(lo)} to {dg._fmt(hi)} {un}. ")
                    how += "Colours and bands as the program's scale (15 bands of a round step from just below the minimum)."
                elif self.style == "sap":
                    how = (f"averaged at the joints (SAP2000's default display) from {dg._fmt(lo)} to {dg._fmt(hi)} "
                           f"{un}; unaveraged element values from {dg._fmt(min(raw))} to {dg._fmt(max(raw))} {un}. "
                           "Colours and bands as SAP2000's scale (15 bands of a round step from just below the minimum): "
                           "blue = most positive ... purple = most negative.")
                else:
                    how = (f"not averaged at the nodes (the program's default display), from {dg._fmt(lo)} to "
                           f"{dg._fmt(hi)} {un}. Colours and bands as the program's scale (15 bands of a round step from "
                           "just below the minimum): purple = most negative ... blue = most positive.")
                block(title, f"{len(els)} shell elements; {comp} {how}", d)

            slabs: dict[float, list[dict]] = defaultdict(list)
            walls: dict[tuple[str, float], list[dict]] = defaultdict(list)
            sc, wc = self.p.shell_component, self.p.wall_component
            for e in r["shells"]:
                xs, ys, zs = zip(*e["xyz"])
                if max(zs) - min(zs) < 1e-3 and sc in e["values"]:
                    slabs[round(sum(zs) / len(zs), 3)].append(
                        {"poly": [(x, y) for x, y, _ in e["xyz"]], "nodes": e["nodes"], "vals": e["values"][sc]})
                elif wc in e["values"] and max(xs) - min(xs) < 1e-3:
                    walls[("X", round(xs[0], 3))].append(
                        {"poly": [(y, z) for _, y, z in e["xyz"]], "nodes": e["nodes"], "vals": e["values"][wc]})
                elif wc in e["values"] and max(ys) - min(ys) < 1e-3:
                    walls[("Y", round(ys[0], 3))].append(
                        {"poly": [(x, z) for x, _, z in e["xyz"]], "nodes": e["nodes"], "vals": e["values"][wc]})
            for z in sorted(slabs, reverse=True)[:self.p.max_plans]:
                contour(f"{sc} contour - {level_name(z)}", sc, slabs[z], level_name(z))
            for (ax, c) in sorted(walls, key=lambda k_: -len(walls[k_]))[:self.p.max_plans]:
                where = f"wall plane {ax} = {dg._fmt(c)}"
                contour(f"{wc} contour - {where}", wc, walls[(ax, c)], where)
        elev = {s_["name"]: s_["elevation"] for s_ in geo.get("stories") or []}
        if r.get("drifts") and elev:
            rows = [{**x, "Elevation": elev[x["Story"]]} for x in r["drifts"] if x["Story"] in elev]
            block("Story drifts", "Largest drift ratio per story and direction"
                  + (f"; limit {self.p.drift_limit} given by the engineer." if self.p.drift_limit else "."),
                  dg.story_chart(rows, "Drift", "drift ratio", w, h * 0.8, self.p.drift_limit))
        if r.get("shears") and elev:
            rows = [{**x, "Elevation": elev[x["Story"]]} for x in r["shears"] if x["Story"] in elev]
            block("Story shears", f"Story shear at the bottom of each story (table 'Story Forces'), {u['force']}.",
                  dg.story_chart(rows, "Shear", f"story shear ({u['force']})", w, h * 0.8))
        return out

    def definitions(self) -> list:
        import pandas as pd
        d = self.data["definitions"]
        out = [self.para("Definitions", "h1")]
        for key, title in (("materials", "Materials"), ("frame_sections", "Frame sections"),
                           ("area_sections", "Area / slab sections"), ("load_patterns", "Load patterns"),
                           ("load_cases", "Load cases")):
            block = d.get(key, {})
            if not block.get("available", True):
                self.data["skipped"].append((title.lower(), block.get("error", "not available")))
                continue
            items = block.get("items", [])
            if items and isinstance(items[0], dict):
                df = pd.DataFrame(items).rename(columns={"name": "Name", "type": "Type"})
            else:
                df = _grid(items, 4)
            out += self.frame_table(df, title, "list_definitions")
        if self.data.get("combo_rows"):
            out += self.frame_table(pd.DataFrame(self.data["combo_rows"]), "Load combinations",
                                    "RespCombo.GetCaseList")
        return out

    def tables(self) -> list:
        out = [self.para("Database tables", "h1")]
        for key, df in self.data.get("tables", []):
            out += self.frame_table(df, key, f"database table '{key}'")
        return out

    def results(self) -> list:
        import pandas as pd
        res = self.data.get("results", {})
        if not res:
            return []
        out = [self.para("Analysis results", "h1"),
               self.para("Governing values found in the program's result tables for the selected cases and "
                         "combinations (modal rows are left out of base reactions).", "note")]
        if "base_reactions" in res and res["base_reactions"].get("summary"):
            s = res["base_reactions"]["summary"]
            rows = []
            for comp, g in s["governing"].items():
                mx, mn = g.get("max") or {}, g.get("min") or {}
                rows.append({"Component": comp, "Max": mx.get("value"), "Max case": _case(mx),
                             "Min": mn.get("value"), "Min case": _case(mn)})
            out += self.frame_table(pd.DataFrame(rows), "Base reactions: governing",
                                    f"table '{res['base_reactions']['table_key']}'")
            out += self.frame_table(pd.DataFrame(s.get("per_case", [])), "Base reactions per case",
                                    f"table '{res['base_reactions']['table_key']}'")
        if "story_drifts" in res and res["story_drifts"].get("summary"):
            s = res["story_drifts"]["summary"]
            g = s.get("governing") or {}
            out += [self.para("Story drifts", "h2"),
                    self.para(f"Governing drift ratio {_fmt(g.get('value'))} at {g.get('Story', '')}, "
                              f"{g.get('Direction', '')} direction, {_case(g)}.", "body")]
            env = pd.DataFrame(s.get("story_envelope", [])).rename(columns={"story": "Story", "max_drift": "Max drift"})
            out += self.frame_table(env, "Story drift envelope",
                                    f"table '{res['story_drifts']['table_key']}'")
        if "modal_periods" in res and res["modal_periods"].get("summary"):
            for case, m in res["modal_periods"]["summary"].items():
                if case.startswith("_"):
                    continue
                dom = ", ".join(f"{k}: mode {v.get('mode')} (T = {_fmt(v.get('period'))} s, {_fmt(v.get('ratio'))})"
                                for k, v in (m.get("dominant_modes") or {}).items())
                cum = ", ".join(f"{k} {_fmt(v.get('at_last_mode'))}" + (
                    f" (90% at mode {v.get('mode_reaching_90pct')})" if v.get("mode_reaching_90pct") else "")
                    for k, v in (m.get("cumulative_mass") or {}).items())
                out += [self.para(f"Modal case {case}", "h2"),
                        self.para(f"{m.get('number_of_modes')} modes; fundamental period "
                                  f"{_fmt(m.get('fundamental_period'))} s. Dominant: {dom or '-'}. "
                                  f"Cumulative mass: {cum or '-'}.", "body")]
                out += self.frame_table(pd.DataFrame(m.get("modes", [])), f"Modes ({case})",
                                        f"table '{res['modal_periods']['table_key']}'")
        if "joint_displacements" in res and res["joint_displacements"].get("summary"):
            s = res["joint_displacements"]["summary"]
            rows = []
            for comp, g in s["governing"].items():
                a = g.get("abs_max") or {}
                rows.append({"Component": comp, "Max |value|": a.get("value"), "Where": _where(a), "Case": _case(a)})
            out += self.frame_table(pd.DataFrame(rows), "Joint displacements: governing",
                                    f"table '{res['joint_displacements']['table_key']}'")
        return out

    def design(self) -> list:
        import pandas as pd
        from reportlab.platypus import KeepTogether
        out: list = []
        geo = self.data.get("geometry")
        w = self.avail
        page_h = self.pagesize[1] - 2 * self.margin - 10 * self.mm
        h = min(w * 0.62, (page_h - 32 * self.mm) / 2)
        for res in self.data.get("design", []):
            kind = res["kind"].replace("_", " ")
            st = res.get("settings", {})
            combos = res.get("combinations") or st.get("combinations") or {}
            out += [self.para(f"Design - {kind}", "h1"),
                    self.para(f"Design by {self.program}'s own engine. Code: {res.get('code') or 'as in the model'}. "
                              "Strength combinations: " + (", ".join(combos.get("strength", [])) or "-")
                              + (". Deflection combinations: " + ", ".join(combos["deflection"])
                                 if combos.get("deflection") else "") + f". Units: {self.units_text}. Code, "
                              "combinations and preferences are the model's / the designer's.", "note")]
            prefs = st.get("preferences")
            if isinstance(prefs, dict) and prefs:
                out += self.frame_table(pd.DataFrame([{"Preference": k, "Value": v} for k, v in prefs.items()
                                                      if k not in ("GUID",)]),
                                        f"{kind.capitalize()} design preferences", f"table '{st.get('preferences_table')}'")
            ratios = res.get("ratios") or {}
            if ratios:
                n_fail = sum(1 for r in ratios.values() if r > 1.0)
                rmax = max(ratios.values())
                gov = max(ratios, key=ratios.get)
                out.append(self.para(f"{len(ratios)} members with a D/C ratio; largest {rmax:.3f} ({gov}); "
                                     f"{n_fail} above 1.0.", "body"))
            if res.get("steel"):
                df = pd.DataFrame(res["steel"])
                out += self.frame_table(df.sort_values("Ratio", ascending=False), "Steel design summary (all members, "
                                        "highest ratio first)", "DesignSteel.GetSummaryResults")
                bad = df[(df["Ratio"] > 1.0) | (df["Error"] != "")]
                if len(bad):
                    out += self.frame_table(bad, "Members over 1.0 or with design errors",
                                            "DesignSteel.GetSummaryResults")
            if res.get("beams"):
                df = pd.DataFrame(res["beams"])
                gov = df.loc[df.groupby("Frame")["BotArea"].idxmax()] if len(df) else df
                out += self.frame_table(gov, "Concrete beams: station of the largest bottom steel per beam "
                                        f"(areas in {self.units['length']}2, shear per length)",
                                        "DesignConcrete.GetSummaryResultsBeam")
            if res.get("columns"):
                df = pd.DataFrame(res["columns"])
                gov = df.loc[df.groupby("Frame")["PMMRatio" if df["PMMRatio"].max() > 0 else "PMMArea"].idxmax()]
                out += self.frame_table(gov, "Concrete columns: governing station per column",
                                        "DesignConcrete.GetSummaryResultsColumn")
            for key, title in (("strips", "Slab strip design"), ("punching", "Punching shear checks"),
                               ("piers", "Shear wall pier design")):
                if res.get(key):
                    out += self.frame_table(pd.DataFrame(res[key]), title, f"{self.program} design summary")
            pun = {f"Point {int(float(r['Point']))}": float(r["Ratio"]) for r in res.get("punching") or []
                   if r.get("table", "").endswith("Punching Shear Data") and isinstance(r.get("Ratio"), float)}
            if pun:
                out.append(KeepTogether([self.para("Punching shear D/C ratios", "h2"),
                                         self.para("The highest ratios at columns / points; dashed line at 1.0.",
                                                   "note"), dg.ratio_bars(pun, w, h * 0.9)]))
                self.figures += 1
            if ratios and geo:
                def block(title: str, note: str, drawing) -> None:
                    out.append(KeepTogether([self.para(title, "h2"), self.para(note, "note"), drawing]))
                    self.figures += 1
                block(f"D/C ratios - 3-D ({kind})", "Each member coloured by its governing ratio.",
                      dg.ratio_overview(geo, ratios, w, h))
                block("Highest D/C ratios", "The 25 highest governing ratios; dashed line at 1.0.",
                      dg.ratio_bars(ratios, w, h * 0.9))
                block("Members per D/C band", f"{len(ratios)} members.", dg.ratio_histogram(ratios, w, h * 0.6))
                for title, axis, c, _ in dg.elevation_planes(geo, self.p.max_plans):
                    names = {f["name"] for f in geo["frames"]
                             if abs(dg._p1(f)[0 if axis == "x" else 1] - c) < dg.TOL
                             and abs(dg._p2(f)[0 if axis == "x" else 1] - c) < dg.TOL}
                    if names & set(ratios):
                        block(f"D/C ratios - {title}", "Ratio labelled on each designed member.",
                              dg.ratio_elevation(geo, ratios, axis, c, w, h))
        return out

    def skipped(self) -> list:
        import pandas as pd
        if not self.data["skipped"]:
            return []
        return [self.para("Not included", "h1")] + self.frame_table(
            pd.DataFrame([{"Item": a, "Reason": b} for a, b in self.data["skipped"]]), "Items not in this report",
            "export_report")

    def story(self) -> list:
        story: list = []
        for name in self.data["sections"]:
            story += getattr(self, name)()
        return story + self.skipped()

    def meta(self) -> dict[str, Any]:
        return {"title": self.title, "program": self.program, "model_file": self.model_file,
                "units_text": self.units_text, "generated": self.generated, "pagesize": tuple(self.pagesize),
                "margin": self.margin, "landscape": self.orientation == "landscape"}

    def build(self, destination: Path) -> None:
        from reportlab.platypus import SimpleDocTemplate
        story = self.story()
        doc = SimpleDocTemplate(str(destination), pagesize=self.pagesize, leftMargin=self.margin,
                                rightMargin=self.margin, topMargin=self.margin + 4, bottomMargin=self.margin + 6,
                                title=self.title, author="CSI MCP", subject=f"{self.program} - {self.model_file}")
        doc.build(story, onFirstPage=self._page, onLaterPages=self._page)


def _is_num(text: str) -> bool:
    try:
        float(text)
        return True
    except ValueError:
        return False


def _grid(names: list[str], cols: int):
    import pandas as pd
    rows = [names[i:i + cols] + [""] * (cols - len(names[i:i + cols])) for i in range(0, len(names), cols)]
    return pd.DataFrame(rows, columns=[f"Name {i + 1}" for i in range(cols)])


def _case(g: dict[str, Any]) -> str:
    return " ".join(str(g.get(k)) for k in ("OutputCase", "Case", "StepType") if g.get(k))


def _where(g: dict[str, Any]) -> str:
    return " ".join(f"{k} {g[k]}" for k in ("Story", "Label", "Joint", "Node", "UniqueName") if g.get(k))


def _target(p: ReportP) -> Path:
    st = _status_core(p.software, counts=False)
    model = st.get("model_file")
    return safety.output_target(p.output_path, "." + p.format, p.overwrite, "reports",
                                Path(model or f"{p.software}_model").stem, model, TableError)


@action("export_report", "Report of the model and its results built from the program's data, as PDF, Word (.docx) or "
        "RTF: cover, model (counts, case status), diagrams (3-D overview, plans, elevations, load views per pattern), "
        "definitions (materials, sections, patterns, cases, combinations), chosen database tables and headline "
        "results, result diagrams and design. A4 portrait unless page_size / orientation say otherwise; long tables "
        "also go to CSV. Writes only the report / CSV files; the model is not changed.", "Output", "read", ReportP,
        long_running=True, options={"result_diagrams": {"story_charts": "E"},
                                    "design_kinds": {"concrete_slab": "EF", "shear_wall": "E"}},
        fields={"drift_limit": "E"})
def export_report(p: ReportP) -> dict[str, Any]:
    path = _target(p)
    data = gather(p)
    pdf = _Pdf(p, data, path)
    extra: dict[str, Any] = {}
    try:
        with safety.staged_file(path, p.overwrite) as tmp:
            if p.format == "pdf":
                pdf.build(tmp)
            else:
                import doc_export
                bl = doc_export.blocks(pdf.story())
                writer = doc_export.write_docx if p.format == "docx" else doc_export.write_rtf
                extra = writer(bl, pdf.meta(), tmp)
    except PermissionError as exc:
        raise TableError(f"Cannot write {path}: {exc}", hint="Close the file if it is open (viewer or Word).") from exc
    return {"report": str(path), "format": p.format,
            "pages": pdf.pages if p.format == "pdf" else "set by Word when the file is opened",
            "page_size": p.page_size, "orientation": pdf.orientation,
            "sections": data["sections"], "skipped": [{"item": a, "reason": b} for a, b in data["skipped"]],
            "figures": pdf.figures, "csv_appendices": pdf.csv_files, "units": describe_units(p.units),
            "tables": [{"key": k, "rows": len(df)} for k, df in data.get("tables", [])], **extra}
