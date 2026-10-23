import os
import re
import sys
import io
import json
import subprocess
import tempfile
import gspread
import time
import csv
from dotenv import load_dotenv
from google import genai
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload

# ---------------------------------------------------------
# LOAD ENVIRONMENT & INITIALIZE CLIENTS
# ---------------------------------------------------------
load_dotenv()

GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY") or ""
SPREADSHEET_NAME: str = os.getenv("SPREADSHEET_NAME") or "Pending Shipments"
SHEET_TAB_NAME: str = os.getenv("SHEET_TAB_NAME") or "Sheet1"
SERVICE_ACCOUNT_FILE = "credentials.json"
OUTPUT_PAYLOADS_FILE = "pending_payloads.json"

if not GEMINI_API_KEY:
    print("[ERROR] GEMINI_API_KEY missing from .env file.")
    sys.exit(1)

ai_client = genai.Client(api_key=GEMINI_API_KEY)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

# ---------------------------------------------------------
# HELPER FUNCTIONS
# ---------------------------------------------------------
def extract_file_id_from_url(url: str) -> str | None:
    match = re.search(r"/d/([a-zA-Z0-9_-]+)", url)
    return match.group(1) if match else None

def download_pdf_from_drive(drive_service, file_id: str) -> tuple[bytes | None, str]:
    try:
        file_metadata = drive_service.files().get(fileId=file_id, supportsAllDrives=True).execute()
        file_name: str = file_metadata.get("name", "document.pdf")
        request = drive_service.files().get_media(fileId=file_id, supportsAllDrives=True)
        file_stream = io.BytesIO()
        downloader = MediaIoBaseDownload(file_stream, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()
        return file_stream.getvalue(), file_name
    except Exception as e:
        print(f"  [ERROR] Failed to download file from Drive: {e}")
        return None, "document.pdf"

def open_pdf_locally(pdf_bytes: bytes, filename: str):
    temp_dir = tempfile.gettempdir()
    temp_path = os.path.join(temp_dir, filename)
    with open(temp_path, "wb") as f:
        f.write(pdf_bytes)

    print(f"  [INFO] Opening local viewer for: {filename}")
    try:
        if sys.platform == "win32":
            os.startfile(temp_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", temp_path])
        else:
            subprocess.run(["xdg-open", temp_path], check=False)
    except Exception as e:
        print(f"  [WARNING] Could not auto-open PDF viewer: {e}")

def extract_with_gemini_ai(pdf_bytes: bytes, retries: int = 3) -> dict:
    prompt = """
    Analyze this logistics document PDF and return a valid JSON object only with these exact keys:
    {
      "customer_ref_number": "File or job number (e.g. 9318, S042). Return empty string if missing.",
      "master_bill_of_lading_number": "MBL, HBL, or AWB number. Return empty string if missing.",
      "bank_document_number": "L/C or Documentary Credit number. Return empty string if missing.",
      "invoice_no": "Commercial Invoice number. Return empty string if missing.",
      "consignment_mode": "FCL, LCL, or Air. Default to FCL if unclear."
    }
    Return ONLY JSON. Do not write explanations.
    """
    for attempt in range(1, retries + 1):
        try:
            response = ai_client.models.generate_content(
                model='gemini-3.5-flash-lite',
                contents=[
                    genai.types.Part.from_bytes(data=pdf_bytes, mime_type='application/pdf'),
                    prompt
                ]
            )
            raw_text = response.text or ""
            cleaned_text = raw_text.replace("```json", "").replace("```", "").strip()
            return json.loads(cleaned_text)
        except Exception as e:
            if "503" in str(e) or "UNAVAILABLE" in str(e):
                if attempt < retries:
                    sleep_time = attempt * 3  # Wait 3s, then 6s
                    print(f"  [RETRY] Gemini API busy (503). Retrying in {sleep_time}s (Attempt {attempt}/{retries})...")
                    time.sleep(sleep_time)
                    continue
            print(f"  [WARNING] Gemini extraction warning: {e}")
            break
    return {}
def interactive_review_and_edit(payload: dict, pdf_bytes: bytes | None, pdf_filename: str | None) -> dict | None:
    opened_pdf_already = False
    filename_str = pdf_filename or "document.pdf"

    while True:
        print("\n" + "="*60)
        print("          REVIEW SHIPMENT PAYLOAD FOR MAALBARDAAR")
        print("="*60)
        print(f"  Row Index in Sheet                          : {payload.get('sheet_row')}")
        print(f"  1. File Ref Number (customer_ref_number)    : {payload.get('customer_ref_number') or '[MISSING]'}")
        print(f"  2. Master B/L Number (mbl_number)          : {payload.get('master_bill_of_lading_number') or '[MISSING]'}")
        print(f"  3. Bank Doc / L/C (bank_document_number)  : {payload.get('bank_document_number') or 'N/A'}")
        print(f"  4. Invoice Number (invoice_no)             : {payload.get('invoice_no') or 'N/A'}")
        print(f"  5. Consignment Mode (consignment_mode)     : {payload.get('consignment_mode')}")
        print("="*60)

        missing_critical = not payload.get("customer_ref_number") or not payload.get("master_bill_of_lading_number")

        if missing_critical and not opened_pdf_already and pdf_bytes:
            print("\n[ATTENTION] Critical field(s) missing. Opening PDF for manual verification...")
            open_pdf_locally(pdf_bytes, filename_str)
            opened_pdf_already = True

        print("\nOPTIONS:")
        print("  [A] Approve & Queue for Posting")
        print("  [E] Edit a Field")
        print("  [V] Open PDF Viewer")
        print("  [S] Skip this row")

        choice = input("\nEnter choice (A / E / V / S): ").strip().upper()

        if choice == "A":
            return payload
        elif choice == "S":
            print("  [INFO] Skipped by user.")
            return None
        elif choice == "V":
            if pdf_bytes:
                open_pdf_locally(pdf_bytes, filename_str)
            else:
                print("  [ERROR] No local PDF bytes available.")
        elif choice == "E":
            print("\nSelect field to edit (1-5):")
            field_choice = input("Choice: ").strip()
            if field_choice == "1":
                payload["customer_ref_number"] = input("Enter File Ref Number: ").strip()
            elif field_choice == "2":
                payload["master_bill_of_lading_number"] = input("Enter Master B/L Number: ").strip()
            elif field_choice == "3":
                payload["bank_document_number"] = input("Enter Bank Doc / L/C Number: ").strip()
            elif field_choice == "4":
                payload["invoice_no"] = input("Enter Invoice Number: ").strip()
            elif field_choice == "5":
                mode_input = input("Enter Consignment Mode (FCL/LCL/Air): ").strip().upper()
                payload["consignment_mode"] = "LCL" if mode_input == "LC" else mode_input

# ---------------------------------------------------------
# MAIN PROCESS EXECUTION
# ---------------------------------------------------------
def run_extraction():
    print("=" * 65)
    print("      STAGE 1: EXTRACTION, REVIEW & APPROVAL")
    print("=" * 65)

    creds = Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    gc = gspread.authorize(creds)
    drive_service = build('drive', 'v3', credentials=creds)

    try:
        sh = gc.open(SPREADSHEET_NAME)
        worksheet = sh.worksheet(SHEET_TAB_NAME)
    except Exception as e:
        print(f"[ERROR] Error opening sheet: {e}")
        return

    all_values = worksheet.get_all_values()
    if not all_values:
        print("[INFO] No data found.")
        return

    headers = [h.strip() for h in all_values[0]]
    rows = all_values[1:]

    def get_col_idx(name: str) -> int | None:
        return headers.index(name) + 1 if name in headers else None

    status_col_idx = get_col_idx("Maalbardaar Shipment ID")
    file_col_idx = get_col_idx("File Number (Drive)")
    spec_col_idx = get_col_idx("Special Instructions")
    doc_type_col_idx = get_col_idx("Document Type")
    mbl_col_idx = get_col_idx("MBL/HBL")

    approved_payloads = []

    for row_num, row in enumerate(rows, start=2):
        row_data = row + [""] * (len(headers) - len(row))
        status = row_data[status_col_idx - 1].strip().upper() if status_col_idx else ""

        if status == "PENDING":
            file_no = row_data[file_col_idx - 1].strip() if file_col_idx else ""
            spec_inst = row_data[spec_col_idx - 1].strip() if spec_col_idx else ""
            consignment_mode = row_data[doc_type_col_idx - 1].strip() if doc_type_col_idx else "FCL"
            sheet_mbl = row_data[mbl_col_idx - 1].strip() if mbl_col_idx else ""

            drive_link = ""
            if "||" in spec_inst:
                drive_link = spec_inst.split("||")[1].strip()
            elif "https://drive.google.com" in spec_inst:
                drive_link = spec_inst.strip()

            file_id = extract_file_id_from_url(drive_link)
            pdf_bytes: bytes | None = None
            pdf_filename: str = "document.pdf"
            ai_data: dict = {}

            if file_id:
                print(f"\n[INFO] Row {row_num}: Downloading PDF...")
                pdf_bytes, pdf_filename = download_pdf_from_drive(drive_service, file_id)
                if pdf_bytes:
                    print("[INFO] Running Gemini AI extraction...")
                    ai_data = extract_with_gemini_ai(pdf_bytes)

            # Determine consignment mode & convert LC -> LCL
            raw_mode = consignment_mode if consignment_mode != "MISSING" else ai_data.get("consignment_mode", "FCL")
            final_mode = "LCL" if raw_mode.upper() == "LC" else raw_mode.upper()

            payload = {
                "sheet_row": row_num,
                "customer_ref_number": file_no if file_no != "MISSING" else ai_data.get("customer_ref_number", ""),
                "consignment_mode": final_mode,
                "master_bill_of_lading_number": sheet_mbl if sheet_mbl != "MISSING" else ai_data.get("master_bill_of_lading_number", ""),
                "bank_document_number": ai_data.get("bank_document_number", ""),
                "invoice_no": ai_data.get("invoice_no", ""),
                "item_description": f"{spec_inst} | Drive Link: {drive_link}".strip(" |"),
                "status": "draft"
            }

            approved = interactive_review_and_edit(payload, pdf_bytes, pdf_filename)
            if approved:
                approved_payloads.append(approved)

    with open(OUTPUT_PAYLOADS_FILE, "w", encoding="utf-8") as f:
        json.dump(approved_payloads, f, indent=2)

    print(f"\n[SUCCESS] Completed. {len(approved_payloads)} shipment(s) approved and saved to '{OUTPUT_PAYLOADS_FILE}'.")

if __name__ == "__main__":
    run_extraction()