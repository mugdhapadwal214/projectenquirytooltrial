"""
Expense reimbursement sheet, built to the house form.

The caps are policy, not decoration. A sheet that lets someone claim 400 for a
lunch capped at 150 wastes the claimant's time and the approver's, so the
overage is worked out here and printed against the line - the form arrives
already saying what it will actually pay.
"""
import datetime
import re

from docx import Document
from docx.enum.table import WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

COMPANY = "MAKERSTRIVE ENGINEERING PVT LTD"

# Per-day caps from the printed form. Anything not listed is uncapped.
CAPS = {
    "Breakfast": 75.0,
    "Lunch": 150.0,
    "Dinner": 150.0,
    "Stay": 1000.0,
}
CATEGORIES = ["Breakfast", "Lunch", "Dinner", "Stay", "Travel", "Other"]

RULES = [
    "The company will accept only printed/handwritten receipts/GST bills for "
    "reimbursement of food and accommodation. UPI payments will not be accepted.",
]

HEADER_BLUE = "1F4E79"


def _shade(cell, hexcolor):
    from docx.oxml.ns import qn
    from docx.oxml import OxmlElement
    el = OxmlElement("w:shd")
    el.set(qn("w:fill"), hexcolor)
    cell._tc.get_or_add_tcPr().append(el)


def _txt(cell, text, bold=False, size=9.5, white=False, align=None):
    cell.text = ""
    p = cell.paragraphs[0]
    p.paragraph_format.space_after = Pt(0)
    if align is not None:
        p.alignment = align
    r = p.add_run(str(text))
    r.bold = bold
    r.font.size = Pt(size)
    if white:
        r.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)


def _num(v):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0.0
    return f


def day_count(period_from, period_to):
    """Days covered, inclusive. Used to turn a per-day cap into a total."""
    try:
        a = datetime.date.fromisoformat(period_from)
        b = datetime.date.fromisoformat(period_to)
    except (TypeError, ValueError):
        return 1
    return max((b - a).days + 1, 1)


def check(items, period_from="", period_to=""):
    """
    Total the claim and flag what policy will not pay.

    Returns the priced lines plus a list of exceptions. A capped category is
    checked PER DAY, not per line: two lunches on one date are one day's lunch
    allowance between them, which is how the cap is meant to work and how a
    per-line check would get it wrong.
    """
    priced, per_day = [], {}
    for i, it in enumerate(items or [], 1):
        cost = _num(it.get("cost"))
        cat = (it.get("category") or "").strip()
        date = (it.get("date") or "").strip()
        priced.append({
            "n": i,
            "date": date,
            "description": (it.get("description") or "").strip(),
            "vendor": (it.get("vendor") or "").strip(),
            "category": cat,
            "bill_no": (it.get("bill_no") or "").strip(),
            "cost": cost,
        })
        if cat in CAPS:
            per_day.setdefault((cat, date), 0.0)
            per_day[(cat, date)] += cost

    exceptions = []
    for (cat, date), spent in sorted(per_day.items()):
        cap = CAPS[cat]
        if spent > cap + 1e-9:
            exceptions.append({
                "category": cat, "date": date, "claimed": spent, "cap": cap,
                "over": spent - cap,
                "text": f"{cat} on {date or 'an unstated date'}: "
                        f"{spent:,.2f} claimed against a cap of {cap:,.2f} "
                        f"\u2014 {spent - cap:,.2f} over.",
            })

    subtotal = sum(p["cost"] for p in priced)
    over = sum(e["over"] for e in exceptions)
    missing_bill = [p["n"] for p in priced
                    if p["cost"] and not p["bill_no"]]
    return {
        "items": priced,
        "subtotal": subtotal,
        "over_cap": over,
        "payable": max(subtotal - over, 0.0),
        "exceptions": exceptions,
        "missing_bill": missing_bill,
        "days": day_count(period_from, period_to),
    }


def _logo_path():
    """
    Company logo for the sheet header, if the firm has supplied one.

    Drop a PNG or JPG at data/logo.png (or .jpg) and every sheet picks it up.
    Nothing to configure and nothing to rebuild.
    """
    import os
    roots = []
    try:                                  # where the app is INSTALLED
        try:
            from .runtime_paths import app_dir
        except ImportError:
            from runtime_paths import app_dir
        roots.append(app_dir())
    except Exception:
        pass
    # Where this file sits. In a PyInstaller build that is a temporary
    # extraction folder, NOT beside the exe - so it cannot be the only place
    # looked at, which is why dropping the logo next to the app did nothing.
    roots.append(os.path.dirname(os.path.abspath(__file__)))

    for root in roots:
        for sub in ("data", ""):
            for name in ("logo.png", "logo.jpg", "logo.jpeg"):
                p = os.path.join(root, sub, name) if sub else os.path.join(root, name)
                if os.path.exists(p):
                    return p
    return None


BILL_TYPES = (".pdf", ".jpg", ".jpeg", ".png")


def attach_bills(doc, bills):
    """
    Put the receipts in the sheet itself.

    The form says "Don't forget to attach receipts", and until now that was a
    note to the claimant rather than something the app did. An approver got a
    sheet of figures with nothing behind them, and the receipts arrived - or
    did not - by some other route.

    Images go in as pictures, one per page, captioned with the line they back
    up. A PDF cannot be embedded in a .docx, so it is listed by name and
    written out beside the sheet instead; saying so is better than silently
    dropping it.
    """
    if not bills:
        return

    def line(text, size=11, bold=False, italic=False, after=2):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(after)
        run = p.add_run(str(text))
        run.bold = bold
        run.italic = italic
        run.font.size = Pt(size)

    doc.add_page_break()
    line("Attached Receipts", size=14, bold=True, after=8)

    pdfs = [b for b in bills if b["name"].lower().endswith(".pdf")]
    imgs = [b for b in bills if not b["name"].lower().endswith(".pdf")]

    for b in imgs:
        line(b["name"], size=10, bold=True, after=3)
        try:
            doc.add_picture(b["path"], width=Cm(15.0))
        except Exception:
            line("(image could not be read)", size=10, italic=True)
        doc.add_paragraph()

    if pdfs:
        line("PDF receipts, saved alongside this sheet:",
             size=11, bold=True, after=3)
        for n, b in enumerate(pdfs, 1):
            line(f"{n}. {b['name']}", size=11, after=1)


def build(path, data):
    """Write the reimbursement sheet."""
    d = data or {}
    calc = check(d.get("items"), d.get("period_from"), d.get("period_to"))

    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(1.8)
    sec.top_margin = sec.bottom_margin = Cm(1.5)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)

    # The real logo if one has been dropped in, the wordmark if not. The file
    # is looked up rather than shipped, because the company artwork is not
    # mine to invent and a wrong logo on an expense claim is worse than none.
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    logo = _logo_path()
    if logo:
        p.add_run().add_picture(logo, width=Cm(4.2))
    else:
        r = p.add_run("MS")
        r.bold = True
        r.font.size = Pt(22)
        r.font.color.rgb = RGBColor(0x1F, 0x4E, 0x79)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(f"Reimbursement Sheet No.- {d.get('sheet_no', '')}")
    r.bold = True
    r.font.size = Pt(11)

    top = doc.add_table(rows=2, cols=3)
    top.alignment = WD_TABLE_ALIGNMENT.LEFT
    widths = [8.6, 4.3, 4.3]
    for row in top.rows:
        for i, cell in enumerate(row.cells):
            cell.width = Cm(widths[i])
    _txt(top.rows[0].cells[0], "Expense Reimbursement", bold=True, size=13)
    for i, lbl in enumerate(("Period From", "Period To"), start=1):
        _shade(top.rows[0].cells[i], HEADER_BLUE)
        _txt(top.rows[0].cells[i], lbl, bold=True, white=True,
             align=WD_ALIGN_PARAGRAPH.CENTER)
    _txt(top.rows[1].cells[0], "")
    _txt(top.rows[1].cells[1], d.get("period_from", ""),
         align=WD_ALIGN_PARAGRAPH.CENTER)
    _txt(top.rows[1].cells[2], d.get("period_to", ""),
         align=WD_ALIGN_PARAGRAPH.CENTER)
    top.rows[0].cells[1].paragraphs[0].paragraph_format.space_after = Pt(0)

    doc.add_paragraph()
    for label, value in (("Company Name:", COMPANY),
                         ("Employee Name:", (d.get("employee") or d.get("employee_name") or "")),
                         ("Employee ID:", d.get("employee_id", "")),
                         ("Department:", d.get("department", ""))):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(1)
        r = p.add_run(f"{label} ")
        r.bold = True
        r.font.size = Pt(10)
        r2 = p.add_run(str(value))
        r2.font.size = Pt(10)

    doc.add_paragraph()
    for line in RULES:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(4)
        p.add_run(line).font.size = Pt(9)
    for line in (f"Food: Breakfast - {CAPS['Breakfast']:g}/- CAP per day, "
                 f"Lunch - {CAPS['Lunch']:g}/- CAP per day, "
                 f"Dinner - {CAPS['Dinner']:g}/- CAP per day",
                 f"Stay: \u20b9{CAPS['Stay']:g}/- CAP per day",
                 "Travel: Valid tickets/bills will be accepted."):
        p = doc.add_paragraph(style="List Bullet")
        p.paragraph_format.space_after = Pt(1)
        p.add_run(line).font.size = Pt(9)

    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run("Itemized Expenses")
    r.bold = True
    r.font.size = Pt(10)

    heads = ["Sr.No", "Date", "Description", "Vendor Name",
             "Category/Bill no.", "Cost"]
    w = [1.4, 2.2, 5.4, 3.4, 3.4, 2.4]
    t = doc.add_table(rows=1, cols=len(heads))
    t.style = "Table Grid"
    for i, h in enumerate(heads):
        t.rows[0].cells[i].width = Cm(w[i])
        _shade(t.rows[0].cells[i], HEADER_BLUE)
        _txt(t.rows[0].cells[i], h, bold=True, white=True,
             align=WD_ALIGN_PARAGRAPH.CENTER)

    rows = calc["items"] or []
    for it in rows:
        cells = t.add_row().cells
        cat = it["category"]
        tag = f"{cat} / {it['bill_no']}" if it["bill_no"] else cat
        vals = [it["n"], it["date"], it["description"], it["vendor"], tag,
                f"{it['cost']:,.2f}"]
        for i, v in enumerate(vals):
            cells[i].width = Cm(w[i])
            _txt(cells[i], v, size=9.5,
                 align=WD_ALIGN_PARAGRAPH.RIGHT if i == 5 else None)

    # Keep the printed form's shape even for a short claim.
    for _ in range(max(0, 8 - len(rows))):
        cells = t.add_row().cells
        for i in range(len(heads)):
            cells[i].width = Cm(w[i])
            _txt(cells[i], "")

    sub = t.add_row().cells
    for i in range(len(heads)):
        _shade(sub[i], HEADER_BLUE)
    _txt(sub[0], "Subtotal:", bold=True, white=True)
    for i in range(1, 5):
        _txt(sub[i], "", white=True)
    _txt(sub[5], f"{calc['subtotal']:,.2f}", bold=True, white=True,
         align=WD_ALIGN_PARAGRAPH.RIGHT)

    doc.add_paragraph()
    if calc["exceptions"]:
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run("Above policy cap \u2014 not payable")
        r.bold = True
        r.font.size = Pt(9.5)
        r.font.color.rgb = RGBColor(0xC0, 0x26, 0x26)
        for e in calc["exceptions"]:
            pp = doc.add_paragraph(style="List Bullet")
            pp.paragraph_format.space_after = Pt(0)
            pp.add_run(e["text"]).font.size = Pt(9)

    foot = doc.add_table(rows=3, cols=2)
    foot.rows[0].cells[0].width = Cm(9.0)
    for label, value, row in (
            ("Advance Payment:", d.get("advance", ""), 0),
            ("Less above cap:", f"{calc['over_cap']:,.2f}", 1),
            ("Total Reimbursement:", f"{calc['payable'] - _num(d.get('advance')):,.2f}", 2)):
        _txt(foot.rows[row].cells[0], "")
        c = foot.rows[row].cells[1]
        c.text = ""
        p = c.paragraphs[0]
        p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        p.paragraph_format.space_after = Pt(0)
        r = p.add_run(f"{label}  {value}")
        r.bold = (row == 2)
        r.font.size = Pt(10)

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    r = p.add_run("*Don't forget to attach receipts*")
    r.italic = True
    r.font.size = Pt(9)

    if d.get("notes"):
        p = doc.add_paragraph()
        p.paragraph_format.space_after = Pt(2)
        r = p.add_run("Notes: ")
        r.bold = True
        r.font.size = Pt(9.5)
        p.add_run(str(d["notes"])).font.size = Pt(9.5)

    doc.add_paragraph()
    sig = doc.add_table(rows=1, cols=2)
    _txt(sig.rows[0].cells[0], "Employee Signature:  ____________________", size=10)
    _txt(sig.rows[0].cells[1], "Approval Signature:  ____________________", size=10)

    attach_bills(doc, data.get("bills") or [])

    doc.save(path)
    return path


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text or "")).strip("_") or "sheet"
