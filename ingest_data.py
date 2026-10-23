import sys
import gspread
from google.oauth2.service_account import Credentials

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
SERVICE_ACCOUNT_FILE = "credentials.json"
SPREADSHEET_NAME = "Pending Shipments"  # Exact title of your Google Sheet
SHEET_TAB_NAME = "Sheet1"                # Sheet tab name (default: Sheet1)

# Scopes required for Google Sheets & Google Drive API
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive"
]

def get_google_sheets_client():
    """Authenticate and return the gspread client."""
    try:
        creds = Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, 
            scopes=SCOPES
        )
        client = gspread.authorize(creds)
        return client
    except FileNotFoundError:
        print(f"❌ Error: Credentials file '{SERVICE_ACCOUNT_FILE}' not found.")
        print("Please ensure your Google Service Account JSON key is renamed to 'credentials.json' and placed in the project folder.")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Authentication Error: {e}")
        sys.exit(1)

def run_ingestion():
    print("=" * 65)
    print("      MAALBARDAAR GOOGLE SHEETS DATA INGESTION SCRIPT")
    print("=" * 65)

    # 1. Connect to Google Sheets
    print("... Authenticating with Google Sheets & Drive API...")
    gc = get_google_sheets_client()

    try:
        sh = gc.open(SPREADSHEET_NAME)
        worksheet = sh.worksheet(SHEET_TAB_NAME)
        print(f"OK: Successfully connected to Sheet: '{SPREADSHEET_NAME}'\n")
    except Exception as e:
        print(f"FAILED: Error opening spreadsheet '{SPREADSHEET_NAME}': {e}")
        print("Ensure the sheet name is correct and shared with your Service Account email as Editor.")
        return

    # 2. Fetch all values from sheet
    all_values = worksheet.get_all_values()
    if not all_values:
        print("FAILED: No data found in the spreadsheet.")
        return

    headers = [h.strip() for h in all_values[0]]
    rows = all_values[1:]

    # Map column positions (1-based index for gspread)
    required_cols = [
        "Maalbardaar Shipment ID", 
        "File Number (Drive)", 
        "Company Name", 
        "Document Type", 
        "MBL/HBL", 
        "Special Instructions"
    ]

    col_map = {}
    for col_name in required_cols:
        if col_name in headers:
            col_map[col_name] = headers.index(col_name) + 1
        else:
            print(f"FAILED: Required column missing: '{col_name}'")
            return

    status_col_idx = col_map["Maalbardaar Shipment ID"]
    file_col_idx = col_map["File Number (Drive)"]
    company_col_idx = col_map["Company Name"]
    doc_type_col_idx = col_map["Document Type"]
    mbl_col_idx = col_map["MBL/HBL"]
    spec_col_idx = col_map["Special Instructions"]

    ingested_count = 0
    ingested_payloads = []

    # 3. Read and process pending rows
    for row_num, row in enumerate(rows, start=2):  # Row 1 is header
        row_data = row + [""] * (len(headers) - len(row))
        status = row_data[status_col_idx - 1].strip().upper()

        if status == "PENDING":
            ingested_count += 1
            file_no = row_data[file_col_idx - 1].strip() or "MISSING"
            company = row_data[company_col_idx - 1].strip() or "MISSING"
            doc_type = row_data[doc_type_col_idx - 1].strip() or "MISSING"
            mbl_hbl = row_data[mbl_col_idx - 1].strip() or "MISSING"
            spec_inst = row_data[spec_col_idx - 1].strip()

            # Separate Special Instructions / Audit Warnings and Google Drive Link
            drive_link = ""
            notes = spec_inst

            if "||" in spec_inst:
                parts = spec_inst.split("||")
                notes = parts[0].strip()
                drive_link = parts[1].strip()
            elif "https://drive.google.com" in spec_inst:
                drive_link = spec_inst.strip()
                notes = ""

            # Prepare structured payload (Ready for future API POST request)
            payload = {
                "sheet_row": row_num,
                "file_number": file_no,
                "company_name": company,
                "document_type": doc_type,
                "mbl_hbl": mbl_hbl,
                "special_instructions": notes,
                "drive_link": drive_link
            }
            ingested_payloads.append(payload)

            # Print ingested details in console (READ ONLY - NO SHEET UPDATES)
            print(f"------------------------------------------------------------")
            print(f" INGESTED RECORD [Row {row_num}]")
            print(f"------------------------------------------------------------")
            print(f"   File Number    : {payload['file_number']}")
            print(f"   Company Name   : {payload['company_name']}")
            print(f"   Document Type  : {payload['document_type']}")
            print(f"   MBL / HBL      : {payload['mbl_hbl']}")
            print(f"   Notes / Audit  : {payload['special_instructions']}")
            print(f"   Drive Link     : {payload['drive_link']}")
            print(f"  Status on Sheet : Left as 'PENDING'\n")

    print("=" * 65)
    print(f"OK: Read Ingestion Complete! {ingested_count} pending shipment(s) loaded.")
    print("   (Google Sheet remained unmodified)")
    print("=" * 65)

if __name__ == "__main__":
    run_ingestion()