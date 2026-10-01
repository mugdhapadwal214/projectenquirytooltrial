"""
rfq_rm.py — the raw material RFQ, in the house format.

Structure follows RFQ_RM.docx exactly:

    header table          issuer / contact / project / vendor
    REQUEST FOR QUOTATION
    Product Details: Raw Material
    item blocks           one boxed table per line item
    Material Selection Criteria
    Developing & Submitting The Quote
    Address for Delivery of Materials
    Price
    Delivery and payments

ONE DOCUMENT PER VENDOR
    The vendor's name goes in the header, so each supplier receives a document
    addressed to them. Three raw-material vendors means three files, ready to
    send, rather than one generic sheet that has to be edited three times.

NO PRICES IN THE DOCUMENT
    This goes OUT to a supplier so that they can price it. Our own estimated
    cost is deliberately absent: showing a vendor the number we already have
    anchors their quote, and can only ever cost money. Weight is given because
    the material is bought by weight and the supplier needs it to quote at all.

WHAT COUNTS AS RAW MATERIAL
    Plate that has to be bought, and pipe. Bought-out components (flanges and
    the like) are finished parts, not raw material, and belong in a separate
    enquiry — they are excluded here rather than quietly folded in.
"""
import datetime
import json
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp


def house_config():
    """Issuer details and the standard clause text, from data/company.json."""
    try:
        with open(_dp("company.json"), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


# --------------------------------------------------------------- helpers ---

def _shade(cell, hexcolor):
    el = OxmlElement("w:shd")
    el.set(qn("w:val"), "clear")
    el.set(qn("w:fill"), hexcolor)
    cell._tc.get_or_add_tcPr().append(el)


def _fixed(table, widths_cm):
    """
    Word ignores column widths unless the table declares a fixed layout AND an
    explicit grid. Setting cell.width alone does nothing.
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


def _p(doc, text="", size=11.5, bold=False, italic=False, after=4, align=None):
    par = doc.add_paragraph()
    par.paragraph_format.space_after = Pt(after)
    par.paragraph_format.space_before = Pt(0)
    if align is not None:
        par.alignment = align
    r = par.add_run(text)
    r.font.size = Pt(size)
    r.bold = bold
    r.italic = italic
    return par


def _heading(doc, text):
    par = doc.add_paragraph()
    par.paragraph_format.space_before = Pt(10)
    par.paragraph_format.space_after = Pt(3)
    r = par.add_run(text)
    r.bold = True
    r.font.size = Pt(13)
    return par


def _logo_path():
    """
    Company logo, looked up rather than shipped.

    Drop a PNG or JPG beside the app or in its data folder and both the RFQ and
    the reimbursement sheet pick it up. In a built exe the module lives in a
    temporary extraction folder, NOT beside the exe, so the installed app
    folder has to be searched first or a logo placed next to the app is never
    found.
    """
    import os
    roots = []
    try:
        try:
            from .runtime_paths import app_dir
        except ImportError:
            from runtime_paths import app_dir
        roots.append(app_dir())
    except Exception:
        pass
    roots.append(os.path.dirname(os.path.abspath(__file__)))
    for root in roots:
        for sub in ("data", ""):
            for name in ("logo.png", "logo.jpg", "logo.jpeg"):
                p = os.path.join(root, sub, name) if sub else os.path.join(root, name)
                if os.path.exists(p):
                    return p
    return None


def _bullets(doc, items):
    """
    Numbered 1. 2. 3., not bulleted.

    Numbered on purpose: a vendor querying clause 6 of the RFQ needs a clause 6
    to point at. The numbers are written into the text rather than applied as a
    Word list style, so they survive the vendor pasting the clauses into a
    reply or a purchase order.
    """
    for n, it in enumerate(items, 1):
        par = doc.add_paragraph()
        par.paragraph_format.space_after = Pt(2)
        par.paragraph_format.left_indent = Cm(1.0)
        par.paragraph_format.first_line_indent = Cm(-0.6)
        run = par.add_run(f"{n}. {it}")
        run.font.size = Pt(11.5)


def _num(v, d=0):
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return "0"


# ----------------------------------------------------------- item blocks ---

# Stock round bar, ISO metric (mm). A supplier rolls these sizes and nothing
# between them, so a part drawn at 11.2 is cut from 12 and the 0.8 is turned
# off. Quoting 11.2 would get a phone call back, not a price.
BAR_DIAMETERS = (6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 35, 36,
                 40, 45, 50, 55, 56, 60, 63, 70, 80, 90, 100, 110, 120, 125,
                 140, 160, 180, 200, 220, 250)

# Bar is supplied in these lengths.
BAR_LENGTHS_MM = (3000.0, 6000.0)

# Parting-off loss per piece. Every cut consumes material and the last piece
# on a bar has to fit whole.
BAR_CUT_ALLOWANCE_MM = 10.0


def standard_bar_dia(dia_mm):
    """Next stock diameter at or above the drawn size. Never rounds down."""
    d = float(dia_mm or 0)
    for s in BAR_DIAMETERS:
        if s >= d - 1e-9:
            return float(s)
    return d          # larger than anything stocked; quote it as drawn


def bar_order(dia_mm, piece_len_mm, qty, allowance=BAR_CUT_ALLOWANCE_MM,
              lengths=BAR_LENGTHS_MM):
    """
    How many standard bars to buy, and what is left over.

    PIECES PER BAR, NOT TOTAL LENGTH DIVIDED BY BAR LENGTH.
    Bar cannot be joined. A piece either fits in what remains of a bar or it
    starts a new one, so the count is floor(bar / (piece + cut)) pieces per
    bar - dividing the total length instead quietly assumes a part can span
    two bars and under-orders whenever the piece does not divide evenly.

    Both stock lengths are costed and the one using less steel wins, tie going
    to the shorter bar because it is easier to handle and to store.
    """
    d = standard_bar_dia(dia_mm)
    need = float(piece_len_mm or 0) + float(allowance)
    qty = int(qty or 0)
    if need <= 0 or qty <= 0:
        return None

    best = None
    for L in sorted(lengths):
        per = int(L // need)
        if per <= 0:
            continue                      # piece longer than this stock bar
        bars = -(-qty // per)             # ceil
        on_last = qty - per * (bars - 1)
        cand = {
            "stock_dia_mm": d,
            "stock_length_mm": L,
            "pieces_per_bar": per,
            "bars": bars,
            "total_length_mm": bars * L,
            "piece_plus_cut_mm": need,
            "offcut_mm": round(L - on_last * need, 1),
            "pieces_on_last_bar": on_last,
        }
        if best is None or cand["total_length_mm"] < best["total_length_mm"]:
            best = cand
    return best


def rm_items(plates, pipes, bars=None, customs=None):
    """
    One line item per thing to be bought.

    Plate is grouped by material, thickness and plate size — that is how it is
    ordered and how it is priced. Pipe keeps its own shape, since it is bought
    by size, schedule and length rather than by sheet.
    """
    items = []
    for p in plates or []:
        size = str(p.get("size") or "")
        thk = p.get("thickness")
        items.append({
            "description": "Plate / Sheet",
            "material": p.get("material", ""),
            "size": size,
            "thickness": f"{_num(thk, 1)} mm" if thk is not None else "",
            "length": "",
            "qty": int(p.get("qty") or 0),
            "weight_kg": float(p.get("weight_kg") or 0),
        })
    for x in pipes or []:
        items.append({
            "description": "Pipe",
            "material": x.get("material", ""),
            "size": f'NPS {x.get("nps", "")}" \u00b7 SCH {x.get("schedule", "")}',
            "thickness": f'{_num(x.get("thickness"), 2)} mm',
            "length": f'{_num(x.get("length_m"), 1)} m per piece',
            "qty": int(x.get("qty") or 0),
            "weight_kg": float(x.get("weight_kg") or 0),
        })

    for b in bars or []:
        o = bar_order(b.get("dia"), b.get("piece_length_mm"), b.get("qty"))
        if not o:
            continue
        # Weight of what is ORDERED, not of the finished parts: the stock bar
        # is fatter than the drawn part and longer than the sum of the pieces,
        # and the supplier is quoting the stock. Scaled off the part weight so
        # no density table is needed here.
        part_d = float(b.get("dia") or 0)
        part_total_mm = float(b.get("piece_length_mm") or 0) * int(b.get("qty") or 0)
        w = float(b.get("weight_kg") or 0)
        if part_d > 0 and part_total_mm > 0 and w > 0:
            w = w * (o["stock_dia_mm"] ** 2 * o["total_length_mm"]) / \
                (part_d ** 2 * part_total_mm)
        drop = (f' + {_num(o["offcut_mm"], 0)} mm offcut from the last bar'
                if o["offcut_mm"] >= 1 else "")
        items.append({
            "description": "Round Bar",
            "material": b.get("material", ""),
            "size": f'\u00d8{_num(o["stock_dia_mm"], 0)} mm',
            "thickness": "",
            "length": (f'{_num(o["stock_length_mm"] / 1000.0, 1)} m standard length'
                       f' \u00b7 {o["pieces_per_bar"]} piece(s) per bar{drop}'),
            "qty": o["bars"],
            "weight_kg": w,
            "note": (f'For {b.get("qty")} off \u00d8{_num(part_d, 1)} \u00d7 '
                     f'{_num(b.get("piece_length_mm"), 0)} mm, including '
                     f'{_num(BAR_CUT_ALLOWANCE_MM, 0)} mm cutting allowance per piece.'),
        })

    for c in customs or []:
        items.append({
            "description": c.get("label") or "Custom part",
            "material": c.get("material", ""),
            "size": c.get("dimensions") or "as per drawing",
            "thickness": "",
            "length": "",
            "qty": int(c.get("qty") or 0),
            "weight_kg": 0.0,
            "note": c.get("remarks") or "",
        })
    return items


def _item_block(doc, n, rows):
    """One boxed table per item, exactly as the house format lays it out."""
    t = doc.add_table(rows=0, cols=1)
    t.style = "Table Grid"
    _fixed(t, [17.0])
    for i, (label, value) in enumerate(rows):
        cell = t.add_row().cells[0]
        cell.width = Cm(17.0)
        cell.text = ""
        par = cell.paragraphs[0]
        par.paragraph_format.space_after = Pt(0)
        if i == 0:
            r = par.add_run(f"{n}) {label}:  ")
            r.bold = True
            r.font.size = Pt(12)
            r2 = par.add_run(value)
            r2.bold = True
            r2.font.size = Pt(12)
            _shade(cell, "EDF1F7")
        else:
            r = par.add_run(f"{label}: ")
            r.font.size = Pt(11.5)
            r2 = par.add_run(value)
            r2.font.size = Pt(11.5)
    doc.add_paragraph()


# ------------------------------------------------------------- content ----

def content(project, vendor, items, rfq_no, cfg=None, validity_days=10):
    """
    Everything the RFQ says, with nothing about how it looks.

    build() renders this to Word and the app renders the same dict to HTML for
    the on-screen preview. One source, two renderers - so what an approver is
    shown on screen cannot drift away from what the supplier is sent. Adding a
    clause here makes it appear in both; writing it into build() directly would
    put it in the document and not the preview, which is the failure worth
    designing out.
    """
    cfg = cfg or house_config()
    comp = cfg.get("company", {})
    con = cfg.get("contact", {})
    today = datetime.date.today()
    vname = (vendor.get("company") or {}).get("name", "") if vendor else ""

    header = [
        (f"Company Name: {comp.get('name', '')}",
         f"Contact: {con.get('person', '')}"),
        (f"Address: {comp.get('address', '')}",
         f"Phone: {con.get('phone', '')}"),
        (f"GSTIN: {comp.get('gstin', '')}",
         f"Email: {con.get('email', '')}"),
        (f"Project no: {project.get('number') or project.get('id', '')}",
         f"Validity of RFQ: {validity_days} Days"),
        (f"Date: {today.strftime('%d %b %Y')}",
         f"Vendor Name: {vname}"),
    ]

    blocks = []
    for n, it in enumerate(items or [], 1):
        rows = [("Item Description", it["description"]),
                ("Material", it["material"]),
                ("Size Required", it["size"]),
                ("Thickness", it["thickness"])]
        if it.get("length"):
            rows.append(("Length", it["length"]))
        rows += [("Quantity", str(it["qty"])),
                 ("Approx. Weight", f'{_num(it["weight_kg"])} Kg')]
        if it.get("note"):
            rows.append(("Note", it["note"]))
        blocks.append({"n": n, "rows": rows})

    total_kg = sum(float(i.get("weight_kg") or 0) for i in items or [])

    sections = []
    for title, key in (("Material Selection Criteria:", "material_criteria"),
                       ("Developing & Submitting The Quote:", "quote_instructions")):
        lines = cfg.get(key) or []
        if lines:
            sections.append({"heading": title, "style": "bullets", "lines": list(lines)})
    addr = cfg.get("delivery_address") or []
    if addr:
        sections.append({"heading": "Address for Delivery of Materials:",
                         "style": "lines", "lines": list(addr)})
    if cfg.get("price_note"):
        sections.append({"heading": "Price:", "style": "lines",
                         "lines": [cfg["price_note"]]})
    dp = cfg.get("delivery_payment") or []
    if dp:
        sections.append({"heading": "Delivery and payments:", "style": "bullets",
                         "lines": list(dp)})

    return {
        "rfq_no": rfq_no,
        "vendor_name": vname,
        "header": header,
        "section_title": "Product Details: Raw Material",
        "items": blocks,
        "empty_note": "No raw material required for this project.",
        "total_text": f"Total approximate weight: {_num(total_kg)} Kg",
        "sections": sections,
    }


# ---------------------------------------------------------------- build ----

def build(path, project, vendor, items, rfq_no, cfg=None, validity_days=10):
    """
    Write one RFQ, addressed to one vendor.

    `items` comes from rm_items(). `vendor` is a record from vendors.py, or
    None for an unaddressed copy. Everything it says comes from content(); this
    function only decides how it looks on the page.
    """
    cfg = cfg or house_config()
    c = content(project, vendor, items, rfq_no, cfg, validity_days)

    doc = Document()
    sec = doc.sections[0]
    sec.left_margin = sec.right_margin = Cm(2.0)
    sec.top_margin = sec.bottom_margin = Cm(1.6)
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(11.5)

    # Logo in the page HEADER, not in the body, so it repeats on every page -
    # an RFQ runs to three or four pages and a vendor should be able to tell
    # who sent page 3. Same file the reimbursement sheet uses.
    logo = _logo_path()
    if logo:
        hp = sec.header.paragraphs[0]
        hp.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        hp.add_run().add_picture(logo, width=Cm(4.2))

    # --- header table -------------------------------------------------------
    w = [8.5, 8.5]
    t = doc.add_table(rows=0, cols=2)
    t.style = "Table Grid"
    _fixed(t, w)
    for left, right in c["header"]:
        row = t.add_row()
        for i, txt in enumerate((left, right)):
            row.cells[i].width = Cm(w[i])
            row.cells[i].text = ""
            par = row.cells[i].paragraphs[0]
            par.paragraph_format.space_after = Pt(0)
            rr = par.add_run(txt)
            rr.font.size = Pt(11)

    doc.add_paragraph()
    _p(doc, "REQUEST FOR QUOTATION", size=19, bold=True, after=2,
       align=WD_ALIGN_PARAGRAPH.CENTER)
    _p(doc, f"RFQ No: {c['rfq_no']}", size=11, after=10,
       align=WD_ALIGN_PARAGRAPH.CENTER)

    _heading(doc, c["section_title"])
    doc.add_paragraph()

    if c["items"]:
        for blk in c["items"]:
            _item_block(doc, blk["n"], blk["rows"])
    else:
        _p(doc, c["empty_note"], italic=True)

    _p(doc, c["total_text"], bold=True, after=10)

    for s in c["sections"]:
        _heading(doc, s["heading"])
        if s["style"] == "bullets":
            _bullets(doc, s["lines"])
        else:
            for line in s["lines"]:
                _p(doc, line, size=11.5, after=1)

    doc.save(path)
    return path


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(text)).strip("_")
