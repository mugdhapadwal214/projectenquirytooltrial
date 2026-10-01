"""
app.py — UI glue for the RM Estimator.

Serves the frontend (templates/index.html + static/) and exposes a small
JSON API that the frontend calls. All engineering logic lives in
calculator.py; all reference data lives in database.py / meta.py. This
file should not contain any math.

Run standalone (browser):   python app.py --web
Run as desktop app:         python app.py            (uses pywebview)
"""

import sys
import threading
import webbrowser
import platform

from flask import (Flask, render_template, request, jsonify, Response,
                   session, redirect, url_for)

# Same try/except relative-import pattern used across the Reliability app,
# so this module works both as part of a package and run directly.
import json
import os
import secrets
import shutil
import re

try:
    import inventory
    import jobs
    import pipe_database
    import projects
    import filesave
    import vendors
    import users
    import approvals
    import mailer
    import rfq_rm
    import runtime_paths
    import rfq_docx
    import bom_import
    import reimburse
    import rfq_archive
    import rfq
    from calculator import estimate, CUT_GAP_FACTOR
    from database import (
    MATERIALS,
    STANDARD_PLATES,
    get_flange_classes,
    get_flange_nps,
    get_flange_types,
    get_flange_from_database,
)
    from meta import COMPONENT_SCHEMAS, list_component_types, legend_for
except ImportError:
    import inventory
    import jobs
    import pipe_database
    import projects
    import filesave
    import vendors
    import users
    import approvals
    import mailer
    import rfq_rm
    import runtime_paths
    import rfq_docx
    import bom_import
    import reimburse
    import rfq_archive
    import rfq
    from calculator import estimate, CUT_GAP_FACTOR
    from database import (
    MATERIALS,
    STANDARD_PLATES,
    get_flange_classes,
    get_flange_nps,
    get_flange_types,
    get_flange_from_database,
)
    from meta import COMPONENT_SCHEMAS, list_component_types, legend_for


CHAPTER_ID = "rm_estimator"

# Set by main() when a .rmp is double-clicked. The page asks for it once on
# load and opens that project.
OPEN_ON_START = None

CUT_CFG_FILE = runtime_paths.data_path("cutting.json")


def cut_defaults():
    """Cutting settings from data/cutting.json, editable without a rebuild."""
    try:
        with open(CUT_CFG_FILE, "r", encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {"rate_per_m": 0, "pierce_charge": 0, "remnant_charge_pct": 0,
                "min_side_mm": 200, "min_area_m2": 0.10}

app = Flask(__name__)


def _secret_key():
    """
    Signing key for the session cookie.

    Generated once and kept beside the data, not hardcoded: a key baked into
    the source would be identical in every copy of the app, and anyone with
    the source could forge a session. Persisted so a restart does not sign
    everyone out.
    """
    path = runtime_paths.data_path("session.key")
    try:
        with open(path, "r", encoding="utf-8") as f:
            k = f.read().strip()
        if k:
            return k
    except OSError:
        pass
    k = secrets.token_hex(32)
    with open(path, "w", encoding="utf-8") as f:
        f.write(k)
    return k


app.secret_key = _secret_key()
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Reachable without signing in. Everything else is guarded.
OPEN_ENDPOINTS = {"login", "logout", "static"}


@app.before_request
def require_login():
    """
    One guard for the whole app.

    Applied here rather than decorating each route: with thirty-odd endpoints,
    a per-route decorator is one forgotten line away from an open API. The
    default is closed and the exceptions are listed above.
    """
    if request.endpoint in OPEN_ENDPOINTS or request.endpoint is None:
        return None
    if session.get("email"):
        return None
    if request.path.startswith("/api/"):
        return jsonify({"error": "Not signed in", "login_required": True}), 401
    return redirect(url_for("login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    ctx = {"designations": users.DESIGNATIONS, "message": "", "ok": False,
           "panel": "in"}

    if request.method == "POST":
        action = request.form.get("action")
        email = request.form.get("email", "")
        password = request.form.get("password", "")

        if action == "register":
            ctx["panel"] = "up"
            try:
                users.create(email, password, request.form.get("name", ""),
                             request.form.get("designation", ""))
            except ValueError as exc:
                ctx["message"] = str(exc)
                return render_template("login.html", **ctx)
            # Straight in after registering - there is no verification step to
            # wait for, and bouncing back to a sign-in form would just be a
            # second thing to type.
            u = users.verify(email, password)
            session["email"] = u["email"]
            session["name"] = u["name"]
            session["designation"] = u["designation"]
            return redirect(url_for("index"))

        u = users.verify(email, password)
        if not u:
            # Deliberately does not say which half was wrong.
            ctx["message"] = "Email or password is incorrect"
            return render_template("login.html", **ctx)
        session["email"] = u["email"]
        session["name"] = u["name"]
        session["designation"] = u["designation"]
        return redirect(url_for("index"))

    if not users.count():
        ctx["panel"] = "up"
        ctx["message"] = "No accounts yet \u2014 create the first one."
        ctx["ok"] = True
    return render_template("login.html", **ctx)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/api/me")
def api_me():
    return jsonify({"email": session.get("email"), "name": session.get("name"),
                    "designation": session.get("designation"),
                    "designations": users.DESIGNATIONS})


# ------------------------------------------------------------------ pages --

@app.route("/")
def index():
    return render_template(
        "index.html",
        component_types=list_component_types(),
        materials=list(MATERIALS.keys()),
        cut_gap_factor=CUT_GAP_FACTOR,
        standard_plates=STANDARD_PLATES,
        cut_defaults=cut_defaults(),
        session_name=session.get("name", ""),
        session_designation=session.get("designation", ""),
    )


# -------------------------------------------------------------------- api --

@app.route("/api/schema/<component_type>")
def api_schema(component_type):
    schema = COMPONENT_SCHEMAS.get(component_type)
    if schema is None:
        return jsonify({"error": f"Unknown component type: {component_type}"}), 404
    # Flange selection is database-driven. The UI needs the available
    # pressure classes immediately; NPS and flange type are populated
    # progressively after each selection.
    fields = schema["fields"]
    if component_type == "Flange":
        fields = [dict(f) for f in fields]
        for f in fields:
            if f["key"] == "pressure_class":
                # Options must be strings: <select> onchange always hands back
                # a string, and pressure_class gets reassigned every time the
                # user picks a class. If the option list stays numeric while
                # the row's value turns into a string, later re-renders can't
                # match r.pressure_class to any <option>, so the dropdown
                # silently snaps back to the first entry.
                f["options"] = [str(c) for c in get_flange_classes()]
            elif f["key"] == "nps":
                first_class = get_flange_classes()[0]
                f["options"] = get_flange_nps(first_class)
            elif f["key"] == "flange_type":
                first_class = get_flange_classes()[0]
                first_nps = get_flange_nps(first_class)[0]
                f["options"] = get_flange_types(first_class, first_nps)

    return jsonify({
        "note": schema["note"],
        "fields": fields,
        "legend": legend_for(component_type),
    })

@app.route("/api/flange/classes")
def api_flange_classes():
    return jsonify({
        "classes": [str(c) for c in get_flange_classes()]
    })


@app.route("/api/flange/nps/<pressure_class>")
def api_flange_nps(pressure_class):
    try:
        return jsonify({
            "nps": get_flange_nps(pressure_class)
        })
    except (TypeError, ValueError):
        return jsonify({
            "error": "Invalid pressure class"
        }), 400


@app.route("/api/flange/types/<pressure_class>/<path:nps>")
def api_flange_types(pressure_class, nps):
    try:
        return jsonify({
            "types": get_flange_types(
                pressure_class,
                nps
            )
        })
    except (TypeError, ValueError):
        return jsonify({
            "error": "Invalid flange selection"
        }), 400


@app.route("/api/flange/detail", methods=["POST"])
def api_flange_detail():

    data = request.get_json(force=True)

    try:
        pressure_class = data["pressure_class"]
        nps = data["nps"]
        flange_type = data["flange_type"]

        flange = get_flange_from_database(
            pressure_class,
            nps,
            flange_type
        )

    except (KeyError, TypeError, ValueError):
        return jsonify({
            "error": "Invalid flange selection"
        }), 400

    if flange is None:
        return jsonify({
            "error": "Flange not found"
        }), 404

    return jsonify(flange)

@app.route("/api/pipe/nps")
def api_pipe_nps():
    return jsonify({"nps": pipe_database.nps_list()})


@app.route("/api/pipe/schedules/<path:nps>")
def api_pipe_schedules(nps):
    scheds = pipe_database.schedules(nps)
    if not scheds:
        return jsonify({"error": f"No schedules published for NPS {nps}"}), 400
    return jsonify({"schedules": scheds})


@app.route("/api/pipe/detail", methods=["POST"])
def api_pipe_detail():
    """
    Dimensions for one size/schedule. Material is optional and only affects
    kg/m — the chart is carbon steel, so a stainless pipe of the same size is
    rescaled rather than priced as steel.
    """
    d = request.get_json(force=True)
    density = MATERIALS.get(d.get("material")) if d.get("material") else None
    got = pipe_database.detail(d.get("nps"), d.get("schedule"), density)
    if not got:
        return jsonify({"error": f"NPS {d.get('nps')} SCH {d.get('schedule')} "
                                 f"is not published in the chart"}), 400
    return jsonify(got)


@app.route("/api/calculate", methods=["POST"])
def api_calculate():
    """
    Expected JSON body:
    {
        "parts": [ {"_type": "Dish End", "_row": 0, ...fields...,
                    "material": "Mild Steel", "thk": 5, "qty": 1, "rate": 100},
                   {"_type": "Shell", "_row": 3, ...} ],
        "standard_plate": {"width": 1250, "length": 2500}   // or null
        "combine": true
    }

    Parts of DIFFERENT component types arrive in one list. That is deliberate:
    a dish end and a shell of the same material and thickness can be cut from
    the same plate, so the grouping has to see them together. Each part
    carries its own rate; there is no global rate and no cutting process.
    """
    data = request.get_json(force=True)
    parts = data.get("parts", [])

    if not parts:
        return jsonify({"error": "No parts supplied"}), 400

    for p in parts:
        ctype = p.get("_type")
        if ctype not in COMPONENT_SCHEMAS:
            return jsonify({"error": f"Unknown component type: {ctype}"}), 400

    sp = data.get("standard_plate")
    if sp:
        try:
            sp = {"width": float(sp["width"]), "length": float(sp["length"])}
        except (KeyError, TypeError, ValueError):
            return jsonify({"error": "Invalid standard plate size"}), 400
        if sp["width"] <= 0 or sp["length"] <= 0:
            return jsonify({"error": "Standard plate size must be positive"}), 400

    cut_cfg = cut_defaults()
    cut_cfg.update({k: v for k, v in (data.get("cutting") or {}).items()
                    if v not in (None, "")})

    # Only offer the rack if the user asked for it. Estimating against stock
    # you have not committed to using would quietly understate a quotation.
    stock = inventory.available() if data.get("use_remnants") else []

    try:
        result = estimate(
            parts=parts,
            schemas=COMPONENT_SCHEMAS,
            materials=MATERIALS,
            standard_plate=sp,
            stock_sizes=STANDARD_PLATES,
            auto_plate=bool(data.get("auto_plate", True)),
            plate_overrides=data.get("plate_overrides") or {},
            combine=bool(data.get("combine", True)),
            cut_cfg=cut_cfg,
            remnant_stock=stock,
            remnant_only_row_ids=data.get("remnant_only_row_ids") or [],
        )
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"error": f"Invalid or missing part data: {exc}"}), 400

    return jsonify(result)


# ------------------------------------------------------------- inventory --

@app.route("/api/remnants")
def api_remnants():
    """Everything on the rack, plus a per-material roll-up."""
    items = inventory.available()
    summary = {}
    for r in items:
        k = f"{r['material']} @ {r['thickness']} mm"
        e = summary.setdefault(k, {"count": 0, "area_m2": 0.0})
        e["count"] += 1
        e["area_m2"] += r["area_m2"]
    return jsonify({"remnants": items, "summary": summary,
                    "total_area_m2": round(sum(r["area_m2"] for r in items), 3)})


@app.route("/api/remnants/add", methods=["POST"])
def api_remnants_add():
    """Add offcut by hand — for stock that predates the app."""
    d = request.get_json(force=True)
    try:
        item = {
            "material": d["material"],
            "thickness": float(d["thickness"]),
            "width": float(d["width"]),
            "length": float(d["length"]),
        }
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "material, thickness, width and length are required"}), 400
    if item["width"] <= 0 or item["length"] <= 0 or item["thickness"] <= 0:
        return jsonify({"error": "Sizes must be greater than zero"}), 400
    if item["material"] not in MATERIALS:
        return jsonify({"error": f"Unknown material: {item['material']}"}), 400
    return jsonify({"added": inventory.add([item], source=d.get("source") or "added by hand")})


@app.route("/api/remnants/remove", methods=["POST"])
def api_remnants_remove():
    d = request.get_json(force=True)
    rid = d.get("id")
    if not rid:
        return jsonify({"error": "id is required"}), 400
    return jsonify({"removed": inventory.remove(rid)})


@app.route("/api/remnants/auto-stock", methods=["POST"])
def api_remnants_auto_stock():
    """Leftover of the calculation on screen, mirrored into the Store of the
    Project Enquiry tool. Replaces the same project's previous leftover, so a
    what-if cannot inflate the stock."""
    d = request.get_json(force=True)
    kept = inventory.sync_auto(d.get("project"), d.get("harvest") or [],
                               note=d.get("note") or "")
    return jsonify({"stocked": len(kept), "rows": kept})


@app.route("/api/remnants/commit", methods=["POST"])
def api_remnants_commit():
    """
    Book a job against the rack: retire the remnants it consumed and add the
    new offcut it created. Deliberately a separate, explicit step — estimating
    must never change stock on its own, or every what-if would corrupt the rack.
    """
    d = request.get_json(force=True)
    source = d.get("source") or "job"
    inventory.drop_auto(d.get("project"))      # the provisional rows become real ones now
    for rid in d.get("remnants_used") or []:
        inventory.consume(rid, source=source)
    added = inventory.add(d.get("harvest") or [], source=source)

    # Record the job line by line. Booking removes the parts from the working
    # table, so without this the history of what has actually been made would
    # be lost the moment the offcut hit the rack.
    rec = None
    superseded = []
    pid = d.get("project")
    if d.get("lines") and pid and projects.entry(pid):
        # Parts reopened from an earlier job are being booked again. Drop the
        # job they came from FIRST, or the project counts them twice - and the
        # summary, the raw material weight and the RFQ all inflate together.
        superseded = projects.drop_jobs(pid, d.get("supersedes") or [])
        tot = dict(d.get("job_totals") or {})
        tot["remnants_used"] = len(d.get("remnants_used") or [])
        tot["remnants_racked"] = len(added)
        rec = projects.add_job(pid, source, d["lines"], tot)

    return jsonify({"consumed": d.get("remnants_used") or [],
                    "added": added,
                    "job": rec,
                    "superseded": superseded})


@app.route("/api/jobs")
def api_jobs():
    """
    Booked jobs and running totals for the summary page.

    Scoped to a project. Without one there is nothing to report — the ledger
    belongs to a project, not to the installation.
    """
    pid = request.args.get("project")
    if not pid or not projects.entry(pid):
        return jsonify({"jobs": [], "summary": projects.summary("P-0000")
                        if False else _empty_summary()})
    return jsonify({"jobs": projects.jobs(pid), "summary": projects.summary(pid)})


def _empty_summary():
    return {"jobs": 0, "parts_cost": 0, "made_qty": 0, "bought_qty": 0,
            "total_qty": 0, "plate_cost": 0, "cut_cost": 0, "purchase_cost": 0,
            "grand_total": 0, "plate_weight_kg": 0, "plates_bought": 0,
            "by_type": {}}


@app.route("/api/jobs/clear", methods=["POST"])
def api_jobs_clear():
    d = request.get_json(force=True)
    pid = d.get("project")
    if not pid or not projects.entry(pid):
        return jsonify({"error": "No project selected"}), 400
    res = projects.clear_jobs(pid)
    # Say what survived, not just that something happened. The rack is the
    # part people are surprised by: steel that was cut stays cut, and offcuts
    # those jobs produced stay on the shelf, so the physical stock no longer
    # matches a history that is now empty.
    res["rack_pieces"] = len(inventory.available())
    res["ok"] = True
    return jsonify(res)


# ------------------------------------------------------------- projects --

@app.route("/api/startup")
def api_startup():
    """
    Anything the page needs to know once, at load.

    Currently just the project to open when the app was launched by
    double-clicking a .rmp. Cleared after it is read, so a browser refresh does
    not keep reopening the same file.
    """
    global OPEN_ON_START
    pid = OPEN_ON_START
    OPEN_ON_START = None
    return jsonify({"open_project": pid})


@app.route("/api/projects")
def api_projects():
    return jsonify({"projects": projects.all_projects()})


@app.route("/api/projects/create", methods=["POST"])
def api_projects_create():
    d = request.get_json(force=True)
    missing = [lbl for lbl, key in (("Project Number", "number"),
                                    ("Project Name", "name"))
               if not (d.get(key) or "").strip()]
    if missing:
        return jsonify({"error": f"{' and '.join(missing)} "
                                 f"{'are' if len(missing) > 1 else 'is'} required"}), 400
    return jsonify({"project": projects.create(
        d["name"], d.get("client", ""), d.get("notes", ""), d.get("number", ""))})


@app.route("/api/projects/<pid>")
def api_project_open(pid):
    e = projects.entry(pid)
    if not e:
        return jsonify({"error": f"No project {pid}"}), 404
    return jsonify({"project": e, "state": projects.load_state(pid)})


@app.route("/api/projects/<pid>/progress")
def api_project_progress(pid):
    """
    What the project has actually committed, for the status board.

    The board used to read only the in-memory result of the last Calculate.
    That is gone the moment the project is reopened, so a project with six
    plates already booked came back tomorrow reading "not calculated yet" -
    the board was describing the browser session, not the project.
    """
    e = projects.entry(pid)
    if not e:
        return jsonify({"error": f"No project {pid}"}), 404
    s = projects.summary(pid) or {}
    jobs = projects.jobs(pid) or []
    return jsonify({
        "jobs": len(jobs),
        "plates": int(s.get("plates") or s.get("plates_required") or 0),
        "scrap_kg": float(s.get("scrap_kg") or 0),
        "total_cost": float(s.get("total_cost") or 0),
        "last_job": (jobs[-1].get("created") if jobs else "") or "",
    })


@app.route("/api/projects/<pid>/save", methods=["POST"])
def api_project_save(pid):
    if not projects.entry(pid):
        return jsonify({"error": f"No project {pid}"}), 404
    d = request.get_json(force=True)
    projects.save_state(pid, d.get("state") or {})
    return jsonify({"saved": True, "project": projects.entry(pid)})


@app.route("/api/projects/<pid>/rename", methods=["POST"])
def api_project_rename(pid):
    d = request.get_json(force=True)
    e = projects.rename(pid, d.get("name"), d.get("client"), d.get("notes"))
    if not e:
        return jsonify({"error": f"No project {pid}"}), 404
    return jsonify({"project": e})


@app.route("/api/projects/<pid>/delete", methods=["POST"])
def api_project_delete(pid):
    return jsonify({"deleted": projects.delete(pid)})


@app.route("/api/projects/<pid>/export")
def api_project_export(pid):
    """Download the project as a single self-contained .rmp file."""
    doc = projects.export(pid)
    if not doc:
        return jsonify({"error": f"No project {pid}"}), 404
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", doc["project"]["name"]).strip("_")
    body = json.dumps(doc, indent=1)
    return Response(body, mimetype="application/json", headers={
        "Content-Disposition": f'attachment; filename="{name or pid}.rmp"'})


@app.route("/api/projects/<pid>/save-file", methods=["POST"])
def api_project_save_file(pid):
    """
    Write the .rmp to disk.

    A browser download is useless in the desktop window — pywebview has no
    download manager, which is why "Save as file" appeared to do nothing.
    This writes the file for real and reports where it went.
    """
    doc = projects.export(pid)
    if not doc:
        return jsonify({"error": f"No project {pid}"}), 404
    base = doc["project"].get("number") or doc["project"].get("name") or pid
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(base)).strip("_") or pid
    res = filesave.write(json.dumps(doc, indent=1), f"{name}.rmp",
                         sub="saved_projects",
                         file_types=("RM Estimator project (*.rmp)",))
    if res["cancelled"]:
        return jsonify({"cancelled": True})
    return jsonify(res)


@app.route("/api/reveal", methods=["POST"])
def api_reveal():
    d = request.get_json(force=True)
    path = d.get("path")
    if not path:
        return jsonify({"error": "path is required"}), 400
    return jsonify({"opened": filesave.reveal(path)})


@app.route("/api/projects/import", methods=["POST"])
def api_project_import():
    d = request.get_json(force=True)
    try:
        e = projects.import_file(d.get("file"), d.get("name"))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"project": e})


@app.route("/api/projects/<pid>/rfq")
def api_project_rfq(pid):
    """
    Basic RFQ for a project. Placeholder format — see rfq.py.

    Served inline so it opens in a tab and can be read, printed to PDF, or
    saved from the browser. Add ?download=1 to force a file instead. It was
    previously always an attachment, which meant clicking Generate RFQ
    downloaded a file the user then had to go and find.
    """
    e = projects.entry(pid)
    if not e:
        return jsonify({"error": f"No project {pid}"}), 404
    plates = json.loads(request.args.get("plates") or "[]")
    doc = rfq.collect(e, projects.summary(pid), projects.jobs(pid), plates)
    html_doc = rfq.render_html(doc)

    headers = {}
    if request.args.get("download"):
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", e["name"]).strip("_") or pid
        headers["Content-Disposition"] = f'attachment; filename="RFQ_{name}.html"'
    return Response(html_doc, mimetype="text/html", headers=headers)


@app.route("/api/projects/<pid>/rfq-docx", methods=["POST"])
def api_project_rfq_docx(pid):
    """
    Write the RFQ as a Word document.

    Word rather than HTML because an RFQ is edited before it goes out — terms
    added, schedules removed, dates set. The buying team needs the file they
    can actually work in.
    """
    e = projects.entry(pid)
    if not e:
        return jsonify({"error": f"No project {pid}"}), 404

    body = request.get_json(silent=True) or {}
    plates = body.get("plates") or []
    doc = rfq.collect(e, projects.summary(pid), projects.jobs(pid), plates)

    base = e.get("number") or e.get("name") or pid
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", str(base)).strip("_") or pid
    filename = f"RFQ_{name}.docx"

    # Build into a temp file first, then hand the bytes to the saver, so a
    # cancelled Save dialog leaves nothing behind.
    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), filename)
    rfq_docx.build(doc, tmp)
    with open(tmp, "rb") as f:
        data = f.read()
    try:
        os.remove(tmp)
    except OSError:
        pass

    res = filesave.write(data, filename, sub="rfq",
                         file_types=("Word document (*.docx)",))
    if res["cancelled"]:
        return jsonify({"cancelled": True})
    return jsonify(res)


# ---------------------------------------------------------------- vendors --

@app.route("/api/vendors")
def api_vendors():
    return jsonify({"vendors": vendors.all_vendors(),
                    "categories": vendors.categories(),
                    "by_category": {k: [v["id"] for v in vs]
                                    for k, vs in vendors.by_category().items()}})


@app.route("/api/vendors/save", methods=["POST"])
def api_vendors_save():
    d = request.get_json(force=True)
    vid = d.get("id")
    try:
        rec = vendors.update(vid, d) if vid else vendors.add(d)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    if rec is None:
        return jsonify({"error": f"No vendor {vid}"}), 404
    return jsonify({"vendor": rec})


@app.route("/api/vendors/delete", methods=["POST"])
def api_vendors_delete():
    d = request.get_json(force=True)
    return jsonify({"removed": vendors.delete(d.get("id"))})


# ------------------------------------------------------- raw material RFQ --

def _rm_items_for(pid, plates):
    """
    What this project has to BUY. Plate comes from the booked ledger first —
    an RFQ is normally raised on a finished job, when nothing is on screen —
    with anything unbooked passed in as a fallback.
    """
    booked = {}
    for j in projects.jobs(pid):
        for p in (j.get("totals", {}) or {}).get("plates", []) or []:
            key = (p.get("material"), p.get("thickness"), p.get("size"))
            e = booked.setdefault(key, {"material": p.get("material"),
                                        "thickness": p.get("thickness"),
                                        "size": p.get("size"),
                                        "qty": 0, "weight_kg": 0.0})
            e["qty"] += int(p.get("qty") or 0)
            e["weight_kg"] += float(p.get("weight_kg") or 0)

    pipes = {}
    for j in projects.jobs(pid):
        for ln in j.get("lines", []) or []:
            if ln.get("component_type") != "Pipe":
                continue
            key = (ln.get("material"), ln.get("spec"))
            # Older jobs recorded only the formatted spec string, so fall back
            # to reading the size out of it rather than printing a blank RFQ.
            nps, sch = ln.get("nps") or "", ln.get("schedule") or ""
            if not nps or not sch:
                m = re.search(r'NPS\s*([^"\s]+)"?\s*(?:\u00b7|\u2013|-)?\s*SCH\s*(\S+)',
                              str(ln.get("spec") or ""))
                if m:
                    nps = nps or m.group(1)
                    sch = sch or m.group(2)
            e = pipes.setdefault(key, {"material": ln.get("material"),
                                       "nps": nps, "schedule": sch,
                                       "thickness": ln.get("thickness"),
                                       "length_m": float(ln.get("length_m") or 0),
                                       "qty": 0, "weight_kg": 0.0})
            e["qty"] += int(ln.get("qty") or 0)
            e["weight_kg"] += float(ln.get("weight_kg") or 0)

    # Bar, grouped by material and by the exact piece it is cut into. Two
    # different cut lengths off the same diameter are two different orders,
    # because the pieces-per-bar arithmetic is different for each.
    bars = {}
    customs = []
    for j in projects.jobs(pid):
        for ln in j.get("lines", []) or []:
            ct = ln.get("component_type")
            if ct == "Round Bar":
                key = (ln.get("material"), ln.get("bar_dia"), ln.get("bar_piece_mm"))
                e = bars.setdefault(key, {
                    "material": ln.get("material"),
                    "dia": float(ln.get("bar_dia") or 0),
                    "piece_length_mm": float(ln.get("bar_piece_mm") or 0),
                    "qty": 0, "weight_kg": 0.0})
                e["qty"] += int(ln.get("qty") or 0)
                e["weight_kg"] += float(ln.get("weight_kg") or 0)
            elif ct == "Custom":
                customs.append({
                    "label": ln.get("label") or ln.get("part_name") or "Custom part",
                    "material": ln.get("material") or "",
                    "dimensions": ln.get("dimensions") or ln.get("spec") or "",
                    "remarks": ln.get("remarks") or "",
                    "qty": int(ln.get("qty") or 0),
                })

    return rfq_rm.rm_items(list(booked.values()) or (plates or []),
                           list(pipes.values()),
                           bars=[b for b in bars.values() if b["dia"] > 0],
                           customs=customs)


@app.route("/api/projects/<pid>/rm-items", methods=["POST"])
def api_rm_items(pid):
    body = request.get_json(silent=True) or {}
    items = _rm_items_for(pid, body.get("plates") or [])
    return jsonify({"items": items,
                    "total_kg": sum(i["weight_kg"] for i in items)})


# --------------------------------------------------------------- approvals --

def _approver_emails(designation):
    return [u["email"] for u in users.all_users()
            if u["designation"] == designation]


def _me():
    return {"name": session.get("name"), "email": session.get("email"),
            "designation": session.get("designation")}


@app.route("/api/approvals")
def api_approvals():
    """Everything relevant to the signed-in user."""
    role = session.get("designation")
    pid = request.args.get("project")
    # Who actually holds each role, so the chain shows names and not just
    # titles. An approver who has not registered yet is worth seeing as a gap
    # rather than discovering when the request stalls on them.
    holders = {}
    for g in approvals.CHAIN:
        holders[g] = [{"name": u["name"], "email": u["email"]}
                      for u in users.all_users() if u["designation"] == g]

    return jsonify({
        "mine": approvals.pending_for(role),
        "project": approvals.all_requests(pid) if pid else [],
        "open": approvals.open_request_for(pid) if pid else None,
        "chain": approvals.CHAIN,
        "holders": holders,
        "my_role": role,
        "mail_configured": mailer.is_configured(),
    })


@app.route("/api/projects/<pid>/submit-approval", methods=["POST"])
def api_submit_approval(pid):
    e = projects.entry(pid)
    if not e:
        return jsonify({"error": f"No project {pid}"}), 404
    body = request.get_json(force=True)
    items = _rm_items_for(pid, body.get("plates") or [])
    picked = [v for v in (vendors.get(i) for i in body.get("vendor_ids") or []) if v]
    try:
        rec = approvals.submit(e, items, picked, _me(), body.get("note", ""))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    # Tell whoever is first in the chain. Mail may not be configured; the
    # outbox records it either way so the request is never invisible.
    step = approvals.next_step(rec)
    note = None
    if step:
        note = mailer.notify_approver(rec, step["designation"],
                                      _approver_emails(step["designation"]))
    return jsonify({"request": rec, "mail": note})


@app.route("/api/approvals/<rid>/act", methods=["POST"])
def api_approval_act(rid):
    body = request.get_json(force=True)
    action = body.get("action")
    comment = body.get("comment", "")
    me = _me()
    try:
        if action == "approve":
            rec = approvals.approve(rid, me, comment)
        elif action == "reject":
            rec = approvals.reject(rid, me, comment)
        elif action == "withdraw":
            return jsonify({"withdrawn": approvals.withdraw(rid, me)})
        else:
            return jsonify({"error": "Unknown action"}), 400
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    mail = None
    if rec["status"] == approvals.REJECTED:
        mail = mailer.notify_outcome(
            rec, "rejected", me, comment,
            [rec["submitted_by"].get("email")] if rec["submitted_by"].get("email") else [])
    elif rec["status"] == approvals.APPROVED:
        mail = mailer.notify_outcome(
            rec, "fully approved", me, comment,
            [rec["submitted_by"].get("email")] if rec["submitted_by"].get("email") else [])
    else:
        step = approvals.next_step(rec)
        if step:
            mail = mailer.notify_approver(rec, step["designation"],
                                          _approver_emails(step["designation"]))
    return jsonify({"request": rec, "mail": mail})


@app.route("/api/outbox")
def api_outbox():
    return jsonify({"messages": mailer.outbox(),
                    "configured": mailer.is_configured()})


@app.route("/api/rfq-archive")
def api_rfq_archive():
    """Every RFQ issued, newest first. `?project=<id>` narrows it."""
    return jsonify({"rfqs": rfq_archive.all(request.args.get("project") or None)})


@app.route("/api/rfq-archive/<entry_id>/save-as", methods=["POST"])
def api_rfq_archive_save_as(entry_id):
    """Hand an archived RFQ back out, wherever the user wants it this time."""
    rec = rfq_archive.get(entry_id)
    if not rec:
        return jsonify({"error": "That RFQ is not in the archive."}), 404
    try:
        with open(rec["path"], "rb") as f:
            blob = f.read()
    except OSError:
        return jsonify({"error": "The archived file is missing from disk. "
                                 f"It was recorded at {rec['path']}."}), 410
    res = filesave.write(blob, rec["file"], sub="rfq",
                         file_types=("Word document (*.docx)",))
    if res["cancelled"]:
        return jsonify({"cancelled": True})
    return jsonify(res)


_BILLS = {}      # upload token -> [{"name","path"}]  (this run only)


@app.route("/api/reimburse/bills", methods=["POST"])
def api_reimburse_bills():
    """
    Take receipt files for the sheet being filled in.

    Held for this run of the app only, in a temp folder. They are not project
    data and they are not filed anywhere until the sheet is generated - at
    which point images go into the document and PDFs are written out beside
    it. Keeping them in memory until then means a claimant who abandons a
    half-filled sheet leaves nothing behind.
    """
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "Choose at least one file."}), 400

    import tempfile
    token = request.form.get("token") or ""
    if not token:
        token = os.path.basename(tempfile.mkdtemp(prefix="bills_"))
    folder = os.path.join(tempfile.gettempdir(), token)
    os.makedirs(folder, exist_ok=True)

    kept, rejected = _BILLS.setdefault(token, []), []
    for f in files:
        name = os.path.basename(f.filename or "")
        if not name.lower().endswith(reimburse.BILL_TYPES):
            rejected.append(name)
            continue
        path = os.path.join(folder, name)
        f.save(path)
        kept = [b for b in kept if b["name"] != name]      # re-upload replaces
        kept.append({"name": name, "path": path})
    _BILLS[token] = kept

    return jsonify({"token": token,
                    "bills": [{"name": b["name"]} for b in kept],
                    "rejected": rejected})


@app.route("/api/reimburse/bills/<token>/<name>", methods=["DELETE"])
def api_reimburse_bill_delete(token, name):
    _BILLS[token] = [b for b in _BILLS.get(token, [])
                     if b["name"] != os.path.basename(name)]
    return jsonify({"bills": [{"name": b["name"]} for b in _BILLS[token]]})


@app.route("/api/reimburse/check", methods=["POST"])
def api_reimburse_check():
    """
    Totals and cap breaches, live while the sheet is being filled in.

    The caps are the house rules on the form: breakfast 75, lunch 150, dinner
    150, stay 1000, each PER DAY - so two lunches on one date are judged
    together, not separately. Showing the shortfall while typing is the point;
    a claimant who learns at the approval stage that 170 was never payable has
    wasted their time and the approver's.
    """
    data = request.get_json(force=True) or {}
    return jsonify(reimburse.check(data.get("items") or [],
                                   data.get("period_from") or "",
                                   data.get("period_to") or ""))


@app.route("/api/reimburse/docx", methods=["POST"])
def api_reimburse_docx():
    """Write the sheet to the house form and hand back where it landed."""
    data = request.get_json(force=True) or {}
    if not (data.get("employee") or "").strip():
        return jsonify({"error": "Enter the employee name."}), 400
    if not (data.get("items") or []):
        return jsonify({"error": "Add at least one expense line."}), 400

    who = reimburse.safe_name(data.get("employee") or "employee")
    fname = f"Reimbursement_{who}_{reimburse.safe_name(data.get('sheet_no') or 'sheet')}.docx"

    # Build to a temp file, then hand the bytes to filesave so the desktop
    # build opens a real Save dialog. Writing straight into a folder beside the
    # exe meant the sheet went somewhere the claimant never chose and often
    # never found.
    bills = _BILLS.get(data.get("bills_token") or "", [])
    data = dict(data, bills=bills)

    import tempfile
    tmp = os.path.join(tempfile.gettempdir(), fname)
    reimburse.build(tmp, data)
    with open(tmp, "rb") as f:
        blob = f.read()
    try:
        os.remove(tmp)
    except OSError:
        pass

    res = filesave.write(blob, fname, sub="reimbursement",
                         file_types=("Word document (*.docx)",))
    if res["cancelled"]:
        return jsonify({"cancelled": True})
    # PDFs cannot live inside a .docx, so they go next to it. Silently losing
    # a receipt an approver is told to expect would be worse than a slightly
    # messier folder.
    saved_pdfs = []
    pdfs = [b for b in bills if b["name"].lower().endswith(".pdf")]
    if pdfs and res.get("path"):
        import shutil
        dest = os.path.dirname(res["path"])
        for b in pdfs:
            try:
                shutil.copyfile(b["path"], os.path.join(dest, b["name"]))
                saved_pdfs.append(b["name"])
            except OSError:
                pass

    res["file"] = fname
    res["bills_in_doc"] = len(bills) - len(pdfs)
    res["pdfs_saved"] = saved_pdfs
    res["totals"] = reimburse.check(data.get("items") or [],
                                    data.get("period_from") or "",
                                    data.get("period_to") or "")
    return jsonify(res)


@app.route("/api/bom/import", methods=["POST"])
def api_bom_import():
    """
    Read a designer's BOM spreadsheet into BOM lines.

    Returns the lines it could build AND everything it could not place, by row
    number. The caller shows both. A line that silently arrives with the wrong
    component type becomes a wrong blank, a wrong nest and a wrong price, and
    nobody goes back to the BOM to find out why - so anything ambiguous is
    imported with the type left blank and reported, rather than guessed at.
    """
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"error": "Choose an .xlsx file to import."}), 400
    if not f.filename.lower().endswith((".xlsx", ".xlsm")):
        return jsonify({"error": "Only .xlsx or .xlsm workbooks can be read. "
                                 "If this is an .xls or a CSV, open it in Excel "
                                 "and save it as .xlsx first."}), 400

    codes = (request.form.get("codes") or "").split(",")
    try:
        out = bom_import.parse(f.read(), [c for c in codes if c])
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(out)


@app.route("/api/projects/<pid>/rfq-preview", methods=["POST"])
def api_rfq_preview(pid):
    """
    The RFQ exactly as it will be written, before anything is committed.

    Read-only on purpose. Nothing is approved, no document is written and no
    number is burnt - it exists so the person sending an RFQ for approval can
    see what they are about to put in front of a supplier. The content comes
    from rfq_rm.content(), the same call build() renders to Word, so the
    preview cannot show something the document will not say.
    """
    e = projects.entry(pid)
    if not e:
        return jsonify({"error": f"No project {pid}"}), 404

    body = request.get_json(force=True) or {}
    items = _rm_items_for(pid, body.get("plates") or [])
    if not items:
        return jsonify({"error": "No raw material to quote. Calculate and book "
                                 "a job first, or add plate to the project."}), 400

    picked = [v for v in (vendors.get(i) for i in (body.get("vendor_ids") or [])) if v]
    cfg = rfq_rm.house_config()
    pnum = e.get("number") or e.get("id")
    prefix = cfg.get("rfq_prefix", "RFQ")
    validity = cfg.get("validity_days", 10)

    docs = []
    for n, v in enumerate(picked or [None], 1):
        docs.append(rfq_rm.content(e, v, items,
                                   f"{prefix}-{pnum}-RM-{n:03d}", cfg, validity))
    return jsonify({"docs": docs, "draft": True})


@app.route("/api/approvals/<rid>/rfq-preview")
def api_approval_rfq_preview(rid):
    """
    The RFQ an approver is being asked to sign off.

    Built from the request's frozen snapshot, not from the project as it
    stands now. If someone edits the job after submitting, the approver must
    still see the figures they are approving - that is the whole point of the
    snapshot, and reading live data here would quietly defeat it.
    """
    rec = approvals.get(rid)
    if not rec:
        return jsonify({"error": f"No request {rid}"}), 404

    snap = rec.get("snapshot") or {}
    items = snap.get("items") or []
    cfg = rfq_rm.house_config()
    pnum = rec.get("project_number") or rec.get("project_id")
    prefix = cfg.get("rfq_prefix", "RFQ")
    proj = {"number": pnum, "id": rec.get("project_id"),
            "name": rec.get("project_name", "")}

    docs = []
    for n, v in enumerate(snap.get("vendors") or [None], 1):
        vend = {"company": {"name": v.get("name", "")}} if v else None
        docs.append(rfq_rm.content(proj, vend, items,
                                   f"{prefix}-{pnum}-RM-{n:03d}", cfg,
                                   cfg.get("validity_days", 10)))
    return jsonify({"docs": docs, "status": rec.get("status"),
                    "request_id": rec.get("id")})


@app.route("/api/projects/<pid>/rfq-rm", methods=["POST"])
def api_rfq_rm(pid):
    """
    One RFQ per selected vendor, each addressed to that vendor by name.

    Written straight to disk rather than offered as a download: inside the
    desktop window there is no download manager, so a browser download would
    silently do nothing.
    """
    e = projects.entry(pid)
    if not e:
        return jsonify({"error": f"No project {pid}"}), 404

    body = request.get_json(force=True)
    items = _rm_items_for(pid, body.get("plates") or [])
    if not items:
        return jsonify({"error": "No raw material to quote. Calculate and book "
                                 "a job first, or add plate to the project."}), 400

    # The gate. An RFQ is a commitment to a supplier, so it cannot be issued
    # until the chain has signed it off - and the approval must still match
    # what the project says now, not what it said when it was submitted.
    rec = approvals.open_request_for(pid) or next(
        (r for r in approvals.all_requests(pid)
         if r["status"] in (approvals.APPROVED, approvals.ISSUED)), None)
    if not rec:
        return jsonify({"error": "Send this RFQ for approval first \u2014 it has "
                                 "not been reviewed."}), 403
    rec = approvals.check_stale(rec["id"], items) or rec
    if rec["status"] == approvals.STALE:
        return jsonify({"error": f"{rec['id']} was approved for different figures. "
                                 f"The project has changed since \u2014 resubmit "
                                 f"for approval."}), 403
    if rec["status"] != approvals.APPROVED and rec["status"] != approvals.ISSUED:
        waiting = approvals.next_step(rec)
        return jsonify({"error": f"{rec['id']} is still awaiting approval"
                                 + (f" from the {waiting['designation']}." if waiting
                                    else ".")}), 403

    ids = body.get("vendor_ids") or []
    picked_vendors = [v for v in (vendors.get(i) for i in ids) if v]
    if not picked_vendors:
        return jsonify({"error": "Select at least one vendor. Add raw material "
                                 "vendors on the Vendor List page."}), 400

    cfg = rfq_rm.house_config()
    pnum = e.get("number") or e.get("id")
    prefix = cfg.get("rfq_prefix", "RFQ")

    # WRITE FIRST, ASK SECOND.
    #
    # The documents are always built into the app's own rfq folder, and only
    # then moved if the user picks somewhere else. Asking first meant that any
    # quirk in the native dialog - a backend returning an empty result, a
    # cancel, a threading hiccup - silently produced no files at all, which is
    # the worst outcome: the work is lost and there is nothing to point at.
    # This way the RFQs exist no matter what the dialog does.
    staging = filesave.default_dir("rfq")

    written = []
    for n, v in enumerate(picked_vendors, 1):
        vname = (v.get("company") or {}).get("name", "vendor")
        rfq_no = f"{prefix}-{pnum}-RM-{n:03d}"
        fname = (f"{prefix}_{rfq_rm.safe_name(pnum)}_"
                 f"{rfq_rm.safe_name(vname)}.docx")
        path = os.path.join(staging, fname)
        rfq_rm.build(path, e, v, items, rfq_no, cfg,
                     cfg.get("validity_days", 10))

        # Archive BEFORE anything else can go wrong. An RFQ is a commercial
        # document; months later somebody asks what was quoted to whom and
        # when. That answer must not depend on where a user pointed a Save
        # dialog, or whether they cancelled it.
        # NAME THIS `arch`, NOT `rec`. `rec` is the approval record fetched
        # above and still needed below: mailer.send_rfq_to_vendor() takes it,
        # and approvals.mark_issued() takes its id. Rebinding it here handed
        # both of them an archive entry instead, so the approval was never
        # marked issued and the mail carried the wrong record.
        arch = rfq_archive.record(
            path, rfq_no, pid, pnum, vname, kind="RM",
            weight_kg=sum(float(i.get("weight_kg") or 0) for i in items),
            extra={"approval": rec["id"]})
        written.append({"vendor": vname, "rfq_no": rfq_no, "path": path,
                        "file": fname, "archive_id": arch["id"]})

    out_dir = staging
    moved = False
    folder = filesave.ask_folder("rfq")
    if folder:
        try:
            os.makedirs(folder, exist_ok=True)
            for w in written:
                dest = os.path.join(folder, w["file"])
                shutil.move(w["path"], dest)
                w["path"] = dest
            out_dir = folder
            moved = True
        except OSError as exc:
            # Files stay where they were built rather than disappearing.
            app.logger.warning("Could not move RFQs to %s: %s", folder, exc)

    # Mail each vendor their own document, if we have an address and SMTP is
    # set up. Recorded in the outbox either way.
    mailed = []
    if body.get("email_vendors") and not rfq_archive.all(pid):
        # First generation for this project AND the email box is ticked, so
        # nobody has yet opened the document that is about to reach a supplier.
        # The RFQs are written and archived regardless - only the sending is
        # held back, and the next press goes out normally.
        return jsonify({
            "written": written, "dir": out_dir, "moved": moved,
            "approval": rec["id"], "mailed": [],
            "held": "The RFQ has been generated and saved, but not sent. Open "
                    "the document and check it, then press Generate RFQ per "
                    "vendor again to email it.",
        })

    if body.get("email_vendors"):
        for v, w in zip(picked_vendors, written):
            addr = ""
            for c in v.get("contacts") or []:
                if c.get("email"):
                    addr = c["email"]
                    break
            if not addr:
                mailed.append({"vendor": w["vendor"], "status": "no address"})
                continue
            with open(w["path"], "rb") as f:
                data = f.read()
            m = mailer.send_rfq_to_vendor(w["vendor"], addr, rec,
                                          w["file"], data, cfg)
            mailed.append({"vendor": w["vendor"], "to": addr,
                           "status": m["status"], "detail": m["detail"]})
        approvals.mark_issued(rec["id"], _me(),
                              [x["vendor"] for x in mailed])

    return jsonify({"written": written, "folder": out_dir, "moved": moved,
                    "approval": rec["id"], "mailed": mailed,
                    "items": len(items)})


@app.route("/api/remnants/clear", methods=["POST"])
def api_remnants_clear():
    inventory.clear()
    return jsonify({"ok": True})


# ------------------------------------------------------------------- boot --

PORT = 5057
URL = f"http://127.0.0.1:{PORT}"


def run_in_browser(reason=None):
    """Serve the app and open the default browser. No pywebview needed."""
    if reason:
        print(f"\\n  Desktop window unavailable: {reason}")
    print(f"  Running in the browser instead: {URL}")
    print("  Install pywebview for the desktop window:  pip install pywebview")
    print("  Press Ctrl+C to stop.\\n")
    threading.Timer(1.0, lambda: webbrowser.open(URL)).start()
    app.run(port=PORT, use_reloader=False)


def run_flask():
    app.run(port=5057, use_reloader=False)


def check_material_names():
    """
    Warn at startup if anything on disk refers to a material that no longer
    exists in database.py.

    Editing MATERIALS is a normal thing to do, but the name is the key
    everywhere else — saved remnants and the job ledger. A rename leaves those
    pointing at nothing: a remnant becomes unmatchable dead stock, and a part
    fails mid-calculation. Better to say so on startup than to fail later with
    a KeyError.
    """
    known = set(MATERIALS)
    problems = []

    for label, path in (("offcut stock", "remnants.json"),
                        ("job history", "jobs.json")):
        full = runtime_paths.data_path(path)
        try:
            with open(full, "r", encoding="utf-8") as f:
                txt = f.read()
        except OSError:
            continue
        found = set(re.findall(r'"material"\s*:\s*"([^"]*)"', txt))
        for m in sorted(found - known):
            problems.append(f"{label}: {m!r} is no longer a known material")

    if problems:
        print("\n  Material name check:")
        for p in problems:
            print("   -", p)
        print("   Known materials:", ", ".join(sorted(known)), "\n")
    return problems


def open_arg_file():
    """
    Handle a .rmp passed on the command line, so double-clicking one opens it.

    Imported as a NEW project rather than replacing anything. The file may have
    come from another machine where the ids mean something else, and silently
    overwriting someone's work would be far worse than making a copy they can
    delete.

    Returns the project id to open, or None.
    """
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        return None
    path = args[0]
    if not os.path.isfile(path):
        print(f"  Cannot open {path!r} — file not found.")
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            doc = json.load(f)
        e = projects.import_file(doc)
        print(f"  Opened {os.path.basename(path)} as {e['id']} \u2014 {e['name']}")
        return e["id"]
    except (OSError, ValueError) as exc:
        print(f"  Could not open {path!r}: {exc}")
        return None


def _webview2_installed():
    """
    Best-effort check for the Microsoft Edge WebView2 Runtime on Windows.

    THE BUG THIS CATCHES
        pywebview's Windows backend does not raise an error when WebView2 is
        missing — it silently drops back to the decades-old Internet Explorer
        (Trident/MSHTML) engine instead. The window still opens, so the app
        LOOKS like it started, but this app's JS/CSS is written for a modern
        browser and breaks under that engine: buttons that do nothing, blank
        panels, layout falling apart. That is almost certainly what "opens
        but doesn't run properly on other PCs" means.

        WebView2 ships inside Windows 11 and current Windows 10 out of the
        box, but a machine that has been kept lean (some corporate images,
        older installs) can be missing it.

    Not Windows -> True (irrelevant; pywebview uses a different backend).
    """
    if platform.system() != "Windows":
        return True
    import winreg
    client_id = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive, path in (
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{client_id}"),
        (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{client_id}"),
        (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{client_id}"),
    ):
        try:
            winreg.OpenKey(hive, path)
            return True
        except OSError:
            continue
    return False


def main():
    runtime_paths.purge_pycache()
    check_material_names()

    # A .rmp on the command line is imported before the window opens, so the
    # project is already there when the page first loads.
    global OPEN_ON_START
    OPEN_ON_START = open_arg_file()
    if "--web" in sys.argv:
        run_in_browser()
        return

    # pywebview is optional. If it is missing, or fails to start (no GUI
    # backend, unsupported Python version, headless machine), fall back to
    # the browser rather than dying with a traceback — the app itself works
    # identically either way.
    try:
        import webview
    except ImportError:
        run_in_browser("pywebview is not installed")
        return

    if not _webview2_installed():
        run_in_browser(
            "Microsoft Edge WebView2 Runtime is not installed on this PC.\n"
            "  Without it the desktop window opens but renders with a very\n"
            "  old browser engine that cannot run this app's screens.\n"
            "  One-time fix (~2 minutes): download and run the WebView2\n"
            "  Runtime installer from https://developer.microsoft.com/microsoft-edge/webview2/\n"
            "  Then relaunch RM Estimator for the desktop window."
        )
        return

    try:
        t = threading.Thread(target=run_flask, daemon=True)
        t.start()
        win = webview.create_window("Project Estimation", URL, width=1400, height=900)
        # The saver needs the window to raise a native Save dialog. Without
        # it every export silently falls back to a folder the user has to go
        # and find.
        filesave.register_window(win)
        # Force the modern Chromium engine explicitly. Left as None, pywebview
        # would auto-pick and could still silently choose the legacy engine
        # in edge cases the registry check above doesn't catch.
        webview.start(gui="edgechromium")
    except Exception as exc:
        run_in_browser(f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
