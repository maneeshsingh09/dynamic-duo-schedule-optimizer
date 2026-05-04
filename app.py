from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

import pandas as pd
import streamlit as st

import optimizer_core as core

APP_TITLE = "Dynamic Duo Cleaning - Simple Schedule Planner v10"


def safe_cols(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df[[c for c in cols if c in df.columns]].copy()


def normalize_bookings_for_editor(raw: pd.DataFrame) -> pd.DataFrame:
    df = core.normalize_columns(raw.copy(), core.BOOKING_ALIASES) if raw is not None and not raw.empty else pd.DataFrame()
    if df.empty:
        return df
    df = df.reset_index(drop=True)
    df["booking_row_id"] = range(len(df))
    defaults: Dict[str, Any] = {
        "client": "",
        "address": "",
        "preferred_resource": "",
        "lock_resource": "No",
        "min_workers": 1,
        "max_workers": 2,
        "requires_team": "No",
        "priority": "Normal",
        "job_hours": 2.5,
        "job_price": "",
        "time_window": "Flexible",
    }
    for col, val in defaults.items():
        if col not in df.columns:
            df[col] = val
        df[col] = df[col].fillna(val)
    return df


def apply_booking_editor(raw: pd.DataFrame, edited: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty or edited is None or edited.empty:
        return raw
    base = normalize_bookings_for_editor(raw)
    edit_cols = [
        "booking_row_id", "preferred_resource", "lock_resource", "min_workers", "max_workers",
        "requires_team", "priority", "job_hours", "job_price", "time_window",
    ]
    edit = edited[[c for c in edit_cols if c in edited.columns]].copy()
    if "booking_row_id" not in edit.columns:
        return base
    edit = edit.set_index("booking_row_id")
    base = base.set_index("booking_row_id")
    for col in edit.columns:
        base.loc[edit.index, col] = edit[col]
    return base.reset_index(drop=True)


def append_temporary_crews(crews_raw: pd.DataFrame) -> pd.DataFrame:
    temp = st.session_state.get("temp_crews", [])
    if not temp:
        return crews_raw
    temp_df = pd.DataFrame(temp)
    if crews_raw is None or crews_raw.empty:
        return temp_df
    return pd.concat([crews_raw, temp_df], ignore_index=True)


def render_quick_team_builder(cleaners_raw: pd.DataFrame) -> None:
    st.markdown("### 2) Teams for this week")
    st.caption("Use this when multiple cleaners will ride/work together. The app will treat them as one route resource, and one-person hours will be divided by the team size/productivity.")
    try:
        cleaners_preview = core.prepare_cleaners(cleaners_raw)
        cleaner_names = list(cleaners_preview["cleaner"].astype(str))
    except Exception:
        cleaner_names = []

    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([1.3, 1.7, 1.3, 1.0])
        with c1:
            team_name = st.text_input("Team name", value="")
        with c2:
            members = st.multiselect("Cleaners working together", cleaner_names)
        with c3:
            days = st.multiselect("Days", [d.title() for d in core.WEEKDAY_ORDER[:5]], default=[d.title() for d in core.WEEKDAY_ORDER[:5]])
        with c4:
            team_type = st.selectbox("Type", ["Fixed", "Optional"])
        multiplier = st.slider("Speed factor", min_value=1.0, max_value=4.0, value=float(max(1, len(members))), step=0.25, help="Example: 2 cleaners usually complete a 4-hour one-person job in about 2 hours, so speed factor is 2.")
        if st.button("Add team for this run", type="secondary", disabled=len(members) < 2):
            final_name = team_name.strip() or "/".join(members)
            row = {
                "resource_name": final_name,
                "members": ";".join(members),
                "team_type": team_type,
                "available_days": ",".join(days),
                "base_address": "",
                "can_split_after_job": "No" if team_type == "Fixed" else "Yes",
                "always_together": "Yes" if team_type == "Fixed" else "No",
                "carpool": "Yes",
                "max_jobs_per_day": 4,
                "max_hours_per_day": 8,
                "start_time": core.DEFAULT_START,
                "end_time": core.DEFAULT_END,
                "productivity_multiplier": multiplier,
                "hourly_cost_override": "",
            }
            st.session_state.setdefault("temp_crews", []).append(row)
            st.success(f"Added team: {final_name}")
            st.rerun()

        temp = st.session_state.get("temp_crews", [])
        if temp:
            st.write("Temporary teams added for this run:")
            st.dataframe(pd.DataFrame(temp)[["resource_name", "members", "team_type", "available_days", "productivity_multiplier"]], use_container_width=True, hide_index=True)
            if st.button("Clear temporary teams"):
                st.session_state["temp_crews"] = []
                st.rerun()


def render_assignment_controls(bookings_raw: pd.DataFrame, resource_names: List[str]) -> pd.DataFrame:
    st.markdown("### 3) Optional job controls")
    st.caption("Most of the time, leave this alone. Use it only when you want to force a cleaner/team, require 2 cleaners, or mark a high-priority client before optimization.")
    if bookings_raw is None or bookings_raw.empty:
        return bookings_raw
    norm = normalize_bookings_for_editor(bookings_raw)
    cols = ["booking_row_id", "client", "address", "preferred_resource", "lock_resource", "min_workers", "max_workers", "requires_team", "priority", "job_hours", "job_price", "time_window"]
    view = safe_cols(norm, cols)
    with st.expander("Optional: force cleaner/team or worker count for specific jobs", expanded=False):
        edited = st.data_editor(
            view,
            use_container_width=True,
            hide_index=True,
            disabled=["booking_row_id", "client", "address"],
            column_config={
                "preferred_resource": st.column_config.SelectboxColumn("Preferred cleaner/team", options=[""] + resource_names),
                "lock_resource": st.column_config.SelectboxColumn("Lock?", options=["No", "Yes"]),
                "requires_team": st.column_config.SelectboxColumn("Team required?", options=["No", "Yes"]),
                "priority": st.column_config.SelectboxColumn("Priority", options=["VIP", "High", "Normal", "Low"]),
                "time_window": st.column_config.SelectboxColumn("Time window", options=["Flexible", "Morning", "Afternoon", "Fixed"]),
            },
            key="simple_booking_controls",
        )
        st.caption("Example: create team Isabel/Jacky above, then choose Isabel/Jacky as preferred cleaner/team and set Lock = Yes for a specific job.")
    return apply_booking_editor(bookings_raw, edited if "edited" in locals() else view)


def day_summary(schedule: pd.DataFrame, day_value: Any) -> pd.DataFrame:
    df = schedule[schedule["date"].astype(str) == str(day_value)].copy()
    if df.empty:
        return df
    return df.sort_values(["resource", "start_min"])


def render_weekly_schedule(schedule: pd.DataFrame, unassigned: pd.DataFrame, alerts: pd.DataFrame, price_suggestions: pd.DataFrame, time_learning: pd.DataFrame) -> pd.DataFrame:
    st.subheader("Weekly schedule by day")
    if schedule.empty:
        st.info("No jobs scheduled yet.")
        return schedule

    dates = sorted(schedule["date"].unique(), key=lambda x: str(x))
    for d in dates:
        day_df = day_summary(schedule, d)
        if day_df.empty:
            continue
        day_label = pd.to_datetime(str(d)).strftime("%A, %b %d")
        total_miles = float(day_df["travel_miles"].astype(float).sum()) if "travel_miles" in day_df else 0.0
        resources_used = int(day_df["resource"].nunique()) if "resource" in day_df else 0
        jobs = len(day_df)
        with st.expander(f"{day_label} — {jobs} jobs | {resources_used} cleaner/team routes | {total_miles:.1f} drive miles before jobs", expanded=True):
            resource_names = list(day_df["resource"].dropna().astype(str).unique())
            for resource in resource_names:
                r = day_df[day_df["resource"].astype(str) == resource].copy().sort_values("start_min")
                miles = float(r["travel_miles"].astype(float).sum()) if "travel_miles" in r else 0.0
                hours = float(r["duration_hours"].astype(float).sum()) if "duration_hours" in r else 0.0
                members = str(r["members"].iloc[0]) if "members" in r.columns and not r.empty else ""
                with st.container(border=True):
                    st.markdown(f"**{resource}** {f'({members})' if members and members != resource else ''}")
                    m1, m2, m3 = st.columns(3)
                    m1.metric("Jobs", len(r))
                    m2.metric("Drive miles", f"{miles:.1f}")
                    m3.metric("Work hours", f"{hours:.1f}")
                    cols = ["start", "end", "client", "city", "cleaning_type", "duration_hours", "travel_miles", "profit_score", "score_notes"]
                    st.dataframe(safe_cols(r, cols), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Approve or adjust final schedule")
    reviewed = core.add_manager_review_columns(schedule)
    display_cols = ["manager_status", "lock_assignment", "manager_note", "date", "day", "resource", "start", "end", "client", "city", "duration_hours", "travel_miles", "profit_score", "instance_id"]
    review_cols = [c for c in display_cols if c in reviewed.columns]
    edited_review = st.data_editor(
        reviewed[review_cols],
        use_container_width=True,
        hide_index=True,
        column_config={
            "manager_status": st.column_config.SelectboxColumn("Status", options=["Approve", "Lock", "Needs Review", "Reject"], required=True),
            "lock_assignment": st.column_config.CheckboxColumn("Lock"),
            "manager_note": st.column_config.TextColumn("Manager note"),
        },
        disabled=[c for c in review_cols if c not in {"manager_status", "lock_assignment", "manager_note"}],
        key="simple_manager_review_editor",
    )
    reviewed_schedule = core.apply_review_status_to_schedule(schedule, edited_review)
    approved, rejected, needs_review = core.split_reviewed_schedule(reviewed_schedule)
    c1, c2, c3 = st.columns(3)
    c1.metric("Approved/locked", len(approved))
    c2.metric("Needs review", len(needs_review))
    c3.metric("Rejected", len(rejected))

    with st.expander("Show problem jobs and learning notes", expanded=False):
        core.display_df("Unassigned jobs", unassigned)
        core.display_df("High-value warnings", safe_cols(alerts, ["date", "resource", "client", "alert", "detail", "severity"]))
        core.display_df("Price / route suggestions", safe_cols(price_suggestions, ["client", "resource", "date", "suggestion", "reason", "profit_score", "travel_miles"]))
        core.display_df("Actual time learning applied", time_learning)
    return reviewed_schedule


def render_new_booking_checker(resources: pd.DataFrame, dates: List[date], schedule: pd.DataFrame, booking_instances: pd.DataFrame, schedules_dict: Dict, member_events: Dict, exceptions: pd.DataFrame, area_memory: pd.DataFrame, points: List[Dict[str, Any]], point_idx: Dict[str, int], api_key: str, use_google: bool, routing_provider: str, hours_are_person_hours: bool, mileage_cost: float, travel_hour_cost: float) -> None:
    st.subheader("New booking checker")
    st.caption("Use this before confirming a new client. It tells you which day and cleaner/team fits best with the existing week.")
    with st.container(border=True):
        r1c1, r1c2, r1c3 = st.columns([1.4, 1.8, 1.0])
        with r1c1:
            nb_client = st.text_input("Client name", value="New Lead")
        with r1c2:
            nb_address = st.text_input("Address/city", value="Lakeville, MN")
        with r1c3:
            nb_price = st.number_input("Quoted price", value=240.0, min_value=0.0, step=10.0)
        r2c1, r2c2, r2c3, r2c4 = st.columns(4)
        with r2c1:
            nb_type = st.selectbox("Cleaning type", ["Standard", "Deep Cleaning", "Move Out", "Airbnb"])
        with r2c2:
            nb_hours = st.number_input("One-person job hours", value=3.0, min_value=0.5, step=0.5)
        with r2c3:
            nb_days = st.text_input("Possible days", value="Tuesday,Wednesday,Thursday")
        with r2c4:
            nb_window = st.selectbox("Time window", ["Flexible", "Morning", "Afternoon", "Fixed"])
        r3c1, r3c2, r3c3 = st.columns(3)
        with r3c1:
            nb_priority = st.selectbox("Priority", ["VIP", "High", "Normal", "Low"], index=2)
        with r3c2:
            workers_choice = st.selectbox("Cleaner setup", ["Can be solo or team", "Must be 2+ cleaners", "Solo only"])
        with r3c3:
            max_options = min(8, len(resources)) if len(resources) else 1
            result_count = st.slider("Options to show", 3, max(3, max_options), min(5, max(3, max_options)))

    if st.button("Find best day + cleaner/team", type="primary"):
        if workers_choice == "Must be 2+ cleaners":
            min_workers, max_workers, requires_team = 2, 4, "Yes"
        elif workers_choice == "Solo only":
            min_workers, max_workers, requires_team = 1, 1, "No"
        else:
            min_workers, max_workers, requires_team = 1, 4, "No"
        new_raw = pd.DataFrame([{ 
            "client": nb_client,
            "address": nb_address,
            "service_date": "",
            "preferred_day": "",
            "flexible_days": nb_days,
            "time_window": nb_window,
            "earliest_start": core.DEFAULT_START,
            "latest_finish": core.DEFAULT_END,
            "job_hours": nb_hours,
            "cleaning_type": nb_type,
            "can_shift": "Yes",
            "frequency": "One time",
            "job_price": nb_price,
            "preferred_resource": "",
            "lock_resource": "No",
            "priority": nb_priority,
            "risk_level": "Medium" if nb_type == "Deep Cleaning" else "Low",
            "min_workers": min_workers,
            "max_workers": max_workers,
            "requires_team": requires_team,
        }])
        new_booking = core.prepare_bookings(new_raw)
        temp_instances = core.expand_recurring_bookings(new_booking, dates)
        all_instances = pd.concat([booking_instances, temp_instances], ignore_index=True)
        pts2, idx2, _ptsdf2 = core.make_points(resources, all_instances, api_key, use_google)
        m2, t2, _ = core.compute_route_matrix(pts2, api_key, use_google, routing_provider)
        suggestions: List[Dict[str, Any]] = []
        for _, row in temp_instances.iterrows():
            for d in core.candidate_dates_for_booking(row, dates):
                for _, res in resources.iterrows():
                    ok, cand = core.evaluate_candidate(row, res.to_dict(), d, schedules_dict, member_events, exceptions, m2, t2, idx2, area_memory, hours_are_person_hours, mileage_cost, travel_hour_cost)
                    if ok:
                        suggestions.append(cand)
        sug_df = pd.DataFrame(suggestions).sort_values("score").head(result_count) if suggestions else pd.DataFrame()
        if sug_df.empty:
            st.error("No clean fit found. Try more flexible days, a wider time window, or assign a team.")
        else:
            best = sug_df.iloc[0]
            st.success(f"Best fit: {best['day']} with {best['resource']} around {best['start']}. Adds about {best['travel_miles']} drive miles before the job.")
            show_cols = ["date", "day", "resource", "resource_type", "members", "start", "end", "duration_hours", "travel_miles", "profit_score", "score_notes"]
            st.dataframe(safe_cols(sug_df, show_cols), use_container_width=True, hide_index=True)
            st.markdown("**Client message option**")
            msg = f"Hi {nb_client if nb_client != 'New Lead' else ''}, we have availability {best['day']} around {best['start']} and already have a cleaner/team in your area that day. Would that work for your cleaning?".replace("Hi ,", "Hi,")
            st.code(msg, language="text")


def render_export_tab(approved_schedule: pd.DataFrame, reviewed_schedule: pd.DataFrame, schedule: pd.DataFrame, unassigned: pd.DataFrame, alerts: pd.DataFrame, price_suggestions: pd.DataFrame, move_messages: pd.DataFrame, time_learning: pd.DataFrame, actuals: pd.DataFrame, resources: pd.DataFrame, points_df: pd.DataFrame, bookings_raw: pd.DataFrame, sheet_id: str, use_google_sheets_master: bool) -> None:
    st.subheader("Export + cleaner texts")
    approved_texts = core.build_daily_text(approved_schedule)
    if approved_texts.empty:
        st.info("Approve rows in the Weekly schedule tab to create cleaner text messages.")
    else:
        for _, r in approved_texts.iterrows():
            st.markdown(f"**{r['resource']} — {pd.to_datetime(r['date']).strftime('%A, %b %d')}**")
            st.code(r["message"], language="text")

    sheets = {
        "Approved Schedule": approved_schedule,
        "Full Reviewed Schedule": reviewed_schedule,
        "Original Recommendation": schedule,
        "Unassigned": unassigned,
        "Warnings": alerts,
        "Price Suggestions": price_suggestions,
        "Move Messages": move_messages,
        "Actual Time Learning": time_learning,
        "Actuals Uploaded": actuals,
        "Cleaner Texts": approved_texts,
        "Resources": resources.drop(columns=["member_keys"], errors="ignore"),
        "Points": points_df,
        "Active Bookings Source": bookings_raw,
    }
    excel = core.to_excel(sheets)
    st.download_button("Download full Excel report", data=excel, file_name="dynamic_duo_simple_schedule_report_v10.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if not approved_schedule.empty:
        st.download_button("Download approved schedule CSV", data=approved_schedule.to_csv(index=False), file_name="approved_schedule_v10.csv", mime="text/csv")

    st.divider()
    st.subheader("Save approved result for both admins")
    if use_google_sheets_master and core.google_sheets_configured(sheet_id)[0]:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Save approved schedule to Google Sheet"):
                st.success(core.write_google_tab(sheet_id, core.MASTER_SHEET_TABS["approved"], approved_schedule))
        with c2:
            if st.button("Save reviewed schedule to Google Sheet"):
                st.success(core.write_google_tab(sheet_id, core.MASTER_SHEET_TABS["reviewed"], reviewed_schedule))
    else:
        st.info("Connect Google Sheets if you want both admins to see the approved result inside the shared Sheet.")


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    st.title(APP_TITLE)
    st.caption("Simplified workflow: upload BookingKoala CSV → build cleaner/team routes → review by day → export/update BookingKoala.")

    with st.sidebar:
        st.header("Basic setup")
        api_key_default = core.get_secret_value("GOOGLE_MAPS_API_KEY")
        api_key = st.text_input("Google Maps API key", value=api_key_default, type="password")
        use_google = st.checkbox("Use Google driving routes", value=bool(api_key_default))
        routing_provider = st.selectbox("Routing provider", ["Auto", "Routes API", "Distance Matrix API (Legacy)", "Approximate only"], index=0)
        if routing_provider == "Approximate only":
            use_google = False
        week_start = st.date_input("Week start", value=date.today())
        horizon_weeks = st.slider("Planning horizon", 1, 4, 2)
        include_weekends = st.checkbox("Include weekends", value=False)
        hours_are_person_hours = st.checkbox("Job hours are one-person hours", value=True)

        st.divider()
        st.header("Shared admin setup")
        sheet_id_default = core.get_google_sheet_id()
        sheet_id = st.text_input("Google Sheet ID", value=sheet_id_default, type="password")
        sheets_ok, sheets_msg = core.google_sheets_configured(sheet_id)
        use_google_sheets_master = st.checkbox("Use shared Google Sheet", value=sheets_ok)
        if use_google_sheets_master:
            st.success("Google Sheet ready") if sheets_ok else st.warning(sheets_msg)

        with st.expander("Advanced costs/warnings"):
            mileage_cost = st.number_input("Mileage cost per mile", value=0.67, min_value=0.0, step=0.05)
            travel_hour_cost = st.number_input("Travel time cost per hour", value=15.0, min_value=0.0, step=1.0)
            min_profit = st.number_input("Minimum target profit per job", value=70.0, min_value=0.0, step=10.0)
            long_drive_miles = st.number_input("Bad route if drive leg exceeds miles", value=22.0, min_value=1.0, step=1.0)

    st.markdown("### 1) Upload weekly bookings")
    top1, top2 = st.columns([1.25, 1])
    with top1:
        bookings_file = st.file_uploader("BookingKoala/GHL bookings CSV", type=["csv"], key="bookings")
        st.caption("This is the main file you export every week from BookingKoala/GHL.")
    with top2:
        uploaded_by = st.text_input("Admin name / initials", value="Admin")
        st.caption(f"Planning window: {week_start.isoformat()} → {(week_start + timedelta(days=(7 * int(horizon_weeks)) - 1)).isoformat()}")
        if bookings_file is not None:
            uploaded_bookings_preview = core.read_uploaded_csv(bookings_file)
            st.success(f"Uploaded {len(uploaded_bookings_preview)} booking row(s) for this run.")
            if use_google_sheets_master and sheets_ok:
                if st.button("Save this CSV as Active Week for both admins", type="primary"):
                    active_bookings = core.stamp_active_bookings(uploaded_bookings_preview, week_start, horizon_weeks, include_weekends, uploaded_by)
                    meta = core.make_active_week_metadata(week_start, horizon_weeks, include_weekends, uploaded_by, len(active_bookings))
                    st.success(core.write_google_tab(sheet_id, core.MASTER_SHEET_TABS["bookings"], active_bookings))
                    st.success(core.write_google_tab(sheet_id, core.MASTER_SHEET_TABS["active_meta"], meta))
        elif use_google_sheets_master and sheets_ok:
            st.info("No CSV uploaded. The app will load the shared Active Week from Google Sheets.")
        else:
            st.warning("Upload a bookings CSV or connect Google Sheets Active Week.")

    with st.expander("Advanced CSV overrides", expanded=False):
        c1, c2, c3, c4, c5 = st.columns(5)
        with c1:
            cleaners_file = st.file_uploader("Cleaners CSV", type=["csv"], key="cleaners")
        with c2:
            crew_file = st.file_uploader("Crew rules CSV", type=["csv"], key="crews")
        with c3:
            exceptions_file = st.file_uploader("Day off CSV", type=["csv"], key="exceptions")
        with c4:
            area_file = st.file_uploader("Area memory CSV", type=["csv"], key="area")
        with c5:
            actuals_file = st.file_uploader("Actual time CSV", type=["csv"], key="actuals")

    try:
        files = {"cleaners": cleaners_file, "bookings": bookings_file, "crews": crew_file, "exceptions": exceptions_file, "area": area_file, "actuals": actuals_file}
        master_data, load_statuses = core.load_master_data(sheet_id, use_google_sheets_master, files)
        cleaners_raw = master_data["cleaners"]
        bookings_raw = master_data["bookings"]
        crews_raw = master_data["crews"]
        exceptions_raw = master_data["exceptions"]
        area_raw = master_data["area"]
        actuals_raw = master_data["actuals"]

        with st.expander("Data source status", expanded=False):
            for status in load_statuses:
                st.write("- " + status)

        if bookings_raw is None or bookings_raw.empty:
            st.warning("No bookings found. Upload a BookingKoala/GHL CSV or save/load Active Week from Google Sheets.")
            return

        render_quick_team_builder(cleaners_raw)
        crews_raw = append_temporary_crews(crews_raw)

        cleaners = core.prepare_cleaners(cleaners_raw)
        crews = core.prepare_crew_rules(crews_raw)
        resources, resource_lookup = core.build_resources(cleaners, crews)
        resource_names = list(resources["resource"].astype(str)) if not resources.empty else []
        bookings_raw = render_assignment_controls(bookings_raw, resource_names)

        bookings = core.prepare_bookings(bookings_raw)
        actuals = core.prepare_actuals(actuals_raw)
        bookings, time_learning = core.apply_actual_time_learning(bookings, actuals)
        exceptions = core.prepare_availability_exceptions(exceptions_raw)
        area_memory = core.prepare_area_memory(area_raw)
        dates = core.week_dates(week_start, horizon_weeks, include_weekends)
        booking_instances = core.expand_recurring_bookings(bookings, dates)

        if booking_instances.empty:
            st.warning("No bookings found inside this planning horizon.")
            return

        with st.spinner("Optimizing routes by cleaner/team and location..."):
            points, point_idx, points_df = core.make_points(resources, booking_instances, api_key, use_google)
            miles_matrix, minutes_matrix, route_source = core.compute_route_matrix(points, api_key, use_google, routing_provider)
            schedule, unassigned, alerts, schedules_dict, member_events = core.optimize_schedule(
                booking_instances, resources, dates, exceptions, miles_matrix, minutes_matrix, point_idx,
                area_memory, hours_are_person_hours, mileage_cost, travel_hour_cost,
            )
            price_suggestions = core.price_adjustment_suggestions(schedule, min_profit, long_drive_miles)
            move_messages = core.build_move_message_suggestions(schedule, price_suggestions)

        if "fallback" in str(route_source).lower() or "approx" in str(route_source).lower():
            st.warning(f"Routing source: {route_source}. The schedule still works, but miles are approximate until Google routing is fixed.")
        else:
            st.success(f"Optimization completed using {route_source}.")

        total_miles = float(schedule["travel_miles"].astype(float).sum()) if not schedule.empty and "travel_miles" in schedule else 0.0
        used_resources = int(schedule["resource"].nunique()) if not schedule.empty and "resource" in schedule else 0
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scheduled jobs", len(schedule))
        m2.metric("Unassigned", len(unassigned))
        m3.metric("Cleaner/team routes", used_resources)
        m4.metric("Drive miles before jobs", f"{total_miles:.1f}")

        tab1, tab2, tab3, tab4 = st.tabs(["Weekly schedule", "New booking checker", "Cleaners & teams", "Export"])

        with tab1:
            reviewed_schedule = render_weekly_schedule(schedule, unassigned, alerts, price_suggestions, time_learning)
            approved_schedule, rejected_schedule, needs_review_schedule = core.split_reviewed_schedule(reviewed_schedule)

        with tab2:
            render_new_booking_checker(resources, dates, schedule, booking_instances, schedules_dict, member_events, exceptions, area_memory, points, point_idx, api_key, use_google, routing_provider, hours_are_person_hours, mileage_cost, travel_hour_cost)

        with tab3:
            st.subheader("Cleaners & teams being used by the optimizer")
            st.caption("These are the solo cleaners and crew resources the app can assign. Update permanent cleaners/crews in Google Sheets; use the team builder above for temporary weekly teams.")
            core.display_df("Available cleaner/team resources", resources.drop(columns=["member_keys"], errors="ignore"))
            core.display_df("Cleaner master list", cleaners.drop(columns=["cleaner_key", "available_day_list", "allow_solo_bool"], errors="ignore"))
            core.display_df("Crew rules", crews.drop(columns=["resource_key", "member_keys", "member_list", "available_day_list", "can_split_bool", "always_together_bool", "carpool_bool"], errors="ignore"))

        with tab4:
            # Re-read review state from session by rerendering helper? The Weekly tab sets local variables only when opened.
            # If tab4 is opened first, default all approved.
            if "reviewed_schedule" not in locals() or reviewed_schedule is None or reviewed_schedule.empty:
                reviewed_schedule = core.add_manager_review_columns(schedule)
            approved_schedule, rejected_schedule, needs_review_schedule = core.split_reviewed_schedule(reviewed_schedule)
            render_export_tab(approved_schedule, reviewed_schedule, schedule, unassigned, alerts, price_suggestions, move_messages, time_learning, actuals, resources, points_df, bookings_raw, sheet_id, use_google_sheets_master)

    except Exception as exc:
        st.error(f"Could not optimize schedule: {exc}")
        st.exception(exc)


if __name__ == "__main__":
    main()
