# Dynamic Duo Cleaning - Schedule Planner v17

This version changes the app from only a weekly optimizer into a live future scheduling planner.

## Main workflow

1. Export BookingKoala bookings for the next 1-4 weeks.
2. Upload the CSV in the app.
3. Click **Save as Future Schedule** so both admins use the same shared schedule.
4. The app builds the recommended cleaner/team schedule by day using addresses, cleaner locations, Google routing, job labor hours, and the 30-minute minimum gap.
5. During the week, use **Live Booking Planner** before confirming a new lead.
6. Save the selected option as **Pending Hold** or **Confirmed**. The app writes it to the **Live Bookings** Google Sheet tab so future suggestions treat that slot as occupied/held.
7. Weekend review should become exception-only: check unassigned/problem jobs, not rebuild the whole schedule manually.

## Google Sheet tabs

Existing tabs still work. v17 will create these automatically if they do not exist:

- Live Bookings
- Booking Decisions

`Live Bookings` stores pending holds and confirmed additions created from the planner. `Booking Decisions` logs the chosen slot and top alternatives so admins can understand why a booking was placed there.

## Duration logic

BookingKoala `Estimated job length (HH:MM)` is treated as total one-person labor time.

- 1 cleaner = full labor time
- 2 cleaners = half
- 3 cleaners = one-third

The scheduler also adds the configured minimum gap between jobs, default 30 minutes, plus travel time.

## Files to upload to GitHub

Replace at least:

- app.py
- optimizer_core.py
- README.md

Then commit and reboot Streamlit.
