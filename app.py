import os
import io
import csv
import base64
import requests
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv

# ------------------ Optional Google Drive ------------------
GDRIVE_AVAILABLE = False
try:
    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive
    GDRIVE_AVAILABLE = True
except Exception:
    pass

# ------------------ Optional FTP ------------------
from ftplib import FTP, error_perm

# ------------------ Setup ------------------
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
EXPORTS_BASE = BASE_DIR / "exports"
EXPORTS_BASE.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "dev")
DEBUG_KEY = os.getenv("DEBUG_KEY", "my-debug-key")

COLUMNS = [
    "Sno", "CreatedDate", "CreatedBy", "DocumentNumber",
    "ParentCode", "ParentName", "TransactionType", "Quantity",
    "Source", "Destination"
]


# ------------------ Helper Functions ------------------
def _today_folder() -> Path:
    folder = EXPORTS_BASE / datetime.now().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def _get_env_bool(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")


def _make_csv_bytes(rows: list[dict]) -> io.BytesIO:
    sio = io.StringIO()
    writer = csv.DictWriter(sio, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in COLUMNS})
    return io.BytesIO(sio.getvalue().encode("utf-8-sig"))


def _upload_gas_webapp(filename: str, file_bytes: io.BytesIO):
    url = (os.getenv("DRIVE_WEBAPP_URL") or "").strip()
    token = (os.getenv("DRIVE_UPLOAD_TOKEN") or "").strip()
    if not url or not token:
        raise RuntimeError("Missing DRIVE_WEBAPP_URL or DRIVE_UPLOAD_TOKEN in environment")

    file_bytes.seek(0)
    payload = {
        "token": token,
        "filename": filename,
        "mimetype": "text/csv",
        "content_b64": base64.b64encode(file_bytes.read()).decode("utf-8"),
    }
    try:
        resp = requests.post(url, json=payload, timeout=30)
    except Exception as e:
        raise RuntimeError(f"GAS upload request failed: {e}")
    if resp.status_code != 200:
        raise RuntimeError(f"GAS upload failed ({resp.status_code}): {resp.text[:500]}")
    return resp.text


def _upload_ftp(filename: str, file_bytes: io.BytesIO):
    host = (os.getenv("FTP_HOST") or "").strip()
    port = int((os.getenv("FTP_PORT") or "21").strip() or 21)
    user = (os.getenv("FTP_USER") or "").strip()
    pwd = (os.getenv("FTP_PASS") or "").strip()
    remote_dir = (os.getenv("FTP_REMOTE_DIR") or "/").strip()

    if not host or not user:
        raise RuntimeError("FTP credentials missing (FTP_HOST / FTP_USER).")

    with FTP() as ftp:
        ftp.connect(host, port, timeout=30)
        ftp.login(user, pwd)
        if remote_dir and remote_dir.strip("/") != "":
            for part in [p for p in remote_dir.split("/") if p]:
                try:
                    ftp.mkd(part)
                except error_perm:
                    pass
                ftp.cwd(part)
        file_bytes.seek(0)
        ftp.storbinary(f"STOR {filename}", file_bytes)


# ------------------ Validation ------------------
def _is_blank(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _validate_no_blanks(form):
    errors = []
    singles = {
        "CreatedBy": form.get("CreatedBy"),
        "Source": form.get("Source"),
        "Destination": form.get("Destination"),
    }
    for name, val in singles.items():
        if _is_blank(val):
            errors.append(f"• {name}: is required.")

    parent_codes = form.getlist("ParentCode[]")
    parent_names = form.getlist("ParentName[]")
    quantities = form.getlist("Quantity[]")

    if not (parent_codes or quantities or parent_names):
        errors.append("• Items: please add at least one row.")
    else:
        total_rows = max(len(parent_codes), len(parent_names), len(quantities))
        for i in range(total_rows):
            pc = (parent_codes[i] if i < len(parent_codes) else "").strip()
            pn = (parent_names[i] if i < len(parent_names) else "").strip()
            qty = (quantities[i] if i < len(quantities) else "").strip()
            row_issues = []
            if _is_blank(pc):
                row_issues.append("ParentCode")
            if _is_blank(pn):
                row_issues.append("ParentName")
            if _is_blank(qty):
                row_issues.append("Quantity")
            if row_issues:
                errors.append(f"• Row {i+1}: blank -> {', '.join(row_issues)}")

    if errors:
        return False, "Please fill all required fields:\n" + "\n".join(errors)
    return True, ""


# ------------------ Main Routes ------------------
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        ok, msg = _validate_no_blanks(request.form)
        if not ok:
            flash(msg, "error")
            return redirect(url_for("index"))

        form_type = request.form.get("form_type", "Store Return")
        created_by = (request.form.get("CreatedBy") or "").strip()
        document_number = (request.form.get("DocumentNumber") or "").strip()
        source = (request.form.get("Source") or "").strip()
        destination = (request.form.get("Destination") or "").strip()

        parent_codes = request.form.getlist("ParentCode[]")
        parent_names = request.form.getlist("ParentName[]")
        quantities = request.form.getlist("Quantity[]")

        rows = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(len(parent_codes)):
            rows.append({
                "Sno": i + 1,
                "CreatedDate": now_str,
                "CreatedBy": created_by,
                "DocumentNumber": document_number,
                "ParentCode": parent_codes[i].strip(),
                "ParentName": parent_names[i].strip(),
                "TransactionType": "Return" if form_type == "Store Return" else "Damage",
                "Quantity": quantities[i].strip(),
                "Source": source,
                "Destination": destination
            })

        ts = datetime.now().strftime("%y%m%d%H%M%S")
        base = "STORE_RETURN" if form_type == "Store Return" else "STORE_RET_DAMAGE"
        filename = f"{base}{ts}.CSV"

        csv_bytes = _make_csv_bytes(rows)
        out_dir = _today_folder()
        out_path = out_dir / filename
        with open(out_path, "wb") as f:
            f.write(csv_bytes.getvalue())

        sent_targets = []
        if _get_env_bool("EXPORT_TO_FTP"):
            try:
                csv_bytes.seek(0)
                _upload_ftp(filename, csv_bytes)
                sent_targets.append("FTP")
            except Exception as e:
                flash(f"FTP upload failed: {e}", "error")

        if _get_env_bool("EXPORT_TO_GAS_WEBAPP"):
            try:
                csv_bytes.seek(0)
                reply = _upload_gas_webapp(filename, csv_bytes)
                sent_targets.append("Google Apps Script")
                flash(f"GAS reply: {reply[:200]}", "info")
            except Exception as e:
                flash(f"GAS upload failed: {e}", "error")

        msg = (
            f"✅ File <b>{filename}</b> exported successfully "
            + (f"and sent to <b>{', '.join(sent_targets)}</b>."
               if sent_targets else "and saved locally.")
        )
        flash(msg, "success")
        return redirect(url_for("index"))

    return render_template("form.html")


# ------------------ Debug Routes for Render Free Plan ------------------
@app.get("/debug/env")
def debug_env():
    if request.args.get("key") != DEBUG_KEY:
        return ("forbidden", 403)
    keys = ["EXPORT_TO_GAS_WEBAPP", "DRIVE_WEBAPP_URL", "DRIVE_UPLOAD_TOKEN", "SECRET_KEY"]
    data = {k: bool(os.getenv(k)) for k in keys}
    url = (os.getenv("DRIVE_WEBAPP_URL") or "")
    data["drive_url_tail"] = url[-25:] if url else ""
    return data, 200


@app.get("/debug/gas-test")
def debug_gas_test():
    if request.args.get("key") != DEBUG_KEY:
        return ("forbidden", 403)
    content = "Sno,CreatedDate,CreatedBy\n1,2025-01-01 12:00:00,render-debug\n"
    buf = io.BytesIO(content.encode("utf-8"))
    try:
        res = _upload_gas_webapp("DEBUG_UPLOAD.csv", buf)
        return {"ok": True, "gas_reply": res[:200]}, 200
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500


# ------------------ Health ------------------
@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)
