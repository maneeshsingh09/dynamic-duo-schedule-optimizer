from __future__ import annotations

from datetime import date, timedelta
from itertools import combinations
from typing import Any, Dict, List, Tuple

import pandas as pd
import streamlit as st

import optimizer_core as core

APP_TITLE = "Dynamic Duo Cleaning - Schedule Planner v19"


def safe_cols(df: pd.DataFrame, cols: List[str]) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    return df[[c for c in cols if c in df.columns]].copy()


def clean_money(x: Any) -> str:
    try:
        if pd.isna(x) or str(x) == "":
            return "-"
        return f"${float(x):,.0f}"
    except Exception:
        return str(x)


def css() -> None:
    st.markdown(
        """
        <style>
        .dd-card {border:1px solid #e7e7e7;border-radius:18px;padding:14px 16px;margin:8px 0;background:#ffffff;box-shadow:0 1px 4px rgba(0,0,0,.04)}
        .dd-day {border:1px solid #dedede;border-radius:20px;padding:14px;margin:6px 0;background:#fbfbfb;min-height:210px}
        .dd-day-title {font-size:17px;font-weight:700;margin-bottom:4px}
        .dd-meta {font-size:13px;color:#666;margin-bottom:8px}
        .dd-route {border-left:5px solid #dedede;border-radius:12px;background:#fff;padding:10px 12px;margin:10px 0}
        .dd-resource {font-weight:700;font-size:15px;margin-bottom:4px}
        .dd-job {font-size:13px;line-height:1.35;margin:5px 0;padding-bottom:5px;border-bottom:1px dashed #eee}
        .dd-pill {display:inline-block;border-radius:999px;padding:2px 8px;background:#f2f2f2;font-size:12px;margin:2px 4px 2px 0}
        .dd-best {border:1px solid #d7eadb;background:#f6fff7;border-radius:18px;padding:14px;margin:8px 0}
        .dd-warn {border:1px solid #ffe2a3;background:#fffaf0;border-radius:14px;padding:12px;margin:8px 0}
        .block-container {padding-top: 2rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )


def normalize_bookings_for_editor(raw: pd.DataFrame) -> pd.DataFrame:
    df = core.normalize_columns(raw.copy(), core.BOOKING_ALIASES) if raw is not None and not raw.empty else pd.DataFrame()
    if df.empty:
        return df
    df = df.reset_index(drop=True)
    df["booking_row_id"] = range(len(df))
    defaults: Dict[str, Any] = {
        "client": "", "address": "", "preferred_resource": "", "lock_resource": "No",
        "min_workers": 1, "max_workers": 4, "requires_team": "No", "priority": "Normal",
        "job_hours": 2.5, "job_price": "", "time_window": "Flexible", "appointment_duration": "", "original_worker_count": "", "assigned_workers": "",
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


def build_pair_rows(cleaners_raw: pd.DataFrame, chosen_pair_labels: List[str], days: List[str]) -> pd.DataFrame:
    if cleaners_raw is None or cleaners_raw.empty or not chosen_pair_labels:
        return pd.DataFrame()
    cleaners = core.prepare_cleaners(cleaners_raw)
    by_name = {str(r["cleaner"]): r for _, r in cleaners.iterrows()}
    rows = []
    for label in chosen_pair_labels:
        if " + " not in label:
            continue
        a, b = [x.strip() for x in label.split(" + ", 1)]
        if a not in by_name or b not in by_name:
            continue
        hourly = float(by_name[a].get("hourly_cost", 25)) + float(by_name[b].get("hourly_cost", 25))
        rows.append({
            "resource_name": f"{a}/{b}",
            "members": f"{a};{b}",
            "team_type": "Optional",
            "available_days": ",".join(days),
            "base_address": str(by_name[a].get("base_address", "")),
            "can_split_after_job": "Yes",
            "always_together": "No",
            "carpool": "Yes",
            "max_jobs_per_day": 4,
            "max_hours_per_day": 8,
            "start_time": core.DEFAULT_START,
            "end_time": core.DEFAULT_END,
            "productivity_multiplier": 2,
            "hourly_cost_override": hourly,
        })
    return pd.DataFrame(rows)


def render_team_setup(cleaners_raw: pd.DataFrame, crews_raw: pd.DataFrame) -> pd.DataFrame:
    st.subheader("Cleaner/team setup for this week")
    st.caption("Use this only when cleaners will work together. The optimizer can still split them into solo jobs later if the team is optional.")
    try:
        cleaners_preview = core.prepare_cleaners(cleaners_raw)
        cleaner_names = list(cleaners_preview["cleaner"].astype(str))
    except Exception:
        cleaner_names = []

    with st.container(border=True):
        c1, c2, c3 = st.columns([1.5, 2.0, 1.2])
        with c1:
            team_name = st.text_input("Manual team name", placeholder="Example: Billy/Eduardo")
        with c2:
            members = st.multiselect("Cleaners together", cleaner_names, key="manual_team_members")
        with c3:
            team_type = st.selectbox("Team type", ["Optional", "Fixed"], help="Fixed = always together. Optional = can split later if needed.")
        days = st.multiselect("Days this team can work", [d.title() for d in core.WEEKDAY_ORDER[:5]], default=[d.title() for d in core.WEEKDAY_ORDER[:5]], key="manual_team_days")
        speed = st.slider("Team speed factor", 1.0, 4.0, value=float(max(1, len(members))), step=0.25, help="2 cleaners usually finish a 4-hour one-person job in about 2 hours.")
        if st.button("Add this team for current run", disabled=len(members) < 2):
            final_name = team_name.strip() or "/".join(members)
            st.session_state.setdefault("temp_crews_v19", []).append({
                "resource_name": final_name, "members": ";".join(members), "team_type": team_type,
                "available_days": ",".join(days), "base_address": "", "can_split_after_job": "No" if team_type == "Fixed" else "Yes",
                "always_together": "Yes" if team_type == "Fixed" else "No", "carpool": "Yes", "max_jobs_per_day": 4,
                "max_hours_per_day": 8, "start_time": core.DEFAULT_START, "end_time": core.DEFAULT_END,
                "productivity_multiplier": speed, "hourly_cost_override": "",
            })
            st.rerun()

    pair_rows = pd.DataFrame()
    with st.expander("Optional: allow automatic temporary pairs for large jobs", expanded=False):
        st.caption("This lets the app consider pairs for larger cleanings while still allowing those same cleaners to work solo at other times.")
        all_pairs = [f"{a} + {b}" for a, b in combinations(cleaner_names, 2)]
        common_default = [p for p in all_pairs if p in {"Billy + Eduardo", "Isabel + Jacky"}]
        chosen_pairs = st.multiselect("Allowed temporary pairs", all_pairs, default=common_default[:3])
        pair_days = st.multiselect("Pair days", [d.title() for d in core.WEEKDAY_ORDER[:5]], default=[d.title() for d in core.WEEKDAY_ORDER[:5]], key="pair_days")
        pair_rows = build_pair_rows(cleaners_raw, chosen_pairs, pair_days)
        if not pair_rows.empty:
            st.dataframe(pair_rows[["resource_name", "members", "team_type", "available_days", "productivity_multiplier"]], use_container_width=True, hide_index=True)

    pieces = []
    if crews_raw is not None and not crews_raw.empty:
        pieces.append(crews_raw)
    if st.session_state.get("temp_crews_v19"):
        pieces.append(pd.DataFrame(st.session_state["temp_crews_v19"]))
    if not pair_rows.empty:
        pieces.append(pair_rows)
    if not pieces:
        return crews_raw
    combined = pd.concat(pieces, ignore_index=True)
    with st.expander("Current teams available to the optimizer", expanded=False):
        st.dataframe(safe_cols(combined, ["resource_name", "members", "team_type", "available_days", "productivity_multiplier", "always_together"]), use_container_width=True, hide_index=True)
        if st.button("Clear manual temporary teams"):
            st.session_state["temp_crews_v19"] = []
            st.rerun()
    return combined


def apply_large_job_rules(bookings_raw: pd.DataFrame, threshold: float, mode: str) -> pd.DataFrame:
    if bookings_raw is None or bookings_raw.empty:
        return bookings_raw
    out = normalize_bookings_for_editor(bookings_raw)
    hrs = out["job_hours"].apply(lambda x: core.parse_float(x, 0.0))
    big = hrs >= float(threshold)
    if mode == "Let app choose solo or team for big jobs":
        out.loc[big, "max_workers"] = out.loc[big, "max_workers"].apply(lambda x: max(int(core.parse_float(x, 1)), 4))
        out.loc[big, "requires_team"] = out.loc[big, "requires_team"].replace("", "No")
    elif mode == "Prefer/require team for big jobs":
        out.loc[big, "min_workers"] = 2
        out.loc[big, "max_workers"] = 4
        out.loc[big, "requires_team"] = "Yes"
    return out


def render_job_controls(bookings_raw: pd.DataFrame, resource_names: List[str]) -> pd.DataFrame:
    st.subheader("Optional job controls")
    st.caption("Leave this alone unless you need to force a team, lock a priority client, or correct hours/price before optimizing.")
    norm = normalize_bookings_for_editor(bookings_raw)
    view = safe_cols(norm, ["booking_row_id", "client", "address", "appointment_duration", "original_worker_count", "assigned_workers", "preferred_resource", "lock_resource", "min_workers", "max_workers", "requires_team", "priority", "job_hours", "job_price", "time_window"])
    with st.expander("Adjust specific jobs", expanded=False):
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
            key="job_controls_v19",
        )
    return apply_booking_editor(bookings_raw, edited)


def route_card_html(resource: str, r: pd.DataFrame) -> str:
    miles = float(r["travel_miles"].astype(float).sum()) if "travel_miles" in r else 0.0
    hours = float(r["duration_hours"].astype(float).sum()) if "duration_hours" in r else 0.0
    members = str(r["members"].iloc[0]) if "members" in r.columns and not r.empty else ""
    jobs_html = ""
    for _, row in r.sort_values("start_min").iterrows():
        jobs_html += (
            f"<div class='dd-job'><b>{row.get('start','')}–{row.get('end','')}</b> · "
            f"{row.get('client','')}<br><span style='color:#666'>{row.get('city','')} · {row.get('cleaning_type','')} · "
            f"{row.get('duration_hours','')} hrs · {row.get('travel_miles','')} job-to-job mi · gap {row.get('gap_after_prev_mins',0)} min</span></div>"
        )
    return (
        f"<div class='dd-route'><div class='dd-resource'>{resource}</div>"
        f"<div class='dd-meta'>{members if members and members != resource else ''}</div>"
        f"<span class='dd-pill'>{len(r)} jobs</span><span class='dd-pill'>{miles:.1f} mi</span><span class='dd-pill'>{hours:.1f} work hrs</span>"
        f"{jobs_html}</div>"
    )


def render_calendar_overview(schedule: pd.DataFrame, dates: List[date]) -> None:
    st.subheader("Weekly schedule calendar")
    st.caption("Each day shows cleaner/team routes, job order, counted job-to-job miles, and work hours. Home→first job and last job→home are intentionally excluded.")
    if schedule is None or schedule.empty:
        st.info("No scheduled jobs yet.")
        return
    dates_to_show = [d for d in dates if str(d) in set(schedule["date"].astype(str))]
    if not dates_to_show:
        dates_to_show = sorted([pd.to_datetime(str(x)).date() for x in schedule["date"].unique()])
    for i in range(0, len(dates_to_show), 5):
        row_dates = dates_to_show[i:i+5]
        cols = st.columns(len(row_dates))
        for col, d in zip(cols, row_dates):
            day_df = schedule[schedule["date"].astype(str) == str(d)].copy()
            label = pd.to_datetime(str(d)).strftime("%a, %b %d")
            total_miles = float(day_df["travel_miles"].astype(float).sum()) if not day_df.empty and "travel_miles" in day_df else 0.0
            with col:
                html = f"<div class='dd-day'><div class='dd-day-title'>{label}</div><div class='dd-meta'>{len(day_df)} jobs · {day_df['resource'].nunique() if not day_df.empty else 0} routes · {total_miles:.1f} job-to-job mi</div>"
                for resource in day_df.sort_values(["resource", "start_min"])["resource"].dropna().astype(str).unique():
                    r = day_df[day_df["resource"].astype(str) == resource]
                    html += route_card_html(resource, r)
                html += "</div>"
                st.markdown(html, unsafe_allow_html=True)


def render_approval_calendar(schedule: pd.DataFrame, dates: List[date]) -> pd.DataFrame:
    st.subheader("Approve or adjust final schedule")
    st.caption("Approve day by day. Use Lock when you are sure that route should not be changed.")
    reviewed = core.add_manager_review_columns(schedule)
    edited_parts = []
    for d in dates:
        day_df = reviewed[reviewed["date"].astype(str) == str(d)].copy()
        if day_df.empty:
            continue
        day_label = pd.to_datetime(str(d)).strftime("%A, %b %d")
        total_miles = float(day_df["travel_miles"].astype(float).sum()) if "travel_miles" in day_df else 0.0
        with st.expander(f"{day_label} — approve {len(day_df)} jobs · {total_miles:.1f} job-to-job miles", expanded=False):
            display_cols = ["manager_status", "lock_assignment", "manager_note", "resource", "start", "end", "client", "city", "duration_hours", "bookingkoala_duration_mins", "bookingkoala_worker_count", "original_person_hours", "gap_after_prev_mins", "travel_miles", "positioning_miles_not_counted", "profit_score", "duration_source", "instance_id"]
            cols = [c for c in display_cols if c in day_df.columns]
            edited = st.data_editor(
                day_df[cols],
                use_container_width=True,
                hide_index=True,
                disabled=[c for c in cols if c not in {"manager_status", "lock_assignment", "manager_note"}],
                column_config={
                    "manager_status": st.column_config.SelectboxColumn("Status", options=["Approve", "Lock", "Needs Review", "Reject"], required=True),
                    "lock_assignment": st.column_config.CheckboxColumn("Lock"),
                    "manager_note": st.column_config.TextColumn("Manager note"),
                },
                key=f"review_day_{str(d)}",
            )
            edited_parts.append(edited)
    if edited_parts:
        edited_all = pd.concat(edited_parts, ignore_index=True)
        reviewed_schedule = core.apply_review_status_to_schedule(schedule, edited_all)
    else:
        reviewed_schedule = reviewed
    approved, rejected, needs_review = core.split_reviewed_schedule(reviewed_schedule)
    c1, c2, c3 = st.columns(3)
    c1.metric("Approved/locked", len(approved))
    c2.metric("Needs review", len(needs_review))
    c3.metric("Rejected", len(rejected))
    return reviewed_schedule


def render_problem_summary(unassigned: pd.DataFrame, alerts: pd.DataFrame, price_suggestions: pd.DataFrame, time_learning: pd.DataFrame) -> None:
    issues = len(unassigned) + len(price_suggestions)
    if issues == 0 and (alerts is None or alerts.empty):
        st.success("No major scheduling problems found.")
        return
    with st.expander("Problem jobs / price warnings", expanded=False):
        core.display_df("Unassigned jobs", unassigned)
        core.display_df("Route warnings", safe_cols(alerts, ["date", "resource", "client", "type", "severity", "message", "advice"]))
        core.display_df("Price suggestions", safe_cols(price_suggestions, ["client", "resource", "date", "suggestion", "reason", "profit_score", "travel_miles", "positioning_miles_not_counted"]))
        core.display_df("Actual time learning applied", time_learning)



def render_team_assist_suggestions(assist_df: pd.DataFrame) -> None:
    st.subheader("Smart team assist suggestions")
    st.caption("These are advisory only. They show where a cleaner who finishes nearby could join a larger job later. Approve manually before changing BookingKoala.")
    if assist_df is None or assist_df.empty:
        st.info("No strong helper-join options found for this schedule. The current solo/team routes are likely cleaner than forced split-join coordination.")
        return
    with st.expander(f"Review {len(assist_df)} possible helper-join options", expanded=True):
        for _, r in assist_df.head(10).iterrows():
            st.markdown(
                f"""
                <div class='dd-best'>
                <div class='dd-day-title'>{pd.to_datetime(str(r.get('date'))).strftime('%a, %b %d')} · {r.get('helper')} can assist {r.get('client')}</div>
                <div class='dd-meta'>Current: {r.get('current_resource')} · Helper after: {r.get('helper_after_job')}</div>
                <span class='dd-pill'>Join around {r.get('helper_arrival')}</span>
                <span class='dd-pill'>Finish {r.get('estimated_finish_with_helper')} vs {r.get('original_finish')}</span>
                <span class='dd-pill'>Save ~{r.get('time_saved_minutes')} min</span>
                <span class='dd-pill'>{r.get('helper_drive_miles')} mi helper drive</span>
                <div style='font-size:13px;margin-top:8px;color:#555'>{r.get('recommendation')} · {r.get('next_job_check')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
        st.dataframe(assist_df, use_container_width=True, hide_index=True)


def build_live_booking_row_from_option(input_row: Dict[str, Any], option: Dict[str, Any], status: str, admin_name: str) -> pd.DataFrame:
    """Create a normalized live-booking row that the optimizer can read later."""
    hold_id_src = f"{input_row.get('client','')}-{input_row.get('address','')}-{option.get('date','')}-{option.get('resource','')}-{option.get('start','')}-{pd.Timestamp.now().isoformat()}"
    hold_id = core.hashlib.md5(hold_id_src.encode("utf-8")).hexdigest()[:10] if hasattr(core, "hashlib") else str(pd.Timestamp.now().value)[-10:]
    assigned_workers = str(option.get("members") or option.get("resource") or "")
    workers = int(option.get("assigned_worker_count", input_row.get("min_workers", 1)) or 1)
    row = {
        "booking_source": "Live Booking Planner",
        "booking_status": status,
        "hold_id": hold_id,
        "created_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "created_by": admin_name or "Admin",
        "client": input_row.get("client", ""),
        "address": input_row.get("address", ""),
        "service_date": str(option.get("date", "")),
        "preferred_day": str(option.get("day", "")),
        "flexible_days": input_row.get("flexible_days", ""),
        "time_window": "Fixed",
        "earliest_start": str(option.get("start", core.DEFAULT_START)),
        "latest_finish": str(option.get("end", core.DEFAULT_END)),
        "job_hours": input_row.get("job_hours", 2.5),
        "cleaning_type": input_row.get("cleaning_type", "Standard"),
        "can_shift": "No" if status in {"Confirmed", "Pending Hold"} else "Yes",
        "frequency": input_row.get("frequency", "One time"),
        "recurrence_interval_weeks": input_row.get("recurrence_interval_weeks", ""),
        "job_price": input_row.get("job_price", ""),
        "preferred_resource": str(option.get("resource", "")),
        "lock_resource": "Yes",
        "priority": input_row.get("priority", "Normal"),
        "risk_level": input_row.get("risk_level", "Low"),
        "min_workers": workers,
        "max_workers": workers,
        "requires_team": "Yes" if workers >= 2 else "No",
        "assigned_workers": assigned_workers,
        "original_worker_count": workers,
        "notes": input_row.get("notes", "") + f" | chosen by optimizer: {option.get('why_this_option', option.get('score_notes',''))}",
    }
    return pd.DataFrame([row])


def render_live_booking_planner(resources: pd.DataFrame, dates: List[date], schedule: pd.DataFrame, booking_instances: pd.DataFrame, schedules_dict: Dict, member_events: Dict, exceptions: pd.DataFrame, area_memory: pd.DataFrame, live_bookings_raw: pd.DataFrame, sheet_id: str, use_google_sheets_master: bool, admin_name: str, api_key: str, use_google: bool, routing_provider: str, hours_are_person_hours: bool, mileage_cost: float, travel_hour_cost: float, min_gap_minutes: int) -> None:
    st.subheader("Live Booking Planner")
    st.caption("Use this before confirming a new lead. It checks the shared future schedule, pending holds, routes, teams, travel, and recurring pattern before telling you where to place the booking.")

    if live_bookings_raw is not None and not live_bookings_raw.empty:
        with st.expander(f"Current live holds / confirmed additions ({len(live_bookings_raw)} rows)", expanded=False):
            view_cols = ["booking_status", "client", "address", "service_date", "earliest_start", "preferred_resource", "job_hours", "job_price", "created_by"]
            st.dataframe(safe_cols(live_bookings_raw, view_cols), use_container_width=True, hide_index=True)

    with st.container(border=True):
        st.markdown("#### New lead details")
        c1, c2, c3, c4 = st.columns([1.2, 2.0, 1.0, 1.0])
        with c1:
            nb_client = st.text_input("Client", value="New Lead")
        with c2:
            nb_address = st.text_input("Address / city", value="Lakeville, MN")
        with c3:
            nb_hours = st.number_input("Total one-person labor hours", value=3.0, min_value=0.5, step=0.25, help="Example: if two cleaners would take 2 hours, enter about 4 one-person hours.")
        with c4:
            nb_price = st.number_input("Quoted price", value=240.0, min_value=0.0, step=10.0)
        c5, c6, c7, c8 = st.columns(4)
        with c5:
            nb_type = st.selectbox("Type", ["Standard", "Deep Cleaning", "Move Out", "Airbnb"])
        with c6:
            nb_days = st.text_input("Possible days", value="Monday,Tuesday,Wednesday,Thursday,Friday", help="Days client can accept. The app ranks the best option.")
        with c7:
            nb_window = st.selectbox("Client time flexibility", ["Flexible", "Morning", "Afternoon", "Fixed"])
        with c8:
            worker_pref = st.selectbox("Cleaner setup", ["App chooses solo/team", "Must be team", "Solo only"])
        c9, c10, c11, c12 = st.columns(4)
        with c9:
            frequency = st.selectbox("Frequency", ["One time", "Weekly", "Biweekly", "Every 4 weeks", "Monthly"])
        with c10:
            priority = st.selectbox("Priority", ["Normal", "High", "VIP", "Low"])
        with c11:
            risk = st.selectbox("Risk / difficulty", ["Low", "Medium", "High"] if nb_type != "Deep Cleaning" else ["Medium", "High", "Low"])
        with c12:
            save_status = st.selectbox("When saved", ["Pending Hold", "Confirmed"])
        notes = st.text_area("Notes for this lead", value="", height=70)

    if worker_pref == "Must be team":
        min_workers, max_workers, requires_team = 2, 4, "Yes"
    elif worker_pref == "Solo only":
        min_workers, max_workers, requires_team = 1, 1, "No"
    else:
        min_workers, max_workers, requires_team = 1, 4, "No"

    interval = ""
    if frequency == "Weekly":
        interval = "1"
    elif frequency == "Biweekly":
        interval = "2"
    elif frequency == "Every 4 weeks" or frequency == "Monthly":
        interval = "4"

    input_row = {
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
        "frequency": frequency,
        "recurrence_interval_weeks": interval,
        "job_price": nb_price,
        "preferred_resource": "",
        "lock_resource": "No",
        "priority": priority,
        "risk_level": risk,
        "min_workers": min_workers,
        "max_workers": max_workers,
        "requires_team": requires_team,
        "notes": notes,
    }

    run_key = "live_booking_suggestions_v17"
    input_key = "live_booking_input_v17"
    if st.button("Find best day/time/team", type="primary"):
        new_booking = core.prepare_bookings(pd.DataFrame([input_row]))
        temp_instances = core.expand_recurring_bookings(new_booking, dates)
        all_instances = pd.concat([booking_instances, temp_instances], ignore_index=True)
        pts2, idx2, _ = core.make_points(resources, all_instances, api_key, use_google)
        m2, t2, _ = core.compute_route_matrix(pts2, api_key, use_google, routing_provider)
        suggestions: List[Dict[str, Any]] = []
        for _, row in temp_instances.iterrows():
            # For recurring jobs, evaluate each generated occurrence. The first occurrence is used for the hold,
            # and the score includes warnings if later occurrences look harder.
            for d in core.candidate_dates_for_booking(row, dates):
                for _, res in resources.iterrows():
                    ok, cand = core.evaluate_candidate(row, res.to_dict(), d, schedules_dict, member_events, exceptions, m2, t2, idx2, area_memory, hours_are_person_hours, mileage_cost, travel_hour_cost, min_gap_minutes)
                    if ok:
                        suggestions.append(cand)
        sug_df = pd.DataFrame(suggestions).sort_values("score").head(20) if suggestions else pd.DataFrame()
        if not sug_df.empty:
            def option_reason(opt: pd.Series) -> str:
                workers = int(opt.get("assigned_worker_count", 1) or 1)
                reasons = []
                if workers >= 2 and nb_hours >= 4:
                    reasons.append("team makes sense because this is a larger job")
                elif workers == 1 and nb_hours <= 3:
                    reasons.append("solo is enough for this smaller job")
                if float(opt.get("travel_miles", 0) or 0) <= 8:
                    reasons.append("low added drive from existing route")
                if str(opt.get("score_notes", "")).strip():
                    reasons.append(str(opt.get("score_notes")))
                if frequency != "One time":
                    reasons.append(f"checks {frequency.lower()} pattern inside the selected horizon")
                return "; ".join(reasons) or "best balance of time, route, and cleaner availability"
            sug_df["why_this_option"] = sug_df.apply(option_reason, axis=1)
        st.session_state[run_key] = sug_df
        st.session_state[input_key] = input_row

    sug_df = st.session_state.get(run_key, pd.DataFrame())
    if sug_df is None or sug_df.empty:
        st.info("Enter lead details and click Find best day/time/team. The planner will rank options across the selected planning horizon.")
        return

    st.markdown("### Recommended booking slots")
    top = sug_df.head(6).copy()
    cols = st.columns(3)
    for i, (_, opt) in enumerate(top.head(3).iterrows()):
        with cols[i % 3]:
            label = "Best overall" if i == 0 else f"Option {i+1}"
            st.markdown(
                f"""
                <div class='dd-best'>
                <div class='dd-day-title'>{label}: {opt['day']} · {opt['resource']}</div>
                <div class='dd-meta'>{opt['start']}–{opt['end']} · {opt.get('city','')}</div>
                <span class='dd-pill'>{opt['travel_miles']} job-to-job mi added</span>
                <span class='dd-pill'>{opt['duration_hours']} work hrs</span>
                <span class='dd-pill'>{int(opt.get('assigned_worker_count', 1) or 1)} cleaner(s)</span>
                <span class='dd-pill'>Profit {clean_money(opt.get('profit_score',''))}</span>
                <div style='font-size:13px;margin-top:8px;color:#555'>{opt.get('why_this_option','')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )
    st.dataframe(safe_cols(sug_df, ["date", "day", "resource", "members", "team_decision", "assigned_worker_count", "start", "end", "duration_hours", "travel_miles", "profit_score", "why_this_option"]), use_container_width=True, hide_index=True)

    labels = []
    for idx, r in sug_df.head(12).reset_index(drop=True).iterrows():
        labels.append(f"#{idx+1} {r.get('day')} {r.get('start')}–{r.get('end')} · {r.get('resource')} · {r.get('travel_miles')} job-to-job mi")
    selected_label = st.selectbox("Choose slot to save/hold", labels)
    selected_index = labels.index(selected_label)
    selected = sug_df.head(12).reset_index(drop=True).iloc[selected_index].to_dict()


    msg = f"Hi {nb_client if nb_client != 'New Lead' else ''}, we have availability {selected['day']} around {selected['start']} and already have a cleaner/team nearby that day. Would that work for your cleaning?".replace("Hi ,", "Hi,")
    st.text_area("Copy/paste client message", msg, height=90)

    csave1, csave2 = st.columns([1, 1])
    with csave1:
        if st.button(f"Save selected slot as {save_status}", type="primary"):
            live_row = build_live_booking_row_from_option(st.session_state.get(input_key, input_row), selected, save_status, admin_name)
            if use_google_sheets_master and core.google_sheets_configured(sheet_id)[0]:
                st.success(core.append_google_tab(sheet_id, core.MASTER_SHEET_TABS["live_bookings"], live_row))
                decision = core.build_booking_decision_row(st.session_state.get(input_key, input_row), selected, sug_df, save_status, admin_name)
                st.success(core.append_google_tab(sheet_id, core.MASTER_SHEET_TABS["booking_decisions"], decision))
                st.info("Saved. Refresh/rerun the app so this hold is included in the next schedule and future booking suggestions.")
            else:
                st.session_state.setdefault("local_live_bookings_v17", []).append(live_row.iloc[0].to_dict())
                st.success("Saved locally for this browser session. Connect Google Sheets to share it with the other admin.")
    with csave2:
        if st.button("Clear planner suggestions"):
            st.session_state.pop(run_key, None)
            st.session_state.pop(input_key, None)
            st.rerun()

def render_export(approved_schedule: pd.DataFrame, reviewed_schedule: pd.DataFrame, schedule: pd.DataFrame, unassigned: pd.DataFrame, alerts: pd.DataFrame, price_suggestions: pd.DataFrame, move_messages: pd.DataFrame, assist_suggestions: pd.DataFrame, time_learning: pd.DataFrame, actuals: pd.DataFrame, resources: pd.DataFrame, points_df: pd.DataFrame, bookings_raw: pd.DataFrame, live_bookings_raw: pd.DataFrame, sheet_id: str, use_google_sheets_master: bool) -> None:
    st.subheader("Export + cleaner messages")
    approved_texts = core.build_daily_text(approved_schedule)
    if not approved_texts.empty:
        with st.expander("Cleaner daily text messages", expanded=True):
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
        "Team Assist Suggestions": assist_suggestions,
        "Actual Time Learning": time_learning,
        "Actuals Uploaded": actuals,
        "Cleaner Texts": approved_texts,
        "Resources": resources.drop(columns=["member_keys"], errors="ignore"),
        "Points": points_df,
        "Active Bookings Source": bookings_raw,
        "Live Bookings": live_bookings_raw,
    }
    excel = core.to_excel(sheets)
    st.download_button("Download Excel report", data=excel, file_name="dynamic_duo_schedule_report_v17.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    if not approved_schedule.empty:
        st.download_button("Download approved schedule CSV", data=approved_schedule.to_csv(index=False), file_name="approved_schedule_v17.csv", mime="text/csv")
    if use_google_sheets_master and core.google_sheets_configured(sheet_id)[0]:
        c1, c2 = st.columns(2)
        with c1:
            if st.button("Save approved schedule to shared Sheet"):
                st.success(core.write_google_tab(sheet_id, core.MASTER_SHEET_TABS["approved"], approved_schedule))
        with c2:
            if st.button("Save reviewed schedule to shared Sheet"):
                st.success(core.write_google_tab(sheet_id, core.MASTER_SHEET_TABS["reviewed"], reviewed_schedule))


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    css()
    st.title("Dynamic Duo Schedule Planner")
    st.caption("Live workflow: upload future BookingKoala schedule → app keeps a shared future plan → new bookings are placed into the best day/time before they create weekend chaos.")

    with st.sidebar:
        st.header("Setup")
        week_start = st.date_input("Week start", value=date.today())
        horizon_weeks = st.slider("Planning horizon", 1, 4, 2)
        include_weekends = st.checkbox("Include weekends", value=False)
        hours_are_person_hours = st.checkbox("Manual job_hours values are one-person hours", value=True, help="BookingKoala appointment durations are auto-converted using cleaner count. This only affects manually entered job_hours.")
        min_gap_minutes = st.number_input("Minimum gap between cleanings", value=30, min_value=0, max_value=120, step=5, help="Adds rest/travel buffer after each job before the next job on the same route.")
        with st.expander("Smart team logic", expanded=False):
            enable_smart_pairs = st.checkbox("Let app compare solo vs temporary pairs", value=True)
            smart_pair_limit = st.slider("Maximum smart temporary pairs", 0, 20, 12)
            st.caption("This lets the optimizer compare solo routes vs two-cleaner options for larger jobs. Helper-join suggestions are still shown for manual approval.")

        st.divider()
        st.header("Shared data")
        sheet_id_default = core.get_google_sheet_id()
        sheet_id = st.text_input("Google Sheet ID", value=sheet_id_default, type="password")
        sheets_ok, sheets_msg = core.google_sheets_configured(sheet_id)
        use_google_sheets_master = st.checkbox("Use shared Google Sheet", value=sheets_ok)
        if use_google_sheets_master:
            st.success("Google Sheet ready") if sheets_ok else st.warning(sheets_msg)

        with st.expander("Google routing", expanded=False):
            api_key_default = core.get_secret_value("GOOGLE_MAPS_API_KEY")
            api_key = st.text_input("Google Maps API key", value=api_key_default, type="password")
            use_google = st.checkbox("Use Google driving routes", value=False, help="Leave off until API key restrictions are fixed. The app still works with approximate miles.")
            routing_provider = st.selectbox("Provider", ["Approximate only", "Auto", "Routes API", "Distance Matrix API (Legacy)"], index=0)
            if routing_provider == "Approximate only":
                use_google = False
            st.caption("If Google says API_KEY_SERVICE_BLOCKED or REQUEST_DENIED, create/fix a server-side Google Maps key that allows Routes API and/or Distance Matrix API.")

        with st.expander("Costs / warnings", expanded=False):
            mileage_cost = st.number_input("Mileage cost per mile", value=0.67, min_value=0.0, step=0.05)
            travel_hour_cost = st.number_input("Travel time cost per hour", value=15.0, min_value=0.0, step=1.0)
            min_profit = st.number_input("Minimum target profit per job", value=70.0, min_value=0.0, step=10.0)
            long_drive_miles = st.number_input("Bad route if drive leg exceeds miles", value=22.0, min_value=1.0, step=1.0)

    st.markdown("### Upload weekly schedule")
    u1, u2, u3 = st.columns([1.4, 1.1, 1.0])
    with u1:
        bookings_file = st.file_uploader("BookingKoala/GHL bookings CSV", type=["csv"], key="bookings")
        st.caption("Upload once, then save as the shared Future Schedule so both admins and the Live Booking Planner use the same data.")
    with u2:
        uploaded_by = st.text_input("Admin name / initials", value="Admin")
        st.caption(f"Planning window: {week_start.isoformat()} → {(week_start + timedelta(days=(7 * int(horizon_weeks)) - 1)).isoformat()}")
    with u3:
        if bookings_file is not None:
            uploaded_preview = core.read_uploaded_csv(bookings_file)
            st.success(f"Uploaded {len(uploaded_preview)} booking row(s).")
            if use_google_sheets_master and sheets_ok:
                if st.button("Save as Future Schedule", type="primary"):
                    active_bookings = core.stamp_active_bookings(uploaded_preview, week_start, horizon_weeks, include_weekends, uploaded_by)
                    meta = core.make_active_week_metadata(week_start, horizon_weeks, include_weekends, uploaded_by, len(active_bookings))
                    st.success(core.write_google_tab(sheet_id, core.MASTER_SHEET_TABS["bookings"], active_bookings))
                    st.success(core.write_google_tab(sheet_id, core.MASTER_SHEET_TABS["active_meta"], meta))
        elif use_google_sheets_master and sheets_ok:
            st.info("No upload. Loading shared Future Schedule from Google Sheet.")
        else:
            st.warning("Upload bookings or connect the shared Future Schedule.")

    with st.expander("Advanced CSV overrides", expanded=False):
        c1, c2, c3, c4, c5 = st.columns(5)
        cleaners_file = c1.file_uploader("Cleaners CSV", type=["csv"], key="cleaners")
        crew_file = c2.file_uploader("Crew rules CSV", type=["csv"], key="crews")
        exceptions_file = c3.file_uploader("Day off CSV", type=["csv"], key="exceptions")
        area_file = c4.file_uploader("Area memory CSV", type=["csv"], key="area")
        actuals_file = c5.file_uploader("Actual time CSV", type=["csv"], key="actuals")

    try:
        files = {"cleaners": cleaners_file, "bookings": bookings_file, "crews": crew_file, "exceptions": exceptions_file, "area": area_file, "actuals": actuals_file}
        master_data, load_statuses = core.load_master_data(sheet_id, use_google_sheets_master, files)
        cleaners_raw = master_data["cleaners"]
        bookings_raw = master_data["bookings"]
        crews_raw = master_data["crews"]
        exceptions_raw = master_data["exceptions"]
        area_raw = master_data["area"]
        actuals_raw = master_data["actuals"]
        live_bookings_raw = master_data.get("live_bookings", pd.DataFrame())
        if st.session_state.get("local_live_bookings_v17"):
            live_bookings_raw = pd.concat([live_bookings_raw, pd.DataFrame(st.session_state["local_live_bookings_v17"])], ignore_index=True, sort=False)
        active_bookings_raw = bookings_raw.copy()
        combined_bookings_raw = core.combine_active_and_live_bookings(active_bookings_raw, live_bookings_raw)
        if combined_bookings_raw is not None and not combined_bookings_raw.empty:
            bookings_raw = combined_bookings_raw

        with st.expander("Data source status", expanded=False):
            for status in load_statuses:
                st.write("- " + status)

        if bookings_raw is None or bookings_raw.empty:
            st.warning("No bookings found. Upload a BookingKoala/GHL CSV or load the shared Future Schedule from Google Sheets.")
            return

        dates = core.week_dates(week_start, horizon_weeks, include_weekends)
        # Add smart temporary pairs before building resources, so the optimizer can compare solo vs team options.
        if 'enable_smart_pairs' not in locals():
            enable_smart_pairs = True
        if 'smart_pair_limit' not in locals():
            smart_pair_limit = 12
        if enable_smart_pairs:
            smart_pairs = core.generate_smart_temp_pair_crews(cleaners_raw, crews_raw, dates, max_pairs=int(smart_pair_limit))
            if smart_pairs is not None and not smart_pairs.empty:
                crews_raw = pd.concat([crews_raw, smart_pairs], ignore_index=True) if crews_raw is not None and not crews_raw.empty else smart_pairs

        tab_schedule, tab_new_booking, tab_teams, tab_export = st.tabs(["Future Schedule", "Live Booking Planner", "Cleaners & Teams", "Export"])

        with tab_teams:
            crews_raw = render_team_setup(cleaners_raw, crews_raw)
            st.divider()
            st.subheader("Large job team behavior")
            colA, colB = st.columns([1.4, 1.0])
            with colA:
                large_job_mode = st.selectbox("For large jobs", ["Let app choose solo or team for big jobs", "Prefer/require team for big jobs", "No automatic rule"])
            with colB:
                large_job_threshold = st.number_input("Large job threshold: one-person hours", value=3.5, min_value=1.0, step=0.5)
            if large_job_mode != "No automatic rule":
                bookings_raw = apply_large_job_rules(bookings_raw, large_job_threshold, large_job_mode)
            cleaners = core.prepare_cleaners(cleaners_raw)
            crews = core.prepare_crew_rules(crews_raw)
            resources, resource_lookup = core.build_resources(cleaners, crews)
            resource_names = list(resources["resource"].astype(str)) if not resources.empty else []
            bookings_raw = render_job_controls(bookings_raw, resource_names)
            core.display_df("Cleaner/team resources", resources.drop(columns=["member_keys"], errors="ignore"))
        # If user has not opened Cleaners & Teams first, still apply default team setup and resources.
        if "crews" not in locals():
            crews_raw = render_team_setup(cleaners_raw, crews_raw) if False else crews_raw
            cleaners = core.prepare_cleaners(cleaners_raw)
            crews = core.prepare_crew_rules(crews_raw)
            resources, resource_lookup = core.build_resources(cleaners, crews)
            bookings_raw = apply_large_job_rules(bookings_raw, 3.5, "Let app choose solo or team for big jobs")

        bookings = core.prepare_bookings(bookings_raw)
        actuals = core.prepare_actuals(actuals_raw)
        bookings, time_learning = core.apply_actual_time_learning(bookings, actuals)
        exceptions = core.prepare_availability_exceptions(exceptions_raw)
        area_memory = core.prepare_area_memory(area_raw)
        booking_instances = core.expand_recurring_bookings(bookings, dates)
        if booking_instances.empty:
            st.warning("No bookings found inside this planning horizon.")
            return

        with st.spinner("Creating day-by-day cleaner/team routes..."):
            points, point_idx, points_df = core.make_points(resources, booking_instances, api_key if 'api_key' in locals() else '', use_google if 'use_google' in locals() else False)
            miles_matrix, minutes_matrix, route_source = core.compute_route_matrix(points, api_key if 'api_key' in locals() else '', use_google if 'use_google' in locals() else False, routing_provider if 'routing_provider' in locals() else "Approximate only")
            schedule, unassigned, alerts, schedules_dict, member_events = core.optimize_schedule(
                booking_instances, resources, dates, exceptions, miles_matrix, minutes_matrix, point_idx,
                area_memory, hours_are_person_hours, mileage_cost, travel_hour_cost, int(min_gap_minutes),
            )
            price_suggestions = core.price_adjustment_suggestions(schedule, min_profit, long_drive_miles)
            move_messages = core.build_move_message_suggestions(schedule, price_suggestions)
            assist_suggestions = core.build_team_assist_suggestions(schedule, resources, miles_matrix, minutes_matrix, point_idx, int(min_gap_minutes))

        total_miles = float(schedule["travel_miles"].astype(float).sum()) if not schedule.empty and "travel_miles" in schedule else 0.0
        used_resources = int(schedule["resource"].nunique()) if not schedule.empty and "resource" in schedule else 0
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("Scheduled jobs", len(schedule))
        m2.metric("Unassigned", len(unassigned))
        m3.metric("Cleaner/team routes", used_resources)
        m4.metric("Job-to-job miles", f"{total_miles:.1f}")
        if live_bookings_raw is not None and not live_bookings_raw.empty:
            st.caption(f"Live planner is holding/tracking {len(live_bookings_raw)} pending or confirmed booking addition(s) from the shared Google Sheet.")

        if "approx" in str(route_source).lower():
            st.markdown(f"<div class='dd-warn'><b>Routing source:</b> {route_source}. Schedule works, but miles are approximate.</div>", unsafe_allow_html=True)
            with st.expander("Google routing fix details", expanded=False):
                err = st.session_state.get("last_google_routing_error", "")
                if err:
                    st.code(err[:2000], language="text")
                st.write("The error usually means this API key is not allowed to call Routes API or Distance Matrix API. While testing, use a separate server-side key with Application restrictions set to None and API restrictions allowing Routes API, Geocoding API, and Distance Matrix API.")
        else:
            st.success(f"Optimization completed using {route_source}.")

        with tab_schedule:
            render_calendar_overview(schedule, dates)
            render_team_assist_suggestions(assist_suggestions)
            render_problem_summary(unassigned, alerts, price_suggestions, time_learning)
            reviewed_schedule = render_approval_calendar(schedule, dates)
            st.session_state["reviewed_schedule_v17"] = reviewed_schedule
            approved_schedule, rejected_schedule, needs_review_schedule = core.split_reviewed_schedule(reviewed_schedule)
            st.session_state["approved_schedule_v17"] = approved_schedule

        with tab_new_booking:
            render_live_booking_planner(resources, dates, schedule, booking_instances, schedules_dict, member_events, exceptions, area_memory, live_bookings_raw, sheet_id, use_google_sheets_master, uploaded_by, api_key if 'api_key' in locals() else '', use_google if 'use_google' in locals() else False, routing_provider if 'routing_provider' in locals() else "Approximate only", hours_are_person_hours, mileage_cost, travel_hour_cost, int(min_gap_minutes))

        with tab_export:
            reviewed_schedule = st.session_state.get("reviewed_schedule_v17", core.add_manager_review_columns(schedule))
            approved_schedule, rejected_schedule, needs_review_schedule = core.split_reviewed_schedule(reviewed_schedule)
            render_export(approved_schedule, reviewed_schedule, schedule, unassigned, alerts, price_suggestions, move_messages, assist_suggestions, time_learning, actuals, resources, points_df, active_bookings_raw if 'active_bookings_raw' in locals() else bookings_raw, live_bookings_raw if 'live_bookings_raw' in locals() else pd.DataFrame(), sheet_id, use_google_sheets_master)

    except Exception as exc:
        st.error(f"Could not optimize schedule: {exc}")
        st.exception(exc)


if __name__ == "__main__":
    main()
