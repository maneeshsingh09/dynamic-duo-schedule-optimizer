# Dynamic Duo Cleaning Schedule Planner v11

Simplified Streamlit version focused on the real workflow:

1. Upload BookingKoala/GHL weekly CSV
2. Save as Active Week for both admins, if Google Sheets is connected
3. Build day-by-day cleaner/team routes
4. Review/approve schedule by day
5. Use New Booking Checker before confirming new jobs
6. Export approved schedule / cleaner texts

## Main files

- `app.py` — Streamlit app
- `optimizer_core.py` — scheduling and Google Sheets/Maps helper logic
- `requirements.txt` — packages for Streamlit Cloud
- `dynamic_duo_google_sheets_master_template.xlsx` — Google Sheets master template

## Streamlit entrypoint

Use:

```text
app.py
```

## Google routing note

The app works without Google routing by using approximate fallback miles. To use real driving miles, create a Google Maps API key that can call:

- Routes API
- Geocoding API
- Distance Matrix API (Legacy), optional backup

While testing, keep Application restrictions as `None`; after it works, restrict the key safely.
