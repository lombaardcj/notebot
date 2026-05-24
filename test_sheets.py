"""
test_sheets.py — standalone Google Sheets integration test

Run from the project root:
    python3 test_sheets.py

What it does:
  1. Loads config from .env
  2. Authenticates with the service account
  3. Opens the configured sheet (creates the header row if empty)
  4. Appends a clearly-labelled test row
  5. Reads it back to verify the write succeeded
  6. Offers to delete the test row when done

Prerequisites:
  - GOOGLE_ENABLED=true in .env  (or the checks below are skipped)
  - credentials.json present (service account key file)
  - The sheet named by GOOGLE_SHEET_NAME exists in Google Drive
  - The service account email has Editor access to that sheet
"""

import os
import sys
from pathlib import Path


# ── Load .env ────────────────────────────────────────────────────────────────

def load_env(path: str = ".env"):
    env_file = Path(path)
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value

load_env()


# ── Config ───────────────────────────────────────────────────────────────────

GOOGLE_ENABLED          = os.getenv("GOOGLE_ENABLED", "false").strip().lower() == "true"
SERVICE_ACCOUNT_FILE    = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "credentials.json")
SHEET_NAME              = os.getenv("GOOGLE_SHEET_NAME", "Voice Transcripts")


def fail(msg: str):
    print(f"\n❌  {msg}")
    sys.exit(1)


def check_prerequisites():
    print("── Prerequisites ───────────────────────────────────────────")

    if not GOOGLE_ENABLED:
        fail(
            "GOOGLE_ENABLED is not set to 'true' in .env.\n"
            "   Add  GOOGLE_ENABLED=true  to your .env file and re-run."
        )
    print("  ✅  GOOGLE_ENABLED=true")

    try:
        import gspread
        from google.oauth2.service_account import Credentials
        print("  ✅  gspread and google-auth are installed")
        return gspread, Credentials
    except ImportError as e:
        fail(
            f"Missing dependency: {e}\n"
            "   Run:  pip install gspread google-auth"
        )

    if not Path(SERVICE_ACCOUNT_FILE).exists():
        fail(
            f"credentials.json not found at: {SERVICE_ACCOUNT_FILE}\n"
            "   Download your service account key from Google Cloud Console\n"
            "   and place it at that path."
        )
    print(f"  ✅  Credentials file found: {SERVICE_ACCOUNT_FILE}")


def run_test():
    gspread, Credentials = check_prerequisites()

    # Re-check file after imports succeed
    if not Path(SERVICE_ACCOUNT_FILE).exists():
        fail(
            f"Credentials file not found: {SERVICE_ACCOUNT_FILE}\n"
            "   Download your service account key from Google Cloud Console."
        )
    print(f"  ✅  Credentials file found: {SERVICE_ACCOUNT_FILE}")

    print("\n── Authentication ──────────────────────────────────────────")
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive",
        ]
        creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=scopes)
        client = gspread.authorize(creds)
        print("  ✅  Authenticated with Google")
    except Exception as e:
        fail(f"Authentication failed: {e}")

    print("\n── Opening sheet ───────────────────────────────────────────")
    try:
        sheet = client.open(SHEET_NAME).sheet1
        print(f"  ✅  Opened sheet: '{SHEET_NAME}'")
    except gspread.exceptions.SpreadsheetNotFound:
        fail(
            f"Sheet '{SHEET_NAME}' not found.\n"
            "   • Check that GOOGLE_SHEET_NAME matches exactly (case-sensitive).\n"
            "   • Make sure the sheet is shared with the service account email\n"
            f"     (found in {SERVICE_ACCOUNT_FILE} under 'client_email')."
        )
    except Exception as e:
        fail(f"Could not open sheet: {e}")

    print("\n── Header row ──────────────────────────────────────────────")
    existing = sheet.get_all_values()
    if not existing:
        sheet.append_row(["Timestamp", "Sender", "Transcript"])
        print("  ✅  Header row written (sheet was empty)")
    else:
        print(f"  ✅  Sheet already has {len(existing)} row(s) — skipping header")

    print("\n── Write test ──────────────────────────────────────────────")
    import datetime
    test_ts = datetime.datetime.now().isoformat(timespec="seconds")
    test_row = [test_ts, "test_sheets.py", "🧪 Integration test — safe to delete"]
    sheet.append_row(test_row)
    print(f"  ✅  Test row appended: {test_row}")

    print("\n── Read-back verification ──────────────────────────────────")
    all_rows = sheet.get_all_values()
    last_row = all_rows[-1] if all_rows else []
    if last_row and last_row[0] == test_ts:
        print(f"  ✅  Read back successfully: {last_row}")
    else:
        fail(f"Read-back mismatch. Last row was: {last_row}")

    print("\n── Cleanup ─────────────────────────────────────────────────")
    answer = input("  Delete the test row now? [y/N] ").strip().lower()
    if answer == "y":
        row_index = len(all_rows)   # 1-based, last row
        sheet.delete_rows(row_index)
        print(f"  ✅  Row {row_index} deleted.")
    else:
        print("  ⏭  Skipped. You can delete the last row in the sheet manually.")

    print("\n✅  Google Sheets integration is working correctly.\n")


if __name__ == "__main__":
    run_test()
