"""Plain text of what the resume builder printed, saved next to the PDF as <name>.txt (25 Nov: the ATS keyword check
in jh/ats.py reads it; reading the PDF back would need a PDF library on the laptop)."""
import html
import re


def _texts(f):
    if f is None:
        return
    if isinstance(f, str):
        yield f
        return
    if isinstance(f, (list, tuple)):
        for x in f:
            yield from _texts(x)
        return
    if hasattr(f, "text") and isinstance(getattr(f, "text"), str):          # Paragraph
        yield f.text
        return
    if hasattr(f, "_cellvalues"):                                             # Table
        for row in f._cellvalues:
            yield from _texts(row)
        return
    if hasattr(f, "_content"):                                                # KeepTogether
        yield from _texts(f._content)


def dump(story, out_pdf, extra=()) -> str:
    parts = list(extra) + list(_texts(story))
    txt = "\n".join(re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", p))).strip() for p in parts)
    path = re.sub(r"\.pdf$", "", str(out_pdf), flags=re.I) + ".txt"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(ln for ln in txt.splitlines() if ln))
    return path
