# Maalbardaar Shipment Automation

Scripts for reading pending shipments from Google Sheets, extracting and reviewing shipment details, and submitting approved payloads to the Maalbardaar shipments API.

## Workflow

1. **Optional read-only check:** Run `ingest_data.py` to list rows marked `PENDING` and inspect their shipment details. This script does not update the sheet or create an output file.
2. **Extract and approve:** Run `extract_and_approve.py`. It reads pending rows, downloads linked Drive PDFs when available, uses Gemini to extract shipment fields, and prompts you to approve, edit, view, or skip each record. Approved records are saved to `pending_payloads.json`.
3. **Post approved records:** Run `post_shipments.py` to submit the saved payloads. Successful submissions are recorded locally, and the generated shipment ID is written back into the `Maalbardaar Shipment ID` column in Google Sheets.

Posting sends requests to the Maalbardaar API and can modify both the external service and the spreadsheet. Review the approved payloads before running this step.

## Requirements

- Python 3.10 or later
- A Google service-account JSON key with access to the spreadsheet and its linked Drive files
- A Gemini API key for extraction
- A Maalbardaar API key for the posting script

Install the Python packages from the project directory:

```bash
python -m pip install gspread google-auth google-api-python-client google-genai python-dotenv requests
```

## Configuration

1. Save the Google service-account key in the project directory as `credentials.json`.
2. Share the spreadsheet with the service-account email address. Grant it Editor access; ensure the account can access any linked Drive PDFs as well.
3. Create a `.env` file in the project directory:

   ```dotenv
   GEMINI_API_KEY=your_gemini_api_key
   MAALBARDAAR_API_KEY=your_maalbardaar_api_key
   # Optional; these are the defaults:
   SPREADSHEET_NAME=Pending Shipments
   SHEET_TAB_NAME=Sheet1
   MAALBARDAAR_API_URL=https://api.maalbardaar.com/shipments-app/shipments/create
   ```

   `GEMINI_API_KEY` is required by `extract_and_approve.py`. `MAALBARDAAR_API_KEY` is required by `post_shipments.py`. The spreadsheet name, tab, and API URL can be overridden in `.env`.

   **Posting authentication needs configuration:** although `post_shipments.py` checks that `MAALBARDAAR_API_KEY` is present, its request currently sends a literal placeholder as the `Authorization` header instead of using that environment variable. Configure the header in the script for the API's required authentication scheme before attempting live submissions.

## Google Sheet format

The first row must contain these column names:

| Column                      | Used for                                                    |
| --------------------------- | ----------------------------------------------------------- |
| `Maalbardaar Shipment ID` | Pending status and, after posting, the returned shipment ID |
| `File Number (Drive)`     | Shipment/customer reference                                 |
| `Company Name`            | Company detail displayed by the read-only ingestion script  |
| `Document Type`           | Consignment mode used during extraction                     |
| `MBL/HBL`                 | Master bill of lading number                                |
| `Special Instructions`    | Notes and, optionally, the Drive PDF link                   |

Rows are processed when `Maalbardaar Shipment ID` is `PENDING` (case-insensitive). Put a Drive link in `Special Instructions` either by itself or after the notes separated with `||`, for example:

```text
Check consignee details || https://drive.google.com/file/d/FILE_ID/view
```

The extraction script can also use the sheet's `File Number (Drive)` and `MBL/HBL` values directly. For missing values, Gemini may extract details from the linked PDF; you can review and edit fields interactively before approval.

## Running the scripts

Run each script from the project directory so it can find `.env`, `credentials.json`, and its local data files:

```bash
python ingest_data.py
python extract_and_approve.py
python post_shipments.py
```

Run them in that order when using the full workflow; the ingestion step is optional and read-only. The extraction and posting scripts can also be run separately when their required inputs already exist.

## Local output and duplicate handling

- `pending_payloads.json` — approved records produced by extraction and consumed by posting.
- `posted_shipments.json` — local keys used to prevent reposting the same MBL/reference combination.
- `posted_shipments_log.csv` — append-only success, failure, and duplicate-skip audit log.

These files, `.env`, and `credentials.json` are excluded by `.gitignore`. Keep credentials private and do not commit them or generated shipment data.
