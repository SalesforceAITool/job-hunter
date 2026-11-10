"""Word (.docx) version of Akshay's original resume layout (same content as classic.py).

    python classic_docx.py out.docx
"""
import html as htmllib
import re
import sys

from docx import Document
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor, Inches

import orig_content as O
from classic import BOLD_PREFIXES, LEAD_BOLD, NAME

CG, CAL = "Century Gothic", "Calibri"


def _font(run, name, size, bold=False, underline=False, color=None):
    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    run.underline = underline
    rpr = run._element.get_or_add_rPr()
    rfonts = rpr.find(qn("w:rFonts"))
    if rfonts is None:
        rfonts = OxmlElement("w:rFonts")
        rpr.append(rfonts)
    for a in ("w:ascii", "w:hAnsi", "w:cs", "w:eastAsia"):
        rfonts.set(qn(a), name)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)
    return run


def _plain(html):
    """Our content strings carry a little reportlab markup; turn it into [(text, bold, underline)]."""
    html = html.replace("&mdash;", "—").replace("&ndash;", "–").replace("&nbsp;", " ")
    html = re.sub(r"<a [^>]*>(.*?)</a>", r"\1", html)
    html = re.sub(r"</?u>", "", html)
    out, bold = [], False
    for part in re.split(r"(<b>|</b>|<br/>)", html):
        if part == "<b>":
            bold = True
        elif part == "</b>":
            bold = False
        elif part == "<br/>":
            out.append(("\n", bold))
        elif part:
            out.append((htmllib.unescape(re.sub(r"<[^>]+>", "", part)), bold))
    return out


def _para(doc_or_cell, html, size=10, font=CG, bold=False, underline=False, space_after=0, align=None, style=None):
    p = doc_or_cell.add_paragraph(style=style) if style else doc_or_cell.add_paragraph()
    pf = p.paragraph_format
    pf.space_after = Pt(space_after)
    pf.space_before = Pt(0)
    pf.line_spacing = Pt(14)
    if align:
        p.alignment = align
    for text, b in _plain(html):
        _font(p.add_run(text), font, size, bold or b, underline)
    return p


def _bullet(doc, html):
    p = _para(doc, html, style="List Bullet")
    p.paragraph_format.left_indent = Inches(0.75)
    p.paragraph_format.first_line_indent = Inches(-0.25)
    return p


def _borders(table):
    tbl = table._tbl
    pr = tbl.tblPr
    b = OxmlElement("w:tblBorders")
    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
        e = OxmlElement("w:" + edge)
        e.set(qn("w:val"), "single"); e.set(qn("w:sz"), "4"); e.set(qn("w:color"), "000000")
        b.append(e)
    pr.append(b)


def _widths(table, inches):
    table.autofit = False
    grid = table._tbl.tblGrid
    for gc, w in zip(grid.findall(qn("w:gridCol")), inches):
        gc.set(qn("w:w"), str(int(w * 1440)))
    for row in table.rows:
        for c, w in zip(row.cells, inches):
            c.width = Inches(w)


def _cell_text(cell, html, size=10, font=CG, bold=False, first=True):
    p = cell.paragraphs[0] if first and not cell.paragraphs[0].text else cell.add_paragraph()
    pf = p.paragraph_format
    pf.line_spacing = Pt(14 if size >= 10 else 12)
    pf.space_before = Pt(3 if first else 0)
    pf.space_after = Pt(3)
    for text, b in _plain(html):
        _font(p.add_run(text), font, size, bold or b)
    return p


def build(out, cfg=None):
    cfg = cfg or {}
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Inches(8.5), Inches(11)
    sec.left_margin = sec.right_margin = Inches(1)
    sec.top_margin, sec.bottom_margin = Inches(1.05), Inches(0.8)
    sec.header_distance = Inches(0.45)
    hp = sec.header.paragraphs[0]
    hp.alignment = WD_ALIGN_PARAGRAPH.CENTER
    _font(hp.add_run(NAME), CAL, 22, bold=True)
    st = doc.styles["Normal"]
    st.font.name, st.font.size = CG, Pt(10)

    for label, val in [("Phone", "+91 7566442162"), ("Education", "BE(IT) 2017"),
                       ("Linkedin", "https://www.linkedin.com/in/akshay-kumar-18565910a/"),
                       ("TrailheadId", "https://www.salesforce.com/trailblazer/akumar2783"),
                       ("Email", "akshay.verma0031@gmail.com")]:
        p = doc.add_paragraph(); p.paragraph_format.space_after = Pt(0)
        _font(p.add_run(label), CAL, 11, bold=True)
        _font(p.add_run(" :  "), CAL, 11)
        _font(p.add_run(val), CAL, 11, underline=label in ("Linkedin", "TrailheadId", "Email"),
              color="0563C1" if label in ("Linkedin", "TrailheadId", "Email") else None)
    doc.add_paragraph()

    _para(doc, "SKILLS SUMMARY / TECHNICAL HIGHLIGHTS", bold=True, space_after=10)
    items = (["<b>%s</b>" % cfg["summary"]] if cfg.get("summary") else [])
    for b in O.SKILLS_SUMMARY:
        if b.startswith(LEAD_BOLD):
            b = "<b>%s</b>%s" % (LEAD_BOLD, b[len(LEAD_BOLD):])
        elif b.startswith(BOLD_PREFIXES):
            b = "<b>%s</b>" % b
        items.append(b)
    for i in items:
        _bullet(doc, i)

    doc.add_paragraph()
    _para(doc, "Work Experience:", bold=True, underline=True, space_after=10)
    _para(doc, "7+ years of experience in IT industry working as Salesforce Consultant", space_after=12)
    t = doc.add_table(rows=0, cols=2); t.alignment = WD_TABLE_ALIGNMENT.CENTER; _borders(t)
    for who, when in O.WORK:
        co, _, title = who.partition(" / ")
        r = t.add_row().cells
        _cell_text(r[0], "<b>%s /</b> %s" % (co, title))
        p = _cell_text(r[1], when.replace("&ndash;", "–"), size=9, font=CAL)
        for run in p.runs:
            run.font.color.rgb = RGBColor(0x40, 0x40, 0x40)
    _widths(t, (3.62, 2.56))

    doc.add_paragraph()
    _para(doc, "TECHNICAL HIGHLIGHTS", bold=True, space_after=10)
    t = doc.add_table(rows=0, cols=2); t.alignment = WD_TABLE_ALIGNMENT.CENTER; _borders(t)
    rows = cfg.get("skills") or O.TECH_ROWS
    for label, text in rows:
        r = t.add_row().cells
        _cell_text(r[0], label)
        r[0].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        parts = text.split("<br/><br/>")
        for j, part in enumerate(parts):
            _cell_text(r[1], part, bold=(label == "Technical Skills:" or bool(cfg.get("skills"))), first=(j == 0))
            if j < len(parts) - 1:
                r[1].add_paragraph().paragraph_format.line_spacing = Pt(8)
    _widths(t, (2.12, 4.06))

    doc.add_paragraph()
    t = doc.add_table(rows=0, cols=2); t.alignment = WD_TABLE_ALIGNMENT.CENTER; _borders(t)
    for label, certs in (("Salesforce Certification", O.CERTS), ("Other Certification", O.OTHER_CERTS)):
        r = t.add_row().cells
        _cell_text(r[0], label, bold=True)
        r[0].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for j, c in enumerate(certs):
            _cell_text(r[1], c, size=11, font=CAL, bold=True, first=(j == 0))
    _widths(t, (2.12, 4.06))

    by_key = dict(zip(O.PROJECT_KEYS, O.PROJECTS))
    order = [k for k in (cfg.get("project_order") or []) if k in by_key]
    order += [k for k in O.PROJECT_KEYS if k not in order]
    for k in order:
        head, intro, items = by_key[k]
        doc.add_paragraph()
        _para(doc, head, bold=True, underline=True, space_after=10)
        if intro:
            _para(doc, intro, space_after=10)
        for i in items:
            _bullet(doc, i)
    doc.core_properties.title = NAME + " - Resume"
    doc.core_properties.author = NAME
    doc.save(out)
    return out


if __name__ == "__main__":
    print(build(sys.argv[1] if len(sys.argv) > 1 else "Akshay_Verma_Resume.docx"))
