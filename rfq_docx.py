"""
rfq_docx.py — the RFQ as a Word document.

WHY .docx AND NOT THE HTML
    An RFQ gets edited before it goes out — a buyer adds terms, deletes a
    schedule, changes a delivery date. HTML is fine to look at and useless to
    edit, so the document the app produces is the one the buying team actually
    works in.

SCOPE: RAW MATERIAL ONLY
    This document covers material supply. It prints nothing the database
    cannot back.

    Heat treatment and secondary processes were removed rather than left as a
    "to be defined" placeholder. An empty section in a document sent to a
    supplier is worse than no section: it invites someone to quote against a
    requirement that was never stated. When the process data exists, the
    section comes back with real content in it.

    The same rule governs the material specification: composition and property
    limits are printed straight from data/materials.json, and a material the
    master does not know is shown as an obvious gap rather than a confident
    blank.
"""
import datetime

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

NAVY = RGBColor(0x1F, 0x38, 0x64)
AMBER_FILL = "FFF4E0"


def _shade(cell, hexcolor):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hexcolor)
    cell._tc.get_or_add_tcPr().append(el)


def _repeat_header(row):
    trPr = row._tr.get_or_add_trPr()
    el = OxmlElement("w:tblHeader")
    el.set(qn("w:val"), "true")
    trPr.append(el)


def _fixed_layout(table, widths_cm):
    """
    python-docx honours column widths only if the table declares a fixed
    layout AND an explicit grid. Without both, Word re-flows every column and
    the widths set below are ignored.
    """
    tblPr = table._tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    grid = table._tbl.find(qn("w:tblGrid"))
    if grid is not None:
        table._tbl.remove(grid)
    grid = OxmlElement("w:tblGrid")
    for w in widths_cm:
        gc = OxmlElement("w:gridCol")
        gc.set(qn("w:w"), str(int(w * 567)))
        grid.append(gc)
    tblPr.addnext(grid)


def _h(doc, text, size=12):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text.upper())
    r.bold = True
    r.font.size = Pt(size)
    r.font.color.rgb = NAVY
    return p


def _para(doc, text, size=9.5, italic=False, bold=False):
    p = doc.add_paragraph()
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.italic = italic
    r.bold = bold
    return p


def _table(doc, headers, widths):
    t = doc.add_table(rows=1, cols=len(headers))
    t.style = "Table Grid"
    t.autofit = False
    _fixed_layout(t, widths)
    hdr = t.rows[0]
    for i, h in enumerate(headers):
        c = hdr.cells[i]
        c.width = Cm(widths[i])
        c.text = ""
        run = c.paragraphs[0].add_run(h)
        run.bold = True
        run.font.size = Pt(8.5)
        run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
        _shade(c, "1F3864")
    _repeat_header(hdr)
    return t


def _row(t, values, widths, size=8.5):
    r = t.add_row()
    for i, v in enumerate(values):
        c = r.cells[i]
        c.width = Cm(widths[i])
        c.text = ""
        run = c.paragraphs[0].add_run("" if v is None else str(v))
        run.font.size = Pt(size)
    return r


def _todo(doc, title, body):
    """A shaded block for something genuinely not yet decided."""
    t = doc.add_table(rows=1, cols=1)
    t.style = "Table Grid"
    _fixed_layout(t, [17.0])
    c = t.rows[0].cells[0]
    c.width = Cm(17.0)
    c.text = ""
    p = c.paragraphs[0]
    r = p.add_run(title + "  ")
    r.bold = True
    r.font.size = Pt(9.5)
    r.font.color.rgb = RGBColor(0xB0, 0x6A, 0x00)
    r2 = p.add_run(body)
    r2.font.size = Pt(9)
    _shade(c, AMBER_FILL)
    doc.add_paragraph()


def _money(v):
    try:
        return f"\u20b9{float(v):,.0f}"
    except (TypeError, ValueError):
        return "\u20b90"


def _num(v, d=1):
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return "0"


def build(data, path):
    """Write the RFQ. `data` is the dict from rfq.collect()."""
    proj = data.get("project") or {}
    s = data.get("summary") or {}
    today = datetime.date.today()

    doc = Document()
    sec = doc.sections[0]
    sec.orientation = WD_ORIENT.PORTRAIT
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin = sec.bottom_margin = Cm(1.8)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)

    ref = f"RFQ/{proj.get('number') or proj.get('id', '')}/{today.strftime('%Y%m%d')}"
    foot = sec.footer.paragraphs[0]
    foot.alignment = WD_ALIGN_PARAGRAPH.CENTER
    fr = foot.add_run(f"{ref}  |  {proj.get('name', '')}  |  "
                      f"Generated {today.strftime('%d %b %Y')}")
    fr.font.size = Pt(7.5)

    # --- title -------------------------------------------------------------
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run("REQUEST FOR QUOTATION")
    r.bold = True
    r.font.size = Pt(18)
    r.font.color.rgb = NAVY
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    r = p.add_run(ref)
    r.font.size = Pt(9)

    w = [4.5, 12.5]
    t = _table(doc, ["Particulars", "Details"], w)
    for k, v in (("Project number", proj.get("number") or proj.get("id", "")),
                 ("Project name", proj.get("name", "")),
                 ("Customer", proj.get("client") or "\u2014"),
                 ("Description", proj.get("notes") or "\u2014"),
                 ("Date of issue", today.strftime("%d %b %Y")),
                 ("Quotation due by", "To be advised")):
        row = _row(t, [k, v], w, size=9.5)
        row.cells[0].paragraphs[0].runs[0].bold = True

    # --- 1 scope -----------------------------------------------------------
    _h(doc, "1  Scope")
    _para(doc, "Quotations are invited for the supply of plate, the cutting of the "
               "profiles listed below, and the supply of the bought-out components "
               "scheduled here. Each schedule may be quoted and awarded separately.")

    # --- 2 plate -----------------------------------------------------------
    _h(doc, "2  Plate to be supplied")
    plates = data.get("plates") or []
    if plates:
        w = [1.0, 4.6, 2.0, 3.6, 1.6, 2.4, 2.6]
        t = _table(doc, ["#", "Material", "Thk (mm)", "Plate size", "Qty",
                         "Weight (kg)", "Value"], w)
        for i, pl in enumerate(plates, 1):
            _row(t, [i, pl.get("material", ""), _num(pl.get("thickness")),
                     pl.get("size", ""), pl.get("qty", 0),
                     _num(pl.get("weight_kg")), _money(pl.get("cost"))], w)
    else:
        _para(doc, "Nothing in this schedule.", italic=True)

    # --- 3 bought-out ------------------------------------------------------
    _h(doc, "3  Bought-out components")
    bought = data.get("bought_lines") or []
    if bought:
        w = [1.0, 3.0, 5.4, 3.4, 1.6, 3.4]
        t = _table(doc, ["#", "Type", "Description", "Material", "Qty", "Value"], w)
        for i, b in enumerate(bought, 1):
            _row(t, [i, b.get("component_type", ""),
                     b.get("spec") or b.get("label", ""), b.get("material", ""),
                     b.get("qty", 0), _money(b.get("purchase_cost"))], w)
    else:
        _para(doc, "Nothing in this schedule.", italic=True)

    # --- 4 profiles --------------------------------------------------------
    _h(doc, "4  Profiles to be cut")
    lines = data.get("plate_lines") or []
    if lines:
        w = [1.0, 3.0, 3.6, 3.0, 3.4, 1.4, 2.4]
        t = _table(doc, ["#", "Component", "Description", "Blank", "Material",
                         "Qty", "Weight (kg)"], w)
        for i, ln in enumerate(lines, 1):
            _row(t, [i, ln.get("component_type", ""), ln.get("label", ""),
                     ln.get("spec", ""), ln.get("material", ""),
                     ln.get("qty", 0), _num(ln.get("weight_kg"), 2)], w)
    else:
        _para(doc, "Nothing in this schedule.", italic=True)

    # --- 5 material spec ---------------------------------------------------
    _h(doc, "5  Material specification and certification")
    recs = data.get("materials") or []
    if recs:
        w = [4.2, 4.6, 4.2, 4.0]
        for rec in recs:
            if rec.get("unknown"):
                _para(doc, f"{rec.get('name')} \u2014 not in the material "
                           f"master, specification unavailable.", italic=True)
                continue
            t = _table(doc, ["Material", "Standard / grade", "Condition",
                             "Certification"], w)
            _row(t, [rec.get("name", ""),
                     f"{rec.get('standard', '')}\n{rec.get('grade', '')}",
                     rec.get("condition", ""),
                     rec.get("certification", "")], w, size=9)

            comp = rec.get("composition") or {}
            if comp:
                _para(doc, "Chemical composition, weight %", size=9, bold=True)
                cw = [3.4] * 5
                ct = _table(doc, ["Element", "Limit", "Element", "Limit", ""], cw)
                items = list(comp.items())
                for a in range(0, len(items), 2):
                    pair = items[a:a + 2]
                    vals = [pair[0][0], pair[0][1]]
                    vals += [pair[1][0], pair[1][1]] if len(pair) > 1 else ["", ""]
                    _row(ct, vals + [""], cw, size=8.5)

            mech = rec.get("mechanical") or {}
            if mech:
                _para(doc, "Mechanical properties", size=9, bold=True)
                mw = [6.0, 11.0]
                mt = _table(doc, ["Property", "Requirement"], mw)
                for k, v in mech.items():
                    _row(mt, [k, v], mw, size=8.5)

            # The material master's `notes` field is internal working
            # commentary — unverified figures, open questions, things to
            # confirm. None of that belongs in a document going to a supplier.
            doc.add_paragraph()

    # --- 6 commercial ------------------------------------------------------
    _h(doc, "6  Commercial summary")
    w = [6.0, 4.0]
    t = _table(doc, ["Item", "Estimated value"], w)
    for k, v in (("Plate material", s.get("plate_cost")),
                 ("Cutting", s.get("cut_cost")),
                 ("Bought-out components", s.get("purchase_cost"))):
        _row(t, [k, _money(v)], w, size=9.5)
    row = _row(t, ["Estimated total", _money(s.get("grand_total"))], w, size=10)
    for c in row.cells:
        c.paragraphs[0].runs[0].bold = True
    _para(doc, "Figures above are the estimator's own working values, given for "
               "scope only. They are not a budget, a target price, or a ceiling.",
          size=8.5, italic=True)

    # --- 8 terms -----------------------------------------------------------
    _h(doc, "7  Terms")
    w = [5.0, 12.0]
    t = _table(doc, ["Item", "Requirement"], w)
    for k in ("Delivery", "Delivery address", "Payment terms",
              "Validity of offer", "Inspection"):
        _row(t, [k, "To be advised"], w, size=9.5)

    doc.add_paragraph()
    _para(doc, "Raw material only. Items marked \u201cto be advised\u201d are for "
               "the buyer to complete before issue.", size=8, italic=True)

    doc.save(path)
    return path
