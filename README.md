# Dynamic Duo Cleaning - Schedule Optimizer v7

This is a Streamlit web app for weekly cleaning route/schedule planning.

## What v7 does

- Upload weekly BookingKoala/GHL bookings CSV
- Save uploaded BookingKoala/GHL CSV as the shared **Active Week** in Google Sheets
- Let the second admin load the same Active Week without uploading again
- Read shared master data from Google Sheets if configured
- Keep cleaner data, crew rules, day-offs, area memory, active bookings, and actual-time history in one shared place for two admins
- Optimize cleaner/team schedules by day, route, drive time, job duration, priority, profit, and risk buffer
- Support fixed crews such as Isabel/Jacky and optional pairings such as Billy/Eduardo
- Check new bookings before confirming them
- Flag bad routes, late-running risks, and low-profit jobs
- Suggest moving flexible clients to better cluster days
- Generate cleaner daily text messages
- Let a manager approve, lock, reject, or mark assignments for review before export
- Optionally save approved/reviewed schedules and alerts back to Google Sheets

## Recommended workflow

1. Keep your shared master data in Google Sheets:
   - Cleaners
   - Crew Rules
   - Availability Exceptions
   - Area Memory
   - Actual Time History
2. Export the upcoming week from BookingKoala or GHL.
3. Upload the bookings CSV into the app.
4. Click **Save uploaded CSV as Active Week for both admins**.
5. Run/review the optimizer.
6. The second admin can open the app, leave the bookings uploader empty, and the same Active Week will load from Google Sheets.
7. Approve, lock, reject, or mark rows for review.
8. Export the approved schedule.
9. Update BookingKoala manually.
10. Optionally save approved schedule / reviewed schedule / alerts back into Google Sheets so both admins can see the same final version.

BookingKoala stays the official booking calendar. This app is the planning and decision layer.

## Google Sheets setup

Use the included file:

`dynamic_duo_google_sheets_master_template.xlsx`

Upload or import it into Google Sheets. It contains the tabs the app expects:

- Instructions
- Cleaners
- Crew Rules
- Availability Exceptions
- Area Memory
- Actual Time History
- Active Bookings
- Active Week Metadata
- Approved Schedule
- Reviewed Schedule
- Alerts

### Important

Weekly bookings still come from your BookingKoala/GHL CSV export, but once you click **Save uploaded CSV as Active Week**, the app writes those rows into the `Active Bookings` tab. Other admins can then load the same week from Google Sheets without re-uploading the CSV.

## Streamlit secrets setup

Do not put API keys or Google service account JSON directly into GitHub.

Copy `.streamlit/secrets.example.toml` to `.streamlit/secrets.toml` for local testing, or paste the same values into Streamlit Cloud app secrets.

Required for Google Maps driving routes:

```toml
GOOGLE_MAPS_API_KEY = "YOUR_GOOGLE_MAPS_API_KEY"
```

Required for Google Sheets shared master data:

```toml
[google_sheets]
spreadsheet_id = "YOUR_GOOGLE_SHEET_ID"

[gcp_service_account]
type = "service_account"
project_id = "YOUR_PROJECT_ID"
private_key_id = "YOUR_PRIVATE_KEY_ID"
private_key = "-----BEGIN PRIVATE KEY-----\nYOUR_PRIVATE_KEY\n-----END PRIVATE KEY-----\n"
client_email = "your-service-account@your-project.iam.gserviceaccount.com"
client_id = "YOUR_CLIENT_ID"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "YOUR_CLIENT_CERT_URL"
universe_domain = "googleapis.com"
```

Then open your Google Sheet and share it with the `client_email` as Editor.

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

## Deploy

1. Create a GitHub repo.
2. Upload the files from this folder.
3. Deploy the repo on Streamlit Community Cloud.
4. Add your secrets in Streamlit Cloud settings.
5. Open the app and test with sample data first.

## Data override behavior

- If Google Sheets is connected, the app reads master-data tabs from the Sheet.
- If you upload a BookingKoala/GHL CSV, that upload overrides `Active Bookings` for your current run.
- Click **Save uploaded CSV as Active Week for both admins** to write the upload to Google Sheets.
- If the bookings uploader is empty, the app loads the shared `Active Bookings` tab.
- If neither Sheets nor CSV exists, the app uses sample data so the app still opens.

## First real test suggestion

Test with one real week first. Check:

- Did any client get missed?
- Did fixed crews stay together correctly?
- Did optional crews avoid double-booking?
- Were drive times realistic with Google Maps enabled?
- Were the manager approval/export steps easy enough for both admins?
- Did the Active Week load correctly for both admins?
- Did the actual-vs-estimated history add useful buffers?
