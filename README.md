# Dynamic Duo Cleaning - Simple Schedule Planner v10

This version is simplified for daily use.

## Main workflow

1. Export weekly bookings from BookingKoala/GHL.
2. Upload the CSV in the app.
3. Optionally save it as the shared Active Week in Google Sheets so both admins can see it.
4. Add temporary teams for the week if needed, such as Isabel/Jacky or Billy/Eduardo.
5. Optionally force a cleaner/team or worker count for specific jobs.
6. Review the schedule by day.
7. Approve/lock/reject rows.
8. Export the approved schedule and update BookingKoala manually.

## What was simplified from v9

- Removed the separate Alerts + Emergency tab.
- Removed the Map tab from the main workflow.
- Focused the interface on Weekly Schedule, New Booking Checker, Cleaners & Teams, and Export.
- Added an easy temporary team builder so you can select multiple cleaners working together for a day/week.
- Added an optional job-control table to force a cleaner/team, require a team, or keep a client locked.
- Added day-by-day schedule cards showing each cleaner/team route, total jobs, work hours, and miles.

## Files

- `app.py` - simplified Streamlit interface.
- `optimizer_core.py` - scheduling, Google Sheets, Google Maps, and optimization logic.
- `requirements.txt` - Python requirements.
- `dynamic_duo_google_sheets_master_template.xlsx` - shared Google Sheets template.

## Deploy

Use `app.py` as the main file on Streamlit Cloud.

Keep API keys and service account JSON only in Streamlit Secrets, not GitHub.
