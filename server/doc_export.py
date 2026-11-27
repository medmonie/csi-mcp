"""Word (.docx) and RTF output of export_report (0.20.0).

The report is built once, as for the PDF (the same sections, tables and diagrams), and its content is written to a
Word document: headings, notes, tables (header row repeated on every page, as in the PDF) and the diagrams as PNG
pictures, with the report's page size, orientation, margins, header (title, program) and footer (model, units, page
number). Word paginates the document itself.

No extra packages: the .docx is written as Office Open XML (zip) and the RTF as text; the diagrams (reportlab
drawings of lines, polylines, polygons, rectangles, circles and strings) are rasterised with Pillow, which reportlab
already needs.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

PX_PER_PT = 2.0          # picture resolution: 144 dpi
SUPER = 2                # drawn at twice that, then reduced (smooth edges)
EMU_PER_PT = 12700
TWIP_PER_PT = 20


# --------------------------------------------------------------------------- diagrams to PNG

def _rgb(c: Any) -> tuple[int, int, int] | None:
    if c is None:
        return None
    if getattr(c, "alpha", 1) == 0:
        return None
    return int(round(c.red * 255)), int(round(c.green * 255)), int(round(c.blue * 255))


def _font(size_px: float):
    from PIL import ImageFont
    for name in ("arial.ttf", "Arial.ttf", "DejaVuSans.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, max(1, int(round(size_px))))
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=max(1, int(round(size_px))))
    except TypeError:
        return ImageFont.load_default()


def drawing_png(d: Any) -> tuple[bytes, float, float]:
    """A reportlab Drawing as PNG bytes, with its size in points."""
    from PIL import Image, ImageDraw
    from reportlab.graphics.shapes import Circle, Line, PolyLine, Polygon, Rect, String

    w, h = float(d.width), float(d.height)
    k = PX_PER_PT * SUPER
    img = Image.new("RGB", (max(1, int(w * k)), max(1, int(h * k))), (255, 255, 255))
    dr = ImageDraw.Draw(img)

    def xy(x: float, y: float) -> tuple[float, float]:
        return x * k, (h - y) * k

    def width(s: Any) -> int:
        return max(1, int(round(float(getattr(s, "strokeWidth", 1) or 1) * k)))

    def shapes(node: Any):
        for s in getattr(node, "contents", []):
            if hasattr(s, "contents") and not isinstance(s, (String,)):
                yield from shapes(s)
            else:
                yield s

    for s in shapes(d):
        if isinstance(s, Line):
            col = _rgb(s.strokeColor)
            if col:
                dr.line([xy(s.x1, s.y1), xy(s.x2, s.y2)], fill=col, width=width(s))
        elif isinstance(s, (Polygon, PolyLine)):
            pts = list(s.points)
            pp = [xy(pts[i], pts[i + 1]) for i in range(0, len(pts) - 1, 2)]
            if len(pp) < 2:
                continue
            if isinstance(s, Polygon):
                fill, line = _rgb(s.fillColor), _rgb(s.strokeColor)
                if fill and len(pp) >= 3:
                    dr.polygon(pp, fill=fill)
                if line and getattr(s, "strokeWidth", 1):
                    dr.line(pp + [pp[0]], fill=line, width=width(s))
            else:
                line = _rgb(s.strokeColor)
                if line:
                    dr.line(pp, fill=line, width=width(s))
        elif isinstance(s, Rect):
            (x0, y1), (x1, y0) = xy(s.x, s.y), xy(s.x + s.width, s.y + s.height)
            box = [min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)]
            fill, line = _rgb(s.fillColor), _rgb(s.strokeColor)
            dr.rectangle(box, fill=fill, outline=line if line and getattr(s, "strokeWidth", 1) else None,
                         width=width(s))
        elif isinstance(s, Circle):
            cx, cy = xy(s.cx, s.cy)
            r = s.r * k
            fill, line = _rgb(s.fillColor), _rgb(s.strokeColor)
            dr.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill, outline=line, width=width(s))
        elif isinstance(s, String):
            col = _rgb(s.fillColor) or (0, 0, 0)
            anchor = {"start": "ls", "middle": "ms", "end": "rs"}.get(getattr(s, "textAnchor", "start"), "ls")
            dr.text(xy(s.x, s.y), str(s.text), fill=col, font=_font(float(s.fontSize) * k), anchor=anchor)
    img = img.resize((max(1, int(w * PX_PER_PT)), max(1, int(h * PX_PER_PT))), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, "PNG", optimize=True)
    return buf.getvalue(), w, h


# --------------------------------------------------------------------------- story to blocks

def _text(cell: Any) -> str:
    if cell is None:
        return ""
    if isinstance(cell, str):
        return cell
    if hasattr(cell, "getPlainText"):
        return cell.getPlainText()
    if isinstance(cell, (list, tuple)):
        return " ".join(_text(c) for c in cell)
    return str(cell)


def blocks(story: list) -> list[dict[str, Any]]:
    """The report's flowables as neutral blocks: para (style), table, image, page_break."""
    from reportlab.graphics.shapes import Drawing
    from reportlab.platypus import KeepTogether, PageBreak, Paragraph, Spacer, Table

    style_of = {"t": "title", "h1": "h1", "h2": "h2", "b": "body", "n": "note", "ch": "body"}
    out: list[dict[str, Any]] = []

    def walk(items: list) -> None:
        for f in items:
            if isinstance(f, KeepTogether):
                start = len(out)
                walk(list(getattr(f, "_content", [])))
                group = out[start:]
                if group and group[-1]["kind"] == "image" and all(b["kind"] == "para" for b in group[:-1]):
                    # a figure with its heading / note: one block that is never split over two pages
                    del out[start:]
                    out.append({"kind": "figure", "paras": group[:-1], "image": group[-1]})
            elif isinstance(f, PageBreak):
                out.append({"kind": "page_break"})
            elif isinstance(f, Spacer):
                continue
            elif isinstance(f, Paragraph):
                out.append({"kind": "para", "style": style_of.get(f.style.name, "body"), "text": f.getPlainText()})
            elif isinstance(f, Table):
                rows = [[_text(c) for c in r] for r in f._cellvalues]
                widths = [float(w) if isinstance(w, (int, float)) else None for w in (f._colWidths or [])]
                header = int(getattr(f, "repeatRows", 0) or 0)
                kv = header == 0 and len(rows[0]) == 2 if rows else False
                out.append({"kind": "table", "rows": rows, "widths": widths, "header_rows": header,
                            "key_value": kv})
            elif isinstance(f, Drawing):
                png, w, h = drawing_png(f)
                out.append({"kind": "image", "png": png, "w": w, "h": h})
            elif hasattr(f, "getPlainText"):
                out.append({"kind": "para", "style": "body", "text": f.getPlainText()})
    walk(story)
    return out


# --------------------------------------------------------------------------- DOCX

STYLES = {"title": ("Title", 22, True, "000000"), "h1": ("Heading1", 14, True, "1F3864"),
          "h2": ("Heading2", 11, True, "1F3864"), "body": ("Normal", 9, False, "000000"),
          "note": ("Note", 7.5, False, "555555"), "cell": ("TableText", 7, False, "000000")}


def _run(text: str, size: float | None = None, bold: bool = False, color: str | None = None) -> str:
    props = ""
    if bold:
        props += "<w:b/>"
    if color:
        props += f'<w:color w:val="{color}"/>'
    if size:
        props += f'<w:sz w:val="{int(round(size * 2))}"/>'
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    parts = str(text).split("\n")
    body = '<w:br/>'.join(f'<w:t xml:space="preserve">{escape(p)}</w:t>' for p in parts)
    return f"<w:r>{rpr}{body}</w:r>"


def _styles_xml() -> str:
    out = []
    for key, (sid, size, bold, color) in STYLES.items():
        base = "" if sid == "Normal" else '<w:basedOn w:val="Normal"/>'
        level = {"Heading1": 0, "Heading2": 1}.get(sid)
        ppr = (f"<w:pPr><w:keepNext/><w:spacing w:before=\"{160 if level == 0 else 120}\" w:after=\"80\"/>"
               f"<w:outlineLvl w:val=\"{level}\"/></w:pPr>") if level is not None else \
            ('<w:pPr><w:spacing w:after="60"/></w:pPr>' if sid in ("Normal", "Note") else
             '<w:pPr><w:spacing w:after="0"/></w:pPr>' if sid == "TableText" else
             '<w:pPr><w:spacing w:before="1200" w:after="240"/></w:pPr>')
        rpr = (f'<w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>{"<w:b/>" if bold else ""}'
               f'<w:color w:val="{color}"/><w:sz w:val="{int(size * 2)}"/></w:rPr>')
        name = {"Heading1": "heading 1", "Heading2": "heading 2", "Normal": "Normal", "Title": "Title"}.get(sid, sid)
        out.append(f'<w:style w:type="paragraph" w:styleId="{sid}"{" w:default=\"1\"" if sid == "Normal" else ""}>'
                   f'<w:name w:val="{name}"/>{base}<w:qFormat/>{ppr}{rpr}</w:style>')
    table = ('<w:style w:type="table" w:styleId="ReportTable"><w:name w:val="Report Table"/><w:tblPr>'
             '<w:tblBorders>' + "".join(f'<w:{b} w:val="single" w:sz="2" w:space="0" w:color="BBBBBB"/>'
                                       for b in ("top", "left", "bottom", "right", "insideH", "insideV")) +
             '</w:tblBorders><w:tblCellMar><w:left w:w="57" w:type="dxa"/><w:right w:w="57" w:type="dxa"/>'
             '</w:tblCellMar></w:tblPr></w:style>')
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:cs="Arial"/>'
            '<w:sz w:val="18"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault></w:docDefaults>'
            + "".join(out) + table + '</w:styles>')


def _cell_xml(text: str, width_tw: int, header: bool, shade: bool) -> str:
    sh = '<w:shd w:val="clear" w:color="auto" w:fill="E8E8E8"/>' if shade else ""
    return (f'<w:tc><w:tcPr><w:tcW w:w="{width_tw}" w:type="dxa"/>{sh}</w:tcPr>'
            f'<w:p><w:pPr><w:pStyle w:val="TableText"/></w:pPr>{_run(text, bold=header)}</w:p></w:tc>')


def _table_xml(b: dict[str, Any], avail_tw: int) -> str:
    rows = b["rows"]
    n = max(len(r) for r in rows)
    widths = [w for w in b["widths"] if w] if b["widths"] else []
    if len(widths) == n:
        tw = [int(w * TWIP_PER_PT) for w in widths]
        total = sum(tw)
        if total > avail_tw:
            tw = [int(t * avail_tw / total) for t in tw]
    else:
        tw = [avail_tw // n] * n
    grid = "".join(f'<w:gridCol w:w="{t}"/>' for t in tw)
    out = [f'<w:tbl><w:tblPr><w:tblStyle w:val="ReportTable"/><w:tblW w:w="{sum(tw)}" w:type="dxa"/>'
           f'<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>']
    for i, r in enumerate(rows):
        head = i < b["header_rows"]
        trpr = "<w:trPr><w:tblHeader/><w:cantSplit/></w:trPr>" if head else "<w:trPr><w:cantSplit/></w:trPr>"
        cells = "".join(_cell_xml(r[j] if j < len(r) else "", tw[j], head or (b["key_value"] and j == 0),
                                  head or (b["key_value"] and j == 0)) for j in range(n))
        out.append(f"<w:tr>{trpr}{cells}</w:tr>")
    out.append("</w:tbl>")
    out.append('<w:p><w:pPr><w:spacing w:after="80"/></w:pPr></w:p>')
    return "".join(out)


def _image_xml(rid: str, idx: int, w: float, h: float, avail_pt: float) -> str:
    if w > avail_pt:
        h, w = h * avail_pt / w, avail_pt
    cx, cy = int(w * EMU_PER_PT), int(h * EMU_PER_PT)
    return ('<w:p><w:pPr><w:keepNext/><w:spacing w:after="120"/></w:pPr><w:r><w:drawing>'
            f'<wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/>'
            f'<wp:docPr id="{idx}" name="Figure {idx}"/>'
            '<a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
            '<a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            '<pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">'
            f'<pic:nvPicPr><pic:cNvPr id="{idx}" name="figure{idx}.png"/><pic:cNvPicPr/></pic:nvPicPr>'
            f'<pic:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill>'
            f'<pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
            '<a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic>'
            '</wp:inline></w:drawing></w:r></w:p>')


NS = ('xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
      'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
      'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
      'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"')


def write_docx(bl: list[dict[str, Any]], meta: dict[str, Any], path: Path) -> dict[str, int]:
    """meta: title, program, model_file, units_text, generated, pagesize (w, h points), margin (points),
    landscape (bool)."""
    pw, ph = meta["pagesize"]
    m = meta["margin"]
    avail_pt = pw - 2 * m
    avail_tw = int(avail_pt * TWIP_PER_PT)
    body: list[str] = []
    media: list[tuple[str, bytes]] = []
    n_tables = 0
    for b in bl:
        if b["kind"] == "para":
            sid = STYLES[b["style"]][0] if b["style"] in STYLES else "Normal"
            keep = "<w:keepNext/>" if b.get("keep") else ""
            body.append(f'<w:p><w:pPr><w:pStyle w:val="{sid}"/>{keep}</w:pPr>{_run(b["text"])}</w:p>')
        elif b["kind"] == "page_break":
            body.append('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
        elif b["kind"] == "table" and b["rows"]:
            body.append(_table_xml(b, avail_tw))
            n_tables += 1
        elif b["kind"] == "image":
            idx = len(media) + 1
            media.append((f"image{idx}.png", b["png"]))
            body.append(_image_xml(f"rIdImg{idx}", idx, b["w"], b["h"], avail_pt))
        elif b["kind"] == "figure":
            # one borderless table row that cannot split: Word and LibreOffice keep the heading, note and picture
            # on one page (keepNext alone is not honoured for a picture that does not fit - measured, LibreOffice)
            idx = len(media) + 1
            im = b["image"]
            media.append((f"image{idx}.png", im["png"]))
            inner = "".join(f'<w:p><w:pPr><w:pStyle w:val="{STYLES[q["style"]][0] if q["style"] in STYLES else "Normal"}"'
                            f'/></w:pPr>{_run(q["text"])}</w:p>' for q in b["paras"])
            pic = _image_xml(f"rIdImg{idx}", idx, im["w"], im["h"], avail_pt - 8)
            body.append(f'<w:tbl><w:tblPr><w:tblW w:w="{avail_tw}" w:type="dxa"/><w:tblLayout w:type="fixed"/>'
                        '<w:tblCellMar><w:left w:w="0" w:type="dxa"/><w:right w:w="0" w:type="dxa"/></w:tblCellMar>'
                        f'</w:tblPr><w:tblGrid><w:gridCol w:w="{avail_tw}"/></w:tblGrid><w:tr><w:trPr><w:cantSplit/>'
                        f'</w:trPr><w:tc><w:tcPr><w:tcW w:w="{avail_tw}" w:type="dxa"/></w:tcPr>{inner}{pic}</w:tc>'
                        '</w:tr></w:tbl><w:p><w:pPr><w:spacing w:after="60"/></w:pPr></w:p>')
    tw = lambda v: int(round(v * TWIP_PER_PT))  # noqa: E731
    sect = (f'<w:sectPr><w:headerReference w:type="default" r:id="rIdHeader"/>'
            f'<w:footerReference w:type="default" r:id="rIdFooter"/>'
            f'<w:pgSz w:w="{tw(pw)}" w:h="{tw(ph)}"{" w:orient=\"landscape\"" if meta["landscape"] else ""}/>'
            f'<w:pgMar w:top="{tw(m + 12)}" w:right="{tw(m)}" w:bottom="{tw(m + 18)}" w:left="{tw(m)}" '
            f'w:header="{tw(m - 14)}" w:footer="{tw(m - 22)}" w:gutter="0"/></w:sectPr>')
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {NS}><w:body>'
                + "".join(body) + sect + '</w:body></w:document>')
    right_tab = f'<w:tabs><w:tab w:val="right" w:pos="{avail_tw}"/></w:tabs>'
    header = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:hdr {NS}><w:p><w:pPr>{right_tab}'
              '<w:pBdr><w:bottom w:val="single" w:sz="4" w:space="1" w:color="999999"/></w:pBdr></w:pPr>'
              f'{_run(meta["title"][:90], 8, True)}<w:r><w:rPr><w:sz w:val="16"/></w:rPr><w:tab/></w:r>'
              f'{_run(meta["program"], 8)}</w:p></w:hdr>')
    model = meta["model_file"] if len(meta["model_file"]) <= 110 else "..." + meta["model_file"][-107:]
    footer = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr {NS}><w:p><w:pPr>{right_tab}'
              '<w:pBdr><w:top w:val="single" w:sz="4" w:space="1" w:color="999999"/></w:pBdr></w:pPr>'
              f'{_run("Model: " + model, 7)}<w:r><w:rPr><w:sz w:val="14"/></w:rPr><w:tab/></w:r>'
              f'{_run("Page ", 7)}<w:r><w:rPr><w:sz w:val="14"/></w:rPr><w:fldChar w:fldCharType="begin"/></w:r>'
              '<w:r><w:rPr><w:sz w:val="14"/></w:rPr><w:instrText xml:space="preserve"> PAGE </w:instrText></w:r>'
              '<w:r><w:rPr><w:sz w:val="14"/></w:rPr><w:fldChar w:fldCharType="separate"/></w:r>'
              f'{_run("1", 7)}<w:r><w:rPr><w:sz w:val="14"/></w:rPr><w:fldChar w:fldCharType="end"/></w:r></w:p>'
              f'<w:p>{_run("Units: " + meta["units_text"] + "  |  Generated " + meta["generated"] + " by CSI MCP", 7)}'
              '</w:p></w:ftr>')
    rels = ['<Relationship Id="rIdStyles" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'styles" Target="styles.xml"/>',
            '<Relationship Id="rIdHeader" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'header" Target="header1.xml"/>',
            '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'footer" Target="footer1.xml"/>']
    rels += [f'<Relationship Id="rIdImg{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
             f'relationships/image" Target="media/{name}"/>' for i, (name, _) in enumerate(media, 1)]
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Default Extension="png" ContentType="image/png"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
                   'officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-'
                   'officedocument.wordprocessingml.styles+xml"/>'
                   '<Override PartName="/word/header1.xml" ContentType="application/vnd.openxmlformats-'
                   'officedocument.wordprocessingml.header+xml"/>'
                   '<Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-'
                   'officedocument.wordprocessingml.footer+xml"/>'
                   '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.'
                   'core-properties+xml"/></Types>')
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
                   'relationships/officeDocument" Target="word/document.xml"/>'
                   '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/'
                   'metadata/core-properties" Target="docProps/core.xml"/></Relationships>')
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/'
                   'core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/'
                   'dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">'
                   f'<dc:title>{escape(meta["title"])}</dc:title><dc:creator>CSI MCP</dc:creator>'
                   f'<dc:subject>{escape(meta["program"] + " - " + meta["model_file"])}</dc:subject>'
                   f'<dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created></cp:coreProperties>')
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(rels) + '</Relationships>')
        z.writestr("word/document.xml", document)
        z.writestr("word/styles.xml", _styles_xml())
        z.writestr("word/header1.xml", header)
        z.writestr("word/footer1.xml", footer)
        for name, data in media:
            z.writestr(f"word/media/{name}", data)
    return {"figures": len(media), "tables": n_tables}


# --------------------------------------------------------------------------- RTF

def _rtf(text: str) -> str:
    out = []
    for ch in str(text):
        o = ord(ch)
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\line ")
        elif o < 128:
            out.append(ch)
        else:
            encoded = ch.encode("utf-16-le", errors="surrogatepass")
            for i in range(0, len(encoded), 2):
                out.append(f"\\u{int.from_bytes(encoded[i:i+2], 'little', signed=True)}?")
    return "".join(out)


RTF_STYLE = {"title": (44, True, 0), "h1": (28, True, 2), "h2": (22, True, 2), "body": (18, False, 0),
             "note": (15, False, 1)}


def write_rtf(bl: list[dict[str, Any]], meta: dict[str, Any], path: Path) -> dict[str, int]:
    pw, ph = meta["pagesize"]
    m = meta["margin"]
    tw = lambda v: int(round(v * TWIP_PER_PT))  # noqa: E731
    avail_pt = pw - 2 * m
    parts = ["{\\rtf1\\ansi\\ansicpg1252\\deff0{\\fonttbl{\\f0\\fswiss Arial;}}",
             "{\\colortbl;\\red0\\green0\\blue0;\\red85\\green85\\blue85;\\red31\\green56\\blue100;"
             "\\red232\\green232\\blue232;\\red187\\green187\\blue187;}",
             f"{{\\info{{\\title {_rtf(meta['title'])}}}{{\\author CSI MCP}}}}",
             f"\\paperw{tw(pw)}\\paperh{tw(ph)}\\margl{tw(m)}\\margr{tw(m)}\\margt{tw(m + 12)}\\margb{tw(m + 18)}"
             f"\\headery{tw(m - 14)}\\footery{tw(m - 22)}" + ("\\landscape" if meta["landscape"] else ""),
             f"{{\\header\\pard\\plain\\f0\\fs16\\tqr\\tx{tw(avail_pt)}\\b {_rtf(meta['title'][:90])}\\b0\\tab "
             f"{_rtf(meta['program'])}\\par}}",
             f"{{\\footer\\pard\\plain\\f0\\fs14\\tqr\\tx{tw(avail_pt)} Model: {_rtf(meta['model_file'][-110:])}"
             f"\\tab Page \\chpgn\\par Units: {_rtf(meta['units_text'])}  |  Generated {meta['generated']} by CSI "
             "MCP\\par}"]
    figures = tables = 0
    for b in bl:
        if b["kind"] == "para":
            size, bold, color = RTF_STYLE.get(b["style"], RTF_STYLE["body"])
            keep = "\\keepn" if b["style"] in ("h1", "h2") or b.get("keep") else ""
            space = "\\sb240\\sa120" if b["style"] in ("h1", "h2") else "\\sa80"
            parts.append(f"\\pard\\plain\\f0{keep}{space}\\fs{size}\\cf{color}{'\\b' if bold else ''} "
                         f"{_rtf(b['text'])}\\par")
        elif b["kind"] == "page_break":
            parts.append("\\pard\\page")
        elif b["kind"] == "table" and b["rows"]:
            rows = b["rows"]
            n = max(len(r) for r in rows)
            widths = [w for w in b["widths"] if w] if b["widths"] else []
            if len(widths) != n:
                widths = [avail_pt / n] * n
            total = sum(widths)
            if total > avail_pt:
                widths = [w * avail_pt / total for w in widths]
            for i, r in enumerate(rows):
                head = i < b["header_rows"]
                row = ["\\trowd\\trgaph57\\trleft0" + ("\\trhdr" if head else "") + "\\trkeep"]
                x = 0.0
                for j in range(n):
                    x += widths[j]
                    shade = head or (b["key_value"] and j == 0)
                    row.append("\\clbrdrt\\brdrs\\brdrw5\\brdrcf5\\clbrdrl\\brdrs\\brdrw5\\brdrcf5"
                               "\\clbrdrb\\brdrs\\brdrw5\\brdrcf5\\clbrdrr\\brdrs\\brdrw5\\brdrcf5"
                               + ("\\clcbpat4" if shade else "") + f"\\cellx{tw(x)}")
                row.append("\\pard\\plain\\intbl\\f0\\fs14 ")
                for j in range(n):
                    shade = head or (b["key_value"] and j == 0)
                    txt = _rtf(r[j] if j < len(r) else "")
                    row.append(("{\\b " + txt + "}" if shade else txt) + "\\cell ")
                row.append("\\row")
                parts.append("".join(row))
            parts.append("\\pard\\plain\\f0\\fs8\\par")
            tables += 1
        elif b["kind"] in ("image", "figure"):
            im = b["image"] if b["kind"] == "figure" else b
            w, h = im["w"], im["h"]
            if w > avail_pt - 8:
                h, w = h * (avail_pt - 8) / w, avail_pt - 8
            from PIL import Image
            px_w, px_h = Image.open(io.BytesIO(im["png"])).size
            pict = (f"{{\\pict\\pngblip\\picw{px_w}\\pich{px_h}\\picwgoal{tw(w)}\\pichgoal{tw(h)}\n"
                    + im["png"].hex() + "}")
            if b["kind"] == "figure":
                # one table row that cannot split (\\trkeep): heading, note and picture stay on one page
                cell = []
                for q in b["paras"]:
                    size, bold, color = RTF_STYLE.get(q["style"], RTF_STYLE["body"])
                    cell.append(f"\\pard\\plain\\intbl\\f0\\sa80\\fs{size}\\cf{color}{'\\b' if bold else ''} "
                                f"{_rtf(q['text'])}\\par")
                cell.append(f"\\pard\\plain\\intbl\\f0 {pict}\\cell")
                parts.append(f"\\trowd\\trgaph0\\trleft0\\trkeep\\cellx{tw(avail_pt)}" + "".join(cell) + "\\row")
                parts.append("\\pard\\plain\\f0\\fs8\\par")
            else:
                parts.append(f"\\pard\\plain\\keepn\\sa120{pict}\\par")
            figures += 1
    parts.append("}")
    path.write_text("\n".join(parts), encoding="ascii", errors="replace")
    return {"figures": figures, "tables": tables}
