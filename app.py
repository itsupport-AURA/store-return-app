import os
import io
import csv
import base64
import requests
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash
from dotenv import load_dotenv

# ---- Optional Google Drive (pydrive2) ----
GDRIVE_AVAILABLE = False
try:
    from pydrive2.auth import GoogleAuth
    from pydrive2.drive import GoogleDrive
    GDRIVE_AVAILABLE = True
except Exception:
    pass

# ---- Optional FTP ----
from ftplib import FTP, error_perm


# =========================
# App setup / configuration
# =========================
load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
EXPORTS_BASE = BASE_DIR / "exports"
EXPORTS_BASE.mkdir(exist_ok=True)

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.getenv("SECRET_KEY", "dev")  # flash messages


# =========================
# Constants
# =========================
COLUMNS = [
    "Sno", "CreatedDate", "CreatedBy", "DocumentNumber",
    "ParentCode", "ParentName", "TransactionType", "Quantity",
    "Source", "Destination"
]
TEMPLATES = {
    "Store Return": COLUMNS,
    "Store Return Damage": COLUMNS
}


# =========================
# Helpers
# =========================
def _today_folder() -> Path:
    folder = EXPORTS_BASE / datetime.now().strftime("%Y%m%d")
    folder.mkdir(parents=True, exist_ok=True)
    return folder

def _get_env_bool(name: str, default: str = "false") -> bool:
    return (os.getenv(name, default) or "").strip().lower() in ("1", "true", "yes", "on")

def _make_csv_bytes(rows: list[dict]) -> io.BytesIO:
    """Write rows into CSV with Excel-friendly UTF-8 BOM."""
    sio = io.StringIO()
    writer = csv.DictWriter(sio, fieldnames=COLUMNS, extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow({k: r.get(k, "") for k in COLUMNS})
    return io.BytesIO(sio.getvalue().encode("utf-8-sig"))

def _upload_ftp(filename: str, file_bytes: io.BytesIO):
    host = (os.getenv("FTP_HOST") or "").strip()
    port = int((os.getenv("FTP_PORT") or "21").strip() or 21)
    user = (os.getenv("FTP_USER") or "").strip()
    pwd  = (os.getenv("FTP_PASS") or "").strip()
    remote_dir = (os.getenv("FTP_REMOTE_DIR") or "/").strip()

    if not host or not user:
        raise RuntimeError("FTP credentials missing (FTP_HOST / FTP_USER).")

    with FTP() as ftp:
        ftp.connect(host, port, timeout=30)
        ftp.login(user, pwd)

        # ensure nested remote directories
        if remote_dir and remote_dir.strip("/") != "":
            for part in [p for p in remote_dir.split("/") if p]:
                try:
                    ftp.mkd(part)
                except error_perm:
                    pass
                ftp.cwd(part)

        file_bytes.seek(0)
        ftp.storbinary(f"STOR {filename}", file_bytes)

def _upload_gdrive(filename: str, file_bytes: io.BytesIO):
    if not GDRIVE_AVAILABLE:
        raise RuntimeError("pydrive2 not installed. Set EXPORT_TO_GDRIVE=false or install it.")

    service_json = (os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON") or "").strip()
    folder_id = (os.getenv("GDRIVE_FOLDER_ID") or "").strip()
    if not service_json or not os.path.exists(service_json):
        raise RuntimeError("Service account JSON missing (GDRIVE_SERVICE_ACCOUNT_JSON).")

    gauth = GoogleAuth(settings=dict(
        client_config_backend="service",
        service_config={
            "client_json_file_path": service_json,
            "scope": ["https://www.googleapis.com/auth/drive.file"]
        }
    ))
    gauth.ServiceAuth()
    drive = GoogleDrive(gauth)

    file_bytes.seek(0)
    meta = {"title": filename}
    if folder_id:
        meta["parents"] = [{"id": folder_id}]
    f = drive.CreateFile(meta)
    f.content = file_bytes.read()
    f.Upload()

def _upload_gas_webapp(filename: str, file_bytes: io.BytesIO):
    """Upload file to GAS WebApp defined in .env (DRIVE_WEBAPP_URL + DRIVE_UPLOAD_TOKEN)."""
    url = (os.getenv("DRIVE_WEBAPP_URL") or "").strip()
    token = (os.getenv("DRIVE_UPLOAD_TOKEN") or "").strip()
    if not url or not token:
        raise RuntimeError("Missing DRIVE_WEBAPP_URL or DRIVE_UPLOAD_TOKEN in .env")

    file_bytes.seek(0)
    payload = {
        "token": token,
        "filename": filename,
        "mimetype": "text/csv",
        "content_b64": base64.b64encode(file_bytes.read()).decode("utf-8"),
    }
    resp = requests.post(url, json=payload, timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"GAS upload failed ({resp.status_code}): {resp.text[:300]}")
    return resp.text


# =========================
# Routes
# =========================
@app.route("/", methods=["GET", "POST"])
def index():
    if request.method == "POST":
        # read top fields
        form_type = request.form.get("form_type", "Store Return")
        created_by = (request.form.get("CreatedBy") or "").strip()
        document_number = (request.form.get("DocumentNumber") or "").strip()
        source = (request.form.get("Source") or "").strip()
        destination = (request.form.get("Destination") or "").strip()

        # repeating table
        parent_codes = request.form.getlist("ParentCode[]")
        parent_names = request.form.getlist("ParentName[]")
        quantities   = request.form.getlist("Quantity[]")

        rows = []
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for i in range(len(parent_codes)):
            pc  = (parent_codes[i] or "").strip()
            qty = (quantities[i] or "").strip()
            if not pc or not qty:
                continue
            rows.append({
                "Sno": i + 1,
                "CreatedDate": now_str,
                "CreatedBy": created_by,
                "DocumentNumber": document_number,
                "ParentCode": pc,
                "ParentName": (parent_names[i] or "").strip(),
                "TransactionType": "Return" if form_type == "Store Return" else "Damage",
                "Quantity": qty,
                "Source": source,
                "Destination": destination
            })

        if not rows:
            flash("Please add at least one valid item (Parent Code + Quantity).", "error")
            return redirect(url_for("index"))

        # filename like before
        ts = datetime.now().strftime("%y%m%d%H%M%S")
        base = "STORE_RETURN" if form_type == "Store Return" else "STORE_RET_DAMAGE"
        filename = f"{base}{ts}.CSV"

        # write local file under exports/YYYYMMDD/
        csv_bytes = _make_csv_bytes(rows)
        out_dir = _today_folder()
        out_path = out_dir / filename
        with open(out_path, "wb") as f:
            f.write(csv_bytes.getvalue())

        # optional remote uploads
        sent_targets = []

        if _get_env_bool("EXPORT_TO_FTP"):
            try:
                csv_bytes.seek(0)
                _upload_ftp(filename, csv_bytes)
                sent_targets.append("FTP")
            except Exception as e:
                flash(f"FTP upload failed: {e}", "error")

        if _get_env_bool("EXPORT_TO_GDRIVE"):
            try:
                csv_bytes.seek(0)
                _upload_gdrive(filename, csv_bytes)
                sent_targets.append("Google Drive")
            except Exception as e:
                flash(f"Google Drive upload failed: {e}", "error")

        if _get_env_bool("EXPORT_TO_GAS_WEBAPP"):
            try:
                csv_bytes.seek(0)
                _upload_gas_webapp(filename, csv_bytes)
                sent_targets.append("Google Apps Script")
            except Exception as e:
                flash(f"GAS upload failed: {e}", "error")

        # success banner
        msg = (
            f"✅ File <b>{filename}</b> has been exported successfully "
            + (f"and sent to <b>{', '.join(sent_targets)}</b>."
               if sent_targets else "and saved locally.")
        )
        flash(msg, "success")
        return redirect(url_for("index"))

    # GET: render your form
    return render_template("form.html", columns=COLUMNS, templates=TEMPLATES)

@app.get("/health")
def health():
    return {"ok": True}


if __name__ == "__main__":
    # http://127.0.0.1:8000/
    app.run(host="0.0.0.0", port=8000, debug=True)
