# Store Return / Damage Export (Final)

**Build date:** 2025-11-04 05:50:07

This package contains the working app with strict **no-blank-cell** validation (client + server).

## Run (Windows)

1. Copy `.env.example` to `.env` and edit values.
2. Double-click **`run_app.bat`**.
3. Open http://127.0.0.1:8000

## Structure

- `app.py` — Flask app with server-side validation and optional FTP/Drive/GAS upload.
- `templates/form.html` — form with required fields and client validation.
- `static/styles.css` — minimal styling.
- `exports/` — generated automatically for daily CSV folders.
- `run_app.bat` — one-click launcher.
- `requirements.txt` — dependencies.
- `.env.example` — sample configuration.

## Validation Rules

- Singles: `CreatedBy`, `Source`, `Destination` are required.
- Rows: each row requires `ParentCode`, `ParentName`, `Quantity` (>=1).
- Any blank fails with a readable message (row numbers included).

## Filenames

- `STORE_RETURNYYMMDDhhmmss.CSV`
- `STORE_RET_DAMAGEYYMMDDhhmmss.CSV`

## Optional Uploads
Enable in `.env`:
- `EXPORT_TO_FTP=true`
- `EXPORT_TO_GDRIVE=true`
- `EXPORT_TO_GAS_WEBAPP=true`

