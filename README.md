# Dynamic Duo Cleaning - Schedule Planner v19

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

Existing tabs still work. v19 will create these automatically if they do not exist:

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

## v19 changes: cluster-first mileage policy

This version changes the mileage logic to match Dynamic Duo Cleaning's preferred reporting:

- Cleaner base/home to the first job is **not counted** in the displayed route miles.
- Last job back home is **not counted**.
- Only job-to-job mileage between cleanings is counted.
- The optimizer still uses base-to-first-job distance as a light positioning signal so a cleaner is not assigned to a completely unreasonable first stop, but it does not appear in daily mileage totals or travel cost.
- A `positioning_miles_not_counted` column is shown for review/debugging.
- Scoring now penalizes long job-to-job jumps and rewards tighter clusters, so the app behaves more like a dispatcher grouping nearby jobs.


## v19 baseline + cluster logic update

- Preserves every BookingKoala row as the baseline schedule when Provider/team and start time are present.
- Unknown providers are kept as manual BookingKoala resources instead of being dropped.
- Mileage now counts only job-to-job travel. Cleaner home/base to first job and last job to home are excluded.
- New/pending bookings are inserted into existing future routes by least added job-to-job miles.
- Long job-to-job jumps are flagged as bad cluster jumps for future rescheduling decisions.
