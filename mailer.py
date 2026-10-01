"""
mailer.py — notification and RFQ email.

THE HONEST POSITION
    This is an offline desktop app. It can only send mail if you give it an
    SMTP account and it has internet at that moment. Neither is guaranteed,
    so the design assumes sending will sometimes fail and makes sure nothing
    is lost when it does:

      * Every message is written to the outbox FIRST, then sending is
        attempted. A send that fails leaves a record that says so.
      * With no SMTP configured the message still lands in the outbox, marked
        "not sent". The approval flow carries on - it is not blocked by mail
        that cannot go anywhere.
      * The outbox is visible in the app, so "did the director get told?" has
        an answer other than a shrug.

    Nothing here silently swallows a failure. A message is either sent, or on
    screen saying it was not.

CREDENTIALS
    data/mail.json holds the SMTP settings. Use an app-specific password, not
    the account's real one - this file sits in plain sight next to the app and
    anyone with the folder can read it. That is a real limitation of a desktop
    build, and the reason a server deployment should hold this in a secret
    store instead.
"""
import datetime
import json
import os
import smtplib
import threading
from email.message import EmailMessage

try:
    from .runtime_paths import data_path as _dp
except ImportError:
    from runtime_paths import data_path as _dp

_LOCK = threading.Lock()

SENT, FAILED, NOT_CONFIGURED = "sent", "failed", "not configured"


def config():
    try:
        with open(_dp("mail.json"), "r", encoding="utf-8") as f:
            return {k: v for k, v in json.load(f).items() if not k.startswith("_")}
    except (OSError, ValueError):
        return {}


def is_configured():
    c = config()
    return bool(c.get("host") and c.get("from_address"))


def _outbox_file():
    return _dp("outbox.json")


def _load_outbox():
    try:
        with open(_outbox_file(), "r", encoding="utf-8") as f:
            d = json.load(f)
        d.setdefault("messages", [])
        return d
    except (OSError, ValueError):
        return {"messages": []}


def _record(entry):
    with _LOCK:
        d = _load_outbox()
        d["messages"].insert(0, entry)
        d["messages"] = d["messages"][:500]      # keep it from growing forever
        tmp = _outbox_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=1)
        os.replace(tmp, _outbox_file())
        return entry


def outbox(limit=100):
    return _load_outbox()["messages"][:limit]


def send(to, subject, body, attachments=None, kind="notification"):
    """
    Try to send one message. Always returns a record; never raises.

    `attachments` is a list of (filename, bytes).
    """
    to = [t for t in (to if isinstance(to, (list, tuple)) else [to]) if t]
    entry = {
        "at": datetime.datetime.now().isoformat(timespec="seconds"),
        "to": to, "subject": subject, "kind": kind,
        "attachments": [a[0] for a in (attachments or [])],
        "status": NOT_CONFIGURED, "detail": "",
        "body": body[:2000],
    }

    if not to:
        entry.update(status=FAILED, detail="No recipient address")
        return _record(entry)

    if not is_configured():
        entry["detail"] = ("No SMTP settings in data/mail.json, so this was "
                           "recorded but not sent.")
        return _record(entry)

    c = config()
    msg = EmailMessage()
    msg["From"] = c["from_address"]
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    msg.set_content(body)
    for name, data in (attachments or []):
        msg.add_attachment(data, maintype="application",
                           subtype="vnd.openxmlformats-officedocument."
                                   "wordprocessingml.document",
                           filename=name)

    try:
        port = int(c.get("port", 587))
        if c.get("use_ssl"):
            server = smtplib.SMTP_SSL(c["host"], port, timeout=20)
        else:
            server = smtplib.SMTP(c["host"], port, timeout=20)
        with server:
            if not c.get("use_ssl"):
                server.starttls()
            if c.get("username"):
                server.login(c["username"], c.get("password", ""))
            server.send_message(msg)
        entry.update(status=SENT, detail="")
    except Exception as exc:
        # Deliberately broad: SMTP fails in many ways and every one of them
        # should end up visible in the outbox rather than as a traceback.
        entry.update(status=FAILED, detail=f"{type(exc).__name__}: {exc}")
    return _record(entry)


# ------------------------------------------------------------- templates ---

def notify_approver(req, role, recipients, app_note=""):
    subject = (f"Approval needed: RFQ for {req['project_number']} "
               f"- {req['project_name']}")
    lines = [
        f"An RFQ is waiting for your approval as {role}.",
        "",
        f"Request     : {req['id']}",
        f"Project     : {req['project_number']} - {req['project_name']}",
        f"Submitted by: {req['submitted_by'].get('name', '')} "
        f"({req['submitted_by'].get('designation', '')})",
        f"Material    : {len(req['snapshot']['items'])} line(s), "
        f"{req['snapshot']['total_kg']:,.0f} kg",
        f"Vendors     : {', '.join(v['name'] for v in req['snapshot']['vendors'])}",
    ]
    if req.get("note"):
        lines += ["", f"Note: {req['note']}"]
    if app_note:
        lines += ["", app_note]
    lines += ["", "Open the RM Estimator and go to Approvals to review it."]
    return send(recipients, subject, "\n".join(lines), kind="approval request")


def notify_outcome(req, action, who, comment, recipients):
    subject = (f"RFQ {action}: {req['project_number']} - {req['project_name']}")
    lines = [
        f"{who.get('name','')} ({who.get('designation','')}) {action} "
        f"request {req['id']}.",
        f"Project: {req['project_number']} - {req['project_name']}",
    ]
    if comment:
        lines += ["", f"Comment: {comment}"]
    return send(recipients, subject, "\n".join(lines), kind=f"rfq {action}")


def send_rfq_to_vendor(vendor_name, vendor_email, req, filename, data, cfg):
    company = (cfg.get("company") or {}).get("name", "")
    contact = cfg.get("contact") or {}
    subject = f"Request for Quotation - {req['project_number']} - {company}"
    body = "\n".join([
        f"Dear {vendor_name},",
        "",
        "Please find attached our request for quotation for the raw material "
        "detailed in the document.",
        "",
        f"Project reference : {req['project_number']}",
        f"Total approximate weight: {req['snapshot']['total_kg']:,.0f} kg",
        "",
        "We would be grateful for your quotation, including rate, delivery "
        "lead time and applicable taxes, by return.",
        "",
        "Regards,",
        contact.get("person", ""),
        company,
        contact.get("phone", ""),
        contact.get("email", ""),
    ])
    return send(vendor_email, subject, body,
                attachments=[(filename, data)], kind="rfq to vendor")
