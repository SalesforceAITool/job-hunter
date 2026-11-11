"""Akshay's original Word resume layout, rebuilt with reportlab (used for every tailored resume).

Matches Akshay_Resume_8_Year_1.pdf (Word): US Letter, 1" side margins, "Akshay Kumar Verma" in Calibri Bold 22
centred at the top of every page, a Calibri 11 contact block, Century Gothic 10 body with round bullets,
a bordered Work Experience table (company bold / title, dates in Calibri 9 grey), a bordered Technical
Highlights table, a bordered certification table (Calibri Bold 11), then projects with bold underlined titles.

Fonts come from Windows (C:\\Windows\\Fonts or the per-user font folder); when Century Gothic is missing it
falls back to Calibri / Carlito, then Helvetica, so a PDF is always produced.
"""
import os
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import orig_content as O

NAME = "Akshay Kumar Verma"
EMAIL = os.environ.get("JH_PROFILE_EMAIL") or "akshay.verma0031@gmail.com"   # set by the app from Settings
CONTACT = [
    ("Phone", "+91 7566442162"),
    ("Education", "BE(IT) 2017"),
    ("Linkedin", "<a href='https://www.linkedin.com/in/akshay-kumar-18565910a/' color='#0563C1'><u>https://www.linkedin.com/in/akshay-kumar-18565910a/</u></a>"),
    ("TrailheadId", "<a href='https://www.salesforce.com/trailblazer/akumar2783' color='#0563C1'><u>https://www.salesforce.com/trailblazer/akumar2783</u></a>"),
    ("Email", "<a href='mailto:" + EMAIL + "' color='#0563C1'><u>" + EMAIL + "</u></a>"),
]
# Skills Summary bullets that are bold in the original (matched by their first words)
BOLD_PREFIXES = ("Hands-on Agentforce", "Experience in integrating SFDC", "Experience in JavaScript", "Experienced in Salesforce Lightning",
                 "Experience in customizing Data model", "Experience in implementing Apex", "Strong knowledge of Salesforce configuration",
                 "Resolved APEX CPU", "Developed a robust framework")
LEAD_BOLD = "7+ years of Salesforce Technology"

_FONT_DIRS = [Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts",
              Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Windows" / "Fonts",
              Path("/usr/share/fonts/truetype/crosextra"), Path("/usr/share/fonts/truetype/msttcorefonts"),
              Path.home() / ".fonts", Path("/usr/share/fonts/truetype/dejavu")]
_WANT = {
    "CG": ["GOTHIC.TTF", "gothic.ttf", "CenturyGothic.ttf"],
    "CG-B": ["GOTHICB.TTF", "gothicb.ttf", "CenturyGothic-Bold.ttf"],
    "CAL": ["calibri.ttf", "CALIBRI.TTF", "Carlito-Regular.ttf"],
    "CAL-B": ["calibrib.ttf", "CALIBRIB.TTF", "Carlito-Bold.ttf"],
}


def _find(names):
    for d in _FONT_DIRS:
        for n in names:
            p = d / n
            try:
                if p.is_file():
                    return str(p)
            except OSError:
                pass
    return None


def register_fonts() -> dict:
    """Map logical names -> registered font names (with fallbacks)."""
    got = {}
    for key, names in _WANT.items():
        path = _find(names)
        if path:
            try:
                pdfmetrics.registerFont(TTFont("JH-" + key, path))
                got[key] = "JH-" + key
            except Exception:
                pass
    cal = got.get("CAL", "Helvetica")
    calb = got.get("CAL-B", "Helvetica-Bold")
    out = {"CG": got.get("CG", cal), "CG-B": got.get("CG-B", calb), "CAL": cal, "CAL-B": calb}
    try:
        from reportlab.lib.fonts import addMapping
        addMapping(out["CG"], 0, 0, out["CG"]); addMapping(out["CG"], 1, 0, out["CG-B"])
        addMapping(out["CG"], 0, 1, out["CG"]); addMapping(out["CG"], 1, 1, out["CG-B"])
        addMapping(out["CAL"], 0, 0, out["CAL"]); addMapping(out["CAL"], 1, 0, out["CAL-B"])
        addMapping(out["CAL"], 0, 1, out["CAL"]); addMapping(out["CAL"], 1, 1, out["CAL-B"])
    except Exception:
        pass
    return out


def build(cfg: dict, out: str) -> str:
    F = register_fonts()
    INK = colors.black
    GREY = colors.HexColor("#404040")
    body = ParagraphStyle("body", fontName=F["CG"], fontSize=10, leading=13.6, textColor=INK)
    bullet = ParagraphStyle("bul", parent=body, leftIndent=54, bulletIndent=36, bulletFontName=F["CG"], spaceAfter=0.4)
    contact = ParagraphStyle("contact", fontName=F["CAL"], fontSize=11, leading=13.4, textColor=INK)
    sec = ParagraphStyle("sec", fontName=F["CG-B"], fontSize=10, leading=13.6, spaceBefore=14, spaceAfter=10)
    sec_u = ParagraphStyle("secu", parent=sec)
    ptitle = ParagraphStyle("pt", fontName=F["CG-B"], fontSize=10, leading=13.6, spaceBefore=18, spaceAfter=10)
    cell = ParagraphStyle("cell", parent=body, leading=12.4)
    cell_b = ParagraphStyle("cellb", parent=cell, fontName=F["CG-B"])
    date = ParagraphStyle("date", fontName=F["CAL"], fontSize=9, leading=11, textColor=GREY)
    certs_s = ParagraphStyle("certs", fontName=F["CAL-B"], fontSize=11, leading=13.4)

    s = []
    for label, val in CONTACT:
        s.append(Paragraph("<font name='%s'>%s</font> :&nbsp; %s" % (F["CAL-B"], label, val), contact))
    s.append(Spacer(1, 12))

    # ---- Skills summary (tailored summary goes first, bold)
    s.append(Paragraph("SKILLS SUMMARY / TECHNICAL HIGHLIGHTS", sec))
    items = []
    if cfg.get("summary"):
        items.append("<b>%s</b>" % cfg["summary"])
    for b in O.SKILLS_SUMMARY:
        if b.startswith(LEAD_BOLD):
            b = "<b>%s</b>%s" % (LEAD_BOLD, b[len(LEAD_BOLD):])
        elif b.startswith(BOLD_PREFIXES):
            b = "<b>%s</b>" % b
        items.append(b)
    s += [Paragraph(i, bullet, bulletText="•") for i in items]

    # ---- Work experience table
    s.append(Spacer(1, 18))
    s.append(Paragraph("<u>Work Experience:</u>", sec_u))
    s.append(Paragraph("7+ years of experience in IT industry working as Salesforce Consultant", body))
    s.append(Spacer(1, 12))
    rows = []
    for who, when in O.WORK:
        co, _, title = who.partition(" / ")
        rows.append([Paragraph("<b>%s /</b> %s" % (co, title), cell), Paragraph(when, date)])
    t = Table(rows, colWidths=[261, 184], hAlign="CENTER")
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, INK), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                           ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                           ("LEFTPADDING", (0, 0), (-1, -1), 5)]))
    s.append(t)

    # ---- Technical highlights table
    s.append(Spacer(1, 18))
    s.append(Paragraph("TECHNICAL HIGHLIGHTS", sec))
    trows = []
    if cfg.get("skills"):
        for label, text in cfg["skills"]:
            trows.append([Paragraph(label, cell), Paragraph(text, cell_b)])
    else:
        for label, text in O.TECH_ROWS:
            trows.append([Paragraph(label, cell), Paragraph(text, cell_b if label == "Technical Skills:" else cell)])
    t = Table(trows, colWidths=[153, 292], hAlign="CENTER")
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, INK), ("VALIGN", (0, 0), (0, -1), "MIDDLE"),
                           ("VALIGN", (1, 0), (1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("LEFTPADDING", (0, 0), (-1, -1), 5)]))
    s.append(t)

    # ---- Certifications table
    s.append(Spacer(1, 18))
    crows = [[Paragraph("<b>Salesforce Certification</b>", cell), [Paragraph(c, certs_s) for c in O.CERTS]],
             [Paragraph("<b>Other Certification</b>", cell), [Paragraph(c, certs_s) for c in O.OTHER_CERTS]]]
    t = Table(crows, colWidths=[153, 292], hAlign="CENTER")
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.6, INK), ("VALIGN", (0, 0), (0, -1), "MIDDLE"),
                           ("VALIGN", (1, 0), (1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 4),
                           ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("LEFTPADDING", (0, 0), (-1, -1), 6)]))
    s.append(t)

    # ---- Projects (tailored order)
    by_key = dict(zip(O.PROJECT_KEYS, O.PROJECTS))
    order = [k for k in (cfg.get("project_order") or []) if k in by_key]
    order += [k for k in O.PROJECT_KEYS if k not in order]
    for k in order:
        head, intro, items = by_key[k]
        blk = [Paragraph("<u>%s</u>" % head, ptitle)]
        if intro:
            blk += [Paragraph(intro, body), Spacer(1, 10)]
        bl = [Paragraph(i, bullet, bulletText="•") for i in items]
        s.append(KeepTogether(blk + bl[:1]))
        s += bl[1:]

    def header(canvas, doc):
        canvas.saveState()
        canvas.setFont(F["CAL-B"], 22)
        canvas.drawCentredString(letter[0] / 2.0, letter[1] - 58, NAME)
        canvas.restoreState()

    SimpleDocTemplate(out, pagesize=letter, leftMargin=72, rightMargin=72, topMargin=76, bottomMargin=60,
                      title="%s - Resume" % NAME, author=NAME).build(s, onFirstPage=header, onLaterPages=header)
    return out
