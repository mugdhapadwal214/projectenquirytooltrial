"""
rfq.py — a first-cut Request for Quotation from a finished project.

STATUS: PLACEHOLDER FORMAT
    The layout here is a reasonable default, not your house format. It exists
    so the button does something real and the plumbing is proven; replace the
    sections once the actual format is available.

    Two things are deliberately stubbed rather than invented, and both are
    marked in the output so they cannot be mistaken for real data:

      * (removed) Heat treatment / secondary processes. Was an explicit
        placeholder block.
      * Material specification — grade, composition limits, MTC requirement,
        test certificate class. There is no materials database behind this yet,
        so the document states the grade the user selected and says the
        specification is to follow. Printing a made-up composition would be far
        worse than printing nothing: someone downstream would order to it.

WHY HTML
    It opens in any browser, prints to PDF from there, and needs no extra
    dependency. When the real format arrives it can move to .docx if that is
    what the buying team circulates — python-docx is already available.
"""
import datetime
import html


def _esc(v):
    return html.escape(str(v if v is not None else ""))


def _money(v):
    try:
        return f"&#8377;{float(v):,.0f}"
    except (TypeError, ValueError):
        return "&#8377;0"


def _num(v, d=2):
    try:
        return f"{float(v):,.{d}f}"
    except (TypeError, ValueError):
        return "0"


def collect(project, summary, jobs, plates):
    """
    Reduce a project down to the lines an RFQ actually asks for.

    Plate, purchased components and cutting are quoted differently and often by
    different suppliers, so they are gathered as three separate schedules
    rather than one merged list.
    """
    plate_lines, bought_lines = [], []
    for j in jobs:
        for ln in j.get("lines", []):
            if ln.get("procurement") == "purchased":
                bought_lines.append(dict(ln, job=j["id"]))
            else:
                plate_lines.append(dict(ln, job=j["id"]))

    # Roll identical purchased items together — a buyer wants one line for
    # eight identical flanges, not eight lines.
    merged = {}
    for ln in bought_lines:
        key = (ln.get("component_type"), ln.get("spec"), ln.get("material"))
        e = merged.setdefault(key, dict(ln, qty=0, purchase_cost=0.0))
        e["qty"] += int(ln.get("qty") or 0)
        e["purchase_cost"] += float(ln.get("purchase_cost") or 0)

    # Plate comes from the BOOKED jobs first. An RFQ is normally raised on a
    # finished project, when nothing is on screen — reading only the live
    # result would leave the plate schedule empty exactly when it matters.
    # The passed-in `plates` is a fallback for quoting work not yet booked.
    booked = {}
    for j in jobs:
        for p in (j.get("totals", {}) or {}).get("plates", []) or []:
            key = (p.get("material"), p.get("thickness"), p.get("size"))
            e = booked.setdefault(key, {"material": p.get("material"),
                                        "thickness": p.get("thickness"),
                                        "size": p.get("size"),
                                        "qty": 0, "weight_kg": 0.0, "cost": 0.0})
            e["qty"] += int(p.get("qty") or 0)
            e["weight_kg"] += float(p.get("weight_kg") or 0)
            e["cost"] += float(p.get("cost") or 0)

    # Attach the full material record for every material actually used, so the
    # specification section prints real composition and property limits rather
    # than "to be advised". Anything the master does not know about is passed
    # through as a bare name and shows as unknown — better an obvious gap than
    # a confident blank.
    try:
        from .database import material_info
    except ImportError:
        from database import material_info

    used = []
    for m in sorted({ln.get("material") for ln in plate_lines if ln.get("material")}
                    | {p.get("material") for p in (list(booked.values())
                       or plates or []) if p.get("material")}):
        rec = material_info(m)
        used.append(rec if rec else {"name": m, "unknown": True})

    return {
        "plate_lines": plate_lines,
        "bought_lines": list(merged.values()),
        "plates": list(booked.values()) or (plates or []),
        "materials": used,
        "summary": summary or {},
        "project": project or {},
    }


def render_html(data):
    p = data["project"]
    s = data["summary"]
    today = datetime.date.today().strftime("%d %b %Y")
    ref = f"RFQ/{p.get('id', 'P-0000')}/{datetime.date.today().strftime('%Y%m%d')}"

    def rows(items, cols):
        if not items:
            return ('<tr><td colspan="%d" class="empty">Nothing in this '
                    'schedule.</td></tr>' % len(cols))
        out = []
        for i, it in enumerate(items, 1):
            cells = "".join(f"<td>{c(it)}</td>" for c in cols)
            out.append(f"<tr><td>{i}</td>{cells}</tr>")
        return "".join(out)

    plate_rows = rows(data["plates"], [
        lambda x: _esc(x.get("material")),
        lambda x: _num(x.get("thickness"), 1),
        lambda x: _esc(x.get("size")),
        lambda x: x.get("qty"),
        lambda x: _num(x.get("weight_kg"), 1),
        lambda x: _money(x.get("cost")),
    ])

    bought_rows = rows(data["bought_lines"], [
        lambda x: _esc(x.get("component_type")),
        lambda x: _esc(x.get("spec") or x.get("label")),
        lambda x: _esc(x.get("material")),
        lambda x: x.get("qty"),
        lambda x: _money(x.get("purchase_cost")),
    ])

    cut_rows = rows(data["plate_lines"], [
        lambda x: _esc(x.get("component_type")),
        lambda x: _esc(x.get("label")),
        lambda x: _esc(x.get("spec")),
        lambda x: _esc(x.get("material")),
        lambda x: x.get("qty"),
        lambda x: _num(x.get("weight_kg"), 2),
    ])

    def _mat_block(i, rec):
        if rec.get("unknown"):
            return (f"<tr><td>{i}</td><td>{_esc(rec.get('name'))}</td>"
                    f'<td class="todo" colspan="2">Not in the material master '
                    f'\u2014 specification unavailable.</td></tr>')
        comp = ", ".join(f"{k} {v}" for k, v in (rec.get("composition") or {}).items())
        mech = "<br>".join(f"{k}: {v}" for k, v in (rec.get("mechanical") or {}).items())
        return (
            f"<tr><td>{i}</td><td><b>{_esc(rec.get('name'))}</b>"
            f"<div class='sub'>{_esc(rec.get('condition') or '')}</div></td>"
            f"<td>{_esc(rec.get('standard'))}"
            f"<div class='sub'>Grade {_esc(rec.get('grade'))}</div></td>"
            f"<td>{_esc(rec.get('certification'))}</td></tr>"
            f"<tr class='matdetail'><td></td>"
            f"<td colspan='3'><b>Composition (wt %)</b><div class='sub'>{_esc(comp)}</div>"
            f"<b>Mechanical (MPa / %)</b><div class='sub'>{mech}</div>"
            # The material master carries a `notes` field, but it is internal
            # working commentary — unverified figures, open questions, things
            # to confirm. None of that belongs in a document going to a
            # supplier, so it is deliberately not printed.
            + "</td></tr>")

    mat_recs = data.get("materials") or []
    mat_rows = "".join(_mat_block(i, r) for i, r in enumerate(mat_recs, 1)) or \
        '<tr><td colspan="4" class="empty">No materials recorded.</td></tr>'

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>{_esc(ref)} — {_esc(p.get('name'))}</title>
<style>
 body{{font:13px/1.55 "Segoe UI",Roboto,system-ui,sans-serif;color:#1b1f27;
   max-width:920px;margin:28px auto;padding:0 24px}}
 h1{{font-size:20px;margin:0 0 2px}}
 h2{{font-size:13px;text-transform:uppercase;letter-spacing:.6px;color:#1f3864;
   margin:26px 0 8px;border-bottom:2px solid #1f3864;padding-bottom:4px}}
 .sub{{color:#667085;font-size:12px}}
 table{{width:100%;border-collapse:collapse;margin-top:6px;font-size:12px}}
 th{{background:#eef1f6;color:#1f3864;text-align:left;padding:6px 8px;
   border:1px solid #d5dbe4;font-size:11px;text-transform:uppercase;
   letter-spacing:.4px}}
 td{{padding:6px 8px;border:1px solid #e3e7ee;vertical-align:top}}
 td:first-child{{width:36px;color:#667085}}
 .empty{{text-align:center;color:#667085;font-style:italic}}
 .todo{{color:#b06a00;font-style:italic}}
 tr.matdetail td{{background:#fafbfc;font-size:11px;padding-top:4px}}
 tr.matdetail b{{display:block;margin-top:5px;color:#1f3864;font-size:10.5px;
   text-transform:uppercase;letter-spacing:.4px}}
 .note-inline{{color:#b06a00;margin-top:5px}}
 .meta{{width:100%;margin-top:10px}}
 .meta td{{border:none;padding:2px 0}}
 .meta td:first-child{{width:150px;color:#667085}}
 .note{{background:#fffaf2;border:1px solid #e8d3b5;padding:10px 12px;
   border-radius:5px;color:#7a5200;font-size:12px;margin-top:8px}}
 .totals td{{font-weight:600}}
 .totals tr:last-child td{{border-top:2px solid #1f3864;font-size:14px}}
 @media print{{body{{margin:0}} .noprint{{display:none}}}}
</style></head><body>

<h1>Request for Quotation</h1>
<div class="sub">{_esc(ref)}</div>

<table class="meta">
  <tr><td>Project</td><td><b>{_esc(p.get('name'))}</b></td></tr>
  <tr><td>Project reference</td><td>{_esc(p.get('id'))}</td></tr>
  <tr><td>Client</td><td>{_esc(p.get('client') or '—')}</td></tr>
  <tr><td>Date of issue</td><td>{today}</td></tr>
  <tr><td>Quotation due by</td><td class="todo">To be advised</td></tr>
</table>

<h2>1 &nbsp;Scope</h2>
<p>Quotations are invited for the supply of plate, the cutting of the profiles
listed below, and the supply of the bought-out components scheduled in
Section 3. Quote each schedule separately; they may be awarded separately.</p>

<h2>2 &nbsp;Plate to be supplied</h2>
<table><thead><tr><th>#</th><th>Material</th><th>Thk (mm)</th><th>Plate size</th>
<th>Qty</th><th>Weight (kg)</th><th>Value</th></tr></thead>
<tbody>{plate_rows}</tbody></table>

<h2>3 &nbsp;Bought-out components</h2>
<table><thead><tr><th>#</th><th>Type</th><th>Description</th><th>Material</th>
<th>Qty</th><th>Value</th></tr></thead>
<tbody>{bought_rows}</tbody></table>

<h2>4 &nbsp;Profiles to be cut</h2>
<table><thead><tr><th>#</th><th>Component</th><th>Description</th><th>Blank</th>
<th>Material</th><th>Qty</th><th>Weight (kg)</th></tr></thead>
<tbody>{cut_rows}</tbody></table>

<h2>5 &nbsp;Material specification and certification</h2>
<table><thead><tr><th>#</th><th>Material</th><th>Specification / grade</th>
<th>Certification required</th></tr></thead>
<tbody>{mat_rows}</tbody></table>

<h2>6 &nbsp;Commercial summary</h2>
<table class="totals">
  <tr><td>Plate material</td><td>{_money(s.get('plate_cost'))}</td></tr>
  <tr><td>Cutting</td><td>{_money(s.get('cut_cost'))}</td></tr>
  <tr><td>Bought-out components</td><td>{_money(s.get('purchase_cost'))}</td></tr>
  <tr><td>Estimated total</td><td>{_money(s.get('grand_total'))}</td></tr>
</table>
<p class="sub">Figures above are the estimator's own working values, given for
scope only. They are not a budget, a target price, or a ceiling.</p>

<h2>7 &nbsp;Terms</h2>
<table class="meta">
  <tr><td>Delivery</td><td class="todo">To be advised</td></tr>
  <tr><td>Delivery address</td><td class="todo">To be advised</td></tr>
  <tr><td>Payment terms</td><td class="todo">To be advised</td></tr>
  <tr><td>Validity of offer</td><td class="todo">To be advised</td></tr>
  <tr><td>Inspection</td><td class="todo">To be advised</td></tr>
</table>

<p class="sub" style="margin-top:26px">Generated by RM Estimator on {today}.
Raw material only. Items marked <span class="todo">to be advised</span> are for
the buyer to complete before issue.</p>

</body></html>"""
