import os
import sys
import csv
import json
import requests
import gspread
from datetime import datetime
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

load_dotenv()

MAALBARDAAR_API_URL: str = os.getenv("MAALBARDAAR_API_URL") or "https://api.maalbardaar.com/shipments-app/shipments/create"
MAALBARDAAR_API_KEY: str = os.getenv("MAALBARDAAR_API_KEY") or ""
SPREADSHEET_NAME: str = os.getenv("SPREADSHEET_NAME") or "Pending Shipments"
SHEET_TAB_NAME: str = os.getenv("SHEET_TAB_NAME") or "Sheet1"
SERVICE_ACCOUNT_FILE = "credentials.json"
INPUT_PAYLOADS_FILE = "pending_payloads.json"
DUPLICATE_LOG_FILE = "posted_shipments.json"
CSV_AUDIT_LOG_FILE = "posted_shipments_log.csv"

if not MAALBARDAAR_API_KEY:
    print("[ERROR] MAALBARDAAR_API_KEY is missing from .env file.")
    sys.exit(1)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def load_posted_records() -> set[str]:
    """Loads previously posted unique keys to prevent duplicate API posts."""
    if os.path.exists(DUPLICATE_LOG_FILE):
        try:
            with open(DUPLICATE_LOG_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except Exception:
            return set()
    return set()

def save_posted_record(unique_key: str):
    records = load_posted_records()
    records.add(unique_key)
    with open(DUPLICATE_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(list(records), f, indent=2)

def log_to_csv(timestamp: str, row_num: int, ref_no: str, mbl_no: str, shipment_id: str, status: str, details: str):
    """Appends submission record to a local CSV log file."""
    file_exists = os.path.exists(CSV_AUDIT_LOG_FILE)
    
    headers = ["Timestamp", "Sheet_Row", "Customer_Ref_Number", "Master_BL_Number", "Maalbardaar_Shipment_ID", "Status", "Details"]
    
    with open(CSV_AUDIT_LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(headers)  # Write CSV header on first creation
        
        writer.writerow([timestamp, row_num, ref_no, mbl_no, shipment_id, status, details])

def run_post_shipments():
    print("=" * 65)
    print("      STAGE 2: POST SHIPMENTS TO MAALBARDAAR API")
    print("=" * 65)

    if not os.path.exists(INPUT_PAYLOADS_FILE):
        print(f"[ERROR] '{INPUT_PAYLOADS_FILE}' not found. Run extract_and_approve.py first.")
        return

    with open(INPUT_PAYLOADS_FILE, "r", encoding="utf-8") as f:
        payloads = json.load(f)

    if not payloads:
        print("[INFO] No approved payloads to post.")
        return

    posted_keys = load_posted_records()

    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)

    try:
        sh = gc.open(SPREADSHEET_NAME)
        worksheet = sh.worksheet(SHEET_TAB_NAME)
    except Exception as e:
        print(f"[ERROR] Connecting to Google Sheets: {e}")
        return

    headers = [h.strip() for h in worksheet.get_all_values()[0]]
    status_col_idx = headers.index("Maalbardaar Shipment ID") + 1 if "Maalbardaar Shipment ID" in headers else None

    api_headers = {
        "Authorization": f"Bearer {MAALBARDAAR_API_KEY}",
        "Content-Type": "application/json"
    }

    success_count = 0

    for payload in payloads:
        sheet_row = payload.pop("sheet_row", None)
        mbl = payload.get("master_bill_of_lading_number", "")
        ref = payload.get("customer_ref_number", "")
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        unique_key = f"{mbl}_{ref}".strip("_")

        if unique_key in posted_keys:
            print(f"[SKIPPED] Duplicate prevented for MBL '{mbl}' / Ref '{ref}'.")
            log_to_csv(now_str, sheet_row, ref, mbl, "N/A", "SKIPPED", "Duplicate record skipped")
            continue

        print(f"\n[POSTING] Row {sheet_row} -> MBL: {mbl} | Ref: {ref}")

        try:
            response = requests.post(MAALBARDAAR_API_URL, json=payload, headers=api_headers, timeout=15)
            response.raise_for_status()

            resp_json = response.json()
            generated_id = resp_json.get("id") or resp_json.get("shipment_id") or f"PROCESSED-{sheet_row}"

            save_posted_record(unique_key)
            success_count += 1

            if status_col_idx and sheet_row:
                worksheet.update_cell(sheet_row, status_col_idx, generated_id)
                print(f"  [SUCCESS] Created in Maalbardaar -> ID: {generated_id}")
                print(f"  [UPDATED] Live Sheet Row {sheet_row} Column A -> '{generated_id}'")

            log_to_csv(now_str, sheet_row, ref, mbl, generated_id, "SUCCESS", "Posted successfully")

        except Exception as e:
            err_msg = str(e)
            print(f"  [ERROR] API Post Failed for Row {sheet_row}: {err_msg}")
            log_to_csv(now_str, sheet_row, ref, mbl, "FAILED", "FAILED", err_msg)

    print("=" * 65)
    print(f"[COMPLETED] {success_count} new shipment(s) created and logged to '{CSV_AUDIT_LOG_FILE}'.")

if __name__ == "__main__":
    run_post_shipments()